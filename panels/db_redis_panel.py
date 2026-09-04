# -*- coding: utf-8 -*-
"""Redis 工作台：Key 树浏览 + Key 详情（类型/TTL/值）+ AI 助手 + 命令行。

参考 Another Redis Desktop Manager。连接由 tools.db_connect.open_connection 建立。
"""

from __future__ import annotations

import json
import time

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QHeaderView,
)

from ui.connection_dialog import ConnectionDialog
from tools.db_connect import (
    DIALECTS, DEFAULT_PORTS, DbError, close_connection, delete_connection,
    load_connections, open_connection, upsert_connection,
)
from tools.db_redis_ops import (
    RedisScanState, build_prefix_index, format_redis_bytes, keys_for_prefix,
    redis_delete_key, redis_expire_key, redis_get_value, redis_info_sections,
    redis_overview, redis_rename_key, redis_scan_page, redis_ttl, redis_type,
)
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_surface, apply_table
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_page_toolbar
from ui.icons import apply_icon, qicon
from ui.splitter_prefs import install_splitter_prefs


class _RedisWorker(QThread):
    completed = pyqtSignal(str, object)   # (kind, payload)
    failed = pyqtSignal(str, str)         # (kind, error)

    def __init__(self, kind: str, item: dict, **kwargs):
        super().__init__()
        self.kind = kind
        self.item = dict(item or {})
        self.kwargs = kwargs
        self.cancelled = False
        self.generation = int(kwargs.get('generation') or 0)

    def run(self):
        conn = None
        try:
            conn = open_connection(self.item)
            if self.kind == 'test':
                self.completed.emit('test', {'ok': True})
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
                self.completed.emit(self.kind, {
                    'overview': overview,
                    'info_table': info_table,
                    'scan': page,
                    'generation': int(self.kwargs.get('generation') or 0),
                    'append': bool(self.kwargs.get('append')),
                })
            elif self.kind == 'key_meta':
                key = self.kwargs.get('key', '')
                kind_name = redis_type(conn, key)
                ttl = redis_ttl(conn, key)
                self.completed.emit('key_meta', {'key': key, 'type': kind_name, 'ttl': ttl})
            elif self.kind == 'key_value':
                key = self.kwargs.get('key', '')
                kind_name = self.kwargs.get('type', '')
                value = redis_get_value(conn, key, kind_name)
                self.completed.emit('key_value', {'key': key, 'type': kind_name, 'value': value})
            elif self.kind == 'delete':
                n = redis_delete_key(conn, self.kwargs.get('key', ''))
                self.completed.emit('delete', {'key': self.kwargs.get('key', ''), 'deleted': n})
            elif self.kind == 'rename':
                ok = redis_rename_key(conn, self.kwargs.get('key', ''),
                                      self.kwargs.get('new_key', ''))
                self.completed.emit('rename', {'ok': ok, 'new_key': self.kwargs.get('new_key', '')})
            elif self.kind == 'expire':
                ok = redis_expire_key(conn, self.kwargs.get('key', ''),
                                      int(self.kwargs.get('seconds', -1)))
                self.completed.emit('expire', {'ok': ok, 'key': self.kwargs.get('key', '')})
            elif self.kind == 'command':
                sql = self.kwargs.get('sql', '')
                from tools.db_connect import _run_redis
                result = _run_redis(conn, sql, 0, 100)
                self.completed.emit('command', result)
            else:
                raise DbError(f'未知任务：{self.kind}')
        except Exception as exc:
            self.failed.emit(self.kind, redact_error(str(exc)))
        finally:
            close_connection(conn)


class RedisWorkbenchPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._conn = None
        self._worker = None
        self._bg_worker = None
        self._selected_key = ''
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

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

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
        self.info_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.info_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ov_l.addWidget(self.info_table, 1)
        self.side_tabs.addTab(overview, 'Overview')

        # Tab 1：Key 详情
        detail = QWidget()
        detail.setObjectName('redis-detail-pane')
        det_l = QVBoxLayout(detail)
        det_l.setContentsMargins(10, 10, 10, 10)
        self.key_name = QLabel()
        self.key_name.setObjectName('section-title')
        self.key_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        det_l.addWidget(self.key_name)
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
        self.hash_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.value_tabs.addTab(self.hash_table, 'Hash')
        self.list_table = QTableWidget()
        self.list_table.setColumnCount(2)
        self.list_table.setHorizontalHeaderLabels(['index', 'value'])
        apply_table(self.list_table, alternating=True)
        self.list_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.value_tabs.addTab(self.list_table, 'List/Set')
        self.zset_table = QTableWidget()
        self.zset_table.setColumnCount(2)
        self.zset_table.setHorizontalHeaderLabels(['member', 'score'])
        apply_table(self.zset_table, alternating=True)
        self.zset_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.value_tabs.addTab(self.zset_table, 'ZSet')
        det_l.addWidget(self.value_tabs, 1)
        actions = QHBoxLayout()
        self.refresh_val_btn = QPushButton()
        apply_button(self.refresh_val_btn, 'secondary', compact=True)
        self.refresh_val_btn.clicked.connect(self._load_key_value)
        self.copy_val_btn = QPushButton()
        apply_button(self.copy_val_btn, 'ghost', compact=True)
        self.copy_val_btn.clicked.connect(self._copy_value)
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
        for w in (self.refresh_val_btn, self.copy_val_btn, self.copy_key_btn,
                  self.del_btn, self.rename_btn, self.expire_btn):
            actions.addWidget(w)
        actions.addStretch(1)
        det_l.addLayout(actions)
        self.side_tabs.addTab(detail, 'Key 详情')

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
        self.side_tabs.addTab(ai_page, 'AI 助手')

        # 底部命令行（Console 只放右侧下方）
        bottom = QFrame()
        bottom.setObjectName('redis-console-pane')
        bottom_l = QVBoxLayout(bottom)
        bottom_l.setContentsMargins(8, 8, 8, 8)
        cmd_bar = QHBoxLayout()
        self.cmd_prompt = QLabel('127.0.0.1:6379>')
        self.cmd_prompt.setObjectName('field-hint')
        self.cmd_input = QLineEdit()
        size_line(self.cmd_input, 'path')
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
        self.cmd_output.setMinimumHeight(120)
        bottom_l.addWidget(self.cmd_output, 1)
        self.bottom_frame = bottom

        # 右侧上下垂直 Splitter：上面是详情页签，下面是控制台
        self._bottom_split = QSplitter(Qt.Orientation.Vertical)
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
        self.main_split.addWidget(left)
        self.main_split.addWidget(self._bottom_split)
        self.main_split.setStretchFactor(0, 2)
        self.main_split.setStretchFactor(1, 5)
        root.addWidget(self.main_split, 1)
        install_splitter_prefs(
            self.main_split, defaults=[440, 960], page_id='redis-workbench', tab_id='main_split',
            min_sizes=[360, 520], accessible_name='Redis 左右主分隔',
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
        self.copy_key_btn.setText('复制 Key 名' if zh else 'Copy key name')
        self.del_btn.setText('删除 Key' if zh else 'Delete key')
        self.rename_btn.setText('重命名' if zh else 'Rename')
        self.expire_btn.setText('设置过期' if zh else 'Set TTL')
        self.side_tabs.setTabText(0, 'Overview')
        self.side_tabs.setTabText(1, 'Key 详情' if zh else 'Key details')
        self.side_tabs.setTabText(2, 'AI 助手' if zh else 'AI assistant')
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

    def apply_layout_mode(self, mode, low_height=False):
        # 视觉 header 已整体移除（模块身份由 Sidebar 标明），无 subtitle 可调；
        # main_window 经 hasattr 鸭子调用，保留方法签名即可。
        return

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
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        rows = [item for item in load_connections() if str(item.get('dialect') or '').lower() == 'redis']
        if not rows:
            self.conn_combo.addItem(
                '无 Redis 连接，点击“新建”创建' if self.language == 'zh' else 'No Redis connection', None
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
            self._refresh_keys()
        else:
            self.db_badge.setText('未连接' if self.language == 'zh' else 'Not connected')
            self.key_stats.setText('未连接 Redis' if self.language == 'zh' else 'Not connected')
            self.ov_hint.setText('未连接' if self.language == 'zh' else 'Not connected')
            for card in (self.card_keys, self.card_memory, self.card_nodes, self.card_version):
                card.value.setText('—')
        self._update_ai_banner()

    def _edit_connection(self, new=False):
        current = None if new else self._current_conn()
        dialog = ConnectionDialog(self.language, current, self, locked_dialect='redis')
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
                              f"删除 Redis 连接「{item.get('name') or ''}」？"
                              if zh else f"Delete Redis connection '{item.get('name') or ''}'?",
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
            f.setFamily('Consolas')
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
        key = (item.text() if item is not None else '') or ''
        if not key:
            return
        self._selected_key = key
        self._selected_type = ''
        self.key_name.setText(self._selected_key)
        self.side_tabs.setCurrentIndex(1)
        self._load_key_meta()

    def _load_key_meta(self):
        if not self._selected_key:
            return
        item = self._current_conn()
        if not item:
            return
        self._run_worker('key_meta', item, key=self._selected_key)

    def _load_key_value(self):
        if not self._selected_key:
            return
        item = self._current_conn()
        if not item:
            return
        self._run_worker('key_value', item, key=self._selected_key, type=self._selected_type)

    def _copy_key_name(self):
        if self._selected_key:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._selected_key)

    def _copy_value(self):
        value = self.string_value.toPlainText()
        if value:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(value)

    def _delete_key(self):
        if not self._selected_key:
            return
        zh = self.language == 'zh'
        if not confirm_action(self, self._title(),
                              f'确定删除 key「{self._selected_key}」？此操作不可撤销'
                              if zh else f'Delete key "{self._selected_key}"? This cannot be undone.',
                              confirm_text='删除' if zh else 'Delete', danger=True):
            return
        item = self._current_conn()
        if item:
            self._run_worker('delete', item, key=self._selected_key)

    def _rename_key(self):
        if not self._selected_key:
            return
        zh = self.language == 'zh'
        new_key, ok = QInputDialog.getText(self, '重命名 Key' if zh else 'Rename key',
                                           '新 Key 名：' if zh else 'New key name:',
                                           text=self._selected_key)
        if not ok or not new_key.strip():
            return
        item = self._current_conn()
        if item:
            self._run_worker('rename', item, key=self._selected_key, new_key=new_key.strip())

    def _expire_key(self):
        if not self._selected_key:
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
        item = self._current_conn()
        if item:
            self._run_worker('expire', item, key=self._selected_key, seconds=sec)

    # ── 命令行 ────────────────────────────────────────────────────────────

    def _run_command(self):
        sql = self.cmd_input.text().strip()
        if not sql:
            return
        from tools.sql_guard import reject_reason
        reason = reject_reason(sql, 'redis')
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
        if self._bg_worker is not None and self._bg_worker.isRunning():
            self._bg_worker.cancelled = True
        worker = _RedisWorker(kind, item, **kwargs)
        self._bg_worker = worker
        worker.completed.connect(lambda k, p, w=worker: self._on_worker_done(k, p, w))
        worker.failed.connect(lambda k, e, w=worker: self._on_worker_failed(k, e, w))
        worker.start()

    def _run_worker(self, kind, item, **kwargs):
        if kind in ('scan', 'bootstrap', 'overview'):
            self._run_bg(kind, item, **kwargs)
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _RedisWorker(kind, item, **kwargs)
        self._worker.completed.connect(lambda k, p, w=self._worker: self._on_worker_done(k, p, w))
        self._worker.failed.connect(lambda k, e, w=self._worker: self._on_worker_failed(k, e, w))
        self._worker.start()

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
        if worker is not None and getattr(worker, 'cancelled', False):
            return
        if kind in ('scan', 'bootstrap', 'overview'):
            gen = int((payload or {}).get('generation') or getattr(worker, 'generation', 0) or 0)
            if gen and gen != self._scan.generation:
                return
        if kind == 'test':
            show_info(self, self._title(), '连接成功' if self.language == 'zh' else 'Connection OK')
        elif kind in ('scan', 'bootstrap'):
            page = (payload or {}).get('scan') or {}
            keys = list(page.get('keys') or [])
            if not payload.get('append'):
                self._key_cache = []
                self._scan.keys = []
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
            ttl = int(payload.get('ttl') or -2)
            ttl_str = f'{ttl}s' if ttl >= 0 else ('永不过期' if ttl == -1 else '已过期')
            if hasattr(self, 'key_type_badge'):
                self.key_type_badge.setText(f'TYPE: {self._selected_type.upper()}')
                self.key_type_badge.show()
                self.key_ttl_badge.setText(f'TTL: {ttl_str}')
                self.key_ttl_badge.show()
                self.key_size_badge.hide()
            self.key_meta.setText(
                f'类型: {self._selected_type} · TTL: {ttl if ttl >= 0 else "永不过期"}'
            )
            self._load_key_value()
            self._update_ai_banner()
        elif kind == 'key_value':
            k_type = str(payload.get('type') or '')
            val = payload.get('value')
            if hasattr(self, 'key_size_badge'):
                if isinstance(val, dict) and 'size' in val:
                    self.key_size_badge.setText(f'SIZE: {val["size"]} B')
                    self.key_size_badge.show()
                elif isinstance(val, (list, tuple, set)):
                    self.key_size_badge.setText(f'LENGTH: {len(val)}')
                    self.key_size_badge.show()
                elif isinstance(val, dict) and 'raw' in val:
                    sz = len(val.get('raw') or b'')
                    self.key_size_badge.setText(f'SIZE: {sz} B')
                    self.key_size_badge.show()
                elif isinstance(val, (str, bytes)):
                    self.key_size_badge.setText(f'SIZE: {len(val)} B')
                    self.key_size_badge.show()
            self._render_value(k_type, val)
        elif kind == 'delete':
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
        if worker is not None and getattr(worker, 'cancelled', False):
            return
        if kind in ('scan', 'bootstrap', 'overview'):
            gen = getattr(worker, 'generation', 0)
            if gen and gen != self._scan.generation:
                return
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
