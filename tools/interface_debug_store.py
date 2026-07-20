# -*- coding: utf-8 -*-
"""接口排查持久配置：仅保存路径/端口/地址/证书指纹，不含报文。"""

from __future__ import annotations

import json
import os
import uuid

from config import CONFIG_DIR, ensure_config_dir

INTERFACE_DEBUG_FILE = os.path.join(CONFIG_DIR, 'interface_debug.json')

DEFAULT_CONFIG = {
    'browser_path': '',
    'debug_port': 9222,
    'local_targets': [],
    'default_target_id': '',
    'ie_proxy_port': 8899,
    'ie_certificate_thumbprint': '',
    'proxy_restore_snapshot': None,
}


def _normalize_target(item):
    if not isinstance(item, dict):
        return None
    name = str(item.get('name') or '').strip() or '本地服务'
    base_url = str(item.get('base_url') or '').strip()
    tid = str(item.get('id') or uuid.uuid4().hex)
    return {'id': tid, 'name': name, 'base_url': base_url}


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
