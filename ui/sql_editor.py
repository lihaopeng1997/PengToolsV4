# -*- coding: utf-8 -*-
"""原生 SQL 编辑器：行号、当前行高亮、关键字着色。不引入浏览器内核。"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, QStringListModel, Qt
from PyQt6.QtGui import QAction, QColor, QFont, QKeyEvent, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat
from PyQt6.QtWidgets import QCompleter, QMenu, QPlainTextEdit, QWidget

SQL_KEYWORDS = (
    'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN',
    'GROUP BY', 'ORDER BY', 'HAVING', 'INSERT', 'UPDATE', 'DELETE', 'MERGE',
    'WITH', 'CREATE', 'ALTER', 'DROP', 'UNION', 'CASE', 'WHEN', 'THEN', 'ELSE',
    'END', 'AND', 'OR', 'IN', 'EXISTS', 'LIKE', 'IS NULL', 'IS NOT NULL',
    'SELECT * FROM',
)


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
        self.setObjectName('sql-editor')
        self.setFont(QFont('Consolas', 10))
        self.setTabStopDistance(32)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._gutter = _LineNumberArea(self)
        self._snapshot = {}
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current)
        self.selectionChanged.connect(self._highlight_current)
        self._highlighter = _SqlHighlighter(self.document(), {})
        self._completer = QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._completer.activated.connect(self._insert_completion)
        self._model = QStringListModel(self)
        self._completer.setModel(self._model)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._apply_selection_style()
        self._update_gutter_width(0)
        self._highlight_current()

    def bind_schema(self, snapshot: dict | None):
        self._snapshot = snapshot if isinstance(snapshot, dict) else {}

    def _apply_selection_style(self):
        primary = self._token('PRIMARY', '#2F6FED')
        on_primary = self._token('ON_PRIMARY', '#FFFFFF')
        self.setStyleSheet(
            f'QPlainTextEdit#sql-editor {{'
            f'selection-background-color: {primary};'
            f'selection-color: {on_primary};'
            f'}}'
        )

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
        if self.textCursor().hasSelection():
            self.setExtraSelections([])
            return
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

    def _current_token(self) -> tuple[str, int, int]:
        cursor = self.textCursor()
        pos = cursor.position()
        text = self.toPlainText()
        start = pos
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] in '._'):
            start -= 1
        end = pos
        while end < len(text) and (text[end].isalnum() or text[end] == '_'):
            end += 1
        return text[start:pos], start, pos

    def _schema_names(self) -> tuple[list[str], dict[str, list[str]]]:
        tables = []
        columns: dict[str, list[str]] = {}
        for obj in (self._snapshot.get('objects') or []):
            name = str(obj.get('name') or '')
            owner = str(obj.get('owner') or '')
            if not name:
                continue
            tables.append(name)
            if owner:
                tables.append(f'{owner}.{name}')
            cols = [str(c.get('name') or '') for c in (obj.get('columns') or []) if c.get('name')]
            columns[name.upper()] = cols
            if owner:
                columns[f'{owner}.{name}'.upper()] = cols
        return tables, columns

    def _completion_candidates(self, prefix: str) -> list[str]:
        prefix_raw = prefix
        tables, columns = self._schema_names()
        if '.' in prefix_raw:
            table, _, col_prefix = prefix_raw.rpartition('.')
            hits = []
            for col in columns.get(table.upper(), []):
                if col.upper().startswith(col_prefix.upper()) or not col_prefix:
                    hits.append(f'{table}.{col}' if table else col)
            return hits
        low = prefix_raw.lower()
        hits = [kw for kw in SQL_KEYWORDS if kw.lower().startswith(low)]

        def _name_hit(name: str) -> bool:
            folded = name.lower()
            if folded.startswith(low):
                return True
            compact = folded.replace('_', '')
            needle = low.replace('_', '')
            if compact.startswith(needle):
                return True
            if len(needle) >= 3:
                it = iter(folded)
                return all(ch in it for ch in needle)
            return False

        hits.extend(name for name in tables if _name_hit(name))
        # 去重保序
        seen = set()
        out = []
        for item in hits:
            key = item.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _insert_completion(self, text: str):
        token, start, pos = self._current_token()
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(pos, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QKeyEvent):
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            if event.key() in (
                Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape,
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
            ):
                event.ignore()
                return
        super().keyPressEvent(event)
        token, _start, _pos = self._current_token()
        if event.text() and (event.text().isalnum() or event.text() in '._'):
            if token or token.endswith('.'):
                cands = self._completion_candidates(token)
                self._model.setStringList(cands)
                if cands:
                    self._completer.setCompletionPrefix(token)
                    cr = self.cursorRect()
                    cr.setWidth(self._completer.popup().sizeHintForColumn(0) + 24)
                    self._completer.complete(cr)
                    return
        if popup is not None:
            popup.hide()

    def _show_menu(self, pos):
        menu = QMenu(self)
        for name, slot, enabled in (
            ('撤销', self.undo, self.document().isUndoAvailable()),
            ('重做', self.redo, self.document().isRedoAvailable()),
            (None, None, False),
            ('剪切', self.cut, self.textCursor().hasSelection()),
            ('复制', self.copy, self.textCursor().hasSelection()),
            ('粘贴', self.paste, True),
            ('全选', self.selectAll, bool(self.toPlainText())),
        ):
            if name is None:
                if menu.actions():
                    menu.addSeparator()
                continue
            if not enabled:
                continue
            action = QAction(name, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.exec(self.mapToGlobal(pos))
