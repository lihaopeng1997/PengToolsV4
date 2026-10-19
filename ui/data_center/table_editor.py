"""Typed local editor for one signed relational table source.

The editor never opens a connection or creates SQL from a display label. It
keeps the fetched rows and local overlays in memory and delegates every write
to the existing verified ``ChangeSet`` / ``ManualSession`` owner.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from PyQt6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from tools.data_center.changeset import ChangePreview, ChangeSet, ChangeSetError
from tools.data_center.changeset_dialects import create_changeset_dialect
from tools.data_center.manual_session import ManualQueryPage
from tools.data_center.table_source import TableSourceMetadata, build_table_select
from ui.data_center.result_view import ResultTableView
from ui.data_center.value_inspector import inspect_value, value_preview
from ui.data_center.value_format import is_loaded_value


PAGE_SIZES = (50, 200, 500)


class TableEditorModel(QAbstractTableModel):
    """Keep typed source rows and a per-table local change overlay."""

    def __init__(
        self,
        metadata: TableSourceMetadata,
        choice: Any,
        language: str = "zh",
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(metadata, TableSourceMetadata):
            raise TypeError("metadata must be TableSourceMetadata")
        self.metadata = metadata
        self.choice = choice
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self._column_names = tuple(column.name for column in metadata.columns)
        self._rows: list[tuple[Any, ...]] = []
        self._originals: list[dict[str, Any]] = []
        self._row_keys: list[tuple[Any, ...] | None] = []
        self._updates: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._deletes: set[tuple[Any, ...]] = set()
        self._inserts: list[dict[str, Any]] = []
        self._editing_enabled = True
        self._page_stale = False
        self._change_set: ChangeSet | None = None
        if metadata.verified_changeset is not None:
            dialect = create_changeset_dialect(choice, metadata.target)
            self._change_set = ChangeSet(metadata.verified_changeset, dialect)

    @property
    def has_staged_changes(self) -> bool:
        return bool(self._updates or self._deletes or self._inserts)

    @property
    def staged_count(self) -> int:
        return len(self._updates) + len(self._deletes) + len(self._inserts)

    @property
    def change_set(self) -> ChangeSet | None:
        return self._change_set

    @property
    def page_stale(self) -> bool:
        return self._page_stale

    @property
    def can_edit(self) -> bool:
        return self._change_set is not None and self._editing_enabled and not self._page_stale

    @property
    def editing_enabled(self) -> bool:
        """Whether the owner currently allows edits while an operation runs."""
        return self._editing_enabled

    @property
    def can_insert(self) -> bool:
        if not self.can_edit:
            return False
        columns = {column.name: column for column in self.metadata.columns}
        required = set(self.metadata.primary_key) | {
            column.name for column in self.metadata.columns if column.required_on_insert
        }
        return all(
            name in columns and columns[name].editable and columns[name].generated is not True
            for name in required
        )

    def set_editing_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._editing_enabled == enabled:
            return
        self._editing_enabled = enabled
        self._emit_all_data_changed()

    def mark_page_stale(self) -> None:
        if self._page_stale:
            return
        self._page_stale = True
        self._emit_all_data_changed()

    @property
    def columns(self) -> tuple[str, ...]:
        return self._column_names

    def set_language(self, language: str) -> None:
        selected = "en" if str(language).lower().startswith("en") else "zh"
        if selected == self.language:
            return
        self.language = selected
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self.columnCount() - 1)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows) + len(self._inserts)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._column_names) + 1

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._column_names):
                return self._column_names[section]
            if section == len(self._column_names):
                return "变更" if self.language == "zh" else "Pending"
            return None
        return section + 1 if 0 <= section < self.rowCount() else None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < self.rowCount()):
            return None
        row = index.row()
        inserted = row >= len(self._rows)
        if index.column() == len(self._column_names):
            status = self._row_status(row)
            if role == Qt.ItemDataRole.DisplayRole:
                return status
            if role == Qt.ItemDataRole.ToolTipRole:
                return status
            if role == Qt.ItemDataRole.ForegroundRole and status:
                return QColor("#8b5e1a" if "insert" in status.casefold() or "新增" in status else "#9a4b4b")
            return None

        name = self._column_names[index.column()]
        if inserted:
            values = self._inserts[row - len(self._rows)]
            value = values.get(name)
        else:
            value = self._value_for_base(row, name)
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            return _display_value(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            column = self.metadata.columns[index.column()]
            return f"{column.type_name} · {_full_value(value)}"
        if role == Qt.ItemDataRole.ForegroundRole and not inserted and self._row_keys[row] in self._deletes:
            return QColor("#8a8a8a")
        if role == Qt.ItemDataRole.BackgroundRole and not inserted and self._row_keys[row] in self._updates:
            return QColor("#fff6dd")
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() >= len(self._column_names) or self._change_set is None:
            return flags
        name = self._column_names[index.column()]
        column = self.metadata.columns[index.column()]
        row = index.row()
        has_valid_key = row >= len(self._rows) or self._row_keys[row] is not None
        if self.can_edit and column.editable and column.generated is not True and (
            row >= len(self._rows)
            or row < len(self._rows) and name not in self.metadata.primary_key
        ) and has_valid_key:
            if row >= len(self._rows) or self._row_keys[row] not in self._deletes:
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not self.can_edit:
            return False
        if index.column() < 0 or index.column() >= len(self._column_names):
            return False
        row = index.row()
        name = self._column_names[index.column()]
        column = self.metadata.columns[index.column()]
        inserted = row >= len(self._rows)
        if not column.editable or column.generated is True or (name in self.metadata.primary_key and not inserted):
            return False
        try:
            if inserted:
                insert_index = row - len(self._rows)
                if not (0 <= insert_index < len(self._inserts)):
                    return False
                old_values = self._inserts[insert_index]
                old_value = old_values.get(name)
                typed = _coerce_value(value, column.type_name, old_value)
                if typed is None and column.required_on_insert:
                    return False
                updated_values = dict(old_values)
                updated_values[name] = typed
                inserts = list(self._inserts)
                inserts[insert_index] = updated_values
                change_set = self._build_change_set(self._updates, self._deletes, inserts)
                self._inserts = inserts
                self._change_set = change_set
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
                self.dataChanged.emit(self.index(row, len(self._column_names)), self.index(row, len(self._column_names)), [Qt.ItemDataRole.DisplayRole])
                return True
            key = self._row_keys[row]
            if key is None or key in self._deletes:
                return False
            typed = _coerce_value(value, column.type_name, self._rows[row][index.column()])
            if typed is None and not column.nullable:
                return False
            before = self._updates.get(key, {}).copy()
            candidate = dict(before)
            candidate[name] = typed
            original = self._originals[row][name]
            if _same_value(typed, original):
                candidate.pop(name, None)
            updates = dict(self._updates)
            if candidate:
                updates[key] = candidate
            else:
                updates.pop(key, None)
            change_set = self._build_change_set(updates, self._deletes, self._inserts)
        except (ValueError, InvalidOperation, ChangeSetError, TypeError):
            return False
        self._updates = updates
        self._change_set = change_set
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole, Qt.ItemDataRole.BackgroundRole])
        self.dataChanged.emit(self.index(row, len(self._column_names)), self.index(row, len(self._column_names)), [Qt.ItemDataRole.DisplayRole])
        return True

    def replace_page(self, page: ManualQueryPage) -> None:
        self.beginResetModel()
        self._page_stale = False
        self._editing_enabled = True
        self._rows = [tuple(row) for row in page.rows]
        self._originals = [self._row_mapping(row) for row in self._rows]
        self._row_keys = [self._make_key(values) for values in self._originals]
        self._updates.clear()
        self._deletes.clear()
        self._inserts.clear()
        self._rebuild_change_set()
        self.endResetModel()

    def append_page(self, page: ManualQueryPage) -> None:
        start = len(self._rows) + len(self._inserts)
        rows = [tuple(row) for row in page.rows]
        if rows:
            self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
            for row in rows:
                values = self._row_mapping(row)
                self._rows.append(row)
                self._originals.append(values)
                self._row_keys.append(self._make_key(values))
            self.endInsertRows()

    def stage_delete(self, row: int) -> bool:
        if not self.can_edit or row < 0 or row >= self.rowCount():
            return False
        if row >= len(self._rows):
            self.beginResetModel()
            self._inserts.pop(row - len(self._rows))
            self._rebuild_change_set()
            self.endResetModel()
            return True
        key = self._row_keys[row]
        if key is None:
            return False
        if key in self._deletes:
            return True
        updates = dict(self._updates)
        updates.pop(key, None)
        deletes = set(self._deletes)
        deletes.add(key)
        try:
            change_set = self._build_change_set(updates, deletes, self._inserts)
        except (ChangeSetError, TypeError, ValueError):
            return False
        self._updates = updates
        self._deletes = deletes
        self._change_set = change_set
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1), [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole])
        return True

    def stage_insert(self, values: Mapping[str, Any]) -> bool:
        if not self.can_insert:
            return False
        try:
            normalized = dict(values)
            columns = {column.name: column for column in self.metadata.columns}
            if any(
                value is None and columns[name].required_on_insert
                for name, value in normalized.items()
                if name in columns
            ):
                return False
            inserts = [*self._inserts, normalized]
            change_set = self._build_change_set(self._updates, self._deletes, inserts)
        except (ChangeSetError, ValueError, TypeError):
            return False
        row = self.rowCount()
        self.beginInsertRows(QModelIndex(), row, row)
        self._inserts = inserts
        self._change_set = change_set
        self.endInsertRows()
        return True

    def set_sql_null(self, row: int, column: int) -> bool:
        return self.setData(self.index(row, column), None, Qt.ItemDataRole.EditRole)

    def value_at(self, row: int, column: int) -> Any:
        if not (0 <= row < self.rowCount() and 0 <= column < len(self._column_names)):
            return None
        return self.data(self.index(row, column), Qt.ItemDataRole.UserRole)

    def clear_staged_after_apply(self) -> None:
        self.beginResetModel()
        self._updates.clear()
        self._deletes.clear()
        self._inserts.clear()
        self._rebuild_change_set()
        self.endResetModel()

    def discard_staged(self) -> None:
        self.clear_staged_after_apply()

    def _emit_all_data_changed(self) -> None:
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole],
            )

    def _row_mapping(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {name: row[index] if index < len(row) else None for index, name in enumerate(self._column_names)}

    def _make_key(self, values: Mapping[str, Any]) -> tuple[Any, ...] | None:
        key = tuple(values.get(name) for name in self.metadata.primary_key)
        if not key or any(value is None for value in key):
            return None
        try:
            hash(key)
        except TypeError:
            return None
        return key

    def _value_for_base(self, row: int, name: str) -> Any:
        original = self._originals[row].get(name)
        key = self._row_keys[row]
        if key is None:
            return original
        return self._updates.get(key, {}).get(name, original)

    def _row_status(self, row: int) -> str:
        inserted = row >= len(self._rows)
        if inserted:
            return "新增" if self.language == "zh" else "insert"
        key = self._row_keys[row]
        if key in self._deletes:
            return "待删除" if self.language == "zh" else "delete"
        if key in self._updates:
            return "待更新" if self.language == "zh" else "update"
        return ""

    def _rebuild_change_set(self) -> None:
        self._change_set = self._build_change_set(self._updates, self._deletes, self._inserts)

    def _build_change_set(
        self,
        updates: Mapping[tuple[Any, ...], Mapping[str, Any]],
        deletes: set[tuple[Any, ...]],
        inserts: list[Mapping[str, Any]],
    ) -> ChangeSet | None:
        if self.metadata.verified_changeset is None:
            return None
        change_set = ChangeSet(
            self.metadata.verified_changeset,
            create_changeset_dialect(self.choice, self.metadata.target),
        )
        for index, key in enumerate(self._row_keys):
            if key is None:
                continue
            primary_key = {name: value for name, value in zip(self.metadata.primary_key, key)}
            originals = self._originals[index]
            compared = {
                name: originals[name]
                for name in self.metadata.verified_changeset.concurrency_columns or ()
                if name in originals
            }
            if key in deletes:
                change_set.stage_delete(primary_key, compared)
            elif key in updates:
                change_set.stage_update(primary_key, compared, updates[key])
        for values in inserts:
            change_set.stage_insert(values)
        return change_set


class TableEditorWidget(QWidget):
    """One shared presentation widget, rebound to each tab's own model."""

    refresh_requested = pyqtSignal()
    next_page_requested = pyqtSignal()
    apply_requested = pyqtSignal(object)
    import_requested = pyqtSignal()
    staged_changed = pyqtSignal(bool)
    sql_view_requested = pyqtSignal(bool)
    server_query_requested = pyqtSignal()

    def __init__(self, language: str = "zh", parent=None) -> None:
        super().__init__(parent)
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self.model: TableEditorModel | None = None
        self._signals_model: TableEditorModel | None = None
        self._has_more = False
        self._busy = False
        self._shutdown_gated = False
        self._import_available = False
        self._last_pending = False
        self._sql_pane_visible = True
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self._build_ui()

    def _build_ui(self) -> None:
        zh = self.language == "zh"
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(6)
        controls = QGridLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(4)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索已加载行" if zh else "Search loaded rows")
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.page_size = QComboBox(self)
        for size in PAGE_SIZES:
            self.page_size.addItem(str(size), size)
        self.page_size.setCurrentIndex(1)
        self.refresh_button = QPushButton("刷新" if zh else "Refresh", self)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.next_button = QPushButton("下一页" if zh else "Next page", self)
        self.next_button.clicked.connect(self.next_page_requested.emit)
        self.sql_view_button = QPushButton(self)
        self.sql_view_button.setObjectName("manual-table-sql-view-toggle")
        self.sql_view_button.setAccessibleName("Show SQL editor or focus table data")
        self.sql_view_button.clicked.connect(self._toggle_sql_view)
        self.server_query_button = QPushButton(self)
        self.server_query_button.setObjectName("manual-table-server-query")
        self.server_query_button.clicked.connect(self.server_query_requested.emit)
        self.set_server_query_counts(0, 0)
        self.control_buttons = (self.sql_view_button, self.refresh_button, self.next_button, self.server_query_button)
        for column in range(3):
            controls.setColumnStretch(column, 1)
        controls.addWidget(self.search, 0, 0)
        controls.addWidget(self.server_query_button, 0, 1)
        controls.addWidget(self.sql_view_button, 0, 2)
        controls.addWidget(self.page_size, 1, 0)
        controls.addWidget(self.refresh_button, 1, 1)
        controls.addWidget(self.next_button, 1, 2)
        self.controls_layout = controls
        root.addLayout(controls)

        self.table = ResultTableView(self, language=self.language)
        self.table.setObjectName("manual-table-editor-view")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._inspect_value)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self.insert_button = QPushButton("新增行" if zh else "Insert", self)
        self.insert_button.clicked.connect(self._insert_row)
        self.delete_button = QPushButton("删除所选" if zh else "Delete", self)
        self.delete_button.setToolTip("删除所选行" if zh else "Delete selected rows")
        self.delete_button.clicked.connect(self._delete_selected)
        self.preview_button = QPushButton("预览并应用…" if zh else "Preview…", self)
        self.preview_button.clicked.connect(self._preview_apply)
        self.discard_button = QPushButton("放弃本地变更" if zh else "Discard", self)
        self.discard_button.clicked.connect(self._discard)
        self.import_button = QPushButton("导入文件…" if zh else "Import file…", self)
        self.import_button.setObjectName("manual-table-import-button")
        self.import_button.clicked.connect(self.import_requested.emit)
        self.action_buttons = (
            self.insert_button,
            self.delete_button,
            self.preview_button,
            self.discard_button,
            self.import_button,
        )
        for button in self.action_buttons:
            actions.addWidget(button, 1)
        self._compact_action_labels = False
        self._refresh_action_labels()
        root.addLayout(actions)

        self.import_note = QLabel(self)
        self.import_note.setObjectName("manual-table-import-note")
        self.import_note.setWordWrap(True)
        self.import_note.hide()
        root.addWidget(self.import_note)

        self.status = QLabel(self)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.read_only_note = QLabel(self)
        self.read_only_note.setWordWrap(True)
        root.addWidget(self.read_only_note)
        self.set_sql_pane_visible(True)
        self._sync_button_minimum_heights()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.StyleChange,
        }:
            self._sync_button_minimum_heights()

    def _sync_button_minimum_heights(self) -> None:
        """Keep visible controls at the current font/style minimum height."""
        buttons = (*getattr(self, "control_buttons", ()), *getattr(self, "action_buttons", ()))
        for button in buttons:
            height = button.minimumSizeHint().height()
            if button.minimumHeight() != height:
                button.setMinimumHeight(height)

    def bind_model(self, model: TableEditorModel | None, *, page_size: int = 200, has_more: bool = False, busy: bool = False) -> None:
        previous_model = self._signals_model
        if previous_model is not model:
            if previous_model is not None:
                for signal in (previous_model.dataChanged, previous_model.rowsInserted, previous_model.modelReset):
                    try:
                        signal.disconnect(self._update_status)
                    except (TypeError, RuntimeError):
                        pass
            if model is not None:
                model.dataChanged.connect(self._update_status)
                model.rowsInserted.connect(self._update_status)
                model.modelReset.connect(self._update_status)
            self._signals_model = model
        self.model = model
        self._has_more = bool(has_more)
        self._busy = bool(busy)
        if model is not None:
            model.set_editing_enabled(not busy and not self._shutdown_gated)
        self.proxy.setSourceModel(model)
        self.table.setModel(self.proxy)
        if previous_model is not model:
            self.search.clear()
        zh = self.language == "zh"
        if model is None:
            self.read_only_note.setText("")
            self.status.setText("")
            self._sync_controls(has_more=False, busy=True)
            return
        self._update_read_only_note(model)
        selected = self.page_size.findData(page_size)
        self.page_size.setCurrentIndex(selected if selected >= 0 else 1)
        self._sync_controls(has_more=self._has_more, busy=self._busy)
        self._update_status()

    def _update_read_only_note(self, model: TableEditorModel) -> None:
        zh = self.language == "zh"
        editable = model.change_set is not None
        reason = model.metadata.read_only_reason
        if model.page_stale:
            self.read_only_note.setText(
                "变更已应用；请先显式刷新，再继续编辑。" if zh else
                "Changes were applied; refresh explicitly before editing again."
            )
        elif not editable:
            self.read_only_note.setText(
                ("可浏览；当前对象只读。" if zh else "Browse only; this object is read-only.")
                + (f" {reason}" if reason else "")
            )
        else:
            self.read_only_note.setText("")

    def set_language(self, language: str) -> None:
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self.table.set_language(self.language)
        zh = self.language == "zh"
        self.search.setPlaceholderText("搜索已加载行" if zh else "Search loaded rows")
        self.refresh_button.setText("刷新" if zh else "Refresh")
        self.next_button.setText("下一页" if zh else "Next page")
        self.set_server_query_counts(*self._server_query_counts)
        self._refresh_action_labels()
        self._sync_button_minimum_heights()
        if self.model is not None:
            self.model.set_language(self.language)
            self._update_status()
        self.set_sql_pane_visible(self._sql_pane_visible)

    def set_compact_action_labels(self, compact: bool) -> None:
        self._compact_action_labels = bool(compact)
        self._refresh_action_labels()
        self._sync_button_minimum_heights()

    def _refresh_action_labels(self) -> None:
        zh = self.language == "zh"
        full = (
            ("新增行", "删除所选", "预览并应用…", "放弃本地变更", "导入文件…")
            if zh else
            ("Insert", "Delete", "Preview…", "Discard", "Import file…")
        )
        compact = (
            ("新增", "删除", "预览", "放弃", "导入")
            if zh else
            ("Insert", "Delete", "Preview", "Discard", "Import")
        )
        descriptions = (
            ("新增行", "删除所选行", "预览并应用本地变更", "放弃本地变更", "从文件导入到所选表")
            if zh else
            ("Insert a row", "Delete selected rows", "Preview and apply staged changes", "Discard staged changes", "Import a file into the selected table")
        )
        for button, label, description in zip(
            self.action_buttons,
            compact if getattr(self, "_compact_action_labels", False) else full,
            descriptions,
        ):
            button.setText(label)
            button.setAccessibleName(description)
            button.setToolTip(description)

    def set_sql_pane_visible(self, visible: bool) -> None:
        self._sql_pane_visible = bool(visible)
        zh = self.language == "zh"
        self.sql_view_button.setText(
            ("仅表格" if zh else "Table only") if self._sql_pane_visible else
            ("显示 SQL" if zh else "Show SQL")
        )
        self.sql_view_button.setToolTip(
            ("切换为仅表格视图；SQL 和本地变更保持不变" if zh else "Focus table data; SQL and staged changes are preserved")
            if self._sql_pane_visible else
            ("显示 SQL 编辑器；不会运行查询或提交事务" if zh else "Show the SQL editor; no query or commit will run")
        )

    def set_server_query_counts(self, filters: int, orders: int) -> None:
        self._server_query_counts = (filters, orders)
        label = "筛选与排序" if self.language == "zh" else "Filter / sort"
        if filters or orders:
            label += f" ({filters}/{orders})"
        self.server_query_button.setText(label)
        self.server_query_button.setToolTip(
            f"服务端查询：{filters} 个筛选条件，{orders} 个排序字段；重新读取当前表" if self.language == "zh" else
            f"Server query: {filters} filters, {orders} sort keys; reload the current table"
        )

    def _toggle_sql_view(self) -> None:
        self.sql_view_requested.emit(not self._sql_pane_visible)

    def _sync_controls(self, *, has_more: bool, busy: bool) -> None:
        model = self.model
        editable = bool(model and model.can_edit)
        pending = bool(model and model.has_staged_changes)
        operation_busy = bool(self._shutdown_gated or busy or (model is not None and not model.editing_enabled))
        self.page_size.setEnabled(bool(model and not operation_busy and not pending and not model.page_stale))
        self.refresh_button.setEnabled(bool(model and not operation_busy and not pending))
        self.server_query_button.setEnabled(bool(model and not operation_busy and not pending))
        self.next_button.setEnabled(bool(model and has_more and not operation_busy and not pending and not model.page_stale))
        self.insert_button.setEnabled(bool(model and model.can_insert and not operation_busy))
        self.delete_button.setEnabled(bool(editable and not operation_busy and self.table.selectionModel() is not None))
        self.preview_button.setEnabled(bool(editable and pending and not operation_busy))
        self.discard_button.setEnabled(bool(editable and pending and not operation_busy))
        self.import_button.setEnabled(bool(model is not None and self._import_available and not operation_busy))

    def set_import_availability(self, available: bool, reason: str = "") -> None:
        self._import_available = bool(available)
        self.import_note.setText(str(reason or ""))
        self.import_note.setVisible(bool(reason))
        self._sync_controls(has_more=self._has_more, busy=self._busy)

    def set_shutdown_gated(self, enabled: bool) -> None:
        self._shutdown_gated = bool(enabled)
        if self.model is not None:
            self.model.set_editing_enabled(not self._busy and not self._shutdown_gated)
        self._sync_controls(has_more=self._has_more, busy=self._busy)

    def set_operation_state(self, *, has_more: bool, busy: bool) -> None:
        self._has_more = bool(has_more)
        self._busy = bool(busy)
        if self.model is not None:
            self.model.set_editing_enabled(not busy and not self._shutdown_gated)
        self._sync_controls(has_more=has_more, busy=busy)
        self._update_status()

    def _update_status(self) -> None:
        if self.model is None:
            self.status.setText("")
            if self._last_pending:
                self._last_pending = False
                self.staged_changed.emit(False)
            return
        self._update_read_only_note(self.model)
        visible = self.proxy.rowCount()
        total = self.model.rowCount()
        pending = self.model.staged_count
        has_pending = self.model.has_staged_changes
        if has_pending != self._last_pending:
            self._last_pending = has_pending
            self.staged_changed.emit(has_pending)
        self.status.setText(
            f"{visible}/{total} 行已加载 · {pending} 项待应用" if self.language == "zh" else
            f"{visible}/{total} loaded rows · {pending} pending changes"
        )
        self._sync_controls(has_more=self._has_more, busy=self._busy)

    def _selected_source_rows(self) -> list[int]:
        selection = self.table.selectionModel()
        if selection is None:
            return []
        return sorted({self.proxy.mapToSource(index).row() for index in selection.selectedRows()})

    def _delete_selected(self) -> None:
        if self.model is None or self._shutdown_gated:
            return
        rows = self._selected_source_rows()
        for row in reversed(rows):
            self.model.stage_delete(row)
        self._update_status()

    def _insert_row(self) -> None:
        if self.model is None or self._shutdown_gated:
            return
        dialog = _InsertRowDialog(self.model.metadata, self.language, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.values()
        except (ValueError, InvalidOperation, TypeError):
            QMessageBox.warning(
                self,
                "新增行" if self.language == "zh" else "Insert row",
                "有字段值格式无效。" if self.language == "zh" else "One or more values have an invalid format.",
            )
            return
        if not self.model.stage_insert(values):
            QMessageBox.warning(
                self,
                "新增行" if self.language == "zh" else "Insert row",
                "字段不满足已验证的主键或必填规则。" if self.language == "zh" else
                "The values do not satisfy verified key or required-column rules.",
            )
            return
        self._update_status()

    def _preview_apply(self) -> None:
        if self._shutdown_gated or self.model is None or self.model.change_set is None or not self.model.has_staged_changes:
            return
        preview = self.model.change_set.preview()
        dialog = ChangePreviewDialog(preview, self.language, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_requested.emit(preview)

    def _discard(self) -> None:
        if self._shutdown_gated or self.model is None or not self.model.has_staged_changes:
            return
        zh = self.language == "zh"
        answer = QMessageBox.question(
            self,
            "放弃本地变更" if zh else "Discard local changes",
            "放弃所有尚未应用的本地表格变更？" if zh else "Discard all local table changes that have not been applied?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.model.discard_staged()
            self._update_status()

    def _context_menu(self, point) -> None:
        if self.model is None:
            return
        proxy_index = self.table.indexAt(point)
        if not proxy_index.isValid():
            return
        source_index = self.proxy.mapToSource(proxy_index)
        menu = QMenu(self)
        if source_index.column() < len(self.model.columns) and self.model.flags(source_index) & Qt.ItemFlag.ItemIsEditable:
            action = menu.addAction("设为 SQL NULL" if self.language == "zh" else "Set SQL NULL")
            action.triggered.connect(lambda: self.model and self.model.set_sql_null(source_index.row(), source_index.column()))
        if menu.actions():
            menu.exec(self.table.viewport().mapToGlobal(point))

    def _inspect_value(self, proxy_index: QModelIndex) -> None:
        if self.model is None or not proxy_index.isValid():
            return
        index = self.proxy.mapToSource(proxy_index)
        if index.column() >= len(self.model.columns):
            return
        value = self.model.value_at(index.row(), index.column())
        _preview, total = value_preview(value, 301)
        if total <= 300 and is_loaded_value(value):
            return
        inspect_value(self.model.columns[index.column()], value, language=self.language, parent=self)


class _InsertRowDialog(QDialog):
    def __init__(self, metadata: TableSourceMetadata, language: str, parent=None) -> None:
        super().__init__(parent)
        self.metadata = metadata
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        zh = self.language == "zh"
        self.setWindowTitle("新增行" if zh else "Insert row")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._fields: dict[str, tuple[QLineEdit, QCheckBox, QCheckBox | None]] = {}
        for column in metadata.columns:
            if column.generated is True or not column.editable:
                continue
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(row)
            null_box = QCheckBox("SQL NULL" if not zh else "SQL NULL", row)
            null_box.toggled.connect(lambda checked, target=edit: target.setEnabled(not checked))
            omit_box = None
            if column.has_default is True and column.name not in metadata.primary_key:
                omit_box = QCheckBox("默认" if zh else "Default", row)
                omit_box.setChecked(True)
                omit_box.toggled.connect(lambda checked, target=edit, null=null_box: (target.setEnabled(not checked and not null.isChecked()), null.setEnabled(not checked)))
                row_layout.addWidget(omit_box)
            row_layout.addWidget(null_box)
            row_layout.addWidget(edit, 1)
            form.addRow(column.name, row)
            self._fields[column.name] = (edit, null_box, omit_box)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(540, min(620, 150 + len(self._fields) * 38))

    def values(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        by_name = {column.name: column for column in self.metadata.columns}
        for name, (edit, null_box, omit_box) in self._fields.items():
            if omit_box is not None and omit_box.isChecked():
                continue
            column = by_name[name]
            if null_box.isChecked():
                if not column.nullable:
                    raise ValueError("required column cannot be NULL")
                output[name] = None
                continue
            output[name] = _coerce_value(edit.text(), column.type_name, None)
        return output


class ChangePreviewDialog(QDialog):
    """Require an explicit review and confirmation before applying a batch."""

    def __init__(self, preview: ChangePreview, language: str = "zh", parent=None) -> None:
        super().__init__(parent)
        self.preview = preview
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        zh = self.language == "zh"
        self.setWindowTitle("变更预览" if zh else "Change preview")
        layout = QVBoxLayout(self)
        target = preview.target
        identity = f"{target.connection_id} · {target.database}"
        if target.schema:
            identity += f" · {target.schema}"
        layout.addWidget(QLabel(("目标：" if zh else "Target: ") + identity, self))
        text = QPlainTextEdit(self)
        text.setReadOnly(True)
        blocks = []
        for number, statement in enumerate(preview.statements, start=1):
            params = _safe_repr(statement.parameters)
            blocks.append(
                f"{number}. {statement.operation}\n{statement.sql}\n"
                f"{('绑定参数' if zh else 'Bound parameters')}: {params}\n"
                f"{('预期行数' if zh else 'Expected rows')}: {statement.expected_rowcount}"
            )
        text.setPlainText("\n\n".join(blocks))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(self)
        self.confirm_button = buttons.addButton("应用到事务" if zh else "Apply to transaction", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = buttons.addButton("取消" if zh else "Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.confirm_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(760, 520)


def _coerce_value(value: Any, type_name: str, original: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value
    if isinstance(original, bool):
        folded = text.strip().casefold()
        if folded in {"true", "1", "yes"}:
            return True
        if folded in {"false", "0", "no"}:
            return False
        raise ValueError("invalid boolean")
    if isinstance(original, int) and not isinstance(original, bool):
        return int(text)
    if isinstance(original, Decimal):
        return Decimal(text)
    if isinstance(original, float):
        return float(text)
    if isinstance(original, datetime):
        return datetime.fromisoformat(text)
    if isinstance(original, date):
        return date.fromisoformat(text)
    if isinstance(original, time):
        return time.fromisoformat(text)
    normalized = str(type_name or "").casefold()
    if any(token in normalized for token in ("decimal", "numeric", "number")):
        return Decimal(text)
    if any(token in normalized for token in ("tinyint", "smallint", "mediumint", "integer", "bigint", "int")) and not any(token in normalized for token in ("point", "interval")):
        return int(text)
    if any(token in normalized for token in ("float", "double", "real", "binary_float", "binary_double")):
        return float(text)
    if normalized in {"bool", "boolean"}:
        folded = text.strip().casefold()
        if folded in {"true", "1", "yes"}:
            return True
        if folded in {"false", "0", "no"}:
            return False
        raise ValueError("invalid boolean")
    return text


def _same_value(left: Any, right: Any) -> bool:
    try:
        result = left == right
        return result if isinstance(result, bool) else bool(result)
    except Exception:
        return False


def _full_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if not is_loaded_value(value):
        return f"<{type(value).__name__}>"
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return str(value)


def _display_value(value: Any) -> str:
    text = _full_value(value)
    return text if len(text) <= 512 else text[:512] + "…"


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return "<value>"


__all__ = ["ChangePreviewDialog", "TableEditorModel", "TableEditorWidget"]
