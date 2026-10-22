# -*- coding: utf-8 -*-
"""Redis 工作台：Key 树浏览 + Key 详情（类型/TTL/值）+ AI 助手 + 命令行。

参考 Another Redis Desktop Manager。连接由 tools.db_connect.open_connection 建立。
"""

from __future__ import annotations

import json
import uuid

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QSizePolicy,
    QComboBox, QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QHeaderView,
)

from ui.connection_dialog import ConnectionDialog
from tools.db_connect import (
    DbError, close_connection, delete_connection,
    load_connections, open_connection, upsert_connection,
)
from tools.db_redis_ops import (
    RedisScanState, build_prefix_index, format_redis_bytes, keys_for_prefix,
    redis_delete_key, redis_expire_key, redis_info_sections,
    redis_overview, redis_rename_key, redis_scan_page,
)
from tools.data_center.nosql_workspace import NoSqlWorkspaceCodecError, capture_redis_command
from tools.data_center.redis_export import export_key
from tools.data_center.redis_value import RedisKeyContext, RedisValuePage, page_display_value, read_value_page
from tools.data_center.profile_host import DataCenterProfileHost
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_table
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_page_toolbar
from ui.icons import apply_icon, qicon
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


_ACTIVE_REDIS_WORKERS = set()


def _release_active_redis_worker(worker):
    _ACTIVE_REDIS_WORKERS.discard(worker)


def format_key_ttl_value(ttl: int, language: str = 'zh') -> str:
    """Format Redis TTL consistently for badges and detail metadata."""
    try:
        ttl_val = int(ttl)
    except (ValueError, TypeError):
        return '—'
    if ttl_val >= 0:
        return f'{ttl_val}s'
    if ttl_val == -1:
        return '永不过期' if language == 'zh' else 'No expiry'
    if ttl_val == -2:
        return 'Key 不存在' if language == 'zh' else 'Key missing'
    return '—'


def format_key_ttl_badge(ttl: int, language: str = 'zh') -> str:
    """Add the TTL label to the shared display value."""
    return f'TTL: {format_key_ttl_value(ttl, language)}'


class _RedisWorker(QThread):
    completed = pyqtSignal(str, object)   # (kind, payload)
    failed = pyqtSignal(str, str)         # (kind, error)
    progress = pyqtSignal(int, int)

    def __init__(self, kind: str, item: dict, *, nosql_draft_id: str = "", connection_opener=None, **kwargs):
        super().__init__()
        self.kind = kind
        self.item = dict(item or {})
        self.connection_opener = connection_opener or open_connection
        self.kwargs = kwargs
        self.cancelled = False
        self.generation = int(kwargs.get('generation') or 0)
        self._nosql_draft_id = str(nosql_draft_id or "")

    @property
    def nosql_draft_id(self) -> str:
        return self._nosql_draft_id

    def run(self):
        conn = None
        try:
            conn = self.connection_opener(self.item)
            if self.cancelled:
                return
            if self.kind == 'test':
                self._emit_completed('test', {'ok': True})
            elif self.kind == 'export_key':
                receipt = export_key(conn, **self.kwargs, cancel=lambda: self.cancelled, progress=self.progress.emit)
                self.completed.emit('export_key', receipt)
            elif self.kind in ('scan', 'bootstrap', 'overview'):
                overview = None
                if self.kind in ('bootstrap', 'overview') or self.kwargs.get('with_overview'):
                    overview = redis_overview(conn)
                if self.cancelled:
                    return
                page = None
                if self.kind != 'overview':
                    page = redis_scan_page(
                        conn,
                        self.kwargs.get('pattern', '*'),
                        cursor=self.kwargs.get('cursor', 0),
                        count=int(self.kwargs.get('count') or 500),
                        limit=int(self.kwargs.get('limit') or 2000),
                        cancel=lambda: self.cancelled,
                    )
                if self.cancelled:
                    return
                info_table = None
                if overview is not None:
                    try:
                        info_table = redis_info_sections(conn)
                    except Exception:
                        info_table = {'priority': [], 'all': []}
                self._emit_completed(self.kind, {
                    'overview': overview,
                    'info_table': info_table,
                    'scan': page,
                    'generation': int(self.kwargs.get('generation') or 0),
                    'append': bool(self.kwargs.get('append')),
                })
            elif self.kind in ('key_meta', 'key_value'):
                page = read_value_page(conn, self.kwargs['context'], self.kwargs.get('position', 0),
                                       expected_kind=self.kwargs.get('expected_kind', ''), cancel=lambda: self.cancelled)
                value = page_display_value(page)
                self._emit_completed(self.kind, {'page': page, 'type': page.kind, 'ttl': page.ttl, 'value': value})
            elif self.kind == 'delete':
                n = redis_delete_key(conn, self.kwargs.get('key', ''))
                self._emit_completed('delete', {'key': self.kwargs.get('key', ''), 'deleted': n})
            elif self.kind == 'rename':
                ok = redis_rename_key(conn, self.kwargs.get('key', ''),
                                      self.kwargs.get('new_key', ''))
                self._emit_completed('rename', {'ok': ok, 'new_key': self.kwargs.get('new_key', '')})
            elif self.kind == 'expire':
                ok = redis_expire_key(conn, self.kwargs.get('key', ''),
                                      int(self.kwargs.get('seconds', -1)))
                self._emit_completed('expire', {'ok': ok, 'key': self.kwargs.get('key', '')})
            elif self.kind == 'command':
                sql = self.kwargs.get('sql', '')
                from tools.db_connect import _run_redis
                result = _run_redis(conn, sql, 0, 100)
                self._emit_completed('command', result)
            else:
                raise DbError(f'未知任务：{self.kind}')
        except Exception as exc:
            if not self.cancelled:
                self.failed.emit(self.kind, redact_error(str(exc)))
        finally:
            close_connection(conn)

    def _emit_completed(self, kind, payload):
        if not self.cancelled:
            self.completed.emit(kind, payload)


class RedisWorkbenchPanel(QWidget):
    def __init__(self, language='zh', *, data_center_agent_options=None, data_center_history_options=None):
        super().__init__()
        self.language = language
        history_options = dict(data_center_history_options or {})
        self._nosql_profile_host = history_options.pop('profile_host', None) or DataCenterProfileHost()
        self._nosql_history_service = history_options.pop('history_service', None)
        self._nosql_draft_sequence = 0
        self._conn = None
        self._worker = None
        self._bg_worker = None
        self._legacy_workers = set()
        self._workbench_shutdown_requested = False
        self._query_status_workers = {}
        self._query_status_sequence = 0
        self._query_status_last_terminal = None
        self.destroyed.connect(
            lambda *_args, entries=self._query_status_workers: entries.clear(),
        )
        self._selected_key = ''
        self._key_identities = {}
        self._key_identity_scope = None
        self._export_dialog = None
        self._value_context = None
        self._value_page = None
        self._value_stale = False
        self._value_notice = ''
        self._value_positions = [0]
        self._value_page_index = 0
        self._pending_value_request = None
        self._value_queue_timer = QTimer(self)
        self._value_queue_timer.setSingleShot(True)
        self._value_queue_timer.timeout.connect(self._drain_value_request)
        self._selected_type = ''
        self._key_cache = []
        self._tree_filter = ''
        self._selected_prefix = ''
        self._scan = RedisScanState()
        self._overview = None
        self._info_all = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(280)
        self._search_timer.timeout.connect(self._start_search_scan)
        self._setup_ui()
        self.set_language(language)
        self._reload_connections()
        self._create_nosql_draft()
        from ui.data_center.integration import DataCenterWorkbenchAgent
        self.data_center_agent = DataCenterWorkbenchAgent(
            selection_provider=self._data_center_agent_selection,
            put_sql=self._put_data_center_agent_command,
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
        self.root_layout = root

        # 模块身份由 Sidebar 标明，内容区不再重复大标题/副标题（Step 4B 收口）；
        # 页面首个主要区域 = 连接 toolbar。
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
        self.refresh_btn.clicked.connect(self._refresh_keys)
        for w in (self.conn_combo, self.conn_new_btn, self.conn_edit_btn, self.conn_del_btn,
                  self.test_btn, self.refresh_btn):
            top.addWidget(w)
        top.addStretch(1)

        self.home_btn = QPushButton()
        apply_button(self.home_btn, 'ghost', compact=True)
        self.home_btn.setObjectName('header-home-btn')
        self.home_btn.setProperty('homeAction', True)
        apply_icon(self.home_btn, 'home', size=16)
        def _on_home_clicked():
            top_win = self.window()
            if hasattr(top_win, 'navigate_to') and callable(getattr(top_win, 'navigate_to')):
                top_win.navigate_to(0)
        self.home_btn.clicked.connect(_on_home_clicked)
        top.addWidget(self.home_btn)

        self.toolbar = toolbar
        root.addWidget(toolbar)
        self.query_status_band = QueryStatusBand(self)
        root.addWidget(self.query_status_band)
        self.nosql_draft_tabs = NoSqlDraftTabs('redis', self.language, self)
        self.nosql_draft_tabs.draft_about_to_change.connect(self._save_nosql_draft_text)
        self.nosql_draft_tabs.draft_changed.connect(self._load_nosql_draft_text)
        self.nosql_draft_tabs.new_requested.connect(self._create_nosql_draft)
        self.nosql_draft_tabs.history_requested.connect(self._open_nosql_history)
        root.addWidget(self.nosql_draft_tabs)

        # ── 左侧容器：全高拉伸 ──────────────────────────────────────────────
        left = QFrame()
        left.setObjectName('redis-left-pane')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(8, 8, 8, 8)
        left_l.setSpacing(6)
        self.db_badge = QLabel()
        self.db_badge.setObjectName('status-pill')
        left_l.addWidget(self.db_badge)
        self.key_filter = QLineEdit()
        size_line(self.key_filter, 'std')
        self.key_filter.setPlaceholderText('搜索 Key / 前缀')
        self.key_filter.textChanged.connect(self._on_filter_changed)
        left_l.addWidget(self.key_filter)

        # 内部垂直 Splitter：上方 Prefix 树，下方 Key 列表
        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.setHandleWidth(16)
        self.left_split.setProperty('prismGutter', True)

        # 上半部分：Prefix 树
        tree_container = QWidget()
        tree_l = QVBoxLayout(tree_container)
        tree_l.setContentsMargins(0, 0, 0, 0)
        tree_l.setSpacing(4)
        self.prefix_label = QLabel()
        self.prefix_label.setObjectName('field-hint')
        tree_l.addWidget(self.prefix_label)
        self.key_tree = QTreeWidget()
        self.key_tree.setColumnCount(2)
        self.key_tree.setHeaderLabels(['Prefix', 'Keys'])
        self.key_tree.setIndentation(18)
        self.key_tree.setRootIsDecorated(True)
        self.key_tree.itemClicked.connect(self._on_prefix_clicked)
        self.key_tree.header().setStretchLastSection(False)
        self.key_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.key_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tree_l.addWidget(self.key_tree, 1)
        self.left_split.addWidget(tree_container)

        # 下半部分：Key 列表与面包屑导航栏
        list_container = QWidget()
        list_l = QVBoxLayout(list_container)
        list_l.setContentsMargins(0, 0, 0, 0)
        list_l.setSpacing(4)

        breadcrumb_bar = QHBoxLayout()
        breadcrumb_bar.setContentsMargins(0, 2, 0, 2)
        breadcrumb_bar.setSpacing(6)
        self.key_list_label = QLabel()
        self.key_list_label.setObjectName('field-hint')
        self.key_list_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.clear_prefix_btn = QPushButton()
        apply_button(self.clear_prefix_btn, 'ghost', compact=True)
        self.clear_prefix_btn.hide()
        self.clear_prefix_btn.clicked.connect(self._clear_selected_prefix)
        breadcrumb_bar.addWidget(self.key_list_label, 1)
        breadcrumb_bar.addWidget(self.clear_prefix_btn)
        list_l.addLayout(breadcrumb_bar)

        self.key_list = QListWidget()
        self.key_list.setObjectName('redis-key-list')
        self.key_list.itemClicked.connect(self._on_key_list_clicked)
        list_l.addWidget(self.key_list, 1)
        self.load_more_btn = QPushButton()
        apply_button(self.load_more_btn, 'ghost', compact=True)
        self.load_more_btn.clicked.connect(self._load_more_keys)
        self.load_more_btn.hide()
        list_l.addWidget(self.load_more_btn)
        self.key_stats = QLabel()
        self.key_stats.setObjectName('field-hint')
        self.key_stats.setWordWrap(True)
        list_l.addWidget(self.key_stats)
        self.left_split.addWidget(list_container)

        self.left_split.setStretchFactor(0, 1)
        self.left_split.setStretchFactor(1, 1)
        left_l.addWidget(self.left_split, 1)
        install_splitter_prefs(
            self.left_split, defaults=[320, 360], page_id='redis-workbench', tab_id='left_split',
            min_sizes=[140, 160], accessible_name='Redis 左侧前缀与Key列表分隔',
        )

        # ── 右侧区域 ───────────────────────────────────────────────────────
        self.side_tabs = QTabWidget()
        def add_scrollable_tab(content, title):
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.side_tabs.addTab(scroll, title)

        overview = QWidget()
        ov_l = QVBoxLayout(overview)
        ov_l.setContentsMargins(10, 8, 10, 8)
        ov_l.setSpacing(8)
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.card_keys = self._make_summary_card('Keys')
        self.card_memory = self._make_summary_card('Memory')
        self.card_nodes = self._make_summary_card('Nodes')
        self.card_version = self._make_summary_card('Version')
        for card in (self.card_keys, self.card_memory, self.card_nodes, self.card_version):
            cards.addWidget(card)
        ov_l.addLayout(cards)
        self.ov_hint = QLabel()
        self.ov_hint.setObjectName('field-hint')
        self.ov_hint.setWordWrap(True)
        ov_l.addWidget(self.ov_hint)
        self.nodes_title = QLabel()
        self.nodes_title.setObjectName('section-title')
        ov_l.addWidget(self.nodes_title)
        self.nodes_table = QTableWidget()
        self.nodes_table.setColumnCount(7)
        apply_table(self.nodes_table, alternating=True)
        self.nodes_table.setMinimumHeight(96)
        self.nodes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.nodes_table.setMaximumHeight(180)
        ov_l.addWidget(self.nodes_table)
        info_head = QHBoxLayout()
        self.info_title = QLabel()
        self.info_title.setObjectName('section-title')
        self.info_all_btn = QPushButton()
        apply_button(self.info_all_btn, 'ghost', compact=True)
        self.info_all_btn.clicked.connect(self._toggle_info_all)
        info_head.addWidget(self.info_title)
        info_head.addStretch(1)
        info_head.addWidget(self.info_all_btn)
        ov_l.addLayout(info_head)
        self.info_table = QTableWidget()
        self.info_table.setColumnCount(2)
        apply_table(self.info_table, alternating=True)
        self.info_table.setMinimumHeight(120)
        self.info_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.info_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ov_l.addWidget(self.info_table, 1)
        add_scrollable_tab(overview, 'Overview')

        # Tab 1：Key 详情
        detail = QWidget()
        detail.setObjectName('redis-detail-pane')
        det_l = QVBoxLayout(detail)
        det_l.setContentsMargins(10, 10, 10, 10)
        self.key_name = QLabel()
        self.key_name.setTextFormat(Qt.TextFormat.PlainText)
        self.key_name.setObjectName('section-title')
        self.key_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        det_l.addWidget(self.key_name)
        self.raw_key_combo = QComboBox()
        self.raw_key_combo.setToolTip('原始 Key（Base64）' if self.language == 'zh' else 'Raw key (Base64)')
        self.raw_key_combo.currentIndexChanged.connect(self._select_raw_value_key)
        det_l.addWidget(self.raw_key_combo)
        summary_strip = QHBoxLayout()
        summary_strip.setContentsMargins(0, 0, 0, 0)
        summary_strip.setSpacing(6)
        self.key_type_badge = QLabel('')
        self.key_type_badge.setObjectName('status-pill')
        self.key_type_badge.hide()
        self.key_ttl_badge = QLabel('')
        self.key_ttl_badge.setObjectName('status-pill')
        self.key_ttl_badge.hide()
        self.key_size_badge = QLabel('')
        self.key_size_badge.setObjectName('status-pill')
        self.key_size_badge.hide()
        summary_strip.addWidget(self.key_type_badge)
        summary_strip.addWidget(self.key_ttl_badge)
        summary_strip.addWidget(self.key_size_badge)
        summary_strip.addStretch(1)
        det_l.addLayout(summary_strip)
        self.key_meta = QLabel()
        self.key_meta.setObjectName('field-hint')
        self.key_meta.setWordWrap(True)
        det_l.addWidget(self.key_meta)
        self.value_tabs = QTabWidget()
        self.value_tabs.setMinimumHeight(280)

        # String 子页签：增加多编码切换栏
        str_container = QWidget()
        str_l = QVBoxLayout(str_container)
        str_l.setContentsMargins(0, 4, 0, 0)
        str_l.setSpacing(4)
        fmt_bar = QHBoxLayout()
        self.fmt_label = QLabel('编码视图:')
        self.fmt_label.setObjectName('field-hint')
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(['自动安全解码', 'UTF-8', 'GB18030', 'Hex (十六进制)', 'Base64', 'JSON (格式化)'])
        self.fmt_combo.currentIndexChanged.connect(self._on_fmt_changed)
        fmt_bar.addWidget(self.fmt_label)
        fmt_bar.addWidget(self.fmt_combo)
        fmt_bar.addStretch(1)
        str_l.addLayout(fmt_bar)
        self.string_value = QPlainTextEdit()
        self.string_value.setReadOnly(True)
        str_l.addWidget(self.string_value, 1)
        self.value_tabs.addTab(str_container, 'String')

        self.hash_table = QTableWidget()
        self.hash_table.setColumnCount(2)
        self.hash_table.setHorizontalHeaderLabels(['field', 'value'])
        apply_table(self.hash_table, alternating=True)
        self.hash_table.setMinimumHeight(120)
        self.hash_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.value_tabs.addTab(self.hash_table, 'Hash')
        self.list_table = QTableWidget()
        self.list_table.setColumnCount(2)
        self.list_table.setHorizontalHeaderLabels(['index', 'value'])
        apply_table(self.list_table, alternating=True)
        self.list_table.setMinimumHeight(120)
        self.list_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.value_tabs.addTab(self.list_table, 'List/Set')
        self.zset_table = QTableWidget()
        self.zset_table.setColumnCount(2)
        self.zset_table.setHorizontalHeaderLabels(['member', 'score'])
        apply_table(self.zset_table, alternating=True)
        self.zset_table.setMinimumHeight(120)
        self.zset_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.value_tabs.addTab(self.zset_table, 'ZSet')
        det_l.addWidget(self.value_tabs, 1)
        value_pager = QHBoxLayout()
        self.value_prev_btn = QPushButton()
        self.value_next_btn = QPushButton()
        for button in (self.value_prev_btn, self.value_next_btn):
            apply_button(button, 'ghost', compact=True)
            value_pager.addWidget(button)
        self.value_page_label = QLabel()
        self.value_page_label.setWordWrap(True)
        value_pager.addWidget(self.value_page_label, 1)
        self.value_prev_btn.clicked.connect(lambda: self._navigate_value_page(-1))
        self.value_next_btn.clicked.connect(lambda: self._navigate_value_page(1))
        det_l.addLayout(value_pager)
        actions = WrapLayout()
        self.refresh_val_btn = QPushButton()
        apply_button(self.refresh_val_btn, 'secondary', compact=True)
        self.refresh_val_btn.clicked.connect(self._load_key_value)
        self.copy_val_btn = QPushButton()
        apply_button(self.copy_val_btn, 'ghost', compact=True)
        self.copy_val_btn.clicked.connect(self._copy_value)
        self.export_key_btn = QPushButton()
        apply_button(self.export_key_btn, 'ghost', compact=True)
        self.export_key_btn.clicked.connect(self._open_key_export)
        self.copy_key_btn = QPushButton()
        apply_button(self.copy_key_btn, 'ghost', compact=True)
        self.copy_key_btn.clicked.connect(self._copy_key_name)
        self.del_btn = QPushButton()
        apply_button(self.del_btn, 'ghost', compact=True)
        self.del_btn.clicked.connect(self._delete_key)
        self.rename_btn = QPushButton()
        apply_button(self.rename_btn, 'ghost', compact=True)
        self.rename_btn.clicked.connect(self._rename_key)
        self.expire_btn = QPushButton()
        apply_button(self.expire_btn, 'ghost', compact=True)
        self.expire_btn.clicked.connect(self._expire_key)
        for w in (self.refresh_val_btn, self.copy_val_btn, self.export_key_btn, self.copy_key_btn,
                  self.del_btn, self.rename_btn, self.expire_btn):
            actions.addWidget(w)
        det_l.addLayout(actions)
        add_scrollable_tab(detail, 'Key 详情')

        # Tab 2：AI 助手
        ai_page = QWidget()
        ai_l = QVBoxLayout(ai_page)
        ai_l.setContentsMargins(10, 10, 10, 10)
        self.ai_banner = QLabel()
        self.ai_banner.setObjectName('page-context')
        self.ai_banner.setWordWrap(True)
        ai_l.addWidget(self.ai_banner)
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
        add_scrollable_tab(ai_page, 'AI 助手')

        # 底部命令行（Console 只放右侧下方）
        bottom = QFrame()
        bottom.setObjectName('redis-console-pane')
        bottom_l = QVBoxLayout(bottom)
        bottom_l.setContentsMargins(8, 8, 8, 8)
        cmd_bar = QHBoxLayout()
        self.cmd_prompt = QLabel('127.0.0.1:6379>')
        self.cmd_prompt.setObjectName('field-hint')
        # 提示符含主机地址，窄窗口/大字号下允许截断（悬停看全），不撑宽工作台
        self.cmd_prompt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cmd_prompt.setMinimumWidth(80)
        self.cmd_prompt.setMaximumWidth(260)
        self.cmd_input = QLineEdit()
        size_line(self.cmd_input, 'path')
        self.cmd_input.setMinimumWidth(140)
        self.cmd_input.returnPressed.connect(self._run_command)
        self.cmd_btn = QPushButton()
        apply_button(self.cmd_btn, 'primary', compact=True)
        self.cmd_btn.clicked.connect(self._run_command)
        cmd_bar.addWidget(self.cmd_prompt)
        cmd_bar.addWidget(self.cmd_input, 1)
        cmd_bar.addWidget(self.cmd_btn)
        bottom_l.addLayout(cmd_bar)
        self.cmd_output = QPlainTextEdit()
        self.cmd_output.setReadOnly(True)
        self.cmd_output.setMinimumHeight(96)
        bottom_l.addWidget(self.cmd_output, 1)
        self.bottom_frame = bottom

        # 右侧上下垂直 Splitter：上面是详情页签，下面是控制台
        self._bottom_split = QSplitter(Qt.Orientation.Vertical)
        self._bottom_split.setHandleWidth(16)
        self._bottom_split.setProperty('prismGutter', True)
        self._bottom_split.addWidget(self.side_tabs)
        self._bottom_split.addWidget(bottom)
        self._bottom_split.setStretchFactor(0, 3)
        self._bottom_split.setStretchFactor(1, 1)
        install_splitter_prefs(
            self._bottom_split, defaults=[520, 200], page_id='redis-workbench', tab_id='right_split',
            min_sizes=[240, 120], accessible_name='Redis 右侧上下分隔',
        )

        # 全局主水平 Splitter：左全高 Key 树与列表 | 右侧上下工作台与控制台
        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split.setHandleWidth(16)
        self.main_split.setProperty('prismGutter', True)
        self.main_split.addWidget(left)
        self.main_split.addWidget(self._bottom_split)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        root.addWidget(self.main_split, 1)
        install_splitter_prefs(
            self.main_split, defaults=[240, 960], page_id='redis-workbench', tab_id='main_split',
            defaults_for_extent=lambda extent: [240, max(360, extent - 240)],
            min_sizes=[240, 480], accessible_name='Redis 左右主分隔',
        )

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.conn_new_btn.setText('新建连接' if zh else 'New')
        self.conn_edit_btn.setText('编辑' if zh else 'Edit')
        self.conn_del_btn.setText('删除' if zh else 'Delete')
        self.test_btn.setText('测试连接' if zh else 'Test')
        self.refresh_btn.setText('刷新' if zh else 'Refresh')
        self.home_btn.setText('返回首页' if zh else 'Home')
        self.home_btn.setToolTip('返回首页' if zh else 'Return to Home')
        self.key_filter.setPlaceholderText('搜索 Key / 前缀' if zh else 'Search key / prefix')
        self.prefix_label.setText('前缀树 (Prefix Tree)' if zh else 'Prefix Tree')
        if not getattr(self, '_selected_prefix', ''):
            self.key_list_label.setText('全部 Key' if zh else 'All Keys')
        else:
            self.key_list_label.setText(f'前缀: {self._selected_prefix}' if zh else f'Prefix: {self._selected_prefix}')
        self.clear_prefix_btn.setText('清空前缀' if zh else 'Clear')
        if hasattr(self, 'fmt_label'):
            self.fmt_label.setText('编码视图:' if zh else 'Encoding:')
        self.load_more_btn.setText('加载更多' if zh else 'Load more')
        self.nodes_title.setText('节点' if zh else 'Nodes')
        self.info_title.setText('Redis 信息' if zh else 'Redis INFO')
        self.info_all_btn.setText('查看全部 INFO' if zh else 'Show all INFO')
        self.nodes_table.setHorizontalHeaderLabels(
            ['节点', '角色', '状态', 'Keys', 'Expires', 'Avg TTL', 'Memory']
            if zh else
            ['Node', 'Role', 'Status', 'Keys', 'Expires', 'Avg TTL', 'Memory']
        )
        self.info_table.setHorizontalHeaderLabels(['Name', 'Value'])
        self.card_keys.caption.setText('Keys')
        self.card_memory.caption.setText('Memory')
        self.card_nodes.caption.setText('Nodes')
        self.card_version.caption.setText('Version')
        self.refresh_val_btn.setText('刷新值' if zh else 'Refresh value')
        self.copy_val_btn.setText('复制值' if zh else 'Copy value')
        self.export_key_btn.setText('导出选中 Key' if zh else 'Export selected key')
        self.value_prev_btn.setText('上一页' if zh else 'Previous')
        self.value_next_btn.setText('下一页 / 块' if zh else 'Next page / chunk')
        self.raw_key_combo.setToolTip('原始 Key（Base64）' if zh else 'Raw key (Base64)')
        if self._value_page is not None or self._value_notice:
            self._update_value_page_label()
        self._sync_value_controls()
        self.copy_key_btn.setText('复制 Key 名' if zh else 'Copy key name')
        self.del_btn.setText('删除 Key' if zh else 'Delete key')
        self.rename_btn.setText('重命名' if zh else 'Rename')
        self.expire_btn.setText('设置过期' if zh else 'Set TTL')
        self.side_tabs.setTabText(0, 'Overview')
        self.side_tabs.setTabText(1, 'Key 详情' if zh else 'Key details')
        self.side_tabs.setTabText(2, 'AI 助手' if zh else 'AI assistant')
        if hasattr(self, 'data_center_agent'):
            self.side_tabs.setTabText(3, '只读 Agent' if zh else 'Read-only Agent')
            self.data_center_agent.set_language(language)
        self.ai_send_btn.setText('发送' if zh else 'Send')
        self.ai_input.setPlaceholderText(
            '描述 Redis 操作需求，AI 基于当前选中 Key 生成命令/Lua 脚本（不会自动执行）'
            if zh else
            'Describe Redis task; AI drafts commands/Lua from the selected key (never auto-runs)'
        )
        self.cmd_btn.setText('执行' if zh else 'Run')
        self.cmd_input.setPlaceholderText(
            '输入 Redis 命令，如 GET key / HGETALL hash / SCAN 0 MATCH * COUNT 20'
            if zh else 'Redis command, e.g. GET key / HGETALL hash'
        )
        if hasattr(self, 'nosql_draft_tabs'):
            self.nosql_draft_tabs.set_language(language)

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import page_spacing_for_mode
        from ui.splitter_prefs import layout_bucket
        self._layout_mode = mode
        if hasattr(self, 'root_layout') and self.root_layout is not None:
            self.root_layout.setSpacing(page_spacing_for_mode(mode, low_height))
        if hasattr(self, 'main_split') and self.main_split is not None:
            if mode in ('narrow', 'compact'):
                col_defs = [240, 680]
                col_mins = [200, 360]
            else:
                col_defs = [240, 960]
                col_mins = [240, 480]
            install_splitter_prefs(
                self.main_split,
                defaults=col_defs,
                defaults_for_extent=lambda extent: [240, max(360, extent - 240)],
                page_id='redis-workbench',
                tab_id='main_split',
                bucket=layout_bucket(mode),
                min_sizes=col_mins,
                accessible_name='Redis 左右主分隔',
            )

    def _title(self) -> str:
        # 仍用于业务提示 / 对话框标题，勿删。
        return 'Redis 工作台' if self.language == 'zh' else 'Redis Workbench'

    def _make_summary_card(self, caption: str) -> QFrame:
        card = QFrame()
        card.setObjectName('dashboard-task-card')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        cap = QLabel(caption)
        cap.setObjectName('field-hint')
        val = QLabel('—')
        val.setObjectName('section-title')
        lay.addWidget(cap)
        lay.addWidget(val)
        card.caption = cap
        card.value = val
        return card

    def _dash(self, value) -> str:
        if value is None or value == '':
            return '—'
        return str(value)

    def _fmt_count(self, value, *, exact: bool) -> str:
        if value is None:
            return '—'
        text = f'{int(value):,}'
        return text if exact else f'{text}+'

    def _scan_pattern(self) -> str:
        text = (self._tree_filter or '').strip()
        if not text or text == '*':
            return '*'
        if any(ch in text for ch in '*?[]'):
            return text
        return f'*{text}*'

    def _clear_workspace(self):
        self._reset_value_selection()
        self._key_identities = {}
        self._key_identity_scope = None
        self._scan.start(self._scan_pattern())
        self._key_cache = []
        self._selected_prefix = ''
        self._selected_key = ''
        self._selected_type = ''
        self._overview = None
        self.key_tree.clear()
        self.key_list.clear()
        self.nodes_table.setRowCount(0)
        self.info_table.setRowCount(0)
        for card in (self.card_keys, self.card_memory, self.card_nodes, self.card_version):
            card.value.setText('…')
        self.ov_hint.setText('加载中…' if self.language == 'zh' else 'Loading…')
        self.key_stats.setText('加载中…' if self.language == 'zh' else 'Loading…')
        self.load_more_btn.hide()
        self.key_name.setText('')
        self.key_meta.setText('')
        if hasattr(self, 'key_type_badge'):
            self.key_type_badge.hide()
            self.key_ttl_badge.hide()
            self.key_size_badge.hide()

    # ── 连接管理 ──────────────────────────────────────────────────────────

    def _reload_connections(self, select_id: str = ''):
        try:
            rows = [item for item in load_connections() if str(item.get('dialect') or '').lower() == 'redis']
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
                '无 Redis 连接' if self.language == 'zh' else 'No Redis connection', None
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
            draft = make_nosql_draft("redis", item, self._nosql_profile_host)
        except NoSqlHistoryError as error:
            draft = NoSqlDraft(
                draft_id=uuid.uuid4().hex,
                provider="redis",
                target=None,
                editor_texts={"command": ""},
                runnable=False,
                validation_code=error.code,
            )
        self.nosql_draft_tabs.add_draft(draft)
        return draft

    def _save_nosql_draft_text(self, draft):
        if isinstance(draft, NoSqlDraft):
            draft.editor_texts["command"] = self.cmd_input.text()

    def _load_nosql_draft_text(self, draft):
        if self._value_context is not None and (draft is None or draft.target != self._value_context.target):
            self._reset_value_selection()
        self.cmd_output.clear()
        if not isinstance(draft, NoSqlDraft):
            self.cmd_input.clear()
            self.cmd_btn.setEnabled(False)
            self.nosql_draft_tabs.set_status(None)
            self._refresh_query_status()
            return
        self.cmd_input.setText(draft.editor_texts.get("command", ""))
        self.cmd_btn.setEnabled(bool(draft.runnable and draft.target is not None))
        self.nosql_draft_tabs.set_status(draft)
        self._set_nosql_prompt(draft)
        self._refresh_query_status()

    def _set_nosql_prompt(self, draft):
        if isinstance(draft, NoSqlDraft) and draft.target is not None:
            self.cmd_prompt.setText(f"{draft.target.connection_id}[db{draft.db_index}]>")
            self.cmd_prompt.setToolTip(self.cmd_prompt.text())

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
        draft = restore_nosql_history_record(record, "redis", self._nosql_profile_host)
        self.nosql_draft_tabs.add_draft(draft)

    def _data_center_agent_selection(self):
        item = self._current_conn()
        if not item:
            return {'reason': '未选择 Redis 目标连接'}
        return {'profile': item, 'database': str(item.get('db', item.get('database', 0)) or 0)}

    def _put_data_center_agent_command(self, text: str):
        command = str(text or '').strip()
        if command:
            self.cmd_input.setText(command)
            self.cmd_input.setFocus()

    def agent_shutdown_cleanup(self) -> bool:
        host = getattr(self, 'data_center_agent', None)
        return True if host is None else bool(host.agent_shutdown_cleanup())

    def _track_legacy_worker(self, worker):
        self._legacy_workers.add(worker)
        _ACTIVE_REDIS_WORKERS.add(worker)
        worker.finished.connect(lambda w=worker: _release_active_redis_worker(w))
        worker.finished.connect(self._on_legacy_worker_finished)

    def _on_legacy_worker_finished(self):
        worker = self.sender()
        if worker is None:
            return
        self._legacy_workers.discard(worker)
        _release_active_redis_worker(worker)
        self._query_status_workers.pop(worker, None)
        if self._worker is worker:
            self._worker = None
        if self._bg_worker is worker:
            self._bg_worker = None
        if not self._workbench_shutdown_requested:
            self._refresh_query_status()
            self._sync_value_controls()
            if self._pending_value_request is not None and self._worker is None:
                self._value_queue_timer.start(0)

    def workbench_shutdown_cleanup(self) -> bool:
        """Request cooperative Redis worker cancellation and retain live threads."""
        self._workbench_shutdown_requested = True
        self._pending_value_request = None
        self._value_queue_timer.stop()
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
        if self._bg_worker is not None and self._bg_worker.isRunning():
            self._bg_worker.cancelled = True
        item = self._current_conn()
        self._clear_workspace()
        if item:
            mode = str(item.get('mode') or '').lower()
            if mode == 'cluster':
                self.db_badge.setText(f"集群 · {item.get('name') or item.get('host') or ''}")
            else:
                self.db_badge.setText(f"{item.get('host') or ''}:{item.get('port') or 6379}")
            self.cmd_prompt.setText(f"{item.get('host') or '127.0.0.1'}:{item.get('port') or 6379}>")
            self.cmd_prompt.setToolTip(self.cmd_prompt.text())
            self._refresh_keys()
        else:
            self.db_badge.setText('未连接' if self.language == 'zh' else 'Not connected')
            self.key_stats.setText('未连接 Redis' if self.language == 'zh' else 'Not connected')
            self.ov_hint.setText('未连接' if self.language == 'zh' else 'Not connected')
            for card in (self.card_keys, self.card_memory, self.card_nodes, self.card_version):
                card.value.setText('—')
        self._update_ai_banner()
        host = getattr(self, 'data_center_agent', None)
        if host is not None:
            host.target_changed()
        self._set_nosql_prompt(self.nosql_draft_tabs.current_draft)

    def _edit_connection(self, new=False):
        current = None if new else self._current_conn()
        dialog = ConnectionDialog(self.language, current, self, locked_dialect='redis')
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
                              f"删除 Redis 连接「{item.get('name') or ''}」？"
                              if zh else f"Delete Redis connection '{item.get('name') or ''}'?",
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

    # ── Key 树 ────────────────────────────────────────────────────────────

    def _refresh_keys(self):
        item = self._current_conn()
        if not item:
            return
        gen = self._scan.start(self._scan_pattern())
        self._key_cache = []
        self._run_bg('bootstrap', item, pattern=self._scan.pattern, cursor=0,
                     limit=2000, generation=gen, append=False)

    def _start_search_scan(self):
        item = self._current_conn()
        if not item:
            return
        gen = self._scan.start(self._scan_pattern())
        self._key_cache = []
        self._selected_prefix = ''
        self.key_list.clear()
        self._run_bg('scan', item, pattern=self._scan.pattern, cursor=0,
                     limit=2000, generation=gen, append=False)

    def _load_more_keys(self):
        item = self._current_conn()
        if not item or self._scan.finished:
            return
        self._run_bg(
            'scan', item, pattern=self._scan.pattern, cursor=self._scan.cursor,
            limit=2000, generation=self._scan.generation, append=True,
        )

    def _on_filter_changed(self, text):
        self._tree_filter = text.strip()
        self._search_timer.start()

    def _clear_selected_prefix(self):
        self._selected_prefix = ''
        self.key_list_label.setText('全部 Key' if self.language == 'zh' else 'All Keys')
        self.clear_prefix_btn.hide()
        self.key_tree.clearSelection()
        self._render_key_list(self._key_cache)

    def _render_prefix_tree(self, keys: list[str], *, incomplete: bool):
        index = build_prefix_index(keys, incomplete=incomplete)
        self.key_tree.setUpdatesEnabled(False)
        self.key_tree.blockSignals(True)
        self.key_tree.clear()

        def add_nodes(parent, nodes, depth=0):
            for node in nodes:
                item = QTreeWidgetItem(parent)
                count = node.get('count') or 0
                label = f'{count}+' if node.get('incomplete') else f'{count}'
                item.setText(0, node.get('name') or '')
                item.setText(1, label)
                folder_ico = qicon('folder-open', size=14)
                if not folder_ico.isNull():
                    item.setIcon(0, folder_ico)
                path_str = str(node.get('path') or '')
                breadcrumb_path = path_str.replace(':', ' / ')
                item.setToolTip(0, f"前缀：{breadcrumb_path}（基于当前扫描结果）" if self.language == 'zh' else f"Prefix: {breadcrumb_path}")
                item.setData(0, Qt.ItemDataRole.UserRole, {
                    'kind': 'prefix', 'path': path_str,
                })
                # 父节点加粗，强化结构清晰度
                if depth == 0 or bool(node.get('children')):
                    f = item.font(0)
                    f.setBold(True)
                    item.setFont(0, f)
                item.setExpanded(True)
                add_nodes(item, node.get('children') or [], depth + 1)

        add_nodes(self.key_tree.invisibleRootItem(), index.get('prefixes') or [], 0)
        self.key_tree.blockSignals(False)
        self.key_tree.setUpdatesEnabled(True)
        if not (index.get('prefixes') or []) and not keys:
            self.key_stats.setText(
                '当前范围未发现 Key' if self.language == 'zh' else 'No keys in this range'
            )
        self._render_key_list(keys)

    def _render_key_list(self, keys: list[str]):
        shown = keys_for_prefix(keys, self._selected_prefix)
        self.key_list.setUpdatesEnabled(False)
        self.key_list.clear()
        key_ico = qicon('shield-key', size=14)
        for key in shown[:5000]:
            item = QListWidgetItem(key)
            if not key_ico.isNull():
                item.setIcon(key_ico)
            f = item.font()
            from ui.theme_manager import FONT_MONO_FAMILIES
            f.setFamilies(list(FONT_MONO_FAMILIES))
            item.setFont(f)
            item.setToolTip(key)
            self.key_list.addItem(item)
        self.key_list.setUpdatesEnabled(True)
        extra = ''
        if getattr(self._scan, 'partial', False):
            extra = ' · 部分节点扫描失败，Key 列表可能不完整' if self.language == 'zh' else ' · some nodes failed, key list may be incomplete'
        elif not self._scan.finished:
            extra = ' · 当前扫描结果，不代表全集' if self.language == 'zh' else ' · sampled, not complete'
        self.key_stats.setText(
            f'已加载 {len(keys)} 个 Key · 列表 {len(shown)}{extra}'
            if self.language == 'zh' else
            f'Loaded {len(keys)} keys · list {len(shown)}{extra}'
        )
        self.load_more_btn.setVisible(not self._scan.finished)

    def _on_prefix_clicked(self, item: QTreeWidgetItem, _column: int):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get('kind') != 'prefix':
            return
        prefix = str(meta.get('path') or '')
        self._selected_prefix = prefix
        self.key_list_label.setText(
            f'前缀: {prefix}' if self.language == 'zh' else f'Prefix: {prefix}'
        )
        self.clear_prefix_btn.show()
        self._render_key_list(self._key_cache)

    def _on_key_list_clicked(self, item: QListWidgetItem):
        import base64

        self._reset_value_selection()
        if item is None:
            return
        key = item.text()
        self._selected_key = key
        self._selected_type = ''
        self.key_name.setText(self._selected_key)
        self.side_tabs.setCurrentIndex(1)
        keys = tuple(sorted(self._key_identities.get(key, ())))
        self.raw_key_combo.blockSignals(True)
        if len(keys) > 1:
            self.raw_key_combo.addItem('请选择原始 Key' if self.language == 'zh' else 'Select raw key', None)
        for raw in keys:
            if len(raw) <= 64 * 1024:
                encoded = base64.b64encode(raw).decode('ascii')
                self.raw_key_combo.addItem(f'Base64: {encoded[:120]} ({len(raw)} bytes)', raw)
                self.raw_key_combo.setItemData(self.raw_key_combo.count() - 1, encoded, Qt.ItemDataRole.ToolTipRole)
        self.raw_key_combo.blockSignals(False)
        self._select_raw_value_key()
        if not keys:
            self.value_page_label.setText('请刷新当前工作区 Key 列表。' if self.language == 'zh' else 'Refresh this workspace key list.')

    def _reset_value_selection(self):
        self._selected_key = ''
        self._selected_type = ''
        self.key_name.clear()
        self._value_context = None
        self._value_page = None
        self._value_stale = False
        self._value_notice = ''
        self._value_positions = [0]
        self._value_page_index = 0
        self._pending_value_request = None
        self.raw_key_combo.blockSignals(True)
        self.raw_key_combo.clear()
        self.raw_key_combo.blockSignals(False)
        self.string_value.clear()
        self._raw_string_bytes = b''
        self._raw_string_text = ''
        self._string_inspected = {}
        self.key_meta.clear()
        for table in (self.hash_table, self.list_table, self.zset_table):
            table.setRowCount(0)
        for badge in (self.key_type_badge, self.key_ttl_badge, self.key_size_badge):
            badge.hide()
        self.value_page_label.clear()
        self._sync_value_controls()

    def _select_raw_value_key(self):
        draft = self.nosql_draft_tabs.current_draft
        key = self.raw_key_combo.currentData()
        self._pending_value_request = None
        self._value_context = None
        self._value_page = None
        self._value_stale = False
        self._value_notice = ''
        self._value_positions = [0]
        self._value_page_index = 0
        self.string_value.clear()
        self._raw_string_bytes = b''
        self._raw_string_text = ''
        self._string_inspected = {}
        self.value_page_label.clear()
        for table in (self.hash_table, self.list_table, self.zset_table):
            table.setRowCount(0)
        self.key_meta.clear()
        for badge in (self.key_type_badge, self.key_ttl_badge, self.key_size_badge):
            badge.hide()
        self._sync_value_controls()
        if type(key) is not bytes or not isinstance(draft, NoSqlDraft) or draft.target is None:
            self._sync_value_controls()
            return
        target = draft.target
        if self._key_identity_scope != (target.connection_id, target.profile_revision, target.database, draft.mode):
            self.value_page_label.setText('请刷新当前工作区 Key 列表。' if self.language == 'zh' else 'Refresh this workspace key list.')
            return
        self._value_context = RedisKeyContext(target, key, uuid.uuid4().hex)
        self._load_key_meta()

    def _load_key_meta(self):
        self._request_value_page('key_meta', 0, 0)

    def _load_key_value(self):
        self._request_value_page('key_value', 0, 0)

    def _request_value_page(self, kind, index, position):
        if self._value_context is None or self._workbench_shutdown_requested:
            return
        self._pending_value_request = (kind, index, position, self._value_context)
        self._drain_value_request()

    def _drain_value_request(self):
        if self._workbench_shutdown_requested or self._worker is not None or self._pending_value_request is None:
            return
        kind, index, position, context = self._pending_value_request
        self._pending_value_request = None
        draft = self.nosql_draft_tabs.current_draft
        if context != self._value_context or draft is None or draft.target != context.target:
            return
        try:
            profile = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError:
            self._value_stale = True
            self._value_notice = 'PROFILE_MISMATCH'
            self._update_value_page_label()
            return
        expected_kind = self._value_page.kind if index and self._value_page is not None else ''
        self._value_stale = self._value_page is not None
        self._value_notice = ''
        self._update_value_page_label()
        accepted = self._run_worker(kind, {**profile, 'database': int(context.target.database)},
                                    draft_id=draft.draft_id, context=context, position=position,
                                    page_index=index, expected_kind=expected_kind)
        if not accepted:
            self._value_stale = True
            self._value_notice = 'WORKER_START_FAILED'
            self._update_value_page_label()
        self._sync_value_controls()

    def _navigate_value_page(self, direction):
        if self._worker is not None or self._value_page is None:
            return
        index = self._value_page_index + direction
        if direction < 0 and index >= 0:
            self._request_value_page('key_value', index, self._value_positions[index])
        elif direction > 0 and self._value_page.has_more:
            self._request_value_page('key_value', index, self._value_page.next_position)

    def _sync_value_controls(self):
        draft = self.nosql_draft_tabs.current_draft
        available = (not self._workbench_shutdown_requested and self._worker is None and self._pending_value_request is None and self._value_context is not None
                     and draft is not None and draft.target == self._value_context.target)
        self.value_prev_btn.setEnabled(bool(available and self._value_page is not None and self._value_page_index > 0))
        self.value_next_btn.setEnabled(bool(available and self._value_page is not None and self._value_page.has_more))
        self.refresh_val_btn.setEnabled(bool(available))
        for button in (self.del_btn, self.rename_btn, self.expire_btn):
            button.setEnabled(bool(available))

    def _apply_value_page(self, worker, payload):
        page = payload['page']
        self._value_page = page
        self._value_stale = False
        self._value_notice = ''
        index = worker.kwargs['page_index']
        self._value_positions[index:] = [page.position]
        self._value_page_index = index
        self._selected_type = page.kind
        self.key_type_badge.setText(f'TYPE: {page.kind.upper()}')
        self.key_ttl_badge.setText(format_key_ttl_badge(page.ttl, self.language))
        self.key_type_badge.show()
        self.key_ttl_badge.show()
        self.key_meta.setText(f'{page.context.target.connection_id} / DB {page.context.target.database} · {page.kind}')
        tables = (self.hash_table, self.list_table, self.zset_table)
        sorting = [table.isSortingEnabled() for table in tables]
        for table in tables:
            table.setSortingEnabled(False)
        self._render_value(page.kind, payload['value'])
        for table, enabled in zip(tables, sorting):
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        item.setData(Qt.ItemDataRole.UserRole, row)
                if page.kind == 'list' and table is self.list_table:
                    table.item(row, 0).setText(str(page.position + row))
            table.setSortingEnabled(enabled)
        self._update_value_page_label()
        if page.total is not None:
            self.key_size_badge.setText(f"{'SIZE' if page.kind == 'string' else 'LENGTH'}: {page.total}")
            self.key_size_badge.show()
        else:
            self.key_size_badge.hide()
        self._sync_value_controls()

    def _update_value_page_label(self):
        page = self._value_page
        notices = {
            'REDIS_VALUE_MISSING': ('Key 已过期或不存在。', 'Key expired or does not exist.'),
            'REDIS_VALUE_KEY_CHANGED': ('读取期间 Key 或类型已变化。', 'The key or type changed during reading.'),
            'REDIS_VALUE_ITEM_LIMIT': ('单个值超过 4 MiB 浏览上限，请使用导出。', 'An item exceeds the 4 MiB viewing limit; use export.'),
            'REDIS_VALUE_PAGE_LIMIT': ('本批超过 8 MiB / 1000 项浏览预算，请使用导出。', 'This batch exceeds the 8 MiB / 1000-item viewing budget; use export.'),
            'REDIS_VALUE_UNSUPPORTED_TYPE': ('暂不支持此类型的浏览。', 'Viewing is unavailable for this type.'),
            'PROFILE_MISMATCH': ('原连接已变化或不可用。', 'Original connection changed or unavailable.'),
            'WORKER_START_FAILED': ('读取未能启动。', 'Read did not start.'),
        }
        notice = notices.get(self._value_notice, ('读取失败。', 'Read failed.'))[0 if self.language == 'zh' else 1] if self._value_notice else ''
        if self._value_stale:
            notice += (' 上次结果未更新，可刷新重试。' if self.language == 'zh' else ' Previous result retained; refresh to retry.') if self._value_notice else (' 正在读取；上次结果尚未更新。' if self.language == 'zh' else ' Reading; previous result not updated.')
        if page is None:
            self.value_page_label.setText(notice)
            return
        amount = len(page.data)
        suffix = ('还有下一页 / 块' if page.has_more else '当前读取已到末尾') if self.language == 'zh' else ('More pages / chunks' if page.has_more else 'End of current read')
        if page.kind == 'string':
            text = f'字节 {page.position}–{page.position + amount} / {page.total} · 当前块' if self.language == 'zh' else f'Bytes {page.position}–{page.position + amount} / {page.total} · current chunk'
        else:
            text = f'第 {self._value_page_index + 1} 页 · 本页 {amount} 项' if self.language == 'zh' else f'Page {self._value_page_index + 1} · {amount} items'
        self.value_page_label.setText(text + ' · ' + suffix + (' · ' + notice if notice else ''))

    def _copy_key_name(self):
        if self._selected_key:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._selected_key)

    def _copy_value(self):
        value = self.string_value.toPlainText()
        if value:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(value)

    def _open_key_export(self):
        from ui.data_center.redis_export import RedisExportDialog

        selected = self.key_list.currentItem()
        draft = self.nosql_draft_tabs.current_draft
        if self._worker is not None or self._workbench_shutdown_requested:
            return
        if selected is None or not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            show_warning(self, self._title(), '请先选择 Key 和可用工作区。' if self.language == 'zh' else 'Select a key and an available workspace first.')
            return
        target = draft.target
        scope = (target.connection_id, target.profile_revision, target.database, draft.mode)
        keys = tuple(sorted(self._key_identities.get(selected.text(), ())))
        if self._key_identity_scope != scope or not keys:
            show_warning(self, self._title(), '请在当前工作区刷新 Key 列表后选择并导出。' if self.language == 'zh' else 'Refresh the key list in this workspace before selecting and exporting.')
            return
        if any(len(key) > 64 * 1024 for key in keys):
            show_warning(self, self._title(), 'Key 名称超过 64 KiB 导出上限。' if self.language == 'zh' else 'The key name exceeds the 64 KiB export limit.')
            return

        def dispatch(values, attach):
            if not any(candidate is draft for candidate in self.nosql_draft_tabs.drafts()) or draft.target != target:
                raise NoSqlHistoryError('PROFILE_MISMATCH')
            profile = validate_nosql_target(draft, self._nosql_profile_host)
            item = {**profile, 'database': int(target.database)}
            return self._run_worker('export_key', item, draft_id=draft.draft_id, on_created=attach, **values)

        dialog = RedisExportDialog(target, keys, dispatch, language=self.language, parent=self)
        self._export_dialog = dialog
        dialog.show()

    def _delete_key(self):
        context = self._value_context
        if context is None:
            return
        zh = self.language == 'zh'
        if not confirm_action(self, self._title(),
                              f'确定删除 key「{self._selected_key}」？此操作不可撤销'
                              if zh else f'Delete key "{self._selected_key}"? This cannot be undone.',
                              confirm_text='删除' if zh else 'Delete', danger=True):
            return
        self._start_captured_key_mutation(context, 'delete')

    def _rename_key(self):
        context = self._value_context
        if context is None:
            return
        zh = self.language == 'zh'
        new_key, ok = QInputDialog.getText(self, '重命名 Key' if zh else 'Rename key',
                                           '新 Key 名：' if zh else 'New key name:',
                                           text=self._selected_key)
        if not ok or not new_key.strip():
            return
        self._start_captured_key_mutation(context, 'rename', new_key=new_key.strip())

    def _expire_key(self):
        context = self._value_context
        if context is None:
            return
        zh = self.language == 'zh'
        seconds, ok = QInputDialog.getText(self, '设置 TTL' if zh else 'Set TTL',
                                           '秒（-1 表示永不过期）：' if zh else 'Seconds (-1 = never expires):',
                                           text='-1')
        if not ok:
            return
        try:
            sec = int(seconds.strip())
        except ValueError:
            show_warning(self, self._title(), '请输入整数' if zh else 'Enter an integer')
            return
        self._start_captured_key_mutation(context, 'expire', seconds=sec)

    def _start_captured_key_mutation(self, context, kind, **values):
        draft = next((candidate for candidate in self.nosql_draft_tabs.drafts() if candidate.target == context.target), None)
        if draft is None or self._workbench_shutdown_requested:
            return
        try:
            profile = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError:
            self.value_page_label.setText('原连接已变化或不可用，未执行操作。' if self.language == 'zh' else 'Original connection changed or unavailable; operation not sent.')
            return
        self._run_worker(kind, {**profile, 'database': int(context.target.database)}, draft_id=draft.draft_id,
                         key=context.key, context=context, **values)
        self._sync_value_controls()

    # ── 命令行 ────────────────────────────────────────────────────────────

    def _run_command(self):
        editor_text = self.cmd_input.text()
        sql = editor_text.strip()
        if not sql:
            return
        from tools.sql_guard import reject_reason
        reason = reject_reason(sql, 'redis')
        if reason:
            show_warning(self, self._title(), reason)
            return
        draft = self.nosql_draft_tabs.current_draft
        if not isinstance(draft, NoSqlDraft) or not draft.runnable or draft.target is None:
            reason = draft.validation_code if isinstance(draft, NoSqlDraft) else "PROFILE_UNAVAILABLE"
            show_warning(self, self._title(), reason)
            return
        try:
            item = validate_nosql_target(draft, self._nosql_profile_host)
        except NoSqlHistoryError as error:
            draft.runnable = False
            draft.validation_code = error.code
            self.nosql_draft_tabs.update_draft(draft)
            self.nosql_draft_tabs.set_status(draft)
            self.cmd_btn.setEnabled(False)
            show_warning(self, self._title(), error.code)
            return
        try:
            snapshot = capture_redis_command(
                draft.target,
                editor_text,
                sql,
                mode=draft.mode,
                db_index=int(draft.db_index),
            )
            history_capture = make_nosql_history_capture(snapshot)
            dispatch_text = snapshot.dispatched_text
        except NoSqlWorkspaceCodecError:
            # History is best-effort; an accepted command remains executable.
            snapshot = None
            history_capture = None
            dispatch_text = sql
        accepted = self._run_worker(
            'command', item, draft_id=draft.draft_id,
            history_capture=history_capture, sql=dispatch_text,
        )
        if accepted:
            if snapshot is not None:
                draft.snapshot = snapshot
            self._append_cmd(f'{self.cmd_prompt.text()} {dispatch_text}')
            self.cmd_input.clear()
            draft.editor_texts['command'] = ''

    def _append_cmd(self, line: str, is_ok: bool = True):
        self.cmd_output.appendPlainText(line)

    # ── AI 助手 ───────────────────────────────────────────────────────────

    def _update_ai_banner(self):
        zh = self.language == 'zh'
        if self._selected_key:
            self.ai_banner.setText(
                f'📌 当前上下文：Key「{self._selected_key}」· 类型 {self._selected_type or "?"}'
            )
        else:
            item = self._current_conn()
            if item:
                self.ai_banner.setText(
                    f'📌 已连接 {item.get("name") or item.get("host")}，点击左侧 Key 后 AI 可感知其类型与 TTL'
                    if zh else
                    f'📌 Connected to {item.get("name") or item.get("host")}. Select a key for AI context.'
                )
            else:
                self.ai_banner.setText('📌 未连接，AI 仅能生成通用 Redis 命令' if zh else 'Not connected')

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
        if self._selected_key:
            context = f'当前选中 Key: {self._selected_key}，类型: {self._selected_type or "未知"}。'
        system = (
            '你是内网 Redis 助手。基于用户描述生成 Redis 命令或 Lua 脚本，'
            '只输出命令/脚本和简短说明，不要自动执行，不要包含删除生产数据的危险命令。'
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

    def _run_bg(self, kind, item, **kwargs):
        if self._workbench_shutdown_requested:
            return
        if self._bg_worker is not None:
            if self._bg_worker.isRunning():
                self._bg_worker.cancelled = True
            self._query_status_workers.pop(self._bg_worker, None)
        worker = _RedisWorker(kind, item, connection_opener=getattr(self, "_connection_opener", None), **kwargs)
        self._bg_worker = worker
        self._track_legacy_worker(worker)
        self._register_query_status_worker(worker, kind)
        worker.completed.connect(self._on_worker_done)
        worker.failed.connect(self._on_worker_failed)
        worker.start()

    def _run_worker(self, kind, item, *, draft_id=None, history_capture=None, on_created=None, **kwargs):
        if self._workbench_shutdown_requested:
            return False
        if kind in ('scan', 'bootstrap', 'overview'):
            return self._run_bg(kind, item, **kwargs)
        if self._worker is not None and self._worker.isRunning():
            return False
        if self._worker is not None:
            self._query_status_workers.pop(self._worker, None)
        worker = _RedisWorker(kind, item, nosql_draft_id=draft_id or "", connection_opener=getattr(self, "_connection_opener", None), **kwargs)
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
        self._register_query_status_worker(worker, kind)
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
            _release_active_redis_worker(worker)
            self._query_status_workers.pop(worker, None)
            if self._worker is worker:
                self._worker = None
            return False
        return True

    def _register_query_status_worker(self, worker, kind):
        self._query_status_sequence += 1
        self._query_status_workers[worker] = (
            self._query_status_sequence, kind, 'running', '',
        )
        worker.finished.connect(self._on_query_status_worker_finished)
        if not self._workbench_shutdown_requested:
            self._refresh_query_status()

    def _on_query_status_worker_finished(self):
        worker = self.sender()
        if worker is None:
            return
        self._query_status_workers.pop(worker, None)
        if not self._workbench_shutdown_requested:
            self._refresh_query_status()

    def _record_query_status_result(self, worker, kind, state, detail=''):
        try:
            entry = self._query_status_workers.get(worker)
        except TypeError:
            return
        if entry is None:
            return
        sequence, tracked_kind, _previous_state, _previous_detail = entry
        self._query_status_workers[worker] = (sequence, tracked_kind, state, detail)
        terminal = (
            sequence, tracked_kind, state, detail,
            str(getattr(worker, 'nosql_draft_id', '') or ''),
        )
        if self._query_status_last_terminal is None or sequence >= self._query_status_last_terminal[0]:
            self._query_status_last_terminal = terminal
        self._refresh_query_status()

    def _refresh_query_status(self):
        if self._workbench_shutdown_requested:
            return
        draft = self.nosql_draft_tabs.current_draft
        active_draft_id = draft.draft_id if isinstance(draft, NoSqlDraft) else ''
        running = [
            (entry[0], entry[1])
            for worker, entry in self._query_status_workers.items()
            if entry[2] == 'running'
            and (
                entry[1] != 'command'
                or getattr(worker, 'nosql_draft_id', '') == active_draft_id
            )
        ]
        if running:
            _sequence, kind = max(running, key=lambda item: item[0])
            self._set_query_status(kind, 'running')
            return
        terminal = self._query_status_last_terminal
        if terminal is None:
            self.query_status_band.setText('', querying=False)
            return
        _sequence, kind, state, detail, draft_id = terminal
        if kind == 'command' and draft_id != active_draft_id:
            self.query_status_band.setText('', querying=False)
            return
        self._set_query_status(kind, state, detail)

    def _set_query_status(self, kind, state, detail=''):
        zh = self.language == 'zh'
        labels = {
            'test': ('正在测试连接…', 'Testing connection…', '连接测试完成', 'Connection test complete'),
            'bootstrap': ('正在刷新 Key…', 'Refreshing keys…', 'Key 刷新完成', 'Key refresh complete'),
            'scan': ('正在扫描 Key…', 'Scanning keys…', 'Key 扫描完成', 'Key scan complete'),
            'overview': ('正在加载 Redis 信息…', 'Loading Redis overview…', 'Redis 信息加载完成', 'Redis overview loaded'),
            'key_meta': ('正在加载 Key 元信息…', 'Loading key details…', 'Key 元信息已加载', 'Key details loaded'),
            'key_value': ('正在加载 Key 值…', 'Loading key value…', 'Key 值已加载', 'Key value loaded'),
            'command': ('正在执行命令…', 'Running command…', '命令执行完成', 'Command complete'),
            'delete': ('正在删除 Key…', 'Deleting key…', 'Key 删除完成', 'Key deletion complete'),
            'rename': ('正在重命名 Key…', 'Renaming key…', 'Key 重命名完成', 'Key rename complete'),
            'expire': ('正在设置 TTL…', 'Setting TTL…', 'TTL 设置完成', 'TTL update complete'),
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

    def _fill_overview(self, overview: dict | None, info_table: dict | None):
        zh = self.language == 'zh'
        self._overview = overview or {}
        ov = self._overview
        exact = bool(ov.get('total_keys_exact'))
        self.card_keys.value.setText(self._fmt_count(ov.get('total_keys'), exact=exact))
        self.card_memory.value.setText(ov.get('used_memory_human') or format_redis_bytes(ov.get('used_memory')))
        self.card_nodes.value.setText(self._dash(ov.get('cluster_node_count')))
        self.card_version.value.setText(self._dash(ov.get('redis_version')))
        hints = []
        if ov.get('mode') == 'cluster' and not exact:
            hints.append('Keys 为当前可聚合扫描/节点结果，不代表全集' if zh else 'Key count is sampled, not a full inventory')
        unavailable = [n for n in (ov.get('nodes') or []) if n.get('status') != 'online']
        if unavailable and ov.get('mode') == 'cluster':
            hints.append('部分节点不可用' if zh else 'Some nodes unavailable')
        if ov.get('error') == 'all_nodes_failed':
            hints = ['连接失败：所有节点不可用' if zh else 'All cluster nodes unavailable']
        self.ov_hint.setText(' · '.join(hints))
        nodes = ov.get('nodes') or []
        self.nodes_table.setRowCount(len(nodes))
        for row, node in enumerate(nodes):
            label = f"{node.get('host') or ''}:{node.get('port') or ''}".strip(':')
            values = [
                label or '—',
                node.get('role') or '—',
                node.get('status') or 'unavailable',
                '—' if node.get('keys') is None else f"{int(node['keys']):,}",
                '—' if node.get('expires') is None else f"{int(node['expires']):,}",
                '—' if node.get('avg_ttl') is None else str(node.get('avg_ttl')),
                node.get('used_memory_human') or format_redis_bytes(node.get('used_memory')),
            ]
            for col, text in enumerate(values):
                self.nodes_table.setItem(row, col, QTableWidgetItem(str(text)))
        self._info_table_data = info_table or {'priority': [], 'all': []}
        self._render_info_table()

    def _render_info_table(self):
        data = getattr(self, '_info_table_data', {'priority': [], 'all': []})
        rows = data.get('all') if self._info_all else data.get('priority')
        rows = rows or []
        self.info_table.setRowCount(len(rows))
        if not rows:
            self.info_table.setRowCount(1)
            self.info_table.setItem(0, 0, QTableWidgetItem('INFO'))
            self.info_table.setItem(0, 1, QTableWidgetItem('unavailable'))
            return
        for i, item in enumerate(rows):
            self.info_table.setItem(i, 0, QTableWidgetItem(str(item.get('name') or '')))
            self.info_table.setItem(i, 1, QTableWidgetItem(str(item.get('value') or '—')))

    def _toggle_info_all(self):
        self._info_all = not self._info_all
        zh = self.language == 'zh'
        self.info_all_btn.setText(
            ('收起 INFO' if self._info_all else '查看全部 INFO') if zh else
            ('Hide extra INFO' if self._info_all else 'Show all INFO')
        )
        self._render_info_table()

    def _on_worker_done(self, kind: str, payload, worker=None):
        if worker is None:
            worker = self.sender()
        if self._workbench_shutdown_requested:
            return
        if worker is not None and getattr(worker, 'cancelled', False):
            return
        if kind in ('delete', 'rename', 'expire') and worker is not None and worker.kwargs.get('context') is not None:
            draft = self.nosql_draft_tabs.current_draft
            context = worker.kwargs['context']
            if context != self._value_context or draft is None or draft.target != context.target:
                return
        if kind in ('key_meta', 'key_value') and isinstance(payload.get('page'), RedisValuePage):
            draft = self.nosql_draft_tabs.current_draft
            if (worker is None or worker is not self._worker or payload['page'].context != self._value_context
                    or draft is None or draft.target != payload['page'].context.target):
                return
            self._record_query_status_result(worker, kind, 'done')
            self._apply_value_page(worker, payload)
            return
        if kind == 'command':
            draft = self.nosql_draft_tabs.current_draft
            if (
                worker is None
                or not isinstance(draft, NoSqlDraft)
                or draft.draft_id != getattr(worker, 'nosql_draft_id', '')
            ):
                return
        if kind in ('scan', 'bootstrap', 'overview'):
            gen = int((payload or {}).get('generation') or getattr(worker, 'generation', 0) or 0)
            if gen and gen != self._scan.generation:
                return
        if worker is not None:
            self._record_query_status_result(worker, kind, 'done')
        if kind == 'test':
            show_info(self, self._title(), '连接成功' if self.language == 'zh' else 'Connection OK')
        elif kind in ('scan', 'bootstrap'):
            page = (payload or {}).get('scan') or {}
            keys = list(page.get('keys') or [])
            if not payload.get('append'):
                self._key_cache = []
                self._scan.keys = []
                self._key_identities = {}
                self._key_identity_scope = None
            try:
                choice = self._nosql_profile_host.capture_connection(worker.item)
                scope = (choice.connection_id, choice.revision, str(choice.database), choice.mode)
                if self._key_identity_scope not in (None, scope):
                    self._key_identities = {}
                self._key_identity_scope = scope
                for label, raw_values in page.get('raw_keys', {}).items():
                    self._key_identities.setdefault(label, set()).update(raw_values)
            except Exception:
                self._key_identities = {}
                self._key_identity_scope = None
            self._scan.apply(
                self._scan.generation,
                keys,
                page.get('cursor'),
                bool(page.get('finished')),
                partial=bool(page.get('partial')),
                failed_nodes=list(page.get('failed_nodes') or []),
            )
            self._key_cache = list(self._scan.keys)
            if payload.get('overview') is not None:
                self._fill_overview(payload.get('overview'), payload.get('info_table'))
            self._render_prefix_tree(
                self._key_cache,
                incomplete=getattr(self._scan, 'incomplete', not self._scan.finished),
            )
        elif kind == 'overview':
            self._fill_overview((payload or {}).get('overview'), (payload or {}).get('info_table'))
        elif kind == 'key_meta':
            self._selected_type = str(payload.get('type') or '')
            raw_ttl = payload.get('ttl')
            try:
                ttl = int(raw_ttl) if raw_ttl is not None else -2
            except (TypeError, ValueError):
                ttl = -2
            ttl_str = format_key_ttl_badge(ttl, self.language)
            if hasattr(self, 'key_type_badge'):
                self.key_type_badge.setText(f'TYPE: {self._selected_type.upper()}')
                self.key_type_badge.show()
                self.key_ttl_badge.setText(ttl_str)
                self.key_ttl_badge.show()
                self.key_size_badge.hide()
            self.key_meta.setText(
                f'{"类型" if self.language == "zh" else "Type"}: {self._selected_type}'
                f' · TTL: {format_key_ttl_value(ttl, self.language)}'
            )
            if self._worker is None or not self._worker.isRunning():
                self._load_key_value()
            self._update_ai_banner()
        elif kind == 'key_value':
            k_type = str(payload.get('type') or '')
            val = payload.get('value')
            if hasattr(self, 'key_size_badge'):
                if isinstance(val, dict) and 'size' in val:
                    self.key_size_badge.setText(f'SIZE: {val["size"]} B')
                    self.key_size_badge.show()
                elif isinstance(val, dict) and 'raw' in val:
                    sz = len(val.get('raw') or b'')
                    self.key_size_badge.setText(f'SIZE: {sz} B')
                    self.key_size_badge.show()
                elif isinstance(val, (list, tuple, set)):
                    self.key_size_badge.setText(f'LENGTH: {len(val)}')
                    self.key_size_badge.show()
                elif isinstance(val, dict):
                    self.key_size_badge.setText(f'LENGTH: {len(val)}')
                    self.key_size_badge.show()
                elif isinstance(val, (str, bytes)):
                    self.key_size_badge.setText(f'SIZE: {len(val)} B')
                    self.key_size_badge.show()
            self._render_value(k_type, val)
        elif kind == 'delete':
            self._reset_value_selection()
            self._selected_key = ''
            self.key_name.setText('')
            self.key_meta.setText('')
            if hasattr(self, 'key_type_badge'):
                self.key_type_badge.hide()
                self.key_ttl_badge.hide()
                self.key_size_badge.hide()
            self._refresh_keys()
        elif kind == 'rename':
            self._refresh_keys()
        elif kind == 'expire':
            self._load_key_meta()
            self._refresh_keys()
        elif kind == 'command':
            from tools.db_connect import _stringify
            columns = payload.get('columns') or []
            rows = payload.get('rows') or []
            for row in rows:
                if len(columns) == len(row):
                    line = '  '.join(f'{c}={v}' for c, v in zip(columns, row))
                else:
                    line = '  '.join(_stringify(c) for c in row)
                self.cmd_output.appendPlainText(line)

    def _on_worker_failed(self, kind: str, error: str, worker=None):
        if worker is None:
            worker = self.sender()
        if self._workbench_shutdown_requested:
            return
        if worker is not None and getattr(worker, 'cancelled', False):
            return
        if kind in ('key_meta', 'key_value') and worker is not None and worker.kwargs.get('context') is not None:
            if worker is self._worker and worker.kwargs['context'] == self._value_context:
                self._record_query_status_result(worker, kind, 'failed', error)
                self._value_stale = True
                self._value_notice = error if error.startswith('REDIS_VALUE_') else 'READ_FAILED'
                self._update_value_page_label()
            return
        if kind in ('delete', 'rename', 'expire') and worker is not None and worker.kwargs.get('context') is not None:
            draft = self.nosql_draft_tabs.current_draft
            context = worker.kwargs['context']
            if context != self._value_context or draft is None or draft.target != context.target:
                return
        if kind == 'command':
            draft = self.nosql_draft_tabs.current_draft
            if (
                worker is None
                or not isinstance(draft, NoSqlDraft)
                or draft.draft_id != getattr(worker, 'nosql_draft_id', '')
            ):
                return
        if kind in ('scan', 'bootstrap', 'overview'):
            gen = getattr(worker, 'generation', 0)
            if gen and gen != self._scan.generation:
                return
        if worker is not None:
            self._record_query_status_result(worker, kind, 'failed', error)
        if kind == 'export_key':
            return  # The captured modeless export dialog reports its outcome.
        if kind in ('scan', 'bootstrap', 'overview'):
            self.ov_hint.setText(error)
            self.key_stats.setText(error)
            if kind == 'overview':
                show_error(self, self._title(), error)
            return
        if kind == 'command':
            self.cmd_output.appendPlainText(f'错误: {error}')
        else:
            show_error(self, self._title(), error)

    def _on_fmt_changed(self, idx: int):
        raw_b = getattr(self, '_raw_string_bytes', b'')
        inspected = getattr(self, '_string_inspected', {})
        default_text = inspected.get('text') if isinstance(inspected, dict) else getattr(self, '_raw_string_text', '')
        if not raw_b and not default_text:
            return
        if idx == 0:  # 自动安全解码
            self.string_value.setPlainText(default_text or '')
        elif idx == 1:  # UTF-8
            try:
                self.string_value.setPlainText(raw_b.decode('utf-8'))
            except Exception as e:
                self.string_value.setPlainText(f'[UTF-8 解码失败: {e}]\n前缀 Hex:\n{raw_b[:128].hex(" ")}')
        elif idx == 2:  # GB18030
            try:
                self.string_value.setPlainText(raw_b.decode('gb18030'))
            except Exception as e:
                self.string_value.setPlainText(f'[GB18030 解码失败: {e}]\n前缀 Hex:\n{raw_b[:128].hex(" ")}')
        elif idx == 3:  # Hex (十六进制)
            if len(raw_b) > 512 * 1024:
                preview = raw_b[:512 * 1024].hex(' ')
                self.string_value.setPlainText(f'[大文件截断：仅展示前 512KB Hex，总大小 {len(raw_b)} 字节]\n{preview}')
            else:
                self.string_value.setPlainText(raw_b.hex(' '))
        elif idx == 4:  # Base64
            from tools.db_redis_ops import redis_bytes_base64
            self.string_value.setPlainText(redis_bytes_base64(raw_b))
        elif idx == 5:  # JSON (格式化)
            try:
                txt = raw_b.decode('utf-8')
                parsed = json.loads(txt)
                self.string_value.setPlainText(json.dumps(parsed, ensure_ascii=False, indent=2))
            except Exception as e:
                self.string_value.setPlainText(f'[JSON 解析失败: {e}]\n{default_text}')

    def _render_value(self, kind: str, value):
        kind = (kind or '').lower()

        DISPLAY_PREVIEW_CHARS = 300
        TOOLTIP_PREVIEW_CHARS = 1000

        def cell_preview(v):
            if isinstance(v, dict) and 'raw' in v:
                k = v.get('kind', '')
                sz = v.get('size', 0)
                if k in ('utf8', 'gb18030'):
                    t = str(v.get('text') or '')
                    disp = (t[:DISPLAY_PREVIEW_CHARS] + '...') if len(t) > DISPLAY_PREVIEW_CHARS else t
                    tip = (t[:TOOLTIP_PREVIEW_CHARS] + '...') if len(t) > TOOLTIP_PREVIEW_CHARS else t
                    return disp, tip
                elif k == 'java_serialized':
                    t = str(v.get('text') or '')
                    tip = (t[:TOOLTIP_PREVIEW_CHARS] + '...') if len(t) > TOOLTIP_PREVIEW_CHARS else t
                    return f"<Java Serialized {sz} B>", tip
                elif k == 'binary':
                    hex_p = str(v.get('hex_preview') or '')
                    tip = (hex_p[:TOOLTIP_PREVIEW_CHARS] + '...') if len(hex_p) > TOOLTIP_PREVIEW_CHARS else hex_p
                    return f"<Binary {sz} B>", tip
                t = str(v.get('text') or '')
                disp = (t[:DISPLAY_PREVIEW_CHARS] + '...') if len(t) > DISPLAY_PREVIEW_CHARS else t
                tip = (t[:TOOLTIP_PREVIEW_CHARS] + '...') if len(t) > TOOLTIP_PREVIEW_CHARS else t
                return disp, tip
            s = str(v or '')
            disp = (s[:DISPLAY_PREVIEW_CHARS] + '...') if len(s) > DISPLAY_PREVIEW_CHARS else s
            tip = (s[:TOOLTIP_PREVIEW_CHARS] + '...') if len(s) > TOOLTIP_PREVIEW_CHARS else s
            return disp, tip

        if kind == 'hash':
            self.value_tabs.setCurrentIndex(1)
            self.hash_table.setRowCount(0)
            if isinstance(value, list):
                entries = value
            elif isinstance(value, dict):
                entries = [{'field': k, 'value': v} for k, v in value.items()]
            else:
                entries = []
            for entry in entries:
                f_data = entry.get('field') if isinstance(entry, dict) else None
                v_data = entry.get('value') if isinstance(entry, dict) else None
                f_disp, f_tip = cell_preview(f_data)
                v_disp, v_tip = cell_preview(v_data)
                row = self.hash_table.rowCount()
                self.hash_table.insertRow(row)
                f_cell = QTableWidgetItem(f_disp)
                if f_tip:
                    f_cell.setToolTip(f_tip)
                self.hash_table.setItem(row, 0, f_cell)
                v_cell = QTableWidgetItem(v_disp)
                if v_tip:
                    v_cell.setToolTip(v_tip)
                self.hash_table.setItem(row, 1, v_cell)
        elif kind == 'list' or kind == 'set':
            self.value_tabs.setCurrentIndex(2)
            self.list_table.setRowCount(0)
            data = value if isinstance(value, (list, tuple, set)) else []
            for i, v in enumerate(data):
                disp, tip = cell_preview(v)
                row = self.list_table.rowCount()
                self.list_table.insertRow(row)
                self.list_table.setItem(row, 0, QTableWidgetItem(str(i)))
                val_item = QTableWidgetItem(disp)
                if tip:
                    val_item.setToolTip(tip)
                self.list_table.setItem(row, 1, val_item)
        elif kind == 'zset':
            self.value_tabs.setCurrentIndex(3)
            self.zset_table.setRowCount(0)
            data = value if isinstance(value, list) else []
            for entry in data:
                if isinstance(entry, dict):
                    m_data = entry.get('member')
                    disp_m, tip_m = cell_preview(m_data)
                    row = self.zset_table.rowCount()
                    self.zset_table.insertRow(row)
                    m_item = QTableWidgetItem(disp_m)
                    if tip_m:
                        m_item.setToolTip(tip_m)
                    self.zset_table.setItem(row, 0, m_item)
                    score_str = str(entry.get('score', ''))
                    score_item = QTableWidgetItem(score_str)
                    self.zset_table.setItem(row, 1, score_item)
        else:
            self.value_tabs.setCurrentIndex(0)
            if isinstance(value, dict) and 'raw' in value:
                self._raw_string_bytes = value.get('raw') if isinstance(value.get('raw'), bytes) else str(value.get('raw') or '').encode('utf-8')
                self._string_inspected = value
                self._raw_string_text = str(value.get('text') or '')
            elif isinstance(value, bytes):
                from tools.db_redis_ops import inspect_redis_bytes
                self._string_inspected = inspect_redis_bytes(value)
                self._raw_string_bytes = value
                self._raw_string_text = self._string_inspected.get('text', '')
            elif isinstance(value, (dict, list)):
                try:
                    text = json.dumps(value, ensure_ascii=False, indent=2)
                except Exception:
                    text = str(value)
                self._raw_string_text = text
                self._raw_string_bytes = text.encode('utf-8', errors='ignore')
                self._string_inspected = {'raw': self._raw_string_bytes, 'text': text}
            else:
                text = str(value or '')
                self._raw_string_text = text
                self._raw_string_bytes = text.encode('utf-8', errors='ignore')
                self._string_inspected = {'raw': self._raw_string_bytes, 'text': text}

            if hasattr(self, 'fmt_combo'):
                self.fmt_combo.blockSignals(True)
                self.fmt_combo.setCurrentIndex(0)
                self.fmt_combo.blockSignals(False)
            self.string_value.setPlainText(self._raw_string_text)
