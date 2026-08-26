# -*- coding: utf-8 -*-
"""SQL 控制台：Navicat 风格多标签编辑 + 结构快照 + AI 草案（绝不自动执行）。"""

from __future__ import annotations

from datetime import datetime
import time

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from config import SQL_DRAFTS_DIR
from panels.ai_token_edit import AiPromptEdit, ObjectPickDialog
from tools.ai_object_context import (
    add_field, add_object, context_matches_snapshot, field_qualified,
    selected_field_names, selected_table_names,
)
from tools.db_connect import (
    DIALECTS, DEFAULT_PORTS, PAGE_SIZE, MAX_ROWS, DbError, delete_connection,
    load_connections, open_connection, close_connection, run_console_statement,
    upsert_connection,
)
from tools.intranet_llm import is_enabled, load_ai_local
from tools.tameng_agent import format_evidence_bar, prepare_request, validate_generated_sql
from tools.schema_snapshot import (
    clip_snapshot_for_prompt, connection_fingerprint, delete_snapshot, format_object_label,
    load_snapshot, save_snapshot, scan_schema, search_fields, snapshot_status,
)
from ui.aurora_progress import AuroraProgress
from tools.sql_guard import ai_draft_safety, classify_statement, redact_error, statement_at_cursor
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_table
from ui.field_metrics import size_enum_combo, size_line, size_pick_combo, wrap_secret_field
from ui.page_chrome import make_empty_state, make_page_header, make_page_toolbar
from ui.sql_editor import SqlEditor


def compose_nl_query(table: str, columns=None, language='zh') -> str:
    """把所选表/字段编成自然语言问句，便于插入输入框。"""
    name = str(table or '').strip()
    cols = [str(item).strip() for item in (columns or []) if str(item).strip()]
    zh = language == 'zh'
    if name and cols:
        joined = '、'.join(cols) if zh else ', '.join(cols)
        return f'帮我查询表 {name} 的字段 {joined}' if zh else f'query table {name} columns {joined}'
    if name:
        return f'帮我查询表 {name} 的数据' if zh else f'query table {name}'
    if cols:
        joined = '、'.join(cols) if zh else ', '.join(cols)
        return f'帮我查询字段 {joined}' if zh else f'query columns {joined}'
    return ''


class _DbWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, kind: str, item: dict, **kwargs):
        super().__init__()
        self.kind = kind
        self.item = item
        self.kwargs = kwargs
        self.cancelled = False

    def run(self):
        conn = None
        try:
            conn = open_connection(self.item)
            dialect = str(self.item.get('dialect') or 'oracle')
            if self.kind == 'test':
                self.completed.emit({'ok': True})
            elif self.kind == 'scan':
                payload = scan_schema(conn, self.item, cancel=lambda: self.cancelled)
                if self.cancelled:
                    payload['status'] = 'failed'
                    payload['warning'] = '扫描已取消'
                    self.completed.emit(payload)
                    return
                save_snapshot(payload)
                self.completed.emit(payload)
            elif self.kind == 'query':
                result = run_console_statement(
                    conn, dialect, self.kwargs.get('sql') or '',
                    offset=int(self.kwargs.get('offset') or 0),
                    limit=int(self.kwargs.get('limit') or PAGE_SIZE),
                )
                self.completed.emit(result)
            else:
                raise DbError(f'未知任务：{self.kind}')
        except Exception as exc:
            self.failed.emit(redact_error(str(exc)))
        finally:
            close_connection(conn)


class _AiWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, kwargs: dict):
        super().__init__()
        self.kwargs = kwargs
        self.cancelled = False

    def run(self):
        try:
            from tools.ai_sql_draft import generate_sql_draft
            draft = generate_sql_draft(**self.kwargs)
            if self.cancelled:
                return
            self.completed.emit(draft)
        except Exception as exc:
            if self.cancelled:
                return
            self.failed.emit(redact_error(str(exc)))


class _ConnectionDialog(QDialog):
    def __init__(self, language='zh', item=None, parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setWindowTitle('编辑连接' if zh else 'Edit connection')
        self.setMinimumWidth(480)
        self._item = dict(item or {})
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(str(self._item.get('name') or ''))
        size_line(self.name, 'path')
        self.dialect = QComboBox()
        for key, label in DIALECTS:
            self.dialect.addItem(label, key)
        index = self.dialect.findData(str(self._item.get('dialect') or 'oracle'))
        self.dialect.setCurrentIndex(index if index >= 0 else 0)
        size_enum_combo(self.dialect)
        self.dialect.currentIndexChanged.connect(self._on_dialect_changed)
        self.host = QLineEdit(str(self._item.get('host') or ''))
        size_line(self.host, 'path')
        self.port = QLineEdit(str(self._item.get('port') or DEFAULT_PORTS['oracle']))
        size_line(self.port, 'std')
        self.database = QLineEdit(str(self._item.get('database') or ''))
        self.database.setPlaceholderText('SID / Service / 库名')
        size_line(self.database, 'path')
        self.username = QLineEdit(str(self._item.get('username') or ''))
        size_line(self.username, 'path')
        self.password = QLineEdit()
        self.password_row, self.password_reveal = wrap_secret_field(
            self.password, reveal_text='查看' if zh else 'Show', hide_text='隐藏' if zh else 'Hide'
        )
        self.oracle_hint = QLabel(
            'Oracle 客户端在「设置 → Oracle 兼容」中统一配置主目录和 oci.dll，所有 Oracle 连接共用。'
            if zh else
            'Oracle home and oci.dll are configured once in Settings → Oracle.'
        )
        self.oracle_hint.setObjectName('field-hint')
        self.oracle_hint.setWordWrap(True)
        form.addRow('名称' if zh else 'Name', self.name)
        form.addRow('类型' if zh else 'Type', self.dialect)
        form.addRow('主机' if zh else 'Host', self.host)
        form.addRow('端口' if zh else 'Port', self.port)
        self.database_label = QLabel('库名' if zh else 'Database')
        form.addRow(self.database_label, self.database)
        form.addRow('用户' if zh else 'User', self.username)
        form.addRow('密码' if zh else 'Password', self.password_row)
        form.addRow(self.oracle_hint)
        root.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('取消' if zh else 'Cancel')
        apply_button(cancel, 'secondary', compact=True)
        cancel.clicked.connect(self.reject)
        ok = QPushButton('保存' if zh else 'Save')
        apply_button(ok, 'primary', compact=True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)
        self._on_dialect_changed()

    def _on_dialect_changed(self):
        dialect = self.dialect.currentData() or 'oracle'
        zh = self.language == 'zh'
        self.oracle_hint.setVisible(dialect == 'oracle')
        if dialect == 'oracle':
            self.database_label.setText('SID/服务名' if zh else 'SID')
            self.database.setPlaceholderText('ORCL / 服务名')
        elif dialect == 'dameng':
            self.database_label.setText('模式/库名' if zh else 'Schema')
            self.database.setPlaceholderText('')
        elif dialect == 'redis':
            self.database_label.setText('DB 序号' if zh else 'DB index')
            self.database.setPlaceholderText('0')
        elif dialect == 'mongodb':
            self.database_label.setText('库名' if zh else 'Database')
            self.database.setPlaceholderText('例如 admin / prpcar')
        else:
            self.database_label.setText('库名' if zh else 'Database')
            self.database.setPlaceholderText('mysql 库名，例如 test')
        defaults = {'1521', '2881', '3306', '5236', '6379', '27017'}
        if not self.port.text().strip() or self.port.text().strip() in defaults:
            self.port.setText(str(DEFAULT_PORTS.get(dialect, 3306)))

    def payload(self) -> tuple[dict, str]:
        item = dict(self._item)
        item['name'] = self.name.text().strip()
        item['dialect'] = self.dialect.currentData() or 'oracle'
        item['host'] = self.host.text().strip()
        try:
            item['port'] = int(self.port.text().strip() or DEFAULT_PORTS.get(item['dialect'], 1521))
        except ValueError:
            item['port'] = DEFAULT_PORTS.get(item['dialect'], 1521)
        item['database'] = self.database.text().strip()
        item['username'] = self.username.text().strip()
        return item, self.password.text()


class _SqlTab(QWidget):
    def __init__(self, title: str, conn_item=None, parent=None):
        super().__init__(parent)
        self.base_title = title
        self.dirty = False
        self.conn_item = dict(conn_item) if isinstance(conn_item, dict) else None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.editor = SqlEditor()
        self.editor.textChanged.connect(self._mark_dirty)
        layout.addWidget(self.editor)

    def _mark_dirty(self):
        self.dirty = True


class AiWorkbenchPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._worker = None
        self._ai_worker = None
        self._snapshot = None
        self._offset = 0
        self._has_more = False
        self._last_sql = ''
        self._history = []
        self._tab_seq = 1
        self._agent_busy = False
        self._agent_started = 0.0
        self._pending_evidence = None
        self._last_block = ''
        self._agent_timer = QTimer(self)
        self._agent_timer.setInterval(200)
        self._agent_timer.timeout.connect(self._tick_agent_stage)
        self._setup_ui()
        self.set_language(language)
        self._reload_connections()
        self._new_sql_tab()
        self._refresh_header()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.new_tab_btn = QPushButton()
        apply_button(self.new_tab_btn, 'primary', compact=True)
        self.new_tab_btn.clicked.connect(self._new_sql_tab)
        self.conn_meta = QLabel()
        self.conn_meta.setObjectName('page-context')
        header, self.page_title, self.page_subtitle = make_page_header(
            'SQL 控制台',
            '多标签 SQL 编辑与内网模型草案',
            'database',
            primary_button=self.new_tab_btn,
            trailing=self.conn_meta,
        )
        root.addWidget(header)

        toolbar, tool_l = make_page_toolbar(divided=True)
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
        self.test_btn.clicked.connect(lambda: self._start_db('test'))
        self.scan_btn = QPushButton()
        apply_button(self.scan_btn, 'secondary', compact=True)
        self.scan_btn.clicked.connect(lambda: self._start_db('scan'))
        self.scan_cancel_btn = QPushButton()
        apply_button(self.scan_cancel_btn, 'ghost', compact=True)
        self.scan_cancel_btn.clicked.connect(self._cancel_scan)
        self.scan_cancel_btn.setEnabled(False)
        self.view_snap_btn = QPushButton()
        apply_button(self.view_snap_btn, 'ghost', compact=True)
        self.view_snap_btn.clicked.connect(self._view_snapshot)
        self.del_snap_btn = QPushButton()
        apply_button(self.del_snap_btn, 'ghost', compact=True)
        self.del_snap_btn.clicked.connect(self._delete_snapshot)
        self.model_btn = QPushButton()
        apply_button(self.model_btn, 'ghost', compact=True)
        self.model_btn.clicked.connect(self._open_settings)
        for widget in (
            self.conn_combo, self.conn_new_btn, self.conn_edit_btn, self.conn_del_btn,
            self.test_btn, self.scan_btn, self.scan_cancel_btn, self.view_snap_btn, self.del_snap_btn, self.model_btn,
        ):
            tool_l.addWidget(widget)
        tool_l.addStretch(1)
        root.addWidget(toolbar)

        body = QSplitter(Qt.Orientation.Vertical)
        columns = QSplitter(Qt.Orientation.Horizontal)

        left = QFrame()
        left.setObjectName('dashboard-task-card')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        self.tree_title = QLabel()
        self.tree_title.setObjectName('section-title')
        left_l.addWidget(self.tree_title)
        self.object_filter = QLineEdit()
        size_line(self.object_filter, 'std')
        self.object_filter.textChanged.connect(self._filter_tree)
        left_l.addWidget(self.object_filter)
        self.object_tree = QTreeWidget()
        self.object_tree.setHeaderHidden(True)
        self.object_tree.itemSelectionChanged.connect(self._on_tree_selected)
        self.object_tree.itemDoubleClicked.connect(self._on_tree_double)
        self.object_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.object_tree.customContextMenuRequested.connect(self._tree_menu)
        left_l.addWidget(self.object_tree, 1)
        self.tree_empty = make_empty_state('尚未扫描结构', '点击工具栏「扫描结构」加载当前账号可见对象')
        self.tree_empty.hide()
        left_l.addWidget(self.tree_empty)
        columns.addWidget(left)

        middle = QWidget()
        mid_l = QVBoxLayout(middle)
        mid_l.setContentsMargins(0, 0, 0, 0)
        mid_l.setSpacing(8)
        editor_row = QHBoxLayout()
        self.run_btn = QPushButton()
        apply_button(self.run_btn, 'primary', compact=True)
        self.run_btn.clicked.connect(lambda: self._run_sql(reset=True))
        self.format_btn = QPushButton()
        apply_button(self.format_btn, 'ghost', compact=True)
        self.format_btn.clicked.connect(self._format_sql)
        self.clear_btn = QPushButton()
        apply_button(self.clear_btn, 'ghost', compact=True)
        self.clear_btn.clicked.connect(self._clear_editor)
        self.save_draft_btn = QPushButton()
        apply_button(self.save_draft_btn, 'ghost', compact=True)
        self.save_draft_btn.clicked.connect(self._save_draft)
        editor_row.addWidget(self.run_btn)
        editor_row.addWidget(self.format_btn)
        editor_row.addWidget(self.clear_btn)
        editor_row.addWidget(self.save_draft_btn)
        editor_row.addStretch(1)
        mid_l.addLayout(editor_row)
        self.sql_tabs = QTabWidget()
        self.sql_tabs.setTabsClosable(True)
        self.sql_tabs.tabCloseRequested.connect(self._close_sql_tab)
        self.sql_tabs.currentChanged.connect(self._refresh_tab_titles)
        mid_l.addWidget(self.sql_tabs, 1)
        columns.addWidget(middle)

        self.side_tabs = QTabWidget()
        ai_page = QWidget()
        right_l = QVBoxLayout(ai_page)
        right_l.setContentsMargins(10, 10, 10, 10)
        self.ai_title = QLabel()
        self.ai_title.setObjectName('section-title')
        right_l.addWidget(self.ai_title)
        self.agent_status = QLabel()
        self.agent_status.setObjectName('page-context')
        self.agent_status.setWordWrap(True)
        right_l.addWidget(self.agent_status)
        self.model_status = QLabel()
        self.model_status.setObjectName('field-hint')
        self.model_status.setWordWrap(True)
        right_l.addWidget(self.model_status)
        self.ai_hint = QLabel()
        self.ai_hint.setObjectName('field-hint')
        self.ai_hint.setWordWrap(True)
        right_l.addWidget(self.ai_hint)
        self.nl_input = AiPromptEdit()
        self.nl_input.setMinimumHeight(88)
        self.nl_input.add_table_requested.connect(lambda pos: self._pick_ai_object('table', pos))
        self.nl_input.add_field_requested.connect(lambda pos: self._pick_ai_object('field', pos))
        self.nl_input.tokens_changed.connect(self._refresh_ai_chips)
        right_l.addWidget(self.nl_input, 1)
        self.ai_chips = QLabel()
        self.ai_chips.setObjectName('field-hint')
        self.ai_chips.setWordWrap(True)
        right_l.addWidget(self.ai_chips)
        self.agent_evidence = QLabel()
        self.agent_evidence.setObjectName('field-hint')
        self.agent_evidence.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.agent_evidence.setWordWrap(True)
        right_l.addWidget(self.agent_evidence)
        self.agent_candidates = QListWidget()
        self.agent_candidates.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.agent_candidates.hide()
        right_l.addWidget(self.agent_candidates)
        self.agent_confirm_btn = QPushButton()
        apply_button(self.agent_confirm_btn, 'secondary', compact=True)
        self.agent_confirm_btn.clicked.connect(self._confirm_agent_fields)
        self.agent_confirm_btn.hide()
        right_l.addWidget(self.agent_confirm_btn)
        self.agent_stage = QLabel()
        self.agent_stage.setObjectName('field-hint')
        self.agent_stage.setWordWrap(True)
        self.agent_stage.hide()
        right_l.addWidget(self.agent_stage)
        ai_btns = QHBoxLayout()
        self.ai_gen_btn = QPushButton()
        apply_button(self.ai_gen_btn, 'primary', compact=True)
        self.ai_gen_btn.clicked.connect(lambda: self._run_ai('generate'))
        self.ai_pick_btn = QPushButton()
        apply_button(self.ai_pick_btn, 'secondary', compact=True)
        self.ai_pick_btn.clicked.connect(lambda: self._pick_ai_object('field', self.nl_input.textCursor().position()))
        self.ai_snap_btn = QPushButton()
        apply_button(self.ai_snap_btn, 'ghost', compact=True)
        self.ai_snap_btn.clicked.connect(self._view_snapshot)
        self.agent_cancel_btn = QPushButton()
        apply_button(self.agent_cancel_btn, 'ghost', compact=True)
        self.agent_cancel_btn.clicked.connect(self._cancel_agent)
        self.agent_cancel_btn.hide()
        self.ai_explain_btn = QPushButton()
        apply_button(self.ai_explain_btn, 'ghost', compact=True)
        self.ai_explain_btn.clicked.connect(lambda: self._run_ai('explain'))
        self.ai_opt_btn = QPushButton()
        apply_button(self.ai_opt_btn, 'ghost', compact=True)
        self.ai_opt_btn.clicked.connect(lambda: self._run_ai('optimize'))
        self.ai_fix_btn = QPushButton()
        apply_button(self.ai_fix_btn, 'ghost', compact=True)
        self.ai_fix_btn.clicked.connect(lambda: self._run_ai('fix'))
        self.agent_more = QToolButton()
        self.agent_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        apply_button(self.agent_more, 'ghost', compact=True)
        more_menu = QMenu(self.agent_more)
        more_menu.addAction(self.ai_explain_btn.text() or '解释当前 SQL', lambda: self._run_ai('explain'))
        more_menu.addAction('优化当前 SQL', lambda: self._run_ai('optimize'))
        more_menu.addAction('修复报错', lambda: self._run_ai('fix'))
        more_menu.addAction('复制拦截详情', self._copy_block_detail)
        self.agent_more.setMenu(more_menu)
        self._agent_more_menu = more_menu
        for btn in (self.ai_gen_btn, self.ai_pick_btn, self.ai_snap_btn, self.agent_more, self.agent_cancel_btn):
            ai_btns.addWidget(btn)
        ai_btns.addStretch(1)
        right_l.addLayout(ai_btns)
        self.ai_explain_btn.hide()
        self.ai_opt_btn.hide()
        self.ai_fix_btn.hide()
        self.ai_explain = QTextEdit()
        self.ai_explain.setReadOnly(True)
        self.ai_explain.setObjectName('ai-explain')
        right_l.addWidget(self.ai_explain, 1)
        self.side_tabs.addTab(ai_page, 'TamengAgent')

        detail = QWidget()
        det_l = QVBoxLayout(detail)
        det_l.setContentsMargins(10, 10, 10, 10)
        self.detail_title = QLabel()
        self.detail_title.setObjectName('section-title')
        det_l.addWidget(self.detail_title)
        self.detail_meta = QLabel()
        self.detail_meta.setObjectName('field-hint')
        self.detail_meta.setWordWrap(True)
        det_l.addWidget(self.detail_meta)
        self.field_filter = QLineEdit()
        self.field_filter.textChanged.connect(self._fill_detail_fields)
        det_l.addWidget(self.field_filter)
        self.field_table = QTableWidget()
        apply_table(self.field_table, alternating=True)
        det_l.addWidget(self.field_table, 1)
        field_btns = QHBoxLayout()
        self.insert_fields_btn = QPushButton()
        apply_button(self.insert_fields_btn, 'secondary', compact=True)
        self.insert_fields_btn.clicked.connect(self._insert_detail_fields)
        self.send_ai_btn = QPushButton()
        apply_button(self.send_ai_btn, 'ghost', compact=True)
        self.send_ai_btn.clicked.connect(self._send_detail_to_ai)
        self.field_prev_btn = QPushButton()
        apply_button(self.field_prev_btn, 'ghost', compact=True)
        self.field_prev_btn.clicked.connect(lambda: self._shift_field_page(-1))
        self.field_next_btn = QPushButton()
        apply_button(self.field_next_btn, 'ghost', compact=True)
        self.field_next_btn.clicked.connect(lambda: self._shift_field_page(1))
        field_btns.addWidget(self.insert_fields_btn)
        field_btns.addWidget(self.send_ai_btn)
        field_btns.addStretch(1)
        field_btns.addWidget(self.field_prev_btn)
        field_btns.addWidget(self.field_next_btn)
        det_l.addLayout(field_btns)
        self._detail_object = None
        self._field_page = 0
        self.side_tabs.addTab(detail, '对象详情')
        columns.addWidget(self.side_tabs)
        columns.setStretchFactor(0, 2)
        columns.setStretchFactor(1, 5)
        columns.setStretchFactor(2, 3)
        body.addWidget(columns)

        bottom = QTabWidget()
        self.result = QTableWidget()
        apply_table(self.result, alternating=True)
        result_page = QWidget()
        result_l = QVBoxLayout(result_page)
        result_l.setContentsMargins(0, 8, 0, 0)
        page_row = QHBoxLayout()
        self.next_btn = QPushButton()
        apply_button(self.next_btn, 'secondary', compact=True)
        self.next_btn.clicked.connect(self._fetch_next)
        self.next_btn.setEnabled(False)
        self.all_btn = QPushButton()
        apply_button(self.all_btn, 'ghost', compact=True)
        self.all_btn.clicked.connect(self._fetch_all)
        self.all_btn.setEnabled(False)
        self.result_status = QLabel()
        self.result_status.setObjectName('field-hint')
        page_row.addWidget(self.next_btn)
        page_row.addWidget(self.all_btn)
        page_row.addWidget(self.result_status, 1)
        result_l.addLayout(page_row)
        result_l.addWidget(self.result, 1)
        self.msg_view = QPlainTextEdit()
        self.msg_view.setReadOnly(True)
        self.hist_view = QPlainTextEdit()
        self.hist_view.setReadOnly(True)
        bottom.addTab(result_page, '结果')
        bottom.addTab(self.msg_view, '消息')
        bottom.addTab(self.hist_view, '历史')
        self.result_tabs = bottom
        body.addWidget(bottom)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        root.addWidget(body, 1)

        QShortcut(QKeySequence('Ctrl+N'), self, activated=self._new_sql_tab)
        QShortcut(QKeySequence('Ctrl+W'), self, activated=self._close_current_tab)
        QShortcut(QKeySequence('Ctrl+Return'), self, activated=lambda: self._run_sql(reset=True))
        QShortcut(QKeySequence('F5'), self, activated=lambda: self._run_sql(reset=True))
        self.loading = AuroraProgress(self)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('SQL 控制台' if zh else 'SQL Console')
        self.page_subtitle.setText(
            '多标签编辑 · 结构快照 · TamengAgent 只生成不执行' if zh else
            'Multi-tab SQL · schema snapshot · TamengAgent drafts never auto-run'
        )
        self.new_tab_btn.setText('新建 SQL 标签页' if zh else 'New SQL tab')
        self.conn_new_btn.setText('新建数据库连接' if zh else 'New connection')
        self.conn_edit_btn.setText('编辑' if zh else 'Edit')
        self.conn_del_btn.setText('删除' if zh else 'Delete')
        self.test_btn.setText('测试连接' if zh else 'Test')
        self.scan_btn.setText('扫描结构' if zh else 'Scan schema')
        self.scan_cancel_btn.setText('取消扫描' if zh else 'Cancel scan')
        self.view_snap_btn.setText('查看快照' if zh else 'View snapshot')
        self.del_snap_btn.setText('删除快照' if zh else 'Delete snapshot')
        self.model_btn.setText('模型配置' if zh else 'Model settings')
        self.tree_title.setText('对象目录' if zh else 'Objects')
        self.object_filter.setPlaceholderText('搜索对象名 / 注释' if zh else 'Filter objects')
        self.run_btn.setText('执行当前 SQL' if zh else 'Run current SQL')
        self.format_btn.setText('格式化' if zh else 'Format')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.save_draft_btn.setText('保存草稿' if zh else 'Save draft')
        self.ai_title.setText('TamengAgent')
        self.ai_hint.setText(
            '右键输入框添加表/字段。主按钮只生成草案，不会执行。'
            if zh else
            'Right-click to add table/field tokens. Generate never executes.'
        )
        self.nl_input.setPlaceholderText(
            '用自然语言描述要生成的 SQL，例如：查询 prpcmain 中创建日期倒序'
            if zh else
            'Describe the SQL; TamengAgent only uses the current snapshot'
        )
        self.side_tabs.setTabText(0, 'TamengAgent')
        self.ai_pick_btn.setText('选择表和字段' if zh else 'Pick tables/fields')
        self.ai_snap_btn.setText('查看快照' if zh else 'View snapshot')
        self.agent_more.setText('更多操作' if zh else 'More')
        self.agent_cancel_btn.setText('取消' if zh else 'Cancel')
        self.agent_confirm_btn.setText('使用选中字段生成' if zh else 'Generate with selected fields')
        self._refresh_agent_more_menu()
        self.side_tabs.setTabText(1, '对象详情' if zh else 'Object details')
        self.detail_title.setText('对象详情' if zh else 'Object details')
        self.field_filter.setPlaceholderText('搜索字段' if zh else 'Filter fields')
        self.insert_fields_btn.setText('插入选中字段' if zh else 'Insert fields')
        self.send_ai_btn.setText('发送给 AI' if zh else 'Send to AI')
        self.field_prev_btn.setText('上一页' if zh else 'Prev')
        self.field_next_btn.setText('下一页' if zh else 'Next')
        self.ai_gen_btn.setText('生成 SQL 草案' if zh else 'Generate SQL draft')
        self.ai_explain_btn.setText('解释当前 SQL' if zh else 'Explain current SQL')
        self.ai_opt_btn.setText('优化当前 SQL' if zh else 'Optimize current SQL')
        self.ai_fix_btn.setText('修复报错' if zh else 'Fix error')
        self.next_btn.setText('下一页' if zh else 'Next')
        self.all_btn.setText('获取全部' if zh else 'Fetch all')
        self.result_tabs.setTabText(0, '结果' if zh else 'Result')
        self.result_tabs.setTabText(1, '消息' if zh else 'Messages')
        self.result_tabs.setTabText(2, '历史' if zh else 'History')
        self._refresh_model_status()
        self._refresh_header()

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.page_subtitle, low_height)

    def _title(self) -> str:
        return 'SQL 控制台' if self.language == 'zh' else 'SQL Console'

    def _current_conn(self) -> dict | None:
        tab = self._current_tab()
        if tab is not None and isinstance(tab.conn_item, dict):
            return dict(tab.conn_item)
        data = self.conn_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _browse_conn(self) -> dict | None:
        data = self.conn_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _current_tab(self) -> _SqlTab | None:
        widget = self.sql_tabs.currentWidget()
        return widget if isinstance(widget, _SqlTab) else None

    def _current_editor(self) -> SqlEditor | None:
        tab = self._current_tab()
        return tab.editor if tab is not None else None

    def _reload_connections(self, select_id: str = ''):
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        rows = load_connections()
        if not rows:
            zh = self.language == 'zh'
            self.conn_combo.addItem('未配置数据库连接' if zh else 'No database connection', None)
        for item in rows:
            self.conn_combo.addItem(str(item.get('name') or item.get('id')), item)
            if select_id and item.get('id') == select_id:
                self.conn_combo.setCurrentIndex(self.conn_combo.count() - 1)
        self.conn_combo.blockSignals(False)
        self._on_connection_changed()

    def _on_connection_changed(self):
        item = self._browse_conn()
        self._snapshot = load_snapshot(str(item.get('id') or '')) if item else None
        self.nl_input.bind_snapshot(self._snapshot)
        self._rebuild_tree()
        self._refresh_header()
        self._refresh_model_status()
        self._refresh_ai_pick_state()
        self._refresh_agent_status()

    def _refresh_header(self):
        zh = self.language == 'zh'
        item = self._browse_conn()
        if not item:
            self.conn_meta.setText('未选择连接' if zh else 'No connection')
            return
        dialect = str(item.get('dialect') or '')
        label = dict(DIALECTS).get(dialect, dialect)
        status = snapshot_status(item, self._snapshot)
        self.conn_meta.setText(f"{item.get('name') or ''} · {label} · {status['label']}")

    def _refresh_model_status(self):
        zh = self.language == 'zh'
        ready = False
        try:
            ready = is_enabled()
        except Exception:
            ready = False
        for btn in (self.ai_gen_btn, self.ai_explain_btn, self.ai_opt_btn, self.ai_fix_btn):
            btn.setEnabled(ready)
        stale = False
        item = self._current_conn()
        if item:
            stale = snapshot_status(item, self._snapshot).get('stale') or snapshot_status(item, self._snapshot).get('status') == 'missing'
        extra = ''
        if stale and zh:
            extra = ' 结构快照过期或未扫描，生成前请先扫描。'
        elif stale:
            extra = ' Snapshot missing/stale; scan before generating.'
        self.model_status.setText(
            ('TamengAgent 只生成 SQL 草案，不会执行。' if ready and zh else
             'TamengAgent only drafts SQL and never executes.' if ready else
             '未配置内网模型，可手写 SQL。' if zh else
             'Configure an intranet model in Settings.')
            + extra
        )
        self._refresh_agent_status()

    def _edit_connection(self, new=False):
        current = None if new else self._current_conn()
        before = connection_fingerprint(current or {})
        dialog = _ConnectionDialog(self.language, current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item, password = dialog.payload()
        saved = upsert_connection(item, password if password else None)
        if current and connection_fingerprint(saved) != before and current.get('id') == saved.get('id'):
            snap = load_snapshot(saved.get('id'))
            if snap:
                snap['status'] = 'stale'
                snap['fingerprint'] = before
                save_snapshot(snap)
        self._reload_connections(saved.get('id'))

    def _delete_connection(self):
        item = self._current_conn()
        if not item:
            return
        zh = self.language == 'zh'
        detail = (
            f"删除连接「{item.get('name') or ''}」及其本机结构快照？不会删除数据库中的对象。"
            if zh else
            f"Delete connection '{item.get('name') or ''}' and its local snapshot? Database objects are not dropped."
        )
        if not confirm_action(self, self._title(), detail, confirm_text='删除连接和快照' if zh else 'Delete connection and snapshot', danger=True):
            return
        delete_snapshot(item.get('id'))
        delete_connection(item.get('id'))
        self._reload_connections()

    def _delete_snapshot(self):
        item = self._current_conn()
        if not item:
            return
        zh = self.language == 'zh'
        detail = (
            f"删除「{item.get('name') or ''}」的本机结构快照？重新扫描后才能恢复对象目录和 TamengAgent 上下文。"
            if zh else
            f"Delete the local snapshot for '{item.get('name') or ''}'? Scan again to restore objects and TamengAgent context."
        )
        if not confirm_action(self, self._title(), detail, confirm_text='删除' if zh else 'Delete', danger=True):
            return
        delete_snapshot(item.get('id'))
        self._snapshot = None
        self._rebuild_tree()
        self._refresh_header()

    def _view_snapshot(self):
        zh = self.language == 'zh'
        if not self._snapshot:
            show_warning(self, self._title(), '还没有快照' if zh else 'No snapshot')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('结构快照' if zh else 'Schema snapshot')
        dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont('Consolas', 10))
        view.setPlainText(clip_snapshot_for_prompt(self._snapshot, max_chars=20000) or '')
        layout.addWidget(view)
        dialog.exec()

    def _open_settings(self):
        window = self.window()
        if hasattr(window, '_show_panel'):
            window._show_panel(7)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading'):
            self.loading.place_overlay(self)

    def _busy(self, on: bool, message: str = ''):
        zh = self.language == 'zh'
        for btn in (
            self.test_btn, self.scan_btn, self.run_btn,
            self.ai_gen_btn, self.ai_explain_btn, self.ai_opt_btn, self.ai_fix_btn,
            self.next_btn, self.all_btn,
        ):
            btn.setEnabled(not on)
        if on:
            self.loading.start_busy(message or ('处理中…' if zh else 'Working…'))
        else:
            self._refresh_model_status()
            self.next_btn.setEnabled(self._has_more)
            self.all_btn.setEnabled(self._has_more)

    def _start_db(self, kind: str, **kwargs):
        item = self._browse_conn() if kind in ('test', 'scan') else self._current_conn()
        zh = self.language == 'zh'
        if not item:
            show_warning(self, self._title(), '请先新建并选择连接' if zh else 'Create a connection first')
            return
        labels = {
            'test': '正在测试连接…' if zh else 'Testing connection…',
            'scan': '正在扫描结构…' if zh else 'Scanning schema…',
            'query': '正在执行查询…' if zh else 'Running query…',
        }
        self._busy(True, labels.get(kind, '处理中…' if zh else 'Working…'))
        if kind == 'scan':
            self.scan_cancel_btn.setEnabled(True)
        self.result_status.setText(labels.get(kind, '正在连接…' if zh else 'Working…'))
        self._worker = _DbWorker(kind, item, **kwargs)
        self._worker.completed.connect(lambda payload: self._on_db_ok(kind, payload, kwargs))
        self._worker.failed.connect(self._on_db_fail)
        self._worker.finished.connect(lambda: (self.scan_cancel_btn.setEnabled(False), self._busy(False)))
        self._worker.start()

    def _cancel_scan(self):
        if self._worker is not None:
            self._worker.cancelled = True

    def _on_db_ok(self, kind: str, payload: dict, kwargs: dict):
        zh = self.language == 'zh'
        if kind == 'test':
            self.loading.finish('连接成功' if zh else 'Connected')
            show_info(self, self._title(), '连接成功' if zh else 'Connected')
            self._log_msg('连接测试成功' if zh else 'Connection ok')
            return
        if kind == 'scan':
            self._snapshot = payload
            self.nl_input.bind_snapshot(self._snapshot)
            self._rebuild_tree()
            self._refresh_header()
            self._refresh_ai_pick_state()
            count = len((payload or {}).get('objects') or [])
            warning = str((payload or {}).get('warning') or '')
            if str((payload or {}).get('status') or '') == 'failed':
                self.loading.fail(warning or ('扫描失败' if zh else 'Scan failed'))
            else:
                self.loading.finish(f'已扫描 {count} 个对象' if zh else f'Scanned {count} object(s)')
            show_info(self, self._title(), f'已扫描 {count} 个对象' if zh else f'Scanned {count} object(s)')
            self._log_msg(f'扫描完成，对象 {count}')
            return
        if kind == 'query':
            append = bool(kwargs.get('append'))
            self._last_sql = str(payload.get('sql') or self._last_sql)
            self._offset = int(payload.get('offset') or 0)
            self._has_more = bool(payload.get('has_more'))
            self._fill_result(payload, append=append)
            shown = self.result.rowCount()
            elapsed = payload.get('elapsed_ms')
            rowcount = payload.get('rowcount', shown)
            tx = payload.get('tx') or ''
            extra = ''
            if tx == 'committed':
                extra = '，已提交' if zh else ', committed'
            elif tx == 'implicit':
                extra = '，DDL 按库自身提交语义' if zh else ', DDL implicit commit'
            self.result_status.setText(
                f'行 {shown} · 影响 {rowcount} · {elapsed} ms{extra}'
                if zh else
                f'{shown} row(s) · affected {rowcount} · {elapsed} ms{extra}'
            )
            self._log_msg(self.result_status.text())
            self._history.append({
                'time': datetime.now().strftime('%H:%M:%S'),
                'sql': self._last_sql[:300],
                'status': self.result_status.text(),
            })
            self._hist_refresh()
            self.next_btn.setEnabled(self._has_more)
            self.all_btn.setEnabled(self._has_more)
            self.loading.finish(self.result_status.text() or ('查询完成' if zh else 'Done'))

    def _on_db_fail(self, message: str):
        text = redact_error(str(message or ''))
        self.loading.fail(text or ('失败' if self.language == 'zh' else 'Failed'))
        show_error(self, self._title(), text)
        self.result_status.setText('失败' if self.language == 'zh' else 'Failed')
        self._log_msg(text)

    def _log_msg(self, text: str):
        stamp = datetime.now().strftime('%H:%M:%S')
        self.msg_view.appendPlainText(f'[{stamp}] {text}')

    def _hist_refresh(self):
        lines = [f"{row['time']}  {row['status']}\n{row['sql']}" for row in self._history[-50:]]
        self.hist_view.setPlainText('\n\n'.join(reversed(lines)))

    def _fill_result(self, payload: dict, *, append: bool = False):
        columns = list(payload.get('columns') or [])
        rows = list(payload.get('rows') or [])
        if not append:
            self.result.clear()
            self.result.setColumnCount(len(columns))
            self.result.setHorizontalHeaderLabels(columns)
            self.result.setRowCount(0)
        start = self.result.rowCount()
        if start + len(rows) > MAX_ROWS:
            rows = rows[: max(0, MAX_ROWS - start)]
            self._has_more = False
        self.result.setRowCount(start + len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if c >= self.result.columnCount():
                    self.result.setColumnCount(c + 1)
                self.result.setItem(start + r, c, QTableWidgetItem(str(value)))
        header = self.result.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _editor_sql(self) -> str:
        editor = self._current_editor()
        if editor is None:
            return ''
        selected, pos = editor.selected_or_all()
        if selected:
            return selected
        return statement_at_cursor(editor.toPlainText(), pos)

    def _run_sql(self, *, reset: bool = True):
        item = self._current_conn()
        zh = self.language == 'zh'
        if not item:
            show_warning(self, self._title(), '请先选择连接' if zh else 'Select a connection')
            return
        sql = self._editor_sql().strip()
        if not sql:
            show_warning(self, self._title(), '没有可执行的语句' if zh else 'Nothing to run')
            return
        info = classify_statement(sql, str(item.get('dialect') or 'oracle'))
        if info.get('needs_confirm'):
            detail = (
                f"连接：{item.get('name')}\n类型：{item.get('dialect')}\n类别：{info.get('label')}\n\n{sql}"
                if zh else
                f"Connection: {item.get('name')}\nType: {item.get('dialect')}\nKind: {info.get('label')}\n\n{sql}"
            )
            if not confirm_action(
                self, self._title(), detail,
                confirm_text='执行' if zh else 'Run',
                danger=True,
            ):
                return
        if reset:
            self._offset = 0
            self._last_sql = sql
            self.result.setRowCount(0)
        self._start_db('query', sql=sql, offset=self._offset if not reset else 0, limit=PAGE_SIZE, append=not reset)

    def _fetch_next(self):
        if not self._last_sql or not self._has_more:
            return
        self._start_db('query', sql=self._last_sql, offset=self._offset, limit=PAGE_SIZE, append=True)

    def _fetch_all(self):
        if not self._last_sql:
            return
        remaining = max(PAGE_SIZE, MAX_ROWS - self.result.rowCount())
        self._start_db('query', sql=self._last_sql, offset=self._offset, limit=remaining, append=True)

    def _new_sql_tab(self, text: str = '', title: str = ''):
        zh = self.language == 'zh'
        name = title or (f'未命名查询 {self._tab_seq}' if zh else f'Untitled query {self._tab_seq}')
        self._tab_seq += 1
        tab = _SqlTab(name, self._browse_conn())
        if text:
            tab.editor.setPlainText(text)
            tab.dirty = False
        index = self.sql_tabs.addTab(tab, name)
        self.sql_tabs.setCurrentIndex(index)
        return tab

    def _close_sql_tab(self, index: int):
        if self.sql_tabs.count() <= 1:
            editor = self._current_editor()
            if editor is not None:
                editor.clear()
            return
        self.sql_tabs.removeTab(index)

    def _close_current_tab(self):
        self._close_sql_tab(self.sql_tabs.currentIndex())

    def _refresh_tab_titles(self, _index=0):
        for i in range(self.sql_tabs.count()):
            tab = self.sql_tabs.widget(i)
            if isinstance(tab, _SqlTab):
                mark = '*' if tab.dirty else ''
                self.sql_tabs.setTabText(i, mark + tab.base_title)

    def _format_sql(self):
        editor = self._current_editor()
        if editor is None:
            return
        text = editor.toPlainText()
        editor.setPlainText('\n'.join(line.rstrip() for line in text.splitlines()))

    def _clear_editor(self):
        editor = self._current_editor()
        if editor is not None:
            editor.clear()

    def _save_draft(self):
        editor = self._current_editor()
        zh = self.language == 'zh'
        if editor is None:
            return
        import os
        os.makedirs(SQL_DRAFTS_DIR, exist_ok=True)
        path, _filter = QFileDialog.getSaveFileName(
            self, '保存 SQL 草稿' if zh else 'Save SQL draft', SQL_DRAFTS_DIR, 'SQL (*.sql)'
        )
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(editor.toPlainText())
        show_info(self, self._title(), '草稿已保存（请勿写入密码或生产数据）' if zh else 'Draft saved')

    def _rebuild_tree(self):
        self.object_tree.clear()
        snap = self._snapshot or {}
        objects = list(snap.get('objects') or [])
        empty = not objects
        self.tree_empty.setVisible(empty)
        self.object_tree.setVisible(not empty)
        if empty:
            return
        groups = {}
        for obj in objects:
            owner = str(obj.get('owner') or obj.get('object_type') or 'default')
            groups.setdefault(owner, []).append(obj)
        item = self._browse_conn() or {}
        root = QTreeWidgetItem([str(item.get('name') or 'connection')])
        root.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'conn'})
        for owner, rows in groups.items():
            schema = QTreeWidgetItem([owner])
            schema.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'schema', 'name': owner})
            for obj in rows:
                node = QTreeWidgetItem([format_object_label(obj)])
                node.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'table', 'object': obj})
                schema.addChild(node)
            root.addChild(schema)
        self.object_tree.addTopLevelItem(root)
        root.setExpanded(True)
        if root.childCount() == 1:
            root.child(0).setExpanded(True)
        self._filter_tree(self.object_filter.text())

    def _filter_tree(self, text=''):
        needle = str(text or '').strip().lower()

        def apply_item(item: QTreeWidgetItem) -> bool:
            visible = False
            for i in range(item.childCount()):
                if apply_item(item.child(i)):
                    visible = True
            own = needle in item.text(0).lower() if needle else True
            show = own or visible or not needle
            item.setHidden(not show)
            return show

        for i in range(self.object_tree.topLevelItemCount()):
            apply_item(self.object_tree.topLevelItem(i))

    def _selected_object(self) -> dict | None:
        items = self.object_tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        return data if isinstance(data, dict) else None

    def _on_tree_selected(self):
        data = self._selected_object() or {}
        obj = data.get('object') if isinstance(data.get('object'), dict) else None
        self._detail_object = obj
        self._field_page = 0
        self._fill_detail_fields()

    def _qualified(self, obj: dict) -> str:
        dialect = str((self._current_conn() or {}).get('dialect') or 'oracle')
        name = str(obj.get('name') or '')
        owner = str(obj.get('owner') or '')
        if dialect in ('mysql', 'oceanbase'):
            return f'`{name}`'
        if owner and dialect not in ('redis', 'mongodb'):
            return f'{owner}.{name}'
        return name

    def _insert_text(self, piece: str):
        editor = self._current_editor()
        if editor is None or not piece:
            return
        editor.insertPlainText(piece)

    def _on_tree_double(self, item: QTreeWidgetItem, _column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get('kind') == 'table' and isinstance(data.get('object'), dict):
            self._insert_text(self._qualified(data['object']))

    def _tree_menu(self, pos):
        item = self.object_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        obj = data.get('object') if isinstance(data.get('object'), dict) else None
        if data.get('kind') != 'table' or not obj:
            return
        zh = self.language == 'zh'
        menu = QMenu(self)
        act_select = menu.addAction('生成 SELECT *' if zh else 'Generate SELECT *')
        act_copy = menu.addAction('复制限定名' if zh else 'Copy qualified name')
        chosen = menu.exec(self.object_tree.viewport().mapToGlobal(pos))
        name = self._qualified(obj)
        if chosen == act_select:
            dialect = str((self._browse_conn() or {}).get('dialect') or 'oracle')
            if dialect == 'redis':
                sql = f'SCAN 0 MATCH {obj.get("name")} COUNT 20'
            elif dialect == 'mongodb':
                sql = '{"collection":"%s","filter":{}}' % str(obj.get('name') or '').replace('"', '')
            else:
                sql = f'SELECT * FROM {name}'
            self._insert_text(sql)
        elif chosen == act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(name)

    def _run_ai(self, action: str, *, confirmed=None):
        zh = self.language == 'zh'
        if self._agent_busy:
            show_warning(self, self._title(), '当前草案任务仍在运行' if zh else 'A draft task is still running')
            return
        if not is_enabled():
            show_warning(self, self._title(), '请先在设置中启用内网模型' if zh else 'Enable the intranet model first')
            return
        question = self.nl_input.plain_question()
        editor = self._current_editor()
        current_sql = editor.toPlainText() if editor is not None else ''
        if action in ('explain', 'optimize') and not current_sql.strip():
            show_warning(self, self._title(), '当前 Tab 没有 SQL' if zh else 'Current tab is empty')
            return
        if action == 'generate' and not question:
            show_warning(self, self._title(), '请输入要生成的内容' if zh else 'Enter a prompt')
            return
        item = self._browse_conn()
        evidence = None
        if action == 'generate':
            prepared = prepare_request(
                question, self._snapshot, item,
                tokens=self.nl_input.context,
                confirmed=confirmed,
            )
            if prepared.get('state') == 'NEEDS_SELECTION':
                self._show_agent_candidates(prepared)
                return
            if not prepared.get('ok'):
                self._block_agent(prepared.get('reason') or '', prepared.get('next_action') or '')
                return
            evidence = prepared.get('evidence')
            self._pending_evidence = evidence
            self.agent_evidence.setText(format_evidence_bar(evidence) or '')
            self._hide_agent_candidates()
        self._start_agent_task({
            'question': question or current_sql,
            'action': action,
            'dialect': str((item or {}).get('dialect') or 'oracle'),
            'alias': str((item or {}).get('name') or ''),
            'snapshot': self._snapshot if action != 'generate' else None,
            'selected_tables': selected_table_names(self.nl_input.context),
            'selected_fields': selected_field_names(self.nl_input.context),
            'current_sql': current_sql,
            'error_text': question if action == 'fix' else '',
            'stale': False,
            'evidence': evidence,
            'cfg': load_ai_local(),
        }, action)

    def _on_ai_ok(self, draft: dict):
        from tools.ai_sql_draft import format_explanation
        zh = self.language == 'zh'
        if self._ai_worker is not None and getattr(self._ai_worker, 'cancelled', False):
            self._finish_agent_task(cancelled=True)
            return
        sql = str((draft or {}).get('sql') or '')
        dialect = str((self._browse_conn() or {}).get('dialect') or 'oracle')
        evidence = self._pending_evidence
        if evidence is not None:
            checked = validate_generated_sql(sql, evidence, dialect)
            if not checked.get('allowed'):
                self._block_agent(checked.get('reason') or '草案被拦截', '选择字段后重试')
                self.ai_explain.setPlainText(format_explanation(draft or {}) + '\n' + str(checked.get('reason') or ''))
                self._finish_agent_task()
                return
            bar = format_evidence_bar(evidence)
            self.agent_evidence.setText(bar)
            explain = format_explanation(draft or {})
            extra = [
                f"快照：{evidence.get('snapshot_id') or ''} · {evidence.get('scanned_at') or ''}",
                f"字段证据：{bar}" if bar else '',
                '状态：未执行',
            ]
            self.ai_explain.setPlainText(explain + '\n' + '\n'.join(item for item in extra if item))
            title = 'TamengAgent 草案 · 未执行'
            tab = self._new_sql_tab(sql, title)
            tab.base_title = title
            self._refresh_tab_titles()
            self.result_status.setText(title)
            self._log_msg(title)
            self._finish_agent_task()
            return
        self.ai_explain.setPlainText(format_explanation(draft or {}))
        safety = ai_draft_safety(sql, dialect)
        title = 'TamengAgent 草案 · 未执行'
        if safety.get('fail_closed'):
            self.ai_explain.append('\n' + str(safety.get('reason') or ''))
            self.result_status.setText(title)
            self._finish_agent_task()
            return
        if sql:
            tab = self._new_sql_tab(sql, title)
            tab.base_title = title
            self._refresh_tab_titles()
        self.result_status.setText(title)
        self._log_msg(title)
        self._finish_agent_task()

    def _refresh_agent_status(self):
        zh = self.language == 'zh'
        item = self._browse_conn()
        if not item:
            self.agent_status.setText('未选择连接' if zh else 'No connection')
            return
        dialect = str(item.get('dialect') or '')
        label = dict(DIALECTS).get(dialect, dialect)
        status = snapshot_status(item, self._snapshot)
        scanned = str((self._snapshot or {}).get('scanned_at') or '')
        self.agent_status.setText(
            f"{item.get('name') or ''} · {label} · {status.get('label') or ''} · {scanned}".strip(' ·')
        )

    def _refresh_agent_more_menu(self):
        zh = self.language == 'zh'
        menu = getattr(self, '_agent_more_menu', None)
        if menu is None:
            return
        menu.clear()
        menu.addAction('解释当前 SQL' if zh else 'Explain current SQL', lambda: self._run_ai('explain'))
        menu.addAction('优化当前 SQL' if zh else 'Optimize current SQL', lambda: self._run_ai('optimize'))
        menu.addAction('修复报错' if zh else 'Fix error', lambda: self._run_ai('fix'))
        menu.addAction('复制拦截详情' if zh else 'Copy block details', self._copy_block_detail)
        menu.addAction('查看快照' if zh else 'View snapshot', self._view_snapshot)

    def _show_agent_candidates(self, prepared: dict):
        zh = self.language == 'zh'
        self.agent_candidates.clear()
        fields = ((prepared.get('resolution') or {}).get('fields') or [])
        for item in fields:
            obj = item.get('object') or {}
            col = item.get('column') or {}
            text = (
                f"{obj.get('name')}.{col.get('name')}  {col.get('data_type') or ''}  "
                f"{col.get('comment') or ''}  [{item.get('reason') or ''}]"
            )
            row = QListWidgetItem(text.strip())
            row.setData(Qt.ItemDataRole.UserRole, field_qualified(obj, col))
            self.agent_candidates.addItem(row)
        self.agent_candidates.show()
        self.agent_confirm_btn.show()
        self.ai_explain.setPlainText(prepared.get('reason') or ('找到多个“创建日期”候选，请选择要使用的字段。' if zh else 'Pick a field'))
        self._last_block = prepared.get('reason') or ''

    def _hide_agent_candidates(self):
        self.agent_candidates.hide()
        self.agent_confirm_btn.hide()

    def _confirm_agent_fields(self):
        chosen = []
        for item in self.agent_candidates.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                chosen.append(str(data))
        if not chosen:
            show_warning(self, self._title(), '请选择要使用的字段' if self.language == 'zh' else 'Select fields')
            return
        self._hide_agent_candidates()
        self._run_ai('generate', confirmed=chosen)

    def _block_agent(self, reason: str, next_action: str = ''):
        zh = self.language == 'zh'
        text = str(reason or '')
        if next_action:
            text = f'{text}\n下一步：{next_action}' if zh else f'{text}\nNext: {next_action}'
        self._last_block = text
        self.ai_explain.setPlainText('草案被拦截\n' + text)
        show_warning(self, self._title(), text)

    def _copy_block_detail(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._last_block or self.ai_explain.toPlainText())

    def _start_agent_task(self, kwargs: dict, action: str):
        zh = self.language == 'zh'
        self._agent_busy = True
        self._agent_started = time.monotonic()
        self._agent_action = action
        self.ai_gen_btn.setEnabled(False)
        self.agent_cancel_btn.show()
        self.agent_stage.setText('正在校验 Schema 字段…' if zh else 'Checking schema fields…')
        self.agent_stage.hide()
        self._agent_timer.start()
        self._ai_worker = _AiWorker(kwargs)
        self._ai_worker.completed.connect(self._on_ai_ok)
        self._ai_worker.failed.connect(self._on_agent_fail)
        self._ai_worker.finished.connect(lambda: None)
        self._ai_worker.start()

    def _tick_agent_stage(self):
        if not self._agent_busy:
            self._agent_timer.stop()
            return
        elapsed = time.monotonic() - self._agent_started
        zh = self.language == 'zh'
        if elapsed < 0.4:
            return
        self.agent_stage.show()
        if elapsed < 2:
            self.agent_stage.setText('正在校验 Schema 字段…' if zh else 'Checking schema fields…')
            return
        seconds = int(elapsed)
        self.agent_stage.setText(
            f'校验快照 → 匹配表字段 → 生成草案 → 复核 SQL · 已耗时 {seconds}s'
            if zh else
            f'validate → match → draft → review · {seconds}s'
        )

    def _cancel_agent(self):
        zh = self.language == 'zh'
        if not self._agent_busy:
            return
        self.agent_stage.show()
        self.agent_stage.setText('正在取消…' if zh else 'Cancelling…')
        if self._ai_worker is not None:
            self._ai_worker.cancelled = True

    def _finish_agent_task(self, *, cancelled: bool = False):
        self._agent_busy = False
        self._agent_timer.stop()
        self.agent_cancel_btn.hide()
        if cancelled:
            self.agent_stage.setText('已取消' if self.language == 'zh' else 'Cancelled')
        else:
            self.agent_stage.hide()
        self._refresh_model_status()

    def _on_agent_fail(self, message: str):
        if self._ai_worker is not None and getattr(self._ai_worker, 'cancelled', False):
            self._finish_agent_task(cancelled=True)
            return
        text = redact_error(str(message or ''))
        self._last_block = text
        self.ai_explain.setPlainText(text)
        show_error(self, self._title(), text)
        self._finish_agent_task()

    def _refresh_ai_chips(self):
        zh = self.language == 'zh'
        ctx = self.nl_input.context
        objs = [str(item.get('qualified_name') or item.get('name')) for item in ctx.get('selected_objects') or []]
        fields = [str(item.get('name') or '') for item in ctx.get('selected_fields') or []]
        if not objs and not fields:
            self.ai_chips.setText('尚未添加表或字段 Token' if zh else 'No object tokens yet')
            return
        self.ai_chips.setText(
            ('已添加：' if zh else 'Added: ') +
            '；'.join((['表 ' + ', '.join(objs)] if objs else []) + (['字段 ' + ', '.join(fields)] if fields else []))
        )

    def _refresh_ai_pick_state(self):
        item = self._browse_conn()
        ok, reason = context_matches_snapshot({}, self._snapshot, item)
        self.nl_input.setEnabled(True)
        if not ok:
            self.model_status.setText((self.model_status.text() + ' ' + reason).strip())

    def _pick_ai_object(self, mode: str, position: int):
        zh = self.language == 'zh'
        item = self._browse_conn()
        ok, reason = context_matches_snapshot({}, self._snapshot, item)
        if not ok:
            show_warning(self, self._title(), reason + '\n请先扫描/更新结构。' if zh else reason)
            return
        redis = str((item or {}).get('dialect') or '') == 'redis'
        if mode == 'field' and redis:
            show_warning(self, self._title(), 'Redis 不提供关系型字段选择' if zh else 'Redis has no table fields')
            return
        dialog = ObjectPickDialog(self.language, self._snapshot, mode=mode, parent=self, redis=redis)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        obj = dialog.chosen_object()
        if not obj:
            return
        self.nl_input.bind_snapshot(self._snapshot)
        if mode == 'table':
            token = add_object(self.nl_input.context, obj)
            self.nl_input.insert_token('object', token, position)
        else:
            fields = dialog.chosen_fields()
            if not fields:
                show_warning(self, self._title(), '请先选择表再勾选字段' if zh else 'Pick a table then fields')
                return
            for field in fields:
                token = add_field(self.nl_input.context, obj, field)
                self.nl_input.insert_token('field', token, position)
                position = self.nl_input.textCursor().position()

    def _fill_detail_fields(self, _text=''):
        obj = self._detail_object
        zh = self.language == 'zh'
        self.field_table.clear()
        self.field_table.setColumnCount(5)
        self.field_table.setHorizontalHeaderLabels(
            ['字段名', '类型', '可空', '键', '注释'] if zh else ['Name', 'Type', 'Null', 'Key', 'Comment']
        )
        if not obj:
            self.detail_title.setText('对象详情' if zh else 'Object details')
            self.detail_meta.setText('在左侧选择一个对象' if zh else 'Select an object')
            self.field_table.setRowCount(0)
            return
        qn = self._qualified(obj)
        cols = search_fields(obj, self.field_filter.text())
        page_size = 100
        pages = max(1, (len(cols) + page_size - 1) // page_size)
        self._field_page = max(0, min(self._field_page, pages - 1))
        start = self._field_page * page_size
        view = cols[start:start + page_size]
        comment = str(obj.get('comment') or '').strip()
        title = f"{qn}  [{obj.get('object_type') or 'TABLE'}]"
        if comment:
            title = f'{title}  {comment}'
        self.detail_title.setText(title)
        extra = ' · 推断字段' if obj.get('inferred') and zh else ''
        self.detail_meta.setText(
            f"{qn} · {len(cols)} 个字段 · 第 {self._field_page + 1}/{pages} 页{extra}"
            if zh else
            f'{qn} · {len(cols)} field(s) · page {self._field_page + 1}/{pages}{extra}'
        )
        self.field_table.setRowCount(len(view))
        for i, col in enumerate(view):
            key = 'PK' if col.get('primary_key') else ('IDX' if col.get('indexed') else '')
            values = [
                str(col.get('name') or ''),
                str(col.get('data_type') or ''),
                ('否' if not col.get('nullable') else '是') if zh else ('NO' if not col.get('nullable') else 'YES'),
                key,
                str(col.get('comment') or ''),
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, col if c == 0 else None)
                self.field_table.setItem(i, c, item)
        self.field_table.setSelectionBehavior(self.field_table.SelectionBehavior.SelectRows)
        self.field_prev_btn.setEnabled(self._field_page > 0)
        self.field_next_btn.setEnabled(self._field_page + 1 < pages)

    def _shift_field_page(self, delta: int):
        self._field_page += int(delta)
        self._fill_detail_fields()

    def _selected_detail_fields(self) -> list:
        rows = []
        for index in self.field_table.selectionModel().selectedRows() if self.field_table.selectionModel() else []:
            item = self.field_table.item(index.row(), 0)
            data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def _insert_detail_fields(self):
        names = [str(col.get('name') or '') for col in self._selected_detail_fields()]
        self._insert_text(', '.join(names))

    def _send_detail_to_ai(self):
        if not self._detail_object:
            return
        self._send_object_to_ai(self._detail_object, self._selected_detail_fields())

    def _send_object_to_ai(self, obj: dict, fields: list):
        self.nl_input.bind_snapshot(self._snapshot)
        pos = self.nl_input.textCursor().position()
        token = add_object(self.nl_input.context, obj)
        self.nl_input.insert_token('object', token, pos)
        pos = self.nl_input.textCursor().position()
        for field in fields or []:
            token = add_field(self.nl_input.context, obj, field)
            self.nl_input.insert_token('field', token, pos)
            pos = self.nl_input.textCursor().position()
        self.side_tabs.setCurrentIndex(0)
