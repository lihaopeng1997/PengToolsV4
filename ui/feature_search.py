# -*- coding: utf-8 -*-
"""标题栏功能搜索。只跳转模块，不打开悬浮工具栏。"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.navigation_model import (
    GROUP_LABELS,
    NAV_ITEMS,
    NAV_MODEL,
    is_parent_nav,
    nav_is_db_slot,
)


@dataclass(frozen=True)
class FeatureEntry:
    index: int
    title: str
    detail: str
    haystack: str


_ENGINE_ALIASES = {
    14: 'oracle mysql oceanbase 达梦 dameng redis mongodb 数据库',
}


def feature_catalog(*, language: str = 'zh', private_unlocked: bool = True) -> list[FeatureEntry]:
    """侧栏能打开的页面。父级和旧数据库槽位不单列。"""

    zh = language != 'en'
    entries: list[FeatureEntry] = []
    seen: set[int] = set()

    def add(index: int, group_key: str) -> None:
        if index in seen or is_parent_nav(index) or nav_is_db_slot(index):
            return
        item = NAV_ITEMS.get(index)
        if item is None:
            return
        if item.requires_easter_egg and not private_unlocked:
            return
        seen.add(index)
        title = item.name_zh if zh else item.name_en
        group = GROUP_LABELS.get(group_key, ('', ''))
        group_label = group[0] if zh else group[1]
        detail = item.tooltip_zh if zh else item.tooltip_en
        blob = ' '.join((
            title,
            item.name_zh,
            item.name_en,
            detail,
            group_label,
            _ENGINE_ALIASES.get(index, ''),
        )).casefold()
        entries.append(FeatureEntry(index, title, detail or group_label, blob))

    for group_key, rows in NAV_MODEL:
        for nav_index, _name_zh, _name_en, _icon in rows:
            add(nav_index, group_key)
    add(7, 'settings')
    return entries


def filter_features(entries: list[FeatureEntry], query: str) -> list[FeatureEntry]:
    text = str(query or '').strip().casefold()
    if not text:
        return list(entries)
    parts = [part for part in text.split() if part]
    return [
        entry for entry in entries
        if all(part in entry.haystack for part in parts)
    ]


class FeatureSearchPopup(QFrame):
    """标题栏下的功能搜索。选择后发出导航索引。"""

    chosen = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName('feature-search-popup')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._catalog: list[FeatureEntry] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.query = QLineEdit(self)
        self.query.setObjectName('feature-search-query')
        self.query.setClearButtonEnabled(True)
        self.query.setPlaceholderText('搜索功能')
        self.query.textChanged.connect(self._refill)
        self.query.returnPressed.connect(self._activate_current)
        self.results = QListWidget(self)
        self.results.setObjectName('feature-search-results')
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemActivated.connect(self._activate_item)
        self.results.itemClicked.connect(self._activate_item)
        layout.addWidget(self.query)
        layout.addWidget(self.results, 1)
        self.setFixedWidth(320)
        self.query.installEventFilter(self)

    def set_catalog(self, entries: list[FeatureEntry], *, placeholder: str = '') -> None:
        self._catalog = list(entries)
        if placeholder:
            self.query.setPlaceholderText(placeholder)
        self.query.clear()
        self._refill()

    def open_under(self, anchor: QWidget) -> None:
        self.adjustSize()
        origin = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        screen = anchor.screen() or self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = min(origin.x(), area.right() - self.width() + 1)
            y = origin.y()
            if y + self.height() > area.bottom():
                y = max(area.top(), anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4)
            origin = QPoint(max(area.left(), x), max(area.top(), y))
        self.move(origin)
        self.show()
        self.raise_()
        self.query.setFocus()
        self.query.selectAll()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.query and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(watched, event)

    def _refill(self) -> None:
        matched = filter_features(self._catalog, self.query.text())
        self.results.clear()
        for entry in matched:
            item = QListWidgetItem(entry.title)
            item.setToolTip(entry.detail)
            item.setData(Qt.ItemDataRole.UserRole, entry.index)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        visible = min(8, max(1, self.results.count()))
        self.results.setFixedHeight(visible * 32 + 8)
        self.adjustSize()

    def _move_selection(self, step: int) -> None:
        count = self.results.count()
        if not count:
            return
        row = self.results.currentRow()
        if row < 0:
            row = 0
        else:
            row = (row + step) % count
        self.results.setCurrentRow(row)

    def _activate_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._activate_item(item)

    def _activate_item(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        try:
            nav_index = int(index)
        except (TypeError, ValueError):
            return
        self.hide()
        self.chosen.emit(nav_index)
