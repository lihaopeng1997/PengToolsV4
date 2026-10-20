# -*- coding: utf-8 -*-
"""需求进度阶段：把细状态归到 5 段，供需求管理与首页共用。"""
from __future__ import annotations

from tools.requirements import STATUSES, test_points_progress

STAGES = (
    ('analysis', '分析', ('待分析',)),
    ('develop', '开发', ('待开发', '开发中')),
    ('testing', '测试', ('待测试', '集成测试', '用户测试', '模拟测试')),
    ('release', '待上线', ('待上线',)),
    ('online', '已上线', ('已上线',)),
)
PAUSED_STATUS = '暂停'

# 分组顺序：先看正在推进的，再看待办、已完成、暂停。
STATUS_GROUP_ORDER = ('develop', 'testing', 'release', 'analysis', 'online', 'paused')
STATUS_GROUP_LABELS = {
    'develop': '开发中',
    'testing': '测试中',
    'release': '待上线',
    'analysis': '待分析',
    'online': '已上线',
    'paused': '暂停',
}
# 仍需推进的分组（首页“进行中”只看这些）。
ACTIVE_GROUPS = ('develop', 'testing', 'release', 'analysis')
# 拖放到分组时写入的代表状态。
GROUP_TARGET_STATUS = {
    'analysis': '待分析',
    'develop': '开发中',
    'testing': '待测试',
    'release': '待上线',
    'online': '已上线',
    'paused': PAUSED_STATUS,
}


# 历史数据/示例里出现过的非标准写法
_STATUS_ALIASES = {
    '进行中': 'develop',
    '测试中': 'testing',
    '已完成': 'online',
}


def status_group(status) -> str:
    text = str(status or '').strip() or '待分析'
    if text == PAUSED_STATUS:
        return 'paused'
    if text in _STATUS_ALIASES:
        return _STATUS_ALIASES[text]
    for key, _label, members in STAGES:
        if text in members:
            return key
    return 'analysis'


def stage_index(status) -> int:
    """当前状态所在阶段下标；暂停返回 -1。"""
    group = status_group(status)
    for index, (key, _label, _members) in enumerate(STAGES):
        if key == group:
            return index
    return -1


def known_statuses() -> tuple[str, ...]:
    return tuple(STATUSES)


def next_action(requirement) -> str:
    """按当前阶段与测试点给出一句“下一步”，只做提示，不改数据。"""
    item = requirement if isinstance(requirement, dict) else {}
    group = status_group(item.get('status'))
    done, total = test_points_progress(item.get('test_points'))
    if group == 'analysis':
        return '确认需求后开始开发'
    if group == 'develop':
        return '补充测试点' if total == 0 else '开发完成后提测'
    if group == 'testing':
        if total == 0:
            return '补充测试点'
        if done < total:
            return f'还剩 {total - done} 个测试点'
        return '测试点已全过，可转待上线'
    if group == 'release':
        return '提签并准备升级 SQL'
    if group == 'paused':
        return '已暂停'
    return '已上线'
