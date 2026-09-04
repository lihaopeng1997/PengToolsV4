# -*- coding: utf-8 -*-
"""统一页面骨架：标题区 + 可选主操作。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
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
from PyQt6.QtGui import QColor, QLinearGradient, QPainter


class _PageHeaderFrame(QFrame):
    """带优雅渐变底部分割线的页面头部容器。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._has_bottom_divider = True

    def has_bottom_divider(self) -> bool:
        return self._has_bottom_divider

    def paintEvent(self, event):
        super().paintEvent(event)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        try:
            from ui.theme_manager import ThemeManager, parse_color
            tm = ThemeManager.instance()
            border_raw = tm.token('BORDER') or '#DFE2EC'
            parsed = parse_color(border_raw)
            if parsed:
                r, g, b, a = parsed
                base_col = QColor(r, g, b, min(255, max(60, a)))
            else:
                base_col = QColor(border_raw)
                if not base_col.isValid():
                    base_col = QColor('#DFE2EC')
        except Exception:
            base_col = QColor('#DFE2EC')

        grad = QLinearGradient(0.0, float(h - 1), float(w), float(h - 1))
        c_fade = QColor(base_col)
        c_fade.setAlpha(0)
        grad.setColorAt(0.0, c_fade)
        grad.setColorAt(0.06, base_col)
        grad.setColorAt(0.94, base_col)
        grad.setColorAt(1.0, c_fade)
        painter.fillRect(0, h - 1, w, 1, grad)


def make_page_header(
    title: str,
    subtitle: str = '',
    icon_role: str | None = None,
    *,
    primary_button=None,
    trailing: QWidget | None = None,
    accent: str | None = None,
    show_home: bool = True,
    language: str = 'zh',
) -> tuple[QFrame, QLabel, QLabel]:
    """创建标准页面标题区，返回 (frame, title_label, subtitle_label)。"""
    frame = _PageHeaderFrame()
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

    if show_home:
        home_btn = QPushButton('返回首页' if language == 'zh' else 'Home')
        apply_button(home_btn, 'ghost', compact=True)
        home_btn.setObjectName('header-home-btn')
        home_btn.setProperty('homeAction', True)
        apply_icon(home_btn, 'home', size=16)
        home_btn.setToolTip('返回首页' if language == 'zh' else 'Return to Home')
        def _on_home_clicked():
            top_win = frame.window()
            if hasattr(top_win, 'navigate_to') and callable(getattr(top_win, 'navigate_to')):
                top_win.navigate_to(0)
        home_btn.clicked.connect(_on_home_clicked)
        frame.home_btn = home_btn
        layout.addWidget(home_btn, 0, Qt.AlignmentFlag.AlignTop)

    return frame, title_label, subtitle_label


def set_header_home_language(header_frame: QWidget | None, language: str = 'zh') -> None:
    """更新页面 Header 返回首页按钮语言。"""
    btn = getattr(header_frame, 'home_btn', None)
    if btn is not None:
        zh = language == 'zh'
        btn.setText('返回首页' if zh else 'Home')
        btn.setToolTip('返回首页' if zh else 'Return to Home')
        apply_icon(btn, 'home', size=16)


def make_page_toolbar(*, divided: bool = False) -> tuple[QFrame, QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName('page-toolbar')
    if divided:
        frame.setProperty('divided', True)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 4, 0, 8)
    layout.setSpacing(8)
    return frame, layout


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


def make_empty_state(
    title: str,
    text: str = '',
    icon_role: str | None = None,
    button: QPushButton | None = None,
) -> QFrame:
    """空状态四要素模板（页面骨架规范 v1 §6.3）。

    title：一句话说明空的原因（禁止"暂无数据"式无信息量文案）；
    text：引导用户第一步做什么（≤2 行）；
    icon_role：可选，取 ui.icons 注册的图标名，按 TEXT_MUTED 着色；
    button：可选，调用方创建并接好 clicked 信号（建议 secondary 角色），
    这里只负责居中排版，不承载业务状态。
    """
    frame = QFrame()
    frame.setObjectName('empty-state')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 26, 24, 26)
    layout.setSpacing(6)
    layout.addStretch(1)

    if icon_role:
        icon_label = QLabel()
        icon_label.setObjectName('empty-state-icon')
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            from ui.theme_manager import ThemeManager
            tint = ThemeManager.instance().token('TEXT_MUTED') or '#6B746E'
        except Exception:
            tint = '#6B746E'
        pix = icon_pixmap(icon_role, 28, tint)
        if not pix.isNull():
            icon_label.setPixmap(pix)
            layout.addWidget(icon_label)
        else:
            icon_label.deleteLater()

    title_label = QLabel(title)
    title_label.setObjectName('empty-state-title')
    title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(title_label)

    if text:
        text_label = QLabel(text)
        text_label.setObjectName('empty-state-text')
        text_label.setWordWrap(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text_label)

    if button is not None:
        layout.addSpacing(4)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)

    layout.addStretch(1)
    return frame
