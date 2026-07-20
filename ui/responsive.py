# -*- coding: utf-8 -*-
"""窗口布局模式判断、防抖与统一操作条 ResponsiveActionBar。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QSizePolicy, QToolButton, QWidget

from ui.layout_metrics import (
    BP_COMPACT, BP_LOW_HEIGHT, BP_STANDARD, BP_WIDE,
    MARGIN_COMPACT, MARGIN_STANDARD, MARGIN_WIDE,
    NAV_ICON, NAV_NARROW, NAV_STANDARD, NAV_WIDE,
    SPACING_CARD, SPACING_PAGE,
)


class ActionDensity(str, Enum):
    FULL = 'full'          # Wide / Standard：全部常规按钮
    COMPACT = 'compact'    # Compact：主操作 + 刷新/搜索 + 更多
    OVERFLOW = 'overflow'  # Narrow：主操作 + 更多


def classify_layout(width: int, height: int = 900) -> str:
    """返回 wide | standard | compact | narrow。"""
    if width >= BP_WIDE:
        return 'wide'
    if width >= BP_STANDARD:
        return 'standard'
    if width >= BP_COMPACT:
        return 'compact'
    return 'narrow'


def is_low_height(height: int) -> bool:
    return height < BP_LOW_HEIGHT


def density_for_mode(mode: str) -> ActionDensity:
    if mode in ('wide', 'standard'):
        return ActionDensity.FULL
    if mode == 'compact':
        return ActionDensity.COMPACT
    return ActionDensity.OVERFLOW


def nav_width_for_mode(mode: str) -> int:
    return {
        'wide': NAV_WIDE,
        'standard': NAV_STANDARD,
        'compact': NAV_ICON,
        'narrow': NAV_NARROW,
    }.get(mode, NAV_STANDARD)


def content_margin_for_mode(mode: str) -> int:
    return {
        'wide': MARGIN_WIDE,
        'standard': MARGIN_STANDARD,
        'compact': MARGIN_COMPACT,
        'narrow': MARGIN_COMPACT,
    }.get(mode, MARGIN_STANDARD)


def page_spacing_for_mode(mode: str, low_height: bool = False) -> int:
    if low_height:
        return 10
    return {
        'wide': SPACING_PAGE,
        'standard': SPACING_PAGE,
        'compact': 12,
        'narrow': 10,
    }.get(mode, SPACING_PAGE)


def card_spacing_for_mode(mode: str, low_height: bool = False) -> int:
    if low_height:
        return 10
    return {
        'wide': SPACING_CARD,
        'standard': SPACING_CARD,
        'compact': 10,
        'narrow': 8,
    }.get(mode, SPACING_CARD)


def is_icon_nav(mode: str) -> bool:
    return mode in ('compact', 'narrow')


def editor_orientation_for_mode(mode: str) -> Qt.Orientation:
    """双栏编辑器：Compact 以下改为上下。"""
    if mode in ('compact', 'narrow'):
        return Qt.Orientation.Vertical
    return Qt.Orientation.Horizontal


def editor_min_height() -> int:
    return 180


class LayoutModeController(QObject):
    """主窗口 resize 防抖后广播 layout_mode_changed(mode, low_height)。"""

    layout_mode_changed = pyqtSignal(str, bool)

    def __init__(self, parent=None, debounce_ms: int = 120):
        super().__init__(parent)
        self._mode = ''
        self._low = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._flush)
        self._pending_w = 0
        self._pending_h = 0

    @property
    def mode(self) -> str:
        return self._mode or 'standard'

    @property
    def low_height(self) -> bool:
        return self._low

    def observe(self, width: int, height: int) -> None:
        self._pending_w = width
        self._pending_h = height
        self._timer.start()

    def force(self, width: int, height: int) -> None:
        self._pending_w = width
        self._pending_h = height
        self._flush()

    def _flush(self) -> None:
        mode = classify_layout(self._pending_w, self._pending_h)
        low = is_low_height(self._pending_h)
        if mode == self._mode and low == self._low:
            return
        self._mode = mode
        self._low = low
        self.layout_mode_changed.emit(mode, low)


class ActionRole(str, Enum):
    PRIMARY = 'primary'       # 始终可见（导出/生成/保存/提交/监听）
    SECONDARY = 'secondary'   # Compact 可收纳
    UTILITY = 'utility'       # 刷新/搜索：Compact 仍显示，Narrow 可进更多
    DANGER = 'danger'         # 独立靠右，尽量可见
    OVERFLOW = 'overflow'     # 始终进更多


@dataclass
class _BarItem:
    action: QAction
    role: ActionRole
    button: QToolButton
    always_visible: bool = False


class ResponsiveActionBar(QWidget):
    """可见按钮 +「更多」菜单；与 QAction 同一入口，避免双逻辑。

    使用：
        bar = ResponsiveActionBar()
        act = QAction('导出', self)
        act.triggered.connect(...)
        bar.add_action(act, role=ActionRole.PRIMARY, icon_role='export')
        bar.apply_density(density_for_mode(mode))
    """

    def __init__(self, parent=None, language: str = 'zh'):
        super().__init__(parent)
        self.language = language
        self._items: list[_BarItem] = []
        self._density = ActionDensity.FULL
        self.setObjectName('responsive-action-bar')
        self._root = QHBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(8)
        self._left = QHBoxLayout()
        self._left.setSpacing(8)
        self._root.addLayout(self._left, 1)
        self._right = QHBoxLayout()
        self._right.setSpacing(8)
        self._root.addLayout(self._right, 0)

        self.more_button = QToolButton()
        self.more_button.setObjectName('responsive-more-btn')
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more_button.setProperty('compactAction', True)
        self._more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self._more_menu)
        self._set_more_text()
        try:
            from ui.icons import apply_icon
            apply_icon(self.more_button, 'more', size=16)
        except Exception:
            pass
        self._right.addWidget(self.more_button)
        self.more_button.hide()

    def _set_more_text(self):
        zh = self.language == 'zh'
        self.more_button.setText('更多' if zh else 'More')
        self.more_button.setToolTip('更多操作' if zh else 'More actions')

    def set_language(self, language: str):
        self.language = language
        self._set_more_text()
        self.apply_density(self._density)

    def clear_actions(self):
        for item in self._items:
            item.button.setParent(None)
            item.button.deleteLater()
        self._items.clear()
        self._more_menu.clear()

    def add_action(
        self,
        action: QAction,
        *,
        role: ActionRole | str = ActionRole.SECONDARY,
        icon_role: str | None = None,
        always_visible: bool = False,
        danger: bool = False,
    ) -> QAction:
        if isinstance(role, str):
            role = ActionRole(role)
        if danger:
            role = ActionRole.DANGER
        btn = QToolButton(self)
        btn.setDefaultAction(action)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setProperty('compactAction', True)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if role == ActionRole.PRIMARY:
            btn.setObjectName('primary-btn')
        elif role == ActionRole.DANGER:
            btn.setObjectName('btn-danger')
        else:
            btn.setObjectName('btn-secondary')
        if icon_role:
            try:
                from ui.icons import apply_icon
                apply_icon(btn, icon_role, size=16)
            except Exception:
                pass
        # tooltip 兜底
        if not action.toolTip():
            action.setToolTip(action.text())
        item = _BarItem(action=action, role=role, button=btn, always_visible=always_visible)
        self._items.append(item)
        if role == ActionRole.DANGER:
            self._right.insertWidget(max(0, self._right.count() - 1), btn)
        else:
            self._left.addWidget(btn)
        self.apply_density(self._density)
        return action

    def add_widget_action(
        self,
        text: str,
        slot: Callable,
        *,
        role: ActionRole | str = ActionRole.SECONDARY,
        icon_role: str | None = None,
        always_visible: bool = False,
        tooltip: str = '',
        danger: bool = False,
    ) -> QAction:
        act = QAction(text, self)
        if tooltip:
            act.setToolTip(tooltip)
        act.triggered.connect(slot)
        return self.add_action(
            act, role=role, icon_role=icon_role,
            always_visible=always_visible, danger=danger,
        )

    def apply_density(self, density: ActionDensity | str):
        if isinstance(density, str):
            density = ActionDensity(density)
        self._density = density
        self._more_menu.clear()
        visible_count = 0
        overflow_count = 0

        for item in self._items:
            show_inline = self._should_show_inline(item, density)
            item.button.setVisible(show_inline)
            if show_inline:
                visible_count += 1
                # icon-only on narrow for non-primary
                if density == ActionDensity.OVERFLOW and item.role != ActionRole.PRIMARY:
                    item.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    item.button.setProperty('responsiveIconOnly', True)
                else:
                    item.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                    item.button.setProperty('responsiveIconOnly', False)
            else:
                # 进更多菜单 — 同一 QAction
                if item.action.isEnabled() or True:
                    self._more_menu.addAction(item.action)
                    overflow_count += 1

        self.more_button.setVisible(overflow_count > 0)
        # 样式 polish
        for item in self._items:
            item.button.style().unpolish(item.button)
            item.button.style().polish(item.button)

    def _should_show_inline(self, item: _BarItem, density: ActionDensity) -> bool:
        if item.always_visible:
            return True
        if item.role == ActionRole.OVERFLOW:
            return False
        if density == ActionDensity.FULL:
            return True
        if density == ActionDensity.COMPACT:
            return item.role in (ActionRole.PRIMARY, ActionRole.UTILITY, ActionRole.DANGER)
        # OVERFLOW / Narrow：仅主操作 + danger
        return item.role in (ActionRole.PRIMARY, ActionRole.DANGER)

    def apply_layout_mode(self, mode: str, low_height: bool = False):
        self.apply_density(density_for_mode(mode))


def set_subtitle_visible(subtitle_widget, low_height: bool):
    if subtitle_widget is not None:
        subtitle_widget.setVisible(not low_height)


def apply_splitter_orientation(splitter, mode: str, *, min_editor: int = 180):
    """双栏编辑器在 compact/narrow 改为垂直，并保证最小高度。"""
    if splitter is None:
        return
    orient = editor_orientation_for_mode(mode)
    if splitter.orientation() != orient:
        splitter.setOrientation(orient)
    for i in range(splitter.count()):
        w = splitter.widget(i)
        if w is not None:
            w.setMinimumHeight(min_editor if orient == Qt.Orientation.Vertical else 0)
