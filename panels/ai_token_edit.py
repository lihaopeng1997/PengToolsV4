# -*- coding: utf-8 -*-
"""AI 自然语言输入：可见 Token + 右键添加表/字段。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from tools.ai_object_context import empty_context, keep_tokens, remove_token
from tools.schema_snapshot import format_field_label, format_object_label, search_fields, search_objects
from ui.design_system import apply_button

TOKEN_PROP = 0x0A11


def _plain_format() -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.clearProperty(TOKEN_PROP)
    return fmt


def _token_format(kind: str, token_id: str) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setProperty(TOKEN_PROP, token_id)
    if kind == 'field':
        fmt.setBackground(QColor('#EEF4FF'))
        fmt.setForeground(QColor('#356189'))
    else:
        fmt.setBackground(QColor('#E4EFE8'))
        fmt.setForeground(QColor('#2F5342'))
    fmt.setFontWeight(700)
    return fmt


class AiPromptEdit(QTextEdit):
    tokens_changed = pyqtSignal()
    add_table_requested = pyqtSignal(int)
    add_field_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ai-prompt-edit')
        self.setAcceptRichText(True)
        self.context = empty_context()
        self._menu_pos = 0
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.textChanged.connect(self._sync_tokens_from_document)

    def bind_snapshot(self, snapshot: dict | None):
        snap = snapshot if isinstance(snapshot, dict) else {}
        self.context['snapshot_id'] = str(snap.get('snapshot_id') or '')
        self.context['connection_fingerprint'] = str(snap.get('fingerprint') or '')

    def plain_question(self) -> str:
        return self.toPlainText().strip()

    def insert_token(self, kind: str, token: dict, position: int | None = None):
        if not token:
            return
        label = f"表：{token.get('name')}" if kind == 'object' else f"字段：{token.get('qualified_name') or token.get('name')}"
        cursor = self.textCursor()
        if position is not None:
            cursor.setPosition(max(0, min(int(position), len(self.toPlainText()))))
        cursor.insertText(label, _token_format(kind, str(token.get('token_id') or '')))
        cursor.setCharFormat(_plain_format())
        cursor.insertText(' ', _plain_format())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(_plain_format())
        self.tokens_changed.emit()

    def clear_tokens(self):
        self._remove_token_spans()
        self.context['selected_objects'] = []
        self.context['selected_fields'] = []
        self.tokens_changed.emit()

    def _remove_token_spans(self):
        spans = []
        block = self.document().firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.charFormat().property(TOKEN_PROP):
                    spans.append((frag.position(), frag.length()))
                it += 1
            block = block.next()
        self.blockSignals(True)
        for pos, length in reversed(spans):
            cur = QTextCursor(self.document())
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
        self.blockSignals(False)

    def token_ids_in_document(self) -> list[str]:
        ids = []
        block = self.document().firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                token_id = frag.charFormat().property(TOKEN_PROP)
                if token_id:
                    ids.append(str(token_id))
                it += 1
            block = block.next()
        return ids

    def _sync_tokens_from_document(self):
        keep_tokens(self.context, self.token_ids_in_document())
        self.tokens_changed.emit()

    def _show_menu(self, pos):
        cursor = self.cursorForPosition(pos)
        self._menu_pos = cursor.position()
        zh = True
        parent = self.parent()
        while parent is not None and not hasattr(parent, 'language'):
            parent = parent.parent()
        if parent is not None:
            zh = getattr(parent, 'language', 'zh') == 'zh'
        menu = QMenu(self)
        add_table = QAction('添加表…' if zh else 'Add table…', self)
        add_field = QAction('添加字段…' if zh else 'Add field…', self)
        view = QAction(('查看已添加对象（%s）' if zh else 'Review objects (%s)') % (
            len(self.context.get('selected_objects') or []) + len(self.context.get('selected_fields') or [])
        ), self)
        clear = QAction('清除本次已添加对象' if zh else 'Clear added objects', self)
        add_table.triggered.connect(lambda: self.add_table_requested.emit(self._menu_pos))
        add_field.triggered.connect(lambda: self.add_field_requested.emit(self._menu_pos))
        view.triggered.connect(self._view_tokens)
        clear.triggered.connect(self._clear_tokens_keep_text)
        menu.addAction(add_table)
        menu.addAction(add_field)
        menu.addSeparator()
        menu.addAction(view)
        menu.addAction(clear)
        std = self.createStandardContextMenu()
        if std is not None:
            menu.addSeparator()
            for action in std.actions():
                menu.addAction(action)
        menu.exec(self.mapToGlobal(pos))

    def _clear_tokens_keep_text(self):
        self.clear_tokens()

    def _view_tokens(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('已添加对象')
        root = QVBoxLayout(dialog)
        box = QListWidget()
        for item in (self.context.get('selected_objects') or []):
            box.addItem('表：' + str(item.get('qualified_name') or item.get('name')))
        for item in (self.context.get('selected_fields') or []):
            box.addItem('字段：' + str(item.get('qualified_name') or item.get('name')))
        if box.count() == 0:
            box.addItem('（无）')
        root.addWidget(box)
        close = QPushButton('关闭')
        apply_button(close, 'secondary', compact=True)
        close.clicked.connect(dialog.accept)
        root.addWidget(close)
        dialog.exec()

    def _token_span_at(self, pos: int):
        block = self.document().findBlock(max(0, pos))
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            token_id = frag.charFormat().property(TOKEN_PROP)
            start, end = frag.position(), frag.position() + frag.length()
            if token_id and start <= pos <= end:
                return str(token_id), start, end
            it += 1
        return None

    def _ensure_plain_insert(self):
        cursor = self.textCursor()
        pos = cursor.position()
        hit = self._token_span_at(pos) or self._token_span_at(max(0, pos - 1))
        if hit:
            _token_id, start, end = hit
            if start < pos < end:
                cursor.setPosition(end)
                self.setTextCursor(cursor)
        cursor = self.textCursor()
        cursor.setCharFormat(_plain_format())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(_plain_format())

    def insertFromMimeData(self, source):
        text = source.text() if source is not None else ''
        self._ensure_plain_insert()
        cursor = self.textCursor()
        cursor.insertText(text, _plain_format())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(_plain_format())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            cursor = self.textCursor()
            pos = cursor.position()
            block = self.document().findBlock(max(0, pos - 1 if event.key() == Qt.Key.Key_Backspace else pos))
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                token_id = frag.charFormat().property(TOKEN_PROP)
                start, end = frag.position(), frag.position() + frag.length()
                hit = (event.key() == Qt.Key.Key_Backspace and start < pos <= end) or (
                    event.key() == Qt.Key.Key_Delete and start <= pos < end
                )
                if token_id and hit:
                    cur = QTextCursor(self.document())
                    cur.setPosition(start)
                    cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    cur.removeSelectedText()
                    remove_token(self.context, str(token_id))
                    self.setCurrentCharFormat(_plain_format())
                    self.tokens_changed.emit()
                    return
                it += 1
        elif event.text():
            self._ensure_plain_insert()
        super().keyPressEvent(event)


class ObjectPickDialog(QDialog):
    def __init__(self, language, snapshot, *, mode='table', parent=None, redis=False):
        super().__init__(parent)
        self.language = language
        self.snapshot = snapshot
        self.mode = mode
        self.redis = redis
        zh = language == 'zh'
        self.setWindowTitle('添加字段到自然语言' if mode == 'field' and zh else ('添加表到自然语言' if zh else 'Add object'))
        self.resize(760, 500)
        self._object = None
        root = QVBoxLayout(self)
        cols = QHBoxLayout()
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索表名 / 注释' if zh else 'Search tables')
        self.search.textChanged.connect(self._fill_objects)
        left.addWidget(self.search)
        self.obj_list = QListWidget()
        self.obj_list.currentItemChanged.connect(self._on_object)
        left.addWidget(self.obj_list, 1)
        cols.addLayout(left, 1)
        self.field_search = QLineEdit()
        self.field_list = QListWidget()
        self.field_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.field_list.itemSelectionChanged.connect(self._refresh_ok)
        if mode == 'field':
            right = QVBoxLayout()
            self.field_search.setPlaceholderText('搜索字段名 / 注释' if zh else 'Search fields')
            self.field_search.setEnabled(False)
            self.field_search.textChanged.connect(self._fill_fields)
            right.addWidget(self.field_search)
            right.addWidget(self.field_list, 1)
            cols.addLayout(right, 1)
        root.addLayout(cols, 1)
        self.hint = QLabel()
        self.hint.setObjectName('field-hint')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton('取消' if zh else 'Cancel')
        apply_button(cancel, 'secondary', compact=True)
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton('添加到自然语言' if zh else 'Insert')
        apply_button(self.ok, 'primary', compact=True)
        self.ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        root.addLayout(btns)
        self._fill_objects()
        self._refresh_ok()

    def _fill_objects(self, _text=''):
        self.obj_list.clear()
        for obj in search_objects(self.snapshot, self.search.text()):
            item = QListWidgetItem(format_object_label(obj))
            item.setData(Qt.ItemDataRole.UserRole, obj)
            self.obj_list.addItem(item)
        if self.obj_list.count() == 0:
            zh = self.language == 'zh'
            empty = QListWidgetItem('没有可添加的对象，请先扫描结构' if zh else 'No objects. Scan schema first.')
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.obj_list.addItem(empty)

    def _on_object(self, current, _prev=None):
        self._object = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if self.mode != 'field':
            self._refresh_ok()
            return
        zh = self.language == 'zh'
        can_fields = isinstance(self._object, dict) and not self.redis
        self.field_search.setEnabled(can_fields)
        self.field_search.blockSignals(True)
        self.field_search.clear()
        self.field_search.blockSignals(False)
        if self.redis:
            self.hint.setText('Redis 以键模式为主，不提供关系型字段选择。' if zh else 'Redis has no relational fields.')
            self.field_list.clear()
            self._refresh_ok()
            return
        if not isinstance(self._object, dict):
            self.field_list.clear()
            self._refresh_ok()
            return
        inferred = bool(self._object.get('inferred'))
        self.hint.setText('Mongo 字段来自受控样本推断，不含文档值。' if inferred and zh else '')
        self._fill_fields()

    def _fill_fields(self, _text=''):
        selected = set()
        for item in self.field_list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('name'):
                selected.add(str(data.get('name')))
        self.field_list.blockSignals(True)
        self.field_list.clear()
        if not isinstance(self._object, dict) or self.redis:
            self.field_list.blockSignals(False)
            self._refresh_ok()
            return
        for col in search_fields(self._object, self.field_search.text()):
            item = QListWidgetItem(format_field_label(col))
            item.setData(Qt.ItemDataRole.UserRole, col)
            self.field_list.addItem(item)
            if str(col.get('name') or '') in selected:
                item.setSelected(True)
        self.field_list.blockSignals(False)
        self._refresh_ok()

    def _refresh_ok(self):
        if self.mode == 'table':
            self.ok.setEnabled(isinstance(self._object, dict))
            return
        if self.redis:
            self.ok.setEnabled(False)
            return
        self.ok.setEnabled(isinstance(self._object, dict) and bool(self.field_list.selectedItems()))

    def chosen_object(self):
        return self._object if isinstance(self._object, dict) else None

    def chosen_fields(self) -> list:
        if self.mode != 'field':
            return []
        rows = []
        for item in self.field_list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                rows.append(data)
        return rows
