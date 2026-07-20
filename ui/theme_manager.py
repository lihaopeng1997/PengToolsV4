# -*- coding: utf-8 -*-
"""静谧美学主题系统：四套内置主题，唯一外观入口。

默认 calm（静谧办公）。切换即时生效、本地持久化，不改布局与数据。
"""

from __future__ import annotations

import os
import sys
from copy import deepcopy

from PyQt6.QtWidgets import QApplication

# theme_id → 中英文名
THEME_META = {
    'calm': ('静谧办公', 'Calm Office'),
    'clear': ('晴空清晰', 'Clear Sky'),
    'warm': ('暖书房', 'Warm Study'),
    'night': ('夜间安读', 'Night Read'),
}

DEFAULT_THEME_ID = 'calm'

# 完整 token 表（QSS 占位符名 → 色值）
THEMES: dict[str, dict[str, str]] = {
    'calm': {
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
    },
    'clear': {
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
    },
    'warm': {
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
    },
    'night': {
        'APP_BG': '#202523',
        'SIDEBAR_BG': '#29302C',
        'SIDEBAR_BORDER': '#3A433D',
        'SURFACE': '#2E3732',
        'SURFACE_SOFT': '#343E38',
        'SURFACE_TECH': '#35433C',
        'TEXT_STRONG': '#F0F3F0',
        'TEXT': '#E7ECE7',
        'TEXT_MUTED': '#B8C1B9',
        'TEXT_NAV': '#C5CDC6',
        'BORDER': '#3F4A44',
        'BORDER_STRONG': '#516058',
        'PRIMARY': '#9ABAA6',
        'PRIMARY_HOVER': '#87A994',
        'PRIMARY_SOFT': '#35433C',
        'PRIMARY_ACTIVE': '#B0CDBA',
        'CYAN': '#7FA9A0',
        'SUCCESS': '#7BA88A',
        'WARNING': '#C9A56A',
        'DANGER': '#C78A8A',
        'ICON_MUTED': '#A3ADA5',
        'NAV_HOVER': '#323B36',
        'NAV_ACTIVE_BG': '#35433C',
        'STATUS_BAR_BG': '#252C29',
        'TABLE_ALT': '#333C37',
        'TABLE_SELECT': '#3A4A42',
        'INPUT_BG': '#2A322E',
        'DISABLED_BG': '#2A322E',
        'DISABLED_TEXT': '#6E7871',
        'SHADOW': 'rgba(0, 0, 0, 80)',
        'BRAND_ICON_BG': '#35433C',
        'USER_CHIP_BG': '#35433C',
        'USER_CHIP_TEXT': '#B0CDBA',
        'SCROLL_HANDLE': '#55615A',
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
        # 资源路径
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
            # 刷新图标缓存（依赖 theme id）
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
    """主题卡预览用色块。"""
    p = THEMES[resolve_theme_id(theme_id)]
    return {
        'bg': p['APP_BG'],
        'surface': p['SURFACE'],
        'primary': p['PRIMARY'],
        'sidebar': p['SIDEBAR_BG'],
        'border': p['BORDER'],
    }
