# -*- coding: utf-8 -*-
"""共享导航/快捷入口元数据。

MainWindow 侧栏、QuickPanel 悬浮快捷与设置页编辑器必须从本模块读取，
避免三套模块名与图标映射分叉。

Phase 0 导航收敛（一级 3+1+1 + 交付管理例外）：
    0   首页        (DashboardPanel) — 启动默认页，保持首位
    14  数据中心     (父级，仅折叠/展开，不打开页面；见任务 T4-5 说明)
    16  智能对话     (ModelChatPanel) — 原"聊天"，命名冻结
    17  AI 工作台    (AgentWorkbenchPanel) — 原"工作"，命名冻结（真 Harness）
    24  工具箱       (父级，仅折叠/展开，不打开页面；二级收纳 misc 工具)
    7   设置        (SettingsPanel) — 左侧底部

    交付管理分组（需求管理/发版联动/接口文档更新/日报）保持一级不动：
    用户已确认为核心功能，不降级、不移入工具箱（任务 T4-4 例外）。

    证件类型(1)/车辆 VIN(4)：移出主导航一级，降级为工具箱二级，代码保留。
"""

from __future__ import annotations

from dataclasses import dataclass

# 默认悬浮快捷：需求管理、发版联动、日报、加解密
DEFAULT_FLOATING_SHORTCUTS = [10, 2, 9, 5]
MAX_FLOATING_SHORTCUTS = 6

# 视觉导航顺序（stack_index 仍按历史映射，不依赖数组下标当导航顺序）
# (group_key, [(nav_index, name_zh, name_en, icon_role), ...])
# 单条目分组不渲染分组标题（侧栏直接出按钮/父级头，见 MainWindow）。
# 14 = 数据中心父级（子项为 18–23 六数据库面板，由 MainWindow 折叠组渲染）
# 24 = 工具箱父级（子项见 TOOLBOX_CHILDREN，由 MainWindow 折叠组渲染）
NAV_MODEL = [
    ('workspace', [
        (0, '首页', 'Home', 'home'),
    ]),
    ('datacenter', [
        (14, '数据中心', 'Data Center', 'database'),
    ]),
    ('smartchat', [
        (16, '智能对话', 'Smart Chat', 'chat'),
    ]),
    ('aiworkbench', [
        (17, 'AI 工作台', 'AI Workbench', 'workbench'),
    ]),
    ('delivery', [
        (10, '需求管理', 'Requirements', 'requirements'),
        (2, '发版联动', 'Release Link', 'release'),
        (3, '接口文档更新', 'Interface Docs', 'doc-update'),
        (9, '日报', 'Daily Report', 'daily-report'),
    ]),
    ('toolbox', [
        (24, '工具箱', 'Toolbox', ''),
    ]),
]

GROUP_LABELS = {
    # 单条目分组：标题留空，侧栏不渲染分组标题（Web 侧栏收到空标题渲染为细分隔线）
    'workspace': ('', ''),
    'datacenter': ('', ''),
    'smartchat': ('', ''),
    'aiworkbench': ('', ''),
    'delivery': ('交付管理', 'DELIVERY'),
    'toolbox': ('', ''),
}

# ---------------------------------------------------------------------------
# 导航索引常量（Phase 0：数据中心 / 工具箱 两个可折叠父级；"模型"父级已解散，
# 16=智能对话、17=AI 工作台 升为一级入口）
# ---------------------------------------------------------------------------
SQL_CONSOLE_NAV = 14          # 数据中心父级（仅折叠/展开，不打开页面）
TOOLBOX_NAV = 24              # 工具箱父级（仅折叠/展开，不打开页面）
AI_CHAT_NAV = 16              # 智能对话（原"聊天"）
AI_WORKBENCH_NAV = 17         # AI 工作台（原"工作"，真 Harness）

SQL_DB_NAV_START = 18         # 第一个数据库面板索引（Oracle）
SQL_DB_NAV_MAX = 24           # MongoDB + 1（注意：24 现为工具箱父级，不在 DB 槽位内）

# 六数据库面板固定定义：(name_zh, dialect, nav_index, icon_role)
FIXED_DB_PAGES = [
    ('Oracle', 'oracle', 18, 'database'),
    ('MySQL', 'mysql', 19, 'database'),
    ('OceanBase', 'oceanbase', 20, 'database'),
    ('达梦', 'dameng', 21, 'database'),
    ('Redis', 'redis', 22, 'database'),
    ('MongoDB', 'mongodb', 23, 'database'),
]

# dialect → nav index（供运行时按方言定位面板）
DIALECT_NAV_INDEX = {page[1]: page[2] for page in FIXED_DB_PAGES}

# 工具箱二级子项：(nav_index, name_zh, name_en, icon_role)
# 顺序：常用工具在前，彩蛋门控的自我学习、由主导航降级的证件/VIN 在后。
TOOLBOX_CHILDREN = [
    (5, '加解密', 'Crypto', 'shield-key'),
    (12, '接口排查', 'API Debug', 'api-debug'),
    (11, '格式工具', 'Format Tools', 'json'),
    (6, '命令库', 'Command Library', 'operations'),
    (13, '日志排查', 'Log Inspect', 'search'),
    (8, '自我学习', 'Learning', 'learning'),
    (1, '证件类型', 'Documents', 'document-id'),
    (4, '车辆 VIN', 'Vehicle VIN', 'vin'),
]

# 兼容旧命名：动态槽位机制已废弃（v3.0 改为固定六面板）
DB_NAV_START = SQL_DB_NAV_START
DB_NAV_MAX = SQL_DB_NAV_MAX


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
        6: ('Linux 运维命令搜索与安全引导（只生成/复制，不连机）', 'Linux ops command library (generate/copy only)'),
        7: ('界面与悬浮工具栏设置', 'Interface and floating toolbar settings'),
        8: ('自我学习资料整理与全文搜索', 'Learning library and full-text search'),
        9: ('每日日报与定时提醒', 'Daily reports and reminders'),
        10: ('需求归档、上线台账与工具联动', 'Requirement tracking and tool links'),
        11: ('JSON / XML / SQL / 文本辅助离线格式化', 'Offline JSON / XML / SQL / text helpers'),
        12: ('多浏览器接口实时排查与本机请求测试', 'Multi-browser API capture and local request test'),
        13: ('SSH 多机并行日志关键字截取与本地导出', 'SSH multi-host log keyword extract and local export'),
        14: ('数据中心：Oracle / MySQL / OceanBase / 达梦 / Redis / MongoDB', 'Data center for six database engines'),
        16: ('智能对话：内网模型连续对话与配置验证', 'Smart chat: intranet model chat and config verification'),
        17: ('AI 工作台：绑定项目目录执行受控任务', 'AI workbench: bind project dir and run tasks'),
        18: ('Oracle 工作台：SQL 编辑、对象树与结构快照', 'Oracle workbench: SQL editor, object tree, snapshot'),
        19: ('MySQL 工作台：库表浏览与 SQL 编辑', 'MySQL workbench: schema tree and SQL editor'),
        20: ('OceanBase 工作台：SQL 编辑与分区表浏览', 'OceanBase workbench: SQL editor and partition view'),
        21: ('达梦工作台：模式浏览与 SQL 编辑', 'Dameng workbench: schema tree and SQL editor'),
        22: ('Redis 工作台：Key 树浏览、TTL 管理与命令行', 'Redis workbench: key tree, TTL and CLI'),
        23: ('MongoDB 工作台：集合树、文档浏览器与 Shell', 'MongoDB workbench: collections, documents and shell'),
        24: ('工具箱：加解密 / 接口排查 / 格式工具 / 命令库 / 日志排查等', 'Toolbox: crypto, API debug, format tools and more'),
    }
    # 首页固定为底部入口；设置不进悬浮快捷位
    # 16=智能对话、17=AI 工作台 可进悬浮；父级 14/24 不进
    floating_ok = {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 17}
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
    # 工具箱二级子项：证件/VIN 已由主导航一级降级至此，但 NavItem 仍保留，
    # 以保证悬浮快捷、display_name 等引用方正常工作（面板本身未删除）。
    for nav_index, name_zh, name_en, icon_role in TOOLBOX_CHILDREN:
        tip = tooltips.get(nav_index, ('', ''))
        items[nav_index] = NavItem(
            index=nav_index,
            name_zh=name_zh,
            name_en=name_en,
            icon_role=icon_role,
            group_key='toolbox',
            floating_eligible=nav_index in floating_ok,
            requires_easter_egg=(nav_index == 8),
            tooltip_zh=tip[0],
            tooltip_en=tip[1],
        )
    # 六数据库面板
    for name_zh, dialect, nav_index, icon_role in FIXED_DB_PAGES:
        items[nav_index] = NavItem(
            index=nav_index, name_zh=name_zh, name_en=name_zh,
            icon_role=icon_role, group_key='sql_console_db',
            floating_eligible=False, requires_easter_egg=False,
            tooltip_zh=tooltips[nav_index][0], tooltip_en=tooltips[nav_index][1],
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

# 编辑列表展示顺序（不含首页、设置、父级 14/24）
FLOATING_EDIT_ORDER = [16, 17, 10, 2, 3, 9, 5, 13, 6, 12, 11, 1, 4, 8]

# ---------------------------------------------------------------------------
# 导航索引工具函数
# ---------------------------------------------------------------------------

def nav_is_db_slot(index: int) -> bool:
    """判断导航索引是否属于六数据库面板（v3.0：18–23 固定）。"""
    return SQL_DB_NAV_START <= index < SQL_DB_NAV_MAX


def resolve_db_slot_index(index: int) -> int:
    """将 DB 导航索引 (18+) 转成槽位偏移（0-based，0=Oracle … 5=MongoDB）。"""
    return index - SQL_DB_NAV_START


def db_nav_index_from_slot(slot: int) -> int:
    """将槽位偏移（0-based）转成 DB 导航索引。"""
    return SQL_DB_NAV_START + slot


def dialect_for_nav(index: int) -> str:
    """返回导航索引对应的数据库方言（非 DB 索引返回空串）。"""
    if not nav_is_db_slot(index):
        return ''
    slot = resolve_db_slot_index(index)
    if 0 <= slot < len(FIXED_DB_PAGES):
        return FIXED_DB_PAGES[slot][1]
    return ''


def nav_for_dialect(dialect: str) -> int:
    """返回方言对应的导航索引（未知方言返回 SQL_DB_NAV_START）。"""
    return DIALECT_NAV_INDEX.get(str(dialect or '').lower(), SQL_DB_NAV_START)


def is_parent_nav(index: int) -> bool:
    """判断是否为父级折叠组索引（数据中心 / 工具箱）。

    Phase 0-5（假入口方案 a）：父级头不可点击为页面，仅展开/折叠子菜单；
    实测 native/Web 侧栏均已是该行为，无空白页问题，见 T4 报告。
    """
    return index in (SQL_CONSOLE_NAV, TOOLBOX_NAV)


def get_nav_item(index: int) -> NavItem | None:
    return NAV_ITEMS.get(int(index))


def display_name(index: int, language: str = 'zh') -> str:
    if nav_is_db_slot(index):
        slot = resolve_db_slot_index(index)
        if 0 <= slot < len(FIXED_DB_PAGES):
            return FIXED_DB_PAGES[slot][0]
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
