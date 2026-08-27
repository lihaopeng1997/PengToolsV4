# -*- coding: utf-8 -*-
"""Splitter 比例持久化与双击复位（仅 handle / 表头边界，不改业务语义）。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QSplitter


class _SplitterRestoreFilter(QObject):
    def __init__(self, splitter: QSplitter, defaults: list[int], on_changed=None):
        super().__init__(splitter)
        self.splitter = splitter
        self.defaults = [int(item) for item in defaults]
        self.on_changed = on_changed

    def eventFilter(self, watched, event):
        if event is None:
            return False
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            if self.defaults and self.splitter.count() == len(self.defaults):
                self.splitter.setSizes(list(self.defaults))
                if callable(self.on_changed):
                    self.on_changed(list(self.defaults))
                return True
        return False


def install_splitter_prefs(
    splitter: QSplitter,
    *,
    defaults: list[int],
    saved: list[int] | None = None,
    on_changed=None,
    debounce_ms: int = 250,
) -> None:
    """安装默认比例、可选恢复、双击复位，以及防抖变更回调。"""
    if splitter is None:
        return
    splitter.setChildrenCollapsible(False)
    if splitter.handleWidth() < 6:
        splitter.setHandleWidth(6)
    target = None
    if isinstance(saved, (list, tuple)) and len(saved) == splitter.count():
        try:
            target = [max(1, int(item)) for item in saved]
        except (TypeError, ValueError):
            target = None
    if target is None and defaults and len(defaults) == splitter.count():
        target = [max(1, int(item)) for item in defaults]
    if target:
        splitter.setSizes(target)

    timer = QTimer(splitter)
    timer.setSingleShot(True)
    timer.setInterval(max(0, int(debounce_ms)))

    def _emit():
        if callable(on_changed):
            on_changed(list(splitter.sizes()))

    timer.timeout.connect(_emit)
    splitter.splitterMoved.connect(lambda *_: timer.start())

    filtr = _SplitterRestoreFilter(splitter, defaults or list(splitter.sizes()), on_changed=on_changed)
    splitter.installEventFilter(filtr)
    for index in range(1, splitter.count()):
        handle = splitter.handle(index)
        if handle is not None:
            handle.installEventFilter(filtr)
    splitter.setProperty('_pengtools_splitter_filter', filtr)
    splitter.setProperty('_pengtools_splitter_timer', timer)
