# -*- coding: utf-8 -*-
"""JSON 查看器：树表可读性优先（深层级字段名可横滑、可拖列宽）。

XML 完整能力已迁至网关模块的 XmlWorkspace；本组件专注 JSON。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QHeaderView, QPlainTextEdit, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from tools.json_viewer import (
    format_json_text, json_path_child, json_type_name, node_json_text,
    node_value_text, parse_json_text,
)
from ui.confirm_dialog import show_warning

# 深层级时缩进会吃掉第一列宽度；默认给字段名更宽，并禁止挤到不可读
_KEY_COL_MIN = 160
_KEY_COL_DEFAULT = 280
_KEY_COL_MAX_AUTO = 520
_TYPE_COL_DEFAULT = 88
_VALUE_PREVIEW_MAX = 280


class JsonViewer(QWidget):
    """格式化文本 + 可定位、可复制节点的轻量 JSON 查看器。"""

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._data = None
        self._item_values = {}
        self._matches = []
        self._match_index = -1
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        toolbar = QHBoxLayout()
        self.format_btn = QPushButton()
        self.format_btn.setObjectName('json-tool-btn')
        self.format_btn.clicked.connect(self.format_current)
        toolbar.addWidget(self.format_btn)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('json-search')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._search)
        self.search_edit.returnPressed.connect(self.next_match)
        toolbar.addWidget(self.search_edit, 1)
        self.previous_btn = QPushButton('↑')
        self.previous_btn.setObjectName('json-icon-btn')
        self.previous_btn.clicked.connect(self.previous_match)
        toolbar.addWidget(self.previous_btn)
        self.next_btn = QPushButton('↓')
        self.next_btn.setObjectName('json-icon-btn')
        self.next_btn.clicked.connect(self.next_match)
        toolbar.addWidget(self.next_btn)
        self.search_status = QLabel('0 / 0')
        self.search_status.setObjectName('json-search-status')
        toolbar.addWidget(self.search_status)
        layout.addLayout(toolbar)

        second = QHBoxLayout()
        self.path_caption = QLabel()
        self.path_caption.setObjectName('json-path-caption')
        second.addWidget(self.path_caption)
        self.path_value = QLineEdit('$')
        self.path_value.setObjectName('json-path')
        self.path_value.setReadOnly(True)
        second.addWidget(self.path_value, 1)
        self.copy_path_btn = QPushButton()
        self.copy_path_btn.setObjectName('json-tool-btn')
        self.copy_path_btn.clicked.connect(lambda: self._copy_text(self.path_value.text()))
        second.addWidget(self.copy_path_btn)
        self.expand_btn = QPushButton()
        self.expand_btn.setObjectName('json-tool-btn')
        self.expand_btn.clicked.connect(self._expand_all)
        second.addWidget(self.expand_btn)
        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName('json-tool-btn')
        self.collapse_btn.clicked.connect(self._collapse_all)
        second.addWidget(self.collapse_btn)
        layout.addLayout(second)

        self.tabs = QTabWidget()
        self.tabs.setObjectName('module-tabs')
        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName('json-text')
        # 可编辑：支持粘贴 JSON/XML 后一键格式化
        self.text_edit.setReadOnly(False)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.text_edit.setFont(mono)
        self.tabs.addTab(self.text_edit, '')

        self.tree = QTreeWidget()
        self.tree.setObjectName('json-tree')
        self.tree.setColumnCount(3)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        # 略减缩进，深层级时给字段名多留字宽；允许横向滚动看全名
        self.tree.setIndentation(14)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.setWordWrap(False)
        header = self.tree.header()
        header.setObjectName('json-tree-header')
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(64)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 三列均可拖动调宽；值列默认更宽，字段名列拖窄时自动回弹
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, _KEY_COL_DEFAULT)
        self.tree.setColumnWidth(1, _TYPE_COL_DEFAULT)
        self.tree.setColumnWidth(2, 360)
        header.sectionResized.connect(self._on_tree_section_resized)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.itemDoubleClicked.connect(self._copy_item_value)
        self.tabs.addTab(self.tree, '')
        layout.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.json_status = QLabel()
        self.json_status.setObjectName('field-hint')
        footer.addWidget(self.json_status, 1)
        self.clear_view_btn = QPushButton()
        self.clear_view_btn.setObjectName('json-tool-btn')
        self.clear_view_btn.clicked.connect(self.clear)
        footer.addWidget(self.clear_view_btn)
        self.copy_json_btn = QPushButton()
        self.copy_json_btn.setObjectName('json-tool-btn')
        self.copy_json_btn.clicked.connect(self.copy_formatted_json)
        footer.addWidget(self.copy_json_btn)
        layout.addLayout(footer)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.format_btn.setText('一键格式化' if zh else 'Format')
        self.format_btn.setToolTip(
            '将文本区内容解析为 JSON 并美化缩进' if zh else 'Parse and pretty-print JSON in the text area'
        )
        self.search_edit.setPlaceholderText('搜索字段、值或 JSONPath' if zh else 'Search key, value or JSONPath')
        self.previous_btn.setToolTip('上一个匹配' if zh else 'Previous match')
        self.next_btn.setToolTip('下一个匹配' if zh else 'Next match')
        self.path_caption.setText('节点路径' if zh else 'Node path')
        self.copy_path_btn.setText('复制路径' if zh else 'Copy path')
        self.expand_btn.setText('展开' if zh else 'Expand')
        self.collapse_btn.setText('折叠' if zh else 'Collapse')
        self.tabs.setTabText(0, '格式化文本' if zh else 'Formatted text')
        self.tabs.setTabText(1, '树形节点' if zh else 'Tree nodes')
        self.tree.setHeaderLabels(['字段名', '类型', '值'] if zh else ['Field', 'Type', 'Value'])
        self.tree.headerItem().setToolTip(0, '可拖动列宽 · 深层级可横向滚动查看完整字段名' if zh else 'Drag to resize · scroll horizontally for deep keys')
        self.tree.headerItem().setToolTip(2, '可拖动列宽 · 悬停看完整值' if zh else 'Drag to resize · hover for full value')
        self.clear_view_btn.setText('清空' if zh else 'Clear')
        self.copy_json_btn.setText('复制全部' if zh else 'Copy all')
        self.text_edit.setPlaceholderText(
            '解密明文 / 粘贴 JSON 后可一键格式化（大文本不自动折行，可横向滚动）。XML 请用上方「XML 工具」页。'
            if zh else
            'Decrypted text / paste JSON then format (no wrap; scroll). Use the XML tools tab for XML.'
        )
        if self._data is None and not self.text_edit.toPlainText().strip():
            self.json_status.setText('等待解密结果，或粘贴 JSON' if zh else 'Waiting for content, or paste JSON')

    def set_text(self, text, auto_format=True):
        self.text_edit.setPlainText(text)
        try:
            data = parse_json_text(text)
        except ValueError as exc:
            self._data = None
            self.tree.clear()
            self._item_values.clear()
            self._matches.clear()
            self._match_index = -1
            self.search_status.setText('0 / 0')
            self.path_value.setText('$')
            self.json_status.setText(str(exc))
            return False
        self._data = data
        if auto_format:
            self.text_edit.setPlainText(format_json_text(text))
        self._build_tree()
        count = len(self._item_values)
        self.json_status.setText(
            f'JSON 已识别 · {count} 个节点 · 双击叶子节点即可复制'
            if self.language == 'zh' else
            f'Valid JSON · {count} nodes · double-click a leaf to copy'
        )
        self._search(self.search_edit.text())
        return True

    def clear(self):
        self._data = None
        self.text_edit.clear()
        self.tree.clear()
        self._item_values.clear()
        self._matches.clear()
        self._match_index = -1
        self.search_edit.clear()
        self.search_status.setText('0 / 0')
        self.path_value.setText('$')
        self.json_status.setText(
            '等待解密结果，或粘贴 JSON' if self.language == 'zh' else 'Waiting for content, or paste JSON'
        )

    def plain_text(self):
        return self.text_edit.toPlainText()

    def format_current(self):
        text = self.text_edit.toPlainText()
        try:
            formatted = format_json_text(text)
        except ValueError as exc:
            show_warning(self, 'PengTools JSON', str(exc))
            return False
        self.set_text(formatted, auto_format=False)
        return True

    def _build_tree(self):
        self.tree.clear()
        self._item_values.clear()
        root = self._add_item(None, '$', self._data, '$')
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self._fit_key_column()

    def _fit_key_column(self):
        """按内容撑开字段名列（含缩进），再夹在可读上下限内；用户仍可拖动。"""
        self.tree.resizeColumnToContents(0)
        width = self.tree.columnWidth(0) + 16
        width = max(_KEY_COL_MIN, min(_KEY_COL_MAX_AUTO, width))
        # 至少保持默认宽度，避免浅树时列过窄
        width = max(width, _KEY_COL_DEFAULT)
        self.tree.blockSignals(True)
        self.tree.setColumnWidth(0, width)
        if self.tree.columnWidth(1) < _TYPE_COL_DEFAULT:
            self.tree.setColumnWidth(1, _TYPE_COL_DEFAULT)
        if self.tree.columnWidth(2) < 200:
            self.tree.setColumnWidth(2, 360)
        self.tree.blockSignals(False)

    def _on_tree_section_resized(self, index, _old, new_size):
        """字段名列不允许拖得过窄，避免深层级时完全不可读。"""
        if index != 0 or new_size >= _KEY_COL_MIN:
            return
        header = self.tree.header()
        header.blockSignals(True)
        self.tree.setColumnWidth(0, _KEY_COL_MIN)
        header.blockSignals(False)

    def _add_item(self, parent, key, value, path):
        key_text = str(key)
        type_name = json_type_name(value)
        if isinstance(value, dict):
            full_value = f'{{{len(value)}}}'
            preview = full_value
        elif isinstance(value, list):
            full_value = f'[{len(value)}]'
            preview = full_value
        else:
            full_value = node_value_text(value)
            if len(full_value) > _VALUE_PREVIEW_MAX:
                preview = full_value[: _VALUE_PREVIEW_MAX - 1] + '…'
            else:
                preview = full_value
        item = QTreeWidgetItem([key_text, type_name, preview])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        # 悬停始终可见完整字段名 / 路径 / 值（深层级不截断阅读）
        item.setToolTip(0, f'{key_text}\n{path}')
        item.setToolTip(1, type_name)
        item.setToolTip(2, full_value if full_value else preview)
        item.setTextAlignment(1, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
        self._item_values[id(item)] = value
        (parent.addChild(item) if parent is not None else self.tree.addTopLevelItem(item))
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                self._add_item(item, child_key, child_value, json_path_child(path, child_key))
        elif isinstance(value, list):
            for index, child_value in enumerate(value):
                self._add_item(item, f'[{index}]', child_value, json_path_child(path, index))
        return item

    def _all_items(self):
        result = []

        def visit(item):
            result.append(item)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return result

    def _search(self, query):
        normal = QBrush()
        highlight = QBrush(QColor('#FFF0A6'))
        current = QBrush(QColor('#FFD86B'))
        for item in self._all_items():
            for column in range(3):
                item.setBackground(column, normal)
        needle = query.strip().casefold()
        self._matches = []
        self._match_index = -1
        if needle:
            for item in self._all_items():
                path = item.data(0, Qt.ItemDataRole.UserRole) or ''
                haystack = '\n'.join((item.text(0), item.text(2), path)).casefold()
                if needle in haystack:
                    self._matches.append(item)
                    for column in range(3):
                        item.setBackground(column, highlight)
            if self._matches:
                self._match_index = 0
                for column in range(3):
                    self._matches[0].setBackground(column, current)
                self._focus_match()
        self._update_search_status()

    def _update_search_status(self):
        current = self._match_index + 1 if self._matches else 0
        self.search_status.setText(f'{current} / {len(self._matches)}')

    def _move_match(self, delta):
        if not self._matches:
            return
        old = self._matches[self._match_index]
        for column in range(3):
            old.setBackground(column, QBrush(QColor('#FFF0A6')))
        self._match_index = (self._match_index + delta) % len(self._matches)
        item = self._matches[self._match_index]
        for column in range(3):
            item.setBackground(column, QBrush(QColor('#FFD86B')))
        self._focus_match()
        self._update_search_status()

    def previous_match(self):
        self._move_match(-1)

    def next_match(self):
        self._move_match(1)

    def _focus_match(self):
        item = self._matches[self._match_index]
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tabs.setCurrentIndex(1)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)

    def _on_current_item_changed(self, current, _previous):
        self.path_value.setText(current.data(0, Qt.ItemDataRole.UserRole) if current else '$')

    def _value_for_item(self, item):
        return self._item_values.get(id(item))

    def _copy_item_value(self, item, _column=0):
        if item and item.childCount() == 0:
            self._copy_text(node_value_text(self._value_for_item(item)))

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return
        zh = self.language == 'zh'
        menu = QMenu(self)
        value_action = menu.addAction('复制节点值' if zh else 'Copy node value')
        json_action = menu.addAction('复制节点 JSON' if zh else 'Copy node JSON')
        path_action = menu.addAction('复制 JSONPath' if zh else 'Copy JSONPath')
        menu.addSeparator()
        expand_action = menu.addAction('展开此节点' if zh else 'Expand this node')
        collapse_action = menu.addAction('折叠此节点' if zh else 'Collapse this node')
        chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
        if chosen is value_action:
            self._copy_text(node_value_text(self._value_for_item(item)))
        elif chosen is json_action:
            self._copy_text(node_json_text(self._value_for_item(item)))
        elif chosen is path_action:
            self._copy_text(item.data(0, Qt.ItemDataRole.UserRole))
        elif chosen is expand_action:
            self._set_expanded_recursive(item, True)
        elif chosen is collapse_action:
            self._set_expanded_recursive(item, False)

    @staticmethod
    def _set_expanded_recursive(item, expanded):
        item.setExpanded(expanded)
        for index in range(item.childCount()):
            JsonViewer._set_expanded_recursive(item.child(index), expanded)

    def _expand_all(self):
        self.tree.expandAll()

    def _collapse_all(self):
        self.tree.collapseAll()

    def copy_formatted_json(self):
        # 优先复制当前文本区；JSON 树存在时也可用结构化 JSON
        text = self.text_edit.toPlainText().strip()
        if text:
            self._copy_text(text)
            return
        if self._data is not None:
            self._copy_text(node_json_text(self._data))

    @staticmethod
    def _copy_text(text):
        if text is not None:
            QApplication.clipboard().setText(str(text))
