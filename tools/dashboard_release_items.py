# -*- coding: utf-8 -*-
"""工作台待升级事项的独立持久化。

仅保存手工事项和被用户从看板移除的需求 ID，避免影响需求台账与发版流程。
"""
from __future__ import annotations

import json
import os
import uuid

from config import DASHBOARD_RELEASE_ITEMS_FILE, ensure_config_dir


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
    return {
        'manual_items': [_normalize_item(item) for item in value.get('manual_items', []) if isinstance(item, dict)],
        'hidden_requirement_ids': [str(item) for item in value.get('hidden_requirement_ids', []) if str(item)],
    }


def save_release_board(board, path=None):
    target = path or DASHBOARD_RELEASE_ITEMS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    payload = board if isinstance(board, dict) else {}
    value = {
        'manual_items': [_normalize_item(item) for item in payload.get('manual_items', []) if isinstance(item, dict)],
        'hidden_requirement_ids': sorted({str(item) for item in payload.get('hidden_requirement_ids', []) if str(item)}),
    }
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def load_release_items(path=None):
    return load_release_board(path)['manual_items']


def save_release_items(items, path=None):
    board = load_release_board(path)
    board['manual_items'] = items or []
    save_release_board(board, path)
