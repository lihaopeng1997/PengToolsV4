# -*- coding: utf-8 -*-
"""列表置顶：统一排序、文案前缀与跨模块 pin 状态存储。

- 自有 JSON 条目（需求、接口库等）优先写在对象字段 ``pinned`` / ``pinned_at``。
- 内置/混合列表（运维命令、内置学习资料、日报日期）用 ``list_pins.json`` 按命名空间存 id。
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Callable, Iterable, Optional

from config import CONFIG_DIR, ensure_config_dir

LIST_PINS_FILE = os.path.join(CONFIG_DIR, 'list_pins.json')
PIN_MARK = '📌'
PIN_PREFIX = f'{PIN_MARK} '


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def is_pinned(item: Any) -> bool:
    if isinstance(item, dict):
        return bool(item.get('pinned'))
    return bool(item)


def pinned_at_rank(item: Any) -> str:
    """置顶时间越新越大，便于倒序。"""
    if not isinstance(item, dict):
        return ''
    return str(item.get('pinned_at') or '')


def set_pinned_fields(item: dict, pinned: bool) -> dict:
    """就地/返回同一 dict，写入 pinned 字段。"""
    target = item if isinstance(item, dict) else {}
    target['pinned'] = bool(pinned)
    if pinned:
        target['pinned_at'] = now_iso()
    else:
        target.pop('pinned_at', None)
    return target


def decorate_title(title: str, pinned: bool = False) -> str:
    text = str(title or '')
    if text.startswith(PIN_PREFIX):
        text = text[len(PIN_PREFIX):]
    if pinned:
        return PIN_PREFIX + text
    return text


def pin_sort_key(item: Any, *secondary) -> tuple:
    """置顶优先；同为置顶时 pinned_at 新在前；再按 secondary。"""
    pinned = is_pinned(item)
    return (0 if pinned else 1, '' if pinned else '1', '' if not pinned else '', *(secondary or ()))


def sort_with_pin(items: Iterable[Any], secondary_key: Optional[Callable[[Any], Any]] = None) -> list:
    seq = list(items or [])

    def _key(item):
        sec = secondary_key(item) if secondary_key else ()
        if not isinstance(sec, tuple):
            sec = (sec,)
        # pinned first, then newer pin time, then secondary
        return (
            0 if is_pinned(item) else 1,
            # reverse pinned_at: use negative via string invert is hard; sort with reverse later
            pinned_at_rank(item),
            *sec,
        )

    # stable: pin first, then by secondary; among pins prefer newer pinned_at
    pinned = [x for x in seq if is_pinned(x)]
    plain = [x for x in seq if not is_pinned(x)]
    pinned.sort(key=lambda x: (pinned_at_rank(x),) + ((secondary_key(x),) if secondary_key else ()), reverse=True)
    if secondary_key:
        plain.sort(key=secondary_key)
    return pinned + plain


def load_pin_store(path: Optional[str] = None) -> dict:
    target = path or LIST_PINS_FILE
    try:
        with open(target, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            return {}
        cleaned = {}
        for ns, bucket in data.items():
            if not isinstance(bucket, dict):
                continue
            cleaned[str(ns)] = {
                str(k): {
                    'pinned': bool(v.get('pinned', True) if isinstance(v, dict) else v),
                    'pinned_at': str(v.get('pinned_at') or '') if isinstance(v, dict) else '',
                }
                for k, v in bucket.items()
                if k is not None and str(k)
            }
        return cleaned
    except (OSError, ValueError, TypeError):
        return {}


def save_pin_store(store: dict, path: Optional[str] = None) -> dict:
    target = path or LIST_PINS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)) or '.', exist_ok=True)
    payload = {}
    for ns, bucket in (store or {}).items():
        if not isinstance(bucket, dict):
            continue
        payload[str(ns)] = {
            str(k): {
                'pinned': bool(v.get('pinned')) if isinstance(v, dict) else bool(v),
                'pinned_at': str(v.get('pinned_at') or '') if isinstance(v, dict) else '',
            }
            for k, v in bucket.items()
            if k is not None and str(k) and (
                (isinstance(v, dict) and v.get('pinned')) or (not isinstance(v, dict) and v)
            )
        }
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return payload


def namespace_is_pinned(namespace: str, item_id: str, path: Optional[str] = None) -> bool:
    if not item_id:
        return False
    store = load_pin_store(path)
    bucket = store.get(str(namespace)) or {}
    meta = bucket.get(str(item_id))
    if isinstance(meta, dict):
        return bool(meta.get('pinned'))
    return bool(meta)


def namespace_pinned_at(namespace: str, item_id: str, path: Optional[str] = None) -> str:
    store = load_pin_store(path)
    bucket = store.get(str(namespace)) or {}
    meta = bucket.get(str(item_id)) or {}
    if isinstance(meta, dict):
        return str(meta.get('pinned_at') or '')
    return ''


def set_namespace_pinned(
    namespace: str,
    item_id: str,
    pinned: bool,
    path: Optional[str] = None,
) -> dict:
    if not item_id:
        return load_pin_store(path)
    store = load_pin_store(path)
    ns = str(namespace)
    bucket = dict(store.get(ns) or {})
    key = str(item_id)
    if pinned:
        bucket[key] = {'pinned': True, 'pinned_at': now_iso()}
    else:
        bucket.pop(key, None)
    store[ns] = bucket
    return save_pin_store(store, path)


def apply_namespace_pins(items: Iterable[dict], namespace: str, id_key: str = 'id', path: Optional[str] = None) -> list:
    """给条目拷贝加上命名空间 pin 状态（不写回原对象）。"""
    store = load_pin_store(path)
    bucket = store.get(str(namespace)) or {}
    result = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item_id = str(item.get(id_key) or '')
        meta = bucket.get(item_id) if item_id else None
        if isinstance(meta, dict) and meta.get('pinned'):
            item['pinned'] = True
            item['pinned_at'] = str(meta.get('pinned_at') or item.get('pinned_at') or '')
        elif not item.get('pinned'):
            item['pinned'] = False
        result.append(item)
    return result


def ops_command_pin_id(command: dict) -> str:
    """运维命令稳定 id：command 文本 + 中文标题。"""
    if not isinstance(command, dict):
        return ''
    return f"{command.get('command', '')}\n{command.get('title_zh', '')}"


def pin_action_label(pinned: bool, language: str = 'zh') -> str:
    if language == 'zh':
        return '取消置顶' if pinned else '置顶'
    return 'Unpin' if pinned else 'Pin to top'
