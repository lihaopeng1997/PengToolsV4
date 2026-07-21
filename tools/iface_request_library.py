# -*- coding: utf-8 -*-
"""请求测试：接口库 / 历史记录 / 分类（落盘 data/iface_request_library.json）。

- 与抓包会话隔离：仅保存用户主动测试/收藏的接口，不写抓包内存会话。
- 历史有上限；Body 过长截断，响应仅存预览。
- 读写兼容旧字段（setdefault）。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from config import CONFIG_DIR, ensure_config_dir

LIBRARY_FILE = os.path.join(CONFIG_DIR, 'iface_request_library.json')

UNCATEGORIZED_ID = 'uncategorized'
DEFAULT_CATEGORY_NAME = '未分类'
MAX_HISTORY = 100
MAX_BODY_CHARS = 200_000
MAX_RESP_PREVIEW = 2_000
LIBRARY_VERSION = 1


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _new_id() -> str:
    return uuid.uuid4().hex


def _clip(text: str, limit: int) -> str:
    s = text if isinstance(text, str) else ('' if text is None else str(text))
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit] + f'\n…(已截断，原长 {len(s)})'


def _normalize_category(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    cid = str(item.get('id') or '').strip() or _new_id()
    name = str(item.get('name') or '').strip() or DEFAULT_CATEGORY_NAME
    return {'id': cid, 'name': name}


def _normalize_api(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    method = str(item.get('method') or 'GET').upper().strip() or 'GET'
    url = str(item.get('url') or '').strip()
    if not url and not item.get('path'):
        return None
    return {
        'id': str(item.get('id') or '').strip() or _new_id(),
        'name': str(item.get('name') or '').strip() or _default_name(method, url),
        'category_id': str(item.get('category_id') or UNCATEGORIZED_ID).strip() or UNCATEGORIZED_ID,
        'method': method,
        'url': url or str(item.get('path') or ''),
        'base_host': str(item.get('base_host') or '').strip(),
        'headers_text': str(item.get('headers_text') or ''),
        'params_text': str(item.get('params_text') or ''),
        'body': _clip(str(item.get('body') or ''), MAX_BODY_CHARS),
        'note': str(item.get('note') or ''),
        'updated_at': str(item.get('updated_at') or _now_iso()),
        'created_at': str(item.get('created_at') or item.get('updated_at') or _now_iso()),
    }


def _normalize_history(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    method = str(item.get('method') or 'GET').upper().strip() or 'GET'
    url = str(item.get('url') or '').strip()
    if not url:
        return None
    status = item.get('status')
    try:
        status = int(status) if status is not None and str(status).strip() != '' else None
    except (TypeError, ValueError):
        status = None
    return {
        'id': str(item.get('id') or '').strip() or _new_id(),
        'ts': str(item.get('ts') or _now_iso()),
        'name': str(item.get('name') or '').strip() or _default_name(method, url),
        'category_id': str(item.get('category_id') or UNCATEGORIZED_ID).strip() or UNCATEGORIZED_ID,
        'method': method,
        'url': url,
        'base_host': str(item.get('base_host') or '').strip(),
        'headers_text': str(item.get('headers_text') or ''),
        'params_text': str(item.get('params_text') or ''),
        'body': _clip(str(item.get('body') or ''), MAX_BODY_CHARS),
        'status': status,
        'ok': bool(item.get('ok')) if item.get('ok') is not None else None,
        'error': str(item.get('error') or ''),
        'response_preview': _clip(str(item.get('response_preview') or ''), MAX_RESP_PREVIEW),
        'duration_ms': _safe_int(item.get('duration_ms'), 0),
    }


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _default_name(method: str, url: str) -> str:
    path = urlparse(url or '').path or '/'
    if len(path) > 48:
        path = '…' + path[-47:]
    return f'{(method or "GET").upper()} {path}'


def normalize_library(data: Any = None) -> dict:
    result = {
        'version': LIBRARY_VERSION,
        'categories': [],
        'apis': [],
        'history': [],
        'max_history': MAX_HISTORY,
        'last_category_id': UNCATEGORIZED_ID,
        'last_mode': 'library',  # library | history
    }
    if isinstance(data, dict):
        result.update({k: data[k] for k in result if k in data})
        # 兼容透传未知字段
        for k, v in data.items():
            if k not in result:
                result[k] = v

    cats = []
    seen = set()
    for raw in result.get('categories') or []:
        cat = _normalize_category(raw)
        if cat and cat['id'] not in seen:
            seen.add(cat['id'])
            cats.append(cat)
    if UNCATEGORIZED_ID not in seen:
        cats.insert(0, {'id': UNCATEGORIZED_ID, 'name': DEFAULT_CATEGORY_NAME})
    else:
        # 保证未分类在前
        cats = sorted(cats, key=lambda c: 0 if c['id'] == UNCATEGORIZED_ID else 1)
    result['categories'] = cats
    cat_ids = {c['id'] for c in cats}

    apis = []
    for raw in result.get('apis') or []:
        api = _normalize_api(raw)
        if not api:
            continue
        if api['category_id'] not in cat_ids:
            api['category_id'] = UNCATEGORIZED_ID
        apis.append(api)
    # 新在前
    apis.sort(key=lambda a: a.get('updated_at') or '', reverse=True)
    result['apis'] = apis

    hist = []
    for raw in result.get('history') or []:
        h = _normalize_history(raw)
        if not h:
            continue
        if h['category_id'] not in cat_ids:
            h['category_id'] = UNCATEGORIZED_ID
        hist.append(h)
    hist.sort(key=lambda h: h.get('ts') or '', reverse=True)
    max_h = _safe_int(result.get('max_history'), MAX_HISTORY)
    # 允许 1～500；非法/0 回退默认
    if max_h < 1 or max_h > 500:
        max_h = MAX_HISTORY
    result['max_history'] = max_h
    result['history'] = hist[:max_h]

    last_cat = str(result.get('last_category_id') or UNCATEGORIZED_ID)
    if last_cat not in cat_ids:
        last_cat = UNCATEGORIZED_ID
    result['last_category_id'] = last_cat
    mode = str(result.get('last_mode') or 'library').lower()
    result['last_mode'] = mode if mode in ('library', 'history') else 'library'
    result.setdefault('version', LIBRARY_VERSION)
    return result


def load_library(path: Optional[str] = None) -> dict:
    target = path or LIBRARY_FILE
    ensure_config_dir()
    try:
        with open(target, 'r', encoding='utf-8') as stream:
            return normalize_library(json.load(stream))
    except (OSError, ValueError, TypeError):
        return normalize_library()


def save_library(data: dict, path: Optional[str] = None) -> dict:
    target = path or LIBRARY_FILE
    ensure_config_dir()
    normalized = normalize_library(data)
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(normalized, stream, ensure_ascii=False, indent=2)
    return normalized


def category_name(lib: dict, category_id: str) -> str:
    cid = category_id or UNCATEGORIZED_ID
    for c in lib.get('categories') or []:
        if c.get('id') == cid:
            return c.get('name') or DEFAULT_CATEGORY_NAME
    return DEFAULT_CATEGORY_NAME


def add_category(lib: dict, name: str) -> dict:
    lib = normalize_library(lib)
    name = (name or '').strip()
    if not name:
        raise ValueError('分类名称不能为空')
    for c in lib['categories']:
        if c.get('name') == name:
            raise ValueError(f'分类「{name}」已存在')
    lib['categories'].append({'id': _new_id(), 'name': name})
    return save_library(lib)


def rename_category(lib: dict, category_id: str, name: str) -> dict:
    lib = normalize_library(lib)
    name = (name or '').strip()
    if not name:
        raise ValueError('分类名称不能为空')
    if category_id == UNCATEGORIZED_ID:
        raise ValueError('默认「未分类」不可改名')
    hit = next((c for c in lib['categories'] if c.get('id') == category_id), None)
    if not hit:
        raise ValueError('分类不存在')
    for c in lib['categories']:
        if c.get('id') != category_id and c.get('name') == name:
            raise ValueError(f'分类「{name}」已存在')
    hit['name'] = name
    return save_library(lib)


def delete_category(lib: dict, category_id: str) -> dict:
    lib = normalize_library(lib)
    if category_id == UNCATEGORIZED_ID:
        raise ValueError('默认「未分类」不可删除')
    if not any(c.get('id') == category_id for c in lib['categories']):
        raise ValueError('分类不存在')
    lib['categories'] = [c for c in lib['categories'] if c.get('id') != category_id]
    for api in lib['apis']:
        if api.get('category_id') == category_id:
            api['category_id'] = UNCATEGORIZED_ID
    for h in lib['history']:
        if h.get('category_id') == category_id:
            h['category_id'] = UNCATEGORIZED_ID
    if lib.get('last_category_id') == category_id:
        lib['last_category_id'] = UNCATEGORIZED_ID
    return save_library(lib)


def upsert_api(lib: dict, item: dict) -> dict:
    """新增或更新接口库条目（按 id）。"""
    lib = normalize_library(lib)
    api = _normalize_api(item)
    if not api:
        raise ValueError('接口缺少 URL')
    cat_ids = {c['id'] for c in lib['categories']}
    if api['category_id'] not in cat_ids:
        api['category_id'] = UNCATEGORIZED_ID
    api['updated_at'] = _now_iso()
    if not item.get('created_at'):
        existing = next((a for a in lib['apis'] if a.get('id') == api['id']), None)
        if existing:
            api['created_at'] = existing.get('created_at') or api['updated_at']
        else:
            api['created_at'] = api['updated_at']
    else:
        api['created_at'] = str(item.get('created_at'))
    others = [a for a in lib['apis'] if a.get('id') != api['id']]
    others.insert(0, api)
    lib['apis'] = others
    lib['last_category_id'] = api['category_id']
    return save_library(lib)


def delete_api(lib: dict, api_id: str) -> dict:
    lib = normalize_library(lib)
    lib['apis'] = [a for a in lib['apis'] if a.get('id') != api_id]
    return save_library(lib)


def append_history(lib: dict, entry: dict) -> dict:
    lib = normalize_library(lib)
    h = _normalize_history(entry)
    if not h:
        raise ValueError('历史缺少 URL')
    cat_ids = {c['id'] for c in lib['categories']}
    if h['category_id'] not in cat_ids:
        h['category_id'] = UNCATEGORIZED_ID
    h['ts'] = h.get('ts') or _now_iso()
    hist = [h] + [x for x in lib['history'] if x.get('id') != h['id']]
    lib['history'] = hist[: lib['max_history']]
    return save_library(lib)


def delete_history(lib: dict, history_id: str) -> dict:
    lib = normalize_library(lib)
    lib['history'] = [h for h in lib['history'] if h.get('id') != history_id]
    return save_library(lib)


def clear_history(lib: dict) -> dict:
    lib = normalize_library(lib)
    lib['history'] = []
    return save_library(lib)


def set_last_mode(lib: dict, mode: str) -> dict:
    lib = normalize_library(lib)
    lib['last_mode'] = 'history' if mode == 'history' else 'library'
    return save_library(lib)


def set_last_category(lib: dict, category_id: str) -> dict:
    lib = normalize_library(lib)
    cat_ids = {c['id'] for c in lib['categories']}
    lib['last_category_id'] = category_id if category_id in cat_ids else UNCATEGORIZED_ID
    return save_library(lib)


def filter_items(
    items: list[dict],
    *,
    category_id: str = '',
    keyword: str = '',
) -> list[dict]:
    """category_id 为空或 'all' 表示全部分类。"""
    kw = (keyword or '').strip().lower()
    cat = (category_id or '').strip()
    out = []
    for it in items or []:
        if cat and cat not in ('all', '*') and (it.get('category_id') or UNCATEGORIZED_ID) != cat:
            continue
        if kw:
            blob = ' '.join([
                str(it.get('name') or ''),
                str(it.get('method') or ''),
                str(it.get('url') or ''),
                str(it.get('note') or ''),
                str(it.get('status') or ''),
            ]).lower()
            if kw not in blob:
                continue
        out.append(it)
    return out


def build_api_from_form(
    *,
    name: str,
    category_id: str,
    method: str,
    url: str,
    base_host: str = '',
    headers_text: str = '',
    params_text: str = '',
    body: str = '',
    note: str = '',
    api_id: str = '',
) -> dict:
    return {
        'id': api_id or _new_id(),
        'name': (name or '').strip() or _default_name(method, url),
        'category_id': category_id or UNCATEGORIZED_ID,
        'method': (method or 'GET').upper(),
        'url': (url or '').strip(),
        'base_host': (base_host or '').strip(),
        'headers_text': headers_text or '',
        'params_text': params_text or '',
        'body': body or '',
        'note': note or '',
    }


def build_history_from_send(
    *,
    method: str,
    url: str,
    base_host: str = '',
    headers_text: str = '',
    params_text: str = '',
    body: str = '',
    category_id: str = '',
    status=None,
    ok: Optional[bool] = None,
    error: str = '',
    response_body: str = '',
    duration_ms: int = 0,
    name: str = '',
) -> dict:
    return {
        'id': _new_id(),
        'ts': _now_iso(),
        'name': (name or '').strip() or _default_name(method, url),
        'category_id': category_id or UNCATEGORIZED_ID,
        'method': (method or 'GET').upper(),
        'url': (url or '').strip(),
        'base_host': (base_host or '').strip(),
        'headers_text': headers_text or '',
        'params_text': params_text or '',
        'body': body or '',
        'status': status,
        'ok': ok,
        'error': error or '',
        'response_preview': _clip(response_body or '', MAX_RESP_PREVIEW),
        'duration_ms': duration_ms or 0,
    }


def form_fields_from_item(item: dict) -> dict:
    """转为面板可 _rt_apply_form / 填充 的字段。"""
    item = item or {}
    return {
        'method': (item.get('method') or 'GET').upper(),
        'url': item.get('url') or '',
        'base_host': item.get('base_host') or '',
        'headers_text': item.get('headers_text') or '',
        'params_text': item.get('params_text') or '',
        'body': item.get('body') or '',
        'category_id': item.get('category_id') or UNCATEGORIZED_ID,
        'name': item.get('name') or '',
        'response_body_sample': item.get('response_preview') or '',
    }


def display_label(item: dict, *, mode: str = 'library', category_map: Optional[dict] = None) -> str:
    """列表展示文案。"""
    method = (item.get('method') or 'GET').upper()
    name = item.get('name') or _default_name(method, item.get('url') or '')
    cat = ''
    if category_map is not None:
        cat = category_map.get(item.get('category_id') or UNCATEGORIZED_ID, '')
    if mode == 'history':
        ts = str(item.get('ts') or '')
        if 'T' in ts:
            ts = ts.replace('T', ' ')[5:16]  # MM-DD HH:MM
        st = item.get('status')
        st_s = str(st) if st is not None else ('ERR' if item.get('error') else '—')
        prefix = f'[{st_s}] {ts} · {method}'
        return f'{prefix} {name}' if name else prefix
    cat_part = f'[{cat}] ' if cat and cat != DEFAULT_CATEGORY_NAME else ''
    return f'{cat_part}{method} · {name}'
