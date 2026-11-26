# -*- coding: utf-8 -*-
"""原生 SQL 编辑器：行号、DBX 风格语句块识别与行首执行按钮、美观联想框、关键字着色。"""

from __future__ import annotations

import re

from PyQt6.QtCore import QItemSelectionModel, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QFont, QFontMetrics, QKeyEvent, QPainter, QPen, QPolygonF,
    QStandardItem, QStandardItemModel, QSyntaxHighlighter, QTextCharFormat,
    QTextCursor, QTextFormat,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QCompleter, QListView, QMenu, QPlainTextEdit, QStyle,
    QStyledItemDelegate, QToolTip, QWidget,
)

from tools.data_center.sql_completion import completion_candidates, completion_items, current_token
from tools.data_center.sql_script import SqlScriptError, scan_sql

SQL_KEYWORDS = (
    'SELECT', 'SELECT * FROM', 'SELECT COUNT(*) FROM', 'FROM', 'WHERE',
    'AND', 'OR', 'NOT', 'IN', 'NOT IN', 'BETWEEN', 'LIKE', 'IS NULL', 'IS NOT NULL',
    'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'ON', 'AS', 'DISTINCT',
    'GROUP BY', 'ORDER BY', 'HAVING', 'DESC', 'ASC', 'EXPLAIN',
    'ROWNUM <= 100', 'ROWNUM <= 200', 'FETCH FIRST 100 ROWS ONLY', 'FETCH FIRST 200 ROWS ONLY',
    'LIMIT 100', 'LIMIT 200', 'OFFSET',
    'INSERT INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE FROM', 'MERGE INTO',
    'WITH', 'UNION', 'UNION ALL', 'CASE WHEN', 'THEN', 'ELSE', 'END', 'EXISTS',
    'CREATE TABLE', 'ALTER TABLE', 'DROP TABLE', 'DUAL',
    'ALL_TABLES', 'ALL_TAB_COLUMNS', 'ALL_OBJECTS', 'DBMS_METADATA.GET_DDL',
    'COUNT(*)', 'SUM()', 'AVG()', 'MAX()', 'MIN()', 'LISTAGG()', 'REGEXP_LIKE()',
    'TRUNC(SYSDATE)', 'SYSDATE', 'TO_CHAR()', 'TO_DATE()', 'NVL()', 'COALESCE()',
    'NOW()', 'DATE_FORMAT()', 'SUBSTR()', 'TRIM()', 'ROUND()',
)

_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 11
_DETAIL_ROLE = int(Qt.ItemDataRole.UserRole) + 12

_BADGE_STYLES = {
    'table': ('表', '#6C58D9', '#F3F0FF'),
    'view': ('视', '#0891B2', '#ECFEFF'),
    'column': ('列', '#059669', '#ECFDF5'),
    'func': ('函', '#D97706', '#FFFBEB'),
    'keyword': ('词', '#2563EB', '#EFF6FF'),
}


_STATEMENT_STARTER_RE = re.compile(
    r'^(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|EXPLAIN|SHOW|DESC|DESCRIBE|CALL|BEGIN|DECLARE)\b',
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z0-9_$`"]+(?:\.[A-Za-z0-9_$`"]+)?)',
    re.IGNORECASE,
)


def _split_blank_line_statements(text: str, seg_start: int, seg_end: int) -> list[tuple[int, int]]:
    """Split a semicolon-free SQL segment on blank lines followed by a top-level statement starter."""
    splits: list[int] = [seg_start]
    i = seg_start
    depth = 0
    quote: str | None = None
    while i < seg_end:
        ch = text[i]
        if quote is not None:
            if ch == '\\' and quote in {"'", '`', '"'}:
                i += 2
                continue
            if ch == quote:
                if i + 1 < seg_end and text[i + 1] == quote and quote in {"'", '"'}:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in {"'", '"', '`'}:
            quote = ch
            i += 1
            continue
        if text.startswith('--', i):
            nl = text.find('\n', i + 2, seg_end)
            i = seg_end if nl < 0 else nl + 1
            continue
        if text.startswith('/*', i):
            end_c = text.find('*/', i + 2, seg_end)
            i = seg_end if end_c < 0 else end_c + 2
            continue
        if ch == '(':
            depth += 1
            i += 1
            continue
        if ch == ')':
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch == '\n' and depth == 0:
            j = i + 1
            blank_seen = False
            while j < seg_end and text[j] in ' \t\r\n':
                if text[j] == '\n':
                    blank_seen = True
                j += 1
            if blank_seen and j < seg_end and _STATEMENT_STARTER_RE.match(text[j:min(seg_end, j + 24)]):
                # Only split when there is already non-whitespace SQL before this blank line
                if text[splits[-1]:i].strip():
                    splits.append(j)
                i = j
                continue
        i += 1
    splits.append(seg_end)
    result: list[tuple[int, int]] = []
    for k in range(len(splits) - 1):
        s = splits[k]
        e = splits[k + 1]
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            result.append((s, e))
    return result


class _SqlCompletionDelegate(QStyledItemDelegate):
    """DBX 风格补全列表项：类型徽章 + 补全名称 + 右侧表名/注释提示。"""

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(max(280, option.rect.width()), 30)

    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(4, 1, -4, -1)
        view = option.widget
        is_current = bool(view is not None and hasattr(view, 'currentIndex') and view.currentIndex() == index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) or is_current
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.setPen(QPen(QColor('#C5B7F5'), 1.0))
            painter.setBrush(QColor('#E4DCFC'))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#6C58D9'))
            painter.drawRoundedRect(QRectF(rect.left() + 1.5, rect.top() + 5, 3.0, rect.height() - 10), 1.5, 1.5)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#F4F1FC'))
            painter.drawRoundedRect(rect, 6, 6)

        kind = str(index.data(_KIND_ROLE) or 'keyword')
        badge_text, fg_hex, bg_hex = _BADGE_STYLES.get(kind, _BADGE_STYLES['keyword'])
        badge_rect = QRectF(rect.left() + 6, rect.center().y() - 9, 20, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg_hex))
        painter.drawRoundedRect(badge_rect, 4, 4)
        badge_font = QFont(option.font)
        badge_font.setPointSizeF(max(7.5, (option.font.pointSizeF() or 9.0) * 0.82))
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.setPen(QColor(fg_hex))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        detail = str(index.data(_DETAIL_ROLE) or '')
        main_font = QFont(option.font)
        main_font.setBold(selected)
        main_fm = QFontMetrics(main_font)
        detail_font = QFont(option.font)
        detail_font.setPointSizeF(max(7.5, (option.font.pointSizeF() or 9.0) * 0.85))
        detail_fm = QFontMetrics(detail_font)

        left = badge_rect.right() + 8
        right = rect.right() - 8
        avail = max(40.0, right - left)

        detail_w = 0.0
        if detail and avail > 140:
            max_detail = min(150.0, avail * 0.42)
            elided_detail = detail_fm.elidedText(detail, Qt.TextElideMode.ElideRight, int(max_detail))
            detail_w = detail_fm.horizontalAdvance(elided_detail) + 10
            painter.setFont(detail_font)
            painter.setPen(QColor('#7C7691'))
            painter.drawText(
                QRectF(right - detail_w + 4, rect.top(), detail_w - 4, rect.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                elided_detail,
            )

        main_avail = max(40.0, avail - detail_w)
        elided_main = main_fm.elidedText(text, Qt.TextElideMode.ElideRight, int(main_avail))
        painter.setFont(main_font)
        painter.setPen(QColor('#4D3BBD' if selected else '#262438'))
        painter.drawText(
            QRectF(left, rect.top(), main_avail, rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_main,
        )
        painter.restore()


class _SqlHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        'select', 'from', 'where', 'and', 'or', 'not', 'in', 'is', 'null', 'as',
        'join', 'left', 'right', 'inner', 'outer', 'on', 'group', 'by', 'order',
        'insert', 'into', 'values', 'update', 'set', 'delete', 'create', 'alter',
        'drop', 'table', 'view', 'index', 'with', 'union', 'all', 'distinct',
        'count', 'sum', 'case', 'when', 'then', 'else', 'end', 'commit',
        'rollback', 'grant', 'revoke', 'merge', 'scan', 'match', 'get', 'hgetall',
        'having', 'desc', 'asc', 'limit', 'offset', 'fetch', 'first', 'rows', 'only',
        'rownum', 'dual', 'exists', 'between', 'like', 'sysdate', 'trunc', 'nvl',
    )

    def __init__(self, document, colors: dict):
        super().__init__(document)
        self._kw = QTextCharFormat()
        self._kw.setForeground(QColor(colors.get('keyword', '#2554C7')))
        self._kw.setFontWeight(QFont.Weight.DemiBold)
        self._str = QTextCharFormat()
        self._str.setForeground(QColor(colors.get('string', '#B45309')))
        self._cmt = QTextCharFormat()
        self._cmt.setForeground(QColor(colors.get('comment', '#7C857E')))
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
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_gutter(event)

    def mouseMoveEvent(self, event):
        self._editor.handle_gutter_hover(event)

    def leaveEvent(self, event):
        self._editor.clear_gutter_hover()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._editor.handle_gutter_click(event.position()):
            event.accept()
            return
        super().mousePressEvent(event)


class SqlEditor(QPlainTextEdit):
    # Emitted on focus so a host can pull the object directory once when the
    # completion catalog is still empty.  Debouncing belongs to the host.
    completion_wanted = pyqtSignal()
    run_statement_requested = pyqtSignal()
    inspect_table_requested = pyqtSignal(str)
    view_ddl_requested = pyqtSignal(str)
    preview_data_requested = pyqtSignal(str)
    send_to_ai_requested = pyqtSignal(str)

    _RUN_COL_W = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('sql-editor')
        from ui.design_system import mono_font
        self.setFont(mono_font())
        self.setTabStopDistance(32)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._gutter = _LineNumberArea(self)
        self._snapshot = {}
        self._statement_blocks: list[tuple[int, int, int, int]] = []  # (start_line, end_line, start_pos, end_pos)
        self._active_stmt_index: int = -1
        self._hover_stmt_line: int = -1
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._on_update_request)
        self.textChanged.connect(self._refresh_statement_blocks)
        self.cursorPositionChanged.connect(self._on_cursor_changed)
        self.selectionChanged.connect(self._highlight_current)
        self._highlighter = _SqlHighlighter(self.document(), {})

        self._completer = QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._completer.setMaxVisibleItems(10)
        self._completer.activated.connect(self._insert_completion)
        self._model = QStandardItemModel(self)
        self._completer.setModel(self._model)
        popup = QListView()
        popup.setObjectName('sql-completion-popup')
        popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        popup.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        popup.setUniformItemSizes(True)
        popup.setMouseTracking(True)
        popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        popup.setItemDelegate(_SqlCompletionDelegate(popup))
        popup.setStyleSheet(
            'QListView#sql-completion-popup {'
            '  background: #FFFFFF;'
            '  border: 1px solid #CFC6EC;'
            '  border-radius: 10px;'
            '  padding: 4px;'
            '  outline: 0;'
            '}'
            'QListView#sql-completion-popup::item { border: none; min-height: 30px; }'
        )
        self._completer.setPopup(popup)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._apply_selection_style()
        self._update_gutter_width(0)
        self._refresh_statement_blocks()
        self._highlight_current()

    def bind_schema(self, snapshot: dict | None):
        self._snapshot = snapshot if isinstance(snapshot, dict) else {}
        self._model.clear()
        self._completer.popup().hide()
        self._refresh_statement_blocks()

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        self.completion_wanted.emit()

    def _apply_selection_style(self):
        primary = self._token('PRIMARY', '#6C58D9')
        on_primary = self._token('ON_PRIMARY', '#FFFFFF')
        self.setStyleSheet(
            f'QPlainTextEdit#sql-editor {{'
            f'selection-background-color: {primary};'
            f'selection-color: {on_primary};'
            f'}}'
        )

    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return self._RUN_COL_W + 10 + self.fontMetrics().horizontalAdvance('9') * digits

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

    def _refresh_statement_blocks(self):
        """Scan SQL statements into (start_line, end_line, start_pos, end_pos) blocks."""
        text = self.toPlainText()
        if not text.strip() or len(text) > 256 * 1024:
            self._statement_blocks = []
            self._active_stmt_index = -1
            self._gutter.update()
            self.viewport().update()
            return
        dialect = str(self._snapshot.get('dialect') or 'generic')
        try:
            script = scan_sql(text, dialect)
            segments = script.segments
        except SqlScriptError:
            segments = ()
        blocks: list[tuple[int, int, int, int]] = []
        for seg in segments:
            for s, e in _split_blank_line_statements(text, seg.start, seg.end):
                start_line = text.count('\n', 0, s)
                end_line = text.count('\n', 0, max(s, e - 1))
                blocks.append((start_line, end_line, s, e))
        self._statement_blocks = blocks
        self._update_active_statement()

    def active_statement_text(self) -> str:
        """Return the active statement block matching the green DBX highlight."""
        text = self.toPlainText()
        if 0 <= self._active_stmt_index < len(self._statement_blocks):
            _sl, _el, s, e = self._statement_blocks[self._active_stmt_index]
            return text[s:e].strip()
        return text.strip()

    def _update_active_statement(self):
        if not self._statement_blocks:
            self._active_stmt_index = -1
        else:
            _, pos = self.selected_or_all()
            cur_line = self.textCursor().blockNumber()
            active = -1
            for idx, (s_line, e_line, s_pos, e_pos) in enumerate(self._statement_blocks):
                if s_pos <= pos <= e_pos or s_line <= cur_line <= e_line:
                    active = idx
                    break
            self._active_stmt_index = active
        self._gutter.update()
        self.viewport().update()

    def _on_cursor_changed(self):
        self._update_active_statement()
        self._highlight_current()

    def _stmt_index_at_y(self, y: float) -> tuple[int, int]:
        """Return (stmt_index, start_line) if y hits a statement's gutter start line."""
        if not self._statement_blocks:
            return -1, -1
        start_map = {blk[0]: idx for idx, blk in enumerate(self._statement_blocks)}
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= self._gutter.height():
            if block.isVisible() and top <= y <= bottom:
                bnum = block.blockNumber()
                if bnum in start_map:
                    return start_map[bnum], bnum
                return -1, -1
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
        return -1, -1

    def handle_gutter_hover(self, event):
        pos = event.position()
        stmt_idx, line = (-1, -1)
        if pos.x() <= self._RUN_COL_W + 4:
            stmt_idx, line = self._stmt_index_at_y(pos.y())
        if line != self._hover_stmt_line:
            self._hover_stmt_line = line
            self._gutter.update()
        if stmt_idx >= 0:
            self._gutter.setCursor(Qt.CursorShape.PointingHandCursor)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                '点击执行此 SQL 语句（Ctrl+Enter）',
                self._gutter,
            )
        else:
            self._gutter.setCursor(Qt.CursorShape.ArrowCursor)

    def clear_gutter_hover(self):
        if self._hover_stmt_line != -1:
            self._hover_stmt_line = -1
            self._gutter.update()
        self._gutter.setCursor(Qt.CursorShape.ArrowCursor)

    def handle_gutter_click(self, pos: QPointF) -> bool:
        if pos.x() > self._RUN_COL_W + 4:
            return False
        stmt_idx, _line = self._stmt_index_at_y(pos.y())
        if stmt_idx < 0 or stmt_idx >= len(self._statement_blocks):
            return False
        _s_line, _e_line, s_pos, _e_pos = self._statement_blocks[stmt_idx]
        source = self.toPlainText()
        qt_pos = len(source[:s_pos].encode('utf-16-le', 'surrogatepass')) // 2
        cursor = self.textCursor()
        cursor.setPosition(qt_pos)
        self.setTextCursor(cursor)
        self.setFocus()
        self.run_statement_requested.emit()
        return True

    def paintEvent(self, event):
        super().paintEvent(event)
        # DBX 风格：给光标所在的当前 SQL 语句块绘制轻盈绿色边框，清晰标示 Ctrl+Enter 执行范围
        if self._active_stmt_index < 0 or self._active_stmt_index >= len(self._statement_blocks):
            return
        s_line, e_line, _s_pos, _e_pos = self._statement_blocks[self._active_stmt_index]
        doc = self.document()
        start_blk = doc.findBlockByNumber(s_line)
        end_blk = doc.findBlockByNumber(e_line)
        if not start_blk.isValid() or not end_blk.isValid():
            return
        offset = self.contentOffset()
        top_rect = self.blockBoundingGeometry(start_blk).translated(offset)
        bot_rect = self.blockBoundingGeometry(end_blk).translated(offset)
        vp = self.viewport().rect()
        box = QRectF(
            2.0,
            top_rect.top() + 0.5,
            max(40.0, vp.width() - 6.0),
            max(16.0, bot_rect.bottom() - top_rect.top() - 1.0),
        )
        if not box.intersects(QRectF(event.rect())):
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        border_color = QColor('#10B981')
        border_color.setAlpha(140)
        painter.setPen(QPen(border_color, 1.1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 4.0, 4.0)
        painter.end()

    def paint_gutter(self, event):
        painter = QPainter(self._gutter)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(event.rect(), QColor(self._token('SURFACE_SOFT', '#F5F4FA')))
        # 右侧分隔细线
        painter.setPen(QColor(self._token('BORDER', '#E5E0F2')))
        painter.drawLine(self._gutter.width() - 1, event.rect().top(), self._gutter.width() - 1, event.rect().bottom())

        stmt_start_lines = {blk[0]: idx for idx, blk in enumerate(self._statement_blocks)}
        active_start_line = (
            self._statement_blocks[self._active_stmt_index][0]
            if 0 <= self._active_stmt_index < len(self._statement_blocks)
            else -1
        )
        cur_line = self.textCursor().blockNumber()

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        line_h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if block_number in stmt_start_lines:
                    is_active = block_number == active_start_line
                    is_hover = block_number == self._hover_stmt_line
                    cy = top + line_h / 2.0
                    cx = 10.0
                    if is_hover:
                        bg = QColor('#10B981')
                        bg.setAlpha(38)
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(bg)
                        painter.drawRoundedRect(QRectF(2.5, cy - 8.0, 15.0, 16.0), 4.0, 4.0)
                    tri = QPolygonF([
                        QPointF(cx - 3.0, cy - 4.5),
                        QPointF(cx + 4.5, cy),
                        QPointF(cx - 3.0, cy + 4.5),
                    ])
                    icon_color = QColor('#059669' if (is_active or is_hover) else '#6EE7B7')
                    if is_hover:
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(icon_color)
                    else:
                        painter.setPen(QPen(icon_color, 1.35))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPolygon(tri)

                painter.setPen(QColor('#4D3BBD' if block_number == cur_line else '#7E7891'))
                painter.drawText(
                    self._RUN_COL_W, top, self._gutter.width() - self._RUN_COL_W - 6, line_h,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_number + 1),
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
        selection.format.setBackground(QColor(self._token('PRIMARY_SOFT', '#F1EEFC')))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def selected_or_all(self) -> tuple[str, int]:
        cursor = self.textCursor()
        selected = cursor.selectedText().replace('\u2029', '\n').strip()
        prefix = QTextCursor(cursor)
        prefix.clearSelection()
        prefix.setPosition(0, QTextCursor.MoveMode.KeepAnchor)
        return selected, len(prefix.selectedText())

    def _current_token(self) -> tuple[str, int, int]:
        _selection, position = self.selected_or_all()
        return current_token(self.toPlainText(), position)

    def _completion_candidates(self, prefix: str) -> list[str]:
        _selection, position = self.selected_or_all()
        return completion_candidates(self._snapshot, self.toPlainText(), position, prefix, SQL_KEYWORDS)

    def _completion_items(self, prefix: str) -> list[tuple[str, str, str]]:
        _selection, position = self.selected_or_all()
        return completion_items(self._snapshot, self.toPlainText(), position, prefix, SQL_KEYWORDS)

    def _insert_completion(self, text: str):
        _token, start, pos = self._current_token()
        source = self.toPlainText()
        cursor = self.textCursor()
        cursor.setPosition(len(source[:start].encode('utf-16-le', 'surrogatepass')) // 2)
        cursor.setPosition(len(source[:pos].encode('utf-16-le', 'surrogatepass')) // 2, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QKeyEvent):
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            key = event.key()
            pmodel = popup.model()
            row_total = pmodel.rowCount() if pmodel is not None else 0
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) and row_total > 0:
                cur_row = popup.currentIndex().row()
                if cur_row < 0:
                    cur_row = 0
                if key == Qt.Key.Key_Up:
                    next_row = (cur_row - 1) % row_total
                elif key == Qt.Key.Key_Down:
                    next_row = (cur_row + 1) % row_total
                elif key == Qt.Key.Key_PageUp:
                    next_row = max(0, cur_row - 6)
                else:
                    next_row = min(row_total - 1, cur_row + 6)
                next_idx = pmodel.index(next_row, 0)
                popup.setCurrentIndex(next_idx)
                sel_model = popup.selectionModel()
                if sel_model is not None:
                    sel_model.setCurrentIndex(next_idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                popup.scrollTo(next_idx)
                popup.viewport().update()
                event.accept()
                return
            if key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                cur_idx = popup.currentIndex()
                if not cur_idx.isValid() and row_total > 0:
                    cur_idx = pmodel.index(0, 0)
                if cur_idx.isValid():
                    chosen = str(cur_idx.data(Qt.ItemDataRole.DisplayRole) or '')
                    if chosen:
                        self._insert_completion(chosen)
                popup.hide()
                event.accept()
                return
            if key in (Qt.Key.Key_Escape, Qt.Key.Key_Backtab):
                popup.hide()
                event.accept()
                return
        if event.key() == Qt.Key.Key_Slash and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._toggle_line_comment()
            return
        explicit = event.key() == Qt.Key.Key_Space and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if not explicit:
            super().keyPressEvent(event)
        token, _start, _pos = self._current_token()
        if explicit or event.text() and (event.text().isalnum() or event.text() in '._$#"`'):
            if explicit or token:
                items = self._completion_items(token)
                self._model.clear()
                for text, kind, detail in items[:300]:
                    row = QStandardItem(text)
                    row.setData(kind, _KIND_ROLE)
                    row.setData(detail, _DETAIL_ROLE)
                    self._model.appendRow(row)
                if items:
                    self._completer.setCompletionPrefix('')
                    cr = self.cursorRect()
                    cr.translate(self.viewport().pos())
                    cr.moveTop(cr.bottom() + 4)
                    popup = self._completer.popup()
                    popup_w = min(560, max(340, int(self.width() * 0.55)))
                    row_count = min(10, self._model.rowCount())
                    popup_h = max(112, row_count * 30 + 12)
                    popup.setMinimumHeight(popup_h)
                    popup.setMaximumHeight(320)
                    cr.setWidth(popup_w)
                    self._completer.complete(cr)
                    popup.resize(popup_w, popup_h)
                    pmodel = popup.model()
                    if pmodel is not None and pmodel.rowCount() > 0:
                        first_idx = pmodel.index(0, 0)
                        popup.setCurrentIndex(first_idx)
                        sel_model = popup.selectionModel()
                        if sel_model is not None:
                            sel_model.setCurrentIndex(
                                first_idx,
                                QItemSelectionModel.SelectionFlag.ClearAndSelect,
                            )
                        popup.viewport().update()
                    return
        if popup is not None:
            popup.hide()

    def _toggle_line_comment(self):
        cursor = self.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        cursor.setPosition(start)
        start_blk = cursor.blockNumber()
        cursor.setPosition(end)
        end_blk = cursor.blockNumber()
        doc = self.document()
        lines = [doc.findBlockByNumber(n).text() for n in range(start_blk, end_blk + 1)]
        all_commented = all(not ln.strip() or ln.lstrip().startswith('--') for ln in lines)
        cursor.beginEditBlock()
        for n in range(start_blk, end_blk + 1):
            blk = doc.findBlockByNumber(n)
            txt = blk.text()
            if not txt.strip():
                continue
            c = QTextCursor(blk)
            if all_commented:
                idx = txt.find('--')
                if idx >= 0:
                    c.setPosition(blk.position() + idx)
                    rm_len = 3 if txt[idx:idx + 3] == '-- ' else 2
                    c.setPosition(blk.position() + idx + rm_len, QTextCursor.MoveMode.KeepAnchor)
                    c.removeSelectedText()
            else:
                c.setPosition(blk.position())
                c.insertText('-- ')
        cursor.endEditBlock()

    def _transform_selection(self, fn):
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        text = cursor.selectedText().replace('\u2029', '\n')
        cursor.insertText(fn(text))

    def _format_sql_text(self):
        cursor = self.textCursor()
        has_sel = cursor.hasSelection()
        raw = cursor.selectedText().replace('\u2029', '\n') if has_sel else self.toPlainText()
        if not raw.strip():
            return
        try:
            import sqlglot
            dialect = str(self._snapshot.get('dialect') or 'oracle')
            read_dialect = 'mysql' if dialect.endswith('mysql') else 'oracle'
            parts = [
                stmt.sql(dialect=read_dialect, pretty=True)
                for stmt in sqlglot.parse(raw, read=read_dialect)
                if stmt is not None
            ]
            if not parts:
                return
            formatted = ';\n\n'.join(parts) + (';' if raw.rstrip().endswith(';') else '')
        except Exception:
            return
        if has_sel:
            cursor.insertText(formatted)
        else:
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(formatted)

    def _remove_blank_lines(self):
        text = self.toPlainText()
        cleaned = '\n'.join(line for line in text.splitlines() if line.strip())
        if cleaned != text:
            cursor = self.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(cleaned)

    def _compress_sql_text(self):
        cursor = self.textCursor()
        has_sel = cursor.hasSelection()
        raw = cursor.selectedText().replace('\u2029', '\n') if has_sel else self.active_statement_text()
        if not raw.strip():
            return
        compact = re.sub(r'\s+', ' ', raw).strip()
        if has_sel:
            cursor.insertText(compact)
        elif 0 <= self._active_stmt_index < len(self._statement_blocks):
            _sl, _el, s, e = self._statement_blocks[self._active_stmt_index]
            c = self.textCursor()
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(compact)

    def _detect_table_at_cursor(self) -> str:
        """Identify a table name under cursor or referenced in the active statement."""
        token, _start, _pos = self._current_token()
        cleaned = token.strip('"` ')
        if cleaned and cleaned.upper() not in {k.split()[0] for k in SQL_KEYWORDS}:
            objs = (self._snapshot or {}).get('objects') or ()
            folded = cleaned.casefold()
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                name = str(obj.get('name') or '').strip()
                owner = str(obj.get('owner') or '').strip()
                full = f'{owner}.{name}' if owner else name
                if folded in {name.casefold(), full.casefold()}:
                    return full
        stmt = self.active_statement_text()
        match = _TABLE_REF_RE.search(stmt)
        if match:
            return match.group(1).strip('"` ')
        return ''

    def _columns_for_table(self, table_ref: str) -> list[str]:
        if not table_ref:
            return []
        short = table_ref.rsplit('.', 1)[-1].casefold()
        full = table_ref.casefold()
        for obj in (self._snapshot or {}).get('objects') or ():
            if not isinstance(obj, dict):
                continue
            name = str(obj.get('name') or '').strip()
            owner = str(obj.get('owner') or '').strip()
            obj_full = f'{owner}.{name}'.casefold() if owner else name.casefold()
            if name.casefold() == short or obj_full == full:
                cols = []
                for col in obj.get('columns') or ():
                    col_name = str(col.get('name') or '').strip() if isinstance(col, dict) else str(col or '').strip()
                    if col_name:
                        cols.append(col_name)
                return cols
        return []

    def _expand_star_in_statement(self):
        if not (0 <= self._active_stmt_index < len(self._statement_blocks)):
            return
        _sl, _el, s, e = self._statement_blocks[self._active_stmt_index]
        stmt = self.toPlainText()[s:e]
        table = self._detect_table_at_cursor()
        cols = self._columns_for_table(table)
        if not cols:
            return
        replaced, count = re.subn(r'(\bSELECT\s+(?:DISTINCT\s+)?)\*', r'\1' + ', '.join(cols), stmt, count=1, flags=re.I)
        if count:
            c = self.textCursor()
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(replaced)

    def _show_menu(self, pos):
        menu = QMenu(self)
        has_sel = self.textCursor().hasSelection()
        has_text = bool(self.toPlainText().strip())
        active_sql = self.active_statement_text() if has_text else ''
        table_ref = self._detect_table_at_cursor() if has_text else ''
        can_expand_star = bool(table_ref and '*' in active_sql and self._columns_for_table(table_ref))
        items: list[tuple[str | None, object, bool]] = [
            ('▷ 执行当前语句\tCtrl+Enter', lambda _checked=False: self.run_statement_requested.emit(), has_text),
            (None, None, False),
        ]
        if table_ref:
            items.extend([
                (f'查看表结构 ({table_ref})', lambda _checked=False, t=table_ref: self.inspect_table_requested.emit(t), True),
                (f'查看 DDL ({table_ref})', lambda _checked=False, t=table_ref: self.view_ddl_requested.emit(t), True),
                (f'查看前 100 行数据 ({table_ref})', lambda _checked=False, t=table_ref: self.preview_data_requested.emit(t), True),
                (None, None, False),
            ])
        if can_expand_star:
            items.append(('将 * 展开为字段', lambda _checked=False: self._expand_star_in_statement(), True))
        if active_sql:
            items.append(('发送到 AI 助手', lambda _checked=False, sql=active_sql: self.send_to_ai_requested.emit(sql), True))
            items.append((None, None, False))
        items.extend([
            ('格式化 SQL', lambda _checked=False: self._format_sql_text(), has_text),
            ('压缩为单行', lambda _checked=False: self._compress_sql_text(), has_text),
            ('注释 / 取消注释\tCtrl+/', lambda _checked=False: self._toggle_line_comment(), has_text),
            ('转为大写', lambda _checked=False: self._transform_selection(str.upper), has_sel),
            ('转为小写', lambda _checked=False: self._transform_selection(str.lower), has_sel),
            ('删除空行', lambda _checked=False: self._remove_blank_lines(), has_text),
            (None, None, False),
            ('撤销', self.undo, self.document().isUndoAvailable()),
            ('重做', self.redo, self.document().isRedoAvailable()),
            (None, None, False),
            ('剪切', self.cut, has_sel),
            ('复制', self.copy, has_sel),
            ('粘贴', self.paste, True),
            ('全选', self.selectAll, bool(self.toPlainText())),
        ])
        for name, slot, enabled in items:
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
