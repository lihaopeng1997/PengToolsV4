# -*- coding: utf-8 -*-
"""JSON 文本、树和子键值联动查看器。"""

import json
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QTimer, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QHeaderView, QPushButton, QSplitter, QTableView, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui.design_system import apply_button
from ui.field_metrics import size_line
from ui.table_utils import autofit_columns
from tools.json_viewer import (
    find_duplicate_json_keys, json_path_child, json_type_name, node_json_text,
    node_value_text, parse_json_text,
)
from ui.confirm_dialog import confirm_action, show_warning
from ui.foldable_text_edit import FoldablePlainTextEdit
from ui.search_highlight import (
    apply_text_match_index,
    collect_text_spans,
    ensure_text_pos_unfolded,
    focus_tree_item,
    paint_tree_item,
)

# 深层级时缩进会吃掉第一列宽度；默认给字段名更宽，并禁止挤到不可读
_KEY_COL_MIN = 160
_KEY_COL_DEFAULT = 280
_KEY_COL_MAX_AUTO = 520
_TYPE_COL_DEFAULT = 88
_VALUE_PREVIEW_MAX = 280
_NODE_LIMIT = 20_000
_NODE_ROLE = Qt.ItemDataRole.UserRole + 1
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]]|-Infinity|Infinity|NaN|true|false|null|[-+0-9.eE]+')
_COLON = re.compile(r'\s*:')
_NON_BMP = re.compile(r'[\U00010000-\U0010ffff]')
_DECODER = json.JSONDecoder()


@dataclass
class _JsonNode:
    parent: int | None
    key: str
    path: str
    start: int
    value_start: int
    end: int
    type_name: str
    value_text: str
    children: list[int] = field(default_factory=list)
    child_count: int = 0

    def display_value(self):
        if self.type_name == 'object':
            return f'{{{self.child_count}}}'
        if self.type_name == 'array':
            return f'[{self.child_count}]'
        return self.value_text


_LARGE_JSON_FOLD_LIMIT = 2 * 1024 * 1024
_MAX_AUTO_FORMAT_DEPTH = 128


def _estimate_json_depth(text: str, stop_at: int = _MAX_AUTO_FORMAT_DEPTH + 1) -> int:
    """O(N) 扫描 JSON 括号深度（字符串感知），超过阈值即提前返回。"""
    depth = 0
    max_depth = 0
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in ('{', '['):
            depth += 1
            if depth > max_depth:
                max_depth = depth
                if max_depth >= stop_at:
                    return max_depth
        elif ch in ('}', ']'):
            if depth > 0:
                depth -= 1
    return max_depth


def _source_nodes(text, non_bmp):
    def qt_position(position):
        return position + bisect_left(non_bmp, position)

    nodes = []
    parents = []
    pending_key = None
    truncated = False
    for token in _TOKEN.finditer(text):
        literal = token.group()
        if literal in ('}', ']'):
            index = parents.pop()
            if index is not None:
                nodes[index].end = qt_position(token.end())
            continue
        if literal.startswith('"') and _COLON.match(text, token.end()):
            pending_key = token
            continue

        parent_index = parents[-1] if parents else None
        parent = nodes[parent_index] if parent_index is not None else None
        container = literal in ('{', '[')
        index = None
        if len(nodes) < _NODE_LIMIT:
            start = token.start()
            if parent is None:
                key, label, path = '$', '$', '$'
            elif pending_key is not None:
                key = json.loads(pending_key.group())
                label, path = key, json_path_child(parent.path, key)
                start = pending_key.start()
            else:
                key = parent.child_count
                label, path = f'[{key}]', json_path_child(parent.path, key)
            if container:
                type_name, value_text = ('object' if literal == '{' else 'array'), ''
            else:
                value = json.loads(literal)
                type_name, value_text = json_type_name(value), node_value_text(value)
            index = len(nodes)
            nodes.append(_JsonNode(
                parent_index, label, path, qt_position(start), token.start(),
                qt_position(token.end()), type_name, value_text,
            ))
            if parent is not None:
                parent.children.append(index)
        else:
            truncated = True
            if len(text) > _LARGE_JSON_FOLD_LIMIT // 4:
                break

        if parent is not None:
            parent.child_count += 1
        if container:
            parents.append(index)
        pending_key = None
    return nodes, truncated


def _preview(value):
    return value if len(value) <= _VALUE_PREVIEW_MAX else value[:_VALUE_PREVIEW_MAX - 1] + '…'


class _ChildTableModel(QAbstractTableModel):
    def __init__(self, viewer):
        super().__init__(viewer)
        self._viewer = viewer
        self._children = []

    def set_children(self, children):
        self.beginResetModel()
        self._children = children
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._children)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 2

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return (('子键', '值') if self._viewer.language == 'zh' else ('Child key', 'Value'))[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        node = self._viewer._nodes[self._children[index.row()]]
        value = node.key if index.column() == 0 else node.display_value()
        return _preview(value) if role == Qt.ItemDataRole.DisplayRole else value

    def child_at(self, row):
        return self._children[row]




class JsonViewer(QWidget):
    """格式化文本 + 可定位、可复制节点的轻量 JSON 查看器。"""

    def __init__(self, language='zh'):
        super().__init__()
        self.setObjectName('json-viewer')
        self.language = language
        self._data = None
        self._nodes = []
        self._node_starts = []
        self._items = {}
        self._source = ''
        self._non_bmp = []
        self._truncated = False
        self._syncing = False
        self._setting_text = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(180)
        self._refresh_timer.timeout.connect(self._refresh_from_editor)
        # 树搜索：源节点编号列表；文本搜索：(start, end) 列表
        self._matches = []
        self._match_index = -1
        self._search_mode = 'text'  # 'tree' | 'text'
        self._text_spans: list[tuple[int, int]] = []
        self._painted_indices: set[int] = set()
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.format_btn = QPushButton()
        apply_button(self.format_btn, 'secondary', compact=True)
        self.format_btn.clicked.connect(self.format_current)
        toolbar.addWidget(self.format_btn)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('json-search')
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._search)
        self.search_edit.returnPressed.connect(self.next_match)
        toolbar.addWidget(self.search_edit, 1)
        self.search_scope = QComboBox()
        self.search_scope.addItems(['', ''])
        self.search_scope.currentIndexChanged.connect(lambda: self._search(self.search_edit.text()))
        toolbar.addWidget(self.search_scope)
        self.previous_btn = QPushButton('上一条')
        apply_button(self.previous_btn, 'ghost', compact=True)
        self.previous_btn.clicked.connect(self.previous_match)
        toolbar.addWidget(self.previous_btn)
        self.next_btn = QPushButton('下一条')
        apply_button(self.next_btn, 'ghost', compact=True)
        self.next_btn.clicked.connect(self.next_match)
        toolbar.addWidget(self.next_btn)
        self.search_status = QLabel('0 / 0')
        self.search_status.setObjectName('json-search-status')
        toolbar.addWidget(self.search_status)
        layout.addLayout(toolbar)

        second = QHBoxLayout()
        second.setSpacing(8)
        self.path_caption = QLabel()
        self.path_caption.setObjectName('json-path-caption')
        second.addWidget(self.path_caption)
        self.path_value = QLineEdit('$')
        self.path_value.setObjectName('json-path')
        self.path_value.setReadOnly(True)
        size_line(self.path_value, 'path')
        second.addWidget(self.path_value, 1)
        self.copy_path_btn = QPushButton()
        apply_button(self.copy_path_btn, 'ghost', compact=True)
        self.copy_path_btn.clicked.connect(lambda: self._copy_text(self.path_value.text()))
        second.addWidget(self.copy_path_btn)
        self.expand_btn = QPushButton()
        apply_button(self.expand_btn, 'ghost', compact=True)
        self.expand_btn.clicked.connect(self._expand_all)
        second.addWidget(self.expand_btn)
        self.collapse_btn = QPushButton()
        apply_button(self.collapse_btn, 'ghost', compact=True)
        self.collapse_btn.clicked.connect(self._collapse_all)
        second.addWidget(self.collapse_btn)
        self.glance_btn = QPushButton()
        apply_button(self.glance_btn, 'ghost', compact=True)
        self.glance_btn.setCheckable(True)
        self.glance_btn.setChecked(True)
        self.glance_btn.toggled.connect(self._toggle_glance)
        second.addWidget(self.glance_btn)
        layout.addLayout(second)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        # 格式化文本：HiJson 风格折叠（左侧 +/-、工具栏展开/折叠）
        self.text_edit = FoldablePlainTextEdit(fold_mode='auto')
        self.text_edit.setObjectName('json-text')
        self.text_edit.setReadOnly(False)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        from ui.design_system import mono_font
        self.text_edit.setFont(mono_font())
        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.cursorPositionChanged.connect(self._on_text_cursor)
        self.text_edit.selectionChanged.connect(self._on_text_cursor)
        self.splitter.addWidget(self.text_edit)

        self.tree = QTreeWidget()
        self.tree.setObjectName('json-tree')
        self.tree.setColumnCount(3)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        # 略减缩进，深层级时给字段名多留字宽；允许横向滚动看全名
        self.tree.setIndentation(16)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.setWordWrap(False)
        header = self.tree.header()
        header.setObjectName('json-tree-header')
        try:
            from ui.design_system import apply_list_header
            apply_list_header(header)
        except Exception:
            pass
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
        self.tree.itemExpanded.connect(self._load_children)
        self.tree.itemClicked.connect(lambda item, _column: self._locate_text(item))

        self.child_table = QTableView()
        self.child_table.setObjectName('json-children')
        self.child_table.setAlternatingRowColors(True)
        self.child_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.child_table.verticalHeader().hide()
        self._child_model = _ChildTableModel(self)
        self.child_table.setModel(self._child_model)
        autofit_columns(self.child_table).configure({0: _KEY_COL_MIN}, minimum=True)
        self.child_table.doubleClicked.connect(
            lambda index: self._focus_node(self._child_model.child_at(index.row()))
        )
        details = QSplitter(Qt.Orientation.Vertical)
        details.setChildrenCollapsible(False)
        details.addWidget(self.tree)
        details.addWidget(self.child_table)
        details.setSizes([420, 180])
        self.splitter.addWidget(details)
        self.splitter.setSizes([520, 480])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)

        self.limit_hint = QLabel()
        self.limit_hint.setObjectName('field-hint')
        self.limit_hint.setWordWrap(True)
        self.limit_hint.hide()
        layout.addWidget(self.limit_hint)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.json_status = QLabel()
        self.json_status.setObjectName('field-hint')
        footer.addWidget(self.json_status, 1)
        self.clear_view_btn = QPushButton()
        apply_button(self.clear_view_btn, 'ghost', compact=True)
        self.clear_view_btn.clicked.connect(self.clear)
        footer.addWidget(self.clear_view_btn)
        self.copy_json_btn = QPushButton()
        apply_button(self.copy_json_btn, 'secondary', compact=True)
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
        self.search_edit.setPlaceholderText('搜索（回车下一个）' if zh else 'Search (Enter = next)')
        self.search_scope.setItemText(0, '文本' if zh else 'Text')
        self.search_scope.setItemText(1, '树' if zh else 'Tree')
        self.search_scope.setAccessibleName('搜索范围' if zh else 'Search scope')
        self.previous_btn.setText('上一条' if zh else 'Prev')
        self.next_btn.setText('下一条' if zh else 'Next')
        self.previous_btn.setToolTip('上一个匹配' if zh else 'Previous match')
        self.next_btn.setToolTip('下一个匹配' if zh else 'Next match')
        self.path_caption.setText('节点路径' if zh else 'Node path')
        self.copy_path_btn.setText('复制路径' if zh else 'Copy path')
        self.expand_btn.setText('全部展开' if zh else 'Expand all')
        self.collapse_btn.setText('全部折叠' if zh else 'Collapse all')
        try:
            from ui.icons import apply_icon
            apply_icon(self.expand_btn, 'expand', 14)
            apply_icon(self.collapse_btn, 'collapse', 14)
        except Exception:
            pass
        self.expand_btn.setToolTip(
            '展开格式化文本的折叠块，并展开树形节点' if zh else
            'Expand folded text blocks and tree nodes'
        )
        self.collapse_btn.setToolTip(
            '折叠格式化文本的可折叠块，并折叠树形节点（参考 HiJson）' if zh else
            'Collapse foldable text blocks and tree nodes (HiJson-style)'
        )
        self.glance_btn.setText('缩略图' if zh else 'Minimap')
        self.glance_btn.setToolTip(
            '右侧 CodeGlance 风格缩略导航：点击跳转，拖拽视口滚动，拖左边调宽度'
            if zh else
            'Right-side CodeGlance minimap: click to jump, drag viewport to scroll, drag edge to resize'
        )
        self.child_table.setAccessibleName('选中节点的子键值' if zh else 'Selected node children')
        self._child_model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 1)
        self._update_limit_hint()
        self._update_search_status()
        self.tree.setHeaderLabels(['字段名', '类型', '值'] if zh else ['Field', 'Type', 'Value'])
        self.tree.headerItem().setToolTip(0, '可拖动列宽 · 深层级可横向滚动查看完整字段名' if zh else 'Drag to resize · scroll horizontally for deep keys')
        self.tree.headerItem().setToolTip(2, '可拖动列宽 · 悬停看完整值' if zh else 'Drag to resize · hover for full value')
        self.clear_view_btn.setText('清空' if zh else 'Clear')
        self.copy_json_btn.setText('复制全部' if zh else 'Copy all')
        self.text_edit.setPlaceholderText(
            '粘贴 JSON 后一键格式化。左侧 +/- 可折叠对象/数组（HiJson 风格）；工具栏可全部展开/折叠。'
            if zh else
            'Paste JSON then format. Use +/- margin to fold objects/arrays (HiJson-style); toolbar expands/collapses all.'
        )
        if not self.text_edit.toPlainText().strip():
            self.json_status.setText('等待解密结果，或粘贴 JSON' if zh else 'Waiting for content, or paste JSON')

    def _parse(self, text):
        try:
            return parse_json_text(text), None
        except ValueError as exc:
            return None, str(exc)
        except RecursionError:
            return None, 'JSON 嵌套过深' if self.language == 'zh' else 'JSON nesting is too deep'

    @staticmethod
    def _formatted(data):
        try:
            return json.dumps(data, ensure_ascii=False, indent=2).replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
        except RecursionError as exc:
            raise ValueError('JSON 嵌套过深，无法格式化') from exc

    def set_text(self, text, auto_format=True):
        self._refresh_timer.stop()
        raw_text = str(text or '')
        depth_too_deep = _estimate_json_depth(raw_text) > _MAX_AUTO_FORMAT_DEPTH if raw_text else False
        data, error = self._parse(raw_text)
        if error is None and auto_format:
            if depth_too_deep:
                error = (
                    f'JSON 嵌套超过 {_MAX_AUTO_FORMAT_DEPTH} 层，已保留原文以免卡顿'
                    if self.language == 'zh' else
                    f'JSON nesting exceeds {_MAX_AUTO_FORMAT_DEPTH} levels; kept raw text to keep UI responsive'
                )
            elif len(raw_text) > _LARGE_JSON_FOLD_LIMIT:
                # 超大文本默认保留原文不自动美化，避免主线程生成数十 MB 缩进文本冻结界面
                pass
            else:
                try:
                    raw_text = self._formatted(data)
                except (ValueError, RecursionError):
                    error = (
                        'JSON 嵌套过深，已保留原文（无法自动格式化）'
                        if self.language == 'zh' else
                        'JSON nesting is too deep; kept raw text without auto-formatting'
                    )
        text = raw_text.replace('\r\n', '\n').replace('\r', '\n')
        self._setting_text = True
        try:
            self.text_edit.setPlainText(text)
            if len(text) <= _LARGE_JSON_FOLD_LIMIT and not depth_too_deep:
                self.text_edit.rebuild_folds(expand_all=True)
        finally:
            self._setting_text = False
        self._refresh_document(text, data, error)
        return error is None

    def _on_text_changed(self):
        if not self._setting_text:
            self._refresh_timer.start()

    def _editor_source(self):
        return self.text_edit.document().toRawText().replace('\u2029', '\n')

    def _refresh_from_editor(self):
        text = self._editor_source()
        self._refresh_document(text, *self._parse(text))
        self._on_text_cursor()

    def _refresh_document(self, text, data, error):
        self._data = data
        self._source = text
        self._non_bmp = [match.start() for match in _NON_BMP.finditer(text)]
        self._syncing = True
        try:
            self.tree.clear()
            self._child_model.set_children([])
            self.path_value.setText('$')
            self._items.clear()
            self._painted_indices.clear()
            self._nodes, self._truncated = _source_nodes(text, self._non_bmp) if error is None else ([], False)
            self._node_starts = [node.start for node in self._nodes]
            if self._nodes:
                root = self._add_item(0)
                self.tree.setCurrentItem(root)
                self._fit_key_column()
        finally:
            self._syncing = False

        self._update_limit_hint()
        if error is not None:
            self.json_status.setText(error)
        else:
            count = len(self._nodes)
            folds = len(self.text_edit.fold_regions())
            self.json_status.setText(
                f'JSON 已识别 · {count} 个可浏览节点 · 格式化文本可折叠 {folds} 处 · 双击叶子复制'
                if self.language == 'zh' else
                f'Valid JSON · {count} browsable nodes · {folds} fold points · double-click leaf to copy'
            )
        self._search(self.search_edit.text())

    def _update_limit_hint(self):
        self.limit_hint.setText(
            f'树和子键值表最多展示 {_NODE_LIMIT:,} 个节点（含根节点），其余内容可在文本区查看。'
            if self.language == 'zh' else
            f'Tree and child table show up to {_NODE_LIMIT:,} nodes including the root. See the text pane for the rest.'
        )
        self.limit_hint.setVisible(self._truncated)

    def clear(self):
        self.set_text('')
        self.search_edit.clear()
        self.json_status.setText(
            '等待解密结果，或粘贴 JSON' if self.language == 'zh' else 'Waiting for content, or paste JSON'
        )

    def plain_text(self):
        return self.text_edit.toPlainText()

    def format_current(self):
        text = self._source if not self._refresh_timer.isActive() else self._editor_source()
        data, error = self._parse(text)
        if error is not None:
            show_warning(self, 'PengTools JSON', error)
            return False
        dups = find_duplicate_json_keys(text)
        if dups:
            preview = '、'.join(dups[:5]) + (' 等' if len(dups) > 5 else '')
            zh = self.language == 'zh'
            ok = confirm_action(
                self,
                '检测到重复键' if zh else 'Duplicate keys detected',
                (
                    f'当前 JSON 存在重复键（{preview}）。继续格式化将仅保留每个键最后一次出现的值，是否继续？'
                    if zh else
                    f'Duplicate JSON keys found ({preview}). Formatting keeps only the last value per key. Continue?'
                ),
                confirm_text='仍要格式化' if zh else 'Format anyway',
                danger=True,
            )
            if not ok:
                return False
        try:
            formatted = self._formatted(data)
        except (ValueError, RecursionError) as exc:
            msg = str(exc) if self.language == 'zh' else 'JSON nesting is too deep to format'
            self.json_status.setText(msg)
            show_warning(self, 'PengTools JSON', msg)
            return False
        return self.set_text(formatted, auto_format=False)

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

    _TYPE_COLORS = {
        'object': '#6C58D9',
        'array': '#0891B2',
        'string': '#047857',
        'number': '#1D4ED8',
        'boolean': '#B45309',
        'null': '#94A3B8',
    }

    def _build_item(self, index):
        from PyQt6.QtGui import QColor, QFont
        node = self._nodes[index]
        value = node.display_value()
        item = QTreeWidgetItem([node.key, node.type_name, _preview(value)])
        item.setData(0, Qt.ItemDataRole.UserRole, node.path)
        item.setData(0, _NODE_ROLE, index)
        item.setToolTip(0, f'{node.key}\n{node.path}')
        item.setToolTip(1, node.type_name)
        item.setToolTip(2, value)
        type_color = QColor(self._TYPE_COLORS.get(node.type_name, '#615D73'))
        item.setForeground(0, QColor('#262438'))
        item.setForeground(1, type_color)
        if node.type_name in ('object', 'array'):
            key_font = item.font(0)
            key_font.setBold(True)
            item.setFont(0, key_font)
            item.setForeground(2, QColor('#7C7691'))
        elif node.type_name == 'null':
            val_font = item.font(2)
            val_font.setItalic(True)
            item.setFont(2, val_font)
            item.setForeground(2, QColor('#94A3B8'))
        else:
            item.setForeground(2, type_color if node.type_name in ('number', 'boolean') else QColor('#334155'))
        if node.children:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self._items[index] = item
        return item

    def _add_item(self, index, parent=None):
        item = self._build_item(index)
        (parent.addChild(item) if parent is not None else self.tree.addTopLevelItem(item))
        return item

    def _load_children(self, item):
        if item.childCount():
            return
        children = self._nodes[item.data(0, _NODE_ROLE)].children
        if not children:
            return
        batch = [self._build_item(index) for index in children]
        item.addChildren(batch)

    def _focus_node(self, index):
        ancestors = []
        parent = self._nodes[index].parent
        while parent is not None:
            ancestors.append(parent)
            parent = self._nodes[parent].parent
        for ancestor in reversed(ancestors):
            item = self._items[ancestor]
            self._load_children(item)
            item.setExpanded(True)
        item = self._items[index]
        focus_tree_item(self.tree, item)
        return item

    def _clear_painted_tree_items(self):
        if not self._painted_indices:
            return
        for index in self._painted_indices:
            item = self._items.get(index)
            if item is not None:
                paint_tree_item(item, matched=False)
        self._painted_indices.clear()

    def _search(self, query):
        self.tree.setUpdatesEnabled(False)
        try:
            self._clear_painted_tree_items()
            self.text_edit.set_search_selections([])
            self._matches = []
            self._match_index = -1
            self._text_spans = []
            self._search_mode = 'text' if self.search_scope.currentIndex() == 0 else 'tree'
            needle = (query or '').strip()
            if needle and self._search_mode == 'text':
                non_bmp = self._non_bmp
                if self._refresh_timer.isActive():
                    non_bmp = [match.start() for match in _NON_BMP.finditer(self.plain_text())]
                self._text_spans = [
                    (start + bisect_left(non_bmp, start), end + bisect_left(non_bmp, end))
                    for start, end in collect_text_spans(self.text_edit, needle)
                ]
                self._matches = self._text_spans
            elif needle:
                folded = needle.casefold()
                matches = []
                for index, node in enumerate(self._nodes):
                    if (
                        folded in node.key.casefold()
                        or folded in node.path.casefold()
                        or folded in node.type_name.casefold()
                        or folded in node.display_value().casefold()
                    ):
                        matches.append(index)
                self._matches = matches
                # 大 JSON 搜索时限制同步着色的已展开节点数，避免上万次 setData 导致界面卡顿；
                # 完整匹配计数与上一条/下一条定位保持精确，当前聚焦节点始终着色。
                painted = 0
                for index in matches:
                    item = self._items.get(index)
                    if item is not None:
                        paint_tree_item(item, matched=True)
                        self._painted_indices.add(index)
                        painted += 1
                        if painted >= 300:
                            break

            if self._matches:
                self._match_index = 0
                self._focus_match(take_focus=False)
            self._update_search_status()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _update_search_status(self):
        current = self._match_index + 1 if self._matches else 0
        total = len(self._matches)
        mode = ''
        if total and self._search_mode == 'text':
            mode = ' 文本' if self.language == 'zh' else ' text'
        elif total and self._search_mode == 'tree':
            mode = ' 树' if self.language == 'zh' else ' tree'
        self.search_status.setText(f'{current} / {total}{mode}')

    def _move_match(self, delta):
        if not self._matches:
            return
        if self._search_mode == 'tree':
            old_idx = self._matches[self._match_index]
            old_item = self._items.get(old_idx)
            if old_item is not None:
                paint_tree_item(old_item, matched=True)
                self._painted_indices.add(old_idx)
        self._match_index = (self._match_index + delta) % len(self._matches)
        self._focus_match()
        self._update_search_status()

    def previous_match(self):
        self._move_match(-1)

    def next_match(self):
        self._move_match(1)

    def _focus_match(self, take_focus=True):
        if not self._matches or self._match_index < 0:
            return
        if self._search_mode == 'text':
            apply_text_match_index(self.text_edit, self._text_spans, self._match_index, take_focus=take_focus)
        else:
            idx = self._matches[self._match_index]
            item = self._focus_node(idx)
            paint_tree_item(item, matched=True, current=True)
            self._painted_indices.add(idx)
            self._locate_text(item)

    def _on_text_cursor(self):
        if self._syncing or not self._nodes or self._refresh_timer.isActive():
            return
        position = self.text_edit.textCursor().selectionStart()
        index = bisect_right(self._node_starts, position) - 1
        while index > 0 and self._nodes[index].end <= position:
            index = self._nodes[index].parent
        index = max(index, 0)
        if self.tree.currentItem() is self._items.get(index):
            return
        self._syncing = True
        try:
            self._focus_node(index)
        finally:
            self._syncing = False

    def _on_current_item_changed(self, current, _previous):
        self.path_value.setText(current.data(0, Qt.ItemDataRole.UserRole) if current else '$')
        children = self._nodes[current.data(0, _NODE_ROLE)].children if current else []
        self._child_model.set_children(children)
        if current is not None:
            self._locate_text(current)

    def _locate_text(self, item):
        if self._syncing or self._refresh_timer.isActive():
            return
        position = self._nodes[item.data(0, _NODE_ROLE)].start
        self._syncing = True
        try:
            ensure_text_pos_unfolded(self.text_edit, position)
            cursor = self.text_edit.textCursor()
            cursor.setPosition(position)
            self.text_edit.setTextCursor(cursor)
            self.text_edit.ensureCursorVisible()
        finally:
            self._syncing = False

    def _value_for_item(self, item):
        node = self._nodes[item.data(0, _NODE_ROLE)]
        return _DECODER.raw_decode(self._source, node.value_start)[0]

    def _copy_item_value(self, item, _column=0):
        if item and self._nodes[item.data(0, _NODE_ROLE)].type_name not in ('object', 'array'):
            self._copy_text(node_value_text(self._value_for_item(item)))

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return
        index = item.data(0, _NODE_ROLE)
        zh = self.language == 'zh'
        menu = QMenu(self)
        value_action = menu.addAction('复制节点值' if zh else 'Copy node value')
        json_action = menu.addAction('复制节点 JSON' if zh else 'Copy node JSON')
        path_action = menu.addAction('复制 JSONPath' if zh else 'Copy JSONPath')
        menu.addSeparator()
        expand_action = menu.addAction('展开此节点' if zh else 'Expand this node')
        collapse_action = menu.addAction('折叠此节点' if zh else 'Collapse this node')
        chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
        if self._items.get(index) is not item:
            return
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

    def _set_expanded_recursive(self, item, expanded):
        self.tree.setUpdatesEnabled(False)
        try:
            pending = [item]
            while pending:
                current = pending.pop()
                if expanded:
                    self._load_children(current)
                current.setExpanded(expanded)
                pending.extend(current.child(index) for index in range(current.childCount()))
        finally:
            self.tree.setUpdatesEnabled(True)

    def _expand_all(self):
        self.text_edit.expand_all_folds()
        if self.tree.topLevelItemCount():
            self._set_expanded_recursive(self.tree.topLevelItem(0), True)

    def _collapse_all(self):
        if hasattr(self.text_edit, 'collapse_all_folds'):
            self.text_edit.collapse_all_folds()
        self.tree.collapseAll()
        # 树保留根可见
        if self.tree.topLevelItemCount():
            root = self.tree.topLevelItem(0)
            root.setExpanded(False)

    def _toggle_glance(self, checked: bool):
        if hasattr(self.text_edit, 'set_glance_visible'):
            self.text_edit.set_glance_visible(bool(checked))

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
