# -*- coding: utf-8 -*-
"""首页就地操作：查看详情、改状态、勾测试点、改字段、快速新建。

首页（Web）通过 HomeBridge.homeAction 传入 JSON 指令，这里只做校验与台账读写，
不依赖 Qt；写入一律走 tools.requirements 的原子保存，与需求管理共用同一份数据。
"""
from __future__ import annotations

import datetime
import os
import uuid
from collections.abc import Mapping
from typing import Any

from tools.requirement_stages import (
    GROUP_TARGET_STATUS,
    STATUS_GROUP_LABELS,
    next_action,
    stage_index,
    status_group,
)
from tools.requirements import (
    ONLINE_STATUS,
    PRIORITIES,
    STATUSES,
    classify_requirement,
    load_requirements,
    mark_requirement_online,
    normalize_requirement,
    normalize_test_points,
    save_requirements,
    systems_display_text,
    requirement_systems,
    test_points_progress,
    update_requirement_by_id,
    valid_iso_date,
)

# 首页只允许改这些字段；其余（SVN、目录、SQL 等）仍在需求管理里维护。
EDITABLE_FIELDS = ('title', 'code', 'priority', 'actual_release_date')
_MAX_TITLE = 200
_MAX_CODE = 80
_MAX_POINT = 160


class HomeActionError(ValueError):
    pass


def requirement_detail(item: Mapping[str, Any]) -> dict[str, Any]:
    data = normalize_requirement(dict(item or {}))
    status = str(data.get('status') or '待分析')
    done, total = test_points_progress(data.get('test_points'))
    local_path = str(data.get('dev_local_path') or data.get('local_path') or '')
    return {
        'id': str(data.get('id') or ''),
        'code': str(data.get('code') or ''),
        'title': str(data.get('title') or ''),
        'status': status,
        'group': status_group(status),
        'group_label': STATUS_GROUP_LABELS.get(status_group(status), ''),
        'stage': stage_index(status),
        'priority': str(data.get('priority') or '普通'),
        'category': str(data.get('category') or ''),
        'kind': str(data.get('record_kind') or '需求'),
        'systems': list(requirement_systems(data)),
        'system': systems_display_text(data, empty=''),
        'actual_release_date': str(data.get('actual_release_date') or ''),
        'test_points': data.get('test_points') or [],
        'tp_done': done,
        'tp_total': total,
        'next_action': next_action(data),
        'has_local_dir': bool(local_path and os.path.isdir(local_path)),
        'svn_url': str(data.get('svn_url') or ''),
        'updated_at': str(data.get('updated_at') or data.get('source_modified_at') or ''),
        'description': str(data.get('description') or '')[:2000],
    }


def local_dir_for(requirement_id: str, path=None) -> str:
    item = _find(requirement_id, path)
    if item is None:
        return ''
    folder = str(item.get('dev_local_path') or item.get('local_path') or '')
    return folder if folder and os.path.isdir(folder) else ''


def _find(requirement_id: str, path=None) -> dict | None:
    wanted = str(requirement_id or '')
    if not wanted:
        return None
    return next((item for item in load_requirements(path) if str(item.get('id') or '') == wanted), None)


def _require_id(action: Mapping[str, Any]) -> str:
    rid = str(action.get('id') or '').strip()
    if not rid or rid.startswith('demo-'):
        raise HomeActionError('示例数据不可修改')
    return rid


def _clean_text(value: Any, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _set_status(rid: str, status: str, path=None) -> dict | None:
    if status not in STATUSES:
        raise HomeActionError('未知状态')
    if status == ONLINE_STATUS:
        return mark_requirement_online(rid, path=path)

    def _apply(item):
        item['status'] = status

    return update_requirement_by_id(rid, _apply, path=path)


def _move_group(rid: str, group: str, path=None) -> dict | None:
    if group not in GROUP_TARGET_STATUS:
        raise HomeActionError('未知阶段')
    current = _find(rid, path)
    if current is None:
        return None
    # 同组内保留更细的状态（例如“用户测试”拖到“测试”列不被改回“待测试”）
    if status_group(current.get('status')) == group:
        return normalize_requirement(current)
    return _set_status(rid, GROUP_TARGET_STATUS[group], path)


def _update_fields(rid: str, fields: Any, path=None) -> dict | None:
    if not isinstance(fields, Mapping):
        raise HomeActionError('字段格式不正确')
    changes: dict[str, str] = {}
    for key, value in fields.items():
        if key not in EDITABLE_FIELDS:
            raise HomeActionError(f'首页不能修改字段：{key}')
        if key == 'title':
            text = _clean_text(value, _MAX_TITLE)
            if not text:
                raise HomeActionError('标题不能为空')
            changes[key] = text
        elif key == 'code':
            changes[key] = _clean_text(value, _MAX_CODE)
        elif key == 'priority':
            if value not in PRIORITIES:
                raise HomeActionError('未知优先级')
            changes[key] = str(value)
        elif key == 'actual_release_date':
            text = str(value or '').strip()
            day = valid_iso_date(text)
            if text and not day:
                raise HomeActionError('日期格式应为 YYYY-MM-DD')
            changes[key] = day

    def _apply(item):
        for key, value in changes.items():
            item[key] = value
            if key == 'actual_release_date':
                item['actual_online_date'] = value

    return update_requirement_by_id(rid, _apply, path=path)


def _save_points(rid: str, points: Any, path=None) -> dict | None:
    if not isinstance(points, list):
        raise HomeActionError('测试点格式不正确')
    cleaned = []
    for point in points[:200]:
        if isinstance(point, Mapping):
            text = _clean_text(point.get('text'), _MAX_POINT)
            if text:
                cleaned.append({'id': str(point.get('id') or ''), 'text': text, 'done': bool(point.get('done'))})
    normalized = normalize_test_points(cleaned)

    def _apply(item):
        item['test_points'] = normalized

    return update_requirement_by_id(rid, _apply, path=path)


def _create(action: Mapping[str, Any], path=None) -> dict:
    title = _clean_text(action.get('title'), _MAX_TITLE)
    if not title:
        raise HomeActionError('请填写需求标题')
    group = str(action.get('group') or 'develop')
    status = GROUP_TARGET_STATUS.get(group)
    if status is None:
        raise HomeActionError('未知阶段')
    now = datetime.datetime.now().isoformat(timespec='seconds')
    item = {
        'id': uuid.uuid4().hex,
        'code': _clean_text(action.get('code'), _MAX_CODE),
        'title': title,
        'status': status,
        'priority': '普通',
        'category': classify_requirement(title),
        'record_kind': '需求',
        'created_at': now,
        'updated_at': now,
    }
    day = valid_iso_date(str(action.get('actual_release_date') or ''))
    if day:
        item['actual_release_date'] = day
    if status == ONLINE_STATUS and not day:
        item['actual_release_date'] = datetime.date.today().isoformat()
    items = load_requirements(path)
    created = normalize_requirement(item)
    items.insert(0, created)
    save_requirements(items, path)
    return created


def handle(action: Any, path=None) -> dict[str, Any]:
    """执行一条首页指令，返回 ``{ok, item?, error?}``；不抛异常给桥。"""
    try:
        if not isinstance(action, Mapping):
            raise HomeActionError('指令格式不正确')
        kind = str(action.get('action') or '')
        if kind == 'detail':
            item = _find(str(action.get('id') or ''), path)
            if item is None:
                raise HomeActionError('需求不存在或已删除')
            return {'ok': True, 'item': requirement_detail(item), 'statuses': list(STATUSES),
                    'priorities': list(PRIORITIES)}
        if kind == 'create':
            created = _create(action, path)
            return {'ok': True, 'changed': True, 'item': requirement_detail(created)}
        rid = _require_id(action)
        if kind == 'set_status':
            updated = _set_status(rid, str(action.get('status') or ''), path)
        elif kind == 'move_group':
            updated = _move_group(rid, str(action.get('group') or ''), path)
        elif kind == 'update_fields':
            updated = _update_fields(rid, action.get('fields'), path)
        elif kind == 'save_test_points':
            updated = _save_points(rid, action.get('points'), path)
        else:
            raise HomeActionError('不支持的操作')
        if updated is None:
            raise HomeActionError('需求不存在或已删除')
        return {'ok': True, 'changed': True, 'item': requirement_detail(updated),
                'statuses': list(STATUSES), 'priorities': list(PRIORITIES)}
    except HomeActionError as exc:
        return {'ok': False, 'error': str(exc)}
    except OSError:
        return {'ok': False, 'error': '保存失败：台账文件无法写入'}


__all__ = ['EDITABLE_FIELDS', 'HomeActionError', 'handle', 'local_dir_for', 'requirement_detail']
