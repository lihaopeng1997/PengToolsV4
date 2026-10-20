# -*- coding: utf-8 -*-
"""MongoDB 工作台：数据库/集合树 + 文档浏览（表格/JSON）+ AI 助手 + Shell。

参考 MongoDB Compass。连接由 tools.db_connect.open_connection 建立（返回 database）。
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy

from bson import json_util
from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions

from tools.data_center.mongo_transfer import apply_import, export_documents, export_query, prepare_import

from PyQt6.QtCore import QDateTime, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui.connection_dialog import ConnectionDialog
from tools.db_connect import (
    DbError, close_connection, delete_connection, load_connections, open_connection,
    upsert_connection,
)
from tools.db_mongo_ops import (
    delete_docs, find_docs, insert_doc, list_collections, parse_mongo_query,
    sample_schema,
)
from tools.data_center.nosql_workspace import NoSqlWorkspaceCodecError, capture_mongo_run
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.workspace_store import WorkspaceTarget, WorkspaceTabKind
from tools.data_center.mongo_document import make_document_target, load_document, prepare_edit, apply_edit
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_table
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_page_header, make_page_toolbar
from ui.splitter_prefs import install_splitter_prefs
from ui.query_status_band import QueryStatusBand
from ui.wrap_layout import WrapLayout
from ui.data_center.nosql_query_history import (
    NoSqlDraft,
    NoSqlDraftTabs,
    NoSqlHistoryError,
    make_nosql_draft,
    make_nosql_history_capture,
    open_nosql_history_dialog,
    restore_nosql_history_record,
    validate_nosql_target,
)


_ACTIVE_MONGO_WORKERS = set()
_DISPLAY_JSON_OPTIONS = json_util.RELAXED_JSON_OPTIONS.with_options(uuid_representation=UuidRepresentation.STANDARD)


def _release_active_mongo_worker(worker):
    _ACTIVE_MONGO_WORKERS.discard(worker)


class _MongoWorker(QThread):
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    progress = pyqtSignal(int, int)

    def __init__(self, kind: str, item: dict, *, nosql_draft_id: str = "", connection_opener=None, query_snapshot=None, database_name="", **kwargs):
        super().__init__()
        self.kind = kind
        self.item = dict(item or {})
        self.connection_opener = connection_opener or open_connection
        self.kwargs = kwargs
        self.cancelled = False
        self._nosql_draft_id = str(nosql_draft_id or "")
        self.query_snapshot = query_snapshot
        self.database_name = database_name
        self.mutation_started = False

    @property
    def nosql_draft_id(self) -> str:
        return self._nosql_draft_id

    def run(self):
        conn = None
        owner = None
        try:
            if self.cancelled:
                return
            if self.kind == 'prepare_edit':
                plan = prepare_edit(self.kwargs['loaded'], self.kwargs['edited_text'])
                self._emit_completed(self.kind, plan)
                return
            if self.kind == 'prepare_import':
                plan = prepare_import(self.kwargs['path'], self.kwargs['target'], cancel=lambda: self.cancelled)
                self._emit_completed(self.kind, plan)
                return
            if self.kind == 'export_documents':
                result = export_documents(**self.kwargs, cancel=lambda: self.cancelled, progress=self._transfer_progress)
                self.completed.emit(self.kind, result)
                return
            owner = self.connection_opener(self.item)
            conn = owner
            if self.database_name and getattr(owner, 'name', None) != self.database_name:
                client = getattr(owner, 'client', None)
                if client is None:
                    raise DbError('MONGO_DATABASE_SCOPE_UNAVAILABLE')
                conn = client[self.database_name]
            if self.cancelled:
                return
            if self.kind == 'test':
                target_database = str(self.item.get('database') or 'admin').strip() or 'admin'
                try:
                    cols = conn.list_collection_names()
                    self._emit_completed('test', {'ok': True, 'collections_count': len(cols)})
                except Exception as perm_exc:
                    from tools.db_connect import mongo_operation_error
                    raise mongo_operation_error(
                        perm_exc,
                        item=self.item,
                        operation='listCollections',
                        database=target_database,
                    ) from perm_exc
            elif self.kind == 'collections':
                colls = list_collections(conn, item=self.item)
                self._emit_completed('collections', {'collections': colls})
            elif self.kind == 'query':
                collection = self.kwargs.get('collection', '')
                filt = self.kwargs.get('filter', {})
                sort = self.kwargs.get('sort')
                projection = self.kwargs.get('projection')
                skip = int(self.kwargs.get('skip', 0))
                limit = int(self.kwargs.get('limit', 50))
                docs = find_docs(conn, collection, filt, sort, projection, skip, limit, item=self.item)
                if self.cancelled:
                    return
                has_more = len(docs) > limit
                docs = docs[:limit]
                self._emit_completed('query', {'docs': docs, 'row_count': len(docs),
                                               'has_more': has_more, 'skip': skip, 'limit': limit,
                                               'collection': collection,
                                               'representation': getattr(conn, 'codec_options', CodecOptions()).uuid_representation})
            elif self.kind == 'schema':
                collection = self.kwargs.get('collection', '')
                fields = sample_schema(conn, collection, item=self.item)
                self._emit_completed('schema', {'collection': collection, 'fields': fields})
            elif self.kind == 'insert':
                collection = self.kwargs.get('collection', '')
                doc = self.kwargs.get('doc', {})
                inserted_id = insert_doc(conn, collection, doc, item=self.item)
                self._emit_completed('insert', {'inserted_id': inserted_id})
            elif self.kind == 'delete':
                collection = self.kwargs.get('collection', '')
                filt = self.kwargs.get('filter', {})
                cnt = delete_docs(conn, collection, filt, item=self.item)
                self._emit_completed('delete', {'deleted': cnt, 'deleted_count': cnt})
            elif self.kind == 'document_load':
                self._emit_completed(self.kind, load_document(conn, self.kwargs['context']))
            elif self.kind == 'document_update':
                self.mutation_started = True
                result = apply_edit(conn, self.kwargs['plan'], authorized=self.kwargs.get('authorized') is True)
                # A stop request cannot erase an acknowledged write outcome.
                self.completed.emit(self.kind, {**result, 'stop_requested': self.cancelled})
            elif self.kind == 'document_import':
                self.mutation_started = True
                result = apply_import(conn, self.kwargs['plan'], authorized=self.kwargs.get('authorized') is True,
                                      cancel=lambda: self.cancelled, progress=self._transfer_progress)
                self.completed.emit(self.kind, result)
            elif self.kind == 'export_query':
                result = export_query(conn, self.kwargs['snapshot'], self.kwargs['destination'],
                                      overwrite=self.kwargs.get('overwrite') is True,
                                      cancel=lambda: self.cancelled, progress=self._transfer_progress)
                self.completed.emit(self.kind, result)
            elif self.kind == 'command':
                collection = self.kwargs.get('collection', '')
                filt = self.kwargs.get('filter', {})
                sort = self.kwargs.get('sort')
                projection = self.kwargs.get('projection')
                skip = int(self.kwargs.get('skip', 0))
                limit = int(self.kwargs.get('limit', 50))
                docs = find_docs(conn, collection, filt, sort, projection, skip, limit, item=self.item)
                self._emit_completed('command', {'docs': docs, 'collection': collection})
            else:
                raise DbError(f'未知任务：{self.kind}')
        except Exception as exc:
            if not self.cancelled or self.kind in ('document_update', 'document_import') and self.mutation_started:
                self.failed.emit(self.kind, redact_error(str(exc)))
        finally:
            if owner is not None:
                close_connection(owner)

    def _transfer_progress(self, completed, total):
        if completed % 100 == 0 or total and completed == total:
            self.progress.emit(completed, total)

    def _emit_completed(self, kind, payload):
        if not self.cancelled:
            self.completed.emit(kind, payload)


class MongoDBWorkbenchPanel(QWidget):
    def __init__(self, language='zh', *, data_center_agent_options=None, data_center_history_options=None):
        super().__init__()
        self.language = language
        history_options = dict(data_center_history_options or {})
        self._nosql_profile_host = history_options.pop('profile_host', None) or DataCenterProfileHost()
        self._nosql_history_service = history_options.pop('history_service', None)
        self._nosql_draft_sequence = 0
        self._worker = None
        self._legacy_workers = set()
        self._workbench_shutdown_requested = False
        self._selected_db = ''
        self._selected_coll = ''
        self._docs = []
        self._columns = []
        self._offset = 0
        self._total = None
        self._page_has_more = False
        self._page_size = 50
        self._result_draft_id = ""
        self._result_snapshot = None
        self._page_stale = False
        self._page_read_at = ""
        self._document_to_open = None
        self._document_dialog = None
        self._transfer_dialog = None
        self._result_representation = UuidRepresentation.STANDARD
        self._schema_fields = []
        self._setup_ui()
        self.set_language(language)
        self._reload_connections()
        self._create_nosql_draft()
        from ui.data_center.integration import DataCenterWorkbenchAgent
        self.data_center_agent = DataCenterWorkbenchAgent(
            selection_provider=self._data_center_agent_selection,
            put_sql=self._put_data_center_agent_query,
            language=language,
            **dict(data_center_agent_options or {}),
            parent=self,
        )
        self.side_tabs.addTab(self.data_center_agent, '只读 Agent' if language == 'zh' else 'Read-only Agent')
        self.conn_combo.currentIndexChanged.connect(self.data_center_agent.target_changed)

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
        self.query_status_band = QueryStatusBand(self)
        root.addWidget(self.query_status_band)
        self.nosql_draft_tabs = NoSqlDraftTabs('mongo', self.language, self)
        self.nosql_draft_tabs.draft_about_to_change.connect(self._save_nosql_draft_text)
        self.nosql_draft_tabs.draft_changed.connect(self._load_nosql_draft_text)
        self.nosql_draft_tabs.new_requested.connect(self._create_nosql_draft)
        self.nosql_draft_tabs.history_requested.connect(self._open_nosql_history)
        root.addWidget(self.nosql_draft_tabs)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName('mongo-main-splitter')

        # 左：数据库 → 集合树
        left = QFrame()
        left.setObjectName('mongo-tree-card')
        self.tree_card = left
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
        right.setObjectName('mongo-workspace-card')
        self.workspace_card = right
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        self.side_tabs = QTabWidget()
        self.side_tabs.setObjectName('module-tabs')

        # 文档视图
        docs_page = QWidget()
        docs_l = QVBoxLayout(docs_page)
        docs_l.setContentsMargins(10, 10, 10, 10)
        self.query_input = QPlainTextEdit()
        self.query_input.setObjectName('mongo-query-edit')
        self.query_input.setFixedHeight(96)
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
        self.doc_table.setMinimumHeight(120)
        self.doc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.doc_table.horizontalHeader().setStretchLastSection(True)
        docs_l.addWidget(self.doc_table, 1)
        self.doc_json = QPlainTextEdit()
        self.doc_json.setObjectName('mongo-doc-edit')
        self.doc_json.setReadOnly(True)
        self.doc_json.setMinimumHeight(120)
        self.doc_json.hide()
        docs_l.addWidget(self.doc_json, 1)

        page_row = QHBoxLayout()
        self.prev_btn = QPushButton()
        apply_button(self.prev_btn, 'ghost', compact=True)
        self.prev_btn.clicked.connect(lambda: self._shift_page(-1))
        self.page_label = QLabel()
        self.page_label.setWordWrap(True)
        self.page_label.setObjectName('field-hint')
        self.next_btn = QPushButton()
        apply_button(self.next_btn, 'ghost', compact=True)
        self.next_btn.clicked.connect(lambda: self._shift_page(1))
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(self.page_label, 1)
        page_row.addWidget(self.next_btn)
        docs_l.addLayout(page_row)

        doc_actions = WrapLayout(spacing=6)
        self.insert_btn = QPushButton()
        apply_button(self.insert_btn, 'ghost', compact=True)
        self.insert_btn.clicked.connect(self._insert_doc)
        self.del_btn = QPushButton()
        apply_button(self.del_btn, 'ghost', compact=True)
        self.del_btn.clicked.connect(self._delete_selected)
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'ghost', compact=True)
        self.copy_btn.clicked.connect(self._copy_selected)
        self.edit_doc_btn = QPushButton()
        apply_button(self.edit_doc_btn, 'ghost', compact=True)
        self.edit_doc_btn.clicked.connect(self._edit_document)
        self.import_docs_btn = QPushButton()
        self.export_docs_btn = QPushButton()
        for button in (self.import_docs_btn, self.export_docs_btn):
            apply_button(button, 'ghost', compact=True)
        self.import_docs_btn.clicked.connect(lambda: self._open_transfer(False))
        self.export_docs_btn.clicked.connect(lambda: self._open_transfer(True))
        self.doc_table.itemSelectionChanged.connect(self._sync_pager)
        for w in (self.insert_btn, self.edit_doc_btn, self.del_btn, self.copy_btn, self.import_docs_btn, self.export_docs_btn):
            doc_actions.addWidget(w)
        docs_l.addLayout(doc_actions)
        docs_scroll = QScrollArea()
        docs_scroll.setWidgetResizable(True)
        docs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        docs_scroll.setWidget(docs_page)
        self.side_tabs.addTab(docs_scroll, '文档视图')

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
        # 与 Redis 一致：矮窗口时页内滚动，不撑高整个工作台
        ai_scroll = QScrollArea()
        ai_scroll.setWidgetResizable(True)
        ai_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ai_scroll.setWidget(ai_page)
        self.side_tabs.addTab(ai_scroll, 'AI 助手')

        right_l.addWidget(self.side_tabs)
        body.addWidget(right)
        body.setHandleWidth(16)
        body.setProperty('prismGutter', True)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        self.body_splitter = body
        install_splitter_prefs(
            body, defaults=[240, 720], page_id='mongodb-workbench', tab_id='main',
            defaults_for_extent=lambda extent: [240, max(360, extent - 240)],
            min_sizes=[180, 400], accessible_name='MongoDB 左右分隔',
        )
        root.addWidget(body, 1)

        # Keep the existing Shell controls inside the workspace tabs.
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
        self.side_tabs.addTab(bottom, 'Shell')
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
        self._update_page_label()
        self.insert_btn.setText('插入文档' if zh else 'Insert doc')
        self.edit_doc_btn.setText('修改文档' if zh else 'Edit document')
        self.import_docs_btn.setText('导入 JSON' if zh else 'Import JSON')
        self.export_docs_btn.setText('导出 JSON' if zh else 'Export JSON')
        self.del_btn.setText('删除匹配' if zh else 'Delete match')
        self.copy_btn.setText('复制选中' if zh else 'Copy selected')
        self.side_tabs.setTabText(0, '文档视图' if zh else 'Documents')
        self.side_tabs.setTabText(1, 'AI 助手' if zh else 'AI assistant')
        if hasattr(self, 'data_center_agent'):
            self.side_tabs.setTabText(3, '只读 Agent' if zh else 'Read-only Agent')
            self.data_center_agent.set_language(language)
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
        if hasattr(self, 'nosql_draft_tabs'):
            self.nosql_draft_tabs.set_language(language)

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import page_spacing_for_mode, set_subtitle_visible
        from ui.splitter_prefs import layout_bucket
        self._layout_mode = mode
        if hasattr(self, '_root_layout') and self._root_layout is not None:
            self._root_layout.setSpacing(page_spacing_for_mode(mode, low_height))
        set_subtitle_visible(self.page_subtitle, low_height or mode == 'narrow')
        if hasattr(self, 'body_splitter') and self.body_splitter is not None:
            col_mins = [180, 360] if mode in ('narrow', 'compact') else [200, 400]
            col_defs = [240, 560] if mode in ('narrow', 'compact') else [240, 720]
            install_splitter_prefs(
                self.body_splitter,
                defaults=col_defs,
                defaults_for_extent=lambda extent: [240, max(360, extent - 240)],
                page_id='mongodb-workbench',
                tab_id='main',
                bucket=layout_bucket(mode),
                min_sizes=col_mins,
                accessible_name='MongoDB 左右分隔',
            )

    def _title(self) -> str:
        return 'MongoDB 工作台' if self.language == 'zh' else 'MongoDB Workbench'

    # ── 连接管理 ──────────────────────────────────────────────────────────

    def _reload_connections(self, select_id: str = ''):
        try:
            rows = [item for item in load_connections() if str(item.get('dialect') or '').lower() == 'mongodb']
        except Exception:
            zh = self.language == 'zh'
            show_error(
                self, self._title(),
                '连接配置读取失败；当前选择保持不变。' if zh else
                'Could not load connection settings; the current selection is unchanged.',
            )
            return
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        if not rows:
            self.conn_combo.addItem(
                '无 MongoDB 连接' if self.language == 'zh' else 'No MongoDB connection', None
            )
            self.conn_combo.setItemData(0, '点击“新建连接”创建' if self.language == 'zh'
                                       else 'Use New connection to create one', Qt.ItemDataRole.ToolTipRole)
        for item in rows:
            self.conn_combo.addItem(str(item.get('name') or item.get('id')), item)
            if select_id and item.get('id') == select_id:
                self.conn_combo.setCurrentIndex(self.conn_combo.count() - 1)
        self.conn_combo.blockSignals(False)
        self._on_connection_changed()

    def _current_conn(self) -> dict | None:
        data = self.conn_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _history_service(self):
        if self._nosql_history_service is not None:
            return self._nosql_history_service
        from ui.data_center.query_history_service import get_application_history_service

        return get_application_history_service()

    def _create_nosql_draft(self):
        self._nosql_draft_sequence += 1
        item = self._current_conn()
        try:
            if item is None:
                raise NoSqlHistoryError("PROFILE_UNAVAILABLE")
            database = self._selected_db or str(item.get('database') or '')
            draft = make_nosql_draft(
                "mongo", item, self._nosql_profile_host,
                database=database,
                collection=self._selected_coll,
            )
        except NoSqlHistoryError as error:
            draft = NoSqlDraft(
                draft_id=uuid.uuid4().hex,
                provider="mongo",
                target=None,
                editor_texts={"documents": "", "command": ""},
                runnable=False,
                validation_code=error.code,
            )
        self.nosql_draft_tabs.add_draft(draft)
        return draft

    def _save_nosql_draft_text(self, draft):
        if not isinstance(draft, NoSqlDraft):
            return
        draft.editor_texts["documents"] = self.query_input.toPlainText()
        draft.editor_texts["command"] = self.cmd_input.text()

    def _clear_nosql_results(self):
        self._docs = []
        self._columns = []
        self._offset = 0
        self._total = None
        self._page_has_more = False
        self._result_draft_id = ""
        self._result_snapshot = None
        self._page_stale = False
        self._page_read_at = ""
        self.doc_table.setRowCount(0)
        self.doc_table.setColumnCount(0)
        self.doc_json.clear()
        self.cmd_output.clear()
        self.page_label.setText("")
        self._sync_pager()

    def _load_nosql_draft_text(self, draft):
        self._clear_nosql_results()
        self.query_status_band.setText('', querying=False)
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker_draft_id = getattr(worker, 'nosql_draft_id', '')
            if not worker_draft_id or (
                isinstance(draft, NoSqlDraft) and draft.draft_id == worker_draft_id
            ):
                self._set_query_status(worker.kind, 'running')
        if not isinstance(draft, NoSqlDraft):
            self.query_input.clear()
            self.cmd_input.clear()
            self.query_btn.setEnabled(False)
            self.cmd_btn.setEnabled(False)
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.insert_btn.setEnabled(False)
            self.del_btn.setEnabled(False)
            self.nosql_draft_tabs.set_status(None)
            return
        self.query_input.setPlainText(draft.editor_texts.get("documents", ""))
        self.cmd_input.setText(draft.editor_texts.get("command", ""))
        enabled = bool(draft.runnable and draft.target is not None)
        self.query_btn.setEnabled(enabled)
        self.cmd_btn.setEnabled(enabled)
        self._sync_pager()
        self.insert_btn.setEnabled(enabled)
        self.del_btn.setEnabled(enabled)
        self.nosql_draft_tabs.set_status(draft)
        if draft.target is not None:
            self._selected_db = draft.target.database
            self._selected_coll = draft.target.schema or draft.collection
            self.cmd_prompt.setText(f"{draft.target.connection_id}/{draft.target.database}>")
        if draft.restored_action == "command":
            self.side_tabs.setCurrentIndex(2)
        elif draft.restored_action == "query":
            self.side_tabs.setCurrentIndex(0)
        self._update_ai_banner()

    def _open_nosql_history(self):
        error = open_nosql_history_dialog(
            self,
            self.language,
            self._history_service,
            self._restore_nosql_history_record,
        )
        if error:
            self.nosql_draft_tabs.status_label.setText(
                ("历史不可用: " if self.language == "zh" else "History unavailable: ") + error
            )

    def _restore_nosql_history_record(self, record):
        self._save_nosql_draft_text(self.nosql_draft_tabs.current_draft)
        draft = restore_nosql_history_record(record, "mongo", self._nosql_profile_host)
        self.nosql_draft_tabs.add_draft(draft)

    def _data_center_agent_selection(self):
        profile = self._current_conn()
        if not profile:
            return {'reason': '未选择 MongoDB 目标连接'}
        collections = []
        root = self.coll_tree.topLevelItem(0) if self.coll_tree.topLevelItemCount() else None
        if root is not None:
            for index in range(root.childCount()):
                child = root.child(index)
                meta = child.data(0, Qt.ItemDataRole.UserRole) or {}
                if meta.get('kind') == 'collection':
                    name = str(meta.get('name') or '').strip()
                    if name:
                        collections.append(name)
        if self._selected_coll:
            collections = [self._selected_coll] if self._selected_coll in collections else []
        return {'profile': profile, 'database': self._selected_db, 'schema_allowlist': tuple(collections)}

    def _put_data_center_agent_query(self, query: str):
        text = str(query or '').strip()
        if text:
            self.query_input.insertPlainText(text)
            self.query_input.setFocus()

    def agent_shutdown_cleanup(self) -> bool:
        host = getattr(self, 'data_center_agent', None)
        return True if host is None else bool(host.agent_shutdown_cleanup())

    def _track_legacy_worker(self, worker):
        self._legacy_workers.add(worker)
        _ACTIVE_MONGO_WORKERS.add(worker)
        worker.finished.connect(lambda w=worker: _release_active_mongo_worker(w))
        worker.finished.connect(self._on_legacy_worker_finished)

    def _on_legacy_worker_finished(self):
        worker = self.sender()
        if worker is None:
            return
        self._legacy_workers.discard(worker)
        _release_active_mongo_worker(worker)
        if self._worker is worker:
            self._worker = None
        self._sync_pager()
        pending = self._document_to_open
        if pending is not None and pending[0] is worker:
            self._document_to_open = None
            draft = self.nosql_draft_tabs.current_draft
            if not self._workbench_shutdown_requested and draft is not None and draft.draft_id == worker.nosql_draft_id:
                self._show_document_editor(draft, pending[1])

    def workbench_shutdown_cleanup(self) -> bool:
        """Request cooperative Mongo worker cancellation and retain live threads."""
        self._workbench_shutdown_requested = True
        for worker in tuple(self._legacy_workers):
            worker.cancelled = True
            for signal, slot in (
                (worker.completed, self._on_worker_done),
                (worker.failed, self._on_worker_failed),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        # Keep references until the finished slot has actually been delivered.
        return not self._legacy_workers

    def _on_connection_changed(self):
        item = self._current_conn()
        # The collection tree is scoped to the selected profile. Never carry
        # its previous browse selection into a new draft on another profile.
        self._selected_coll = ''
        if item:
            self._selected_db = str(item.get('database') or '')
            self.cmd_prompt.setText(f'{self._selected_db or "mongo"}>')
            self._refresh_collections()
        else:
            self.coll_tree.clear()
            self.coll_stats.setText('')
        self._update_ai_banner()
        host = getattr(self, 'data_center_agent', None)
        if host is not None:
            host.target_changed()
        self._set_nosql_prompt(self.nosql_draft_tabs.current_draft)

    def _set_nosql_prompt(self, draft):
        if isinstance(draft, NoSqlDraft) and draft.target is not None:
            self.cmd_prompt.setText(f"{draft.target.connection_id}/{draft.target.database}>")

    def _edit_connection(self, new=False):
        current = None if new else self._current_conn()
        dialog = ConnectionDialog(self.language, current, self, locked_dialect='mongodb')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item, password = dialog.payload()
        try:
            saved = upsert_connection(item, password if password else None)
        except Exception:
            zh = self.language == 'zh'
            show_error(
                self, self._title(),
                '连接配置保存状态未确认；请重新加载后再操作。' if zh else
                'Could not confirm the connection save; reload before retrying.',
            )
            return
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
        try:
            delete_connection(item.get('id'))
        except Exception:
            show_error(
                self, self._title(),
                '连接删除状态未确认；请重新加载后再操作。' if zh else
                'Could not confirm the connection deletion; reload before retrying.',
            )
            return
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
        host = getattr(self, 'data_center_agent', None)
        if host is not None:
            host.target_changed()

    def _on_coll_clicked(self, item: QTreeWidgetItem, _column: int):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get('kind') != 'collection':
            return
        self._selected_coll = str(meta.get('name') or '')
        self._offset = 0
        # Tree browsing is selection context for a new draft. Existing drafts
        # retain their captured collection and editor contents.
        self._clear_nosql_results()
        self._load_schema()
        host = getattr(self, 'data_center_agent', None)
        if host is not None:
            host.target_changed()

    def _load_schema(self):
        item = self._current_conn()
        if item and self._selected_coll:
            self._run_worker('schema', item, collection=self._selected_coll)

    # ── 文档查询 ──────────────────────────────────────────────────────────

    def _run_query(self):
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            reason = draft.validation_code if isinstance(draft, NoSqlDraft) else "PROFILE_UNAVAILABLE"
            show_warning(self, self._title(), reason)
            return
        editor_text = self.query_input.toPlainText()
        dispatched_text = editor_text.strip()
        snapshot = draft.snapshot
        if snapshot is not None and snapshot.action == "query" and editor_text == snapshot.editor_text:
            arguments = snapshot.worker_arguments()
        else:
            try:
                parsed = parse_mongo_query(dispatched_text) if dispatched_text else {}
                collection = self._resolve_draft_collection(draft, parsed.get('collection'))
            except DbError as exc:
                show_error(self, self._title(), str(exc))
                return
            except NoSqlHistoryError as exc:
                show_warning(self, self._title(), exc.code)
                return
            limit = parsed.get('limit') or 50
            try:
                snapshot = capture_mongo_run(
                    draft.target,
                    editor_text,
                    dispatched_text,
                    action="query",
                    mode=draft.mode,
                    database=draft.target.database,
                    collection=collection,
                    filter=parsed.get('filter') or {},
                    projection=parsed.get('projection'),
                    sort=parsed.get('sort'),
                    skip=0,
                    limit=limit,
                )
                arguments = snapshot.worker_arguments()
            except NoSqlWorkspaceCodecError:
                # History is best-effort; retain the normal parsed query.
                snapshot = None
                arguments = {
                    "collection": collection,
                    "filter": parsed.get('filter') or {},
                    "projection": parsed.get('projection'),
                    "sort": parsed.get('sort'),
                    "skip": 0,
                    "limit": limit,
                }
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError as exc:
            self._mark_nosql_draft_unavailable(draft, exc.code)
            show_warning(self, self._title(), exc.code)
            return
        try:
            history_capture = make_nosql_history_capture(snapshot) if snapshot is not None else None
        except Exception:
            history_capture = None
        accepted = self._run_worker(
            'query', item, draft_id=draft.draft_id, history_capture=history_capture, query_snapshot=snapshot,
            **{key: arguments[key] for key in ('collection', 'filter', 'sort', 'projection', 'skip', 'limit')},
        )
        if accepted:
            if snapshot is not None:
                draft.snapshot = snapshot
                draft.collection = str(arguments.get('collection') or '')
            draft.editor_texts['documents'] = editor_text

    def _reset_query(self):
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable:
            return
        self.query_input.clear()
        draft.editor_texts['documents'] = ''
        draft.snapshot = None
        self._run_query()

    def _shift_page(self, delta: int):
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            return
        if delta not in {-1, 1} or self._worker is not None and self._worker.isRunning():
            return
        if self._result_draft_id != draft.draft_id or delta > 0 and not self._page_has_more:
            return
        previous = self._result_snapshot
        if previous is None or previous.action != "query" or self.query_input.toPlainText() != previous.editor_text:
            show_warning(
                self,
                self._title(),
                "请先运行当前查询" if self.language == "zh" else "Run the current query first",
            )
            return
        arguments = previous.worker_arguments()
        page_size = arguments.get("limit")
        if type(page_size) is not int or page_size <= 0:
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        new_offset = max(0, int(arguments.get("skip", 0)) + int(delta) * page_size)
        if new_offset == int(arguments.get("skip", 0)):
            return
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
            snapshot = capture_mongo_run(
                draft.target,
                previous.editor_text,
                previous.dispatched_text,
                action="query",
                mode=draft.mode,
                database=draft.target.database,
                collection=str(arguments["collection"]),
                filter=arguments["filter"],
                projection=arguments["projection"],
                sort=arguments["sort"],
                skip=new_offset,
                limit=page_size,
            )
            page_arguments = snapshot.worker_arguments()
            capture = make_nosql_history_capture(snapshot)
        except NoSqlHistoryError as exc:
            self._mark_nosql_draft_unavailable(draft, exc.code)
            show_warning(self, self._title(), exc.code)
            return
        except NoSqlWorkspaceCodecError:
            return
        accepted = self._run_worker(
            'query', item, draft_id=draft.draft_id, history_capture=capture, query_snapshot=snapshot,
            **{key: page_arguments[key] for key in ('collection', 'filter', 'sort', 'projection', 'skip', 'limit')},
        )
        if accepted:
            draft.snapshot = snapshot

    def _render_docs(self, docs: list[dict], total: int | None = None, limit: int = 50, *, skip=None, has_more=None):
        limit = max(1, int(limit))
        self._offset = max(0, skip) if type(skip) is int else self._offset
        self._total = total if type(total) is int and total >= 0 else None
        self._page_size = limit
        self._page_stale = False
        self._page_read_at = QDateTime.currentDateTime().toString('HH:mm:ss')
        self._page_has_more = has_more if type(has_more) is bool else len(docs) > limit or self._total is not None and self._total > self._offset + min(len(docs), limit)
        docs = docs[:limit]
        self._docs = docs
        # 提取列（保留顺序，去重）
        columns = []
        for doc in docs:
            if isinstance(doc, dict):
                for key in doc.keys():
                    if str(key) not in columns:
                        columns.append(str(key))
        self._columns = columns or ['_id']
        sorting = self.doc_table.isSortingEnabled()
        self.doc_table.setSortingEnabled(False)
        self.doc_table.setColumnCount(len(self._columns))
        self.doc_table.setHorizontalHeaderLabels(self._columns)
        self.doc_table.setRowCount(0)
        for doc in docs:
            row = self.doc_table.rowCount()
            self.doc_table.insertRow(row)
            for ci, col in enumerate(self._columns):
                val = doc.get(col, '') if isinstance(doc, dict) else ''
                if isinstance(val, (dict, list)):
                    val = json_util.dumps(val, ensure_ascii=False, json_options=_DISPLAY_JSON_OPTIONS)
                cell = QTableWidgetItem(str(val))
                cell.setData(Qt.ItemDataRole.UserRole, row)
                if ci == 0:
                    cell.setForeground(Qt.GlobalColor.darkCyan)
                self.doc_table.setItem(row, ci, cell)
        self.doc_table.setSortingEnabled(sorting)
        self._update_page_label()
        self._sync_pager()
        # JSON 视图
        try:
            self.doc_json.setPlainText(json_util.dumps(docs, ensure_ascii=False, indent=2, json_options=_DISPLAY_JSON_OPTIONS))
        except Exception:
            self.doc_json.setPlainText(str(docs))

    def _sync_pager(self):
        draft = self.nosql_draft_tabs.current_draft
        busy = self._worker is not None
        available = (not busy and not self._workbench_shutdown_requested and isinstance(draft, NoSqlDraft)
                     and draft.runnable and self._result_draft_id == draft.draft_id and self._result_snapshot is not None)
        self.prev_btn.setEnabled(bool(available and self._offset > 0))
        self.next_btn.setEnabled(bool(available and self._page_has_more))
        selected = self._selected_document()
        self.edit_doc_btn.setEnabled(bool(available and selected is not None and '_id' in selected))
        self.export_docs_btn.setEnabled(available)
        self.import_docs_btn.setEnabled(bool(not busy and not self._workbench_shutdown_requested and draft is not None and draft.runnable and draft.target is not None))

    def _selected_document(self):
        row = self.doc_table.currentRow()
        if row < 0 or len({item.row() for item in self.doc_table.selectedItems()}) != 1:
            return None
        item = self.doc_table.item(row, 0)
        index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return self._docs[index] if type(index) is int and 0 <= index < len(self._docs) else None

    def _update_page_label(self):
        if not self._docs and not self._result_draft_id:
            self.page_label.clear()
            return
        page = self._offset // self._page_size + 1
        if self._total is not None:
            label = f'第 {page} 页 · 显示 {len(self._docs)} / {self._total}' if self.language == 'zh' else f'Page {page} · {len(self._docs)} / {self._total}'
        else:
            state = ('还有下一页' if self._page_has_more else '末页') if self.language == 'zh' else ('More available' if self._page_has_more else 'Last page')
            label = f'第 {page} 页 · 本页 {len(self._docs)} 条 · {state}' if self.language == 'zh' else f'Page {page} · {len(self._docs)} rows · {state}'
        self.page_label.setText(label)
        if self._page_stale:
            stale = f'上次结果 {self._page_read_at}（未更新）' if self.language == 'zh' else f'Previous result {self._page_read_at} (not updated)'
            self.page_label.setText(label + ' · ' + stale)

    def _toggle_view_mode(self):
        table_visible = self.doc_table.isVisible()
        self.doc_table.setVisible(not table_visible)
        self.doc_json.setVisible(table_visible)
        self.view_mode_btn.setText(
            '表格视图' if table_visible else ('JSON 视图' if self.language == 'zh' else 'JSON view')
        )

    def _insert_doc(self):
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            reason = draft.validation_code if isinstance(draft, NoSqlDraft) else "PROFILE_UNAVAILABLE"
            show_warning(self, self._title(), reason)
            return
        try:
            collection = self._resolve_draft_collection(
                draft, draft.collection or draft.target.schema or self._selected_coll,
            )
        except NoSqlHistoryError as exc:
            show_warning(self, self._title(), exc.code)
            return
        if not collection:
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
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError as exc:
            self._mark_nosql_draft_unavailable(draft, exc.code)
            show_warning(self, self._title(), exc.code)
            return
        self._run_worker('insert', item, draft_id=draft.draft_id, collection=collection, doc=doc)

    def _delete_selected(self):
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            reason = draft.validation_code if isinstance(draft, NoSqlDraft) else "PROFILE_UNAVAILABLE"
            show_warning(self, self._title(), reason)
            return
        try:
            collection = self._resolve_draft_collection(
                draft, draft.collection or draft.target.schema or self._selected_coll,
            )
        except NoSqlHistoryError as exc:
            show_warning(self, self._title(), exc.code)
            return
        if not collection:
            show_warning(self, self._title(), '请先选择集合' if self.language == 'zh' else 'Pick a collection')
            return
        raw = self.query_input.toPlainText()
        snapshot = draft.snapshot
        if snapshot is not None and snapshot.action == "query" and raw == snapshot.editor_text:
            filt = snapshot.worker_arguments()["filter"]
        elif raw.strip():
            try:
                parsed = parse_mongo_query(raw.strip())
                self._resolve_draft_collection(draft, parsed.get('collection'))
                filt = parsed.get('filter', {})
            except (DbError, NoSqlHistoryError) as exc:
                show_warning(self, self._title(), getattr(exc, "code", "INVALID_QUERY"))
                return
        else:
            filt = {}
        zh = self.language == 'zh'
        target_text = f"{draft.target.connection_id}/{draft.target.database}.{collection}"
        if not confirm_action(self, self._title(),
                              f'删除「{target_text}」中匹配 {json.dumps(filt, ensure_ascii=False)} 的文档？'
                              if zh else f'Delete docs from {target_text} matching {json.dumps(filt)}?',
                              confirm_text='删除' if zh else 'Delete', danger=True):
            return
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError as exc:
            self._mark_nosql_draft_unavailable(draft, exc.code)
            show_warning(self, self._title(), exc.code)
            return
        self._run_worker('delete', item, draft_id=draft.draft_id, collection=collection, filter=filt)

    def _copy_selected(self):
        if not self._docs:
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(json.dumps(self._docs, ensure_ascii=False, indent=2, default=str))

    def _edit_document(self):
        draft = self.nosql_draft_tabs.current_draft
        selected = self._selected_document()
        if (self._worker is not None or self._workbench_shutdown_requested or draft is None or not draft.runnable
                or self._result_draft_id != draft.draft_id or self._result_snapshot is None
                or selected is None or '_id' not in selected):
            return
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
            context = make_document_target(self._result_snapshot.target,
                                           self._result_snapshot.worker_arguments()['collection'], selected['_id'])
        except Exception as error:
            show_warning(self, self._title(), getattr(error, 'code', 'MONGO_EDIT_TARGET_INVALID'))
            return
        self._run_worker('document_load', item, draft_id=draft.draft_id, context=context)

    def _show_document_editor(self, draft, loaded):
        from ui.data_center.mongo_document_edit import MongoDocumentEditDialog

        def dispatch(kind, values, attach):
            if not any(candidate is draft for candidate in self.nosql_draft_tabs.drafts()):
                raise DbError('MONGO_EDIT_DRAFT_CLOSED')
            if draft.target != loaded.context.target:
                raise DbError('MONGO_EDIT_TARGET_INVALID')
            item = validate_nosql_target(draft, self._nosql_profile_host) if kind == 'document_update' else {}
            return self._run_worker(kind, item, draft_id=draft.draft_id, on_created=attach, **values)

        dialog = MongoDocumentEditDialog(loaded, dispatch, language=self.language, parent=self)
        self._document_dialog = dialog
        dialog.show()

    def _open_transfer(self, exporting):
        from ui.data_center.mongo_transfer import MongoTransferDialog

        draft = self.nosql_draft_tabs.current_draft
        if self._worker is not None or self._workbench_shutdown_requested or draft is None or not draft.runnable or draft.target is None:
            return
        if exporting and (self._result_snapshot is None or self._result_draft_id != draft.draft_id):
            return
        target = self._result_snapshot.target if exporting else draft.target
        snapshot = self._result_snapshot
        documents = tuple(deepcopy(self._docs)) if exporting else ()
        indices = set()
        for item in self.doc_table.selectedItems() if exporting else ():
            index = item.data(Qt.ItemDataRole.UserRole)
            if type(index) is int and 0 <= index < len(documents):
                indices.add(index)
        selected = tuple(documents[index] for index in sorted(indices))
        representation = self._result_representation

        def dispatch(kind, values, attach):
            if not any(candidate is draft for candidate in self.nosql_draft_tabs.drafts()) or draft.target != target:
                raise DbError('MONGO_TRANSFER_TARGET_INVALID')
            item = validate_nosql_target(draft, self._nosql_profile_host) if kind in ('document_import', 'export_query') else {}
            if kind == 'export_documents':
                values = {**values, 'documents': selected if values['scope'] == 'selected' else documents,
                          'representation': representation}
            elif kind == 'export_query':
                values = {**values, 'snapshot': snapshot}
            return self._run_worker(kind, item, draft_id=draft.draft_id, on_created=attach, **values)

        dialog = MongoTransferDialog(target, dispatch, exporting=exporting, selected_count=len(selected),
                                     page_count=len(documents), language=self.language, parent=self)
        self._transfer_dialog = dialog
        dialog.show()

    # ── Shell ─────────────────────────────────────────────────────────────

    def _run_command(self):
        editor_text = self.cmd_input.text()
        sql = editor_text.strip()
        if not sql:
            return
        from tools.sql_guard import reject_reason
        reason = reject_reason(sql, 'mongodb')
        if reason:
            show_warning(self, self._title(), reason)
            return
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            reason = draft.validation_code if isinstance(draft, NoSqlDraft) else "PROFILE_UNAVAILABLE"
            show_warning(self, self._title(), reason)
            return
        snapshot = draft.snapshot
        if snapshot is not None and snapshot.action == "command" and editor_text == snapshot.editor_text:
            arguments = snapshot.worker_arguments()
        else:
            try:
                parsed = parse_mongo_query(sql)
                collection = self._resolve_draft_collection(draft, parsed.get('collection'))
            except DbError as exc:
                show_error(self, self._title(), str(exc))
                return
            except NoSqlHistoryError as exc:
                show_warning(self, self._title(), exc.code)
                return
            limit = parsed.get('limit') or 50
            try:
                snapshot = capture_mongo_run(
                    draft.target,
                    editor_text,
                    sql,
                    action="command",
                    mode=draft.mode,
                    database=draft.target.database,
                    collection=collection,
                    filter=parsed.get('filter') or {},
                    projection=parsed.get('projection'),
                    sort=parsed.get('sort'),
                    skip=0,
                    limit=limit,
                )
                arguments = snapshot.worker_arguments()
            except NoSqlWorkspaceCodecError:
                snapshot = None
                arguments = {
                    "collection": collection,
                    "filter": parsed.get('filter') or {},
                    "projection": parsed.get('projection'),
                    "sort": parsed.get('sort'),
                    "skip": 0,
                    "limit": limit,
                }
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError as exc:
            self._mark_nosql_draft_unavailable(draft, exc.code)
            show_warning(self, self._title(), exc.code)
            return
        try:
            history_capture = make_nosql_history_capture(snapshot) if snapshot is not None else None
        except Exception:
            history_capture = None
        accepted = self._run_worker(
            'command', item, draft_id=draft.draft_id, history_capture=history_capture,
            **{key: arguments[key] for key in ('collection', 'filter', 'sort', 'projection', 'skip', 'limit')},
        )
        if accepted:
            if snapshot is not None:
                draft.snapshot = snapshot
                draft.collection = str(arguments.get('collection') or '')
            self.cmd_input.clear()
            draft.editor_texts['command'] = ''
            self._append_cmd(f'{self.cmd_prompt.text()} {sql}')

    def _resolve_draft_collection(self, draft: NoSqlDraft, query_collection) -> str:
        bound_collection = str(draft.target.schema or draft.collection or '').strip()
        requested_collection = str(query_collection or '').strip()
        if bound_collection and requested_collection and requested_collection != bound_collection:
            raise NoSqlHistoryError("COLLECTION_TARGET_MISMATCH")
        collection = bound_collection or requested_collection
        if not collection:
            raise NoSqlHistoryError("COLLECTION_REQUIRED")
        if not bound_collection:
            target = draft.target
            draft.target = WorkspaceTarget(
                tab_id=target.tab_id,
                connection_id=target.connection_id,
                profile_revision=target.profile_revision,
                database=target.database,
                schema=collection,
                tab_kind=WorkspaceTabKind.MONGO,
            )
            draft.collection = collection
            self.nosql_draft_tabs.update_draft(draft)
            self.nosql_draft_tabs.set_status(draft)
        return collection

    def _mark_nosql_draft_unavailable(self, draft: NoSqlDraft, code: str) -> None:
        draft.runnable = False
        draft.validation_code = str(code or "PROFILE_UNAVAILABLE")
        self.nosql_draft_tabs.update_draft(draft)
        self.nosql_draft_tabs.set_status(draft)
        self.query_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.cmd_btn.setEnabled(False)
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

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

    def _run_worker(self, kind, item, *, draft_id=None, history_capture=None, query_snapshot=None, on_created=None, **kwargs):
        if self._workbench_shutdown_requested:
            return False
        if self._worker is not None and self._worker.isRunning():
            return False
        origin = next((draft for draft in self.nosql_draft_tabs.drafts() if draft.draft_id == draft_id), None)
        database = query_snapshot.target.database if query_snapshot is not None else (origin.target.database if origin is not None and origin.target is not None else '')
        worker = _MongoWorker(kind, item, nosql_draft_id=draft_id or "", connection_opener=getattr(self, "_connection_opener", None), query_snapshot=query_snapshot, database_name=database, **kwargs)
        self._worker = worker
        service = None
        attached = False
        if history_capture is not None:
            try:
                service = self._history_service()
                attached = bool(service.attach_nosql_worker(worker, history_capture))
            except Exception:
                service = None
        self._track_legacy_worker(worker)
        self._set_query_status(kind, 'running')
        if kind == 'query' and self._result_draft_id == draft_id:
            self._page_stale = True
            self._update_page_label()
        self._sync_pager()
        worker.completed.connect(self._on_worker_done)
        worker.failed.connect(self._on_worker_failed)
        try:
            if on_created is not None:
                on_created(worker)
            worker.start()
        except Exception:
            try:
                if worker.isRunning() or worker.isFinished():
                    return True
            except RuntimeError:
                pass
            if attached and service is not None:
                service.detach_unstarted_nosql_worker(worker)
            self._legacy_workers.discard(worker)
            _release_active_mongo_worker(worker)
            if self._worker is worker:
                self._worker = None
            self._sync_pager()
            self._set_query_status(kind, 'failed', 'WORKER_START_FAILED')
            return False
        return True

    def _set_query_status(self, kind, state, detail=''):
        zh = self.language == 'zh'
        labels = {
            'test': ('正在测试连接…', 'Testing connection…', '连接测试完成', 'Connection test complete'),
            'collections': ('正在加载集合…', 'Loading collections…', '集合加载完成', 'Collections loaded'),
            'schema': ('正在加载结构…', 'Loading schema…', '结构加载完成', 'Schema loaded'),
            'query': ('正在执行查询…', 'Running query…', '查询完成', 'Query complete'),
            'command': ('正在执行命令…', 'Running command…', '命令执行完成', 'Command complete'),
            'insert': ('正在插入文档…', 'Inserting document…', '文档插入完成', 'Document inserted'),
            'delete': ('正在删除文档…', 'Deleting documents…', '文档删除完成', 'Documents deleted'),
            'document_load': ('正在读取完整文档…', 'Loading complete document…', '完整文档已读取', 'Complete document loaded'),
            'prepare_edit': ('正在生成修改预览…', 'Preparing edit preview…', '修改预览已生成', 'Edit preview ready'),
            'document_update': ('正在修改文档…', 'Updating document…', '文档修改已返回', 'Document update returned'),
        }
        running_zh, running_en, done_zh, done_en = labels.get(
            kind, ('正在处理…', 'Working…', '处理完成', 'Complete'),
        )
        if state == 'running':
            text = running_zh if zh else running_en
        elif state == 'failed':
            prefix = '操作失败' if zh else 'Operation failed'
            text = f'{prefix}: {detail}' if detail else prefix
        else:
            text = done_zh if zh else done_en
        self.query_status_band.setText(text, querying=state == 'running')

    def _on_worker_done(self, kind: str, payload):
        worker = self.sender()
        if self._workbench_shutdown_requested or (worker is not None and worker is not self._worker):
            return
        if kind in ('query', 'command', 'insert', 'delete', 'document_load', 'prepare_edit', 'document_update', 'prepare_import', 'document_import', 'export_documents', 'export_query'):
            draft = self.nosql_draft_tabs.current_draft
            if (
                worker is None
                or not isinstance(draft, NoSqlDraft)
                or draft.draft_id != getattr(worker, 'nosql_draft_id', '')
            ):
                return
        if worker is self._worker:
            self._set_query_status(kind, 'done')
        if kind == 'test':
            show_info(self, self._title(), '连接成功' if self.language == 'zh' else 'Connection OK')
        elif kind == 'collections':
            self._render_collections(list(payload.get('collections') or []))
        elif kind == 'query':
            self._result_draft_id = worker.nosql_draft_id
            self._result_snapshot = getattr(worker, 'query_snapshot', None)
            self._result_representation = payload.get('representation', UuidRepresentation.STANDARD)
            self._render_docs(list(payload.get('docs') or []), payload.get('total'),
                              int(payload.get('limit') or 50), skip=payload.get('skip'), has_more=payload.get('has_more'))
        elif kind == 'schema':
            self._schema_fields = list(payload.get('fields') or [])
            self.ai_schema.setText('Schema: ' + '、'.join(self._schema_fields))
            self._update_ai_banner()
        elif kind == 'document_load':
            self._document_to_open = (worker, payload)
        elif kind == 'document_update':
            self._page_stale = True
            self._update_page_label()
            self.query_status_band.setText(
                f"已匹配 {payload['matched']} 条，修改 {payload['modified']} 条；请刷新查看。" if self.language == 'zh' else
                f"Matched {payload['matched']}, modified {payload['modified']}; refresh to view.", querying=False)
        elif kind == 'document_import':
            self._page_stale = True
            self._update_page_label()
        elif kind == 'insert':
            self._reset_query()
            show_info(self, self._title(),
                      f"已插入，_id = {payload.get('inserted_id')}" if self.language == 'zh'
                      else f"Inserted, _id = {payload.get('inserted_id')}")
        elif kind == 'delete':
            self._reset_query()
            del_cnt = payload.get('deleted') if payload.get('deleted') is not None else payload.get('deleted_count', 0)
            show_info(self, self._title(),
                      f"已删除 {del_cnt} 条" if self.language == 'zh'
                      else f"Deleted {del_cnt} docs")
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
        worker = self.sender()
        if self._workbench_shutdown_requested or (worker is not None and worker is not self._worker):
            return
        if kind in ('query', 'command', 'insert', 'delete', 'document_load', 'prepare_edit', 'document_update', 'prepare_import', 'document_import', 'export_documents', 'export_query'):
            draft = self.nosql_draft_tabs.current_draft
            if (
                worker is None
                or not isinstance(draft, NoSqlDraft)
                or draft.draft_id != getattr(worker, 'nosql_draft_id', '')
            ):
                return
        if worker is self._worker:
            self._set_query_status(kind, 'failed', error)
        if kind in ('prepare_edit', 'document_update', 'prepare_import', 'document_import', 'export_documents', 'export_query'):
            return  # The captured document dialog presents its own outcome.
        if kind == 'command':
            self.cmd_output.appendPlainText(f'错误: {error}')
        elif kind == 'collections' and ('[AUTHZ_ERROR]' in error or 'code 13' in error or 'unauthorized' in error.lower() or 'not authorized' in error.lower()):
            self._render_unauthorized_collections(error)
            show_warning(self, self._title(), error)
        else:
            show_error(self, self._title(), error)
