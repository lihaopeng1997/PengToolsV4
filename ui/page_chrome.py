# -*- coding: utf-8 -*-
"""统一页面骨架：标题区 + 可选主操作。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)


class PageChrome(QWidget):
    """可复用的页面标题、上下文与操作槽位容器，不承载业务状态。"""

    def __init__(self, title: str, context: str = '', parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('page-chrome')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName('page-title')
        text_column.addWidget(self.title_label)
        self.context_label = QLabel(context, self)
        self.context_label.setObjectName('page-context')
        self.context_label.setWordWrap(True)
        self.context_label.setVisible(bool(context))
        text_column.addWidget(self.context_label)
        layout.addLayout(text_column, 1)

        self.secondary_actions = QHBoxLayout()
        self.secondary_actions.setContentsMargins(0, 0, 0, 0)
        self.secondary_actions.setSpacing(8)
        layout.addLayout(self.secondary_actions)
        self.primary_actions = QHBoxLayout()
        self.primary_actions.setContentsMargins(0, 0, 0, 0)
        self.primary_actions.setSpacing(8)
        layout.addLayout(self.primary_actions)

    def add_secondary_action(self, widget: QWidget) -> None:
        self.secondary_actions.addWidget(widget)

    def add_primary_action(self, widget: QWidget) -> None:
        self.primary_actions.addWidget(widget)

from ui.design_system import apply_button
from ui.icons import apply_icon, icon_pixmap
from ui.layout_metrics import PAGE_HEADER_H


def make_page_header(
    title: str,
    subtitle: str = '',
    icon_role: str | None = None,
    *,
    primary_button=None,
    trailing: QWidget | None = None,
    accent: str | None = None,
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
