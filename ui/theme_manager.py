# -*- coding: utf-8 -*-
"""静谧美学主题系统：四套内置主题，唯一外观入口。

默认 calm（静谧办公）。切换即时生效、本地持久化，不改布局与数据。
"""

from __future__ import annotations

import os
import sys
from copy import deepcopy

from PyQt6.QtWidgets import QApplication

# theme_id → 中英文名 + 副说明
THEME_META = {
    'calm': ('静谧办公', 'Calm Office', '柔和米灰的日常办公界面', 'Soft office greige'),
    'clear': ('晴空清晰', 'Clear Sky', '清爽蓝灰的高效阅读界面', 'Cool blue-grey clarity'),
    'warm': ('暖书房', 'Warm Study', '温暖纸感的长时间阅读', 'Warm paper-like study'),
    'night': ('夜间安读', 'Night Read', '深石板色低眩光界面，层次更清晰', 'Deep slate low-glare UI with clearer layers'),
}

DEFAULT_THEME_ID = 'calm'

# 各主题共享的扩展 token 默认（浅色语义）
# TERM_*：SSH 控制台「岛」——浅色界面上用深色终端形成强对比，色相贴主色避免违和
_LIGHT_EXTRA = {
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
        'APP_BG': '#F6F5F1',
        'SIDEBAR_BG': '#FBFAF7',
        'SIDEBAR_BORDER': '#E8E6DF',
        'SURFACE': '#FFFFFF',
        'SURFACE_SOFT': '#F9F8F4',
        'SURFACE_TECH': '#EEF3EF',
        'TEXT_STRONG': '#272B29',
        'TEXT': '#424A45',
        'TEXT_MUTED': '#7B847E',
        'TEXT_NAV': '#4A524D',
        'BORDER': '#E2E0D9',
        'BORDER_STRONG': '#D0CEC6',
        'PRIMARY': '#668C78',
        'PRIMARY_HOVER': '#567866',
        'PRIMARY_SOFT': '#E9F1EB',
        'PRIMARY_ACTIVE': '#4F735F',
        'CYAN': '#5A8A8C',
        'SUCCESS': '#3E7A5C',
        'WARNING': '#B8893D',
        'DANGER': '#B85C5C',
        'ICON_MUTED': '#7B847E',
        'NAV_HOVER': '#F0EFE9',
        'NAV_ACTIVE_BG': '#E9F1EB',
        'STATUS_BAR_BG': '#FAF9F6',
        'TABLE_ALT': '#F8F7F3',
        'TABLE_SELECT': '#E9F1EB',
        'INPUT_BG': '#FFFFFF',
        'DISABLED_BG': '#F1F0EB',
        'DISABLED_TEXT': '#A8AFA9',
        'SHADOW': 'rgba(39, 43, 41, 40)',
        'BRAND_ICON_BG': '#E9F1EB',
        'USER_CHIP_BG': '#E9F1EB',
        'USER_CHIP_TEXT': '#4F735F',
        'SCROLL_HANDLE': '#C9C7BF',
    }, {
        'CODE_BG': '#F4F6F4',
        'ELEVATED_SURFACE': '#FFFFFF',
        'MONTH_HEADER_BG': '#EEF3EF',
        'MONTH_HEADER_FG': '#2A3A32',
        # 静谧办公：墨绿控制台，贴 PRIMARY 绿
        'TERM_BG': '#101A16',
        'TERM_FG': '#E7F0EA',
        'TERM_MUTED': '#8AA396',
        'TERM_BORDER': '#2C4A3C',
        'TERM_SEL': '#1A3A2E',
        'TERM_SYS': '#7EC8A3',
        'TERM_CHROME': '#0C1511',
        'TERM_FIND_BG': '#0F1A15',
    }),
    'clear': _with_extra({
        'APP_BG': '#F4F7FB',
        'SIDEBAR_BG': '#FAFCFF',
        'SIDEBAR_BORDER': '#E1E8F0',
        'SURFACE': '#FFFFFF',
        'SURFACE_SOFT': '#F7FAFD',
        'SURFACE_TECH': '#E8F0F7',
        'TEXT_STRONG': '#1C2733',
        'TEXT': '#3A4656',
        'TEXT_MUTED': '#738297',
        'TEXT_NAV': '#445168',
        'BORDER': '#DCE3EE',
        'BORDER_STRONG': '#C8D2E2',
        'PRIMARY': '#547A9D',
        'PRIMARY_HOVER': '#456888',
        'PRIMARY_SOFT': '#E8F0F7',
        'PRIMARY_ACTIVE': '#3E6588',
        'CYAN': '#1A9FC4',
        'SUCCESS': '#19875A',
        'WARNING': '#C77818',
        'DANGER': '#C14653',
        'ICON_MUTED': '#68768F',
        'NAV_HOVER': '#EEF2F8',
        'NAV_ACTIVE_BG': '#E8F0F7',
        'STATUS_BAR_BG': '#FAFBFD',
        'TABLE_ALT': '#F5F8FC',
        'TABLE_SELECT': '#E8F0F7',
        'INPUT_BG': '#FFFFFF',
        'DISABLED_BG': '#F1F3F7',
        'DISABLED_TEXT': '#A8B1C2',
        'SHADOW': 'rgba(28, 39, 51, 40)',
        'BRAND_ICON_BG': '#E8F0F7',
        'USER_CHIP_BG': '#E8F0F7',
        'USER_CHIP_TEXT': '#3E6588',
        'SCROLL_HANDLE': '#C5CEDF',
    }, {
        'CODE_BG': '#F2F6FB',
        'INFO_BG': '#EAF2FA',
        'INFO_BORDER': '#B7C9DE',
        'MONTH_HEADER_BG': '#F0F3FA',
        'MONTH_HEADER_FG': '#1E2A44',
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
        'APP_BG': '#FAF7F2',
        'SIDEBAR_BG': '#FFFDF9',
        'SIDEBAR_BORDER': '#EDE6DB',
        'SURFACE': '#FFFFFF',
        'SURFACE_SOFT': '#FBF7F1',
        'SURFACE_TECH': '#F5EBDD',
        'TEXT_STRONG': '#2C241C',
        'TEXT': '#4A3F34',
        'TEXT_MUTED': '#8A7B6A',
        'TEXT_NAV': '#56493C',
        'BORDER': '#E8DFD2',
        'BORDER_STRONG': '#D4C7B5',
        'PRIMARY': '#A87950',
        'PRIMARY_HOVER': '#926744',
        'PRIMARY_SOFT': '#F5EBDD',
        'PRIMARY_ACTIVE': '#8A5F3C',
        'CYAN': '#8B7355',
        'SUCCESS': '#5A7A4E',
        'WARNING': '#C4893A',
        'DANGER': '#B86A5A',
        'ICON_MUTED': '#8A7B6A',
        'NAV_HOVER': '#F5EFE6',
        'NAV_ACTIVE_BG': '#F5EBDD',
        'STATUS_BAR_BG': '#FCFAF6',
        'TABLE_ALT': '#F9F5EF',
        'TABLE_SELECT': '#F5EBDD',
        'INPUT_BG': '#FFFFFF',
        'DISABLED_BG': '#F3EDE5',
        'DISABLED_TEXT': '#B0A596',
        'SHADOW': 'rgba(44, 36, 28, 40)',
        'BRAND_ICON_BG': '#F5EBDD',
        'USER_CHIP_BG': '#F5EBDD',
        'USER_CHIP_TEXT': '#8A5F3C',
        'SCROLL_HANDLE': '#D0C4B4',
    }, {
        'CODE_BG': '#F8F3EB',
        'MONTH_HEADER_BG': '#F5EBDD',
        'MONTH_HEADER_FG': '#3A2E22',
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
    # 夜间安读 v2：深石板中性底 + 薄荷主色，层次/对比清晰，禁止纯白卡片
    # 原则：APP < SIDEBAR < SURFACE < ELEVATED；CODE/INPUT 内凹更深；选中用软色底+亮字
    'night': {
        'APP_BG': '#0F1216',
        'SIDEBAR_BG': '#151A20',
        'SIDEBAR_BORDER': '#2A323C',
        'SURFACE': '#1B2129',
        'SURFACE_SOFT': '#222932',
        'SURFACE_TECH': '#1E2A28',
        'ELEVATED_SURFACE': '#262E38',
        'CODE_BG': '#0C0F13',
        'TEXT_STRONG': '#F2F5F8',
        'TEXT': '#C5CDD6',
        'TEXT_MUTED': '#8A95A3',
        'TEXT_NAV': '#A7B1BD',
        'BORDER': '#2E3642',
        'BORDER_STRONG': '#404A58',
        'PRIMARY': '#6FC3A0',
        'PRIMARY_HOVER': '#5AAF8C',
        'PRIMARY_SOFT': '#1A2C26',
        'PRIMARY_ACTIVE': '#8AD4B4',
        'CYAN': '#6BB8C4',
        'SUCCESS': '#6FBF8E',
        'WARNING': '#D4A85A',
        'DANGER': '#E08A8A',
        'ICON_MUTED': '#7D8794',
        'NAV_HOVER': '#1C232C',
        'NAV_ACTIVE_BG': '#1A2C26',
        'STATUS_BAR_BG': '#12161B',
        'TABLE_ALT': '#171D24',
        # 暗色选中：柔和底 + 亮字（勿用亮主色底配浅字）
        'TABLE_SELECT': '#243D34',
        'INPUT_BG': '#12171D',
        'DISABLED_BG': '#1A1F26',
        'DISABLED_TEXT': '#5C6672',
        'SHADOW': 'rgba(0, 0, 0, 120)',
        'BRAND_ICON_BG': '#1A2C26',
        'USER_CHIP_BG': '#1A2C26',
        'USER_CHIP_TEXT': '#8AD4B4',
        'SCROLL_HANDLE': '#3A4452',
        'OVERLAY_BG': 'rgba(6, 8, 12, 170)',
        'INFO_BG': '#172830',
        'INFO_BORDER': '#2F5A66',
        'SUCCESS_BG': '#16281F',
        'SUCCESS_BORDER': '#3A6B4E',
        'WARNING_BG': '#2E2616',
        'WARNING_BORDER': '#6B5730',
        'DANGER_BG': '#2E1A1C',
        'DANGER_BORDER': '#6B3A40',
        # 夜间：比 SURFACE 更深的纯墨控制台 + 薄荷描边，避免与卡片糊成一片
        'TERM_BG': '#070A0E',
        'TERM_FG': '#E8EEF4',
        'TERM_MUTED': '#7D8A98',
        'TERM_BORDER': '#1E3A32',
        'TERM_SEL': '#16352C',
        'TERM_SYS': '#6FC3A0',
        'TERM_CHROME': '#05080B',
        'TERM_FIND_BG': '#0A0E13',
        # 搜索高亮：琥珀底 + 亮字，暗底上更醒目
        'SEARCH_MATCH': '#4A3D1C',
        'SEARCH_CURRENT': '#6B5520',
        'LOADING_TRACK': '#2A323C',
        'ON_PRIMARY': '#0A100E',
        'ON_STATUS': '#F2F5F8',
        'MONTH_HEADER_BG': '#222932',
        'MONTH_HEADER_FG': '#F2F5F8',
        'HIGHLIGHT_MARK': '#F0C878',
    },
}


def _app_dir() -> str:
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_theme_id(theme_id) -> str:
    if theme_id in THEMES:
        return theme_id
    return DEFAULT_THEME_ID


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

    @classmethod
    def instance(cls) -> 'ThemeManager':
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def theme_id(self) -> str:
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
        palette = THEMES[theme_id]
        for key, value in palette.items():
            qss = qss.replace(f'__{key}__', value)
        try:
            from ui.icons import icon_url
            arrow = icon_url('dropdown')
            check = icon_url('check')
        except Exception:
            resource_dir = os.path.dirname(self._template_path) if self._template_path else ''
            arrow = os.path.join(resource_dir, 'chevron_down.svg').replace('\\', '/')
            check = os.path.join(resource_dir, 'check_white.svg').replace('\\', '/')
        qss = qss.replace('__DROPDOWN_ARROW__', arrow).replace('__CHECKMARK__', check)
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
                app.setStyleSheet(qss)
            for callback in list(self._listeners):
                try:
                    callback(theme_id)
                except Exception:
                    pass
            return theme_id
        except Exception:
            self._theme_id = prev
            if app is not None and prev:
                try:
                    app.setStyleSheet(self.render(prev, font_size=font_size))
                except Exception:
                    pass
            raise

    def add_listener(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)


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
