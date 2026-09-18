# -*- coding: utf-8 -*-
"""Prism native sidebar primitives.

The Web sidebar and the native fallback receive the same ``navModel`` payload.
This module only derives presentation groups from that payload and owns the
native popup boundary, so a 248px group menu can escape a 72/84px sidebar.
It deliberately does not contain navigation indices, permissions, or panel
construction logic.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QApplication,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.icons import apply_icon, qicon


_GROUP_ICON_BY_KEY = {
    'delivery': 'release',
    'ops': 'terminal',
    'devtools': 'json',
    'personal': 'learning',
}


def _leaf(item: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only display fields from a navModel item; preserve its index."""
    result = {
        'i': int(item.get('i', 0)),
        'zh': str(item.get('zh', '')),
        'en': str(item.get('en', '')),
        'icon': str(item.get('icon', '')),
    }
    for field in ('tip', 'dia', 'dash_zh'):
        if isinstance(item.get(field), str):
            result[field] = item[field]
    return result


def _group_icon(source: Mapping[str, Any], parent: Mapping[str, Any] | None) -> str:
    children = parent.get('children', []) if parent else []
    if isinstance(children, list) and any(
        isinstance(child, Mapping) and child.get('icon') == 'spark'
        for child in children
    ):
        # ``workbench`` is the local native equivalent of the Web spark role.
        return 'workbench'
    key = str(source.get('key') or '')
    if _GROUP_ICON_BY_KEY.get(key):
        return _GROUP_ICON_BY_KEY[key]
    candidate = parent
    if candidate is None:
        source_items = source.get('items', [])
        candidate = source_items[0] if source_items and isinstance(source_items[0], Mapping) else None
    return str((candidate or {}).get('icon', 'workbench'))


def derive_prism_groups(nav_model: Mapping[str, Any] | None) -> dict[str, Any]:
    """Derive the prototype rail shape from the injected navModel.

    The function intentionally has no application-specific index table. Parent
    ``children`` and direct group items remain the only source of menu leaves;
    permission filtering therefore stays in ``ui.navigation_model``.
    """
    if not isinstance(nav_model, Mapping):
        return {'home': None, 'groups': [], 'settings': None}

    home = None
    groups: list[dict[str, Any]] = []
    for source in nav_model.get('groups', []):
        if not isinstance(source, Mapping):
            continue
        source_key = str(source.get('key') or f'group-{len(groups)}')
        direct: dict[str, Any] | None = None
        for raw_item in source.get('items', []):
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            children = item.get('children')
            if item.get('i') == 0 and not isinstance(children, list):
                home = _leaf(item)
                continue
            if isinstance(children, list) and children:
                groups.append({
                    'key': f'{source_key}:{item.get("i")}',
                    'zh': str(item.get('zh', '')),
                    'en': str(item.get('en', '')),
                    'icon': _group_icon(source, item),
                    'items': [_leaf(child) for child in children if isinstance(child, Mapping)],
                    'divider_before': False,
                })
                continue
            if direct is None:
                direct = {
                    'key': source_key,
                    'zh': str(source.get('zh', '')),
                    'en': str(source.get('en', '')),
                    'icon': _group_icon(source, None),
                    'items': [],
                    'divider_before': source_key in {'delivery', 'devtools'},
                }
                groups.append(direct)
            direct['items'].append(_leaf(item))

    if groups:
        groups[0]['divider_before'] = True
    settings = nav_model.get('settings')
    return {
        'home': home,
        'groups': groups,
        'settings': _leaf(settings) if isinstance(settings, Mapping) else None,
    }


class PrismSidebar(QWidget):
    """Native Prism rail with menus that can escape the narrow sidebar.

    ``navigate_requested`` is the only business-facing signal. The caller owns
    ``_show_panel`` and panel lifecycle; this widget never constructs a panel.
    """

    navigate_requested = pyqtSignal(int)
    palette_requested = pyqtSignal()

    def __init__(
        self,
        nav_model: Mapping[str, Any] | None = None,
        *,
        language: str = 'zh',
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName('sidebar')
        self._language = language if language in {'zh', 'en'} else 'zh'
        self._model: dict[str, Any] = {'home': None, 'groups': [], 'settings': None}
        self._buttons: dict[int, QPushButton] = {}
        self._group_buttons: dict[str, QPushButton] = {}
        self._settings_button: QPushButton | None = None
        self._palette_button: QPushButton | None = None
        self._group_menu: QMenu | None = None
        self._current = 0
        self._icon_only = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 14, 12, 12)
        outer.setSpacing(0)
        self._outer_layout = outer

        brand = QFrame(self)
        brand.setObjectName('sidebar-brand')
        brand.setFixedHeight(64)
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(8, 8, 8, 8)
        brand_layout.setSpacing(10)
        self._brand_mark = QLabel(brand)
        self._brand_mark.setObjectName('sidebar-brand-icon')
        self._brand_mark.setFixedSize(36, 36)
        brand_layout.addWidget(self._brand_mark)
        self._brand_text = QLabel('PengToolsHub', brand)
        self._brand_text.setObjectName('sidebar_title')
        brand_layout.addWidget(self._brand_text, 1)
        outer.addWidget(brand)

        scroll = QScrollArea(self)
        scroll.setObjectName('sidebar-scroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_host = QWidget(scroll)
        self._nav_host.setObjectName('sidebar-nav-host')
        self._nav_layout = QVBoxLayout(self._nav_host)
        self._nav_layout.setContentsMargins(0, 10, 0, 0)
        self._nav_layout.setSpacing(2)
        scroll.setWidget(self._nav_host)
        outer.addWidget(scroll, 1)

        separator = QFrame(self)
        separator.setObjectName('sidebar-sep')
        separator.setFixedHeight(1)
        outer.addWidget(separator)

        footer = QVBoxLayout()
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(2)
        self._footer_layout = footer
        outer.addLayout(footer)

        self.set_nav_model(nav_model or {})

    @property
    def nav_model(self) -> dict[str, Any]:
        return self._model

    def set_nav_model(self, nav_model: Mapping[str, Any] | None) -> None:
        self._close_group_menu()
        self._model = derive_prism_groups(nav_model)
        self._rebuild()

    def set_current(self, nav_index: int) -> None:
        self._current = int(nav_index)
        for index, button in self._buttons.items():
            button.setChecked(index == self._current)
        for group_key, button in self._group_buttons.items():
            group = next((g for g in self._model['groups'] if g['key'] == group_key), None)
            is_active = bool(group and any(item['i'] == self._current for item in group['items']))
            button.setChecked(is_active)

    def set_language(self, language: str) -> None:
        if language not in {'zh', 'en'}:
            return
        self._language = language
        self._rebuild()

    def set_icon_only(self, icon_only: bool) -> None:
        self._icon_only = bool(icon_only)
        side_margin = 0 if self._icon_only else 12
        self._outer_layout.setContentsMargins(side_margin, 14, side_margin, 12)
        self._brand_text.setVisible(not self._icon_only)
        for index, button in self._buttons.items():
            item = self._item_for_index(index)
            self._apply_button_mode(
                button,
                str(item.get('zh', '')),
                str(item.get('en', '')),
            )
        for key, button in self._group_buttons.items():
            group = self._group_for_key(key)
            self._apply_button_mode(
                button,
                str((group or {}).get('zh', '')),
                str((group or {}).get('en', '')),
            )
        if self._settings_button is not None:
            settings = self._model.get('settings') or {}
            self._apply_button_mode(
                self._settings_button,
                str(settings.get('zh', '设置')),
                str(settings.get('en', 'Settings')),
            )
        if self._palette_button is not None:
            self._apply_button_mode(self._palette_button, '主题', 'Theme')
        for label in self._nav_host.findChildren(QLabel, 'sidebar-section'):
            label.setVisible(not self._icon_only)

    def _item_for_index(self, nav_index: int) -> Mapping[str, Any]:
        home = self._model.get('home')
        if isinstance(home, Mapping) and int(home.get('i', -1)) == int(nav_index):
            return home
        for group in self._model.get('groups', []):
            for item in group.get('items', []):
                if isinstance(item, Mapping) and int(item.get('i', -1)) == int(nav_index):
                    return item
        return {}

    def _group_for_key(self, group_key: str) -> Mapping[str, Any] | None:
        return next(
            (
                group for group in self._model.get('groups', [])
                if isinstance(group, Mapping) and str(group.get('key', '')) == str(group_key)
            ),
            None,
        )

    def show_group_menu(
        self,
        group_key: str,
        anchor: QWidget | None = None,
        *,
        anchor_rect: Mapping[str, Any] | None = None,
    ) -> None:
        if anchor_rect is not None:
            anchor_rect = self._normalize_anchor_rect(anchor_rect)
            if anchor_rect is None:
                return
        group = next((g for g in self._model['groups'] if g['key'] == group_key), None)
        if not group:
            return
        self._close_group_menu()
        menu = QMenu(self)
        menu.setObjectName('prism-nav-group-menu')
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._apply_group_menu_style(menu)
        menu.setMinimumWidth(248)
        primary = QApplication.primaryScreen()
        available_height = primary.availableGeometry().height() if primary else 720
        menu.setMaximumHeight(max(120, int(available_height * 0.75)))
        title = QAction(f"{group['zh']} · {group['en']}", menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()
        leaf_actions: list[QAction] = []
        for item in group['items']:
            label = item['zh'] if self._language == 'zh' else item['en']
            action = QAction(label, menu)
            action.setData(item['i'])
            if item.get('tip'):
                action.setToolTip(str(item['tip']))
            action.setIcon(qicon(item.get('icon', ''), size=16))
            action.setCheckable(True)
            action.setChecked(int(item['i']) == self._current)
            action.triggered.connect(lambda checked=False, index=item['i']: self._emit_navigation(index))
            menu.addAction(action)
            leaf_actions.append(action)
        self._group_menu = menu

        source = anchor or self._group_buttons.get(group_key)
        if source is None and not anchor_rect:
            return
        menu.aboutToHide.connect(lambda m=menu, s=source: self._menu_hidden(m, s))
        if anchor_rect:
            left = int(float(anchor_rect.get('left', 0)))
            top = int(float(anchor_rect.get('top', 0)))
            right = int(float(anchor_rect.get('right', left)))
            top_left = QPoint(right + 10, top)
        else:
            top_left = source.mapToGlobal(QPoint(source.width() + 10, 0))
        menu.adjustSize()
        screen = self._screen_for(source) if source is not None else self._screen_for_point(top_left)
        available = screen.availableGeometry()
        left = top_left.x()
        top = top_left.y()
        if left + menu.width() > available.right() - 10:
            if source is not None:
                left = source.mapToGlobal(QPoint(-menu.width() - 10, 0)).x()
            elif anchor_rect:
                left = int(float(anchor_rect.get('left', left))) - menu.width() - 10
        if top + menu.height() > available.bottom() - 10:
            top = available.bottom() - menu.height() - 10
        left = max(available.left() + 10, min(left, available.right() - menu.width() - 10))
        top = max(available.top() + 10, min(top, available.bottom() - menu.height() - 10))
        menu.popup(QPoint(left, top))
        if leaf_actions:
            menu.setActiveAction(leaf_actions[0])

    def show_group_menu_at(self, group_key: str, anchor_rect: Mapping[str, Any]) -> None:
        """Open a menu from a WebView-local rect mapped by the integrator."""
        self.show_group_menu(group_key, anchor_rect=anchor_rect)

    @staticmethod
    def _normalize_anchor_rect(anchor_rect: Mapping[str, Any]) -> dict[str, float] | None:
        try:
            values = {
                key: float(anchor_rect[key])
                for key in ('left', 'top', 'right', 'bottom', 'width', 'height')
            }
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in values.values()):
            return None
        if values['right'] < values['left'] or values['bottom'] < values['top']:
            return None
        return values

    @staticmethod
    def _apply_group_menu_style(menu: QMenu) -> None:
        """Use Prism calm tokens while keeping QMenu's native focus semantics."""
        try:
            from ui.theme_manager import ThemeManager

            palette = ThemeManager.instance().palette()
        except Exception:
            palette = {}
        surface = palette.get('ELEVATED_SURFACE', palette.get('SURFACE', '#FDFDFF'))
        border = palette.get('ELEVATED_BORDER', palette.get('BORDER', '#E6E2F0'))
        text = palette.get('TEXT_NAV', palette.get('TEXT', '#4B5569'))
        muted = palette.get('TEXT_MUTED', '#615D73')
        primary = palette.get('PRIMARY', '#6C58D9')
        primary_soft = palette.get('PRIMARY_SOFT', '#EEE9FF')
        menu.setStyleSheet(
            'QMenu#prism-nav-group-menu {'
            f'background: {surface}; border: 1px solid {border}; border-radius: 15px; padding: 10px;'
            '}'
            'QMenu#prism-nav-group-menu::item {'
            f'color: {text}; padding: 10px 12px; margin: 1px 0; min-height: 20px; border-radius: 8px;'
            '}'
            'QMenu#prism-nav-group-menu::item:selected {'
            f'color: {primary}; background: {primary_soft};'
            '}'
            'QMenu#prism-nav-group-menu::item:checked {'
            f'color: {primary}; font-weight: 600; background: {primary_soft};'
            '}'
            'QMenu#prism-nav-group-menu::item:disabled {'
            f'color: {muted}; padding-top: 8px; padding-bottom: 8px;'
            '}'
            'QMenu#prism-nav-group-menu::separator {'
            f'height: 1px; background: {border}; margin: 7px 2px;'
            '}'
        )

    def _screen_for(self, widget: QWidget):
        point = widget.mapToGlobal(widget.rect().center())
        return self._screen_for_point(point)

    @staticmethod
    def _screen_for_point(point: QPoint):
        return QApplication.screenAt(point) or QApplication.primaryScreen()

    def _emit_navigation(self, index: int) -> None:
        self._close_group_menu()
        self.navigate_requested.emit(int(index))

    def _menu_hidden(self, menu: QMenu, source: QWidget | None = None) -> None:
        if self._group_menu is menu:
            self._group_menu = None
        if source is not None:
            source.setFocus(Qt.FocusReason.OtherFocusReason)

    def _close_group_menu(self) -> None:
        if self._group_menu is not None:
            menu, self._group_menu = self._group_menu, None
            menu.close()
            menu.deleteLater()

    def _clear_layout(self) -> None:
        while self._nav_layout.count():
            entry = self._nav_layout.takeAt(0)
            if entry.widget() is not None:
                entry.widget().deleteLater()
        while self._footer_layout.count():
            entry = self._footer_layout.takeAt(0)
            if entry.widget() is not None:
                entry.widget().deleteLater()
        self._buttons.clear()
        self._group_buttons.clear()
        self._settings_button = None
        self._palette_button = None

    def _rebuild(self) -> None:
        self._clear_layout()
        home = self._model.get('home')
        if home:
            self._nav_layout.addWidget(self._make_leaf_button(home))
        self._add_separator()
        for group in self._model.get('groups', []):
            if group.get('divider_before'):
                self._add_separator()
            section = QLabel(f"{group['zh']} · {group['en']}", self._nav_host)
            section.setObjectName('sidebar-section')
            # The prototype puts the group name on the rail button; retain the
            # semantic label for inspection/tooling without duplicating it in
            # the visible rail.
            section.hide()
            section.setMinimumSize(0, 0)
            section.setMaximumSize(0, 0)
            self._nav_layout.addWidget(section)
            button = QPushButton(self._nav_host)
            button.setObjectName('nav-btn')
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            apply_icon(button, group.get('icon', 'workbench'), size=20)
            button.setProperty('groupKey', group['key'])
            button.clicked.connect(lambda checked=False, key=group['key'], b=button: self.show_group_menu(key, b))
            self._group_buttons[group['key']] = button
            self._nav_layout.addWidget(button)
            self._apply_button_mode(button, group['zh'], group['en'])

        self._nav_layout.addStretch(1)

        settings = self._model.get('settings')
        if settings:
            self._settings_button = self._make_leaf_button(settings, settings=True)
            self._footer_layout.addWidget(self._settings_button)
        self._palette_button = QPushButton(self)
        self._palette_button.setObjectName('nav-btn-settings')
        self._palette_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_icon(self._palette_button, 'workbench', size=20)
        self._palette_button.clicked.connect(self.palette_requested.emit)
        self._footer_layout.addWidget(self._palette_button)
        self.set_current(self._current)
        self.set_icon_only(self._icon_only)

    def _add_separator(self) -> None:
        line = QFrame(self._nav_host)
        line.setObjectName('sidebar-sep')
        line.setFixedHeight(1)
        self._nav_layout.addWidget(line)

    def _make_leaf_button(self, item: Mapping[str, Any], *, settings: bool = False) -> QPushButton:
        button = QPushButton(self._nav_host)
        button.setObjectName('nav-btn-settings' if settings else 'nav-btn')
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        index = int(item['i'])
        apply_icon(button, str(item.get('icon', '')), size=20)
        button.setProperty('navIndex', index)
        button.clicked.connect(lambda checked=False, value=index: self._emit_navigation(value))
        self._buttons[index] = button
        self._apply_button_mode(button, str(item.get('zh', '')), str(item.get('en', '')))
        return button

    def _apply_button_mode(self, button: QPushButton, zh: str = '', en: str = '') -> None:
        text = en if self._language == 'en' else zh
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setMinimumWidth(0)
        button.setText('' if self._icon_only else text)
        button.setProperty('iconOnly', self._icon_only)
        tip = text or zh or en
        button.setToolTip(tip)
        button.style().unpolish(button)
        button.style().polish(button)
