# -*- coding: utf-8 -*-
"""统一页面骨架：标题区 + 可选主操作。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.design_system import apply_button
from ui.icons import apply_icon, icon_pixmap
from ui.layout_metrics import PAGE_HEADER_H, PRIMARY_SOFT, TEXT_MUTED


def make_page_header(
    title: str,
    subtitle: str = '',
    icon_role: str | None = None,
    *,
    primary_button=None,
    trailing: QWidget | None = None,
    accent: str = PRIMARY_SOFT,
) -> tuple[QFrame, QLabel, QLabel]:
    """创建标准页面标题区，返回 (frame, title_label, subtitle_label)。"""
    frame = QFrame()
    frame.setObjectName('page-header')
    frame.setMinimumHeight(PAGE_HEADER_H - 12)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)

    if icon_role:
        icon_plate = QLabel()
        icon_plate.setObjectName('page-header-icon')
        icon_plate.setFixedSize(36, 36)
        icon_plate.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            from ui.theme_manager import ThemeManager
            tint = ThemeManager.instance().token('PRIMARY_ACTIVE')
        except Exception:
            tint = '#4F735F'
        pix = icon_pixmap(icon_role, 20, tint)
        if not pix.isNull():
            icon_plate.setPixmap(pix)
        layout.addWidget(icon_plate, 0, Qt.AlignmentFlag.AlignTop)

    text_col = QVBoxLayout()
    text_col.setContentsMargins(0, 0, 0, 0)
    text_col.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName('page-title')
    text_col.addWidget(title_label)
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName('page-subtitle')
    subtitle_label.setWordWrap(True)
    if not subtitle:
        subtitle_label.hide()
    text_col.addWidget(subtitle_label)
    layout.addLayout(text_col, 1)

    if trailing is not None:
        layout.addWidget(trailing, 0, Qt.AlignmentFlag.AlignTop)
    if primary_button is not None:
        layout.addWidget(primary_button, 0, Qt.AlignmentFlag.AlignTop)

    return frame, title_label, subtitle_label


def make_filter_bar() -> tuple[QFrame, QHBoxLayout]:
    """标准筛选条容器。"""
    frame = QFrame()
    frame.setObjectName('page-filter-bar')
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(8)
    return frame, layout


def make_zone_card(object_name: str = 'ds-card') -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(object_name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(8)
    return frame, layout
