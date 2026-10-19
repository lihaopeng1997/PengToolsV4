"""Cooperative local find on already-loaded typed results; no queries or copies."""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QItemSelectionModel, QTimer
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from ui.data_center.value_format import is_loaded_value


MAX_MATCHES = 10_000


def scan_matches(pages, needle: str):
    """Yield matches or progress per bounded text chunk, retaining raw cell values."""
    row_offset = 0
    for page in pages:
        for row, values in enumerate(page.rows):
            for column, value in enumerate(values):
                if not is_loaded_value(value):
                    yield None
                    continue
                if type(value) in (bytes, bytearray, memoryview):
                    value = memoryview(value)
                    if value.contiguous:
                        value = value.cast('B')
                    step = max(1, 8192 // value.itemsize)
                    chunks = (('0x' if start == 0 else '') + value[start:start + step].hex()
                              for start in range(0, max(1, len(value)), step))
                else:
                    text = 'NULL' if value is None else value if isinstance(value, str) else str(value)
                    chunks = (text[start:start + 32768] for start in range(0, max(1, len(text)), 32768))
                carry = ''
                for chunk in chunks:
                    combined = carry + chunk.casefold()
                    if needle in combined:
                        yield row_offset + row, column
                        break
                    carry = combined[-(len(needle) - 1):] if len(needle) > 1 else ''
                    yield None
        row_offset += len(page.rows)


class ResultFindDialog(QDialog):
    """Find/highlight raw results without filtering or changing export row indexes."""

    def __init__(self, view, model, *, language='zh', parent=None):
        super().__init__(parent)
        self._view = view
        view.installEventFilter(self)
        self._model = model
        self._language = language
        self._matches = []
        self._position = -1
        self._iterator = None
        self._limited = False
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._advance)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.restart)
        model.modelReset.connect(self.restart)
        model.rowsInserted.connect(self.restart)
        model.destroyed.connect(self.close)
        zh = language == 'zh'
        self.setWindowTitle('查找已加载结果' if zh else 'Find in loaded results')
        self.setObjectName('manual-result-find')
        self.setModal(False)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setMaxLength(256)
        self.search.setPlaceholderText('文本 / NULL / 二进制十六进制' if zh else 'Text / NULL / binary hex')
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(lambda: self.move_match(1))
        layout.addWidget(self.search)
        scope = QLabel('仅查找已加载字段，未载入字段不计入结果。' if zh else 'Loaded fields only; unloaded fields are excluded.', self)
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.previous = QPushButton('上一项' if zh else 'Previous', self)
        self.next = QPushButton('下一项' if zh else 'Next', self)
        close = QPushButton('关闭' if zh else 'Close', self)
        self.previous.clicked.connect(lambda: self.move_match(-1))
        self.next.clicked.connect(lambda: self.move_match(1))
        close.clicked.connect(self.close)
        for button in (self.previous, self.next, close):
            button.setAutoDefault(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.resize(450, 165)
        self._update_status()

    def _search_changed(self, *_args):
        self._timer.stop()
        self._iterator = None
        self._matches.clear()
        self._position = -1
        self._limited = False
        self._debounce.start()
        self._update_status()

    def restart(self, *_args):
        self._search_changed()
        self._debounce.stop()
        needle = self.search.text().casefold()
        if needle and self.isVisible():
            self._iterator = iter(scan_matches(self._model.pages, needle))
            self._timer.start()
        self._update_status()

    def _advance(self):
        deadline = time.monotonic() + .003
        while self._iterator is not None and time.monotonic() < deadline:
            try:
                match = next(self._iterator)
            except StopIteration:
                self._timer.stop()
                self._iterator = None
                break
            if match is not None:
                self._matches.append(match)
                if self._position < 0:
                    self.move_match(1)
                if len(self._matches) >= MAX_MATCHES:
                    self._timer.stop()
                    self._iterator = None
                    self._limited = True
        self._update_status()

    def move_match(self, offset):
        if not self._matches:
            return
        self._position = (self._position + offset) % len(self._matches)
        row, column = self._matches[self._position]
        index = self._model.index(row, column)
        self._view.showColumn(column)
        self._view.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        )
        self._view.scrollTo(index)
        self._update_status()

    def _update_status(self):
        zh = self._language == 'zh'
        count = len(self._matches)
        if not self.search.text():
            text = '仅查找已加载结果；未载入字段不参与查找，不会加载下一页。' if zh else 'Searches loaded values only; unloaded fields are skipped and no next page is fetched.'
        else:
            text = (f'第 {max(0, self._position + 1)} / {count} 个匹配' if zh else f'Match {max(0, self._position + 1)} of {count}')
            if self._timer.isActive() or self._debounce.isActive():
                text += ' · 正在查找' if zh else ' · Searching'
            if self._limited:
                text += f' · 已达到 {count:,} 项，请缩小范围' if zh else f' · {count:,}-match limit; narrow the search'
        self.status.setText(text)
        self.previous.setEnabled(bool(count))
        self.next.setEnabled(bool(count))

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.search.setFocus()
        self.restart()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._view and event.type() == QEvent.Type.Hide:
            self.hide()
        return super().eventFilter(watched, event)

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        self._debounce.stop()
        self._iterator = None
        super().hideEvent(event)
