# -*- coding: utf-8 -*-
"""原生 SQL 编辑器：行号、当前行高亮、关键字着色。不引入浏览器内核。"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextFormat
from PyQt6.QtWidgets import QPlainTextEdit, QWidget


class _SqlHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        'select', 'from', 'where', 'and', 'or', 'not', 'in', 'is', 'null', 'as',
        'join', 'left', 'right', 'inner', 'outer', 'on', 'group', 'by', 'order',
        'insert', 'into', 'values', 'update', 'set', 'delete', 'create', 'alter',
        'drop', 'table', 'view', 'index', 'with', 'union', 'all', 'distinct',
        'count', 'sum', 'case', 'when', 'then', 'else', 'end', 'commit',
        'rollback', 'grant', 'revoke', 'merge', 'scan', 'match', 'get', 'hgetall',
    )

    def __init__(self, document, colors: dict):
        super().__init__(document)
        self._kw = QTextCharFormat()
        self._kw.setForeground(QColor(colors.get('keyword', '#1D4F91')))
        self._str = QTextCharFormat()
        self._str.setForeground(QColor(colors.get('string', '#8A4B08')))
        self._cmt = QTextCharFormat()
        self._cmt.setForeground(QColor(colors.get('comment', '#6B746E')))
        self._cmt.setFontItalic(True)

    def highlightBlock(self, text):
        raw = str(text or '')
        lower = raw.lower()
        i = 0
        n = len(raw)
        while i < n:
            ch = raw[i]
            if ch == '-' and i + 1 < n and raw[i + 1] == '-':
                self.setFormat(i, n - i, self._cmt)
                break
            if ch in ("'", '"'):
                j = i + 1
                quote = ch
                while j < n:
                    if raw[j] == quote:
                        j += 1
                        break
                    j += 1
                self.setFormat(i, j - i, self._str)
                i = j
                continue
            if ch.isalpha() or ch == '_':
                j = i + 1
                while j < n and (raw[j].isalnum() or raw[j] == '_'):
                    j += 1
                if lower[i:j] in self.KEYWORDS:
                    self.setFormat(i, j - i, self._kw)
                i = j
                continue
            i += 1


class _LineNumberArea(QWidget):
    def __init__(self, editor: 'SqlEditor'):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_gutter(event)


class SqlEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont('Consolas', 10))
        self.setTabStopDistance(32)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current)
        self._highlighter = _SqlHighlighter(self.document(), {})
        self._update_gutter_width(0)
        self._highlight_current()

    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance('9') * digits

    def _update_gutter_width(self, _count=0):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def _token(self, name: str, fallback: str) -> str:
        try:
            from ui.theme_manager import ThemeManager
            return ThemeManager.instance().token(name) or fallback
        except Exception:
            return fallback

    def paint_gutter(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(self._token('SURFACE_SOFT', '#F3F4F6')))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor('#6B746E'))
                painter.drawText(
                    0, top, self._gutter.width() - 6, self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def _highlight_current(self):
        from PyQt6.QtWidgets import QTextEdit
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(self._token('PRIMARY_SOFT', '#EEF4FF')))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def selected_or_all(self) -> tuple[str, int]:
        cursor = self.textCursor()
        selected = cursor.selectedText().replace('\u2029', '\n').strip()
        return selected, cursor.position()
