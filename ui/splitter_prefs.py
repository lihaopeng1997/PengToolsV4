# -*- coding: utf-8 -*-
"""Splitter：可访问名称、键盘调整、DPI/min-max 夹紧、按 page+tab+bucket 持久化。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QSplitter, QSplitterHandle

try:
    from PyQt6.sip import isdeleted as _sip_isdeleted
except ImportError:
    try:
        import sip
        _sip_isdeleted = sip.isdeleted
    except ImportError:
        _sip_isdeleted = None


def _is_widget_alive(widget: QObject | None) -> bool:
    if widget is None:
        return False
    if _sip_isdeleted is not None:
        try:
            if _sip_isdeleted(widget):
                return False
        except Exception:
            return False
    return True


SPLITTER_HANDLE_WIDTH = 8


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
    # min_sizes 已是 Qt 逻辑像素；不要再乘 DPI，否则高分屏会把邻格压扁。
    if not min_sizes:
        return [80 for _ in range(count)]
    out = []
    for index in range(count):
        raw = min_sizes[index] if index < len(min_sizes) else 80
        out.append(max(48, int(raw)))
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


def has_extreme_splitter_sizes(sizes: list[int], min_sizes: list[int]) -> bool:
    """识别历史配置中已被误拖到近乎不可见的 pane。"""
    if not sizes or len(sizes) != len(min_sizes):
        return True
    try:
        values = [int(value) for value in sizes]
    except (TypeError, ValueError):
        return True
    return any(value <= 0 or value < minimum // 2 for value, minimum in zip(values, min_sizes))


def normalize_splitter_sizes(
    sizes: list[int] | None,
    defaults: list[int],
    min_sizes: list[int],
    current_total: int | None = None,
    old_total: int | None = None,
) -> list[int]:
    """根据视口总宽/高按比例归一化分栏尺寸；遇到极值或不合法数据时回退到默认比例。"""
    count = len(defaults)
    mins = _scaled_mins(min_sizes, count)
    target = None
    if isinstance(sizes, (list, tuple)) and len(sizes) == count:
        try:
            cand = [int(x) for x in sizes]
            if not has_extreme_splitter_sizes(cand, mins):
                target = cand
        except (TypeError, ValueError):
            target = None

    if target is None:
        target = [max(1, int(x)) for x in defaults]

    base_total = old_total if old_total and old_total > 0 else sum(target)
    curr = int(current_total) if current_total and current_total > 40 else None

    if curr is not None and base_total > 0 and abs(curr - base_total) > 10:
        ratio = curr / float(base_total)
        scaled = [int(round(s * ratio)) for s in target]
        return clamp_splitter_sizes(scaled, mins, curr)

    return clamp_splitter_sizes(target, mins, curr)


class _SplitterCoordinator(QObject):
    def __init__(
        self,
        splitter: QSplitter,
        *,
        defaults: list[int],
        min_sizes: list[int],
        on_changed=None,
        step: int = 24,
        double_click_reset: bool = True,
        debounce_ms: int = 250,
        key: str = '',
        persist: bool = True,
    ):
        super().__init__(splitter)
        self.splitter = splitter
        self.defaults = [int(item) for item in defaults]
        self.min_sizes = list(min_sizes)
        self.on_changed = on_changed
        self.step = max(8, int(step))
        self.double_click_reset = bool(double_click_reset)
        self.key = key
        self.persist = persist

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(max(0, int(debounce_ms)))
        self.timer.timeout.connect(self._on_timeout)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

    def update_config(
        self,
        *,
        defaults: list[int],
        min_sizes: list[int],
        on_changed=None,
        step: int = 24,
        double_click_reset: bool = True,
        debounce_ms: int = 250,
        key: str = '',
        persist: bool = True,
    ):
        self.defaults = [int(item) for item in defaults]
        self.min_sizes = list(min_sizes)
        self.on_changed = on_changed
        self.step = max(8, int(step))
        self.double_click_reset = bool(double_click_reset)
        self.key = key
        self.persist = persist
        self.timer.setInterval(max(0, int(debounce_ms)))

    def _on_splitter_moved(self, pos: int = 0, index: int = 0):
        if _is_widget_alive(self.splitter):
            self.timer.start()

    def _on_timeout(self):
        if not _is_widget_alive(self.splitter):
            return
        total = self._total()
        sizes = clamp_splitter_sizes(list(self.splitter.sizes()), self.min_sizes, total if total > 40 else None)
        if sizes != list(self.splitter.sizes()):
            self.splitter.setSizes(sizes)
        self._persist(list(self.splitter.sizes()))

    def _persist(self, sizes: list[int]):
        if callable(self.on_changed):
            self.on_changed(list(sizes))
        if self.persist and self.key:
            try:
                from config import save_layout_splitter
                save_layout_splitter(self.key, list(sizes))
            except Exception:
                pass

    def _total(self) -> int:
        if not _is_widget_alive(self.splitter):
            return 1
        if self.splitter.orientation() == Qt.Orientation.Horizontal:
            return max(1, self.splitter.width())
        return max(1, self.splitter.height())

    def eventFilter(self, watched, event):
        if event is None or not _is_widget_alive(self.splitter):
            return False
        et = event.type()
        if et == QEvent.Type.MouseButtonDblClick and getattr(event, 'button', lambda: None)() == Qt.MouseButton.LeftButton:
            if self.double_click_reset:
                self._restore_defaults()
                return True
            return False
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
        if not _is_widget_alive(self.splitter):
            return
        if self.defaults and self.splitter.count() == len(self.defaults):
            sizes = clamp_splitter_sizes(self.defaults, self.min_sizes, self._total())
            self.splitter.setSizes(sizes)
            self._persist(list(self.splitter.sizes()))

    def _nudge(self, delta: int):
        if not _is_widget_alive(self.splitter):
            return
        sizes = list(self.splitter.sizes())
        if len(sizes) < 2:
            return
        sizes[0] = sizes[0] + int(delta)
        sizes[1] = sizes[1] - int(delta)
        sizes = clamp_splitter_sizes(sizes, self.min_sizes, self._total())
        self.splitter.setSizes(sizes)
        self._persist(list(self.splitter.sizes()))


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
    double_click_reset: bool = True,
) -> None:
    """安装默认比例、键盘调整、双击复位、夹紧与可选持久化。"""
    if splitter is None:
        return
    count = splitter.count()
    splitter.setChildrenCollapsible(False)
    if splitter.handleWidth() < SPLITTER_HANDLE_WIDTH:
        splitter.setHandleWidth(SPLITTER_HANDLE_WIDTH)
    mins = _scaled_mins(min_sizes, count)
    for index in range(count):
        try:
            splitter.setCollapsible(index, False)
        except Exception:
            pass

    horizontal = splitter.orientation() == Qt.Orientation.Horizontal
    cursor = Qt.CursorShape.SplitHCursor if horizontal else Qt.CursorShape.SplitVCursor

    name = accessible_name or (f'{page_id or "panel"} 分隔条' if page_id else '工作区分隔条')
    splitter.setAccessibleName(name)
    splitter.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    resolved_bucket = bucket or layout_bucket()
    key = splitter_storage_key(page_id, tab_id, resolved_bucket) if page_id else ''

    existing_coord = splitter.property('_pengtools_splitter_coordinator')
    same_bucket = (
        existing_coord is not None
        and getattr(existing_coord, 'key', '') == key
        and bool(key)
    )

    current_sizes = list(splitter.sizes()) if count > 0 else []
    has_valid_live_sizes = (
        len(current_sizes) == count
        and not has_extreme_splitter_sizes(current_sizes, mins)
        and sum(current_sizes) > 40
    )

    should_apply_sizes = not (same_bucket and has_valid_live_sizes)

    if should_apply_sizes:
        loaded = saved
        if loaded is None and persist and key:
            try:
                from config import load_layout_splitter
                loaded = load_layout_splitter(key)
            except Exception:
                loaded = None

        total = splitter.width() if horizontal else splitter.height()
        target = normalize_splitter_sizes(
            loaded,
            defaults=defaults,
            min_sizes=mins,
            current_total=total if total > 40 else None,
        )

        def _apply_initial_sizes():
            if not _is_widget_alive(splitter):
                return
            if splitter.count() != len(target):
                return
            t = splitter.width() if splitter.orientation() == Qt.Orientation.Horizontal else splitter.height()
            splitter.setSizes(clamp_splitter_sizes(target, mins, t if t > 40 else None))

        _apply_initial_sizes()
        QTimer.singleShot(0, _apply_initial_sizes)
    else:
        total = splitter.width() if horizontal else splitter.height()
        splitter.setSizes(clamp_splitter_sizes(current_sizes, mins, total if total > 40 else None))

    if existing_coord is not None and isinstance(existing_coord, _SplitterCoordinator):
        coord = existing_coord
        coord.update_config(
            defaults=defaults,
            min_sizes=mins,
            on_changed=on_changed,
            debounce_ms=debounce_ms,
            key=key,
            persist=persist,
            double_click_reset=double_click_reset,
        )
    else:
        coord = _SplitterCoordinator(
            splitter,
            defaults=defaults,
            min_sizes=mins,
            on_changed=on_changed,
            debounce_ms=debounce_ms,
            key=key,
            persist=persist,
            double_click_reset=double_click_reset,
        )
        splitter.installEventFilter(coord)
        splitter.setProperty('_pengtools_splitter_coordinator', coord)

    for index in range(1, count):
        handle = splitter.handle(index)
        if handle is None:
            continue
        handle.setCursor(cursor)
        handle.setAccessibleName(f'{name} · 第{index}格')
        handle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        handle.removeEventFilter(coord)
        handle.installEventFilter(coord)
        if isinstance(handle, QSplitterHandle):
            tip = '拖动调整；双击恢复默认；方向键微调' if double_click_reset else '拖动调整；方向键微调'
            handle.setToolTip(tip)

    splitter.setProperty('_pengtools_splitter_filter', coord)
    splitter.setProperty('_pengtools_splitter_timer', coord.timer)
    splitter.setProperty('_pengtools_splitter_key', key)
    splitter.setProperty('_pengtools_splitter_mins', mins)
