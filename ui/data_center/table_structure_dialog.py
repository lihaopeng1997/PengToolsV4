"""Read-only table structure view built from verified catalog metadata."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ui.table_utils import autofit_columns

_HEADERS_ZH = ("序号", "列名", "类型", "可空", "主键", "注释")
_HEADERS_EN = ("#", "Column", "Type", "Nullable", "PK", "Comment")


def _text(value):
    return "" if value is None else str(value)


def structure_rows(metadata):
    """Project one verified table into display rows. Pure, for tests."""
    keys = {str(name) for name in getattr(metadata, "primary_key", ()) or ()}
    rows = []
    for index, column in enumerate(getattr(metadata, "columns", ()) or (), start=1):
        rows.append(
            (
                str(index),
                _text(getattr(column, "name", "")),
                _text(getattr(column, "type_name", "")),
                "" if getattr(column, "nullable", None) is None else (
                    "Y" if getattr(column, "nullable") else "N"
                ),
                "PK" if getattr(column, "name", "") in keys else "",
                _text(getattr(column, "comment", "")),
            )
        )
    return rows


class TableStructureDialog(QDialog):
    """Columns, types, nullability, primary key and comments. Never editable."""

    def __init__(self, metadata, *, language="zh", parent=None):
        super().__init__(parent)
        zh = not str(language).lower().startswith("en")
        self.language = "zh" if zh else "en"
        self.metadata = metadata
        self.rows = structure_rows(metadata)
        ref = getattr(metadata, "signed_ref", None)
        schema = _text(getattr(ref, "schema", "")) or _text(getattr(ref, "database", ""))
        name = _text(getattr(ref, "raw_name", "")) or _text(getattr(ref, "name", ""))
        qualified = (schema + "." + name) if schema else name
        self.setWindowTitle((("表结构 · " if zh else "Table structure · ") + qualified).strip())
        self.resize(900, 580)

        layout = QVBoxLayout(self)
        heading = QLabel(qualified or ("未命名对象" if zh else "Unnamed object"))
        heading.setObjectName("table-structure-heading")
        layout.addWidget(heading)

        keys = getattr(metadata, "primary_key", ()) or ()
        summary = (f"共 {len(self.rows)} 列 · 主键：{', '.join(keys) if keys else '无'}"
                   if zh else
                   f"{len(self.rows)} columns · primary key: {', '.join(keys) if keys else 'none'}")
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        reason = _text(getattr(metadata, "read_only_reason", ""))
        if reason:
            note = QLabel(("只读原因：" if zh else "Read-only: ") + reason)
            note.setWordWrap(True)
            layout.addWidget(note)

        table = QTableWidget(len(self.rows), 6, self)
        table.setObjectName("table-structure-grid")
        table.setHorizontalHeaderLabels(_HEADERS_ZH if zh else _HEADERS_EN)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        for row_index, values in enumerate(self.rows):
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if value:
                    item.setToolTip(value)
                if column_index == 4 and value:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row_index, column_index, item)
        autofit_columns(table)
        layout.addWidget(table, 1)
        self.table = table

        buttons = QDialogButtonBox(self)
        copy_button = QPushButton("复制结构" if zh else "Copy", self)
        copy_button.clicked.connect(self.copy_to_clipboard)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def as_text(self):
        header = "\t".join(_HEADERS_ZH if self.language == "zh" else _HEADERS_EN)
        body = "\n".join("\t".join(row) for row in self.rows)
        return header + "\n" + body

    def copy_to_clipboard(self):
        QApplication.clipboard().setText(self.as_text())
