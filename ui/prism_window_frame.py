# -*- coding: utf-8 -*-
"""Safe native-frame adapter for the Prism client shell.

``PrismWindowFrame`` is intentionally a controller/helper, not another
``QMainWindow``.  A future host can keep its existing ``MainWindow`` class and
delegate ``nativeEvent()`` to this object after opting into
``Qt.FramelessWindowHint``.  The helper only handles ``WM_NCHITTEST`` and
delegates move/resize to Qt's ``QWindow.startSystemMove/Resize`` APIs.  It does
not intercept ``WM_CLOSE``, ``WM_SYSCOMMAND``, Alt+F4, or application shutdown.

Coordinate handling is explicit: hit-test helpers accept a screen point and a
window rectangle in the same coordinate space.  The Windows native path uses
physical ``GetWindowRect`` coordinates and scales title-bar child geometry by
the QWindow device-pixel ratio, keeping negative monitor coordinates valid.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from enum import IntEnum
from typing import Any

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QObject
from PyQt6.QtWidgets import QApplication, QWidget

from ui.prism_title_bar import start_system_move as _start_system_move
from ui.prism_title_bar import start_system_resize as _start_system_resize


IS_WINDOWS = sys.platform == "win32"
# ``ctypes.wintypes`` does not expose ``LRESULT`` on every supported Python
# build.  ``SSIZE_T`` has the same pointer-sized signed representation and is
# the safe fallback for DwmDefWindowProc's out parameter.
_NATIVE_LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)

WM_NCHITTEST = 0x0084
WM_SYSCOMMAND = 0x0112
WM_CLOSE = 0x0010
WM_NCRBUTTONUP = 0x00A5
MONITOR_DEFAULTTONEAREST = 0x00000002


class HitTestCode(IntEnum):
    """Win32 hit-test values used by the native frame contract."""

    NOWHERE = 0
    CLIENT = 1
    CAPTION = 2
    MINBUTTON = 8
    MAXBUTTON = 9
    LEFT = 10
    RIGHT = 11
    TOP = 12
    TOPLEFT = 13
    TOPRIGHT = 14
    BOTTOM = 15
    BOTTOMLEFT = 16
    BOTTOMRIGHT = 17
    CLOSE = 20


HTNOWHERE = int(HitTestCode.NOWHERE)
HTCLIENT = int(HitTestCode.CLIENT)
HTCAPTION = int(HitTestCode.CAPTION)
HTMINBUTTON = int(HitTestCode.MINBUTTON)
HTMAXBUTTON = int(HitTestCode.MAXBUTTON)
HTLEFT = int(HitTestCode.LEFT)
HTRIGHT = int(HitTestCode.RIGHT)
HTTOP = int(HitTestCode.TOP)
HTTOPLEFT = int(HitTestCode.TOPLEFT)
HTTOPRIGHT = int(HitTestCode.TOPRIGHT)
HTBOTTOM = int(HitTestCode.BOTTOM)
HTBOTTOMLEFT = int(HitTestCode.BOTTOMLEFT)
HTBOTTOMRIGHT = int(HitTestCode.BOTTOMRIGHT)
HTCLOSE = int(HitTestCode.CLOSE)


_EDGE_TO_HIT = {
    Qt.Edge.LeftEdge: HTLEFT,
    Qt.Edge.RightEdge: HTRIGHT,
    Qt.Edge.TopEdge: HTTOP,
    Qt.Edge.BottomEdge: HTBOTTOM,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge: HTTOPLEFT,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge: HTTOPRIGHT,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: HTBOTTOMLEFT,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge: HTBOTTOMRIGHT,
}


def _safe_float(value: Any, fallback: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _scaled_margin(edge_margin: float, device_pixel_ratio: float) -> int:
    return max(1, int(round(max(1.0, float(edge_margin)) * _safe_float(device_pixel_ratio))))


def edge_for_position(
    global_pos: QPoint,
    window_rect: QRect,
    *,
    edge_margin: float = 8.0,
    device_pixel_ratio: float = 1.0,
    maximized: bool = False,
) -> Qt.Edge:
    """Return the Qt edge combination for a screen point.

    ``global_pos`` and ``window_rect`` must use the same coordinate space.  A
    maximized/full-screen window never reports an edge, so its work area is
    left to Windows.  Negative x/y values and fractional DPR margins are
    handled without assuming the primary monitor starts at (0, 0).
    """

    if maximized or not isinstance(global_pos, QPoint) or not isinstance(window_rect, QRect):
        return Qt.Edge(0)
    if not window_rect.isValid() or not window_rect.contains(global_pos):
        return Qt.Edge(0)

    margin = _scaled_margin(edge_margin, device_pixel_ratio)
    left = global_pos.x() <= window_rect.left() + margin - 1
    right = global_pos.x() >= window_rect.right() - margin + 1
    top = global_pos.y() <= window_rect.top() + margin - 1
    bottom = global_pos.y() >= window_rect.bottom() - margin + 1

    edges = Qt.Edge(0)
    if left:
        edges |= Qt.Edge.LeftEdge
    if right:
        edges |= Qt.Edge.RightEdge
    if top:
        edges |= Qt.Edge.TopEdge
    if bottom:
        edges |= Qt.Edge.BottomEdge
    return edges


def hit_code_for_edges(edges: Qt.Edge) -> int:
    """Map Qt edges to a Win32 ``WM_NCHITTEST`` code."""

    return _EDGE_TO_HIT.get(edges, HTCLIENT)


def screen_point_from_lparam(lparam: int) -> QPoint:
    """Decode signed 16-bit screen coordinates from a Win32 LPARAM."""

    value = int(lparam or 0)
    x_word = value & 0xFFFF
    y_word = (value >> 16) & 0xFFFF
    x = x_word - 0x10000 if x_word & 0x8000 else x_word
    y = y_word - 0x10000 if y_word & 0x8000 else y_word
    return QPoint(x, y)


def _window_handle(target: Any):
    if target is None:
        return None
    getter = getattr(target, "windowHandle", None)
    if callable(getter):
        try:
            return getter()
        except RuntimeError:
            return None
    return target if hasattr(target, "devicePixelRatio") else None


def device_pixel_ratio(target: Any) -> float:
    """Return a safe QWindow DPR, defaulting to 1 for tests/no native handle."""

    handle = _window_handle(target)
    if handle is None:
        return 1.0
    try:
        return _safe_float(handle.devicePixelRatio())
    except (AttributeError, RuntimeError, TypeError):
        return 1.0


def _win32_rect(hwnd: Any) -> QRect | None:
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        from ctypes import wintypes

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        rect = _RECT()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_rect = user32.GetWindowRect
        get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        get_rect.restype = wintypes.BOOL
        if not get_rect(hwnd, ctypes.byref(rect)):
            return None
        return QRect(
            int(rect.left),
            int(rect.top),
            max(0, int(rect.right) - int(rect.left)),
            max(0, int(rect.bottom) - int(rect.top)),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def native_window_rect(target: Any) -> QRect | None:
    """Return a physical-pixel Windows window rectangle when available."""

    handle = _window_handle(target)
    if handle is None:
        return None
    try:
        hwnd = int(handle.winId())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return _win32_rect(hwnd)


def work_area_at(global_pos: QPoint) -> QRect | None:
    """Return the nearest monitor work area, preserving negative coordinates."""

    if not isinstance(global_pos, QPoint):
        return None
    if IS_WINDOWS:
        try:
            from ctypes import wintypes

            class _POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            class _RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            class _MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", _RECT),
                    ("rcWork", _RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            point = _POINT(global_pos.x(), global_pos.y())
            monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
            if monitor:
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(_MONITORINFO)
                get_info = user32.GetMonitorInfoW
                get_info.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
                get_info.restype = wintypes.BOOL
                if get_info(monitor, ctypes.byref(info)):
                    rect = info.rcWork
                    return QRect(
                        int(rect.left),
                        int(rect.top),
                        max(0, int(rect.right) - int(rect.left)),
                        max(0, int(rect.bottom) - int(rect.top)),
                    )
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    app = QApplication.instance()
    if app is not None:
        try:
            screen = app.screenAt(global_pos)
            if screen is not None:
                return screen.availableGeometry()
        except (AttributeError, RuntimeError):
            pass
    return None


def _event_type_is_windows(event_type: Any) -> bool:
    if isinstance(event_type, bytes):
        value = event_type.decode("ascii", "ignore")
    else:
        value = str(event_type)
    return "windows" in value.lower()


def _native_message(message: Any):
    """Read a Qt native-message pointer, returning ``None`` safely on bad input."""

    if message is None:
        return None
    if all(hasattr(message, name) for name in ("message", "hwnd", "lParam")):
        return message
    # Raw integers are too easy to mistake for an arbitrary process address.
    # Qt supplies a sip.voidptr here; callers/tests can supply a message-like
    # object with fields above, but untyped integers fail closed.
    if isinstance(message, (int, ctypes.c_void_p)):
        return None
    try:
        address = int(message)
    except (TypeError, ValueError):
        return None
    if address < 0x10000:
        return None
    try:
        from ctypes import wintypes

        return wintypes.MSG.from_address(address)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _dwm_def_window_proc(message: Any) -> tuple[bool, int]:
    """Let DWM handle standard frame affordances before custom hit testing."""

    if not IS_WINDOWS or message is None:
        return False, 0
    try:
        result = _NATIVE_LRESULT()
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        proc = dwmapi.DwmDefWindowProc
        proc.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            ctypes.POINTER(_NATIVE_LRESULT),
        ]
        proc.restype = wintypes.BOOL
        handled = bool(proc(message.hwnd, message.message, message.wParam, message.lParam, ctypes.byref(result)))
        return handled, int(result.value)
    except (AttributeError, OSError, TypeError, ValueError):
        return False, 0


class PrismWindowFrame(QObject):
    """Controller to attach Prism native-frame behavior to an existing host.

    Typical host wiring, performed only by the main-window integrator::

        self._window_frame = PrismWindowFrame(self, self._prism_title_bar)
        self._window_frame.set_frameless(True)  # before the host is shown
        # in MainWindow.nativeEvent:
        # return self._window_frame.native_event(event_type, message)

    ``native_event`` returns the exact ``(handled, result)`` tuple expected by
    ``QWidget.nativeEvent``.  For non-Windows platforms, disabled controllers,
    non-frameless hosts, and messages other than ``WM_NCHITTEST`` it returns
    ``(False, 0)`` so Qt keeps its default processing.
    """

    def __init__(
        self,
        host: QWidget,
        title_bar: QWidget | None = None,
        *,
        edge_margin: float = 8.0,
        minimum_size: QSize = QSize(960, 640),
        parent: QObject | None = None,
    ):
        super().__init__(parent or (host if isinstance(host, QObject) else None))
        self._host = host
        self._title_bar = title_bar
        self._edge_margin = max(1.0, float(edge_margin))
        self._minimum_size = QSize(max(1, minimum_size.width()), max(1, minimum_size.height()))
        self._native_enabled = True
        self._frameless_enabled = False
        self.set_minimum_size(self._minimum_size)

    @property
    def host(self) -> QWidget:
        return self._host

    @property
    def title_bar(self) -> QWidget | None:
        return self._title_bar

    @property
    def edge_margin(self) -> float:
        return self._edge_margin

    def set_title_bar(self, title_bar: QWidget | None) -> None:
        self._title_bar = title_bar

    def set_native_enabled(self, enabled: bool) -> None:
        self._native_enabled = bool(enabled)

    def set_frameless(self, enabled: bool = True) -> bool:
        """Apply/remove the frameless flag while preserving host visibility/state."""

        if not isinstance(self._host, QWidget):
            return False
        flags = self._host.windowFlags()
        wanted = bool(enabled)
        was_visible = self._host.isVisible()
        was_minimized = self._host.isMinimized()
        was_maximized = self._host.isMaximized()
        if wanted:
            flags |= Qt.WindowType.FramelessWindowHint
        else:
            flags &= ~Qt.WindowType.FramelessWindowHint
        if flags != self._host.windowFlags():
            self._host.setWindowFlags(flags)
            # Qt may hide a widget when window flags change.  Restore the
            # previous state so an already-running host is never left hidden.
            if was_visible:
                if was_maximized:
                    self._host.showMaximized()
                elif was_minimized:
                    self._host.showMinimized()
                else:
                    self._host.show()
        self._frameless_enabled = wanted
        return bool(self._host.windowFlags() & Qt.WindowType.FramelessWindowHint) == wanted

    def is_frameless(self) -> bool:
        if not isinstance(self._host, QWidget):
            return self._frameless_enabled
        try:
            return bool(self._host.windowFlags() & Qt.WindowType.FramelessWindowHint)
        except RuntimeError:
            return self._frameless_enabled

    def set_minimum_size(self, size: QSize) -> QSize:
        """Raise the host minimum size without lowering an existing contract."""

        width = max(1, int(size.width()))
        height = max(1, int(size.height()))
        self._minimum_size = QSize(width, height)
        if isinstance(self._host, QWidget):
            current = self._host.minimumSize()
            self._host.setMinimumSize(max(current.width(), width), max(current.height(), height))
        return QSize(self._minimum_size)

    def minimum_size(self) -> QSize:
        return QSize(self._minimum_size)

    def is_maximized(self) -> bool:
        if not isinstance(self._host, QWidget):
            return False
        try:
            state = self._host.windowState()
            return self._host.isMaximized() or bool(state & Qt.WindowState.WindowFullScreen)
        except RuntimeError:
            return False

    def request_system_move(self) -> bool:
        return _start_system_move(self._host)

    def request_system_resize(self, edges: Qt.Edge) -> bool:
        if self.is_maximized() or not edges:
            return False
        return _start_system_resize(self._host, edges)

    def edge_for_position(self, global_pos: QPoint, *, window_rect: QRect | None = None) -> Qt.Edge:
        rect = window_rect or self._window_rect()
        return edge_for_position(
            global_pos,
            rect,
            edge_margin=self._edge_margin,
            device_pixel_ratio=self.device_pixel_ratio(),
            maximized=self.is_maximized(),
        )

    def hit_test(self, global_pos: QPoint, *, window_rect: QRect | None = None) -> int:
        """Return one Win32 hit code for a screen point."""

        rect = window_rect or self._window_rect()
        if not rect.isValid() or not rect.contains(global_pos):
            return HTCLIENT

        dpr = self.device_pixel_ratio()
        control_code = self._control_hit_test(global_pos, dpr)
        if control_code is not None:
            return control_code

        edges = edge_for_position(
            global_pos,
            rect,
            edge_margin=self._edge_margin,
            device_pixel_ratio=dpr,
            maximized=self.is_maximized(),
        )
        if edges:
            return hit_code_for_edges(edges)

        title_rect = self._title_bar_rect(dpr)
        if title_rect is not None and title_rect.contains(global_pos):
            child = self._title_bar_child_at(global_pos, dpr)
            if child is None:
                return HTCAPTION
            action = str(child.property("windowAction") or "")
            if action == "minimize":
                return HTMINBUTTON
            if action == "maximize":
                return HTMAXBUTTON
            if action == "close":
                return HTCLOSE
            # Pin, menus, and any injected host controls remain client-handled.
            return HTCLIENT

        if self._title_bar is None:
            top_height = max(1, int(round(44.0 * dpr)))
            if global_pos.y() < rect.top() + top_height:
                return HTCAPTION
        return HTCLIENT

    def native_event(self, event_type: Any, message: Any) -> tuple[bool, int]:
        """Handle only Windows ``WM_NCHITTEST`` after giving DWM first refusal."""

        if not IS_WINDOWS or not self._native_enabled or not self.is_frameless():
            return False, 0
        if not _event_type_is_windows(event_type):
            return False, 0
        native = _native_message(message)
        if native is None or int(getattr(native, "message", 0)) != WM_NCHITTEST:
            return False, 0

        handled, result = _dwm_def_window_proc(native)
        if handled:
            return True, result

        position = screen_point_from_lparam(int(getattr(native, "lParam", 0)))
        rect = _win32_rect(getattr(native, "hwnd", None)) or self._window_rect()
        return True, int(self.hit_test(position, window_rect=rect))

    def _window_rect(self) -> QRect:
        native = native_window_rect(self._host)
        if native is not None:
            return native
        if isinstance(self._host, QWidget):
            try:
                return self._host.frameGeometry()
            except RuntimeError:
                pass
        return QRect()

    def device_pixel_ratio(self) -> float:
        return device_pixel_ratio(self._host)

    def _title_bar_rect(self, dpr: float) -> QRect | None:
        if not isinstance(self._title_bar, QWidget):
            return None
        try:
            origin = self._title_bar.mapToGlobal(QPoint(0, 0))
            scale = _safe_float(dpr)
            return QRect(
                int(round(origin.x() * scale)),
                int(round(origin.y() * scale)),
                max(1, int(round(self._title_bar.width() * scale))),
                max(1, int(round(self._title_bar.height() * scale))),
            )
        except (RuntimeError, TypeError):
            return None

    def _title_bar_child_at(self, native_pos: QPoint, dpr: float) -> QWidget | None:
        if not isinstance(self._title_bar, QWidget):
            return None
        scale = _safe_float(dpr)
        logical = QPoint(int(round(native_pos.x() / scale)), int(round(native_pos.y() / scale)))
        try:
            child = self._title_bar.childAt(self._title_bar.mapFromGlobal(logical))
        except RuntimeError:
            return None
        while isinstance(child, QWidget) and child is not self._title_bar:
            if child.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
                child = child.parentWidget()
                continue
            return child
        return None

    def _control_hit_test(self, native_pos: QPoint, dpr: float) -> int | None:
        child = self._title_bar_child_at(native_pos, dpr)
        if child is None:
            return None
        action = str(child.property("windowAction") or "")
        if action == "minimize":
            return HTMINBUTTON
        if action == "maximize":
            return HTMAXBUTTON
        if action == "close":
            return HTCLOSE
        # A pin/menu button remains a regular client control.  Returning None
        # would incorrectly turn its area into a draggable caption.
        return HTCLIENT


__all__ = [
    "HTCAPTION",
    "HTCLIENT",
    "HTCLOSE",
    "HTLEFT",
    "HTMAXBUTTON",
    "HTMINBUTTON",
    "HTRIGHT",
    "HTTOP",
    "HTTOPLEFT",
    "HTTOPRIGHT",
    "HTBOTTOM",
    "HTBOTTOMLEFT",
    "HTBOTTOMRIGHT",
    "HitTestCode",
    "IS_WINDOWS",
    "PrismWindowFrame",
    "WM_CLOSE",
    "WM_NCHITTEST",
    "WM_NCRBUTTONUP",
    "WM_SYSCOMMAND",
    "device_pixel_ratio",
    "edge_for_position",
    "hit_code_for_edges",
    "native_window_rect",
    "screen_point_from_lparam",
    "work_area_at",
]
