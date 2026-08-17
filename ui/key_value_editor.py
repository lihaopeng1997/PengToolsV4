# -*- coding: utf-8 -*-
"""请求测试用的 Key / Value 行编辑器。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.design_system import apply_button
from ui.field_metrics import size_field_height


class KeyValueEditor(QWidget):
    """多行 Key / Value。toPlainText / setPlainText 兼容旧的纯文本接口。"""

    def __init__(self, *, mode: str = 'header', parent=None):
        super().__init__(parent)
        self._mode = 'query' if mode == 'query' else 'header'
        self._sep = '=' if self._mode == 'query' else ': '
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._host = QWidget()
        self._rows = QVBoxLayout(self._host)
        self._rows.setContentsMargins(0, 0, 8, 0)
        self._rows.setSpacing(6)
        self._rows.addStretch(1)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, 1)
        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self.add_btn = QPushButton('添加一行')
        apply_button(self.add_btn, 'ghost', compact=True)
        self.add_btn.clicked.connect(lambda: self._add_row('', '', focus=True))
        add_row.addWidget(self.add_btn)
        root.addLayout(add_row)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for _ in range(4):
            self._add_row('', '')

    def _add_row(self, key: str = '', value: str = '', *, focus: bool = False) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        key_edit = QLineEdit()
        size_field_height(key_edit)
        key_edit.setPlaceholderText('Key')
        key_edit.setText(key)
        key_edit.setFixedWidth(168)
        value_edit = QLineEdit()
        size_field_height(value_edit)
        value_edit.setPlaceholderText('Value')
        value_edit.setText(value)
        delete_btn = QPushButton('删除')
        apply_button(delete_btn, 'ghost', compact=True)
        delete_btn.clicked.connect(lambda: self._remove_row(row))
        layout.addWidget(key_edit)
        layout.addWidget(value_edit, 1)
        layout.addWidget(delete_btn)
        row._key_edit = key_edit  # type: ignore[attr-defined]
        row._value_edit = value_edit  # type: ignore[attr-defined]
        insert_at = max(0, self._rows.count() - 1)
        self._rows.insertWidget(insert_at, row)
        key_edit.textEdited.connect(self._ensure_blank_tail)
        value_edit.textEdited.connect(self._ensure_blank_tail)
        if focus:
            key_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _remove_row(self, row: QWidget) -> None:
        self._rows.removeWidget(row)
        row.deleteLater()
        if not self._pair_widgets():
            self._add_row('', '')

    def _pair_widgets(self) -> list[QWidget]:
        items = []
        for index in range(self._rows.count()):
            item = self._rows.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None and hasattr(widget, '_key_edit'):
                items.append(widget)
        return items

    def _ensure_blank_tail(self) -> None:
        rows = self._pair_widgets()
        if not rows:
            self._add_row('', '')
            return
        last = rows[-1]
        if last._key_edit.text().strip() or last._value_edit.text().strip():
            if len(rows) < 40:
                self._add_row('', '')

    def pairs(self) -> list[tuple[str, str]]:
        result = []
        for row in self._pair_widgets():
            key = row._key_edit.text().strip()
            value = row._value_edit.text()
            if key or value.strip():
                result.append((key, value))
        return result

    def toPlainText(self) -> str:
        lines = []
        for key, value in self.pairs():
            if not key:
                continue
            lines.append(f'{key}{self._sep}{value}')
        return '\n'.join(lines)

    def setPlainText(self, text: str) -> None:
        pairs = _parse_pairs(text or '', self._mode)
        for row in list(self._pair_widgets()):
            self._rows.removeWidget(row)
            row.deleteLater()
        if not pairs:
            pairs = [('', ''), ('', ''), ('', ''), ('', '')]
        for key, value in pairs:
            self._add_row(key, value)
        if pairs[-1][0] or pairs[-1][1]:
            self._add_row('', '')

    def setPlaceholderText(self, _text: str) -> None:
        return

    def setFont(self, font) -> None:
        for row in self._pair_widgets():
            row._key_edit.setFont(font)
            row._value_edit.setFont(font)


def _parse_pairs(text: str, mode: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in str(text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        chunks = line.split('&') if mode == 'query' and '&' in line else [line]
        for chunk in chunks:
            item = chunk.strip()
            if not item:
                continue
            if mode == 'query':
                if '=' in item:
                    key, value = item.split('=', 1)
                    pairs.append((key.strip(), value.strip()))
                else:
                    pairs.append((item, ''))
            elif ':' in item:
                key, value = item.split(':', 1)
                pairs.append((key.strip(), value.strip()))
            else:
                pairs.append((item, ''))
    return pairs
