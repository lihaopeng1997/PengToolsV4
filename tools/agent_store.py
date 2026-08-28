# -*- coding: utf-8 -*-
"""Agent 工作台会话存储：workspace 类型会话，原子写入。

会话结构：
{
  "id": "...", "type": "workspace", "title": "...",
  "workspace_dir": "D:/my/project",   # 会话绑定的工作文件夹
  "messages": [...],                   # 同 model_chat_store 消息结构
  "tool_calls": [...],                 # 每次工具调用记录
  "plan_confirm": False,               # 始终先确认计划（默认关）
  "created_at": "...", "updated_at": "..."
}

落盘：data/agent/workspaces/<id>.json
索引：data/agent/index.json

数据结构（V2，支持「空间 → 对话」二级）：
{
  "id": "...", "type": "workspace", "title": "<空间名>",
  "workspace_dir": "D:/my/project",   # 空间绑定的工作文件夹
  "plan_confirm": false,
  "conversations": [                   # 空间下的多个对话记录（V2 新增）
    { "id": "...", "title": "...",
      "messages": [...], "tool_calls": [...],
      "created_at": "...", "updated_at": "..." }
  ],
  "active_conv_id": "...",             # 当前选中的对话 id
  "created_at": "...", "updated_at": "..."
}

兼容：若顶层仍含旧式 "messages"/"tool_calls"（V1 单工作台单对话），
加载时自动迁移为 conversations[0]，保证不丢旧数据。
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from config import AGENT_DIR, AGENT_WORKSPACES_DIR, AGENT_INDEX_FILE, ensure_config_dir

INDEX_VERSION = 1
TOOL_CALL_MAX = 200  # 单会话最多保留 200 条工具调用记录


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _new_id() -> str:
    return uuid.uuid4().hex


def _ensure_dirs() -> None:
    ensure_config_dir()
    os.makedirs(AGENT_WORKSPACES_DIR, exist_ok=True)


def _atomic_write(path: str, payload) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.agent-', suffix='.tmp', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _index_path() -> str:
    _ensure_dirs()
    return AGENT_INDEX_FILE


def _session_path(session_id: str) -> str:
    safe = ''.join(ch for ch in str(session_id or '') if ch.isalnum() or ch in '-_') or _new_id()
    return os.path.join(AGENT_WORKSPACES_DIR, f'{safe}.json')


def _clean_message(raw) -> dict | None:
    """清洗消息字段，只保留 user/assistant/system 角色。"""
    if not isinstance(raw, dict):
        return None
    role = str(raw.get('role') or '').strip().lower()
    if role not in ('user', 'assistant', 'system', 'tool'):
        return None
    return {
        'id': str(raw.get('id') or _new_id()),
        'role': role,
        'content': str(raw.get('content') or ''),
        'created_at': str(raw.get('created_at') or _now()),
        'tool_call_id': str(raw.get('tool_call_id') or ''),
    }


def _meta_from_session(session: dict) -> dict:
    return {
        'id': str(session.get('id') or ''),
        'title': str(session.get('title') or '新工作台'),
        'workspace_dir': str(session.get('workspace_dir') or ''),
        'created_at': str(session.get('created_at') or ''),
        'updated_at': str(session.get('updated_at') or ''),
    }


# ─── 索引操作 ───────────────────────────────────────────────────────────────

def load_index() -> list[dict]:
    """返回所有工作台会话摘要列表，按 updated_at 倒序。"""
    try:
        with open(_index_path(), 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    items = data.get('sessions') if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if isinstance(item, dict) and item.get('id'):
            result.append({
                'id': str(item.get('id')),
                'title': str(item.get('title') or '新工作台'),
                'workspace_dir': str(item.get('workspace_dir') or ''),
                'created_at': str(item.get('created_at') or ''),
                'updated_at': str(item.get('updated_at') or ''),
            })
    result.sort(key=lambda row: str(row.get('updated_at') or ''), reverse=True)
    return result


def _save_index(rows: list[dict]) -> None:
    _atomic_write(_index_path(), {'version': INDEX_VERSION, 'sessions': rows})


def _touch_index(session: dict) -> None:
    meta = _meta_from_session(session)
    rows = [row for row in load_index() if row.get('id') != meta['id']]
    rows.insert(0, meta)
    _save_index(rows)


def _remove_from_index(session_id: str) -> None:
    rows = [row for row in load_index() if row.get('id') != str(session_id)]
    _save_index(rows)


# ─── 会话 CRUD ──────────────────────────────────────────────────────────────

def empty_workspace(*, title: str = '新工作台', workspace_dir: str = '') -> dict:
    """创建一个空工作台会话（V2：含 conversations 列表 + 默认对话）。"""
    now = _now()
    conv_id = _new_id()
    return {
        'id': _new_id(),
        'type': 'workspace',
        'title': title,
        'workspace_dir': workspace_dir,
        'plan_confirm': False,
        'conversations': [{
            'id': conv_id,
            'title': '对话 1',
            'messages': [],
            'tool_calls': [],
            'created_at': now,
            'updated_at': now,
        }],
        'active_conv_id': conv_id,
        'created_at': now,
        'updated_at': now,
    }


def _normalize_conversations(data: dict) -> dict:
    """确保 workspace 含 conversations 列表；兼容 V1 顶层 messages/tool_calls 迁移。"""
    conversations = data.get('conversations')
    if not isinstance(conversations, list):
        # V1：顶层 messages/tool_calls 迁移为第一段对话
        old_msgs = data.get('messages') or []
        old_tools = data.get('tool_calls') or []
        now = _now()
        conv = {
            'id': _new_id(),
            'title': str(data.get('title') or '工作台'),
            'messages': [_clean_message(m) for m in old_msgs if _clean_message(m)],
            'tool_calls': list(old_tools or []),
            'created_at': now,
            'updated_at': now,
        }
        conversations = [conv]
        data['conversations'] = conversations
    conversations = [c for c in conversations if isinstance(c, dict) and c.get('id')]
    for conv in conversations:
        conv.setdefault('messages', [])
        conv['messages'] = [_clean_message(m) for m in conv['messages'] if _clean_message(m)]
        conv.setdefault('tool_calls', [])
        conv.setdefault('title', '对话')
        conv.setdefault('created_at', str(data.get('created_at') or _now()))
        conv.setdefault('updated_at', conv.get('created_at'))
    data['conversations'] = conversations
    active = data.get('active_conv_id')
    if not active or not any(c['id'] == active for c in conversations):
        data['active_conv_id'] = conversations[0]['id'] if conversations else None
    return data


def load_workspace(session_id: str) -> dict | None:
    """加载完整工作台会话（含对话列表和历史）。V1 数据自动迁移到 conversations。"""
    path = _session_path(session_id)
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get('type') != 'workspace':
        return None
    data['id'] = str(data.get('id') or session_id)
    data = _normalize_conversations(data)
    return data


def save_workspace(session: dict) -> None:
    """原子写入工作台会话并更新索引。"""
    session['updated_at'] = _now()
    session = _normalize_conversations(session)
    # 限制每个对话 tool_calls 长度，防止无限膨胀
    for conv in session.get('conversations') or []:
        if len(conv.get('tool_calls') or []) > TOOL_CALL_MAX:
            conv['tool_calls'] = conv['tool_calls'][-TOOL_CALL_MAX:]
        if len(conv.get('messages') or []) > 5000:
            conv['messages'] = conv['messages'][-5000:]
    _atomic_write(_session_path(session['id']), session)
    _touch_index(session)


def delete_workspace(session_id: str) -> bool:
    """删除工作台会话文件并从索引移除。"""
    path = _session_path(session_id)
    try:
        os.unlink(path)
    except OSError:
        return False
    _remove_from_index(session_id)
    return True


def list_workspaces() -> list[dict]:
    """返回所有工作台会话摘要列表。"""
    return load_index()


def update_workspace(session_id: str, patch: dict) -> dict | None:
    """对工作台会话打补丁（title/workspace_dir/plan_confirm 等），原子写回。"""
    session = load_workspace(session_id)
    if not session:
        return None
    for key in ('title', 'workspace_dir', 'plan_confirm'):
        if key in patch:
            session[key] = patch[key]
    save_workspace(session)
    return session


# ─── 对话（conversation）级 CRUD ──────────────────────────────────────────

def _get_conv(session: dict, conv_id: str | None = None) -> dict | None:
    """取指定对话；未指定则取 active。返回对话 dict（引用）。"""
    s = _normalize_conversations(session)
    convs = s.get('conversations') or []
    if not convs:
        return None
    target = conv_id or s.get('active_conv_id')
    for c in convs:
        if c.get('id') == target:
            return c
    return convs[0]


def create_conversation(session_id: str, title: str = '新对话') -> tuple[dict | None, str]:
    """在空间下新建一个对话，并设为 active。返回 (session, conv_id)。"""
    session = load_workspace(session_id)
    if not session:
        return None, ''
    now = _now()
    conv = {'id': _new_id(), 'title': title or '新对话',
            'messages': [], 'tool_calls': [],
            'created_at': now, 'updated_at': now}
    session.setdefault('conversations', []).append(conv)
    session['active_conv_id'] = conv['id']
    save_workspace(session)
    return session, conv['id']


def load_conversation(session_id: str, conv_id: str | None = None) -> dict | None:
    """加载空间下的一个完整对话（含 messages/tool_calls）。"""
    session = load_workspace(session_id)
    if not session:
        return None
    conv = _get_conv(session, conv_id)
    if not conv:
        return None
    return dict(conv)


def list_conversations(session_id: str) -> list[dict]:
    """列出空间下所有对话摘要。"""
    session = load_workspace(session_id) or {}
    return list(session.get('conversations') or [])


def rename_conversation(session_id: str, conv_id: str, title: str) -> dict | None:
    session = load_workspace(session_id)
    if not session:
        return None
    conv = _get_conv(session, conv_id)
    if not conv:
        return None
    conv['title'] = title or '对话'
    conv['updated_at'] = _now()
    save_workspace(session)
    return session


def delete_conversation(session_id: str, conv_id: str) -> bool:
    """删除空间下的一个对话。若删除的是当前 active，active 指向剩余第一个。"""
    session = load_workspace(session_id)
    if not session:
        return False
    convs = session.get('conversations') or []
    new_convs = [c for c in convs if c.get('id') != conv_id]
    if len(new_convs) == len(convs):
        return False
    session['conversations'] = new_convs
    if session.get('active_conv_id') == conv_id:
        session['active_conv_id'] = new_convs[0]['id'] if new_convs else None
    save_workspace(session)
    return True


def set_active_conversation(session_id: str, conv_id: str) -> dict | None:
    session = load_workspace(session_id)
    if not session:
        return None
    conv = _get_conv(session, conv_id)
    if not conv:
        return None
    session['active_conv_id'] = conv['id']
    save_workspace(session)
    return session


# ─── 消息追加（对话级） ────────────────────────────────────────────────────

def append_message(session_id: str, message: dict, conv_id: str | None = None) -> bool:
    """向指定对话（默认 active）追加一条消息，原子写回。"""
    session = load_workspace(session_id)
    if not session:
        return False
    cleaned = _clean_message(message)
    if not cleaned:
        return False
    conv = _get_conv(session, conv_id)
    if conv is None:
        return False
    conv.setdefault('messages', []).append(cleaned)
    conv['updated_at'] = _now()
    save_workspace(session)
    return True


def append_tool_call(session_id: str, tool_call: dict, conv_id: str | None = None) -> bool:
    """向指定对话（默认 active）追加一条工具调用记录，原子写回。"""
    session = load_workspace(session_id)
    if not session:
        return False
    conv = _get_conv(session, conv_id)
    if conv is None:
        return False
    if not isinstance(conv.get('tool_calls'), list):
        conv['tool_calls'] = []
    conv['tool_calls'].append({
        'id': str(tool_call.get('id') or _new_id()),
        'tool': str(tool_call.get('tool') or ''),
        'args': tool_call.get('args') or {},
        'result': str(tool_call.get('result') or ''),
        'error': str(tool_call.get('error') or ''),
        'timestamp': str(tool_call.get('timestamp') or _now()),
    })
    conv['updated_at'] = _now()
    save_workspace(session)
    return True


def pop_last_assistant_message(session_id: str, conv_id: str | None = None) -> dict | None:
    """撤回指定对话（默认 active）最后一条 assistant 消息，返回被撤回的消息。"""
    session = load_workspace(session_id)
    if not session:
        return None
    conv = _get_conv(session, conv_id)
    if conv is None:
        return None
    msgs = conv.get('messages') or []
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get('role') == 'assistant':
            removed = msgs.pop(i)
            conv['updated_at'] = _now()
            save_workspace(session)
            return removed
    return None
