"""User-defined status names and ordered progress nodes, with stable semantics."""
from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile

GROUPS = {'analysis': '待分析', 'develop': '开发中', 'testing': '测试中',
          'release': '待上线', 'online': '已上线', 'paused': '暂停'}
DEFAULT_ROWS = [
    {'name': name, 'stage': stage, 'group': group}
    for group, stage, names in (
        ('analysis', '分析', ('待分析',)), ('develop', '开发', ('待开发', '开发中')),
        ('testing', '测试', ('待测试', '集成测试', '用户测试', '模拟测试')),
        ('release', '待上线', ('待上线',)), ('online', '已上线', ('已上线',)),
        ('paused', '', ('暂停',)),
    ) for name in names
]


def validate_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError('请配置1～32个状态')
    result, names, stages = [], set(), {}
    for row in rows:
        name, stage, group = (str(row.get(k, '')).strip() for k in ('name', 'stage', 'group'))
        if not name or len(name) > 20 or name in names:
            raise ValueError('状态名称不能为空、不能重复，最多20字')
        if group not in GROUPS:
            raise ValueError('请选择状态的业务分类')
        if group == 'paused':
            stage = ''
        elif not stage or len(stage) > 12:
            raise ValueError('非暂停状态需要进度节点名称，最多12字')
        if stage and stage in stages and stages[stage] != group:
            raise ValueError('同一进度节点的业务分类必须一致')
        if stage:
            stages[stage] = group
        names.add(name)
        result.append({'name': name, 'stage': stage, 'group': group})
    if not 1 <= len(stages) <= 12:
        raise ValueError('请配置1～12个进度节点')
    return result


def workflow_path():
    from config import CONFIG_DIR
    return Path(CONFIG_DIR) / 'requirement_workflow.json'


def validate_statuses(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError('请配置1～32个任务状态')
    result, names = [], set()
    for row in rows:
        name = str(row.get('name', '')).strip()
        group = row.get('group')
        if not name or len(name) > 20 or name in names:
            raise ValueError('状态名称不能为空、不能重复，最多20字')
        if group not in GROUPS:
            raise ValueError('请选择状态的业务分类')
        names.add(name)
        result.append({'name': name, 'group': group})
    return result


def nodes_from_rows(rows):
    nodes = {}
    for row in rows:
        if row.get('stage'):
            nodes.setdefault(row['stage'], []).append(row['name'])
    return [{'name': name, 'statuses': members,
             'default_status': next((s for s in ('开发中', '集成测试') if s in members), members[0])}
            for name, members in nodes.items()]


def validate_nodes(nodes, statuses):
    if not isinstance(nodes, list) or not 0 <= len(nodes) <= 12:
        raise ValueError('最多配置12个进度节点')
    available = {s['name'] for s in statuses}
    names, assigned, result = set(), set(), []
    for node in nodes:
        name = str(node.get('name', '')).strip()
        members = node.get('statuses', [])
        default = node.get('default_status', '')
        if not name or len(name) > 12 or name in names:
            raise ValueError('节点名称不能为空、不能重复，最多12字')
        if not isinstance(members, list) or not members or any(s not in available for s in members):
            raise ValueError(f'节点“{name}”需要关联已有任务状态；请先调整节点关联，再删除被引用的状态')
        if len(set(members)) != len(members) or assigned.intersection(members):
            raise ValueError('同一个任务状态只能关联一个进度节点')
        if default not in members:
            raise ValueError(f'节点“{name}”的默认状态必须属于该节点关联的状态')
        names.add(name)
        assigned.update(members)
        result.append({'name': name, 'statuses': list(members), 'default_status': default})
    return result


def _defaults():
    return {'statuses': validate_statuses(DEFAULT_ROWS), 'nodes': nodes_from_rows(DEFAULT_ROWS), 'retired_groups': {}}


def _migrate_legacy_rows(rows):
    """旧版把状态和节点合并编辑，拆分时恢复原有细分状态。"""
    existing = {row['name']: row for row in rows}
    stages_by_group = {}
    for row in rows:
        if row['stage']:
            stages_by_group.setdefault(row['group'], set()).add(row['stage'])
    result = []
    for default in DEFAULT_ROWS:
        row = dict(existing.get(default['name'], default))
        stages = stages_by_group.get(row['group'], set())
        if row['name'] not in existing and len(stages) == 1:
            row['stage'] = next(iter(stages))
        result.append(row)
    default_names = {row['name'] for row in DEFAULT_ROWS}
    result.extend(dict(row) for row in rows if row['name'] not in default_names)
    return result


@lru_cache(maxsize=8)
def _read(path, mtime, size):
    try:
        obj = json.loads(Path(path).read_text(encoding='utf-8'))
        legacy = obj.get('retired_groups', {})
        legacy = {str(name): group for name, group in legacy.items() if group in GROUPS} if isinstance(legacy, dict) else {}
        if obj.get('version') == 2:
            statuses = validate_statuses(obj['statuses'])
            nodes = validate_nodes(obj['nodes'], statuses)
        else:
            rows = _migrate_legacy_rows(validate_rows(obj['statuses']))
            statuses, nodes = validate_statuses(rows), nodes_from_rows(rows)
        return {'statuses': statuses, 'nodes': nodes, 'retired_groups': legacy}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return _defaults()


def _configuration():
    path = workflow_path()
    try:
        info = path.stat()
        return deepcopy(_read(str(path), info.st_mtime_ns, info.st_size))
    except OSError:
        return _defaults()


def load_workflow():
    return _configuration()['statuses']


def save_workflow(rows):
    """Compatibility entry for the original combined configuration."""
    rows = validate_rows(rows)
    _save_configuration(validate_statuses(rows), nodes_from_rows(rows))


def load_nodes():
    return _configuration()['nodes']


def save_nodes(nodes):
    _save_configuration(load_workflow(), nodes)


def save_statuses(rows, renames=None):
    nodes = load_nodes()
    renames = renames or {}
    for node in nodes:
        node['statuses'] = [renames.get(s, s) for s in node['statuses']]
        node['default_status'] = renames.get(node['default_status'], node['default_status'])
    _save_configuration(rows, nodes)


def _save_configuration(rows, nodes):
    rows = validate_statuses(rows)
    nodes = validate_nodes(nodes, rows)
    previous = _configuration()
    retired = dict(previous['retired_groups'])
    retired.update({row['name']: row['group'] for row in previous['statuses']})
    for row in rows:
        retired.pop(row['name'], None)
    path = workflow_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix='.workflow-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as out:
            json.dump({'version': 2, 'statuses': rows, 'nodes': nodes, 'retired_groups': retired}, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    _read.cache_clear()


def configured_statuses():
    return tuple(row['name'] for row in load_workflow())


def initial_status():
    rows = load_workflow()
    return next((row['name'] for row in rows if row['group'] == 'analysis'), rows[0]['name'])


def configured_stages():
    return tuple((node['name'], node['name'], tuple(node['statuses'])) for node in load_nodes())


def node_default_status(name):
    return next((node['default_status'] for node in load_nodes() if node['name'] == name), '')


def workflow_stage_index(status):
    return next((i for i, (_key, _label, names) in enumerate(configured_stages()) if status in names), -1)


def configured_group(status):
    config = _configuration()
    return next((row['group'] for row in config['statuses'] if row['name'] == status), config['retired_groups'].get(status))


def target_status(group, fallback):
    rows = [row['name'] for row in load_workflow() if row['group'] == group]
    return fallback if fallback in rows or not rows else rows[0]
