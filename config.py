# -*- coding: utf-8 -*-
import json
import os
import sys

def local_data_dir(executable=None, frozen=None):
    is_frozen = getattr(sys, 'frozen', False) if frozen is None else bool(frozen)
    if is_frozen:
        return os.path.join(os.path.dirname(executable or sys.executable), 'data')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


# 版本与构建日期（build_private_release.ps1 打包时会写入 resources/build_info.json）
APP_NAME = 'PengToolsHub'
APP_VERSION = '4.27'
APP_EDITION = 'Private'


def _load_build_info():
    candidates = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(os.path.join(meipass, 'resources', 'build_info.json'))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, 'resources', 'build_info.json'))
    for path in candidates:
        try:
            with open(path, 'r', encoding='utf-8') as stream:
                data = json.load(stream)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError, TypeError):
            continue
    return {}


_BUILD_INFO = _load_build_info()
APP_BUILD_DATE = str(_BUILD_INFO.get('build_date') or '2026-07-17')
APP_VERSION_LABEL = f"V{_BUILD_INFO.get('version') or APP_VERSION}"


def app_version_text(with_date=True):
    base = f'{APP_VERSION_LABEL} {APP_EDITION}'
    if with_date and APP_BUILD_DATE:
        return f'{base} · {APP_BUILD_DATE}'
    return base


CONFIG_DIR = local_data_dir()

SYSTEMS_FILE = os.path.join(CONFIG_DIR, 'systems.json')
CUSTOM_OPS_FILE = os.path.join(CONFIG_DIR, 'ops_custom_commands.json')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')
PRIVATE_KNOWLEDGE_FILE = os.path.join(CONFIG_DIR, 'private_knowledge.json')
DAILY_REPORTS_FILE = os.path.join(CONFIG_DIR, 'daily_reports.json')
DAILY_REPORT_SETTINGS_FILE = os.path.join(CONFIG_DIR, 'daily_report_settings.json')
REQUIREMENTS_FILE = os.path.join(CONFIG_DIR, 'requirements.json')
REQUIREMENT_UI_FILE = os.path.join(CONFIG_DIR, 'requirement_ui.json')
SVN_WORKSPACE_DIR = os.path.join(CONFIG_DIR, 'svn_workspaces')
DEFAULT_SETTINGS = {
    'font_size': 12,
    'ui_theme': 'calm',  # calm | clear | warm | night
    'floating_opacity': 96,
    'floating_always_on_top': True,
    'floating_show_on_startup': True,
    # 悬浮快捷入口：需求管理、升级准备、日报、加解密（导航 index）
    'floating_shortcuts': [10, 2, 9, 5],
    'copy_feedback_ms': 1500,
    'default_language': 'zh',
    'close_ask_each_time': True,
    'close_default_action': 'minimize',
    'keep_awake_enabled': False,
    'keep_awake_interval_minutes': 3,
    # 彩蛋「自我学习」解锁：写在 data/settings.json，升级换 EXE 后仍保留
    'private_unlocked': False,
}
DELIVERY_TEMPLATE = '{日期}/{环境}/{分类}/{系统目录}/{SQL类型}'
VALIDATION_TEMPLATE = '{日期}/验证SQL/{系统目录}'


def _system(name, title, folder, sim_user, prod_user):
    return {
        'name': name,
        'sql_title': title,
        'system_folder': folder,
        'script_author': '李浩鹏',
        'sim_addr': '10.128.23.211', 'sim_sid': 'simutfdb', 'sim_user': sim_user,
        'prod_addr': '10.0.129.207', 'prod_sid': 'hxutf', 'prod_user': prod_user,
        'sim_env_name': '模拟环境', 'prod_env_name': '生产环境',
        'delivery_template': DELIVERY_TEMPLATE,
        'validation_template': VALIDATION_TEMPLATE,
    }


DEFAULT_SYSTEMS = [
    _system('车险承保中心', '车险承保中心', '车险承保-张小龙', 'sitautocore', 'autocore'),
    _system('客户信息平台（ECIF）', 'ECIF', '客户信息平台-张小龙', 'sitecif', 'ecif'),
    _system('数据字典', '数据字典', '数据字典-张小龙', 'sitpermission', 'permission'),
    _system('统一监管接入平台', '统一监管接入平台', '统一监管接入平台-张小龙', 'sitrelt', 'relt'),
    _system('共享中心', '共享中心', '共享中心-张小龙', 'sitautocore', 'autocore'),
]

NAME_ALIASES = {
    'Auto Insurance Center': '车险承保中心',
    'ECIF': '客户信息平台（ECIF）',
    'Data Dictionary': '数据字典',
    'Regulatory Platform': '统一监管接入平台',
    'Shared Center': '共享中心',
}


def ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _normalize_system(system):
    item = dict(system)
    item['name'] = NAME_ALIASES.get(item.get('name'), item.get('name', '新系统'))
    default = next((entry for entry in DEFAULT_SYSTEMS if entry['name'] == item['name']), None)
    if default:
        for key, value in default.items():
            item.setdefault(key, value)
    item.setdefault('sql_title', item['name'])
    item.setdefault('system_folder', item['name'] + '-张小龙')
    item.setdefault('script_author', '李浩鹏')
    item.setdefault('sim_env_name', '模拟环境')
    item.setdefault('prod_env_name', '生产环境')
    item.setdefault('delivery_template', DELIVERY_TEMPLATE)
    item.setdefault('validation_template', VALIDATION_TEMPLATE)
    return item


def load_systems():
    ensure_config_dir()
    if os.path.exists(SYSTEMS_FILE):
        try:
            with open(SYSTEMS_FILE, 'r', encoding='utf-8') as stream:
                loaded = json.load(stream)
            if isinstance(loaded, list) and loaded:
                return [_normalize_system(item) for item in loaded]
        except (OSError, ValueError, TypeError):
            pass
    return [dict(item) for item in DEFAULT_SYSTEMS]


def save_systems(systems):
    ensure_config_dir()
    with open(SYSTEMS_FILE, 'w', encoding='utf-8') as stream:
        json.dump([_normalize_system(item) for item in systems], stream, indent=2, ensure_ascii=False)


def normalize_settings(settings):
    result = dict(DEFAULT_SETTINGS)
    if isinstance(settings, dict):
        result.update(settings)
    result['font_size'] = max(10, min(18, int(result['font_size'])))
    result['floating_opacity'] = max(45, min(100, int(result['floating_opacity'])))
    result['copy_feedback_ms'] = max(500, min(5000, int(result['copy_feedback_ms'])))
    result['floating_always_on_top'] = bool(result['floating_always_on_top'])
    result['floating_show_on_startup'] = bool(result['floating_show_on_startup'])
    result['default_language'] = 'en' if result['default_language'] == 'en' else 'zh'
    theme = str(result.get('ui_theme') or 'calm').strip().lower()
    result['ui_theme'] = theme if theme in ('calm', 'clear', 'warm', 'night') else 'calm'
    result['close_ask_each_time'] = bool(result['close_ask_each_time'])
    result['close_default_action'] = (
        'exit' if result['close_default_action'] == 'exit' else 'minimize'
    )
    result['keep_awake_enabled'] = bool(result['keep_awake_enabled'])
    result['keep_awake_interval_minutes'] = max(
        1, min(60, int(result['keep_awake_interval_minutes']))
    )
    result['private_unlocked'] = bool(result.get('private_unlocked', False))
    # 悬浮快捷：去重/过滤非法 index；彩蛋模块在 UI 层按解锁状态再过滤
    from ui.navigation_model import normalize_floating_shortcuts
    result['floating_shortcuts'] = normalize_floating_shortcuts(
        result.get('floating_shortcuts'),
        private_unlocked=True,
    )
    return result


def load_settings():
    ensure_config_dir()
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as stream:
            return normalize_settings(json.load(stream))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    ensure_config_dir()
    normalized = normalize_settings(settings)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as stream:
        json.dump(normalized, stream, ensure_ascii=False, indent=2)
    return normalized


def load_requirement_ui():
    ensure_config_dir()
    # 右侧以文件浏览为主：文件区默认远大于 SQL 预览区
    # content：上摘要紧凑高度 + 下文件库剩余（仅兼容字段，布局按内容）
    result = {'splitter_sizes': [320, 780], 'content_splitter_sizes': [160, 640]}
    try:
        with open(REQUIREMENT_UI_FILE, 'r', encoding='utf-8') as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            for key in result:
                sizes = loaded.get(key)
                if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(size, int) and size > 0 for size in sizes):
                    result[key] = sizes
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_requirement_ui(settings):
    ensure_config_dir()
    result = {}
    for key, default in (('splitter_sizes', [320, 780]), ('content_splitter_sizes', [240, 560])):
        sizes = settings.get(key, default)
        result[key] = sizes if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(size, int) and size > 0 for size in sizes) else default
    with open(REQUIREMENT_UI_FILE, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
