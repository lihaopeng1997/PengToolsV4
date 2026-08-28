# -*- coding: utf-8 -*-
"""模型对话：网页式多轮聊天，用于验证内网模型配置。不带入 SQL 控制台上下文。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import os

from config import load_settings, save_settings
from tools import harness_project
from tools.agent_runtime import run_agent_loop, TOOL_SCHEMAS, validate_path
from tools.agent_store import (
    append_message as aw_append_message,
    append_tool_call as aw_append_tool_call,
    create_conversation,
    delete_workspace,
    empty_workspace,
    list_conversations as aw_list_conversations,
    list_workspaces,
    load_conversation as aw_load_conversation,
    load_workspace,
    pop_last_assistant_message,
    rename_conversation,
    save_workspace,
    set_active_conversation,
    update_workspace,
)
from tools.chat_intent import detect_take_data_intent
from tools.intranet_llm import list_enabled_items, ping_model
from tools.intranet_llm import list_enabled_items, ping_model
from tools.model_chat_store import (
    SYSTEM_PROMPT, append_message, create_session, delete_session, load_index,
    load_session, rename_session, save_session, search_sessions, trim_messages_for_request,
    update_message,
)
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_warning
from ui.design_system import apply_button
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_empty_state, make_page_header, make_page_toolbar
from ui.splitter_prefs import install_splitter_prefs


class _WorkbenchBridge(QObject):
    """把子线程里的 confirm/progress 转发到主线程执行，避免 PyQt6 跨线程操作 UI 闪退。

    - confirm 需要同步返回：用 BlockingQueuedConnection 让主线程执行弹窗并阻塞子线程直到返回。
    - progress 走异步信号，不阻塞。
    """

    progressEmitted = pyqtSignal(str, str)   # (role, content)

    def __init__(self, owner: 'ModelChatPanel'):
        super().__init__()
        self._owner = owner
        self.progressEmitted.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _confirm_slot(self):
        # 在主线程弹确认框，结果存到调用方容器，BlockingQueuedConnection 保证返回前已填好
        pending = getattr(self, '_pending', None)
        if pending is None:
            return
        title, content, box = pending['title'], pending['content'], pending['box']
        box['value'] = confirm_action(self._owner, title, content)
        self._pending = None

    def confirm(self, title: str, content: str) -> bool:
        """子线程调用：阻塞式让主线程弹确认框，返回用户选择。"""
        from PyQt6.QtCore import QMetaObject
        box = {'value': False}
        self._pending = {'title': title, 'content': content, 'box': box}
        # 把 _confirm_slot 投递到主线程并以 BlockingQueuedConnection 阻塞等待其执行完毕
        QMetaObject.invokeMethod(
            self, '_confirm_slot',
            Qt.ConnectionType.BlockingQueuedConnection,
        )
        return box['value']

    def progress(self, role: str, content: str):
        # 异步投递到主线程，不阻塞子线程
        self.progressEmitted.emit(role, content)

    @pyqtSlot(str, str)
    def _on_progress(self, role: str, content: str):
        self._owner._wb_append_message(role, content)


def _invoke_in_main_thread(receiver, *args):
    """（保留兼容）同 _WorkbenchBridge.confirm 的跨线程同步调用。"""
    raise NotImplementedError('请直接使用 _WorkbenchBridge')


class _WorkbenchWorker(QThread):
    finished = pyqtSignal(str, list, list)   # final_answer, messages, tool_calls
    failed = pyqtSignal(str)                 # 子线程异常信息（已在主线程显示）

    def __init__(self, user_message, workspace_dir, model_cfg,
                 messages, tool_calls, plan_confirm, confirm_cb, progress_cb):
        super().__init__()
        self.user_message = user_message
        self.workspace_dir = workspace_dir
        self.model_cfg = model_cfg
        self.messages = messages
        self.tool_calls = tool_calls
        self.plan_confirm = plan_confirm
        self.confirm_cb = confirm_cb
        self.progress_cb = progress_cb
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        from tools.agent_runtime import run_agent_loop
        try:
            final, msgs, tcs = run_agent_loop(
                user_message=self.user_message,
                workspace_dir=self.workspace_dir,
                model_cfg=self.model_cfg,
                messages=self.messages,
                tool_calls=self.tool_calls,
                plan_confirm=self.plan_confirm,
                confirm_cb=self.confirm_cb,
                progress_cb=self.progress_cb,
            )
            self.finished.emit(final, msgs, tcs)
        except Exception as exc:
            # 关键：子线程任何异常都必须阻止进程崩溃，转发到主线程显示
            import traceback
            from tools.sql_guard import redact_error
            detail = redact_error(str(exc)) or exc.__class__.__name__
            trace = traceback.format_exc()
            self.failed.emit(f'{detail}\n{trace[-1200:]}')


class _ChatWorker(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, messages, cfg):
        super().__init__()
        self.messages = messages
        self.cfg = cfg
        self.cancelled = False

    def run(self):
        try:
            from tools.intranet_llm import chat_completions
            text = chat_completions(self.messages, cfg=self.cfg)
            if self.cancelled:
                return
            self.completed.emit(text)
        except Exception as exc:
            if self.cancelled:
                return
            self.failed.emit(redact_error(str(exc)))


class _PingWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self):
        self.completed.emit(ping_model(self.cfg))


class _ScanWorker(QThread):
    """后台现场扫描连接结构：只读结构读取，不执行任何业务 SQL。"""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, item: dict):
        super().__init__()
        self.item = dict(item or {})

    def run(self):
        from tools.db_connect import open_connection
        from tools.schema_snapshot import save_snapshot, scan_schema
        conn = None
        try:
            conn = open_connection(self.item)
            payload = scan_schema(conn, self.item)
            if str(payload.get('status') or '') == 'failed':
                self.failed.emit(str(payload.get('warning') or '扫描失败'))
                return
            save_snapshot(payload)
            self.completed.emit(payload)
        except Exception as exc:
            self.failed.emit(redact_error(str(exc)))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


class ModelChatPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._worker = None
        self._ping_worker = None
        self._scan_worker = None
        self._session = None
        self._pending_id = ''
        # 工作台模式
        self._workbench_mode = False
        self._workspace_session = None   # 当前 workspace dict
        self._wb_tool_calls = []         # 当前任务累计 tool_calls
        self._wb_agent_worker = None     # _WorkbenchWorker 引用
        self._wb_plan_confirm = False    # 始终先确认计划（默认关）
        self._wb_bridge = None           # 跨线程桥（延迟创建）
        self._setup_ui()
        self.set_language(language)
        self._reload_models()
        self._reload_connections()
        self._reload_sessions()
        self._maybe_show_banner()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        header, self.page_title, self.page_subtitle = make_page_header(
            '模型对话',
            '连续对话并验证内网模型配置',
            'chat',
        )
        root.addWidget(header)

        self.banner = QFrame()
        self.banner.setObjectName('chat-risk-banner')
        banner_l = QHBoxLayout(self.banner)
        banner_l.setContentsMargins(12, 8, 12, 8)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_close = QPushButton()
        apply_button(self.banner_close, 'ghost', compact=True)
        self.banner_close.clicked.connect(self._dismiss_banner)
        banner_l.addWidget(self.banner_text, 1)
        banner_l.addWidget(self.banner_close)
        root.addWidget(self.banner)

        toolbar, top = make_page_toolbar(divided=True)
        self.model_combo = QComboBox()
        size_pick_combo(self.model_combo)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.ping_btn = QPushButton()
        apply_button(self.ping_btn, 'secondary', compact=True)
        self.ping_btn.clicked.connect(self._ping_current)
        self.skill_btn = QPushButton()
        apply_button(self.skill_btn, 'ghost', compact=True)
        self.skill_btn.clicked.connect(self._open_skill_manager)
        self.conn_combo = QComboBox()
        size_pick_combo(self.conn_combo)
        self.conn_combo.setToolTip('取数意图使用：选择数据库连接，只读结构，不自动执行')
        self.ping_status = QLabel()
        self.ping_status.setObjectName('field-hint')
        self.ping_status.setWordWrap(True)
        self.mode_btn = QPushButton()
        apply_button(self.mode_btn, 'ghost', compact=True)
        self.mode_btn.clicked.connect(self._toggle_workbench_mode)
        top.addWidget(self.model_combo)
        top.addWidget(self.ping_btn)
        top.addWidget(self.skill_btn)
        top.addWidget(self.conn_combo)
        top.addWidget(self.ping_status, 1)
        top.addWidget(self.mode_btn)
        root.addWidget(toolbar)

        # 工作台模式隐藏工具栏（会话列表+conn_combo 等）
        self._toolbar_widget = toolbar

        # ── 主内容区：QStackedWidget ───────────────────────────────────────
        # index 0: 现有对话模式（chat mode）
        # index 1: 工作台模式（workbench mode）
        self.content_stack = QStackedWidget()
        root.addWidget(self.content_stack, 1)

        # ── index 0: 对话模式（复用原有 split） ───────────────────────────
        chat_widget = QWidget()
        chat_root = QVBoxLayout(chat_widget)
        chat_root.setContentsMargins(0, 0, 0, 0)
        chat_root.setSpacing(8)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QFrame()
        left.setObjectName('dashboard-task-card')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        size_line(self.search, 'std')
        self.search.textChanged.connect(self._reload_sessions)
        self.new_btn = QPushButton()
        apply_button(self.new_btn, 'secondary', compact=True)
        self.new_btn.clicked.connect(self._new_session)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.new_btn)
        left_l.addLayout(search_row)
        self.session_list = QListWidget()
        self.session_list.currentItemChanged.connect(self._on_session_changed)
        left_l.addWidget(self.session_list, 1)
        sess_btns = QHBoxLayout()
        self.rename_btn = QPushButton()
        apply_button(self.rename_btn, 'ghost', compact=True)
        self.rename_btn.clicked.connect(self._rename_session)
        self.delete_btn = QPushButton()
        apply_button(self.delete_btn, 'ghost', compact=True)
        self.delete_btn.clicked.connect(self._delete_session)
        sess_btns.addWidget(self.rename_btn)
        sess_btns.addWidget(self.delete_btn)
        left_l.addLayout(sess_btns)
        split.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_host = QWidget()
        self.thread_layout = QVBoxLayout(self.thread_host)
        self.thread_layout.setContentsMargins(8, 8, 8, 8)
        self.thread_layout.addStretch(1)
        self.scroll.setWidget(self.thread_host)
        right_l.addWidget(self.scroll, 1)
        self.empty = make_empty_state('还没有对话', '新建会话后即可向已启用的内网模型发消息')
        right_l.addWidget(self.empty)
        self.trim_hint = QLabel()
        self.trim_hint.setObjectName('field-hint')
        self.trim_hint.hide()
        right_l.addWidget(self.trim_hint)
        self.input = QPlainTextEdit()
        self.input.setMaximumHeight(110)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.installEventFilter(self)
        right_l.addWidget(self.input)
        send_row = QHBoxLayout()
        self.send_hint = QLabel()
        self.send_hint.setObjectName('field-hint')
        self.send_btn = QPushButton()
        apply_button(self.send_btn, 'primary', compact=True)
        self.send_btn.clicked.connect(self._send)
        self.stop_btn = QPushButton()
        apply_button(self.stop_btn, 'secondary', compact=True)
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        self.retry_btn = QPushButton()
        apply_button(self.retry_btn, 'ghost', compact=True)
        self.retry_btn.clicked.connect(self._regenerate)
        send_row.addWidget(self.send_hint, 1)
        send_row.addWidget(self.retry_btn)
        send_row.addWidget(self.stop_btn)
        send_row.addWidget(self.send_btn)
        right_l.addLayout(send_row)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        self.chat_splitter = split
        install_splitter_prefs(
            split,
            defaults=[260, 780],
            page_id='model-chat',
            tab_id='main',
            min_sizes=[180, 360],
            accessible_name='模型对话左右分隔',
        )
        chat_root.addWidget(split, 1)
        self.content_stack.addWidget(chat_widget)

        # ── index 1: 工作台模式 ──────────────────────────────────────────
        self._setup_workbench_ui()

    def eventFilter(self, watched, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            settings = load_settings()
            ctrl_enter = bool(settings.get('model_chat_send_with_ctrl_enter', True))
            enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            if enter and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                has_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if ctrl_enter and has_ctrl:
                    self._send()
                    return True
                if (not ctrl_enter) and (not has_ctrl):
                    self._send()
                    return True
        return super().eventFilter(watched, event)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('模型对话' if zh else 'Model Chat')
        self.page_subtitle.setText(
            '连续对话，用于验证内网模型；不自动带入 SQL 控制台内容'
            if zh else
            'Chat to verify intranet models. SQL console context is never auto-sent.'
        )
        self.banner_text.setText(
            '聊天记录以明文保存在本机 data 目录。请勿输入密码、Token、客户隐私、生产敏感数据或抓包报文。'
            if zh else
            'Chats are stored as plain UTF-8 JSON in the local data folder. Do not paste passwords, tokens or production data.'
        )
        self.banner_close.setText('知道了' if zh else 'Dismiss')
        self.ping_btn.setText('测试当前模型' if zh else 'Test model')
        self.skill_btn.setText('skill 管理' if zh else 'Skills')
        self.conn_combo.setPlaceholderText(
            '数据库连接（取数时）' if zh else 'DB connection (for data queries)'
        )
        self.search.setPlaceholderText('搜索会话' if zh else 'Search sessions')
        self.new_btn.setText('新建' if zh else 'New')
        self.rename_btn.setText('重命名' if zh else 'Rename')
        self.delete_btn.setText('删除' if zh else 'Delete')
        self.send_btn.setText('发送' if zh else 'Send')
        self.stop_btn.setText('停止' if zh else 'Stop')
        self.retry_btn.setText('重新生成' if zh else 'Regenerate')
        ctrl = bool(load_settings().get('model_chat_send_with_ctrl_enter', True))
        self.send_hint.setText(
            'Enter 换行，Ctrl+Enter 发送' if ctrl and zh else
            'Ctrl+Enter to send, Enter for newline' if ctrl else
            'Enter 发送，Shift+Enter 换行' if zh else
            'Enter to send, Shift+Enter for newline'
        )
        self.input.setPlaceholderText('输入消息…' if zh else 'Message…')
        # 工作台模式
        self.mode_btn.setText('工作台' if zh else 'Workbench')
        if hasattr(self, 'wb_dir_btn'):
            self.wb_dir_btn.setText('绑定目录' if zh else 'Bind folder')
            self.wb_dir_label.setText(
                self._workspace_session.get('workspace_dir') if self._workspace_session else '（未绑定目录）'
            )
            self.wb_plan_check.setText('确认计划' if zh else 'Confirm plan')
            self.wb_new_btn.setText('新建工作台' if zh else 'New workspace')
            self.wb_toggle_btn.setText('返回对话' if zh else 'Back to chat')
            self.wb_send_btn.setText('发送' if zh else 'Send')
            self.wb_stop_btn.setText('停止' if zh else 'Stop')
            self.wb_input.setPlaceholderText('描述任务…' if zh else 'Describe task…')

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.page_subtitle, low_height)

    def _title(self) -> str:
        return '模型对话' if self.language == 'zh' else 'Model Chat'

    # ── 工作台模式 ────────────────────────────────────────────────────────

    def _setup_workbench_ui(self):
        """构建工作台模式 UI（三栏：项目树 | 对话 | 预览），作为 index 1 加入 content_stack。"""
        wb = QWidget()
        wb_root = QVBoxLayout(wb)
        wb_root.setContentsMargins(0, 0, 0, 0)
        wb_root.setSpacing(6)

        # 工作台工具栏（绑定目录 / plan确认开关）
        wb_toolbar = QFrame()
        wb_toolbar.setObjectName('page-toolbar')
        wb_tl = QHBoxLayout(wb_toolbar)
        wb_tl.setContentsMargins(8, 4, 8, 4)
        self.wb_dir_btn = QPushButton()
        apply_button(self.wb_dir_btn, 'ghost', compact=True)
        self.wb_dir_btn.clicked.connect(self._wb_bind_directory)
        self.wb_dir_label = QLabel()
        self.wb_dir_label.setObjectName('field-hint')
        self.wb_dir_label.setWordWrap(False)
        self.wb_plan_check = QCheckBox()
        self.wb_plan_check.setObjectName('field-hint')
        self.wb_plan_check.stateChanged.connect(self._wb_on_plan_confirm_changed)
        self.wb_new_btn = QPushButton()
        apply_button(self.wb_new_btn, 'secondary', compact=True)
        self.wb_new_btn.clicked.connect(self._wb_new_workspace)
        self.wb_toggle_btn = QPushButton()
        apply_button(self.wb_toggle_btn, 'ghost', compact=True)
        self.wb_toggle_btn.clicked.connect(self._toggle_workbench_mode)
        wb_tl.addWidget(self.wb_dir_btn)
        wb_tl.addWidget(self.wb_dir_label, 1)
        wb_tl.addWidget(self.wb_plan_check)
        wb_tl.addWidget(self.wb_new_btn)
        wb_tl.addWidget(self.wb_toggle_btn)
        self._wb_toolbar = wb_toolbar
        wb_root.addWidget(wb_toolbar)

        # 三栏
        split = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：空间（工作台任务）列表 + 项目文件树
        left = QFrame()
        left.setObjectName('dashboard-task-card')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(8, 8, 8, 8)

        # 空间列表：顶层=工作台（可折叠），子级=该空间下的对话记录
        space_head = QHBoxLayout()
        space_title = QLabel('空间')
        space_title.setObjectName('field-hint')
        self.wb_space_new_btn = QPushButton('+')
        self.wb_space_new_btn.setToolTip('新建对话')
        self.wb_space_new_btn.setFixedSize(22, 22)
        apply_button(self.wb_space_new_btn, 'ghost', compact=True)
        self.wb_space_new_btn.clicked.connect(self._wb_new_conversation)
        space_head.addWidget(space_title)
        space_head.addStretch(1)
        space_head.addWidget(self.wb_space_new_btn)
        left_l.addLayout(space_head)
        self.wb_space_tree = QTreeWidget()
        self.wb_space_tree.setHeaderHidden(True)
        self.wb_space_tree.setIndentation(14)
        self.wb_space_tree.itemClicked.connect(self._wb_on_space_clicked)
        self.wb_space_tree.itemExpanded.connect(lambda _i: None)
        left_l.addWidget(self.wb_space_tree, 3)

        # 项目文件树
        left_l.addWidget(QLabel('项目文件'))
        self.wb_tree = QTreeWidget()
        self.wb_tree.setHeaderHidden(True)
        self.wb_tree.setIndentation(14)
        self.wb_tree.itemDoubleClicked.connect(self._wb_on_tree_double_click)
        left_l.addWidget(self.wb_tree, 1)
        split.addWidget(left)

        # 中栏：对话流 + 输入
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        self.wb_scroll = QScrollArea()
        self.wb_scroll.setWidgetResizable(True)
        self.wb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.wb_thread_host = QWidget()
        self.wb_thread_layout = QVBoxLayout(self.wb_thread_host)
        self.wb_thread_layout.setContentsMargins(8, 8, 8, 8)
        self.wb_thread_layout.addStretch(1)
        self.wb_scroll.setWidget(self.wb_thread_host)
        mid.addWidget(self.wb_scroll, 1)
        self.wb_input = QPlainTextEdit()
        self.wb_input.setMaximumHeight(110)
        self.wb_input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.wb_input.installEventFilter(self)
        mid.addWidget(self.wb_input)
        wb_send_row = QHBoxLayout()
        self.wb_send_btn = QPushButton()
        apply_button(self.wb_send_btn, 'primary', compact=True)
        self.wb_send_btn.clicked.connect(self._wb_send)
        self.wb_stop_btn = QPushButton()
        apply_button(self.wb_stop_btn, 'secondary', compact=True)
        self.wb_stop_btn.clicked.connect(self._wb_stop)
        self.wb_stop_btn.setEnabled(False)
        wb_send_row.addWidget(self.wb_send_btn, 1)
        wb_send_row.addWidget(self.wb_stop_btn)
        mid.addLayout(wb_send_row)
        mid_widget = QWidget()
        mid_widget.setLayout(mid)
        split.addWidget(mid_widget)

        # 右栏：预览（文件内容 / diff）
        right = QFrame()
        right.setObjectName('dashboard-task-card')
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(8, 8, 8, 8)
        self.wb_preview = QPlainTextEdit()
        self.wb_preview.setReadOnly(True)
        self.wb_preview.setObjectName('detail-text')
        right_l.addWidget(QLabel('预览'))
        right_l.addWidget(self.wb_preview, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        install_splitter_prefs(
            split,
            defaults=[200, 560, 240],
            page_id='model-chat',
            tab_id='workbench',
            min_sizes=[120, 280, 160],
            accessible_name='工作台三栏分隔',
        )
        wb_root.addWidget(split, 1)
        self.content_stack.addWidget(wb)
        self._wb_split = split

    def _toggle_workbench_mode(self):
        """对话 ↔ 工作台 模式切换。"""
        self._workbench_mode = not self._workbench_mode
        self.content_stack.setCurrentIndex(1 if self._workbench_mode else 0)
        if self._workbench_mode:
            self.conn_combo.hide()
            self.skill_btn.hide()
            self.ping_btn.hide()
            self._wb_refresh_space_tree()
            # 恢复最近使用的工作台（首次进入）
            if self._workspace_session is None:
                metas = list_workspaces()
                if metas:
                    self._select_workspace(str(metas[0].get('id') or ''),
                                           select_last_conv=True)
        else:
            self.conn_combo.show()
            self.skill_btn.show()
            self.ping_btn.show()

    def _select_workspace(self, ws_id: str, select_last_conv: bool = False):
        """按 id 加载一个空间到工作台（供进入模式/恢复用）。"""
        ws = load_workspace(ws_id)
        if not ws:
            return
        self._workspace_session = ws
        self._wb_tool_calls = []
        self._wb_plan_confirm = bool(ws.get('plan_confirm', False))
        self.wb_plan_check.setChecked(self._wb_plan_confirm)
        self.wb_dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
        if ws.get('workspace_dir'):
            self._wb_refresh_tree(ws['workspace_dir'])
        else:
            self.wb_tree.clear()
        conv = self._active_conversation_of(ws)
        self._wb_render_conversation(ws, conv)
        self._wb_refresh_space_tree(select_ws_id=ws.get('id'),
                                    select_conv_id=conv.get('id') if select_last_conv else '')

    def _wb_bind_directory(self):
        """绑定/更换工作文件夹。未建空间时自动先建一个并绑定。"""
        path = QFileDialog.getExistingDirectory(
            self, '选择工作文件夹', os.path.expanduser('~'),
        )
        if not path:
            return
        if not self._workspace_session:
            # 无空间：自动创建默认空间（避免「绑定目录后无从归属」）
            ws = empty_workspace(title='新工作台')
            save_workspace(ws)
            self._workspace_session = ws
            self._wb_tool_calls = []
            self._wb_plan_confirm = ws.get('plan_confirm', False)
            self.wb_plan_check.setChecked(self._wb_plan_confirm)
        self._workspace_session['workspace_dir'] = path
        save_workspace(self._workspace_session)
        self.wb_dir_label.setText(path)
        self._wb_refresh_tree(path)
        self._wb_refresh_space_tree(select_ws_id=self._workspace_session.get('id'))

    def _wb_refresh_tree(self, workspace_dir: str):
        """刷新项目文件树（白名单扩展名，懒加载顶层）。"""
        self.wb_tree.clear()
        if not workspace_dir or not os.path.isdir(workspace_dir):
            return

        def add_items(parent, dir_path: str, depth=0):
            if depth > 3:
                return
            try:
                entries = sorted(os.listdir(dir_path))
            except OSError:
                return
            for name in entries:
                if name.startswith('.'):
                    continue
                full = os.path.join(dir_path, name)
                is_dir = os.path.isdir(full)
                ext = os.path.splitext(name)[1].lower()
                whitelist = ('.py', '.js', '.ts', '.vue', '.html', '.css', '.scss',
                             '.md', '.json', '.txt', '.yml', '.yaml', '.xml',
                             '.sql', '.sh', '.bat', '.ps1', '.rs', '.go',
                             '.java', '.c', '.cpp', '.h', '.hpp', '.less')
                if not is_dir and ext not in whitelist:
                    continue
                item = QTreeWidgetItem(parent)
                item.setText(0, name)
                item.setData(0, Qt.ItemDataRole.UserRole, full)
                if is_dir:
                    item.setText(0, name + '/')
                    # 占位子节点，等展开时再加载
                    QTreeWidgetItem(item)
                    item.setExpanded(False)
                else:
                    item.setToolTip(0, full)

        root = QTreeWidgetItem(self.wb_tree)
        root.setText(0, os.path.basename(workspace_dir) + '/')
        root.setData(0, Qt.ItemDataRole.UserRole, workspace_dir)
        add_items(root, workspace_dir, depth=0)
        self.wb_tree.insertTopLevelItem(0, root)
        root.setExpanded(True)

    def _wb_on_tree_double_click(self, item: QTreeWidgetItem, column: int):
        """双击文件 → 在预览区显示内容（限白名单且 ≤200KB）。"""
        full = item.data(0, Qt.ItemDataRole.UserRole)
        if not full or os.path.isdir(full):
            return
        # 懒加载子节点
        if item.childCount() == 1 and not item.child(0).data(0, Qt.ItemDataRole.UserRole):
            item.removeChild(item.child(0))
            try:
                for name in sorted(os.listdir(full)):
                    if name.startswith('.'):
                        continue
                    child_full = os.path.join(full, name)
                    is_dir = os.path.isdir(child_full)
                    child = QTreeWidgetItem(item)
                    child.setText(0, name + ('/' if is_dir else ''))
                    child.setData(0, Qt.ItemDataRole.UserRole, child_full)
                    if is_dir:
                        QTreeWidgetItem(child)
            except OSError:
                pass
            return

        size = os.path.getsize(full)
        if size > 200 * 1024:
            self.wb_preview.setPlainText(f'文件超过 200KB 限制（{size // 1024}KB），拒绝读取。')
            return
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            content = f'读取失败: {e}'
        self.wb_preview.setPlainText(content)

    def _wb_on_plan_confirm_changed(self, state):
        self._wb_plan_confirm = bool(state)
        if self._workspace_session:
            self._workspace_session['plan_confirm'] = self._wb_plan_confirm
            save_workspace(self._workspace_session)

    # ── 空间（工作台任务）列表树 ───────────────────────────────────────

    def _wb_refresh_space_tree(self, select_ws_id: str = '', select_conv_id: str = ''):
        """刷新左栏空间树：顶层=工作台，子级=该空间下的对话记录。"""
        self.wb_space_tree.blockSignals(True)
        self.wb_space_tree.clear()
        for meta in list_workspaces():
            ws_id = str(meta.get('id') or '')
            ws_dir = str(meta.get('workspace_dir') or '')
            cur = (' 📁' if ws_dir else '')
            top = QTreeWidgetItem([f"{meta.get('title') or '空间'}{cur}"])
            top.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'space', 'ws_id': ws_id})
            top.setToolTip(0, ws_dir or '（未绑定工作文件夹）')
            # 展开/收起标签样式由 QSS 处理；子级对话
            s = load_workspace(ws_id) or {}
            convs = s.get('conversations') or []
            active_conv = s.get('active_conv_id')
            for i, conv in enumerate(convs):
                cid = str(conv.get('id') or f'c{i}')
                active_mark = '● ' if cid == active_conv else ''
                ctitle = str(conv.get('title') or f'对话 {i + 1}')
                child = QTreeWidgetItem([active_mark + ctitle])
                child.setData(0, Qt.ItemDataRole.UserRole,
                              {'kind': 'conversation', 'ws_id': ws_id, 'conv_id': cid})
                child.setToolTip(0, f'{len(conv.get("messages") or [])} 条消息')
                top.addChild(child)
            self.wb_space_tree.addTopLevelItem(top)
            if ws_id == select_ws_id:
                top.setExpanded(True)
                if select_conv_id:
                    for k in range(top.childCount()):
                        child = top.child(k)
                        if child.data(0, Qt.ItemDataRole.UserRole).get('conv_id') == select_conv_id:
                            self.wb_space_tree.setCurrentItem(child)
                            break
                else:
                    self.wb_space_tree.setCurrentItem(top)
        self.wb_space_tree.blockSignals(False)

    def _wb_on_space_clicked(self, item: QTreeWidgetItem, _column: int):
        """点击空间/对话节点：加载到中栏并设为 active。"""
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = meta.get('kind')
        if kind == 'space':
            ws = load_workspace(meta.get('ws_id') or '')
            if not ws:
                return
            self._workspace_session = ws
            self._wb_plan_confirm = bool(ws.get('plan_confirm', False))
            self.wb_plan_check.setChecked(self._wb_plan_confirm)
            self.wb_dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
            if ws.get('workspace_dir'):
                self._wb_refresh_tree(ws['workspace_dir'])
            else:
                self.wb_tree.clear()
            # 显示 active 对话
            conv = self._active_conversation_of(ws)
            self._wb_render_conversation(ws, conv)
        elif kind == 'conversation':
            ws_id = meta.get('ws_id')
            conv_id = meta.get('conv_id')
            ws = load_workspace(ws_id)
            if not ws:
                return
            if ws.get('active_conv_id') != conv_id:
                ws = set_active_conversation(ws_id, conv_id) or ws
            self._workspace_session = ws
            self._wb_plan_confirm = bool(ws.get('plan_confirm', False))
            self.wb_plan_check.setChecked(self._wb_plan_confirm)
            self.wb_dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
            if ws.get('workspace_dir'):
                self._wb_refresh_tree(ws['workspace_dir'])
            else:
                self.wb_tree.clear()
            conv = self._active_conversation_of(ws)
            self._wb_render_conversation(ws, conv)

    def _active_conversation_of(self, ws: dict) -> dict | None:
        convs = ws.get('conversations') or []
        active = ws.get('active_conv_id')
        if active:
            for c in convs:
                if c.get('id') == active:
                    return c
        return convs[0] if convs else None

    def _wb_render_conversation(self, ws: dict, conv: dict | None):
        """把某对话的消息渲染到中栏。"""
        # 清空中栏
        while self.wb_thread_layout.count() > 1:
            child = self.wb_thread_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if not conv:
            self.wb_input.clear()
            return
        for msg in conv.get('messages') or []:
            role = str(msg.get('role') or '')
            content = str(msg.get('content') or '')
            if role in ('user', 'assistant') and content:
                self._wb_append_message(role, content)

    def _wb_new_conversation(self):
        """在当前空间下新建一个对话。"""
        if not self._workspace_session:
            self._wb_new_workspace()
            return
        if self._wb_agent_worker is not None:
            show_warning(self, self._title(), '当前任务运行中，请先停止。')
            return
        title, ok = QInputDialog.getText(self, '新建对话', '对话名称：')
        if not ok or not title.strip():
            return
        ws, conv_id = create_conversation(self._workspace_session.get('id'), title.strip())
        if not ws:
            return
        self._workspace_session = ws
        self._wb_tool_calls = []
        self._wb_render_conversation(ws, self._active_conversation_of(ws))
        self._wb_refresh_space_tree(select_ws_id=ws.get('id'), select_conv_id=conv_id)

    def _wb_new_workspace(self):
        """新建工作台（空间）。"""
        title, ok = QInputDialog.getText(self, '新建工作台', '工作台名称：')
        if not ok or not title.strip():
            return
        ws = empty_workspace(title=title.strip())
        save_workspace(ws)
        self._workspace_session = ws
        self._wb_tool_calls = []
        self._wb_plan_confirm = ws.get('plan_confirm', False)
        self.wb_plan_check.setChecked(self._wb_plan_confirm)
        self.wb_dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
        if ws.get('workspace_dir'):
            self._wb_refresh_tree(ws['workspace_dir'])
        else:
            self.wb_tree.clear()
        # 渲染默认对话（空）
        self._wb_render_conversation(ws, self._active_conversation_of(ws))
        self._wb_append_message('assistant',
            f'工作台「{title}」已就绪。请先绑定工作文件夹，然后描述你的任务。')
        self._wb_refresh_space_tree(select_ws_id=ws.get('id'))

    def _wb_send(self):
        """工作台发送：启动 ReAct 循环。"""
        if self._wb_agent_worker is not None:
            return
        text = self.wb_input.toPlainText().strip()
        if not text:
            return
        self.wb_input.clear()

        if not self._workspace_session:
            self._wb_new_workspace()

        ws_dir = self._workspace_session.get('workspace_dir') or ''
        if not ws_dir:
            self._wb_append_message('assistant', '请先绑定工作文件夹，再发起任务。')
            return

        # 当前 active 对话（发送消息归属）
        conv = self._active_conversation_of(self._workspace_session)
        if conv is None:
            self._wb_append_message('assistant', '请先新建对话。')
            return
        messages = list(conv.get('messages') or [])
        tool_calls = list(conv.get('tool_calls') or [])
        # 追加用户消息到 active 对话
        messages.append({'id': 'wb-user-' + str(hash(text)), 'role': 'user', 'content': text})
        conv['messages'] = messages
        conv['tool_calls'] = tool_calls
        save_workspace(self._workspace_session)
        self._wb_render_conversation(self._workspace_session, conv)

        # 显示用户消息气泡
        self._wb_append_message('user', text)

        # 读取当前模型配置
        idx = self.model_combo.currentIndex()
        model_cfg = self.model_combo.currentData() or {}
        if not model_cfg.get('enabled') or not model_cfg.get('base_url'):
            self._wb_append_message('assistant', '当前模型未启用或未配置 Base URL，请在设置中检查。')
            return

        self.wb_send_btn.setEnabled(False)
        self.wb_stop_btn.setEnabled(True)

        # 跨线程桥：confirm/progress 从子线程转发到主线程，避免 PyQt6 闪退
        if self._wb_bridge is None:
            self._wb_bridge = _WorkbenchBridge(self)
        confirm_cb = self._wb_bridge.confirm
        progress_cb = self._wb_bridge.progress

        self._wb_agent_worker = _WorkbenchWorker(
            user_message=text,
            workspace_dir=ws_dir,
            model_cfg=model_cfg,
            messages=messages,
            tool_calls=tool_calls,
            plan_confirm=self._wb_plan_confirm,
            confirm_cb=confirm_cb,
            progress_cb=progress_cb,
        )
        self._wb_agent_worker.finished.connect(self._wb_on_agent_done)
        self._wb_agent_worker.failed.connect(self._wb_on_agent_failed)
        self._wb_agent_worker.start()

    def _wb_stop(self):
        if self._wb_agent_worker:
            self._wb_agent_worker.stop()
            self.wb_stop_btn.setEnabled(False)

    def _wb_on_agent_done(self, final_answer: str, messages: list, tool_calls: list):
        self._wb_agent_worker = None
        self.wb_send_btn.setEnabled(True)
        self.wb_stop_btn.setEnabled(False)

        # 保存结果到 active 对话
        if self._workspace_session:
            conv = self._active_conversation_of(self._workspace_session)
            if conv:
                # run_agent_loop 入参 messages 已含本轮全部消息，直接回写
                if messages:
                    conv['messages'] = list(messages)
                if tool_calls:
                    conv['tool_calls'] = list(tool_calls)
                save_workspace(self._workspace_session)
                self._wb_refresh_space_tree(select_ws_id=self._workspace_session.get('id'),
                                            select_conv_id=conv.get('id'))

        # 显示最终答案（若非空且与最后一条 assistant 不重复）
        if final_answer:
            self._wb_append_message('assistant', final_answer)

    def _wb_on_agent_failed(self, error_text: str):
        """工作台 Agent 子线程异常兜底：在主线程复位按钮并提示，绝不闪退。"""
        self._wb_agent_worker = None
        self.wb_send_btn.setEnabled(True)
        self.wb_stop_btn.setEnabled(False)
        zh = self.language == 'zh'
        self._wb_append_message('assistant',
            (f'⚠️ 工作台任务异常：{error_text}' if zh
             else f'⚠️ Workbench task error: {error_text}'))
        show_error(self, self._title(),
                   '工作台任务执行出错' if zh else 'Workbench task error',
                   error_text)

    def _wb_append_message(self, role: str, content: str):
        """在工作台中栏追加一条消息气泡，滚动到底。"""
        bubble = QFrame()
        bubble.setObjectName('detail-summary-card' if role == 'assistant' else 'page-filter-bar')
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(8, 6, 8, 6)
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        prefix = '🤖 ' if role == 'assistant' else '👤 '
        lbl.setText(prefix + content)
        bl.addWidget(lbl)
        # 去掉旧的 stretch
        stretch = self.wb_thread_layout.takeAt(self.wb_thread_layout.count() - 1)
        del stretch
        self.wb_thread_layout.addWidget(bubble)
        self.wb_thread_layout.addStretch(1)
        # 滚动到底
        QApplication.processEvents()
        self.wb_scroll.verticalScrollBar().setValue(
            self.wb_scroll.verticalScrollBar().maximum()
        )

    def _maybe_show_banner(self):
        dismissed = bool(load_settings().get('model_chat_banner_dismissed'))
        self.banner.setVisible(not dismissed)

    def _dismiss_banner(self):
        settings = load_settings()
        settings['model_chat_banner_dismissed'] = True
        save_settings(settings)
        self.banner.hide()

    def _reload_models(self, select_id: str = ''):
        settings = load_settings()
        wanted = select_id or str(settings.get('model_chat_last_model_id') or '')
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for item in list_enabled_items():
            label = f"{item.get('name') or ''} · {item.get('model') or ''}"
            self.model_combo.addItem(label.strip(' ·'), item)
            if wanted and item.get('id') == wanted:
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
        self.model_combo.blockSignals(False)
        self._sync_send_enabled()

    def _on_model_changed(self, _index=0):
        if self._worker is not None and self._worker.isRunning():
            self._stop()
        self._sync_send_enabled()

    def _reload_connections(self):
        from tools.db_connect import load_connections
        zh = self.language == 'zh'
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        for item in load_connections():
            label = str(item.get('name') or item.get('id') or '')
            if item.get('dialect'):
                label = f"{label} ({item.get('dialect')})"
            self.conn_combo.addItem(label, item)
        if self.conn_combo.count() == 0:
            self.conn_combo.addItem(
                '未配置数据库连接' if zh else 'No DB connection configured', None
            )
        self.conn_combo.blockSignals(False)

    def _current_connection(self) -> dict | None:
        # 优先从 SQL 控制台当前活跃的数据库面板获取连接上下文
        window = self.window()
        if window and hasattr(window, '_active_db_context'):
            ctx = window._active_db_context()
            if ctx:
                return ctx
        # fallback: 自身下拉框
        data = self.conn_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _current_model(self) -> dict | None:
        data = self.model_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _sync_send_enabled(self):
        ready = self._current_model() is not None and self._session is not None
        self.send_btn.setEnabled(ready and (self._worker is None or not self._worker.isRunning()))
        if self._current_model() is None:
            zh = self.language == 'zh'
            self.ping_status.setText(
                '没有启用的模型配置，请到设置中新增并启用' if zh else
                'No enabled model. Add one in Settings.'
            )

    def _reload_sessions(self, _text=''):
        current_id = str((self._session or {}).get('id') or '')
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for row in search_sessions(self.search.text()):
            item = QListWidgetItem(str(row.get('title') or '新对话'))
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.session_list.addItem(item)
            if row.get('id') == current_id:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        if self.session_list.count() == 0:
            self._session = None
            self._render_messages()
        elif self.session_list.currentItem() is None:
            self.session_list.setCurrentRow(0)

    def _new_session(self):
        model = self._current_model() or {}
        self._session = create_session(model_config_id=str(model.get('id') or ''), model=str(model.get('model') or ''))
        self._reload_sessions()
        self._maybe_show_banner()

    def _rename_session(self):
        if not self._session:
            return
        zh = self.language == 'zh'
        text, ok = QInputDialog.getText(self, self._title(), '新标题' if zh else 'Title', text=str(self._session.get('title') or ''))
        if not ok:
            return
        self._session = rename_session(self._session.get('id'), text)
        self._reload_sessions()

    def _delete_session(self):
        if not self._session:
            return
        zh = self.language == 'zh'
        if not confirm_action(self, self._title(), '删除该会话？明文记录将从本机移除。' if zh else 'Delete this chat?', confirm_text='删除' if zh else 'Delete', danger=True):
            return
        delete_session(self._session.get('id'))
        self._session = None
        self._reload_sessions()

    def _on_session_changed(self, current, _previous=None):
        if current is None:
            return
        meta = current.data(Qt.ItemDataRole.UserRole) or {}
        self._session = load_session(str(meta.get('id') or ''))
        self._render_messages()
        self._sync_send_enabled()
        if self._session:
            settings = load_settings()
            settings['model_chat_last_session_id'] = self._session.get('id')
            save_settings(settings)

    def _clear_thread(self):
        while self.thread_layout.count() > 1:
            item = self.thread_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_messages(self):
        self._clear_thread()
        messages = list((self._session or {}).get('messages') or [])
        self.empty.setVisible(not messages)
        self.scroll.setVisible(bool(messages))
        zh = self.language == 'zh'
        for msg in messages:
            frame = QFrame()
            role = msg.get('role')
            frame.setObjectName('chat-bubble-user' if role == 'user' else 'chat-bubble-assistant')
            box = QVBoxLayout(frame)
            box.setContentsMargins(10, 8, 10, 8)
            meta = QLabel()
            meta.setObjectName('field-hint')
            if role == 'assistant':
                name = str(msg.get('config_name') or '')
                if not name and msg.get('model_config_id'):
                    from tools.intranet_llm import get_item_by_id
                    cfg = get_item_by_id(msg.get('model_config_id'))
                    name = str((cfg or {}).get('name') or '') or ('已删除配置' if zh else 'Deleted config')
                elif not name:
                    name = '已删除配置' if zh else 'Deleted config'
                meta.setText(f"{name} · {msg.get('model') or ''} · {msg.get('created_at') or ''}")
            else:
                meta.setText(str(msg.get('created_at') or ''))
            body = QLabel(str(msg.get('content') or ''))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if msg.get('status') == 'stopped':
                body.setText(body.text() + ('\n（已停止/未完成）' if zh else '\n(stopped)'))
            box.addWidget(meta)
            box.addWidget(body)
            copy = QPushButton('复制' if zh else 'Copy')
            apply_button(copy, 'ghost', compact=True)
            copy.clicked.connect(lambda _=False, text=str(msg.get('content') or ''): self._copy(text))
            box.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft if role != 'user' else Qt.AlignmentFlag.AlignRight)
            wrap = QHBoxLayout()
            if role == 'user':
                wrap.addStretch(1)
                wrap.addWidget(frame, 6)
            else:
                wrap.addWidget(frame, 6)
                wrap.addStretch(1)
            holder = QWidget()
            holder.setLayout(wrap)
            self.thread_layout.insertWidget(self.thread_layout.count() - 1, holder)

    def _copy(self, text: str):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _send(self):
        text = self.input.toPlainText().strip()
        model = self._current_model()
        zh = self.language == 'zh'
        if not text:
            return
        if self._worker is not None and self._worker.isRunning():
            show_warning(self, self._title(), '请先停止当前生成' if zh else 'Stop the current reply first')
            return
        if model is None:
            show_warning(self, self._title(), '请选择已启用的模型配置' if zh else 'Pick an enabled model')
            return
        intent = detect_take_data_intent(text)
        if intent == 'sql':
            self._handle_sql_intent(text, model)
            return
        if intent == 'linux':
            self._handle_linux_intent(text, model)
            return
        # 非取数意图：走原通用聊天链路
        self._send_chat(text, model)

    def _send_chat(self, text: str, model: dict):
        zh = self.language == 'zh'
        if self._session is None:
            self._session = create_session(model_config_id=str(model.get('id') or ''), model=str(model.get('model') or ''))
        self.input.clear()
        self._session = append_message(
            self._session.get('id'), 'user', text,
            model_config_id=model.get('id'), model=model.get('model'), config_name=model.get('name'),
        )
        pending = append_message(
            self._session.get('id'), 'assistant', '',
            model_config_id=model.get('id'), model=model.get('model'),
            config_name=model.get('name'), status='pending',
        )
        self._session = pending
        self._pending_id = str((pending.get('messages') or [{}])[-1].get('id') or '')
        payload, trimmed = trim_messages_for_request(self._session.get('messages') or [])
        self.trim_hint.setVisible(trimmed)
        self.trim_hint.setText(
            '早期消息已为上下文长度裁剪，本地完整记录未删除' if zh else
            'Older messages trimmed for context; local history is kept.'
        )
        self._render_messages()
        self._reload_sessions()
        self._start_worker(payload, model)

    def _post_plain_reply(self, text: str, model: dict):
        """把一段直接回显的助手消息（不调模型）写入当前会话。"""
        if self._session is None:
            self._session = create_session(
                model_config_id=str(model.get('id') or ''), model=str(model.get('model') or '')
            )
        self._session = append_message(
            self._session.get('id'), 'user', self._last_question,
            model_config_id=model.get('id'), model=model.get('model'), config_name=model.get('name'),
        )
        self._session = append_message(
            self._session.get('id'), 'assistant', text,
            model_config_id=model.get('id'), model=model.get('model'),
            config_name=model.get('name'), status='complete',
        )
        self._render_messages()
        self._reload_sessions()
        self._sync_send_enabled()

    def _handle_sql_intent(self, text: str, model: dict):
        from tools.db_connect import load_connections
        from tools.schema_snapshot import load_snapshot
        from tools.tameng_agent import prepare_request

        self._last_question = text
        conn = self._current_connection()
        if conn is None:
            self._post_plain_reply('请先选择连接并扫描结构。' if self.language == 'zh' else 'Please select a connection and scan its schema first.', model)
            return
        snap = load_snapshot(str(conn.get('id') or ''))
        if snap is None:
            self._post_plain_reply('该连接尚未扫描结构，正在扫描…', model)
            self._start_scan(conn)
            return
        from tools.tameng_agent import snapshot_gate
        gate = snapshot_gate(conn, snap)
        if gate.get('state') == 'SNAPSHOT_STALE':
            stale_note = '结构可能已变化，建议到数据中心重新扫描。' if self.language == 'zh' else 'Schema may have changed; consider re-scanning.'
        else:
            stale_note = ''
        prepared = prepare_request(text, snap, conn)
        state = str(prepared.get('state') or '')
        if state == 'READY' and prepared.get('ok'):
            if stale_note:
                self._post_plain_reply(stale_note, model)
            self._run_sql_chain(text, model, conn, snap, prepared, stale_note)
            return
        if state == 'NEEDS_SELECTION':
            self._render_candidates(text, model, prepared)
            return
        reason = str(prepared.get('reason') or '')
        next_action = str(prepared.get('next_action') or '')
        msg = reason
        if next_action:
            msg = f'{reason} {next_action}'
        if stale_note:
            msg = f'{stale_note}\n{msg}'
        self._post_plain_reply(msg, model)

    def _run_sql_chain(self, text: str, model: dict, conn: dict, snap: dict, prepared: dict, stale_note: str = ''):
        from tools.ai_sql_draft import generate_sql_draft
        from tools.tameng_agent import format_evidence_bar, validate_generated_sql

        evidence = prepared.get('evidence')
        dialect = str((conn or {}).get('dialect') or 'oracle')
        alias = str((conn or {}).get('name') or '')
        cfg = dict(model)
        cfg['enabled'] = True
        try:
            draft = generate_sql_draft(
                text,
                action='generate',
                dialect=dialect,
                alias=alias,
                snapshot=None,
                evidence=evidence,
                cfg=cfg,
            )
        except Exception as exc:
            self._post_plain_reply(redact_error(str(exc)), model)
            return
        sql = str(draft.get('sql') or '').strip()
        if not sql:
            reason = str(draft.get('summary') or '') or '模型未返回 SQL。'
            warnings = '；'.join(str(item) for item in (draft.get('warnings') or []) if str(item))
            msg = reason + (f'（{warnings}）' if warnings else '')
            self._post_plain_reply(msg, model)
            return
        checked = validate_generated_sql(sql, evidence, dialect)
        if not checked.get('allowed'):
            parts = ['草案被拦截：' + str(checked.get('reason') or '未通过校验')]
            unknown_fields = [str(item) for item in (checked.get('unknown_fields') or [])]
            if unknown_fields:
                parts.append('未知字段：' + ', '.join(unknown_fields))
            self._post_plain_reply('\n'.join(parts), model)
            return
        evidence_bar = format_evidence_bar(evidence)
        lines = [sql]
        if evidence_bar:
            lines.append('字段证据：' + evidence_bar)
        lines.append('草案 · 未执行')
        if stale_note:
            lines.insert(0, stale_note)
        self._post_plain_reply('\n'.join(lines), model)

    def _render_candidates(self, text: str, model: dict, prepared: dict):
        from tools.ai_object_context import field_qualified

        self._candidate_context = (text, model, prepared)
        fields = list((prepared.get('resolution') or {}).get('fields') or [])
        lines = ['找到多个候选字段，请选择：']
        for item in fields:
            obj = item.get('object') or {}
            col = item.get('column') or {}
            qn = field_qualified(obj, col) if isinstance(obj, dict) and isinstance(col, dict) else str(col.get('name') or '')
            dtype = str(col.get('data_type') or '') if isinstance(col, dict) else ''
            comment = str(col.get('comment') or '') if isinstance(col, dict) else ''
            parts = [qn]
            if dtype:
                parts.append(dtype)
            if comment:
                parts.append(comment)
            lines.append(' · '.join(part for part in parts if part))
        self._post_plain_reply('\n'.join(lines), model)
        self._render_candidate_buttons(fields)

    def _render_candidate_buttons(self, fields):
        from tools.ai_object_context import field_qualified
        # 在消息线程末尾插入候选按钮行
        holder = QWidget()
        box = QHBoxLayout(holder)
        box.setContentsMargins(8, 4, 8, 4)
        for item in fields:
            obj = item.get('object') or {}
            col = item.get('column') or {}
            qn = field_qualified(obj, col) if isinstance(obj, dict) and isinstance(col, dict) else str(col.get('name') or '')
            btn = QPushButton(qn)
            apply_button(btn, 'secondary', compact=True)
            btn.clicked.connect(lambda _=False, q=qn: self._confirm_candidate(q))
            box.addWidget(btn)
        box.addStretch(1)
        self.thread_layout.insertWidget(self.thread_layout.count() - 1, holder)

    def _confirm_candidate(self, qn: str):
        if not getattr(self, '_candidate_context', None):
            return
        text, model, prepared = self._candidate_context
        self._candidate_context = None
        conn = self._current_connection()
        if conn is None:
            self._post_plain_reply('请先选择连接并扫描结构。' if self.language == 'zh' else 'Please select a connection and scan its schema first.', model)
            return
        from tools.schema_snapshot import load_snapshot
        from tools.tameng_agent import prepare_request
        snap = load_snapshot(str(conn.get('id') or ''))
        if snap is None:
            self._post_plain_reply('该连接尚未扫描结构，正在扫描…', model)
            self._start_scan(conn)
            return
        prepared2 = prepare_request(text, snap, conn, confirmed=[qn])
        if prepared2.get('ok') and prepared2.get('state') == 'READY':
            self._run_sql_chain(text, model, conn, snap, prepared2)
        else:
            self._post_plain_reply(str(prepared2.get('reason') or '仍无法确认字段。'), model)

    def _start_scan(self, conn: dict):
        self._scan_worker = _ScanWorker(conn)
        self._scan_worker.completed.connect(self._on_scan_ok)
        self._scan_worker.failed.connect(self._on_scan_fail)
        self._scan_worker.start()

    def _on_scan_ok(self, _payload):
        self._reload_connections()
        if getattr(self, '_last_question', ''):
            text = self._last_question
            model = self._current_model()
            if model is not None:
                self._handle_sql_intent(text, model)

    def _on_scan_fail(self, message: str):
        model = self._current_model()
        if model is not None:
            self._post_plain_reply(
                '扫描失败：' + redact_error(message) + ('；请先到数据中心确认连接可用' if self.language == 'zh' else '; verify the connection in Data Center first'),
                model,
            )

    def _handle_linux_intent(self, text: str, model: dict):
        from tools.intranet_llm import chat_completions
        from tools.linux_guard import inspect_commands

        self._last_question = text
        cfg = dict(model)
        cfg['enabled'] = True
        system = (
            '你是内网 Linux 运维助手。只把用户的自然语言需求转换成若干条只读查看命令'
            '（如 tail、grep、ls、cat、df、free、ps、uptime、hostname 等），'
            '每行一条，不要写解释，不要写 rm/kill/sudo/重定向/管道到解释器等危险命令。'
        )
        try:
            reply = chat_completions(
                [{'role': 'system', 'content': system}, {'role': 'user', 'content': text}],
                cfg=cfg,
            )
        except Exception as exc:
            self._post_plain_reply(redact_error(str(exc)), model)
            return
        command_list = [line.strip() for line in str(reply or '').splitlines() if line.strip()]
        allowed, rejected = inspect_commands(command_list)
        lines = []
        if allowed:
            lines.append('只读命令草案（未执行）：')
            lines.extend('  ' + cmd for cmd in allowed)
        for cmd, reason in rejected:
            lines.append(f'已拦截：{cmd} —— {reason}')
        if not allowed and not rejected:
            lines.append('未生成可识别的只读命令。')
        self._post_plain_reply('\n'.join(lines), model)

    def _start_worker(self, messages, model):
        cfg = dict(model)
        cfg['enabled'] = True
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._worker = _ChatWorker(messages, cfg)
        self._worker.completed.connect(self._on_chat_ok)
        self._worker.failed.connect(self._on_chat_fail)
        self._worker.finished.connect(self._on_chat_done)
        self._worker.start()
        settings = load_settings()
        settings['model_chat_last_model_id'] = str(model.get('id') or '')
        save_settings(settings)

    def _on_chat_ok(self, text: str):
        if self._session and self._pending_id:
            self._session = update_message(self._session.get('id'), self._pending_id, content=text, status='complete')
            self._render_messages()

    def _on_chat_fail(self, message: str):
        if self._session and self._pending_id:
            self._session = update_message(
                self._session.get('id'), self._pending_id,
                content=redact_error(message), status='stopped',
            )
            self._render_messages()
        show_error(self, self._title(), redact_error(message))

    def _on_chat_done(self):
        self.stop_btn.setEnabled(False)
        self._sync_send_enabled()

    def _stop(self):
        zh = self.language == 'zh'
        if self._worker is not None:
            self._worker.cancelled = True
        if self._session and self._pending_id:
            current = ''
            for msg in self._session.get('messages') or []:
                if msg.get('id') == self._pending_id:
                    current = str(msg.get('content') or '')
            self._session = update_message(
                self._session.get('id'), self._pending_id,
                content=current, status='stopped',
            )
            self._render_messages()
        self.ping_status.setText(
            '请求已在后台结束或等待超时' if zh else
            'The request was detached; it may finish in the background or time out.'
        )
        self.stop_btn.setEnabled(False)
        self._sync_send_enabled()

    def _regenerate(self):
        if not self._session:
            return
        messages = list(self._session.get('messages') or [])
        last_user = ''
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                last_user = str(msg.get('content') or '')
                break
        if not last_user:
            return
        model = self._current_model()
        if model is None:
            return
        pending = append_message(
            self._session.get('id'), 'assistant', '',
            model_config_id=model.get('id'), model=model.get('model'),
            config_name=model.get('name'), status='pending',
        )
        self._session = pending
        self._pending_id = str((pending.get('messages') or [{}])[-1].get('id') or '')
        payload, trimmed = trim_messages_for_request(self._session.get('messages') or [])
        self.trim_hint.setVisible(trimmed)
        self._render_messages()
        self._start_worker(payload, model)

    def _ping_current(self):
        model = self._current_model()
        zh = self.language == 'zh'
        if model is None:
            show_warning(self, self._title(), '请先选择模型' if zh else 'Pick a model')
            return
        cfg = dict(model)
        cfg['enabled'] = True
        self.ping_btn.setEnabled(False)
        self.ping_status.setText('正在测试…' if zh else 'Testing…')
        self._ping_worker = _PingWorker(cfg)
        self._ping_worker.completed.connect(self._on_ping)
        self._ping_worker.finished.connect(lambda: self.ping_btn.setEnabled(True))
        self._ping_worker.start()

    def _open_skill_manager(self):
        dialog = _SkillManagerDialog(self, language=self.language)
        dialog.exec()

    def _on_ping(self, payload: dict):
        zh = self.language == 'zh'
        data = payload if isinstance(payload, dict) else {}
        name = data.get('name') or ''
        model = data.get('model') or ''
        elapsed = data.get('elapsed_ms')
        if data.get('ok'):
            self.ping_status.setText(
                f'成功 · {name} · {model} · {elapsed} ms' if zh else
                f'OK · {name} · {model} · {elapsed} ms'
            )
        else:
            self.ping_status.setText(
                f'失败 · {elapsed} ms · {data.get("error") or ""}' if zh else
                f'Failed · {elapsed} ms · {data.get("error") or ""}'
            )


class _SkillManagerDialog(QDialog):
    """skill 管理：内置 + 用户自定义任务的列表 / 新增 / 删除 / 启停 / 编辑。"""

    def __init__(self, parent=None, language='zh'):
        super().__init__(parent)
        self.language = language
        self.zh = language == 'zh'
        self.setWindowTitle('skill 管理' if self.zh else 'Skills')
        self.resize(560, 420)
        self._setup_ui()
        self._reload()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        root.addWidget(self.list_widget, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton('新增' if self.zh else 'Add')
        apply_button(self.add_btn, 'secondary', compact=True)
        self.add_btn.clicked.connect(self._add)
        self.edit_btn = QPushButton('编辑' if self.zh else 'Edit')
        apply_button(self.edit_btn, 'ghost', compact=True)
        self.edit_btn.clicked.connect(self._edit)
        self.toggle_btn = QPushButton('停用' if self.zh else 'Disable')
        apply_button(self.toggle_btn, 'ghost', compact=True)
        self.toggle_btn.clicked.connect(self._toggle)
        self.delete_btn = QPushButton('删除' if self.zh else 'Delete')
        apply_button(self.delete_btn, 'ghost', compact=True)
        self.delete_btn.clicked.connect(self._delete)
        self.close_btn = QPushButton('关闭' if self.zh else 'Close')
        apply_button(self.close_btn, 'secondary', compact=True)
        self.close_btn.clicked.connect(self.accept)
        btns.addWidget(self.add_btn)
        btns.addWidget(self.edit_btn)
        btns.addWidget(self.toggle_btn)
        btns.addWidget(self.delete_btn)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)
        root.addLayout(btns)

        self._on_selection_changed()

    def _current_task(self) -> dict | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _reload(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        tasks = harness_project.list_tasks()
        for task in tasks:
            label = self._format_task(task)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, task)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._on_selection_changed()

    def _format_task(self, task: dict) -> str:
        source = '内置' if task.get('builtin') else '用户'
        state = '启用' if task.get('enabled', True) else '停用'
        title = task.get('title') or task.get('task') or ''
        desc = task.get('desc') or ''
        parts = [f'{title}（{task.get("task")}）', source, state]
        if desc:
            parts.append(desc)
        return ' · '.join(parts)

    def _on_selection_changed(self, *_args):
        task = self._current_task()
        has = task is not None
        self.edit_btn.setEnabled(has)
        self.toggle_btn.setEnabled(has)
        self.delete_btn.setEnabled(has and not task.get('builtin', False))
        if has:
            self.toggle_btn.setText(
                '停用' if (task.get('enabled', True) and self.zh) else
                '启用' if self.zh else
                'Disable' if task.get('enabled', True) else 'Enable'
            )

    def _add(self):
        zh = self.zh
        path, _filter = QFileDialog.getOpenFileName(
            self, '选择 .md skill 文件' if zh else 'Pick .md skill file',
            '', 'Markdown (*.md)',
        )
        if not path:
            return
        task_name, ok = QInputDialog.getText(
            self, 'task 名' if zh else 'Task name',
            'task 名（如 mongo.query）：' if zh else 'Task name (e.g. mongo.query):',
        )
        if not ok or not str(task_name).strip():
            return
        task_name = str(task_name).strip()
        title, ok = QInputDialog.getText(
            self, '标题' if zh else 'Title',
            '标题：' if zh else 'Title:',
            text=task_name,
        )
        if not ok:
            return
        desc, ok = QInputDialog.getText(
            self, '描述' if zh else 'Description',
            '描述：' if zh else 'Description:',
        )
        if not ok:
            return
        existing = {t.get('task') for t in harness_project.list_tasks()}
        if task_name in existing:
            show_error(self, 'skill 管理' if zh else 'Skills', 'task 已存在：' + task_name)
            return
        try:
            harness_project.install_skill(path)
            harness_project.add_task(task_name, _basename(path), str(title).strip(), str(desc).strip())
        except ValueError as exc:
            show_error(self, 'skill 管理' if zh else 'Skills', str(exc))
            return
        self._reload()

    def _edit(self):
        task = self._current_task()
        if task is None:
            return
        zh = self.zh
        title, ok = QInputDialog.getText(
            self, '标题' if zh else 'Title',
            '标题：' if zh else 'Title:',
            text=str(task.get('title') or ''),
        )
        if not ok:
            return
        desc, ok = QInputDialog.getText(
            self, '描述' if zh else 'Description',
            '描述：' if zh else 'Description:',
            text=str(task.get('desc') or ''),
        )
        if not ok:
            return
        try:
            harness_project.update_task(task.get('task'), title=str(title).strip(), desc=str(desc).strip())
        except ValueError as exc:
            show_error(self, 'skill 管理' if zh else 'Skills', str(exc))
            return
        self._reload()

    def _toggle(self):
        task = self._current_task()
        if task is None:
            return
        enabled = not bool(task.get('enabled', True))
        try:
            harness_project.update_task(task.get('task'), enabled=enabled)
        except ValueError as exc:
            show_error(self, 'skill 管理' if zh else 'Skills', str(exc))
            return
        self._reload()

    def _delete(self):
        task = self._current_task()
        if task is None or task.get('builtin', False):
            return
        zh = self.zh
        if not confirm_action(
            self,
            '删除 skill' if zh else 'Delete skill',
            f'确定删除用户 skill「{task.get("title") or task.get("task")}」？'
            if zh else f'Delete user skill "{task.get("title") or task.get("task")}"?',
            confirm_text='确认删除' if zh else 'Delete',
            danger=True,
        ):
            return
        try:
            harness_project.remove_task(task.get('task'), delete_file=True)
        except ValueError as exc:
            show_error(self, 'skill 管理' if zh else 'Skills', str(exc))
            return
        self._reload()


def _basename(path: str) -> str:
    import os
    return os.path.basename(str(path or ''))
