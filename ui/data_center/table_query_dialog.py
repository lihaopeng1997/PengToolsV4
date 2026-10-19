"""Structured server-side table filters and ordering; no DB I/O in this dialog."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTabWidget,
    QVBoxLayout, QWidget,
)

from tools.data_center.table_source import (
    FILTER_OPERATORS, MAX_TABLE_FILTERS, MAX_TABLE_ORDERS, TableFilter,
    TableOrder, TableQuerySpec, TableSourceError, TableSourceMetadata,
    validate_table_query,
)


class TableQueryDialog(QDialog):
    def __init__(self, metadata: TableSourceMetadata, query: TableQuerySpec, language="zh", parent=None):
        super().__init__(parent)
        self.metadata = metadata
        self.zh = language == "zh"
        self.setWindowTitle(self.text("服务端筛选与排序", "Server filters and sorting"))
        root = QVBoxLayout(self)
        note = QLabel(self.text(
            "查询当前表的全部数据，按所选条件返回分页结果。多个条件需同时满足；排序按行顺序生效。",
            "Query the whole table and return matching pages. All conditions must match; sort rows define priority.",
        ), self)
        note.setWordWrap(True)
        root.addWidget(note)
        tabs = QTabWidget(self)
        self.filters = self._section(tabs, self.text("筛选条件", "Filters"), [self.text("字段", "Column"), self.text("条件", "Operator"), self.text("值", "Value"), ""], MAX_TABLE_FILTERS)
        self.orders = self._section(tabs, self.text("排序", "Sort"), [self.text("字段", "Column"), self.text("方向", "Direction"), ""], MAX_TABLE_ORDERS)
        root.addWidget(tabs, 1)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        buttons = QDialogButtonBox(self)
        clear = buttons.addButton(self.text("清空条件与排序", "Clear filters and sort"), QDialogButtonBox.ButtonRole.ResetRole)
        clear.setAutoDefault(False)
        clear.clicked.connect(self._clear)
        self.apply_button = buttons.addButton(self.text("查询", "Query"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._accept_query)
        cancel = buttons.addButton(self.text("取消", "Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        root.addWidget(buttons)
        for condition in query.filters:
            self.add_filter(condition)
        for order in query.orders:
            self.add_order(order)
        screen = self.screen().availableGeometry()
        self.resize(min(700, screen.width() - 60), min(440, screen.height() - 80))

    def text(self, zh, en):
        return zh if self.zh else en

    def _section(self, tabs, title, headers, maximum):
        page = QWidget(tabs)
        layout = QVBoxLayout(page)
        table = QTableWidget(0, len(headers), page)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(table, 1)
        add = QPushButton(self.text("添加条件" if maximum == MAX_TABLE_FILTERS else "添加排序", "Add filter" if maximum == MAX_TABLE_FILTERS else "Add sort"), page)
        add.setAutoDefault(False)
        add.clicked.connect(lambda: self.add_filter() if table is self.filters else self.add_order())
        layout.addWidget(add)
        tabs.addTab(page, title)
        return table

    def _column_control(self, table, current="", comparable_only=False):
        combo = QComboBox(table)
        for column in self.metadata.columns:
            if not comparable_only or column.comparable:
                combo.addItem(column.name, column.name)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _remove_control(self, table):
        button = QPushButton(self.text("移除", "Remove"), table)
        button.setAutoDefault(False)
        def remove():
            for row in range(table.rowCount()):
                if table.cellWidget(row, table.columnCount() - 1) is button:
                    table.removeRow(row)
                    break
        button.clicked.connect(remove)
        return button

    def add_filter(self, condition=None):
        if self.filters.rowCount() >= MAX_TABLE_FILTERS:
            return
        condition = condition or TableFilter("", "=", "")
        row = self.filters.rowCount()
        self.filters.insertRow(row)
        column = self._column_control(self.filters, condition.column)
        operator = QComboBox(self.filters)
        labels = {"contains": self.text("包含文本", "Contains text"), "starts_with": self.text("文本开头", "Starts with")}
        for value in FILTER_OPERATORS:
            operator.addItem(labels.get(value, value), value)
        operator.setCurrentIndex(operator.findData(condition.operator))
        value = QLineEdit(self.filters)
        value.setMaxLength(16384)
        value.setText("" if condition.value is None else str(condition.value))
        value.setPlaceholderText(self.text("日期使用 ISO 格式", "Dates use ISO format"))
        operator.currentIndexChanged.connect(lambda: value.setEnabled(operator.currentData() not in {"IS NULL", "IS NOT NULL"}))
        value.setEnabled(condition.operator not in {"IS NULL", "IS NOT NULL"})
        for index, widget in enumerate((column, operator, value, self._remove_control(self.filters))):
            self.filters.setCellWidget(row, index, widget)
        self.filters.setRowHeight(row, max(36, value.sizeHint().height() + 4))

    def add_order(self, order=None):
        if self.orders.rowCount() >= MAX_TABLE_ORDERS:
            return
        order = order or TableOrder("")
        row = self.orders.rowCount()
        self.orders.insertRow(row)
        direction = QComboBox(self.orders)
        direction.addItem(self.text("升序", "Ascending"), False)
        direction.addItem(self.text("降序", "Descending"), True)
        direction.setCurrentIndex(int(order.descending))
        for index, widget in enumerate((self._column_control(self.orders, order.column, True), direction, self._remove_control(self.orders))):
            self.orders.setCellWidget(row, index, widget)
        self.orders.setRowHeight(row, max(36, direction.sizeHint().height() + 4))

    def _clear(self):
        self.filters.setRowCount(0)
        self.orders.setRowCount(0)
        self.error.clear()

    def query(self):
        columns = {column.name: column for column in self.metadata.columns}
        conditions = []
        for row in range(self.filters.rowCount()):
            name = self.filters.cellWidget(row, 0).currentData()
            operator = self.filters.cellWidget(row, 1).currentData()
            text = self.filters.cellWidget(row, 2).text()
            column = columns.get(name)
            if column is None:
                raise ValueError("column")
            value = None if operator in {"IS NULL", "IS NOT NULL"} else _typed_value(text, column.type_name, operator)
            if value is not None and not column.comparable:
                raise ValueError("unsupported")
            conditions.append(TableFilter(name, operator, value))
        orders = tuple(TableOrder(self.orders.cellWidget(row, 0).currentData(), self.orders.cellWidget(row, 1).currentData()) for row in range(self.orders.rowCount()))
        return validate_table_query(TableQuerySpec(tuple(conditions), orders))

    def _accept_query(self):
        try:
            self.query()
        except (ValueError, InvalidOperation, TableSourceError, TypeError):
            self.error.setText(self.text("请检查字段、值格式及重复排序；大对象仅支持 NULL 条件。", "Check columns, value formats and duplicate sort keys. LOB values support NULL conditions only."))
            return
        self.accept()


def _typed_value(text, type_name, operator):
    if operator in {"contains", "starts_with"}:
        return text
    kind = type_name.casefold().split("(")[0].strip()
    if kind in {"number", "numeric", "decimal", "int", "integer", "bigint", "smallint", "tinyint", "mediumint", "float", "double", "real", "binary_float", "binary_double"}:
        return Decimal(text.strip())
    if kind == "date":
        return date.fromisoformat(text.strip())
    if kind.startswith("timestamp") or kind == "datetime":
        return datetime.fromisoformat(text.strip())
    return text
