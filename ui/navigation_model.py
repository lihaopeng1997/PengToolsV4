# -*- coding: utf-8 -*-
"""共享导航/快捷入口元数据。

MainWindow 侧栏、QuickPanel 悬浮快捷与设置页编辑器必须从本模块读取，
避免三套模块名与图标映射分叉。
"""

from __future__ import annotations

from dataclasses import dataclass

# 默认悬浮快捷：需求管理、升级准备、日报、加解密
DEFAULT_FLOATING_SHORTCUTS = [10, 2, 9, 5]
MAX_FLOATING_SHORTCUTS = 6

# 视觉导航顺序（stack_index 仍按历史映射，不依赖数组下标当导航顺序）
# (group_key, [(nav_index, name_zh, name_en, icon_role), ...])
NAV_MODEL = [
    ('workspace', [
        (0, '首页', 'Home', 'home'),
    ]),
    ('delivery', [
        (10, '需求管理', 'Requirements', 'requirements'),
        (2, '发版联动', 'Release Link', 'release'),
        (3, '接口文档更新', 'Interface Docs', 'doc-update'),
        (9, '日报', 'Daily Report', 'daily-report'),
    ]),
    ('devtools', [
        (5, '加解密', 'Crypto', 'shield-key'),
        (12, '接口排查', 'API Debug', 'api-debug'),
        (11, '格式工具', 'Format Tools', 'json'),
        (1, '证件类型', 'Documents', 'document-id'),
        (4, '车辆 VIN', 'Vehicle VIN', 'vin'),
        (6, '运维助手', 'Operations', 'operations'),
    ]),
    ('personal', [
        (8, '自我学习', 'Learning', 'learning'),
    ]),
]

GROUP_LABELS = {
    'workspace': ('工作台', 'WORKSPACE'),
    'delivery': ('交付管理', 'DELIVERY'),
    'devtools': ('开发工具', 'DEV TOOLS'),
    'personal': ('个人效率', 'PERSONAL'),
}


@dataclass(frozen=True)
class NavItem:
    index: int
    name_zh: str
    name_en: str
    icon_role: str
    group_key: str
    floating_eligible: bool
    requires_easter_egg: bool
    tooltip_zh: str = ''
    tooltip_en: str = ''


def _build_items() -> dict[int, NavItem]:
    tooltips = {
        0: ('打开完整工作台首页', 'Open full workspace home'),
        1: ('个人与单位证件模拟生成', 'Personal and unit document test data'),
        2: ('发版联动：需求/BUG、SQL 与发版 Excel', 'Release link: requirements, SQL and workbook'),
        3: ('SQL 驱动接口文档更新', 'SQL-driven interface document updater'),
        4: ('中国车辆 VIN 测试数据', 'China vehicle VIN test data'),
        5: ('网关国密解密 · 解密后 JSON 查看', 'Gateway SM decrypt with JSON result view'),
        6: ('Linux 运维命令搜索与安全引导', 'Linux operations command search and safety'),
        7: ('界面与悬浮工具栏设置', 'Interface and floating toolbar settings'),
        8: ('自我学习资料整理与全文搜索', 'Learning library and full-text search'),
        9: ('每日日报与定时提醒', 'Daily reports and reminders'),
        10: ('需求归档、上线台账与工具联动', 'Requirement tracking and tool links'),
        11: ('JSON / XML / SQL / 文本辅助离线格式化', 'Offline JSON / XML / SQL / text helpers'),
        12: ('多浏览器接口实时排查与本机请求测试', 'Multi-browser API capture and local request test'),
    }
    # 首页固定为底部入口；设置不进悬浮快捷位
    # 11 = 格式工具；12 = 接口排查（不改 0–10 历史含义）
    floating_ok = {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12}
    items: dict[int, NavItem] = {}
    for group_key, entries in NAV_MODEL:
        for nav_index, name_zh, name_en, icon_role in entries:
            tip = tooltips.get(nav_index, ('', ''))
            items[nav_index] = NavItem(
                index=nav_index,
                name_zh=name_zh,
                name_en=name_en,
                icon_role=icon_role,
                group_key=group_key,
                floating_eligible=nav_index in floating_ok,
                requires_easter_egg=(nav_index == 8),
                tooltip_zh=tip[0],
                tooltip_en=tip[1],
            )
    # 设置在侧栏底部，不进 NAV_MODEL 分组列表，但导航索引仍有效
    items[7] = NavItem(
        index=7,
        name_zh='设置',
        name_en='Settings',
        icon_role='settings',
        group_key='settings',
        floating_eligible=False,
        requires_easter_egg=False,
        tooltip_zh=tooltips[7][0],
        tooltip_en=tooltips[7][1],
    )
    return items


NAV_ITEMS: dict[int, NavItem] = _build_items()

# 编辑列表展示顺序（不含首页、设置）
FLOATING_EDIT_ORDER = [10, 2, 3, 9, 5, 12, 11, 1, 4, 6, 8]


def get_nav_item(index: int) -> NavItem | None:
    return NAV_ITEMS.get(int(index))


def display_name(index: int, language: str = 'zh') -> str:
    item = get_nav_item(index)
    if item is None:
        return str(index)
    return item.name_zh if language == 'zh' else item.name_en


def display_tooltip(index: int, language: str = 'zh') -> str:
    item = get_nav_item(index)
    if item is None:
        return ''
    return item.tooltip_zh if language == 'zh' else item.tooltip_en


def icon_role_for(index: int) -> str:
    item = get_nav_item(index)
    return item.icon_role if item else 'home'


def floating_candidates(*, private_unlocked: bool = False) -> list[NavItem]:
    """可勾选进悬浮快捷的模块清单。"""
    result = []
    for index in FLOATING_EDIT_ORDER:
        item = NAV_ITEMS[index]
        if item.requires_easter_egg and not private_unlocked:
            continue
        if item.floating_eligible:
            result.append(item)
    return result


def normalize_floating_shortcuts(
    value,
    *,
    private_unlocked: bool = True,
    max_items: int = MAX_FLOATING_SHORTCUTS,
) -> list[int]:
    """去重、过滤非法 index / 未解锁自我学习，保证 1–max 个有效入口。"""
    raw = value if isinstance(value, (list, tuple)) else []
    seen: set[int] = set()
    result: list[int] = []
    for entry in raw:
        try:
            index = int(entry)
        except (TypeError, ValueError):
            continue
        item = get_nav_item(index)
        if item is None or not item.floating_eligible:
            continue
        if item.requires_easter_egg and not private_unlocked:
            continue
        if index in seen:
            continue
        seen.add(index)
        result.append(index)
        if len(result) >= max_items:
            break
    if not result:
        result = list(DEFAULT_FLOATING_SHORTCUTS)
        if not private_unlocked:
            result = [i for i in result if not (get_nav_item(i) and get_nav_item(i).requires_easter_egg)]
            if not result:
                result = [10]
    return result
