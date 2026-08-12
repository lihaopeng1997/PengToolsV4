# -*- coding: utf-8 -*-
"""工作台待升级事项的独立持久化与月份归属。

仅保存手工事项、看板完成键与 UI 偏好，避免污染需求台账与发版流程。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
import uuid

from config import DASHBOARD_RELEASE_ITEMS_FILE, ensure_config_dir


_MONTH_RE = re.compile(r'^(20\d{2})-(0[1-9]|1[0-2])')


def normalize_month_key(value) -> str:
    """规范为 yyyy-MM；无法识别返回空串。"""
    text = str(value or '').strip()
    if not text:
        return ''
    # 已是 yyyy-MM 或 yyyy-MM-dd
    head = text[:7]
    if _MONTH_RE.match(head):
        return head
    # 2026年8月 / 2026/8
    match = re.match(r'^(20\d{2})[-/.年](0?[1-9]|1[0-2])', text)
    if match:
        return f'{int(match.group(1)):04d}-{int(match.group(2)):02d}'
    return ''


def release_month_for(item, *, fallback_current: bool = True, today=None) -> str:
    """待升级事项的统一月份归属。

    - 未勾选 is_monthly_release → 空（不进看板）
    - 已填 online_month → 用该月
    - 已勾选但未填月份 → fallback_current 时归入当前自然月，否则空
    """
    if not isinstance(item, dict) or not item.get('is_monthly_release'):
        return ''
    month = normalize_month_key(item.get('online_month'))
    if month:
        return month
    if fallback_current:
        day = today or datetime.date.today()
        if hasattr(day, 'strftime'):
            return day.strftime('%Y-%m')
        return str(day)[:7]
    return ''


def collect_release_months(requirements, *, today=None) -> list[str]:
    """仅收集已勾选入选任务的归属月份（含空月份→当前月），新到旧排序。"""
    months = {
        release_month_for(item, fallback_current=True, today=today)
        for item in (requirements or [])
        if isinstance(item, dict) and item.get('is_monthly_release')
    }
    return sorted((m for m in months if m), reverse=True)


def is_board_item_completed(item, month: str, completed_keys) -> bool:
    """工作台独立完成态：仅 completed_requirement_keys，不读需求业务 status。"""
    req_id = str((item or {}).get('id') or '')
    month_key = normalize_month_key(month) or str(month or '')[:7]
    if not req_id or not month_key:
        return False
    key = f'{req_id}@{month_key}'
    # 调用方常在循环内传入同一集合；勿每次 set() 拷贝
    if not completed_keys:
        return False
    return key in completed_keys



def _normalize_item(item):
    value = dict(item) if isinstance(item, dict) else {}
    value['id'] = str(value.get('id') or uuid.uuid4().hex)
    value['title'] = str(value.get('title') or '').strip()
    value['planned_date'] = str(value.get('planned_date') or '')[:10]
    value['note'] = str(value.get('note') or '').strip()
    return value


def load_release_board(path=None):
    target = path or DASHBOARD_RELEASE_ITEMS_FILE
    try:
        with open(target, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError):
        value = {}
    if isinstance(value, list):
        value = {'manual_items': value, 'hidden_requirement_ids': []}
    if not isinstance(value, dict):
        value = {}
    prefs = value.get('ui_prefs') if isinstance(value.get('ui_prefs'), dict) else {}
    return {
        'manual_items': [_normalize_item(item) for item in value.get('manual_items', []) if isinstance(item, dict)],
        # 兼容旧版“移除”记录；新版本不再提供从需求添加入口。
        'hidden_requirement_ids': [str(item) for item in value.get('hidden_requirement_ids', []) if str(item)],
        'completed_requirement_keys': [
            str(item) for item in value.get('completed_requirement_keys', []) if str(item)
        ],
        'completed_manual_keys': [
            str(item) for item in value.get('completed_manual_keys', []) if str(item)
        ],
        'ui_prefs': {
            # 已完成分区默认折叠，列表更干净
            'completed_section_collapsed': bool(prefs.get('completed_section_collapsed', True)),
        },
    }


def save_release_board(board, path=None):
    target = path or DASHBOARD_RELEASE_ITEMS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    payload = board if isinstance(board, dict) else {}
    prefs = payload.get('ui_prefs') if isinstance(payload.get('ui_prefs'), dict) else {}
    value = {
        'manual_items': [_normalize_item(item) for item in payload.get('manual_items', []) if isinstance(item, dict)],
        'hidden_requirement_ids': sorted({str(item) for item in payload.get('hidden_requirement_ids', []) if str(item)}),
        'completed_requirement_keys': sorted({
            str(item) for item in payload.get('completed_requirement_keys', []) if str(item)
        }),
        'completed_manual_keys': sorted({
            str(item) for item in payload.get('completed_manual_keys', []) if str(item)
        }),
        'ui_prefs': {
            'completed_section_collapsed': bool(prefs.get('completed_section_collapsed', True)),
        },
    }
    directory = os.path.dirname(os.path.abspath(target)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.release-board-', suffix='.tmp', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def load_release_items(path=None):
    return load_release_board(path)['manual_items']


def save_release_items(items, path=None):
    board = load_release_board(path)
    board['manual_items'] = items or []
    save_release_board(board, path)
