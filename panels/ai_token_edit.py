# -*- coding: utf-8 -*-
"""AI 自然语言输入：可见 Token + 右键添加表/字段。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from tools.ai_object_context import (
    add_field, add_object, context_matches_snapshot, empty_context, keep_tokens,
    qualified_name, remove_token, selected_field_names, selected_table_names,
)
from tools.schema_snapshot import search_fields, search_objects
from ui.design_system import apply_button

TOKEN_PROP = 0x0A11


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
        cursor.insertText(' ')
        self.setTextCursor(cursor)
        self.tokens_changed.emit()

    def clear_tokens(self):
        self.context['selected_objects'] = []
        self.context['selected_fields'] = []
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        block = self.document().firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if fmt.property(TOKEN_PROP):
                    cur = QTextCursor(self.document())
                    cur.setPosition(frag.position())
                    cur.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                    cur.removeSelectedText()
                it += 1
            block = block.next()
        self.tokens_changed.emit()

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
        # 只删 Token 片段，保留自然语言
        self.blockSignals(True)
        block = self.document().firstBlock()
        spans = []
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.charFormat().property(TOKEN_PROP):
                    spans.append((frag.position(), frag.length()))
                it += 1
            block = block.next()
        for pos, length in reversed(spans):
            cur = QTextCursor(self.document())
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
        self.blockSignals(False)
        self.context['selected_objects'] = []
        self.context['selected_fields'] = []
        self.tokens_changed.emit()

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
                    self.tokens_changed.emit()
                    return
                it += 1
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
        self.resize(720, 460)
        self._object = None
        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索对象名 / 注释' if zh else 'Search objects')
        self.search.textChanged.connect(self._fill_objects)
        root.addWidget(self.search)
        cols = QHBoxLayout()
        self.obj_list = QListWidget()
        self.obj_list.currentItemChanged.connect(self._on_object)
        cols.addWidget(self.obj_list, 1)
        self.field_list = QListWidget()
        self.field_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.field_list.itemSelectionChanged.connect(self._refresh_ok)
        if mode == 'field':
            cols.addWidget(self.field_list, 1)
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
            label = qualified_name(obj) or str(obj.get('name') or '')
            kind = str(obj.get('object_type') or 'TABLE')
            item = QListWidgetItem(f'{label}  [{kind}]')
            item.setData(Qt.ItemDataRole.UserRole, obj)
            self.obj_list.addItem(item)
        if self.obj_list.count() == 0:
            zh = self.language == 'zh'
            empty = QListWidgetItem('没有可添加的对象，请先扫描结构' if zh else 'No objects. Scan schema first.')
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.obj_list.addItem(empty)

    def _on_object(self, current, _prev=None):
        self._object = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.field_list.clear()
        if self.mode != 'field':
            self._refresh_ok()
            return
        zh = self.language == 'zh'
        if self.redis:
            self.hint.setText('Redis 以键模式为主，不提供关系型字段选择。' if zh else 'Redis has no relational fields.')
            self._refresh_ok()
            return
        if not isinstance(self._object, dict):
            self._refresh_ok()
            return
        inferred = bool(self._object.get('inferred'))
        self.hint.setText('Mongo 字段来自受控样本推断，不含文档值。' if inferred and zh else '')
        for col in search_fields(self._object, ''):
            extra = str(col.get('data_type') or '')
            item = QListWidgetItem(f"{col.get('name')}  {extra}")
            item.setData(Qt.ItemDataRole.UserRole, col)
            self.field_list.addItem(item)
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
