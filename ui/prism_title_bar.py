# -*- coding: utf-8 -*-
"""Prism client-area title bar.

The title bar deliberately owns presentation and Qt interaction only.  It does
not close the application, persist the pin state, or know about business
panels.  A host window connects the request signals to its existing lifecycle
methods (including ``closeEvent``).

The host may inject real ``QMenu`` instances with :meth:`set_menus`.  Keeping
menu construction outside this widget prevents preview-only actions from
becoming product behaviour and lets the main window reuse its existing QActions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStyle,
    QToolButton,
    QWidget,
)

from ui.icons import brand_pixmap


# These are client-area metrics, intentionally local until the shell is wired
# into main_window.py.  The integration owner can map them to layout_metrics
# after comparing the complete shell at each supported breakpoint.
TITLEBAR_H = 44
WINDOW_BUTTON_SIZE = 32
BRAND_ICON_SIZE = 22


def _window_handle(target: Any):
    """Return a target's QWindow without assuming a concrete host class."""

    if target is None:
        return None
    handle = getattr(target, "windowHandle", None)
    if callable(handle):
        try:
            return handle()
        except RuntimeError:
            return None
    return target if hasattr(target, "startSystemMove") else None


def start_system_move(target: Any) -> bool:
    """Ask Qt/Windows to move *target* using the native window manager.

    ``False`` is an honest result for offscreen tests or a not-yet-created
    native handle.  The caller may then decide whether a platform-specific
    fallback is appropriate; this component never performs coordinate math.
    """

    handle = _window_handle(target)
    if handle is None:
        return False
    try:
        return bool(handle.startSystemMove())
    except (AttributeError, RuntimeError, TypeError):
        return False


def start_system_resize(target: Any, edges: Qt.Edge) -> bool:
    """Ask Qt/Windows to resize *target* at ``edges``.

    Edge hit testing belongs to the top-level host/native event path.  This
    helper only delegates the actual resize to QWindow so Windows Snap, DPI,
    minimum sizes, and multi-monitor geometry remain system-owned.
    """

    if not edges:
        return False
    handle = _window_handle(target)
    if handle is None:
        return False
    try:
        return bool(handle.startSystemResize(edges))
    except (AttributeError, RuntimeError, TypeError):
        return False


class PrismTitleBar(QFrame):
    """Calm Prism title bar with host-owned window actions.

    Public signals intentionally describe requests instead of performing
    window lifecycle work:

    ``pin_toggled(bool)``
        Temporary per-window pin state.  The host decides how to apply the
        ``WindowStaysOnTopHint`` flag and must not persist it here.
    ``minimize_requested()`` / ``maximize_requested()`` / ``close_requested()``
        Connect to the host's existing Qt methods.  In particular, close must
        call ``MainWindow.close()`` so ``closeEvent`` and tray confirmation stay
        authoritative.
    ``drag_double_clicked()``
        Emitted only for an empty title-bar area; the host toggles
        ``showMaximized``/``showNormal``.
    """

    pin_toggled = pyqtSignal(bool)
    minimize_requested = pyqtSignal()
    maximize_requested = pyqtSignal()
    close_requested = pyqtSignal()
    drag_double_clicked = pyqtSignal()
    drag_requested = pyqtSignal()
    system_move_started = pyqtSignal()
    system_move_unavailable = pyqtSignal()

    def __init__(
        self,
        application_name: str = "PengToolsHub",
        *,
        language: str = "zh",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("prism-titlebar")
        self.setFixedHeight(TITLEBAR_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("surfaceRole", "titlebar")

        self._application_name = str(application_name or "PengToolsHub")
        self._language = "en" if str(language).lower().startswith("en") else "zh"
        self._maximized = False
        self._menus: dict[str, QMenu] = {}
        self._menu_buttons: dict[str, QToolButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(4)
        self._layout = layout

        self.brand = QWidget(self)
        self.brand.setObjectName("prism-brand")
        self.brand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        brand_layout = QHBoxLayout(self.brand)
        brand_layout.setContentsMargins(0, 0, 6, 0)
        brand_layout.setSpacing(7)
        self.brand_icon = QLabel(self.brand)
        self.brand_icon.setObjectName("prism-brand-icon")
        self.brand_icon.setFixedSize(BRAND_ICON_SIZE, BRAND_ICON_SIZE)
        self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        brand_layout.addWidget(self.brand_icon)
        self.brand_name = QLabel(self._application_name, self.brand)
        self.brand_name.setObjectName("prism-brand-name")
        self.brand_name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        brand_layout.addWidget(self.brand_name)
        layout.addWidget(self.brand, 0, Qt.AlignmentFlag.AlignVCenter)

        self.menu_bar = QWidget(self)
        self.menu_bar.setObjectName("prism-menu-bar")
        self.menu_bar.setVisible(False)
        menu_layout = QHBoxLayout(self.menu_bar)
        menu_layout.setContentsMargins(0, 0, 4, 0)
        menu_layout.setSpacing(1)
        self._menu_layout = menu_layout
        layout.addWidget(self.menu_bar, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addStretch(1)

        self.window_actions = QWidget(self)
        self.window_actions.setObjectName("prism-window-actions")
        actions_layout = QHBoxLayout(self.window_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(1)
        self._actions_layout = actions_layout
        layout.addWidget(self.window_actions, 0, Qt.AlignmentFlag.AlignVCenter)

        self.pin_button = self._make_action_button(
            "prism-window-pin-btn", self._text("窗口置顶", "Keep window on top"), text="⌖"
        )
        self.pin_button.setCheckable(True)
        self.pin_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.pin_button.setProperty("windowAction", "pin")
        self.pin_button.toggled.connect(self._on_pin_toggled)
        actions_layout.addWidget(self.pin_button)

        self.minimize_button = self._make_action_button(
            "prism-window-min-btn", self._text("最小化", "Minimize")
        )
        self.minimize_button.setProperty("windowAction", "minimize")
        self.minimize_button.clicked.connect(self.minimize_requested.emit)
        actions_layout.addWidget(self.minimize_button)

        self.maximize_button = self._make_action_button(
            "prism-window-max-btn", self._text("最大化", "Maximize")
        )
        self.maximize_button.setProperty("windowAction", "maximize")
        self.maximize_button.clicked.connect(self.maximize_requested.emit)
        actions_layout.addWidget(self.maximize_button)

        self.close_button = self._make_action_button(
            "prism-window-close-btn", self._text("关闭", "Close")
        )
        self.close_button.setProperty("windowAction", "close")
        self.close_button.clicked.connect(self.close_requested.emit)
        actions_layout.addWidget(self.close_button)

        self._refresh_standard_icons()
        self._refresh_brand_icon()
        self._apply_style()

        # ThemeManager has a callback list rather than a Qt signal.  Keep the
        # callback local to this widget and remove it when the widget dies.
        try:
            from ui.theme_manager import ThemeManager

            manager = ThemeManager.instance()
            manager.add_listener(self._theme_changed)
            self.destroyed.connect(lambda: manager.remove_listener(self._theme_changed))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Host-facing state and menu API
    # ------------------------------------------------------------------
    def set_pinned(self, pinned: bool) -> None:
        """Reflect host pin state without emitting a new request."""

        value = bool(pinned)
        blocked = self.pin_button.blockSignals(True)
        self.pin_button.setChecked(value)
        self.pin_button.blockSignals(blocked)
        self._refresh_pin_icon()
        self._refresh_button_style(self.pin_button)

    def is_pinned(self) -> bool:
        return self.pin_button.isChecked()

    def set_maximized(self, maximized: bool) -> None:
        """Reflect the host's actual state and update the restore affordance."""

        self._maximized = bool(maximized)
        self.maximize_button.setProperty("maximized", self._maximized)
        self._refresh_standard_icons()
        self._set_button_texts()
        self._refresh_button_style(self.maximize_button)

    def is_maximized(self) -> bool:
        return self._maximized

    def set_language(self, language: str) -> None:
        self._language = "en" if str(language).lower().startswith("en") else "zh"
        self._set_button_texts()
        for title, button in self._menu_buttons.items():
            # Menu titles are owned by the host; do not translate injected
            # QMenus behind its back.  Refresh the stable accessible name only.
            button.setAccessibleName(title)
        self._apply_style()

    def refresh_theme(self) -> None:
        """Refresh theme-derived colors/icons for hosts without a manager hook."""

        self._theme_changed("")

    def set_menus(self, menus: Mapping[str, QMenu] | None) -> None:
        """Install host-owned menus in the brand/menu area.

        The mapping order becomes the visible order.  Existing menu objects
        and actions stay owned by the caller; this method only attaches them to
        title-bar tool buttons.  Passing ``None`` or an empty mapping hides the
        menu area.
        """

        while self._menu_layout.count():
            item = self._menu_layout.takeAt(0)
            button = item.widget()
            if button is not None:
                button.deleteLater()
        self._menu_buttons.clear()
        self._menus = {}

        for title, menu in (menus or {}).items():
            if not isinstance(menu, QMenu):
                continue
            key = str(title or "").strip()
            if not key:
                continue
            button = QToolButton(self.menu_bar)
            button.setObjectName(f"prism-menu-{self._object_key(key)}")
            button.setText(key)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setMenu(menu)
            button.setProperty("class", "prism-menu")
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(key)
            button.setToolTip(key)
            self._menu_layout.addWidget(button)
            self._menu_buttons[key] = button
            self._menus[key] = menu

        self.menu_bar.setVisible(bool(self._menu_buttons))
        self._apply_style()

    def menu_button(self, title: str) -> QToolButton | None:
        return self._menu_buttons.get(str(title or "").strip())

    def clear_menus(self) -> None:
        self.set_menus(None)

    def add_host_widget(self, widget: QWidget | None) -> bool:
        """Insert a host-owned control group before the window buttons.

        The shell uses this narrow hook for controls that must remain visible
        while the Web sidebar is active (author unlock, user menu, and
        navigation collapse).  Ownership and actions stay with MainWindow;
        the title bar only supplies the common client-area placement.
        """

        if not isinstance(widget, QWidget) or widget.parentWidget() not in (None, self):
            return False
        if widget.parentWidget() is None:
            widget.setParent(self)
        index = self._layout.indexOf(self.window_actions)
        if index < 0:
            self._layout.addWidget(widget)
        else:
            self._layout.insertWidget(index, widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return True

    # ------------------------------------------------------------------
    # Native move/resize delegation
    # ------------------------------------------------------------------
    def request_system_move(self) -> bool:
        """Delegate a title-bar drag to QWindow.startSystemMove()."""

        if start_system_move(self.window()):
            self.system_move_started.emit()
            return True
        self.system_move_unavailable.emit()
        return False

    def request_system_resize(self, edges: Qt.Edge) -> bool:
        """Delegate a host edge resize to QWindow.startSystemResize()."""

        return start_system_resize(self.window(), edges)

    # ------------------------------------------------------------------
    # Qt events and internal presentation helpers
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self.request_system_move():
                event.accept()
                return
            # The host can choose a narrowly-scoped fallback after seeing this
            # signal.  No coordinate-based drag is attempted here.
            self.drag_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _make_action_button(self, object_name: str, tooltip: str, *, text: str = "") -> QToolButton:
        button = QToolButton(self.window_actions)
        button.setObjectName(object_name)
        button.setFixedSize(WINDOW_BUTTON_SIZE, WINDOW_BUTTON_SIZE)
        button.setText(text)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    def _set_button_texts(self) -> None:
        zh = self._language == "zh"
        pin = "窗口置顶" if zh else "Keep window on top"
        minimum = "最小化" if zh else "Minimize"
        maximum = ("还原" if zh else "Restore") if self._maximized else ("最大化" if zh else "Maximize")
        close = "关闭" if zh else "Close"
        for button, text in (
            (self.pin_button, pin),
            (self.minimize_button, minimum),
            (self.maximize_button, maximum),
            (self.close_button, close),
        ):
            button.setToolTip(text)
            button.setAccessibleName(text)
        self.pin_button.setText("")

    def _refresh_standard_icons(self) -> None:
        style = self.style() or QApplication.style()
        if style is None:
            return
        self._refresh_pin_icon()
        self.minimize_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton))
        max_kind = (
            QStyle.StandardPixmap.SP_TitleBarNormalButton
            if self._maximized
            else QStyle.StandardPixmap.SP_TitleBarMaxButton
        )
        self.maximize_button.setIcon(style.standardIcon(max_kind))
        self.close_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setIconSize(QSize(15, 15))

    def _refresh_pin_icon(self) -> None:
        tokens = self._tokens()
        tint = tokens.get("PRIMARY", "#6C58D9") if self.pin_button.isChecked() else tokens.get("TEXT_MUTED", "#615D73")
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(tint))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(5.0, 3.5), QPointF(13.0, 3.5))
        painter.drawLine(QPointF(6.0, 3.5), QPointF(6.8, 9.0))
        painter.drawLine(QPointF(12.0, 3.5), QPointF(11.2, 9.0))
        painter.drawLine(QPointF(6.8, 9.0), QPointF(11.2, 9.0))
        painter.drawLine(QPointF(9.0, 9.0), QPointF(9.0, 14.0))
        painter.drawLine(QPointF(9.0, 14.0), QPointF(6.2, 16.5))
        painter.drawLine(QPointF(9.0, 14.0), QPointF(11.8, 16.5))
        painter.end()
        self.pin_button.setIcon(QIcon(pixmap))
        self.pin_button.setIconSize(QSize(17, 17))

    def _refresh_brand_icon(self) -> None:
        try:
            from ui.theme_manager import ThemeManager

            tint = ThemeManager.instance().token("PRIMARY_ACTIVE") or "#503DBE"
        except Exception:
            tint = "#503DBE"
        pixmap = brand_pixmap("app_mark", BRAND_ICON_SIZE, tint)
        if pixmap.isNull():
            pixmap = brand_pixmap("app", BRAND_ICON_SIZE, tint)
        if not pixmap.isNull():
            self.brand_icon.setPixmap(pixmap)
        else:
            self.brand_icon.clear()

    def _theme_changed(self, _theme_id: str) -> None:
        self._refresh_brand_icon()
        self._refresh_standard_icons()
        self._apply_style()

    def _on_pin_toggled(self, checked: bool) -> None:
        self._refresh_pin_icon()
        self._refresh_button_style(self.pin_button)
        self.pin_toggled.emit(bool(checked))

    @staticmethod
    def _object_key(value: str) -> str:
        result = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
        return result.strip("-") or "menu"

    def _text(self, zh: str, en: str) -> str:
        return zh if self._language == "zh" else en

    def _tokens(self) -> dict[str, str]:
        try:
            from ui.theme_manager import ThemeManager

            return ThemeManager.instance().palette()
        except Exception:
            return {
                "SURFACE": "#FDFDFF",
                "BORDER": "#E6E2F0",
                "TEXT_STRONG": "#262438",
                "TEXT": "#3D3952",
                "TEXT_MUTED": "#615D73",
                "PRIMARY": "#6C58D9",
                "PRIMARY_SOFT": "#EEE9FF",
                "DANGER": "#C45371",
                "ON_PRIMARY": "#FFFFFF",
            }

    def _refresh_button_style(self, button: QToolButton) -> None:
        style = button.style()
        if style is not None:
            style.unpolish(button)
            style.polish(button)
        button.update()

    def _apply_style(self) -> None:
        tokens = self._tokens()
        surface = tokens.get("SURFACE", "#FDFDFF")
        border = tokens.get("BORDER", "#E6E2F0")
        text = tokens.get("TEXT", tokens.get("TEXT_STRONG", "#3D3952"))
        strong = tokens.get("TEXT_STRONG", text)
        muted = tokens.get("TEXT_MUTED", text)
        primary = tokens.get("PRIMARY", "#6C58D9")
        primary_soft = tokens.get("PRIMARY_SOFT", "#EEE9FF")
        danger = tokens.get("DANGER", "#C45371")
        on_primary = tokens.get("ON_PRIMARY", "#FFFFFF")
        self.setStyleSheet(
            """
            QFrame#prism-titlebar {
                background: %(surface)s;
                border-bottom: 1px solid %(border)s;
            }
            QLabel#prism-brand-name {
                color: %(strong)s;
                font-size: 13px;
                font-weight: 600;
            }
            QToolButton#prism-menu-file,
            QToolButton#prism-menu-view,
            QToolButton#prism-menu-help,
            QToolButton[class="prism-menu"] {
                color: %(muted)s;
                border: 0;
                border-radius: 6px;
                padding: 4px 8px;
                background: transparent;
            }
            QToolButton#prism-menu-file:hover,
            QToolButton#prism-menu-view:hover,
            QToolButton#prism-menu-help:hover,
            QToolButton[class="prism-menu"]:hover {
                color: %(primary)s;
                background: %(primary_soft)s;
            }
            QToolButton#prism-window-pin-btn,
            QToolButton#prism-window-min-btn,
            QToolButton#prism-window-max-btn,
            QToolButton#prism-window-close-btn {
                color: %(muted)s;
                border: 0;
                border-radius: 6px;
                background: transparent;
                font-size: 16px;
                padding: 0;
            }
            QToolButton#prism-window-pin-btn:hover,
            QToolButton#prism-window-min-btn:hover,
            QToolButton#prism-window-max-btn:hover {
                color: %(primary)s;
                background: %(primary_soft)s;
            }
            QToolButton#prism-window-pin-btn:checked {
                color: %(primary)s;
                background: %(primary_soft)s;
            }
            QToolButton#prism-window-close-btn:hover {
                color: %(on_primary)s;
                background: %(danger)s;
            }
            QToolButton#prism-window-pin-btn:focus,
            QToolButton#prism-window-min-btn:focus,
            QToolButton#prism-window-max-btn:focus,
            QToolButton#prism-window-close-btn:focus {
                border: 1px solid %(primary)s;
            }
            """
            % {
                "surface": surface,
                "border": border,
                "text": text,
                "strong": strong,
                "muted": muted,
                "primary": primary,
                "primary_soft": primary_soft,
                "danger": danger,
                "on_primary": on_primary,
            }
        )
        self._set_button_texts()
        for button in (*self._menu_buttons.values(), self.pin_button, self.minimize_button, self.maximize_button, self.close_button):
            self._refresh_button_style(button)


__all__ = [
    "BRAND_ICON_SIZE",
    "PrismTitleBar",
    "TITLEBAR_H",
    "WINDOW_BUTTON_SIZE",
    "start_system_move",
    "start_system_resize",
]
