# -*- coding: utf-8 -*-
"""Redis 工作台：Key 树浏览 + Key 详情（类型/TTL/值）+ AI 助手 + 命令行。

参考 Another Redis Desktop Manager。连接由 tools.db_connect.open_connection 建立。
"""

from __future__ import annotations

import json
import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from ui.connection_dialog import ConnectionDialog
from tools.db_connect import (
    DIALECTS, DEFAULT_PORTS, DbError, close_connection, delete_connection,
    load_connections, open_connection, upsert_connection,
)
from tools.db_redis_ops import (
    build_key_tree, filter_keys_by_pattern, redis_db_count, redis_delete_key,
    redis_expire_key, redis_get_value, redis_rename_key, redis_scan_keys,
    redis_server_info, redis_ttl, redis_type,
)
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_table
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_page_toolbar
from ui.icons import apply_icon
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

    def run(self):
        conn = None
        try:
            conn = open_connection(self.item)
            if self.kind == 'test':
                self.completed.emit('test', {'ok': True})
            elif self.kind == 'scan':
                keys = redis_scan_keys(conn, self.kwargs.get('pattern', '*'),
                                       int(self.kwargs.get('limit', 500)))
                info = redis_server_info(conn)
                self.completed.emit('scan', {'keys': keys, 'count': len(keys),
                                             'db_count': redis_db_count(conn), 'info': info})
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
        self._selected_key = ''
        self._selected_type = ''
        self._key_cache = []
        self._tree_filter = ''
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

        # 主区：左 Key 树 | 右（详情/AI 助手）
        body = QSplitter(Qt.Orientation.Horizontal)

        left = QFrame()
        left.setObjectName('dashboard-task-card')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(8, 8, 8, 8)
        self.db_badge = QLabel()
        self.db_badge.setObjectName('status-pill')
        left_l.addWidget(self.db_badge)
        self.key_filter = QLineEdit()
        size_line(self.key_filter, 'std')
        self.key_filter.setPlaceholderText('搜索 Key（* 通配符）')
        self.key_filter.textChanged.connect(self._on_filter_changed)
        left_l.addWidget(self.key_filter)
        self.key_tree = QTreeWidget()
        self.key_tree.setHeaderHidden(True)
        self.key_tree.setIndentation(14)
        self.key_tree.itemClicked.connect(self._on_key_clicked)
        left_l.addWidget(self.key_tree, 1)
        self.key_stats = QLabel()
        self.key_stats.setObjectName('field-hint')
        left_l.addWidget(self.key_stats)
        body.addWidget(left)

        right = QFrame()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        self.side_tabs = QTabWidget()

        # Tab 1：Key 详情
        detail = QWidget()
        det_l = QVBoxLayout(detail)
        det_l.setContentsMargins(10, 10, 10, 10)
        self.key_name = QLabel()
        self.key_name.setObjectName('section-title')
        self.key_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        det_l.addWidget(self.key_name)
        self.key_meta = QLabel()
        self.key_meta.setObjectName('field-hint')
        self.key_meta.setWordWrap(True)
        det_l.addWidget(self.key_meta)
        self.value_tabs = QTabWidget()
        self.string_value = QPlainTextEdit()
        self.string_value.setReadOnly(True)
        self.value_tabs.addTab(self.string_value, 'String')
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

        right_l.addWidget(self.side_tabs)
        body.addWidget(right)
        body.setStretchFactor(0, 2)
        body.setStretchFactor(1, 3)
        install_splitter_prefs(
            body, defaults=[340, 620], page_id='redis-workbench', tab_id='main',
            min_sizes=[240, 400], accessible_name='Redis 左右分隔',
        )

        # 底部命令行
        bottom = QFrame()
        bottom.setObjectName('dashboard-task-card')
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
        self.cmd_output.setMinimumHeight(160)
        bottom_l.addWidget(self.cmd_output, 1)
        self.bottom_frame = bottom
        # 一次性构造成最终树：_bottom_split 直接成为 root 的主 stretch 内容。
        # 禁止先 root.addWidget 再 reparent 进 splitter（replaceWidget 对已
        # reparent 的子项不可靠，曾导致主业务区整体塌陷成空白）。
        self._bottom_split = QSplitter(Qt.Orientation.Vertical)
        self._bottom_split.addWidget(body)
        self._bottom_split.addWidget(bottom)
        self._bottom_split.setStretchFactor(0, 3)
        self._bottom_split.setStretchFactor(1, 1)
        root.addWidget(self._bottom_split, 1)
        install_splitter_prefs(
            self._bottom_split, defaults=[560, 220], page_id='redis-workbench', tab_id='body',
            min_sizes=[240, 140], accessible_name='Redis 上下分隔',
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
        self.key_filter.setPlaceholderText('搜索 Key（* 通配符）' if zh else 'Search keys (* wildcard)')
        self.refresh_val_btn.setText('刷新值' if zh else 'Refresh value')
        self.copy_val_btn.setText('复制值' if zh else 'Copy value')
        self.copy_key_btn.setText('复制 Key 名' if zh else 'Copy key name')
        self.del_btn.setText('删除 Key' if zh else 'Delete key')
        self.rename_btn.setText('重命名' if zh else 'Rename')
        self.expire_btn.setText('设置过期' if zh else 'Set TTL')
        self.side_tabs.setTabText(0, 'Key 详情' if zh else 'Key details')
        self.side_tabs.setTabText(1, 'AI 助手' if zh else 'AI assistant')
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
        item = self._current_conn()
        if item:
            self.db_badge.setText(f"🟢 {item.get('host') or ''}:{item.get('port') or 6379}")
            self.cmd_prompt.setText(f"{item.get('host') or '127.0.0.1'}:{item.get('port') or 6379}>")
            self._refresh_keys()
        else:
            self.db_badge.setText('未连接' if self.language == 'zh' else 'Not connected')
            self.key_tree.clear()
            self.key_stats.setText('未连接 Redis' if self.language == 'zh' else 'Not connected')
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
        self._run_worker('scan', item, pattern=self._tree_filter or '*', limit=500)

    def _on_filter_changed(self, text):
        self._tree_filter = text.strip()
        if self._key_cache:
            self._render_key_tree(self._key_cache)
        else:
            self._refresh_keys()

    def _render_key_tree(self, keys: list[str]):
        filtered = filter_keys_by_pattern(keys, self._tree_filter)
        tree = build_key_tree(filtered)
        self.key_tree.blockSignals(True)
        self.key_tree.clear()

        def add_nodes(parent, nodes):
            for node in nodes:
                item = QTreeWidgetItem(parent)
                if node.get('is_folder'):
                    item.setText(0, node['name'] + ':')
                    item.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'folder', 'name': node['name']})
                    item.setExpanded(True)
                    add_nodes(item, node.get('children', []))
                else:
                    item.setText(0, node['name'])
                    item.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'key', 'key': node['full']})
                    add_nodes(item, node.get('children', []))

        add_nodes(self.key_tree.invisibleRootItem(), tree)
        self.key_tree.blockSignals(False)
        self.key_stats.setText(
            f'共 {len(keys)} 个 Key · 显示 {len(filtered)} 个' if self.language == 'zh'
            else f'{len(keys)} keys · {len(filtered)} shown'
        )

    def _on_key_clicked(self, item: QTreeWidgetItem, _column: int):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get('kind') != 'key':
            return
        self._selected_key = str(meta.get('key') or '')
        self._selected_type = ''
        self.key_name.setText(self._selected_key)
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

    def _run_worker(self, kind, item, **kwargs):
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _RedisWorker(kind, item, **kwargs)
        self._worker.completed.connect(self._on_worker_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _on_worker_done(self, kind: str, payload):
        if kind == 'test':
            show_info(self, self._title(), '连接成功' if self.language == 'zh' else 'Connection OK')
        elif kind == 'scan':
            self._key_cache = list(payload.get('keys') or [])
            self._render_key_tree(self._key_cache)
        elif kind == 'key_meta':
            self._selected_type = str(payload.get('type') or '')
            ttl = int(payload.get('ttl') or -2)
            self.key_meta.setText(
                f'类型: {self._selected_type} · TTL: {ttl if ttl >= 0 else "永不过期"}'
            )
            self._load_key_value()
            self._update_ai_banner()
        elif kind == 'key_value':
            self._render_value(str(payload.get('type') or ''), payload.get('value'))
        elif kind == 'delete':
            self._selected_key = ''
            self.key_name.setText('')
            self.key_meta.setText('')
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

    def _on_worker_failed(self, kind: str, error: str):
        if kind == 'command':
            self.cmd_output.appendPlainText(f'错误: {error}')
        else:
            show_error(self, self._title(), error)

    def _render_value(self, kind: str, value):
        kind = (kind or '').lower()
        if kind == 'hash':
            self.value_tabs.setCurrentIndex(1)
            self.hash_table.setRowCount(0)
            data = value if isinstance(value, dict) else {}
            for k, v in data.items():
                row = self.hash_table.rowCount()
                self.hash_table.insertRow(row)
                self.hash_table.setItem(row, 0, QTableWidgetItem(str(k)))
                self.hash_table.setItem(row, 1, QTableWidgetItem(str(v)))
        elif kind == 'list' or kind == 'set':
            self.value_tabs.setCurrentIndex(2)
            self.list_table.setRowCount(0)
            data = value if isinstance(value, (list, tuple, set)) else []
            for i, v in enumerate(data):
                row = self.list_table.rowCount()
                self.list_table.insertRow(row)
                self.list_table.setItem(row, 0, QTableWidgetItem(str(i)))
                self.list_table.setItem(row, 1, QTableWidgetItem(str(v)))
        elif kind == 'zset':
            self.value_tabs.setCurrentIndex(3)
            self.zset_table.setRowCount(0)
            data = value if isinstance(value, list) else []
            for entry in data:
                if isinstance(entry, dict):
                    row = self.zset_table.rowCount()
                    self.zset_table.insertRow(row)
                    self.zset_table.setItem(row, 0, QTableWidgetItem(str(entry.get('member', ''))))
                    self.zset_table.setItem(row, 1, QTableWidgetItem(str(entry.get('score', ''))))
        else:
            self.value_tabs.setCurrentIndex(0)
            if isinstance(value, (dict, list)):
                try:
                    text = json.dumps(value, ensure_ascii=False, indent=2)
                except Exception:
                    text = str(value)
            else:
                text = str(value)
            self.string_value.setPlainText(text)
