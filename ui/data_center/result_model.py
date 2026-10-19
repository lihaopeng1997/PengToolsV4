"""Typed, paged Qt model for manual relational query results."""

from __future__ import annotations

from typing import Any, Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from tools.data_center.manual_session import ManualQueryPage
from ui.data_center.value_format import is_loaded_value


class ManualResultTableModel(QAbstractTableModel):
    """Hold immutable ManualQueryPage objects without per-cell widget copies."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages: tuple[ManualQueryPage, ...] = ()
        self._row_count = 0

    @property
    def pages(self) -> tuple[ManualQueryPage, ...]:
        return self._pages

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._row_count

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid() or not self._pages:
            return 0
        return len(self._pages[0].columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < self._row_count):
            return None
        value = self._value_at(index.row(), index.column())
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            return display_value(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            return _type_label(value)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole or not self._pages:
            return None
        if orientation == Qt.Orientation.Horizontal:
            columns = self._pages[0].columns
            return columns[section].name if 0 <= section < len(columns) else None
        return section + 1 if 0 <= section < self._row_count else None

    def replace_page(self, page: ManualQueryPage) -> None:
        self.replace_pages((page,))

    def replace_pages(self, pages: Sequence[ManualQueryPage]) -> None:
        values = tuple(pages)
        _validate_page_chain(values)
        self.beginResetModel()
        self._pages = values
        self._row_count = sum(len(page.rows) for page in values)
        self.endResetModel()

    def append_page(self, page: ManualQueryPage) -> None:
        if self._pages:
            previous = self._pages[-1]
            if (
                page.query_id != previous.query_id
                or page.tab_id != previous.tab_id
                or page.generation != previous.generation
                or page.page_number != previous.page_number + 1
                or page.columns != previous.columns
            ):
                raise ValueError("manual result page does not continue the current query")
        elif page.page_number != 1:
            raise ValueError("the first displayed page must be page 1")
        start = self._row_count
        end = start + len(page.rows) - 1
        if end >= start:
            self.beginInsertRows(QModelIndex(), start, end)
            self._pages = (*self._pages, page)
            self._row_count = end + 1
            self.endInsertRows()
        else:
            # Empty pages can still advance a cursor; keep their query/page
            # metadata without emitting an invalid insertion range.
            self._pages = (*self._pages, page)

    def clear(self) -> None:
        if not self._pages:
            return
        self.beginResetModel()
        self._pages = ()
        self._row_count = 0
        self.endResetModel()

    def _value_at(self, row: int, column: int) -> Any:
        if column < 0 or column >= self.columnCount():
            return None
        remaining = row
        for page in self._pages:
            if remaining < len(page.rows):
                values = page.rows[remaining]
                return values[column] if column < len(values) else None
            remaining -= len(page.rows)
        return None


def _validate_page_chain(pages: tuple[ManualQueryPage, ...]) -> None:
    if not pages:
        return
    first = pages[0]
    if first.page_number != 1:
        raise ValueError("the first displayed page must be page 1")
    expected_page_number = 1
    for page in pages:
        if (
            page.query_id != first.query_id
            or page.tab_id != first.tab_id
            or page.generation != first.generation
            or page.page_number != expected_page_number
            or page.columns != first.columns
        ):
            raise ValueError("manual result pages must belong to one query and tab generation")
        expected_page_number += 1


def display_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if not is_loaded_value(value):
        return f"<{type(value).__name__}>"
    if isinstance(value, bytes):
        text = value[:512].hex()
        return text + ("…" if len(value) > 512 else "")
    text = str(value)
    return text if len(text) <= 4096 else text[:4096] + "…"


def _type_label(value: Any) -> str:
    return "NULL" if value is None else type(value).__name__


__all__ = ["ManualResultTableModel", "display_value"]
