# -*- coding: utf-8 -*-
"""接口排查持久配置：仅保存路径/端口/地址/证书指纹/UI 偏好，不含报文。"""

from __future__ import annotations

import json
import os
import uuid

from config import CONFIG_DIR, ensure_config_dir
from tools.interface_session_view import COLUMN_DEFS, COLUMN_KEYS

INTERFACE_DEBUG_FILE = os.path.join(CONFIG_DIR, 'interface_debug.json')

DEFAULT_UI_PREFS = {
    'visible_columns': [c[0] for c in COLUMN_DEFS if c[1]],
    'column_widths': {c[0]: c[2] for c in COLUMN_DEFS},
    'sort_key': 'time',
    'sort_desc': True,
    'active_filters': ['all'],
    'show_static': False,
    'listen_mode': 'proxy',  # proxy | chromium | ie
    'splitter_sizes': {'wide': [420, 580], 'standard': [400, 560], 'compact': [360, 480], 'narrow': [300, 420]},
    'include_auth_in_draft': True,
}

DEFAULT_CONFIG = {
    'browser_path': '',
    'debug_port': 9222,
    'local_targets': [],
    'default_target_id': '',
    'ie_proxy_port': 8899,
    'ie_certificate_thumbprint': '',
    'proxy_restore_snapshot': None,
    'recent_browser_paths': [],
    'ui_prefs': dict(DEFAULT_UI_PREFS),
}


def _normalize_target(item):
    if not isinstance(item, dict):
        return None
    name = str(item.get('name') or '').strip() or '本地服务'
    base_url = str(item.get('base_url') or '').strip()
    tid = str(item.get('id') or uuid.uuid4().hex)
    return {'id': tid, 'name': name, 'base_url': base_url}


def _normalize_ui_prefs(raw) -> dict:
    base = dict(DEFAULT_UI_PREFS)
    if not isinstance(raw, dict):
        return base
    base.update(raw)
    from tools.interface_session_view import normalize_column_key
    raw_cols = base.get('visible_columns') or []
    cols = []
    for c in raw_cols:
        nk = normalize_column_key(c)
        if nk in COLUMN_KEYS and nk not in cols:
            cols.append(nk)
    if not cols:
        cols = list(DEFAULT_UI_PREFS['visible_columns'])
    # Fiddler 核心列始终可见
    for must in ('seq', 'status', 'method', 'host', 'url'):
        if must not in cols:
            cols.insert(0 if must == 'seq' else len(cols), must)
    # 去重保持顺序
    seen = set()
    ordered = []
    for c in cols:
        if c not in seen and c in COLUMN_KEYS:
            seen.add(c)
            ordered.append(c)
    base['visible_columns'] = ordered
    widths = dict(DEFAULT_UI_PREFS['column_widths'])
    if isinstance(base.get('column_widths'), dict):
        for k, v in base['column_widths'].items():
            nk = normalize_column_key(k)
            if nk in COLUMN_KEYS:
                try:
                    widths[nk] = max(40, min(800, int(v)))
                except (TypeError, ValueError):
                    pass
    base['column_widths'] = widths
    sk = normalize_column_key(base.get('sort_key') or 'time')
    base['sort_key'] = sk if sk in COLUMN_KEYS or sk == 'time' else 'time'
    base['sort_desc'] = bool(base.get('sort_desc', True))
    filters = base.get('active_filters') or ['all']
    if not isinstance(filters, list):
        filters = ['all']
    base['active_filters'] = [str(f) for f in filters] or ['all']
    base['show_static'] = bool(base.get('show_static'))
    mode = str(base.get('listen_mode') or 'proxy').lower()
    if mode not in ('proxy', 'chromium', 'ie'):
        mode = 'proxy'
    base['listen_mode'] = mode
    base['include_auth_in_draft'] = bool(base.get('include_auth_in_draft', True))
    sizes = dict(DEFAULT_UI_PREFS['splitter_sizes'])
    if isinstance(base.get('splitter_sizes'), dict):
        for mode, pair in base['splitter_sizes'].items():
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                try:
                    sizes[str(mode)] = [max(120, int(pair[0])), max(180, int(pair[1]))]
                except (TypeError, ValueError):
                    pass
    base['splitter_sizes'] = sizes
    return base


def normalize_interface_debug_config(data=None) -> dict:
    result = dict(DEFAULT_CONFIG)
    if isinstance(data, dict):
        result.update(data)
    try:
        result['debug_port'] = max(1, min(65535, int(result.get('debug_port') or 9222)))
    except (TypeError, ValueError):
        result['debug_port'] = 9222
    try:
        result['ie_proxy_port'] = max(1, min(65535, int(result.get('ie_proxy_port') or 8899)))
    except (TypeError, ValueError):
        result['ie_proxy_port'] = 8899
    result['browser_path'] = str(result.get('browser_path') or '')
    result['ie_certificate_thumbprint'] = str(result.get('ie_certificate_thumbprint') or '')
    result['default_target_id'] = str(result.get('default_target_id') or '')
    targets = []
    for item in result.get('local_targets') or []:
        norm = _normalize_target(item)
        if norm:
            targets.append(norm)
    result['local_targets'] = targets
    snap = result.get('proxy_restore_snapshot')
    if snap is not None and not isinstance(snap, dict):
        result['proxy_restore_snapshot'] = None
    recent = result.get('recent_browser_paths') or []
    if not isinstance(recent, list):
        recent = []
    cleaned = []
    for p in recent:
        s = str(p or '').strip()
        if s and s not in cleaned:
            cleaned.append(s)
    result['recent_browser_paths'] = cleaned[:8]
    result['ui_prefs'] = _normalize_ui_prefs(result.get('ui_prefs'))
    # setdefault 兼容：确保关键字段始终存在
    for key, default in DEFAULT_CONFIG.items():
        result.setdefault(key, default)
    return result


def load_interface_debug_config(path=None) -> dict:
    target = path or INTERFACE_DEBUG_FILE
    ensure_config_dir()
    try:
        with open(target, 'r', encoding='utf-8') as stream:
            return normalize_interface_debug_config(json.load(stream))
    except (OSError, ValueError, TypeError):
        return normalize_interface_debug_config()


def save_interface_debug_config(config, path=None) -> dict:
    target = path or INTERFACE_DEBUG_FILE
    ensure_config_dir()
    normalized = normalize_interface_debug_config(config)
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(normalized, stream, ensure_ascii=False, indent=2)
    return normalized


def update_ui_prefs(partial: dict, path=None) -> dict:
    cfg = load_interface_debug_config(path)
    prefs = dict(cfg.get('ui_prefs') or {})
    prefs.update(partial or {})
    cfg['ui_prefs'] = prefs
    return save_interface_debug_config(cfg, path)
