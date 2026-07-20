# -*- coding: utf-8 -*-
"""统一图标体系：角色 → 资源路径 / QIcon。

本轮只沉淀基础角色与加载方式，不强制给每个业务按钮挂图。
QSS 中的 __DROPDOWN_ARROW__ / __CHECKMARK__ 仍由 run.load_stylesheet 注入。
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtGui import QIcon


# 角色名保持稳定，后续迭代只扩映射、不改调用方语义
ICON_FILES = {
    'app': ('resources', 'app.ico'),
    'dropdown': ('resources', 'chevron_down.svg'),
    'check': ('resources', 'check_white.svg'),
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


def known_roles() -> tuple[str, ...]:
    return tuple(ICON_FILES.keys())
