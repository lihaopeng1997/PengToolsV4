# -*- coding: utf-8 -*-
"""模型对话明文会话：UTF-8 JSON，原子写入；不含 Token / Base URL / 凭据。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from config import MODEL_CHAT_DIR, ensure_config_dir

INDEX_NAME = 'index.json'
MAX_CONTEXT_MESSAGES = 30
MAX_CONTEXT_CHARS = 12000
SYSTEM_PROMPT = (
    '你是内网助手。不要索要或复述密码、Token、连接串、抓包报文或客户隐私。'
    '用户若粘贴了敏感内容，只给出一般性帮助，不要把它写入你的假设为事实。'
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _new_id() -> str:
    return uuid.uuid4().hex


def chat_dir() -> str:
    ensure_config_dir()
    os.makedirs(MODEL_CHAT_DIR, exist_ok=True)
    return MODEL_CHAT_DIR


def _atomic_write(path: str, payload) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.chat-', suffix='.tmp', dir=directory, text=True)
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
    return os.path.join(chat_dir(), INDEX_NAME)


def _session_path(session_id: str) -> str:
    safe = ''.join(ch for ch in str(session_id or '') if ch.isalnum() or ch in '-_') or _new_id()
    return os.path.join(chat_dir(), f'{safe}.json')


def _clean_message(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    role = str(raw.get('role') or '').strip().lower()
    if role not in ('user', 'assistant', 'system'):
        return None
    return {
        'id': str(raw.get('id') or _new_id()),
        'role': role,
        'content': str(raw.get('content') or ''),
        'created_at': str(raw.get('created_at') or _now()),
        'model_config_id': str(raw.get('model_config_id') or ''),
        'model': str(raw.get('model') or ''),
        'config_name': str(raw.get('config_name') or ''),
        'status': str(raw.get('status') or 'complete'),
    }


def _meta_from_session(session: dict) -> dict:
    return {
        'id': str(session.get('id') or ''),
        'title': str(session.get('title') or '新对话'),
        'title_locked': bool(session.get('title_locked')),
        'model_config_id': str(session.get('model_config_id') or ''),
        'model': str(session.get('model') or ''),
        'created_at': str(session.get('created_at') or ''),
        'updated_at': str(session.get('updated_at') or ''),
    }


def load_index() -> list[dict]:
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
                'title': str(item.get('title') or '新对话'),
                'title_locked': bool(item.get('title_locked')),
                'model_config_id': str(item.get('model_config_id') or ''),
                'model': str(item.get('model') or ''),
                'created_at': str(item.get('created_at') or ''),
                'updated_at': str(item.get('updated_at') or ''),
            })
    result.sort(key=lambda row: str(row.get('updated_at') or ''), reverse=True)
    return result


def _save_index(rows: list[dict]) -> None:
    _atomic_write(_index_path(), {'version': 1, 'sessions': rows})


def _touch_index(session: dict) -> None:
    meta = _meta_from_session(session)
    rows = [row for row in load_index() if row.get('id') != meta['id']]
    rows.insert(0, meta)
    _save_index(rows)


def empty_session(*, model_config_id: str = '', model: str = '') -> dict:
    now = _now()
    return {
        'id': _new_id(),
        'title': '新对话',
        'title_locked': False,
        'model_config_id': str(model_config_id or ''),
        'model': str(model or ''),
        'created_at': now,
        'updated_at': now,
        'messages': [],
    }


def load_session(session_id: str) -> dict | None:
    path = _session_path(session_id)
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    messages = []
    for item in data.get('messages') or []:
        cleaned = _clean_message(item)
        if cleaned:
            messages.append(cleaned)
    data['messages'] = messages
    data['id'] = str(data.get('id') or session_id)
    data['title'] = str(data.get('title') or '新对话')
    data['title_locked'] = bool(data.get('title_locked'))
    return data


def save_session(session: dict) -> dict:
    data = dict(session or {})
    data['id'] = str(data.get('id') or _new_id())
    data['updated_at'] = _now()
    messages = []
    for item in data.get('messages') or []:
        cleaned = _clean_message(item)
        if cleaned:
            messages.append(cleaned)
    data['messages'] = messages
    _atomic_write(_session_path(data['id']), data)
    _touch_index(data)
    return data


def create_session(*, model_config_id: str = '', model: str = '') -> dict:
    return save_session(empty_session(model_config_id=model_config_id, model=model))


def delete_session(session_id: str) -> None:
    path = _session_path(session_id)
    try:
        os.unlink(path)
    except OSError:
        pass
    rows = [row for row in load_index() if row.get('id') != session_id]
    _save_index(rows)


def rename_session(session_id: str, title: str) -> dict | None:
    session = load_session(session_id)
    if session is None:
        return None
    session['title'] = str(title or '').strip() or session.get('title') or '新对话'
    session['title_locked'] = True
    return save_session(session)


def search_sessions(keyword: str) -> list[dict]:
    needle = str(keyword or '').strip().lower()
    rows = load_index()
    if not needle:
        return rows
    return [row for row in rows if needle in str(row.get('title') or '').lower()]


def append_message(session_id: str, role: str, content: str, **extra) -> dict | None:
    session = load_session(session_id)
    if session is None:
        return None
    message = _clean_message({
        'role': role,
        'content': content,
        'model_config_id': extra.get('model_config_id') or session.get('model_config_id') or '',
        'model': extra.get('model') or session.get('model') or '',
        'config_name': extra.get('config_name') or '',
        'status': extra.get('status') or 'complete',
    })
    if message is None:
        return session
    session['messages'].append(message)
    if role == 'user' and not session.get('title_locked'):
        title = str(content or '').strip().replace('\n', ' ')
        if title:
            session['title'] = title[:20]
    if extra.get('model_config_id'):
        session['model_config_id'] = str(extra.get('model_config_id'))
    if extra.get('model'):
        session['model'] = str(extra.get('model'))
    return save_session(session)


def update_message(session_id: str, message_id: str, **fields) -> dict | None:
    session = load_session(session_id)
    if session is None:
        return None
    for item in session.get('messages') or []:
        if item.get('id') == message_id:
            if 'content' in fields:
                item['content'] = str(fields.get('content') or '')
            if 'status' in fields:
                item['status'] = str(fields.get('status') or item.get('status') or 'complete')
            break
    return save_session(session)


def trim_messages_for_request(messages, *, max_messages: int = MAX_CONTEXT_MESSAGES, max_chars: int = MAX_CONTEXT_CHARS) -> tuple[list[dict], bool]:
    rows = [item for item in (messages or []) if isinstance(item, dict) and item.get('role') != 'system']
    trimmed = False
    while len(rows) > max_messages and len(rows) >= 2:
        rows = rows[2:]
        trimmed = True
    def packed():
        total = 0
        payload = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        for item in rows:
            text = str(item.get('content') or '')
            total += len(text)
            payload.append({'role': item.get('role'), 'content': text})
        return payload, total
    payload, total = packed()
    while total > max_chars and len(rows) >= 2:
        rows = rows[2:]
        trimmed = True
        payload, total = packed()
    return payload, trimmed
