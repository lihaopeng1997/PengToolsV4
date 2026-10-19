"""Bounded, read-only inspection of a typed result value, without database I/O."""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout
from ui.data_center.value_format import MAX_PREVIEW_CHARACTERS, is_loaded_value, value_preview


class ValueInspectorDialog(QDialog):
    def __init__(self, column: str, value: Any, *, language: str = "zh", parent=None) -> None:
        super().__init__(parent)
        zh = language == "zh"
        self.setWindowTitle("字段值详情" if zh else "Cell value details")
        self.setObjectName("result-value-inspector")
        layout = QVBoxLayout(self)
        label = QLabel(str(column), self)
        label.setWordWrap(True)
        layout.addWidget(label)
        preview, total = value_preview(value)
        kind = "NULL" if value is None else type(value).__name__
        if isinstance(value, str) and not value:
            kind = "空字符串" if zh else "Empty string"
        loaded = is_loaded_value(value)
        summary = f"{kind} · {total:,}" + (" 字符" if zh else " characters") if loaded else kind + (" · 尚未载入" if zh else " · Not loaded")
        self.type_label = QLabel(summary, self)
        layout.addWidget(self.type_label)
        self.value_view = QPlainTextEdit(self)
        self.value_view.setReadOnly(True)
        self.value_view.setPlainText(preview)
        layout.addWidget(self.value_view, 1)
        self.limit_note = QLabel(self)
        self.limit_note.setWordWrap(True)
        self.limit_note.setVisible(not loaded or total > len(preview))
        self.limit_note.setText(
            f"仅展示前 {len(preview):,} 字符。原始值未截断；请通过结果导出保存完整值。" if zh else
            f"Showing the first {len(preview):,} characters. The raw value is intact; export the result to save it in full."
        )
        if not loaded:
            self.limit_note.setText(
                "此字段尚未载入，界面不会直接读取。请在 SQL 中显式转换为文本或二进制并限制长度后重新查询。" if zh else
                "This value has not been loaded. Convert it to bounded text or binary in your SQL and query again."
            )
        layout.addWidget(self.limit_note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumSize(360, 240)
        self.resize(680, 440)


def inspect_value(column: str, value: Any, *, language: str = "zh", parent=None) -> None:
    dialog = ValueInspectorDialog(column, value, language=language, parent=parent)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()
