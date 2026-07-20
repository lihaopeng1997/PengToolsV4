# -*- coding: utf-8 -*-
"""统一图标体系：角色 → 本地 SVG / QIcon。

全部来自 resources/icons/ 与根级 SVG，离线打包；禁止 Emoji / CDN / 在线图标库。
QSS 中的 __DROPDOWN_ARROW__ / __CHECKMARK__ 仍由 run.load_stylesheet 注入。
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QLabel


# 角色 → 相对 app 根的路径片段
ICON_FILES = {
    'app': ('resources', 'app.ico'),
    'dropdown': ('resources', 'chevron_down.svg'),
    'check': ('resources', 'check_white.svg'),
    # Pulse 图标包（resources/icons）
    'requirements': ('resources', 'icons', 'requirements.svg'),
    'release': ('resources', 'icons', 'release.svg'),
    'shield-key': ('resources', 'icons', 'shield-key.svg'),
    'settings': ('resources', 'icons', 'settings.svg'),
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
}

# 对话框语义 → 图标角色
NOTICE_ICON_ROLES = {
    'info': 'info',
    'success': 'success',
    'warning': 'warning',
    'error': 'error',
    'danger': 'warning',
    'flow': 'info',
}


def app_dir() -> str:
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resource_path(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)


def icon_file(role: str) -> str:
    """返回角色对应的绝对文件路径；未知角色返回空串。"""
    parts = ICON_FILES.get(role)
    if not parts:
        return ''
    path = resource_path(*parts)
    return path if os.path.exists(path) else ''


def icon_url(role: str) -> str:
    """供 QSS url(...) 使用的正斜杠路径。"""
    path = icon_file(role)
    return path.replace('\\', '/') if path else ''


def qicon(role: str) -> QIcon:
    path = icon_file(role)
    return QIcon(path) if path else QIcon()


def icon_pixmap(role: str, size: int = 20) -> QPixmap:
    """渲染为 pixmap（SVG 优先 QSvgRenderer，失败回退 QIcon）。"""
    path = icon_file(role)
    if not path:
        return QPixmap()
    if path.lower().endswith('.svg'):
        renderer = QSvgRenderer(path)
        if renderer.isValid():
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            renderer.render(painter)
            painter.end()
            return pix
    icon = QIcon(path)
    return icon.pixmap(QSize(size, size))


def apply_icon(button, role: str, size: int = 18) -> None:
    """给按钮设置本地图标，不影响 objectName / 业务。"""
    icon = qicon(role)
    if icon.isNull():
        return
    button.setIcon(icon)
    button.setIconSize(QSize(size, size))


def make_badge_label(kind: str = 'info', size: int = 40, icon_size: int = 22) -> QLabel:
    """对话框 / 页头语义徽章：彩色圆底 + 本地 SVG 图标。"""
    role = NOTICE_ICON_ROLES.get(kind, 'info')
    badge = QLabel()
    badge.setObjectName(f'notice-badge-{kind if kind in NOTICE_ICON_ROLES else "info"}')
    if kind == 'danger':
        badge.setObjectName('notice-badge-error')
    if kind == 'flow':
        badge.setObjectName('notice-badge-info')
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pix = icon_pixmap(role, icon_size)
    if not pix.isNull():
        badge.setPixmap(pix)
    else:
        badge.setText('')
    return badge


def known_roles() -> tuple[str, ...]:
    return tuple(ICON_FILES.keys())
