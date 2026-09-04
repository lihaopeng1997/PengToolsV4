# -*- coding: utf-8 -*-
"""静谧美学主题系统：四套内置主题，唯一外观入口。

默认 calm（静谧办公）。切换即时生效、本地持久化，不改布局与数据。
"""

from __future__ import annotations

import os
import re
import sys
from copy import deepcopy

from PyQt6.QtWidgets import QApplication

# theme_id → 中英文名 + 副说明
THEME_META = {
    'calm': ('静谧蓝', 'Calm Indigo', '靛蓝紫的清爽现代工作台', 'Indigo-violet modern workbench'),
    'clear': ('晴空清晰', 'Clear Sky', '冷钢蓝灰的高效阅读', 'Cool steel-blue clarity'),
    'warm': ('暖书房', 'Warm Study', '纸感棕调的长时间阅读', 'Warm paper study'),
    'black': ('墨黑', 'Ink Black', '近黑分层、低眩光的夜间工作面', 'Near-black layered night surface'),
}

THEME_IDS = ('calm', 'clear', 'warm', 'black')
THEME_ALIASES = {'night': 'black'}

DEFAULT_THEME_ID = 'calm'

# 各主题共享的扩展 token 默认（浅色语义）
# TERM_*：SSH 控制台「岛」——浅色界面上用深色终端形成强对比，色相贴主色避免违和
_LIGHT_EXTRA = {
    'CONTROL_HEIGHT_COMPACT': '32px',
    'CONTROL_HEIGHT_COMFORTABLE': '36px',
    'ROW_HEIGHT_COMPACT': '32px',
    'ROW_HEIGHT_COMFORTABLE': '40px',
    'FOCUS_RING': '#5B5FC7',
    'STATUS_INFO_BG': '#EAF1F5',
    'STATUS_SUCCESS_BG': '#E8F4EC',
    'STATUS_WARNING_BG': '#FFF5E9',
    'STATUS_DANGER_BG': '#FFF0F1',
    'ELEVATED_SURFACE': '#FFFFFF',
    'CODE_BG': '#F7F8F6',
    'OVERLAY_BG': 'rgba(28, 35, 32, 120)',
    'INFO_BG': '#EAF2F3',
    'INFO_BORDER': '#B7D0D3',
    'SUCCESS_BG': '#E8F4EC',
    'SUCCESS_BORDER': '#A8D0B6',
    'WARNING_BG': '#FFF5E9',
    'WARNING_BORDER': '#F2D2AE',
    'DANGER_BG': '#FFF0F1',
    'DANGER_BORDER': '#F4C9CE',
    'SEARCH_MATCH': '#FFF0A6',
    'SEARCH_CURRENT': '#FFD86B',
    'LOADING_TRACK': '#E2E8F0',
    'ON_PRIMARY': '#FFFFFF',
    'ON_STATUS': '#FFFFFF',
    'MONTH_HEADER_BG': '#F0F3FA',
    'MONTH_HEADER_FG': '#1E2A44',
    'HIGHLIGHT_MARK': '#B24A24',
    # 轻玻璃：仅 Tab/Menu/Dialog/Toast/Loading；SQL/终端/表格仍用实底 SURFACE
    'GLASS_BG': 'rgba(255, 254, 251, 236)',
    'GLASS_BORDER': 'rgba(221, 218, 210, 200)',
    'GLASS_SHADOW': 'rgba(26, 31, 28, 28)',
    # 默认 calm 系终端
    'TERM_BG': '#121A22',
    'TERM_FG': '#E8EEF4',
    'TERM_MUTED': '#8B9AAB',
    'TERM_BORDER': '#2A3D48',
    'TERM_SEL': '#1E3D34',
    'TERM_SYS': '#7EC8A3',
    'TERM_CHROME': '#0E151C',
    'TERM_FIND_BG': '#111827',
}


def _with_extra(base: dict, extra: dict | None = None) -> dict:
    result = dict(base)
    result.update(_LIGHT_EXTRA)
    if extra:
        result.update(extra)
    return result


# 完整 token 表（QSS 占位符名 → 色值）
THEMES: dict[str, dict[str, str]] = {
    'calm': _with_extra({
        'APP_BG': '#EEF0F6',
        'SIDEBAR_BG': '#F5F6FB',
        'SIDEBAR_BORDER': '#E0E3EE',
        'SURFACE': '#FFFFFF',
        'SURFACE_SOFT': '#F5F6FA',
        'SURFACE_TECH': '#ECEEF7',
        'TEXT_STRONG': '#1B1E2A',
        'TEXT': '#3A3F4E',
        'TEXT_MUTED': '#6E7486',
        'TEXT_NAV': '#3F4554',
        'BORDER': '#DFE2EC',
        'BORDER_STRONG': '#C7CCDA',
        'PRIMARY': '#5B5FC7',
        'PRIMARY_HOVER': '#4C50B0',
        'PRIMARY_SOFT': '#ECEEFA',
        'PRIMARY_ACTIVE': '#41449C',
        'CYAN': '#4E7A9E',
        'SUCCESS': '#2F7D52',
        'WARNING': '#B0722E',
        'DANGER': '#C04A54',
        'ICON_MUTED': '#6E7486',
        'NAV_HOVER': '#EBEDF5',
        'NAV_ACTIVE_BG': '#ECEEFA',
        'STATUS_BAR_BG': '#F7F8FC',
        'TABLE_ALT': '#F5F6FA',
        'TABLE_SELECT': '#ECEEFA',
        'INPUT_BG': '#FFFFFF',
        'DISABLED_BG': '#ECEDF3',
        'DISABLED_TEXT': '#A6ABB9',
        'DISABLED_ICON': '#858B9A',
        'SHADOW': 'rgba(43, 48, 74, 60)',
        'BRAND_ICON_BG': '#ECEEFA',
        'USER_CHIP_BG': '#ECEEFA',
        'USER_CHIP_TEXT': '#41449C',
        'SCROLL_HANDLE': '#C2C6D4',
        'PRIMARY_GRAD_START': '#5B5FC7',
        'PRIMARY_GRAD_END': '#4C50B0',
        'GLASS_HIGHLIGHT': 'rgba(255, 255, 255, 0.45)',
        'ELEVATED_BORDER': '#C7CCDA',
        'SHADOW_L1': 'rgba(43, 48, 74, 25)',
        'SHADOW_L2': 'rgba(43, 48, 74, 45)',
        'SHADOW_L4': 'rgba(27, 30, 42, 90)',
        'AURORA_START': '#5B5FC7',
        'AURORA_MID': '#0EA5E9',
        'AURORA_END': '#818CF8',
    }, {
        'CODE_BG': '#F0F1F6',
        'ELEVATED_SURFACE': '#FFFFFF',
        'MONTH_HEADER_BG': '#ECEEF7',
        'MONTH_HEADER_FG': '#1B1E2A',
        'FOCUS_RING': '#5B5FC7',
        'TERM_BG': '#14152B',
        'TERM_FG': '#E8EAF6',
        'TERM_MUTED': '#8B90B8',
        'TERM_BORDER': '#2E3162',
        'TERM_SEL': '#1C1E4A',
        'TERM_SYS': '#8F94E0',
        'TERM_CHROME': '#0E0F1F',
        'TERM_FIND_BG': '#101124',
    }),
    'clear': _with_extra({
        'APP_BG': '#F2F4F7',
        'SIDEBAR_BG': '#F7F9FC',
        'SIDEBAR_BORDER': '#DCE3EC',
        'SURFACE': '#FFFFFF',
        'SURFACE_SOFT': '#F5F8FB',
        'SURFACE_TECH': '#E6EEF5',
        'TEXT_STRONG': '#161D26',
        'TEXT': '#38424E',
        'TEXT_MUTED': '#667486',
        'TEXT_NAV': '#3A4654',
        'BORDER': '#D7DEE7',
        'BORDER_STRONG': '#C2CBD7',
        'PRIMARY': '#3A5770',
        'PRIMARY_HOVER': '#304A60',
        'PRIMARY_SOFT': '#E6EEF5',
        'PRIMARY_ACTIVE': '#2C4559',
        'CYAN': '#2A7A96',
        'SUCCESS': '#1B7A52',
        'WARNING': '#B86B16',
        'DANGER': '#B53D4A',
        'ICON_MUTED': '#667486',
        'NAV_HOVER': '#EEF2F7',
        'NAV_ACTIVE_BG': '#E6EEF5',
        'STATUS_BAR_BG': '#F6F8FB',
        'TABLE_ALT': '#F5F8FB',
        'TABLE_SELECT': '#E6EEF5',
        'INPUT_BG': '#FFFFFF',
        'DISABLED_BG': '#EEF1F5',
        'DISABLED_TEXT': '#A4AEBB',
        'DISABLED_ICON': '#7A8696',
        'SHADOW': 'rgba(22, 29, 38, 36)',
        'BRAND_ICON_BG': '#E6EEF5',
        'USER_CHIP_BG': '#E6EEF5',
        'USER_CHIP_TEXT': '#2C4559',
        'SCROLL_HANDLE': '#BDC6D2',
        'PRIMARY_GRAD_START': '#3A5770',
        'PRIMARY_GRAD_END': '#2B4357',
        'GLASS_HIGHLIGHT': 'rgba(255, 255, 255, 0.50)',
        'ELEVATED_BORDER': '#C2CBD7',
        'SHADOW_L1': 'rgba(22, 29, 38, 20)',
        'SHADOW_L2': 'rgba(22, 29, 38, 40)',
        'SHADOW_L4': 'rgba(14, 20, 28, 80)',
        'AURORA_START': '#3A5770',
        'AURORA_MID': '#4E7A9E',
        'AURORA_END': '#688BA8',
    }, {
        'CODE_BG': '#F0F3F7',
        'INFO_BG': '#EAF2FA',
        'INFO_BORDER': '#B7C9DE',
        'MONTH_HEADER_BG': '#E6EEF5',
        'MONTH_HEADER_FG': '#161D26',
        'FOCUS_RING': '#3A5770',
        # 晴空：深蓝灰控制台，贴 PRIMARY 蓝
        'TERM_BG': '#0E1624',
        'TERM_FG': '#E8EEF8',
        'TERM_MUTED': '#8B9BB4',
        'TERM_BORDER': '#2A3F5C',
        'TERM_SEL': '#1A3350',
        'TERM_SYS': '#7EB6E0',
        'TERM_CHROME': '#0B121C',
        'TERM_FIND_BG': '#0C1420',
    }),
    'warm': _with_extra({
        'APP_BG': '#F6F2EA',
        'SIDEBAR_BG': '#FBF8F2',
        'SIDEBAR_BORDER': '#E6DCCE',
        'SURFACE': '#FFFCF7',
        'SURFACE_SOFT': '#F7F1E7',
        'SURFACE_TECH': '#F1E6D8',
        'TEXT_STRONG': '#241C16',
        'TEXT': '#4A3E33',
        'TEXT_MUTED': '#7A6C5C',
        'TEXT_NAV': '#4A3E33',
        'BORDER': '#E6DCCE',
        'BORDER_STRONG': '#D2C4B0',
        'PRIMARY': '#7A5133',
        'PRIMARY_HOVER': '#68442A',
        'PRIMARY_SOFT': '#F1E6D8',
        'PRIMARY_ACTIVE': '#5E3C25',
        'CYAN': '#7A6550',
        'SUCCESS': '#4E6B42',
        'WARNING': '#B67A2E',
        'DANGER': '#A85A4A',
        'ICON_MUTED': '#7A6C5C',
        'NAV_HOVER': '#F3EBE0',
        'NAV_ACTIVE_BG': '#F1E6D8',
        'STATUS_BAR_BG': '#F8F4EC',
        'TABLE_ALT': '#F7F1E7',
        'TABLE_SELECT': '#F1E6D8',
        'INPUT_BG': '#FFFCF7',
        'DISABLED_BG': '#F0E9DE',
        'DISABLED_TEXT': '#AFA395',
        'DISABLED_ICON': '#8C8072',
        'SHADOW': 'rgba(36, 28, 22, 36)',
        'BRAND_ICON_BG': '#F1E6D8',
        'USER_CHIP_BG': '#F1E6D8',
        'USER_CHIP_TEXT': '#5E3C25',
        'SCROLL_HANDLE': '#C9BDAA',
        'PRIMARY_GRAD_START': '#7A5133',
        'PRIMARY_GRAD_END': '#5E3C25',
        'GLASS_HIGHLIGHT': 'rgba(255, 255, 255, 0.55)',
        'ELEVATED_BORDER': '#D2C4B0',
        'SHADOW_L1': 'rgba(36, 28, 22, 20)',
        'SHADOW_L2': 'rgba(36, 28, 22, 40)',
        'SHADOW_L4': 'rgba(24, 18, 14, 80)',
        'AURORA_START': '#7A5133',
        'AURORA_MID': '#A67C52',
        'AURORA_END': '#C49A6C',
    }, {
        'CODE_BG': '#F4EEE4',
        'MONTH_HEADER_BG': '#F1E6D8',
        'MONTH_HEADER_FG': '#241C16',
        'FOCUS_RING': '#7A5133',
        # 暖书房：深褐墨控制台，贴 PRIMARY 棕
        'TERM_BG': '#16110E',
        'TERM_FG': '#F2E8DC',
        'TERM_MUTED': '#A89884',
        'TERM_BORDER': '#4A3828',
        'TERM_SEL': '#3A2A1C',
        'TERM_SYS': '#D4A574',
        'TERM_CHROME': '#100C09',
        'TERM_FIND_BG': '#14100C',
    }),
    # 墨黑：近黑分层 + 鼠尾草主色（与 calm 同源）。禁止白卡片、禁止薄荷绿铺底。
    'black': {
        'APP_BG': '#09090B',
        'SIDEBAR_BG': '#111114',
        'SIDEBAR_BORDER': '#27272A',
        'SURFACE': '#161618',
        'SURFACE_SOFT': '#1C1C1F',
        'SURFACE_TECH': '#1A1F1C',
        'ELEVATED_SURFACE': '#1E1E22',
        'CODE_BG': '#070708',
        'TEXT_STRONG': '#F4F4F5',
        'TEXT': '#C8C8CC',
        'TEXT_MUTED': '#8A8A90',
        'TEXT_NAV': '#B0B0B5',
        'BORDER': '#2A2A2E',
        'BORDER_STRONG': '#3F3F46',
        'PRIMARY': '#8FBB9E',
        'PRIMARY_HOVER': '#7AAB8B',
        'PRIMARY_SOFT': '#152019',
        'PRIMARY_ACTIVE': '#A8CDB4',
        'CYAN': '#7AA8B0',
        'SUCCESS': '#7EBF96',
        'WARNING': '#D0A15C',
        'DANGER': '#D98989',
        'ICON_MUTED': '#8A8A90',
        'NAV_HOVER': '#1A1A1E',
        'NAV_ACTIVE_BG': '#152019',
        'STATUS_BAR_BG': '#0C0C0E',
        'TABLE_ALT': '#121214',
        'TABLE_SELECT': '#1E2A22',
        'INPUT_BG': '#0E0E10',
        'DISABLED_BG': '#161618',
        'DISABLED_TEXT': '#5C5C62',
        'DISABLED_ICON': '#6A6A70',
        'SHADOW': 'rgba(0, 0, 0, 140)',
        'BRAND_ICON_BG': '#152019',
        'USER_CHIP_BG': '#152019',
        'USER_CHIP_TEXT': '#A8CDB4',
        'SCROLL_HANDLE': '#3F3F46',
        'OVERLAY_BG': 'rgba(4, 4, 5, 180)',
        'INFO_BG': '#142028',
        'INFO_BORDER': '#2C4A54',
        'SUCCESS_BG': '#142018',
        'SUCCESS_BORDER': '#2E4E38',
        'WARNING_BG': '#2A2214',
        'WARNING_BORDER': '#5C4A28',
        'DANGER_BG': '#2A1618',
        'DANGER_BORDER': '#5C3034',
        'TERM_BG': '#050506',
        'TERM_FG': '#E8E8EA',
        'TERM_MUTED': '#7A7A80',
        'TERM_BORDER': '#1E2A22',
        'TERM_SEL': '#152019',
        'TERM_SYS': '#8FBB9E',
        'TERM_CHROME': '#040405',
        'TERM_FIND_BG': '#080809',
        'SEARCH_MATCH': '#4A3D1C',
        'SEARCH_CURRENT': '#6B5520',
        'LOADING_TRACK': '#2A2A2E',
        'ON_PRIMARY': '#0A100C',
        'ON_STATUS': '#F4F4F5',
        'MONTH_HEADER_BG': '#1C1C1F',
        'MONTH_HEADER_FG': '#F4F4F5',
        'HIGHLIGHT_MARK': '#E8C878',
        'CONTROL_HEIGHT_COMPACT': '32px',
        'CONTROL_HEIGHT_COMFORTABLE': '36px',
        'ROW_HEIGHT_COMPACT': '32px',
        'ROW_HEIGHT_COMFORTABLE': '40px',
        'FOCUS_RING': '#A8CDB4',
        'STATUS_INFO_BG': '#142028',
        'STATUS_SUCCESS_BG': '#142018',
        'STATUS_WARNING_BG': '#2A2214',
        'STATUS_DANGER_BG': '#2A1618',
        'GLASS_BG': 'rgba(22, 22, 24, 230)',
        'GLASS_BORDER': 'rgba(63, 63, 70, 200)',
        'GLASS_SHADOW': 'rgba(0, 0, 0, 90)',
        'PRIMARY_GRAD_START': '#8FBB9E',
        'PRIMARY_GRAD_END': '#6F9E7E',
        'GLASS_HIGHLIGHT': 'rgba(255, 255, 255, 0.12)',
        'ELEVATED_BORDER': '#3F3F46',
        'SHADOW_L1': 'rgba(0, 0, 0, 80)',
        'SHADOW_L2': 'rgba(0, 0, 0, 130)',
        'SHADOW_L4': 'rgba(0, 0, 0, 200)',
        'AURORA_START': '#8FBB9E',
        'AURORA_MID': '#568266',
        'AURORA_END': '#A8CDB4',
    },
}


def missing_theme_tokens(palette: dict[str, str], required: tuple[str, ...]) -> tuple[str, ...]:
    """返回主题调色板中缺失或空白的必填 token。"""
    return tuple(key for key in required if not str(palette.get(key) or '').strip())


def unresolved_qss_tokens(qss: str) -> tuple[str, ...]:
    """返回 QSS 中尚未渲染的全大写占位符。"""
    return tuple(sorted(set(re.findall(r'__[A-Z0-9_]+__', qss))))


def _app_dir() -> str:
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_theme_id(theme_id) -> str:
    text = str(theme_id or '').strip().lower()
    text = THEME_ALIASES.get(text, text)
    if text in THEMES:
        return text
    return DEFAULT_THEME_ID


def theme_mode(theme_id: str | None) -> str:
    """返回主题的外观模式：'dark' 或 'light'。

    black / night -> 'dark'
    calm / clear / warm / 其它 -> 'light'
    """
    canonical = resolve_theme_id(theme_id)
    if canonical in ('black', 'night'):
        return 'dark'
    return 'light'


def theme_display_name(theme_id: str, language: str = 'zh') -> str:
    meta = THEME_META.get(resolve_theme_id(theme_id), THEME_META[DEFAULT_THEME_ID])
    return meta[0] if language == 'zh' else meta[1]


def theme_subtitle(theme_id: str, language: str = 'zh') -> str:
    meta = THEME_META.get(resolve_theme_id(theme_id), THEME_META[DEFAULT_THEME_ID])
    return meta[2] if language == 'zh' else meta[3]


def parse_color(value: str):
    """返回 (r,g,b,a 0-255) 或 None。支持 #RGB/#RRGGBB 与 rgba()。"""
    from PyQt6.QtGui import QColor
    text = (value or '').strip()
    if not text:
        return None
    if text.startswith('rgba') or text.startswith('rgb'):
        c = QColor()
        # QColor 不完全解析 rgba 字符串时手拆
        inner = text[text.find('(') + 1:text.rfind(')')]
        parts = [p.strip() for p in inner.split(',')]
        if len(parts) >= 3:
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
            a = int(float(parts[3])) if len(parts) > 3 else 255
            if a <= 1 and '.' in (parts[3] if len(parts) > 3 else ''):
                a = int(float(parts[3]) * 255)
            return r, g, b, max(0, min(255, a))
    c = QColor(text)
    if c.isValid():
        return c.red(), c.green(), c.blue(), c.alpha()
    return None


class ThemeManager:
    """应用级单例主题管理器。"""

    _instance = None

    def __init__(self):
        self._theme_id = DEFAULT_THEME_ID
        self._template: str | None = None
        self._template_path = ''
        self._listeners = []
        self._listener_failures: list[dict[str, str]] = []

    @classmethod
    def instance(cls) -> 'ThemeManager':
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def theme_id(self) -> str:
        return self._theme_id

    def get_theme_id(self) -> str:
        return self._theme_id

    def palette(self, theme_id: str | None = None) -> dict[str, str]:
        return deepcopy(THEMES[resolve_theme_id(theme_id or self._theme_id)])

    def token(self, name: str, theme_id: str | None = None) -> str:
        return self.palette(theme_id).get(name, '#000000')

    def qcolor(self, name: str, theme_id: str | None = None):
        from PyQt6.QtGui import QColor
        raw = self.token(name, theme_id)
        parsed = parse_color(raw)
        if parsed:
            r, g, b, a = parsed
            return QColor(r, g, b, a)
        return QColor(raw)

    def load_template(self, app_path: str | None = None) -> str:
        app_path = app_path or _app_dir()
        candidates = [
            os.path.join(app_path, 'resources', 'style.qss'),
            os.path.join(os.path.dirname(sys.executable), 'resources', 'style.qss'),
        ]
        for path in candidates:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as stream:
                    self._template = stream.read()
                self._template_path = path
                return self._template
        self._template = ''
        return ''

    def render(self, theme_id: str | None = None, font_size: int | None = None) -> str:
        theme_id = resolve_theme_id(theme_id or self._theme_id)
        if self._template is None:
            self.load_template()
        qss = self._template or ''
        palette = dict(THEMES[theme_id])
        palette.setdefault('PRIMARY_GRAD_START', palette.get('PRIMARY', '#5B5FC7'))
        palette.setdefault('PRIMARY_GRAD_END', palette.get('PRIMARY_HOVER', palette.get('PRIMARY', '#4C50B0')))
        for key, value in palette.items():
            qss = qss.replace(f'__{key}__', value)
        try:
            from ui.icons import icon_url, tinted_icon_url
            arrow_tint = palette.get('ICON_MUTED') or palette.get('TEXT_MUTED') or '#8A8A90'
            arrow = tinted_icon_url('dropdown', arrow_tint) or icon_url('dropdown')
            check = icon_url('check')
        except Exception:
            resource_dir = os.path.dirname(self._template_path) if self._template_path else ''
            arrow = os.path.join(resource_dir, 'chevron_down.svg').replace('\\', '/')
            check = os.path.join(resource_dir, 'check_white.svg').replace('\\', '/')
        qss = qss.replace('__DROPDOWN_ARROW__', arrow).replace('__CHECKMARK__', check)
        try:
            from ui.icons import tinted_icon_url
            tint = palette.get('PRIMARY_ACTIVE') or palette.get('TEXT_STRONG') or '#3D594A'
            qss = qss.replace('__BRANCH_CLOSED__', tinted_icon_url('chevron-right', tint) or arrow)
            qss = qss.replace('__BRANCH_OPEN__', tinted_icon_url('chevron-down-tree', tint) or arrow)
        except Exception:
            qss = qss.replace('__BRANCH_CLOSED__', arrow).replace('__BRANCH_OPEN__', arrow)
        unresolved = unresolved_qss_tokens(qss)
        if unresolved:
            raise RuntimeError(f'unresolved QSS tokens: {", ".join(unresolved)}')
        if font_size is not None:
            qss = qss + f'\nQWidget {{ font-size: {int(font_size)}px; }}\n'
        return qss

    def apply(self, app: QApplication | None, theme_id: str, font_size: int | None = None) -> str:
        """注入主题到 QApplication；失败回退上一主题。"""
        app = app or QApplication.instance()
        prev = self._theme_id
        theme_id = resolve_theme_id(theme_id)
        try:
            if self._template is None:
                self.load_template()
            qss = self.render(theme_id, font_size=font_size)
            if not qss.strip():
                raise RuntimeError('empty stylesheet')
            self._theme_id = theme_id
            try:
                from ui.icons import clear_icon_cache
                clear_icon_cache()
            except Exception:
                pass
            if app is not None:
                app.setProperty('base_stylesheet', qss)
                app.setProperty('ui_theme', theme_id)
                self._ensure_fusion_style(app)
                palette = build_app_palette(THEMES[theme_id])
                app.setPalette(palette)
                app.setStyleSheet(qss)
                self._sync_widget_chrome(app, palette)
            for callback in list(self._listeners):
                try:
                    callback(theme_id)
                except Exception as exc:
                    self._listener_failures.append({
                        'theme_id': theme_id,
                        'listener': getattr(callback, '__qualname__', repr(callback)),
                        'error_type': type(exc).__name__,
                        'message': str(exc),
                    })
            return theme_id
        except Exception:
            self._theme_id = prev
            if app is not None and prev:
                try:
                    app.setStyleSheet(self.render(prev, font_size=font_size))
                except Exception:
                    pass
            raise

    @staticmethod
    def _ensure_fusion_style(app: QApplication) -> None:
        """Windows 原生样式会画浅色立体边，墨黑必须走 Fusion 才能吃满调色板。"""
        from PyQt6.QtWidgets import QStyleFactory

        current = (app.style().objectName() if app.style() else '').lower()
        if current == 'fusion':
            return
        style = QStyleFactory.create('Fusion')
        if style is not None:
            app.setStyle(style)

    @staticmethod
    def _sync_widget_chrome(app: QApplication, palette) -> None:
        """QFrame/QWidget 默认不画 QSS 底；补 StyledBackground 并重刷已有控件。"""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (
            QAbstractItemView, QComboBox, QHeaderView, QLineEdit, QMenu,
            QPlainTextEdit, QScrollBar, QTextEdit,
        )

        skip_names = {'theme-card-preview', 'ssh-terminal-host', 'ssh-find-bar'}
        skip_types = (
            QAbstractItemView, QHeaderView, QScrollBar, QComboBox,
            QLineEdit, QTextEdit, QPlainTextEdit, QMenu,
        )
        for widget in app.allWidgets():
            if (widget.objectName() or '') in skip_names:
                continue
            if widget.property('ownPalette'):
                continue
            if isinstance(widget, skip_types):
                continue
            parent = widget.parentWidget()
            if parent is not None and isinstance(parent, (QAbstractItemView, QComboBox, QLineEdit)):
                continue
            widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            widget.setPalette(palette)
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)

    def listener_failures(self) -> tuple[dict[str, str], ...]:
        """返回主题监听失败快照；失败监听不会阻断其余界面刷新。"""
        return tuple(dict(item) for item in self._listener_failures)

    def clear_listener_failures(self) -> None:
        self._listener_failures.clear()

    def add_listener(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)


def build_app_palette(tokens: dict[str, str]):
    """由主题 token 构造应用级 QPalette，供 ThemeManager 与测试共用。"""
    from PyQt6.QtGui import QColor, QPalette

    def _color(key: str, fallback: str) -> QColor:
        raw = str(tokens.get(key) or fallback).strip()
        parsed = parse_color(raw)
        if parsed:
            r, g, b, a = parsed
            return QColor(r, g, b, 255 if a < 32 else a)
        color = QColor(raw)
        return color if color.isValid() else QColor(fallback)

    window = _color('APP_BG', '#F3F2EC')
    base = _color('INPUT_BG', tokens.get('SURFACE', '#FFFEFB'))
    alternate = _color('TABLE_ALT', tokens.get('SURFACE_SOFT', '#F6F5F0'))
    button = _color('SURFACE', '#FFFEFB')
    text = _color('TEXT', '#3A423D')
    strong = _color('TEXT_STRONG', '#1A1F1C')
    muted = _color('TEXT_MUTED', '#6B746E')
    highlight = _color('TABLE_SELECT', '#E4EFE8')
    highlighted = _color('TEXT_STRONG', '#1A1F1C')
    border = _color('BORDER', '#DDDAD2')
    border_strong = _color('BORDER_STRONG', '#C9C6BD')
    disabled_bg = _color('DISABLED_BG', '#EEEDE7')
    disabled_text = _color('DISABLED_TEXT', '#A3AAA5')
    tooltip_bg = _color('ELEVATED_SURFACE', tokens.get('SURFACE', '#FFFEFB'))
    link = _color('PRIMARY', '#3F6B56')

    pal = QPalette()
    active_roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: base,
        QPalette.ColorRole.AlternateBase: alternate,
        QPalette.ColorRole.ToolTipBase: tooltip_bg,
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: button,
        QPalette.ColorRole.ButtonText: strong,
        QPalette.ColorRole.BrightText: strong,
        QPalette.ColorRole.Highlight: highlight,
        QPalette.ColorRole.HighlightedText: highlighted,
        QPalette.ColorRole.PlaceholderText: muted,
        QPalette.ColorRole.Light: border,
        QPalette.ColorRole.Midlight: border,
        QPalette.ColorRole.Mid: border_strong,
        QPalette.ColorRole.Dark: border_strong,
        QPalette.ColorRole.Shadow: _color('APP_BG', '#09090B'),
        QPalette.ColorRole.Link: link,
        QPalette.ColorRole.LinkVisited: link,
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, color in active_roles.items():
            pal.setColor(group, role, color)
    disabled = QPalette.ColorGroup.Disabled
    pal.setColor(disabled, QPalette.ColorRole.Window, disabled_bg)
    pal.setColor(disabled, QPalette.ColorRole.Base, disabled_bg)
    pal.setColor(disabled, QPalette.ColorRole.Button, disabled_bg)
    pal.setColor(disabled, QPalette.ColorRole.WindowText, disabled_text)
    pal.setColor(disabled, QPalette.ColorRole.Text, disabled_text)
    pal.setColor(disabled, QPalette.ColorRole.ButtonText, disabled_text)
    pal.setColor(disabled, QPalette.ColorRole.Highlight, border)
    pal.setColor(disabled, QPalette.ColorRole.HighlightedText, disabled_text)
    pal.setColor(disabled, QPalette.ColorRole.Light, border)
    pal.setColor(disabled, QPalette.ColorRole.Midlight, border)
    pal.setColor(disabled, QPalette.ColorRole.PlaceholderText, disabled_text)
    return pal


def preview_swatches(theme_id: str) -> dict[str, str]:
    """主题卡预览用色块（完整微型界面：底/侧栏/卡/输入/按钮/正文/边框）。"""
    p = THEMES[resolve_theme_id(theme_id)]
    return {
        'bg': p['APP_BG'],
        'surface': p['SURFACE'],
        'elevated': p.get('ELEVATED_SURFACE', p['SURFACE']),
        'input': p.get('CODE_BG', p.get('INPUT_BG', p['SURFACE'])),
        'primary': p['PRIMARY'],
        'sidebar': p['SIDEBAR_BG'],
        'border': p['BORDER'],
        'text_muted': p.get('TEXT_MUTED', p['BORDER']),
        'text_strong': p.get('TEXT_STRONG', p.get('TEXT', '#182238')),
        'on_primary': p.get('ON_PRIMARY', '#FFFFFF'),
    }
