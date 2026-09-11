# -*- coding: utf-8 -*-
"""静谧美学主题系统：浅色 / 深色双主题，唯一外观入口。

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
    'calm': ('晴空棱镜', 'Sky Prism', '晴空浅紫棱镜工作台', 'Sky prism modern workbench'),
    'black': ('墨黑', 'Ink Black', '低眩光深灰紫分层工作面', 'Deep twilight layered night surface'),
}

THEME_IDS = ('calm',)
THEME_ALIASES = {
    'clear': 'calm',
    'warm': 'calm',
    'night': 'calm',
    'light': 'calm',
    'dark': 'calm',
    'black': 'calm',
}

DEFAULT_THEME_ID = 'calm'

# 各主题共享的扩展 token 默认（浅色语义）
# TERM_*：SSH 控制台「岛」——浅色界面上用深色终端形成强对比，色相贴主色避免违和
_LIGHT_EXTRA = {
    'CONTROL_HEIGHT_COMPACT': '32px',
    'CONTROL_HEIGHT_COMFORTABLE': '36px',
    'ROW_HEIGHT_COMPACT': '32px',
    'ROW_HEIGHT_COMFORTABLE': '40px',
    'FOCUS_RING': '#6C58D9',
    'STATUS_INFO_BG': '#EAF2F5',
    'STATUS_SUCCESS_BG': '#E5F4F1',
    'STATUS_WARNING_BG': '#FAF3E6',
    'STATUS_DANGER_BG': '#FCECEF',
    'ELEVATED_SURFACE': '#FFFFFF',
    'CODE_BG': '#F0ECFA',
    'OVERLAY_BG': 'rgba(38, 36, 56, 120)',
    'INFO_BG': '#EAF2F5',
    'INFO_BORDER': '#B7D0D3',
    'SUCCESS_BG': '#E5F4F1',
    'SUCCESS_BORDER': '#A8D0B6',
    'WARNING_BG': '#FAF3E6',
    'WARNING_BORDER': '#F2D2AE',
    'DANGER_BG': '#FCECEF',
    'DANGER_BORDER': '#F4C9CE',
    'SEARCH_MATCH': '#FFF0A6',
    'SEARCH_CURRENT': '#FFD86B',
    'LOADING_TRACK': '#E6E2F0',
    'ON_PRIMARY': '#FFFFFF',
    'ON_STATUS': '#FFFFFF',
    'MONTH_HEADER_BG': '#ECE8F7',
    'MONTH_HEADER_FG': '#262438',
    'HIGHLIGHT_MARK': '#B24A24',
    'SIDEBAR_TEXT': '#262438',
    'SIDEBAR_TEXT_MUTED': '#615D73',
    'SIDEBAR_HIGHLIGHT': 'rgba(108, 88, 217, 0.05)',
    'NAV_ACTIVE_TEXT': '#FFFFFF',
    'ACCENT_INDIGO': '#6C58D9',
    'ACCENT_CYAN': '#0EA5E9',
    'ACCENT_ROSE': '#C45371',
    'ACCENT_EMERALD': '#247F75',
    # 轻玻璃：仅 Tab/Menu/Dialog/Toast/Loading；SQL/终端/表格仍用实底 SURFACE
    'GLASS_BG': 'rgba(253, 253, 255, 236)',
    'GLASS_BORDER': 'rgba(230, 226, 240, 200)',
    'GLASS_SHADOW': 'rgba(38, 36, 56, 28)',
    # 默认 calm 系终端
    'TERM_BG': '#141420',
    'TERM_FG': '#EAE8F5',
    'TERM_MUTED': '#8C88A6',
    'TERM_BORDER': '#2C2A40',
    'TERM_SEL': '#262345',
    'TERM_SYS': '#A99AF5',
    'TERM_CHROME': '#0E0E17',
    'TERM_FIND_BG': '#12121D',
}


def _with_extra(base: dict, extra: dict | None = None) -> dict:
    result = dict(_LIGHT_EXTRA)
    result.update(base)
    if extra:
        result.update(extra)
    return result


# 完整 token 表（QSS 占位符名 → 色值）
THEMES: dict[str, dict[str, str]] = {
    'calm': _with_extra({
        'APP_BG': '#F4F3FA',
        'SIDEBAR_BG': '#F7F6FC',
        'SIDEBAR_BORDER': '#E6E2F0',
        'SIDEBAR_TEXT': '#262438',
        'SIDEBAR_TEXT_MUTED': '#615D73',
        'SIDEBAR_HIGHLIGHT': 'rgba(108, 88, 217, 0.05)',
        'SURFACE': '#FDFDFF',
        'SURFACE_SOFT': '#F0ECFA',
        'SURFACE_TECH': '#E6EBF5',
        'TEXT_STRONG': '#262438',
        'TEXT': '#3D3952',
        'TEXT_MUTED': '#615D73',
        'TEXT_NAV': '#4B5569',
        'BORDER': '#E6E2F0',
        'BORDER_STRONG': '#D0CBDD',
        'PRIMARY': '#6C58D9',
        'PRIMARY_HOVER': '#5D49C5',
        'PRIMARY_SOFT': '#EEE9FF',
        'PRIMARY_ACTIVE': '#503DBE',
        'CYAN': '#0EA5E9',
        'SUCCESS': '#247F75',
        'WARNING': '#A5772A',
        'DANGER': '#C45371',
        'ICON_MUTED': '#615D73',
        'NAV_HOVER': 'rgba(108, 88, 217, 0.08)',
        'NAV_ACTIVE_BG': '#6C58D9',
        'NAV_ACTIVE_TEXT': '#FFFFFF',
        'STATUS_BAR_BG': '#F0ECFA',
        'TABLE_ALT': '#F8F7FD',
        'TABLE_SELECT': '#EEE9FF',
        'INPUT_BG': '#FFFFFF',
        'DISABLED_BG': '#ECEAF4',
        'DISABLED_TEXT': '#9C98AC',
        'DISABLED_ICON': '#88849A',
        'SHADOW': 'rgba(38, 36, 56, 45)',
        'BRAND_ICON_BG': '#EEE9FF',
        'USER_CHIP_BG': '#EEE9FF',
        'USER_CHIP_TEXT': '#6C58D9',
        'SCROLL_HANDLE': '#C6C2D6',
        'PRIMARY_GRAD_START': '#7C6AE6',
        'PRIMARY_GRAD_END': '#6C58D9',
        'GLASS_HIGHLIGHT': 'rgba(255, 255, 255, 0.65)',
        'ELEVATED_BORDER': '#D0CBDD',
        'SHADOW_L1': 'rgba(38, 36, 56, 20)',
        'SHADOW_L2': 'rgba(38, 36, 56, 40)',
        'SHADOW_L4': 'rgba(38, 36, 56, 80)',
        'AURORA_START': '#6C58D9',
        'AURORA_MID': '#0EA5E9',
        'AURORA_END': '#A99AF5',
        'ACCENT_INDIGO': '#6C58D9',
        'ACCENT_CYAN': '#0EA5E9',
        'ACCENT_ROSE': '#C45371',
        'ACCENT_EMERALD': '#247F75',
    }, {
        'CODE_BG': '#F0ECFA',
        'ELEVATED_SURFACE': '#FFFFFF',
        'MONTH_HEADER_BG': '#ECE8F7',
        'MONTH_HEADER_FG': '#262438',
        'FOCUS_RING': '#6C58D9',
        'TERM_BG': '#141420',
        'TERM_FG': '#EAE8F5',
        'TERM_MUTED': '#8C88A6',
        'TERM_BORDER': '#2C2A40',
        'TERM_SEL': '#262345',
        'TERM_SYS': '#A99AF5',
        'TERM_CHROME': '#0E0E17',
        'TERM_FIND_BG': '#12121D',
    }),
    # 墨黑：近黑分层 + 浅紫高光。
    'black': {
        'APP_BG': '#15151E',
        'SIDEBAR_BG': '#191924',
        'SIDEBAR_BORDER': '#393548',
        'SURFACE': '#1E1E2B',
        'SURFACE_SOFT': '#262437',
        'SURFACE_TECH': '#1A1A24',
        'ELEVATED_SURFACE': '#232333',
        'CODE_BG': '#111118',
        'TEXT_STRONG': '#ECEAF7',
        'TEXT': '#D0CEE2',
        'TEXT_MUTED': '#AEA9C2',
        'TEXT_NAV': '#B0ACC4',
        'BORDER': '#393548',
        'BORDER_STRONG': '#4D4862',
        'PRIMARY': '#A99AF5',
        'PRIMARY_HOVER': '#BCB0FF',
        'PRIMARY_SOFT': '#322B4D',
        'PRIMARY_ACTIVE': '#BCB0FF',
        'CYAN': '#7AA8B0',
        'SUCCESS': '#74CABB',
        'WARNING': '#E2B96D',
        'DANGER': '#F18CA7',
        'ICON_MUTED': '#AEA9C2',
        'NAV_HOVER': '#242233',
        'NAV_ACTIVE_BG': '#322B4D',
        'NAV_ACTIVE_TEXT': '#FFFFFF',
        'STATUS_BAR_BG': '#13131B',
        'TABLE_ALT': '#191924',
        'TABLE_SELECT': '#322B4D',
        'INPUT_BG': '#181822',
        'DISABLED_BG': '#1A1A24',
        'DISABLED_TEXT': '#646076',
        'DISABLED_ICON': '#75718A',
        'SHADOW': 'rgba(0, 0, 0, 140)',
        'BRAND_ICON_BG': '#322B4D',
        'USER_CHIP_BG': '#322B4D',
        'USER_CHIP_TEXT': '#BCB0FF',
        'SCROLL_HANDLE': '#4D4862',
        'OVERLAY_BG': 'rgba(10, 10, 15, 180)',
        'INFO_BG': '#182330',
        'INFO_BORDER': '#2C4A54',
        'SUCCESS_BG': '#162B26',
        'SUCCESS_BORDER': '#2E4E38',
        'WARNING_BG': '#2E2618',
        'WARNING_BORDER': '#5C4A28',
        'DANGER_BG': '#321920',
        'DANGER_BORDER': '#5C3034',
        'TERM_BG': '#0F0F16',
        'TERM_FG': '#E8EAF6',
        'TERM_MUTED': '#8B90B8',
        'TERM_BORDER': '#262437',
        'TERM_SEL': '#322B4D',
        'TERM_SYS': '#A99AF5',
        'TERM_CHROME': '#0A0A0F',
        'TERM_FIND_BG': '#14141E',
        'SEARCH_MATCH': '#4A3D1C',
        'SEARCH_CURRENT': '#6B5520',
        'LOADING_TRACK': '#393548',
        'ON_PRIMARY': '#191527',
        'ON_STATUS': '#ECEAF7',
        'MONTH_HEADER_BG': '#232333',
        'MONTH_HEADER_FG': '#ECEAF7',
        'HIGHLIGHT_MARK': '#E8C878',
        'CONTROL_HEIGHT_COMPACT': '32px',
        'CONTROL_HEIGHT_COMFORTABLE': '36px',
        'ROW_HEIGHT_COMPACT': '32px',
        'ROW_HEIGHT_COMFORTABLE': '40px',
        'FOCUS_RING': '#A99AF5',
        'STATUS_INFO_BG': '#182330',
        'STATUS_SUCCESS_BG': '#162B26',
        'STATUS_WARNING_BG': '#2E2618',
        'STATUS_DANGER_BG': '#321920',
        'GLASS_BG': 'rgba(30, 30, 43, 238)',
        'GLASS_BORDER': 'rgba(77, 72, 98, 200)',
        'GLASS_SHADOW': 'rgba(0, 0, 0, 100)',
        'PRIMARY_GRAD_START': '#A99AF5',
        'PRIMARY_GRAD_END': '#8E7CE8',
        'GLASS_HIGHLIGHT': 'rgba(255, 255, 255, 0.12)',
        'ELEVATED_BORDER': '#4D4862',
        'SHADOW_L1': 'rgba(0, 0, 0, 80)',
        'SHADOW_L2': 'rgba(0, 0, 0, 130)',
        'SHADOW_L4': 'rgba(0, 0, 0, 200)',
        'AURORA_START': '#A99AF5',
        'AURORA_MID': '#5E72E4',
        'AURORA_END': '#C2B7FF',
        'SIDEBAR_TEXT': '#ECEAF7',
        'SIDEBAR_TEXT_MUTED': '#AEA9C2',
        'SIDEBAR_HIGHLIGHT': 'rgba(255, 255, 255, 0.05)',
        'NAV_ACTIVE_TEXT': '#FFFFFF',
        'ACCENT_INDIGO': '#A99AF5',
        'ACCENT_CYAN': '#7AC4CF',
        'ACCENT_ROSE': '#F18CA7',
        'ACCENT_EMERALD': '#74CABB',
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
    if text in THEME_IDS:
        return text
    return DEFAULT_THEME_ID


def theme_mode(theme_id: str | None = None) -> str:
    """返回主题的外观模式。生产统一为 'light'，保留 'black' 兼容映射为 'dark'。"""
    text = str(theme_id or '').strip().lower()
    if text in ('black', 'dark', 'night'):
        return 'dark'
    return 'light'


def theme_display_name(theme_id: str, language: str = 'zh') -> str:
    tid = theme_id if theme_id in THEME_META else resolve_theme_id(theme_id)
    meta = THEME_META.get(tid, THEME_META[DEFAULT_THEME_ID])
    return meta[0] if language == 'zh' else meta[1]


def theme_subtitle(theme_id: str, language: str = 'zh') -> str:
    tid = theme_id if theme_id in THEME_META else resolve_theme_id(theme_id)
    meta = THEME_META.get(tid, THEME_META[DEFAULT_THEME_ID])
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

    def palette(self, theme_id: str | None = None) -> dict[str, str]:
        tid = resolve_theme_id(theme_id or self._theme_id)
        return deepcopy(THEMES[tid])

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
        tid = resolve_theme_id(theme_id or self._theme_id)
        if self._template is None:
            self.load_template()
        qss = self._template or ''
        palette = dict(THEMES[tid])
        palette.setdefault('PRIMARY_GRAD_START', palette.get('PRIMARY', '#5B5FC7'))
        palette.setdefault('PRIMARY_GRAD_END', palette.get('PRIMARY_HOVER', palette.get('PRIMARY', '#4C50B0')))
        for key, value in palette.items():
            qss = qss.replace(f'__{key}__', value)
        try:
            from ui.icons import icon_url, tinted_icon_url
            dropdown_tint = palette.get('TEXT_MUTED') or palette.get('PRIMARY_ACTIVE') or '#8A8A90'
            arrow = tinted_icon_url('dropdown', dropdown_tint) or icon_url('dropdown')
            up_arrow = tinted_icon_url('chevron-up', dropdown_tint) or icon_url('chevron-up')
            check = icon_url('check')
        except Exception:
            resource_dir = os.path.dirname(self._template_path) if self._template_path else ''
            arrow = os.path.join(resource_dir, 'chevron_down.svg').replace('\\', '/')
            up_arrow = os.path.join(resource_dir, 'icons', 'chevron-up.svg').replace('\\', '/')
            check = os.path.join(resource_dir, 'check_white.svg').replace('\\', '/')
        qss = qss.replace('__DROPDOWN_ARROW__', arrow).replace('__CHECKMARK__', check)
        qss = qss.replace('__SPIN_UP_ARROW__', up_arrow)
        try:
            from ui.icons import tinted_icon_url
            branch_tint = palette.get('PRIMARY_ACTIVE') or palette.get('TEXT_STRONG') or '#6C58D9'
            qss = qss.replace('__BRANCH_CLOSED__', tinted_icon_url('chevron-right', branch_tint) or arrow)
            qss = qss.replace('__BRANCH_OPEN__', tinted_icon_url('chevron-down-tree', branch_tint) or arrow)
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
    tid = resolve_theme_id(theme_id)
    p = THEMES[tid]
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
