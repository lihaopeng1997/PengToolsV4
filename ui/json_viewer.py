# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QHeaderView, QPlainTextEdit, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from tools.json_viewer import (
    format_json_text, json_path_child, json_type_name, node_json_text,
    node_value_text, parse_json_text,
)
from tools.xml_formatter import format_xml_text


class JsonViewer(QWidget):
    """格式化文本 + 可定位、可复制节点的轻量 JSON 查看器；附带 XML 美化入口。"""

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
        self.xml_format_btn = QPushButton()
        self.xml_format_btn.setObjectName('json-tool-btn')
        self.xml_format_btn.clicked.connect(self.format_xml_current)
        toolbar.addWidget(self.xml_format_btn)
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
        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName('json-text')
        # 可编辑：支持粘贴 JSON/XML 后一键格式化
        self.text_edit.setReadOnly(False)
        self.tabs.addTab(self.text_edit, '')

        self.tree = QTreeWidget()
        self.tree.setObjectName('json-tree')
        self.tree.setColumnCount(3)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 175)
        self.tree.setColumnWidth(1, 72)
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
        self.copy_json_btn = QPushButton()
        self.copy_json_btn.setObjectName('json-tool-btn')
        self.copy_json_btn.clicked.connect(self.copy_formatted_json)
        footer.addWidget(self.copy_json_btn)
        layout.addLayout(footer)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.format_btn.setText('一键格式化' if zh else 'Format')
        self.xml_format_btn.setText('XML 美化' if zh else 'Beautify XML')
        self.xml_format_btn.setToolTip(
            '粘贴 XML（可含外层引号或 \\n / \\" 转义）后一键格式化'
            if zh else
            'Paste XML (outer quotes or \\n / \\" escapes OK) and beautify'
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
        self.tree.setHeaderLabels(['节点', '类型', '值'] if zh else ['Node', 'Type', 'Value'])
        self.copy_json_btn.setText('复制格式化 JSON' if zh else 'Copy formatted JSON')
        self.text_edit.setPlaceholderText(
            '解密明文 / 粘贴 JSON 或 XML 后可一键格式化'
            if zh else
            'Decrypted text / paste JSON or XML then format'
        )
        if self._data is None and not self.text_edit.toPlainText().strip():
            self.json_status.setText('等待解密结果，或粘贴 JSON/XML' if zh else 'Waiting for content, or paste JSON/XML')

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
            '等待解密结果，或粘贴 JSON/XML' if self.language == 'zh' else 'Waiting for content, or paste JSON/XML'
        )

    def plain_text(self):
        return self.text_edit.toPlainText()

    def format_current(self):
        text = self.text_edit.toPlainText()
        try:
            formatted = format_json_text(text)
        except ValueError as exc:
            QMessageBox.warning(self, 'PengTools JSON', str(exc))
            return False
        self.set_text(formatted, auto_format=False)
        return True

    def format_xml_current(self):
        """对当前文本区内容做 XML 美化（去引号/反转义后格式化）。"""
        text = self.text_edit.toPlainText()
        try:
            formatted = format_xml_text(text)
        except ValueError as exc:
            QMessageBox.warning(self, 'PengTools XML', str(exc))
            self.json_status.setText(str(exc))
            return False
        self.text_edit.setPlainText(formatted)
        self.tabs.setCurrentIndex(0)
        # XML 不是 JSON 树：清空树与匹配，避免展示过期节点
        self._data = None
        self.tree.clear()
        self._item_values.clear()
        self._matches.clear()
        self._match_index = -1
        self.search_status.setText('0 / 0')
        self.path_value.setText('$')
        lines = formatted.count('\n') + (0 if formatted.endswith('\n') else 1)
        self.json_status.setText(
            f'XML 已美化 · {lines} 行 · 可复制下方文本'
            if self.language == 'zh' else
            f'XML beautified · {lines} lines · copy text below'
        )
        return True

    def _build_tree(self):
        self.tree.clear()
        self._item_values.clear()
        root = self._add_item(None, '$', self._data, '$')
        root.setExpanded(True)
        self.tree.setCurrentItem(root)

    def _add_item(self, parent, key, value, path):
        if isinstance(value, dict):
            preview = f'{{{len(value)}}}'
        elif isinstance(value, list):
            preview = f'[{len(value)}]'
        else:
            preview = node_value_text(value)
            if len(preview) > 160:
                preview = preview[:157] + '…'
        item = QTreeWidgetItem([str(key), json_type_name(value), preview])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
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
        if self._data is not None:
            self._copy_text(node_json_text(self._data))

    @staticmethod
    def _copy_text(text):
        if text is not None:
            QApplication.clipboard().setText(str(text))
