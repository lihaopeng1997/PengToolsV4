# -*- coding: utf-8 -*-
"""共享导航/快捷入口元数据。

MainWindow 侧栏、QuickPanel 悬浮快捷与设置页编辑器必须从本模块读取，
避免三套模块名与图标映射分叉。

v3.0 导航索引分配：
    0   首页        (DashboardPanel)
    1   证件类型     (CreditCodePanel)
    2   发版联动     (SqlToolPanel)
    3   接口文档更新  (DocxUpdatePanel)
    4   车辆 VIN    (VinPanel)
    5   加解密       (GatewayDecodePanel)
    6   命令库       (OpsPanel)
    7   设置         (SettingsPanel) — 左侧底部
    8   自我学习     (PersonalPanel)
    9   日报         (PersonalPanel, stack reuse)
    10  需求管理     (RequirementPanel)
    11  格式工具     (FormatToolsPanel)
    12  接口排查     (InterfaceDebugPanel)
    13  日志排查     (OpsLogPanel)

    ── 以下为 v3.0 重构区域 ──
    14  数据中心     (父级，仅折叠/展开，不打开页面)
    15  模型         (父级，仅折叠/展开，不打开页面)
    16  聊天         (ModelChatPanel)
    17  工作         (AgentWorkbenchPanel)
    18  Oracle       (OracleWorkbenchPanel)
    19  MySQL        (MySQLWorkbenchPanel)
    20  OceanBase    (OceanBaseWorkbenchPanel)
    21  达梦         (DamengWorkbenchPanel)
    22  Redis        (RedisWorkbenchPanel)
    23  MongoDB      (MongoDBWorkbenchPanel)
"""

from __future__ import annotations

from dataclasses import dataclass

# 默认悬浮快捷：需求管理、升级准备、日报、加解密
DEFAULT_FLOATING_SHORTCUTS = [10, 2, 9, 5]
MAX_FLOATING_SHORTCUTS = 6

# 视觉导航顺序（stack_index 仍按历史映射，不依赖数组下标当导航顺序）
# (group_key, [(nav_index, name_zh, name_en, icon_role), ...])
# 14 = 数据中心父级，15 = 模型父级（子项由 MainWindow 侧栏折叠组渲染）
NAV_MODEL = [
    ('workspace', [
        (0, '首页', 'Home', 'home'),
        (14, '数据中心', 'Data Center', 'database'),
    ]),
    ('ai', [
        (15, '模型', 'AI', 'chat'),
    ]),
    ('delivery', [
        (10, '需求管理', 'Requirements', 'requirements'),
        (2, '发版联动', 'Release Link', 'release'),
        (3, '接口文档更新', 'Interface Docs', 'doc-update'),
        (9, '日报', 'Daily Report', 'daily-report'),
    ]),
    ('ops', [
        (13, '日志排查', 'Log Inspect', 'search'),
        (6, '命令库', 'Command Library', 'operations'),
    ]),
    ('devtools', [
        (5, '加解密', 'Crypto', 'shield-key'),
        (12, '接口排查', 'API Debug', 'api-debug'),
        (11, '格式工具', 'Format Tools', 'json'),
        (1, '证件类型', 'Documents', 'document-id'),
        (4, '车辆 VIN', 'Vehicle VIN', 'vin'),
    ]),
    ('personal', [
        (8, '自我学习', 'Learning', 'learning'),
    ]),
]

GROUP_LABELS = {
    'workspace': ('工作台', 'WORKSPACE'),
    'ai': ('智能助手', 'AI ASSISTANT'),
    'delivery': ('交付管理', 'DELIVERY'),
    'ops': ('运维工作台', 'OPERATIONS'),
    'devtools': ('开发工具', 'DEV TOOLS'),
    'personal': ('个人效率', 'PERSONAL'),
}

# ---------------------------------------------------------------------------
# v3.0 导航索引常量（数据中心 / 模型 两组可折叠子菜单）
# ---------------------------------------------------------------------------
SQL_CONSOLE_NAV = 14          # 数据中心父级（仅折叠/展开）
AI_PARENT_NAV = 15            # 模型父级（仅折叠/展开）
AI_CHAT_NAV = 16              # 聊天
AI_WORKBENCH_NAV = 17         # 工作

SQL_DB_NAV_START = 18         # 第一个数据库面板索引（Oracle）
SQL_DB_NAV_MAX = 24           # MongoDB + 1

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
    dashboard_label: str = ''


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
        15: ('模型：内网模型聊天与 Agent 工作台', 'AI: intranet model chat and agent workbench'),
        16: ('内网模型连续对话与配置验证', 'Intranet model chat and config verification'),
        17: ('Agent 工作台：绑定项目目录执行受控任务', 'Agent workbench: bind project dir and run tasks'),
        18: ('Oracle 工作台：SQL 编辑、对象树与结构快照', 'Oracle workbench: SQL editor, object tree, snapshot'),
        19: ('MySQL 工作台：库表浏览与 SQL 编辑', 'MySQL workbench: schema tree and SQL editor'),
        20: ('OceanBase 工作台：SQL 编辑与分区表浏览', 'OceanBase workbench: SQL editor and partition view'),
        21: ('达梦工作台：模式浏览与 SQL 编辑', 'Dameng workbench: schema tree and SQL editor'),
        22: ('Redis 工作台：Key 树浏览、TTL 管理与命令行', 'Redis workbench: key tree, TTL and CLI'),
        23: ('MongoDB 工作台：集合树、文档浏览器与 Shell', 'MongoDB workbench: collections, documents and shell'),
    }
    # 首页固定为底部入口；设置不进悬浮快捷位
    # 16=聊天、17=工作 可进悬浮；父级 14/15 不进
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
    # 聊天 / 工作 两个子项（挂在"模型"父级下）
    items[16] = NavItem(
        index=16, name_zh='聊天', name_en='Chat', icon_role='chat',
        group_key='ai', floating_eligible=True, requires_easter_egg=False,
        tooltip_zh=tooltips[16][0], tooltip_en=tooltips[16][1],
        dashboard_label='模型对话',
    )
    items[17] = NavItem(
        index=17, name_zh='工作', name_en='Work', icon_role='workbench',
        group_key='ai', floating_eligible=True, requires_easter_egg=False,
        tooltip_zh=tooltips[17][0], tooltip_en=tooltips[17][1],
    )
    # 六数据库面板
    for name_zh, dialect, nav_index, icon_role in FIXED_DB_PAGES:
        items[nav_index] = NavItem(
            index=nav_index, name_zh=name_zh, name_en=name_zh,
            icon_role=icon_role, group_key='sql_console_db',
            floating_eligible=False, requires_easter_egg=False,
            tooltip_zh=tooltips[nav_index][0], tooltip_en=tooltips[nav_index][1],
            dashboard_label='数据中心' if nav_index == 18 else '',
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

# 编辑列表展示顺序（不含首页、设置、父级 14/15）
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
    """判断是否为父级折叠组索引（SQL 控制台 / 模型）。"""
    return index in (SQL_CONSOLE_NAV, AI_PARENT_NAV)


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


DASHBOARD_QUICK_TOOL_INDICES = (18, 16, 12, 13, 5, 11)


def get_dashboard_quick_tools() -> list[dict]:
    """从权威导航模型 NAV_ITEMS 单一派生 Dashboard 6 大常用工具。"""
    tools = []
    for idx in DASHBOARD_QUICK_TOOL_INDICES:
        item = get_nav_item(idx)
        if not item:
            continue
        tools.append({
            'i': idx,
            'zh': item.dashboard_label or item.name_zh,
            'ds': item.tooltip_zh,
            'icon': item.icon_role,
        })
    return tools


def build_web_nav_model_data(*, private_unlocked: bool = False, current: int = 0) -> dict:
    """侧栏与首页 Web 渲染共享数据：唯一权威 ui/navigation_model.py。"""
    dia_short = {
        'oracle': 'ORA', 'mysql': 'MY', 'oceanbase': 'OB',
        'dameng': 'DM', 'redis': 'KV', 'mongodb': 'DOC',
    }
    groups = []
    for key, entries in NAV_MODEL:
        items = []
        for nav_index, name_zh, name_en, icon_role in entries:
            if nav_index == 8 and not private_unlocked:
                continue
            info = NAV_ITEMS[nav_index]
            entry = {
                'i': nav_index,
                'zh': name_zh,
                'en': name_en,
                'dash_zh': info.dashboard_label or name_zh,
                'icon': icon_role,
                'tip': info.tooltip_zh,
            }
            if nav_index == 14:
                entry['children'] = [
                    {
                        'i': i,
                        'zh': zh,
                        'en': zh,
                        'dash_zh': NAV_ITEMS[i].dashboard_label or zh,
                        'icon': icon,
                        'dia': dia_short.get(dialect, dialect[:2].upper()),
                    }
                    for zh, dialect, i, icon in FIXED_DB_PAGES
                ]
            elif nav_index == 15:
                entry['children'] = [
                    {
                        'i': AI_CHAT_NAV,
                        'zh': '聊天',
                        'en': 'CHAT',
                        'dash_zh': NAV_ITEMS[AI_CHAT_NAV].dashboard_label or '聊天',
                        'icon': 'chat',
                    },
                    {
                        'i': AI_WORKBENCH_NAV,
                        'zh': '工作',
                        'en': 'AGENT',
                        'dash_zh': NAV_ITEMS[AI_WORKBENCH_NAV].dashboard_label or '工作',
                        'icon': 'spark',
                    },
                ]
            items.append(entry)
        zh_label, en_label = GROUP_LABELS[key]
        groups.append({'key': key, 'zh': zh_label, 'en': en_label, 'items': items})
    return {
        'groups': groups,
        'settings': {'i': 7, 'zh': '设置', 'en': 'SET', 'dash_zh': '设置', 'icon': 'gear'},
        'current': int(current or 0),
    }
