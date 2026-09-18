# -*- coding: utf-8 -*-
"""Global module tabs for the Prism client shell.

This widget is a small navigation view model, not a second panel manager.  It
stores only real ``navigation_model`` IDs and delegates activation/closing to a
host through signals.  In particular, closing a tab never deletes a panel,
ends a session, cancels a task, or chooses a replacement page on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QStyle,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QWidget,
)

from ui.icons import qicon
from ui.navigation_model import display_name, get_nav_item, icon_role_for, is_parent_nav


HOME_NAV = 0
MODULE_TABS_H = 38
TAB_CLOSE_BUTTON_SIZE = 18


@dataclass(frozen=True)
class ModuleTab:
    """Display metadata derived from one real navigation ID."""

    nav_index: int
    title: str
    icon_role: str
    closable: bool = True


def module_tab_for_nav(nav_index: int, language: str = "zh") -> ModuleTab | None:
    """Build a tab record from the authoritative navigation model.

    Parent entries (14/15), unknown IDs, and malformed values do not become
    tabs.  The home entry is always valid and is marked non-closable.
    """

    try:
        index = int(nav_index)
    except (TypeError, ValueError):
        return None
    if get_nav_item(index) is None or is_parent_nav(index):
        return None
    return ModuleTab(
        nav_index=index,
        title=display_name(index, language),
        icon_role=icon_role_for(index),
        closable=index != HOME_NAV,
    )


class _ModuleTabBar(QTabBar):
    """QTabBar with a stable data contract for the shell wrapper."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("module-tab-bar")
        self.setDocumentMode(True)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setMovable(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setTabsClosable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)


class ModuleTabs(QFrame):
    """Global open-module tab strip.

    ``open_nav``/``remove_nav`` are host synchronization methods and do not
    emit navigation signals.  User interactions emit ``activate_requested``
    or ``close_requested`` with a real navigation ID.  The host then invokes
    the existing ``_show_panel`` and its established deactivation flow.
    """

    activate_requested = pyqtSignal(int)
    close_requested = pyqtSignal(int)
    context_requested = pyqtSignal(int, QPoint)

    def __init__(self, *, language: str = "zh", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("module-tabs")
        self.setFixedHeight(MODULE_TABS_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("surfaceRole", "module-tabs")

        self._language = "en" if str(language).lower().startswith("en") else "zh"
        self._open_nav: list[int] = [HOME_NAV]
        self._current_nav = HOME_NAV
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)
        self._layout = layout

        self.tab_bar = _ModuleTabBar(self)
        layout.addWidget(self.tab_bar)
        self.tab_bar.currentChanged.connect(self._on_current_changed)
        self.tab_bar.tabCloseRequested.connect(self._on_close_requested)
        self.tab_bar.customContextMenuRequested.connect(self._on_context_requested)

        self._apply_style()
        self._refresh_tabs()

        try:
            from ui.theme_manager import ThemeManager

            manager = ThemeManager.instance()
            manager.add_listener(self._theme_changed)
            self.destroyed.connect(lambda: manager.remove_listener(self._theme_changed))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Host synchronization API
    # ------------------------------------------------------------------
    @property
    def open_nav_indices(self) -> tuple[int, ...]:
        return tuple(self._open_nav)

    @property
    def current_nav_index(self) -> int:
        return self._current_nav

    def open_nav(self, nav_index: int, *, activate: bool = True) -> bool:
        """Ensure a real leaf nav ID has a visible tab.

        This method is intentionally silent.  MainWindow can call it after a
        successful ``_show_panel`` without recursively triggering its own
        navigation path.
        """

        record = module_tab_for_nav(nav_index, self._language)
        if record is None:
            return False
        index = record.nav_index
        if index not in self._open_nav:
            self._open_nav.append(index)
            self._refresh_tabs(current=index if activate else self._current_nav)
        elif activate:
            self.set_current_nav(index)
        return True

    def ensure_nav(self, nav_index: int, *, activate: bool = True) -> bool:
        """Readable alias for hosts that treat tabs as a synchronization view."""

        return self.open_nav(nav_index, activate=activate)

    def set_current_nav(self, nav_index: int) -> bool:
        """Select an already-open tab without emitting a host navigation request."""

        record = module_tab_for_nav(nav_index, self._language)
        if record is None or record.nav_index not in self._open_nav:
            return False
        self._current_nav = record.nav_index
        tab_index = self.tab_index(record.nav_index)
        if tab_index >= 0 and self.tab_bar.currentIndex() != tab_index:
            blocked = self.tab_bar.blockSignals(True)
            self.tab_bar.setCurrentIndex(tab_index)
            self.tab_bar.blockSignals(blocked)
        return True

    def remove_nav(self, nav_index: int) -> bool:
        """Remove only the visual navigation tab; never touches a panel."""

        try:
            index = int(nav_index)
        except (TypeError, ValueError):
            return False
        if index == HOME_NAV or index not in self._open_nav:
            return False
        fallback = self.neighbor_nav(index)
        self._open_nav.remove(index)
        current = self._current_nav
        if current == index:
            current = fallback if fallback in self._open_nav else HOME_NAV
        self._refresh_tabs(current=current)
        return True

    def neighbor_nav(self, nav_index: int) -> int:
        """Return a close fallback without mutating the tab list.

        The host should call this before ``remove_nav`` when the active tab is
        being closed, invoke ``_show_panel`` for the returned ID, and then
        remove the visual tab.  A non-active close keeps the current tab.
        """

        try:
            index = int(nav_index)
        except (TypeError, ValueError):
            return self._current_nav
        if index not in self._open_nav or index != self._current_nav:
            return self._current_nav
        position = self._open_nav.index(index)
        if position > 0:
            return self._open_nav[position - 1]
        if position + 1 < len(self._open_nav):
            return self._open_nav[position + 1]
        return HOME_NAV

    def replace_nav(self, nav_indices) -> tuple[int, ...]:
        """Replace the visible list while enforcing home-first and leaf-only."""

        result: list[int] = [HOME_NAV]
        seen = {HOME_NAV}
        for raw in nav_indices or ():
            record = module_tab_for_nav(raw, self._language)
            if record is None or record.nav_index in seen:
                continue
            result.append(record.nav_index)
            seen.add(record.nav_index)
        self._open_nav = result
        if self._current_nav not in seen:
            self._current_nav = HOME_NAV
        self._refresh_tabs(current=self._current_nav)
        return tuple(result)

    def tab_index(self, nav_index: int) -> int:
        try:
            wanted = int(nav_index)
        except (TypeError, ValueError):
            return -1
        for i in range(self.tab_bar.count()):
            if self.tab_bar.tabData(i) == wanted:
                return i
        return -1

    def tab_record(self, nav_index: int) -> ModuleTab | None:
        return module_tab_for_nav(nav_index, self._language)

    def set_language(self, language: str) -> None:
        self._language = "en" if str(language).lower().startswith("en") else "zh"
        self._refresh_tabs(current=self._current_nav)

    def refresh_theme(self) -> None:
        """Refresh theme-derived colors/icons for hosts without a manager hook."""

        self._theme_changed("")

    # ------------------------------------------------------------------
    # QTabBar request signals
    # ------------------------------------------------------------------
    def _on_current_changed(self, tab_index: int) -> None:
        if self._syncing or tab_index < 0:
            return
        raw = self.tab_bar.tabData(tab_index)
        try:
            nav_index = int(raw)
        except (TypeError, ValueError):
            return
        if module_tab_for_nav(nav_index, self._language) is None:
            return
        self._current_nav = nav_index
        self.activate_requested.emit(nav_index)

    def _on_close_requested(self, tab_index: int) -> None:
        if tab_index < 0:
            return
        raw = self.tab_bar.tabData(tab_index)
        try:
            nav_index = int(raw)
        except (TypeError, ValueError):
            return
        record = module_tab_for_nav(nav_index, self._language)
        if record is None or not record.closable:
            return
        # Deliberately do not remove the tab here.  The host chooses the
        # adjacent fallback and calls remove_nav after reusing _show_panel.
        self.close_requested.emit(nav_index)

    def _on_context_requested(self, position: QPoint) -> None:
        tab_index = self.tab_bar.tabAt(position)
        if tab_index < 0:
            return
        raw = self.tab_bar.tabData(tab_index)
        try:
            nav_index = int(raw)
        except (TypeError, ValueError):
            return
        if module_tab_for_nav(nav_index, self._language) is not None:
            self.context_requested.emit(nav_index, self.tab_bar.mapToGlobal(position))

    # ------------------------------------------------------------------
    # Rendering and theme
    # ------------------------------------------------------------------
    def _refresh_tabs(self, *, current: int | None = None) -> None:
        wanted = current if current in self._open_nav else HOME_NAV
        self._syncing = True
        try:
            while self.tab_bar.count():
                self.tab_bar.removeTab(self.tab_bar.count() - 1)
            for nav_index in self._open_nav:
                record = module_tab_for_nav(nav_index, self._language)
                if record is None:
                    continue
                tab_index = self.tab_bar.addTab(qicon(record.icon_role, size=16), record.title)
                self.tab_bar.setTabData(tab_index, record.nav_index)
                self.tab_bar.setTabToolTip(tab_index, record.title)
                self.tab_bar.setTabWhatsThis(tab_index, record.title)
                self._set_tab_close_button(tab_index, record)
            index = self.tab_index(wanted)
            if index >= 0:
                self.tab_bar.setCurrentIndex(index)
                self._current_nav = wanted
            else:
                self._current_nav = HOME_NAV
        finally:
            self._syncing = False

    def _set_tab_close_button(self, tab_index: int, record: ModuleTab) -> None:
        close_button = self.tab_bar.tabButton(tab_index, QTabBar.ButtonPosition.RightSide)
        if not record.closable:
            if close_button is not None:
                close_button.hide()
            self.tab_bar.setTabButton(tab_index, QTabBar.ButtonPosition.RightSide, None)
            return
        if close_button is None:
            close_button = QToolButton(self.tab_bar)
            self.tab_bar.setTabButton(tab_index, QTabBar.ButtonPosition.RightSide, close_button)
        close_button.setObjectName(f"module-tab-close-{record.nav_index}")
        close_button.setFixedSize(TAB_CLOSE_BUTTON_SIZE, TAB_CLOSE_BUTTON_SIZE)
        if hasattr(close_button, "setAutoRaise"):
            close_button.setAutoRaise(True)
        close_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setToolTip(f"关闭 {record.title}")
        close_button.setAccessibleName(f"关闭 {record.title}")
        style = self.style() or self.tab_bar.style()
        if style is not None:
            close_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
            close_button.setIconSize(close_button.size() * 0.6)

    def _theme_changed(self, _theme_id: str) -> None:
        self._apply_style()
        self._refresh_tabs(current=self._current_nav)

    def _tokens(self) -> dict[str, str]:
        try:
            from ui.theme_manager import ThemeManager

            return ThemeManager.instance().palette()
        except Exception:
            return {
                "SURFACE": "#FDFDFF",
                "SURFACE_SOFT": "#F0ECFA",
                "BORDER": "#E6E2F0",
                "TEXT": "#3D3952",
                "TEXT_MUTED": "#615D73",
                "PRIMARY": "#6C58D9",
                "PRIMARY_SOFT": "#EEE9FF",
                "PRIMARY_ACTIVE": "#503DBE",
            }

    def _apply_style(self) -> None:
        tokens = self._tokens()
        surface = tokens.get("SURFACE", "#FDFDFF")
        soft = tokens.get("SURFACE_SOFT", surface)
        border = tokens.get("BORDER", "#E6E2F0")
        text = tokens.get("TEXT", "#3D3952")
        muted = tokens.get("TEXT_MUTED", text)
        primary = tokens.get("PRIMARY", "#6C58D9")
        primary_soft = tokens.get("PRIMARY_SOFT", "#EEE9FF")
        active = tokens.get("PRIMARY_ACTIVE", primary)
        self.setStyleSheet(
            """
            QFrame#module-tabs {
                background: %(soft)s;
                border-bottom: 1px solid %(border)s;
            }
            QTabBar#module-tab-bar {
                background: transparent;
            }
            QTabBar#module-tab-bar::tab {
                min-height: 27px;
                padding: 4px 9px 4px 10px;
                margin: 4px 2px 3px 0;
                color: %(muted)s;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
            }
            QTabBar#module-tab-bar::tab:hover {
                color: %(active)s;
                background: %(primary_soft)s;
            }
            QTabBar#module-tab-bar::tab:selected {
                color: %(active)s;
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-bottom: 2px solid %(primary)s;
                font-weight: 600;
            }
            QToolButton#module-tab-close-0,
            QTabBar#module-tab-bar QToolButton {
                color: %(muted)s;
                border: 0;
                border-radius: 5px;
                padding: 0;
                background: transparent;
            }
            QTabBar#module-tab-bar QToolButton:hover {
                color: %(active)s;
                background: %(primary_soft)s;
            }
            """
            % {
                "surface": surface,
                "soft": soft,
                "border": border,
                "text": text,
                "muted": muted,
                "primary": primary,
                "primary_soft": primary_soft,
                "active": active,
            }
        )


__all__ = [
    "HOME_NAV",
    "MODULE_TABS_H",
    "ModuleTab",
    "ModuleTabs",
    "module_tab_for_nav",
]
