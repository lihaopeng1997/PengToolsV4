# -*- coding: utf-8 -*-
"""MongoDB 工作台：数据库/集合树 + 文档浏览（表格/JSON）+ AI 助手 + Shell。

参考 MongoDB Compass。连接由 tools.db_connect.open_connection 建立（返回 database）。
"""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui.connection_dialog import ConnectionDialog
from tools.db_connect import (
    DbError, close_connection, delete_connection, load_connections, open_connection,
    upsert_connection,
)
from tools.db_mongo_ops import (
    count_docs, delete_docs, find_docs, insert_doc, list_collections, parse_mongo_query,
    sample_schema,
)
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_table
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_page_header, make_page_toolbar
from ui.splitter_prefs import install_splitter_prefs


class _MongoWorker(QThread):
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, kind: str, item: dict, **kwargs):
        super().__init__()
        self.kind = kind
        self.item = dict(item or {})
        self.kwargs = kwargs
        self.cancelled = False

    def run(self):
        conn = None
        try:
            conn = open_connection(self.item)
            if self.kind == 'test':
                # 测试不仅 ping，还校验目标库集合读取权限，杜绝测试成功但刷新报错
                target_database = str(self.item.get('database') or '').strip()
                if target_database and target_database != 'admin':
                    try:
                        conn.list_collection_names()
                    except Exception as perm_exc:
                        p_code = getattr(perm_exc, 'code', None)
                        p_low = str(perm_exc).lower()
                        if p_code == 13 or 'unauthorized' in p_low or 'not authorized' in p_low:
                            from tools.db_connect import clean_mongo_error_message
                            cleaned = clean_mongo_error_message(perm_exc)
                            raise DbError(
                                f"[AUTHZ_ERROR] 连接认证成功，但对数据库「{target_database}」授权不足（Unauthorized / code 13）："
                                f"缺少集合列表权限（listCollections）。\n原始错误：{cleaned}"
                            ) from perm_exc
                self.completed.emit('test', {'ok': True})
            elif self.kind == 'collections':
                colls = list_collections(conn)
                self.completed.emit('collections', {'collections': colls})
            elif self.kind == 'query':
                collection = self.kwargs.get('collection', '')
                filt = self.kwargs.get('filter', {})
                sort = self.kwargs.get('sort')
                projection = self.kwargs.get('projection')
                skip = int(self.kwargs.get('skip', 0))
                limit = int(self.kwargs.get('limit', 50))
                docs = find_docs(conn, collection, filt, sort, projection, skip, limit)
                total = count_docs(conn, collection, filt)
                self.completed.emit('query', {'docs': docs, 'total': total, 'limit': limit,
                                              'collection': collection})
            elif self.kind == 'schema':
                collection = self.kwargs.get('collection', '')
                fields = sample_schema(conn, collection)
                self.completed.emit('schema', {'collection': collection, 'fields': fields})
            elif self.kind == 'insert':
                collection = self.kwargs.get('collection', '')
                doc = self.kwargs.get('doc', {})
                inserted_id = insert_doc(conn, collection, doc)
                self.completed.emit('insert', {'inserted_id': inserted_id})
            elif self.kind == 'delete':
                collection = self.kwargs.get('collection', '')
                filt = self.kwargs.get('filter', {})
                n = delete_docs(conn, collection, filt)
                self.completed.emit('delete', {'deleted': n})
            elif self.kind == 'command':
                sql = self.kwargs.get('sql', '')
                q = parse_mongo_query(sql)
                collection = q.get('collection', '')
                docs = find_docs(conn, collection, q.get('filter', {}), q.get('sort'),
                                 q.get('projection'), 0, q.get('limit') or 50)
                self.completed.emit('command', {'docs': docs, 'collection': collection})
            else:
                raise DbError(f'未知任务：{self.kind}')
        except Exception as exc:
            self.failed.emit(self.kind, redact_error(str(exc)))
        finally:
            close_connection(conn)


class MongoDBWorkbenchPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._worker = None
        self._selected_db = ''
        self._selected_coll = ''
        self._docs = []
        self._columns = []
        self._offset = 0
        self._total = 0
        self._schema_fields = []
        self._setup_ui()
        self.set_language(language)
        self._reload_connections()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        header, self.page_title, self.page_subtitle = make_page_header(
            'MongoDB 工作台',
            '集合浏览 · 文档查询 · Shell',
            'database',
        )
        root.addWidget(header)

        toolbar, top = make_page_toolbar(divided=True)
        self.conn_combo = QComboBox()
        size_pick_combo(self.conn_combo)
        self.conn_combo.currentIndexChanged.connect(self._on_connection_changed)
        self.conn_new_btn = QPushButton()
        apply_button(self.conn_new_btn, 'secondary', compact=True)
        self.conn_new_btn.clicked.connect(lambda: self._edit_connection(new=True))
        self.conn_edit_btn = QPushButton()
        apply_button(self.conn_edit_btn, 'ghost', compact=True)
        self.conn_edit_btn.clicked.connect(lambda: self._edit_connection(new=False))
        self.conn_del_btn = QPushButton()
        apply_button(self.conn_del_btn, 'ghost', compact=True)
        self.conn_del_btn.clicked.connect(self._delete_connection)
        self.test_btn = QPushButton()
        apply_button(self.test_btn, 'secondary', compact=True)
        self.test_btn.clicked.connect(self._test_connection)
        self.refresh_btn = QPushButton()
        apply_button(self.refresh_btn, 'secondary', compact=True)
        self.refresh_btn.clicked.connect(self._refresh_collections)
        for w in (self.conn_combo, self.conn_new_btn, self.conn_edit_btn, self.conn_del_btn,
                  self.test_btn, self.refresh_btn):
            top.addWidget(w)
        top.addStretch(1)
        root.addWidget(toolbar)

        body = QSplitter(Qt.Orientation.Horizontal)

        # 左：数据库 → 集合树
        left = QFrame()
        left.setObjectName('dashboard-task-card')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(8, 8, 8, 8)
        self.coll_filter = QLineEdit()
        size_line(self.coll_filter, 'std')
        self.coll_filter.setPlaceholderText('搜索集合')
        self.coll_filter.textChanged.connect(self._on_filter_changed)
        left_l.addWidget(self.coll_filter)
        self.coll_tree = QTreeWidget()
        self.coll_tree.setHeaderHidden(True)
        self.coll_tree.setIndentation(14)
        self.coll_tree.itemClicked.connect(self._on_coll_clicked)
        left_l.addWidget(self.coll_tree, 1)
        self.coll_stats = QLabel()
        self.coll_stats.setObjectName('field-hint')
        left_l.addWidget(self.coll_stats)
        body.addWidget(left)

        # 右：Tab（文档视图 / AI 助手）
        right = QFrame()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        self.side_tabs = QTabWidget()

        # 文档视图
        docs_page = QWidget()
        docs_l = QVBoxLayout(docs_page)
        docs_l.setContentsMargins(10, 10, 10, 10)
        self.query_input = QPlainTextEdit()
        self.query_input.setMinimumHeight(60)
        self.query_input.setMaximumHeight(110)
        self.query_input.setPlaceholderText('Filter: {"字段":"值"} 或 db.coll.find({...})')
        docs_l.addWidget(self.query_input)
        qrow = QHBoxLayout()
        self.query_btn = QPushButton()
        apply_button(self.query_btn, 'primary', compact=True)
        self.query_btn.clicked.connect(self._run_query)
        self.reset_btn = QPushButton()
        apply_button(self.reset_btn, 'ghost', compact=True)
        self.reset_btn.clicked.connect(self._reset_query)
        self.view_mode_btn = QPushButton()
        apply_button(self.view_mode_btn, 'ghost', compact=True)
        self.view_mode_btn.clicked.connect(self._toggle_view_mode)
        qrow.addWidget(self.query_btn)
        qrow.addWidget(self.reset_btn)
        qrow.addStretch(1)
        qrow.addWidget(self.view_mode_btn)
        docs_l.addLayout(qrow)

        self.doc_table = QTableWidget()
        apply_table(self.doc_table, alternating=True)
        self.doc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.doc_table.horizontalHeader().setStretchLastSection(True)
        docs_l.addWidget(self.doc_table, 1)
        self.doc_json = QPlainTextEdit()
        self.doc_json.setReadOnly(True)
        self.doc_json.hide()
        docs_l.addWidget(self.doc_json, 1)

        page_row = QHBoxLayout()
        self.prev_btn = QPushButton()
        apply_button(self.prev_btn, 'ghost', compact=True)
        self.prev_btn.clicked.connect(lambda: self._shift_page(-1))
        self.page_label = QLabel()
        self.page_label.setObjectName('field-hint')
        self.next_btn = QPushButton()
        apply_button(self.next_btn, 'ghost', compact=True)
        self.next_btn.clicked.connect(lambda: self._shift_page(1))
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(self.page_label, 1)
        page_row.addWidget(self.next_btn)
        docs_l.addLayout(page_row)

        doc_actions = QHBoxLayout()
        self.insert_btn = QPushButton()
        apply_button(self.insert_btn, 'ghost', compact=True)
        self.insert_btn.clicked.connect(self._insert_doc)
        self.del_btn = QPushButton()
        apply_button(self.del_btn, 'ghost', compact=True)
        self.del_btn.clicked.connect(self._delete_selected)
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'ghost', compact=True)
        self.copy_btn.clicked.connect(self._copy_selected)
        for w in (self.insert_btn, self.del_btn, self.copy_btn):
            doc_actions.addWidget(w)
        doc_actions.addStretch(1)
        docs_l.addLayout(doc_actions)
        self.side_tabs.addTab(docs_page, '文档视图')

        # AI 助手
        ai_page = QWidget()
        ai_l = QVBoxLayout(ai_page)
        ai_l.setContentsMargins(10, 10, 10, 10)
        self.ai_banner = QLabel()
        self.ai_banner.setObjectName('page-context')
        self.ai_banner.setWordWrap(True)
        ai_l.addWidget(self.ai_banner)
        self.ai_schema = QLabel()
        self.ai_schema.setObjectName('field-hint')
        self.ai_schema.setWordWrap(True)
        ai_l.addWidget(self.ai_schema)
        self.ai_output = QPlainTextEdit()
        self.ai_output.setReadOnly(True)
        ai_l.addWidget(self.ai_output, 1)
        self.ai_input = QPlainTextEdit()
        self.ai_input.setMinimumHeight(120)
        self.ai_input.setMaximumHeight(200)
        ai_l.addWidget(self.ai_input)
        ai_send = QHBoxLayout()
        self.ai_send_btn = QPushButton()
        apply_button(self.ai_send_btn, 'secondary', compact=True)
        self.ai_send_btn.clicked.connect(self._ai_send)
        ai_send.addStretch(1)
        ai_send.addWidget(self.ai_send_btn)
        ai_l.addLayout(ai_send)
        self.side_tabs.addTab(ai_page, 'AI 助手')

        right_l.addWidget(self.side_tabs)
        body.addWidget(right)
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 3)
        install_splitter_prefs(
            body, defaults=[240, 720], page_id='mongodb-workbench', tab_id='main',
            min_sizes=[180, 400], accessible_name='MongoDB 左右分隔',
        )
        root.addWidget(body, 1)

        # 底部 Shell
        bottom = QFrame()
        bottom.setObjectName('dashboard-task-card')
        bottom_l = QVBoxLayout(bottom)
        bottom_l.setContentsMargins(8, 8, 8, 8)
        cmd_bar = QHBoxLayout()
        self.cmd_prompt = QLabel('mongo>')
        self.cmd_prompt.setObjectName('field-hint')
        self.cmd_input = QLineEdit()
        size_line(self.cmd_input, 'path')
        self.cmd_input.returnPressed.connect(self._run_command)
        self.cmd_btn = QPushButton()
        apply_button(self.cmd_btn, 'secondary', compact=True)
        self.cmd_btn.clicked.connect(self._run_command)
        cmd_bar.addWidget(self.cmd_prompt)
        cmd_bar.addWidget(self.cmd_input, 1)
        cmd_bar.addWidget(self.cmd_btn)
        bottom_l.addLayout(cmd_bar)
        self.cmd_output = QPlainTextEdit()
        self.cmd_output.setReadOnly(True)
        self.cmd_output.setMinimumHeight(160)
        bottom_l.addWidget(self.cmd_output, 1)
        root.addWidget(bottom, 0)
        self._root_layout = root

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('MongoDB 工作台' if zh else 'MongoDB Workbench')
        self.page_subtitle.setText('集合浏览 · 文档查询 · Shell' if zh else 'Collections · documents · shell')
        self.conn_new_btn.setText('新建连接' if zh else 'New')
        self.conn_edit_btn.setText('编辑' if zh else 'Edit')
        self.conn_del_btn.setText('删除' if zh else 'Delete')
        self.test_btn.setText('测试连接' if zh else 'Test')
        self.refresh_btn.setText('刷新' if zh else 'Refresh')
        self.coll_filter.setPlaceholderText('搜索集合' if zh else 'Search collections')
        self.query_input.setPlaceholderText(
            'Filter: {"字段":"值"} 或 db.coll.find({...})' if zh else 'Filter JSON or db.coll.find({...})'
        )
        self.query_btn.setText('执行查询' if zh else 'Run query')
        self.reset_btn.setText('重置' if zh else 'Reset')
        self.view_mode_btn.setText('JSON 视图' if zh else 'JSON view')
        self.prev_btn.setText('上一页' if zh else 'Prev')
        self.next_btn.setText('下一页' if zh else 'Next')
        self.insert_btn.setText('插入文档' if zh else 'Insert doc')
        self.del_btn.setText('删除匹配' if zh else 'Delete match')
        self.copy_btn.setText('复制选中' if zh else 'Copy selected')
        self.side_tabs.setTabText(0, '文档视图' if zh else 'Documents')
        self.side_tabs.setTabText(1, 'AI 助手' if zh else 'AI assistant')
        self.ai_send_btn.setText('发送' if zh else 'Send')
        self.ai_input.setPlaceholderText(
            '描述查询需求，AI 基于当前集合字段结构生成 find/aggregation（不会自动执行）'
            if zh else 'Describe query; AI drafts find/aggregation from schema (never auto-runs)'
        )
        self.cmd_btn.setText('执行' if zh else 'Run')
        self.cmd_input.setPlaceholderText(
            '输入 MongoDB Shell 命令，如 db.policies.find({"status":"active"}).limit(10)'
            if zh else 'MongoDB shell, e.g. db.policies.find({"status":"active"})'
        )

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.page_subtitle, low_height)

    def _title(self) -> str:
        return 'MongoDB 工作台' if self.language == 'zh' else 'MongoDB Workbench'

    # ── 连接管理 ──────────────────────────────────────────────────────────

    def _reload_connections(self, select_id: str = ''):
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        rows = [item for item in load_connections() if str(item.get('dialect') or '').lower() == 'mongodb']
        if not rows:
            self.conn_combo.addItem(
                '无 MongoDB 连接，点击“新建”创建' if self.language == 'zh' else 'No MongoDB connection', None
            )
        for item in rows:
            self.conn_combo.addItem(str(item.get('name') or item.get('id')), item)
            if select_id and item.get('id') == select_id:
                self.conn_combo.setCurrentIndex(self.conn_combo.count() - 1)
        self.conn_combo.blockSignals(False)
        self._on_connection_changed()

    def _current_conn(self) -> dict | None:
        data = self.conn_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _on_connection_changed(self):
        item = self._current_conn()
        if item:
            self._selected_db = str(item.get('database') or '')
            self.cmd_prompt.setText(f'{self._selected_db or "mongo"}>')
            self._refresh_collections()
        else:
            self.coll_tree.clear()
            self.coll_stats.setText('')
        self._update_ai_banner()

    def _edit_connection(self, new=False):
        current = None if new else self._current_conn()
        dialog = ConnectionDialog(self.language, current, self, locked_dialect='mongodb')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item, password = dialog.payload()
        saved = upsert_connection(item, password if password else None)
        self._reload_connections(saved.get('id'))

    def _delete_connection(self):
        item = self._current_conn()
        if not item:
            return
        zh = self.language == 'zh'
        if not confirm_action(self, self._title(),
                              f"删除 MongoDB 连接「{item.get('name') or ''}」？"
                              if zh else f"Delete MongoDB connection '{item.get('name') or ''}'?",
                              confirm_text='删除' if zh else 'Delete', danger=True):
            return
        delete_connection(item.get('id'))
        self._reload_connections()

    def _test_connection(self):
        item = self._current_conn()
        if not item:
            show_warning(self, self._title(), '请先选择连接' if self.language == 'zh' else 'Pick a connection')
            return
        self._run_worker('test', item)

    # ── 集合树 ────────────────────────────────────────────────────────────

    def _refresh_collections(self):
        item = self._current_conn()
        if not item:
            return
        self._run_worker('collections', item)

    def _on_filter_changed(self, text):
        needle = text.strip().lower()
        for i in range(self.coll_tree.topLevelItemCount()):
            item = self.coll_tree.topLevelItem(i)
            item.setHidden(bool(needle) and needle not in item.text(0).lower())

    def _render_collections(self, collections: list[str]):
        self.coll_tree.blockSignals(True)
        self.coll_tree.clear()
        db = self._selected_db or 'db'
        root = QTreeWidgetItem([f'{db} ({len(collections)} colls)'])
        root.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'db', 'name': db})
        for coll in collections:
            child = QTreeWidgetItem([coll])
            child.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'collection', 'name': coll})
            root.addChild(child)
        self.coll_tree.addTopLevelItem(root)
        root.setExpanded(True)
        self.coll_tree.blockSignals(False)
        self.coll_stats.setText(f'{len(collections)} 集合' if self.language == 'zh' else f'{len(collections)} collections')

    def _on_coll_clicked(self, item: QTreeWidgetItem, _column: int):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get('kind') != 'collection':
            return
        self._selected_coll = str(meta.get('name') or '')
        self._offset = 0
        self._reset_query()
        self._load_schema()

    def _load_schema(self):
        item = self._current_conn()
        if item and self._selected_coll:
            self._run_worker('schema', item, collection=self._selected_coll)

    # ── 文档查询 ──────────────────────────────────────────────────────────

    def _run_query(self):
        coll = self._selected_coll
        raw = self.query_input.toPlainText().strip()
        if not coll and raw:
            try:
                parsed = parse_mongo_query(raw)
                extracted = parsed.get('collection') or ''
                if extracted:
                    self._selected_coll = extracted
                    coll = extracted
            except Exception:
                pass
        if not self._selected_coll:
            show_warning(self, self._title(), '请先选择集合或在命令中指定集合（如 db.my_coll.find()）' if self.language == 'zh' else 'Pick a collection or specify in query (e.g. db.my_coll.find())')
            return
        item = self._current_conn()
        if not item:
            return
        raw = self.query_input.toPlainText().strip()
        filt = {}
        sort = None
        projection = None
        limit = 50
        if raw:
            try:
                q = parse_mongo_query(raw)
                filt = q.get('filter', {})
                sort = q.get('sort')
                projection = q.get('projection')
                if q.get('limit'):
                    limit = q['limit']
            except DbError as exc:
                show_error(self, self._title(), str(exc))
                return
        self._offset = 0
        self._run_worker('query', item, collection=self._selected_coll, filter=filt,
                         sort=sort, projection=projection, skip=0, limit=limit)

    def _reset_query(self):
        self.query_input.clear()
        self._offset = 0
        item = self._current_conn()
        if item and self._selected_coll:
            self._run_worker('query', item, collection=self._selected_coll, filter={},
                             skip=0, limit=50)

    def _shift_page(self, delta: int):
        new_offset = self._offset + delta * 50
        if new_offset < 0:
            new_offset = 0
        item = self._current_conn()
        if not item or not self._selected_coll:
            return
        raw = self.query_input.toPlainText().strip()
        filt = {}
        if raw:
            try:
                filt = parse_mongo_query(raw).get('filter', {})
            except DbError:
                filt = {}
        self._offset = new_offset
        self._run_worker('query', item, collection=self._selected_coll, filter=filt,
                         skip=new_offset, limit=50)

    def _render_docs(self, docs: list[dict], total: int = 0, limit: int = 50):
        self._docs = docs
        self._total = total if total else len(docs)
        # 提取列（保留顺序，去重）
        columns = []
        for doc in docs:
            if isinstance(doc, dict):
                for key in doc.keys():
                    if str(key) not in columns:
                        columns.append(str(key))
        self._columns = columns or ['_id']
        self.doc_table.setColumnCount(len(self._columns))
        self.doc_table.setHorizontalHeaderLabels(self._columns)
        self.doc_table.setRowCount(0)
        for doc in docs:
            row = self.doc_table.rowCount()
            self.doc_table.insertRow(row)
            for ci, col in enumerate(self._columns):
                val = doc.get(col, '') if isinstance(doc, dict) else ''
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                cell = QTableWidgetItem(str(val))
                if ci == 0:
                    cell.setForeground(Qt.GlobalColor.darkCyan)
                self.doc_table.setItem(row, ci, cell)
        self.page_label.setText(
            f'第 {self._offset // limit + 1} 页 · 显示 {len(docs)} / {self._total}'
            if self.language == 'zh' else
            f'Page {self._offset // limit + 1} · {len(docs)} / {self._total}'
        )
        # JSON 视图
        try:
            self.doc_json.setPlainText(json.dumps(docs, ensure_ascii=False, indent=2, default=str))
        except Exception:
            self.doc_json.setPlainText(str(docs))

    def _toggle_view_mode(self):
        table_visible = self.doc_table.isVisible()
        self.doc_table.setVisible(not table_visible)
        self.doc_json.setVisible(table_visible)
        self.view_mode_btn.setText(
            '表格视图' if table_visible else ('JSON 视图' if self.language == 'zh' else 'JSON view')
        )

    def _insert_doc(self):
        if not self._selected_coll:
            show_warning(self, self._title(), '请先选择集合' if self.language == 'zh' else 'Pick a collection')
            return
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(
            self,
            '插入文档' if self.language == 'zh' else 'Insert document',
            'JSON 对象：' if self.language == 'zh' else 'JSON object:',
            '{"字段": "值"}',
        )
        if not ok or not text.strip():
            return
        try:
            doc = json.loads(text)
        except ValueError as exc:
            show_error(self, self._title(), 'JSON 解析失败：' + str(exc))
            return
        if not isinstance(doc, dict):
            show_error(self, self._title(), '必须是 JSON 对象')
            return
        item = self._current_conn()
        if item:
            self._run_worker('insert', item, collection=self._selected_coll, doc=doc)

    def _delete_selected(self):
        if not self._selected_coll:
            show_warning(self, self._title(), '请先选择集合' if self.language == 'zh' else 'Pick a collection')
            return
        raw = self.query_input.toPlainText().strip()
        filt = {}
        if raw:
            try:
                filt = parse_mongo_query(raw).get('filter', {})
            except DbError:
                filt = {}
        zh = self.language == 'zh'
        if not confirm_action(self, self._title(),
                              f'删除集合「{self._selected_coll}」中匹配 {json.dumps(filt, ensure_ascii=False)} 的文档？'
                              if zh else f'Delete docs matching {json.dumps(filt)}?',
                              confirm_text='删除' if zh else 'Delete', danger=True):
            return
        item = self._current_conn()
        if item:
            self._run_worker('delete', item, collection=self._selected_coll, filter=filt)

    def _copy_selected(self):
        if not self._docs:
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(json.dumps(self._docs, ensure_ascii=False, indent=2, default=str))

    # ── Shell ─────────────────────────────────────────────────────────────

    def _run_command(self):
        sql = self.cmd_input.text().strip()
        if not sql:
            return
        from tools.sql_guard import reject_reason
        reason = reject_reason(sql, 'mongodb')
        if reason:
            show_warning(self, self._title(), reason)
            return
        item = self._current_conn()
        if not item:
            show_warning(self, self._title(), '请先选择连接' if self.language == 'zh' else 'Pick a connection')
            return
        self._append_cmd(f'{self.cmd_prompt.text()} {sql}')
        self._run_worker('command', item, sql=sql)
        self.cmd_input.clear()

    def _append_cmd(self, line: str):
        self.cmd_output.appendPlainText(line)

    # ── AI 助手 ───────────────────────────────────────────────────────────

    def _update_ai_banner(self):
        zh = self.language == 'zh'
        if self._selected_coll:
            self.ai_banner.setText(
                f'📌 当前上下文：集合「{self._selected_coll}」· 数据库「{self._selected_db}」'
            )
        else:
            item = self._current_conn()
            if item:
                self.ai_banner.setText(
                    f'📌 已连接 {item.get("name") or item.get("host")}，点击左侧集合后 AI 可感知字段结构'
                    if zh else f'📌 Connected to {item.get("name") or item.get("host")}. Select a collection for AI context.'
                )
            else:
                self.ai_banner.setText('📌 未连接' if zh else 'Not connected')

    def _ai_send(self):
        text = self.ai_input.toPlainText().strip()
        if not text:
            return
        from tools.intranet_llm import chat_completions, is_enabled, load_ai_local
        if not is_enabled():
            self.ai_output.setPlainText(
                '未启用内网模型，请先在设置中配置。' if self.language == 'zh' else 'No intranet model configured.'
            )
            return
        cfg = load_ai_local()
        context = ''
        if self._selected_coll:
            fields = '、'.join(self._schema_fields) if self._schema_fields else '未知'
            context = f'当前集合: {self._selected_coll}，数据库: {self._selected_db}，字段结构: {fields}。'
        system = (
            '你是内网 MongoDB 助手。基于用户描述生成 MongoDB find/filter 或 aggregation pipeline，'
            '只输出查询代码和简短说明，不要自动执行，不要包含 drop/删除生产数据。'
        )
        self.ai_output.setPlainText('生成中…' if self.language == 'zh' else 'Generating…')
        try:
            reply = chat_completions(
                [{'role': 'system', 'content': system},
                 {'role': 'user', 'content': context + text}],
                cfg=cfg,
            )
        except Exception as exc:
            reply = redact_error(str(exc))
        self.ai_output.setPlainText(reply)

    # ── Worker 调度 ───────────────────────────────────────────────────────

    def _run_worker(self, kind, item, **kwargs):
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _MongoWorker(kind, item, **kwargs)
        self._worker.completed.connect(self._on_worker_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _on_worker_done(self, kind: str, payload):
        if kind == 'test':
            show_info(self, self._title(), '连接成功' if self.language == 'zh' else 'Connection OK')
        elif kind == 'collections':
            self._render_collections(list(payload.get('collections') or []))
        elif kind == 'query':
            self._render_docs(list(payload.get('docs') or []), int(payload.get('total') or 0),
                              int(payload.get('limit') or 50))
        elif kind == 'schema':
            self._schema_fields = list(payload.get('fields') or [])
            self.ai_schema.setText('Schema: ' + '、'.join(self._schema_fields))
            self._update_ai_banner()
        elif kind == 'insert':
            self._reset_query()
            show_info(self, self._title(),
                      f"已插入，_id = {payload.get('inserted_id')}" if self.language == 'zh'
                      else f"Inserted, _id = {payload.get('inserted_id')}")
        elif kind == 'delete':
            self._reset_query()
            show_info(self, self._title(),
                      f"已删除 {payload.get('deleted')} 条" if self.language == 'zh'
                      else f"Deleted {payload.get('deleted')} docs")
        elif kind == 'command':
            docs = list(payload.get('docs') or [])
            try:
                text = json.dumps(docs, ensure_ascii=False, indent=2, default=str)
            except Exception:
                text = str(docs)
            self.cmd_output.appendPlainText(text)

    def _render_unauthorized_collections(self, error: str):
        self.coll_tree.blockSignals(True)
        self.coll_tree.clear()
        db = self._selected_db or 'db'
        root = QTreeWidgetItem([f'{db} (未授权 listCollections)' if self.language == 'zh' else f'{db} (unauthorized listCollections)'])
        root.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'db', 'name': db})
        hint_child = QTreeWidgetItem([
            '⚠️ 授权不足，请在右侧直接输入集合名查询' if self.language == 'zh' else '⚠️ Unauthorized, specify collection name in query directly'
        ])
        hint_child.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'hint'})
        root.addChild(hint_child)
        self.coll_tree.addTopLevelItem(root)
        root.setExpanded(True)
        self.coll_tree.blockSignals(False)
        self.coll_stats.setText(
            '集合列表未授权 (code 13)' if self.language == 'zh' else 'Unauthorized listCollections (code 13)'
        )

    def _on_worker_failed(self, kind: str, error: str):
        if kind == 'command':
            self.cmd_output.appendPlainText(f'错误: {error}')
        elif kind == 'collections' and ('[AUTHZ_ERROR]' in error or 'code 13' in error or 'unauthorized' in error.lower() or 'not authorized' in error.lower()):
            self._render_unauthorized_collections(error)
            show_warning(self, self._title(), error)
        else:
            show_error(self, self._title(), error)
