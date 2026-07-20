# -*- coding: utf-8 -*-
"""窗口布局模式判断与防抖。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ui.layout_metrics import (
    BP_COMPACT, BP_LOW_HEIGHT, BP_STANDARD, BP_WIDE,
    MARGIN_COMPACT, MARGIN_STANDARD, MARGIN_WIDE,
    NAV_ICON, NAV_NARROW, NAV_STANDARD, NAV_WIDE,
)


def classify_layout(width: int, height: int = 900) -> str:
    """返回 wide | standard | compact | narrow。"""
    if width >= BP_WIDE:
        mode = 'wide'
    elif width >= BP_STANDARD:
        mode = 'standard'
    elif width >= BP_COMPACT:
        mode = 'compact'
    else:
        mode = 'narrow'
    return mode


def is_low_height(height: int) -> bool:
    return height < BP_LOW_HEIGHT


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


def is_icon_nav(mode: str) -> bool:
    return mode in ('compact', 'narrow')


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
