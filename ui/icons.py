# -*- coding: utf-8 -*-
"""统一图标体系：本地 SVG + 显式 tint 染色。"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QLabel

from ui.layout_metrics import ICON_MUTED, PRIMARY_ACTIVE

ICON_FILES = {
    # 品牌资源（优先 brand/，旧 app.ico 仅作兼容回退）
    'app': ('resources', 'brand', 'pengtools-app-v2.ico'),
    'app_mark': ('resources', 'brand', 'pengtools-app-mark.svg'),
    'tray_mark': ('resources', 'brand', 'pengtools-tray-mark.svg'),
    'floating_mark': ('resources', 'brand', 'pengtools-floating-mark.svg'),
    'app_legacy_ico': ('resources', 'app.ico'),
    'app_legacy_png': ('resources', 'app-icon.png'),
    'dropdown': ('resources', 'chevron_down.svg'),
    'check': ('resources', 'check_white.svg'),
    'home': ('resources', 'icons', 'home.svg'),
    'requirements': ('resources', 'icons', 'requirements.svg'),
    'release': ('resources', 'icons', 'release.svg'),
    'doc-update': ('resources', 'icons', 'doc-update.svg'),
    'document-id': ('resources', 'icons', 'document-id.svg'),
    'vin': ('resources', 'icons', 'vin.svg'),
    'shield-key': ('resources', 'icons', 'shield-key.svg'),
    'operations': ('resources', 'icons', 'operations.svg'),
    'settings': ('resources', 'icons', 'settings.svg'),
    'daily-report': ('resources', 'icons', 'daily-report.svg'),
    'learning': ('resources', 'icons', 'learning.svg'),
    'search': ('resources', 'icons', 'search.svg'),
    'add': ('resources', 'icons', 'add.svg'),
    'delete': ('resources', 'icons', 'delete.svg'),
    'edit': ('resources', 'icons', 'edit.svg'),
    'expand': ('resources', 'icons', 'expand.svg'),
    'collapse': ('resources', 'icons', 'collapse.svg'),
    'folder-open': ('resources', 'icons', 'folder-open.svg'),
    'copy': ('resources', 'icons', 'copy.svg'),
    'lock': ('resources', 'icons', 'lock.svg'),
    'unlock': ('resources', 'icons', 'unlock.svg'),
    'xml': ('resources', 'icons', 'xml.svg'),
    'json': ('resources', 'icons', 'json.svg'),
    'success': ('resources', 'icons', 'success.svg'),
    'warning': ('resources', 'icons', 'warning.svg'),
    'error': ('resources', 'icons', 'error.svg'),
    'info': ('resources', 'icons', 'info.svg'),
    'import': ('resources', 'icons', 'import.svg'),
    'export': ('resources', 'icons', 'export.svg'),
    'refresh': ('resources', 'icons', 'refresh.svg'),
    'more': ('resources', 'icons', 'more.svg'),
    'calendar': ('resources', 'icons', 'calendar.svg'),
    'filter': ('resources', 'icons', 'filter.svg'),
    'sort': ('resources', 'icons', 'sort.svg'),
    'save': ('resources', 'icons', 'save.svg'),
    'terminal': ('resources', 'icons', 'terminal.svg'),
    'database': ('resources', 'icons', 'database.svg'),
    'external-open': ('resources', 'icons', 'external-open.svg'),
}

# brand_pixmap 角色 → ICON_FILES key
BRAND_ROLES = {
    'app': 'app',
    'app_ico': 'app',
    'app_mark': 'app_mark',
    'tray': 'tray_mark',
    'floating': 'floating_mark',
}

NOTICE_ICON_ROLES = {
    'info': 'info', 'success': 'success', 'warning': 'warning',
    'error': 'error', 'danger': 'warning', 'flow': 'info',
}

# 导航 stack index → 图标角色
NAV_ICON_BY_INDEX = {
    0: 'home',
    1: 'document-id',
    2: 'release',
    3: 'doc-update',
    4: 'vin',
    5: 'shield-key',
    6: 'operations',
    7: 'settings',
    8: 'learning',
    9: 'daily-report',
    10: 'requirements',
}


def app_dir() -> str:
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resource_path(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)


def icon_file(role: str) -> str:
    parts = ICON_FILES.get(role)
    if not parts:
        return ''
    path = resource_path(*parts)
    return path if os.path.exists(path) else ''


def icon_url(role: str) -> str:
    path = icon_file(role)
    return path.replace('\\', '/') if path else ''


def _svg_data_with_tint(path: str, tint: str) -> bytes:
    with open(path, 'r', encoding='utf-8') as stream:
        data = stream.read()
    # 将 currentColor / 默认 stroke 替换为显式色
    data = data.replace('currentColor', tint)
    if 'stroke=' not in data and '<svg' in data:
        data = data.replace('<svg', f'<svg stroke="{tint}"', 1)
    return data.encode('utf-8')


def clear_icon_cache() -> None:
    """主题切换时清空 pixmap 缓存。"""
    icon_pixmap.cache_clear()
    # brand_pixmap 在本模块后部定义；运行时再清
    cache = globals().get('brand_pixmap')
    if cache is not None and hasattr(cache, 'cache_clear'):
        cache.cache_clear()


@lru_cache(maxsize=512)
def icon_pixmap(role: str, size: int = 20, tint: str = ICON_MUTED) -> QPixmap:
    path = icon_file(role)
    if not path:
        return QPixmap()
    if path.lower().endswith('.svg'):
        renderer = QSvgRenderer(QByteArray(_svg_data_with_tint(path, tint)))
        if renderer.isValid():
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
            return pix
    icon = QIcon(path)
    return icon.pixmap(QSize(size, size))


def qicon(role: str, *, size: int = 20, normal: str = ICON_MUTED, active: str = PRIMARY_ACTIVE) -> QIcon:
    """带 Normal / Active / Selected 状态的 QIcon。"""
    icon = QIcon()
    normal_pix = icon_pixmap(role, size, normal)
    active_pix = icon_pixmap(role, size, active)
    if normal_pix.isNull():
        path = icon_file(role)
        return QIcon(path) if path else QIcon()
    icon.addPixmap(normal_pix, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(active_pix, QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(active_pix, QIcon.Mode.Selected, QIcon.State.On)
    icon.addPixmap(active_pix, QIcon.Mode.Normal, QIcon.State.On)
    disabled = icon_pixmap(role, size, '#A8B1C2')
    if not disabled.isNull():
        icon.addPixmap(disabled, QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def apply_icon(button, role: str, size: int = 18, *, normal: str | None = None, active: str | None = None) -> None:
    # 默认 tint 跟随当前主题
    try:
        from ui.theme_manager import ThemeManager
        pal = ThemeManager.instance().palette()
        normal = normal or pal.get('ICON_MUTED', ICON_MUTED)
        active = active or pal.get('PRIMARY_ACTIVE', PRIMARY_ACTIVE)
    except Exception:
        normal = normal or ICON_MUTED
        active = active or PRIMARY_ACTIVE
    icon = qicon(role, size=size, normal=normal, active=active)
    if icon.isNull():
        return
    button.setIcon(icon)
    button.setIconSize(QSize(size, size))


def make_badge_label(kind: str = 'info', size: int = 40, icon_size: int = 22) -> QLabel:
    role = NOTICE_ICON_ROLES.get(kind, 'info')
    try:
        from ui.theme_manager import ThemeManager
        pal = ThemeManager.instance().palette()
        tints = {
            'info': pal.get('PRIMARY_ACTIVE', PRIMARY_ACTIVE),
            'success': pal.get('SUCCESS', '#19875A'),
            'warning': pal.get('WARNING', '#C77818'),
            'error': pal.get('DANGER', '#C14653'),
            'danger': pal.get('DANGER', '#C14653'),
            'flow': pal.get('PRIMARY_ACTIVE', PRIMARY_ACTIVE),
        }
    except Exception:
        tints = {
            'info': PRIMARY_ACTIVE, 'success': '#19875A', 'warning': '#C77818',
            'error': '#C14653', 'danger': '#C14653', 'flow': PRIMARY_ACTIVE,
        }
    badge = QLabel()
    obj = kind if kind in ('info', 'success', 'warning', 'error') else 'info'
    if kind == 'danger':
        obj = 'error'
    badge.setObjectName(f'notice-badge-{obj}')
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pix = icon_pixmap(role, icon_size, tints.get(kind, PRIMARY_ACTIVE))
    if not pix.isNull():
        badge.setPixmap(pix)
    return badge


def known_roles() -> tuple[str, ...]:
    return tuple(ICON_FILES.keys())


def brand_file(role: str) -> str:
    """品牌资源路径；role: app / app_mark / tray / floating。"""
    key = BRAND_ROLES.get(role, role)
    path = icon_file(key)
    if path:
        return path
    # 兼容：ICO 回退到旧 resources/app.ico
    if role in ('app', 'app_ico'):
        return icon_file('app_legacy_ico')
    return ''


@lru_cache(maxsize=128)
def brand_pixmap(role: str, size: int = 28, tint: str = PRIMARY_ACTIVE) -> QPixmap:
    """加载品牌 SVG（按 tint 染色）或 ICO/PNG。"""
    path = brand_file(role)
    if not path:
        # 二次回退：floating/tray 失败时用 app ico
        path = brand_file('app')
    if not path:
        return QPixmap()
    if path.lower().endswith('.svg'):
        try:
            renderer = QSvgRenderer(QByteArray(_svg_data_with_tint(path, tint)))
            if renderer.isValid():
                pix = QPixmap(size, size)
                pix.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pix)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                renderer.render(painter)
                painter.end()
                return pix
        except Exception:
            pass
        return QPixmap()
    icon = QIcon(path)
    return icon.pixmap(QSize(size, size))


def brand_window_icon() -> QIcon:
    path = brand_file('app')
    if path:
        return QIcon(path)
    legacy = icon_file('app_legacy_ico')
    return QIcon(legacy) if legacy else QIcon()
