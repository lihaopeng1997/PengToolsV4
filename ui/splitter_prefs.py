# -*- coding: utf-8 -*-
"""Splitter：可访问名称、键盘调整、DPI/min-max 夹紧、按 page+tab+bucket 持久化。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QSplitter, QSplitterHandle


def layout_bucket(mode: str | None = None, width: int | None = None) -> str:
    if mode in ('wide', 'standard', 'compact', 'narrow'):
        return mode
    try:
        from ui.responsive import classify_layout
        w = width
        if w is None:
            screen = QApplication.primaryScreen()
            w = int(screen.availableGeometry().width()) if screen is not None else 1440
        return classify_layout(int(w))
    except Exception:
        return 'standard'


def splitter_storage_key(page_id: str, tab_id: str = 'default', bucket: str = 'standard') -> str:
    return f'{page_id}|{tab_id or "default"}|{bucket or "standard"}'


def _dpi_scale() -> float:
    try:
        screen = QApplication.primaryScreen()
        if screen is None:
            return 1.0
        return max(1.0, float(screen.logicalDotsPerInch()) / 96.0)
    except Exception:
        return 1.0


def _scaled_mins(min_sizes: list[int] | None, count: int) -> list[int]:
    scale = _dpi_scale()
    if not min_sizes:
        return [max(80, int(120 * scale)) for _ in range(count)]
    out = []
    for index in range(count):
        raw = min_sizes[index] if index < len(min_sizes) else 120
        out.append(max(48, int(raw * scale)))
    return out


def clamp_splitter_sizes(sizes: list[int], min_sizes: list[int], total: int | None = None) -> list[int]:
    if not sizes:
        return []
    mins = list(min_sizes) if min_sizes and len(min_sizes) == len(sizes) else [80] * len(sizes)
    clamped = [max(mins[i], int(sizes[i])) for i in range(len(sizes))]
    span = int(total) if total and total > 0 else sum(clamped)
    need = sum(mins)
    if span < need:
        return mins[:]
    overflow = sum(clamped) - span
    if overflow <= 0:
        return clamped
    # 从超出 min 最多的格开始回收
    while overflow > 0:
        flexible = [i for i in range(len(clamped)) if clamped[i] > mins[i]]
        if not flexible:
            break
        i = max(flexible, key=lambda idx: clamped[idx] - mins[idx])
        take = min(overflow, clamped[i] - mins[i])
        clamped[i] -= take
        overflow -= take
    return clamped


class _SplitterInteractionFilter(QObject):
    def __init__(self, splitter: QSplitter, defaults: list[int], min_sizes: list[int], on_changed=None, step: int = 24):
        super().__init__(splitter)
        self.splitter = splitter
        self.defaults = [int(item) for item in defaults]
        self.min_sizes = list(min_sizes)
        self.on_changed = on_changed
        self.step = max(8, int(step))

    def eventFilter(self, watched, event):
        if event is None:
            return False
        et = event.type()
        if et == QEvent.Type.MouseButtonDblClick and getattr(event, 'button', lambda: None)() == Qt.MouseButton.LeftButton:
            self._restore_defaults()
            return True
        if et == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            orient = self.splitter.orientation()
            horizontal = orient == Qt.Orientation.Horizontal
            if horizontal and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                delta = -self.step if key == Qt.Key.Key_Left else self.step
                self._nudge(delta)
                return True
            if (not horizontal) and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                delta = -self.step if key == Qt.Key.Key_Up else self.step
                self._nudge(delta)
                return True
            if key == Qt.Key.Key_Home:
                self._restore_defaults()
                return True
        return False

    def _restore_defaults(self):
        if self.defaults and self.splitter.count() == len(self.defaults):
            sizes = clamp_splitter_sizes(self.defaults, self.min_sizes, self._total())
            self.splitter.setSizes(sizes)
            if callable(self.on_changed):
                self.on_changed(list(self.splitter.sizes()))

    def _nudge(self, delta: int):
        sizes = list(self.splitter.sizes())
        if len(sizes) < 2:
            return
        sizes[0] = sizes[0] + int(delta)
        sizes[1] = sizes[1] - int(delta)
        sizes = clamp_splitter_sizes(sizes, self.min_sizes, self._total())
        self.splitter.setSizes(sizes)
        if callable(self.on_changed):
            self.on_changed(list(self.splitter.sizes()))

    def _total(self) -> int:
        if self.splitter.orientation() == Qt.Orientation.Horizontal:
            return max(1, self.splitter.width())
        return max(1, self.splitter.height())


def install_splitter_prefs(
    splitter: QSplitter,
    *,
    defaults: list[int],
    saved: list[int] | None = None,
    on_changed=None,
    debounce_ms: int = 250,
    page_id: str = '',
    tab_id: str = 'default',
    bucket: str | None = None,
    min_sizes: list[int] | None = None,
    accessible_name: str = '',
    persist: bool = True,
) -> None:
    """安装默认比例、键盘调整、双击复位、夹紧与可选持久化。"""
    if splitter is None:
        return
    count = splitter.count()
    splitter.setChildrenCollapsible(False)
    if splitter.handleWidth() < 6:
        splitter.setHandleWidth(6)
    mins = _scaled_mins(min_sizes, count)
    for index in range(count):
        try:
            splitter.setCollapsible(index, False)
        except Exception:
            pass

    name = accessible_name or (f'{page_id or "panel"} 分隔条' if page_id else '工作区分隔条')
    splitter.setAccessibleName(name)
    splitter.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    resolved_bucket = bucket or layout_bucket()
    key = splitter_storage_key(page_id, tab_id, resolved_bucket) if page_id else ''
    loaded = saved
    if loaded is None and persist and key:
        try:
            from config import load_layout_splitter
            loaded = load_layout_splitter(key)
        except Exception:
            loaded = None

    target = None
    if isinstance(loaded, (list, tuple)) and len(loaded) == count:
        try:
            target = [max(1, int(item)) for item in loaded]
        except (TypeError, ValueError):
            target = None
    if target is None and defaults and len(defaults) == count:
        target = [max(1, int(item)) for item in defaults]
    if target:
        def _apply_initial_sizes():
            """在首轮真实布局后重申初始比例，避免子控件 sizeHint 覆盖默认值。"""
            if splitter.count() != len(target):
                return
            total = splitter.width() if splitter.orientation() == Qt.Orientation.Horizontal else splitter.height()
            splitter.setSizes(clamp_splitter_sizes(target, mins, total if total > 40 else None))

        _apply_initial_sizes()
        # QSplitter 会在父控件首次 show 后按子控件 sizeHint 重新分配；
        # 延后到事件循环可确保 defaults / 已保存比例才是最终初始状态。
        QTimer.singleShot(0, _apply_initial_sizes)

    def _persist(sizes: list[int]):
        if callable(on_changed):
            on_changed(list(sizes))
        if persist and key:
            try:
                from config import save_layout_splitter
                save_layout_splitter(key, list(sizes))
            except Exception:
                pass

    timer = QTimer(splitter)
    timer.setSingleShot(True)
    timer.setInterval(max(0, int(debounce_ms)))

    def _emit():
        total = splitter.width() if splitter.orientation() == Qt.Orientation.Horizontal else splitter.height()
        sizes = clamp_splitter_sizes(list(splitter.sizes()), mins, total if total > 40 else None)
        if sizes != list(splitter.sizes()):
            splitter.setSizes(sizes)
        _persist(list(splitter.sizes()))

    timer.timeout.connect(_emit)
    splitter.splitterMoved.connect(lambda *_: timer.start())

    filtr = _SplitterInteractionFilter(
        splitter,
        defaults or list(splitter.sizes()),
        mins,
        on_changed=_persist,
    )
    splitter.installEventFilter(filtr)
    for index in range(1, count):
        handle = splitter.handle(index)
        if handle is None:
            continue
        handle.setAccessibleName(f'{name} · 第{index}格')
        handle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        handle.installEventFilter(filtr)
        if isinstance(handle, QSplitterHandle):
            tip = '拖动调整；双击恢复默认；方向键微调' if True else ''
            handle.setToolTip(tip)

    splitter.setProperty('_pengtools_splitter_filter', filtr)
    splitter.setProperty('_pengtools_splitter_timer', timer)
    splitter.setProperty('_pengtools_splitter_key', key)
    splitter.setProperty('_pengtools_splitter_mins', mins)
