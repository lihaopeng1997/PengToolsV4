# -*- coding: utf-8 -*-
"""AI 输入会话的表/字段上下文。Token 与该模型一一对应，不从普通文本反推。"""

from __future__ import annotations

import uuid


def new_token_id() -> str:
    return uuid.uuid4().hex[:12]


def empty_context(snapshot=None) -> dict:
    snap = snapshot if isinstance(snapshot, dict) else {}
    return {
        'snapshot_id': str(snap.get('snapshot_id') or ''),
        'connection_fingerprint': str(snap.get('fingerprint') or ''),
        'selected_objects': [],
        'selected_fields': [],
    }


def qualified_name(obj: dict) -> str:
    owner = str((obj or {}).get('owner') or '').strip()
    name = str((obj or {}).get('name') or '').strip()
    if owner and name:
        return f'{owner}.{name}'
    return name or owner


def field_qualified(obj: dict, field: dict | str) -> str:
    col = field.get('name') if isinstance(field, dict) else field
    qn = qualified_name(obj)
    name = str(col or '').strip()
    return f'{qn}.{name}' if qn and name else name


def context_matches_snapshot(context: dict, snapshot: dict | None, item: dict | None) -> tuple[bool, str]:
    ctx = context if isinstance(context, dict) else {}
    snap = snapshot if isinstance(snapshot, dict) else None
    if not item:
        return False, '未选择连接'
    if not snap or not snap.get('objects'):
        return False, '尚未扫描结构'
    from tools.schema_snapshot import connection_fingerprint, snapshot_status
    status = snapshot_status(item, snap)
    if status.get('stale') or status.get('status') in ('missing', 'failed', 'empty'):
        return False, status.get('label') or '快照无效'
    if snap.get('fingerprint') and snap.get('fingerprint') != connection_fingerprint(item):
        return False, '快照不属于当前连接'
    if ctx.get('connection_fingerprint') and ctx.get('connection_fingerprint') != snap.get('fingerprint'):
        return False, '上下文快照与当前连接不一致'
    if ctx.get('snapshot_id') and snap.get('snapshot_id') and ctx.get('snapshot_id') != snap.get('snapshot_id'):
        return False, '快照已更新，请重新添加对象'
    return True, ''


def add_object(context: dict, obj: dict) -> dict | None:
    ctx = context if isinstance(context, dict) else empty_context()
    qn = qualified_name(obj)
    if not qn:
        return None
    for item in ctx.get('selected_objects') or []:
        if str(item.get('qualified_name') or '') == qn:
            return item
    token = {
        'token_id': new_token_id(),
        'kind': 'object',
        'qualified_name': qn,
        'name': str(obj.get('name') or ''),
        'owner': str(obj.get('owner') or ''),
        'object_type': str(obj.get('object_type') or 'TABLE'),
        'comment': str(obj.get('comment') or ''),
        'inferred': bool(obj.get('inferred')),
    }
    ctx.setdefault('selected_objects', []).append(token)
    return token


def add_field(context: dict, obj: dict, field: dict) -> dict | None:
    ctx = context if isinstance(context, dict) else empty_context()
    qn = field_qualified(obj, field)
    if not qn:
        return None
    for item in ctx.get('selected_fields') or []:
        if str(item.get('qualified_name') or '') == qn:
            return item
    obj_token = add_object(ctx, obj)
    token = {
        'token_id': new_token_id(),
        'kind': 'field',
        'qualified_name': qn,
        'name': str(field.get('name') or ''),
        'data_type': str(field.get('data_type') or ''),
        'comment': str(field.get('comment') or ''),
        'object_qualified_name': qualified_name(obj),
        'object_token_id': (obj_token or {}).get('token_id') or '',
        'inferred': bool(obj.get('inferred')),
    }
    ctx.setdefault('selected_fields', []).append(token)
    return token


def remove_token(context: dict, token_id: str) -> None:
    ctx = context if isinstance(context, dict) else {}
    wanted = str(token_id or '')
    ctx['selected_objects'] = [item for item in ctx.get('selected_objects') or [] if item.get('token_id') != wanted]
    ctx['selected_fields'] = [item for item in ctx.get('selected_fields') or [] if item.get('token_id') != wanted]


def keep_tokens(context: dict, token_ids) -> None:
    alive = {str(item) for item in (token_ids or []) if str(item)}
    ctx = context if isinstance(context, dict) else {}
    ctx['selected_objects'] = [item for item in ctx.get('selected_objects') or [] if item.get('token_id') in alive]
    ctx['selected_fields'] = [item for item in ctx.get('selected_fields') or [] if item.get('token_id') in alive]


def selected_table_names(context: dict) -> list[str]:
    return [str(item.get('name') or item.get('qualified_name') or '') for item in (context or {}).get('selected_objects') or []]


def selected_field_names(context: dict) -> list[str]:
    return [str(item.get('name') or '') for item in (context or {}).get('selected_fields') or []]
