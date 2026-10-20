# -*- coding: utf-8 -*-
"""Agent 工作台：独立面板，绑定项目目录后运行受控 ReAct 任务。

从 ModelChatPanel 的工作台模式完整提取（v3.0 导航重构），
不再依赖聊天上下文，作为"模型 → 工作"子菜单的独立页面。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections import deque

from PyQt6.QtCore import (
    QEvent, QObject, QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QKeyEvent, QTextCursor, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from config import load_settings
from tools.agent_store import (
    create_conversation,
    commit_conversation_result,
    delete_conversation,
    delete_workspace,
    empty_workspace,
    list_workspaces,
    load_workspace,
    rename_conversation,
    save_workspace,
    set_active_conversation,
    update_workspace,
)
from tools.agent_runtime import IGNORED_DIR_NAMES, WHITELIST_EXTENSIONS
from tools.intranet_llm import list_enabled_items
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_warning
from ui.design_system import apply_button
from ui.field_metrics import size_pick_combo
from ui.page_chrome import make_page_header
from ui.splitter_prefs import install_splitter_prefs


_CONTEXT_BUDGET_KEYS = {
    'schema', 'status', 'estimate_kind', 'input_estimate',
    'context_window_known', 'output_reserve', 'messages_kept',
    'messages_dropped', 'reason',
}
_CONTEXT_BUDGET_V2_KEYS = {
    'schema', 'status', 'estimate_kind', 'request_byte_limit',
}
_CONTEXT_BUDGET_MAX_REQUEST_BYTES = 10_000_000
_CONTEXT_BUDGET_REASONS = {
    'trimmed_to_known_input_budget',
    'trimmed_to_application_budget_window_unknown',
    'within_known_input_budget',
    'within_application_budget_window_unknown',
    'memory_compacted', 'memory_omitted',
    'pending_exchange', 'invalid_input', 'private_reasoning_content',
    'incomplete_history', 'estimator_error', 'invalid_estimate',
    'context_anchors_too_large', 'estimator_inconsistent',
}
_CONFLICT_VIEWER_MAX_CHARS = 256 * 1024


_ROLE_PREFIXES = ('👤', '🤖', '🔧')

_STEP_LABELS = {
    'list_dir': ('📂', '列出目录'),
    'read_file': ('📄', '读取'),
    'search_code': ('🔎', '搜索'),
    'write_file': ('✏️', '写入'),
    'edit_file': ('✏️', '修改'),
    'delete_file': ('🗑', '删除'),
    'run_test': ('🧪', '运行测试'),
    'run_svn': ('🔀', 'SVN'),
}


def _strip_role_prefix(content) -> str:
    text = str(content or '').lstrip()
    while text[:1] and text[:1] in ''.join(_ROLE_PREFIXES):
        text = text[1:].lstrip()
    return text


def describe_tool_step(name, arguments) -> str:
    """把一次工具调用描述成一行人能看懂的步骤。"""
    tool = str(name or '').strip()
    args = arguments if isinstance(arguments, dict) else {}
    icon, verb = _STEP_LABELS.get(tool, ('⚙️', tool or '工具'))
    if tool == 'list_dir':
        target = str(args.get('path') or '').strip() or '项目根目录'
    elif tool == 'search_code':
        pattern = str(args.get('pattern') or '').strip()
        scope = str(args.get('file_pattern') or '').strip()
        target = f'“{pattern}”' + (f'（{scope}）' if scope else '')
    elif tool == 'run_test':
        target = str(args.get('args') or '').strip()
    elif tool == 'run_svn':
        target = str(args.get('operation') or '').strip()
    else:
        target = str(args.get('path') or '').strip()
        if tool == 'read_file' and args.get('start_offset'):
            target += f'（从第 {args.get("start_offset")} 个字符起）'
    return f'{icon}  {verb} {target}'.rstrip()


class _WorkbenchBridge(QObject):
    """把 worker 请求交给 GUI 线程；确认通过可取消 Event 返回，进度按批次读取。"""

    confirmRequested = pyqtSignal(object)
    MAX_PROGRESS_EVENTS = 1024
    MAX_PROGRESS_CHARS = 512 * 1024

    def __init__(self, owner: 'AgentWorkbenchPanel'):
        super().__init__(owner)
        self._owner = owner
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._active_request_id = ''
        self._active_request = None
        self._active_dialog = None
        self._progress = deque()
        self._progress_chars = 0
        self.confirmRequested.connect(self._show_confirmation, Qt.ConnectionType.QueuedConnection)

    def confirm(self, task_id: str, title: str, content: str, cancellation) -> bool:
        """Worker side: wait without blocking the GUI event loop; cancellation always denies."""
        if cancellation.is_cancelled():
            return False
        request_id = uuid.uuid4().hex
        request = {
            'id': request_id,
            'task_id': task_id,
            'title': str(title or ''),
            'content': str(content or ''),
            'cancellation': cancellation,
            'event': threading.Event(),
            'accepted': False,
        }
        with self._lock:
            if self._owner._closing or cancellation.is_cancelled():
                return False
            self._pending[request_id] = request
        self.confirmRequested.emit(request)
        while not request['event'].wait(0.05):
            if cancellation.is_cancelled() or self._owner._closing:
                self.resolve(request_id, False)
        return bool(request['accepted'] and not cancellation.is_cancelled())

    @pyqtSlot(object)
    def _show_confirmation(self, request: dict):
        request_id = request.get('id')
        if not self._owner._task_is_current(request.get('task_id')):
            self.resolve(request_id, False)
            return
        self._active_request_id = request_id
        self._active_request = request
        if (request['event'].is_set() or request['cancellation'].is_cancelled()
                or self._owner._closing):
            self._active_request = None
            self._active_request_id = ''
            self.resolve(request_id, False)
            return
        try:
            from ui.confirm_dialog import confirm_action
            # A timer runs inside confirm_action's modal event loop and closes the
            # dialog if stop/close invalidates the waiting request.
            watcher = QTimer(self)
            watcher.setInterval(40)
            watcher.timeout.connect(lambda rid=request_id: self._cancel_dialog_if_needed(rid))
            watcher.start()
            self._close_cancelled_dialog(request_id)
            accepted = confirm_action(
                self._owner,
                request.get('title') or self._owner._title(),
                request.get('content') or '',
                confirm_text='确认并应用' if self._owner.language == 'zh' else 'Confirm and apply',
                danger=True,
            )
            accepted = bool(accepted and not request['cancellation'].is_cancelled()
                            and self._owner._task_is_current(request.get('task_id')))
        except Exception:
            accepted = False
        finally:
            if 'watcher' in locals():
                watcher.stop()
                watcher.deleteLater()
            self._active_dialog = None
            self._active_request = None
            self._active_request_id = ''
        self.resolve(request_id, accepted)

    def _close_cancelled_dialog(self, request_id: str):
        request = self._active_request
        if request is None or request.get('id') != request_id:
            request = self._pending.get(request_id)
        if request is None:
            return
        from PyQt6.QtWidgets import QApplication
        dialog = QApplication.activeModalWidget()
        if dialog is not None and dialog.parentWidget() is self._owner:
            self._active_dialog = dialog
            if request['cancellation'].is_cancelled() or self._owner._closing:
                dialog.reject()

    def _cancel_dialog_if_needed(self, request_id: str):
        request = self._active_request
        if request is None or request.get('id') != request_id:
            request = self._pending.get(request_id)
        if request is None:
            return
        from PyQt6.QtWidgets import QApplication
        dialog = QApplication.activeModalWidget()
        if dialog is not None and dialog.parentWidget() is self._owner:
            self._active_dialog = dialog
        if request['cancellation'].is_cancelled() or self._owner._closing:
            dialog = self._active_dialog
            if dialog is not None:
                dialog.reject()

    def resolve(self, request_id: str, accepted: bool):
        if not request_id:
            return
        with self._lock:
            request = self._pending.pop(request_id, None)
            if request is None:
                return
            request['accepted'] = bool(accepted)
            request['event'].set()

    def cancel_task(self, task_id: str):
        with self._lock:
            request_ids = [
                request_id for request_id, request in self._pending.items()
                if request.get('task_id') == task_id
            ]
        if (self._active_request is not None
                and self._active_request.get('task_id') == task_id):
            active_id = str(self._active_request.get('id') or '')
            if active_id and active_id not in request_ids:
                request_ids.append(active_id)
        for request_id in request_ids:
            self.resolve(request_id, False)
        if self._active_request_id in request_ids and self._active_dialog is not None:
            self._active_dialog.reject()

    def cancel_all(self):
        with self._lock:
            request_ids = list(self._pending)
        if self._active_request is not None:
            active_id = str(self._active_request.get('id') or '')
            if active_id and active_id not in request_ids:
                request_ids.append(active_id)
        for request_id in request_ids:
            self.resolve(request_id, False)
        if self._active_dialog is not None:
            self._active_dialog.reject()

    def progress(self, task_id: str, kind: str, content: str):
        text = str(content or '')
        event = (task_id, str(kind or ''), text)
        with self._lock:
            self._progress.append(event)
            self._progress_chars += len(text)
            while (len(self._progress) > self.MAX_PROGRESS_EVENTS
                   or self._progress_chars > self.MAX_PROGRESS_CHARS):
                _old_task, _old_kind, old_text = self._progress.popleft()
                self._progress_chars -= len(old_text)

    def take_progress(self):
        with self._lock:
            events = list(self._progress)
            self._progress.clear()
            self._progress_chars = 0
        return events


class _WorkbenchWorker(QThread):
    completed = pyqtSignal(str, str, list, list)  # task_id, final_answer, messages, tool_calls
    failed = pyqtSignal(str, str)                 # task_id, error

    def __init__(self, user_message, workspace_dir, model_cfg,
                 messages, tool_calls, plan_confirm, confirm_cb, progress_cb,
                 *, task_id, workspace_id, conversation_id, cancellation,
                 model_adapter=None, memory_binding=None, context_memory=None):
        super().__init__()
        self.task_id = task_id
        self.workspace_id = workspace_id
        self.conversation_id = conversation_id
        self.user_message = user_message
        self.workspace_dir = workspace_dir
        self.model_cfg = model_cfg
        self.messages = messages
        self.tool_calls = tool_calls
        self.plan_confirm = plan_confirm
        self.confirm_cb = confirm_cb
        self.progress_cb = progress_cb
        self.cancellation = cancellation
        self.model_adapter = model_adapter
        self.memory_binding = memory_binding
        self.context_memory = context_memory

    def stop(self):
        self.cancellation.cancel('cancelled')

    def run(self):
        from tools.agent_runtime import run_agent_loop
        try:
            import inspect
            params = inspect.signature(run_agent_loop).parameters
            kwargs = dict(
                user_message=self.user_message,
                workspace_dir=self.workspace_dir,
                model_cfg=self.model_cfg,
                messages=self.messages,
                tool_calls=self.tool_calls,
                plan_confirm=self.plan_confirm,
                confirm_cb=self.confirm_cb,
                progress_cb=lambda kind, content: self.progress_cb(self.task_id, kind, content),
            )
            if 'cancellation' in params:
                kwargs['cancellation'] = self.cancellation
            else:
                kwargs['cancel_cb'] = self.cancellation.is_cancelled
            if self.model_adapter is not None and 'model_adapter' in params:
                kwargs['model_adapter'] = self.model_adapter
            if self.memory_binding is not None and 'memory_binding' in params:
                kwargs['memory_binding'] = self.memory_binding
                kwargs['context_memory'] = self.context_memory
                kwargs['memory_cb'] = lambda value: setattr(self, 'context_memory', value)
            final, msgs, tcs = run_agent_loop(**kwargs)
            self.completed.emit(self.task_id, final, msgs, tcs)
        except Exception as exc:
            # 关键：子线程任何异常都必须阻止进程崩溃，转发到主线程显示
            import traceback
            detail = redact_error(str(exc)) or exc.__class__.__name__
            trace = traceback.format_exc()
            self.failed.emit(self.task_id, f'{detail}\n{trace[-1200:]}')


class AgentWorkbenchPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._workspace_session = None   # 当前 workspace dict
        self._tool_calls = []            # 当前任务累计 tool_calls
        self._agent_worker = None        # _WorkbenchWorker 引用
        self._active_task = None
        self._closing = False
        self._close_pending = False
        self._shutdown_complete = False
        self._progress_timer = None
        self._stream_holder = None
        self._stream_summary = None
        self._stream_body = None
        self._stream_summary_text = ''
        self._stream_body_text = ''
        self._plan_confirm = False       # 始终先确认计划（默认关）
        self._bridge = None              # 跨线程桥（延迟创建）
        self._send_hint_resize_pending = False
        self._file_attachments: list[str] = []
        self._transient_holder = None
        self._transient_indicator = None
        self._context_budget_state = None
        self._conflict_count = 0
        self._conflict_viewer = None
        self._setup_ui()
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(40)
        self._progress_timer.timeout.connect(self._flush_progress)
        self._progress_timer.start()
        self.set_language(language)
        self._reload_models()
        # 恢复最近使用的工作台
        metas = list_workspaces()
        if metas:
            self._select_workspace(str(metas[0].get('id') or ''), select_last_conv=True)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        toolbar = QWidget()
        top = QHBoxLayout(toolbar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        header, self.page_title, self.page_subtitle = make_page_header(
            'Agent 工作台',
            '绑定项目目录，运行受控任务（只读写工作文件夹内文件）',
            'workbench',
        )
        # 模块 Tab 已显示“Agent 工作台”，页内只保留一行工具条
        header.hide()
        root.addWidget(header)
        self.model_combo = QComboBox()
        size_pick_combo(self.model_combo)
        # 窄窗口时模型名可收缩（悬停看全名），把宽度让给执行方式
        self.model_combo.setMinimumWidth(140)
        self.model_combo.setMaximumWidth(280)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(10)
        self.model_combo.setToolTip('任务执行使用的内网模型')
        self.dir_btn = QPushButton()
        apply_button(self.dir_btn, 'ghost', compact=True)
        self.dir_btn.clicked.connect(self._bind_directory)
        self.dir_label = QLabel()
        self.dir_label.setObjectName('field-hint')
        self.dir_label.setMaximumWidth(280)
        self.dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        top.addWidget(self.model_combo)

        self.exec_mode_combo = QComboBox()
        from ui.field_metrics import size_enum_combo
        size_enum_combo(self.exec_mode_combo, min_w=100, max_w=160)
        self.exec_mode_combo.currentIndexChanged.connect(self._on_exec_mode_changed)
        top.addWidget(self.exec_mode_combo)

        top.addStretch(1)

        self.context_toggle_btn = QPushButton()
        apply_button(self.context_toggle_btn, 'ghost', compact=True)
        self.context_toggle_btn.setCheckable(True)
        self.context_toggle_btn.setChecked(True)
        self.context_toggle_btn.clicked.connect(self._toggle_context_panel)
        top.addWidget(self.context_toggle_btn)

        self.conflicts_btn = QPushButton()
        apply_button(self.conflicts_btn, 'ghost', compact=True)
        self.conflicts_btn.setObjectName('agent-conflicts-btn')
        self.conflicts_btn.clicked.connect(self._open_conflict_viewer)
        self.conflicts_btn.hide()
        top.addWidget(self.conflicts_btn)

        self.new_btn = QPushButton()
        apply_button(self.new_btn, 'secondary', compact=True)
        self.new_btn.clicked.connect(self._new_workspace)
        top.addWidget(self.new_btn)

        root.addWidget(toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName('agent-workbench-splitter')
        split.setChildrenCollapsible(False)
        split.setHandleWidth(16)
        split.setProperty('prismGutter', True)

        # 左栏：空间 / 对话管理树（支持右键管理）
        left_card = QFrame()
        left_card.setObjectName('agent-space-card')
        self.space_card = left_card
        left_l = QVBoxLayout(left_card)
        left_l.setContentsMargins(8, 8, 8, 8)
        left_l.setSpacing(6)

        left_head = QHBoxLayout()
        self.space_title = QLabel()
        self.space_title.setObjectName('section-title')
        left_head.addWidget(self.space_title, 1)

        self.new_ws_btn = QPushButton('+')
        apply_button(self.new_ws_btn, 'ghost', compact=True)
        self.new_ws_btn.setToolTip('新建工作空间')
        self.new_ws_btn.clicked.connect(self._new_workspace)
        left_head.addWidget(self.new_ws_btn)
        self.space_new_btn = self.new_ws_btn
        left_l.addLayout(left_head)

        self.space_tree = QTreeWidget()
        self.space_tree.setHeaderHidden(True)
        self.space_tree.setRootIsDecorated(True)
        self.space_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.space_tree.customContextMenuRequested.connect(self._on_space_tree_menu)
        self.space_tree.itemClicked.connect(self._on_space_clicked)
        left_l.addWidget(self.space_tree, 1)

        ws_manage_row = QHBoxLayout()
        self.bind_dir_btn = QPushButton()
        apply_button(self.bind_dir_btn, 'secondary', compact=True)
        self.bind_dir_btn.clicked.connect(self._bind_directory)
        ws_manage_row.addWidget(self.bind_dir_btn)

        self.dir_label = QLabel()
        self.dir_label.setObjectName('field-hint')
        self.dir_label.setMinimumWidth(0)
        self.dir_label.setMaximumWidth(240)
        self.dir_label.setWordWrap(True)
        self.dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ws_manage_row.addWidget(self.dir_label, 1)
        left_l.addLayout(ws_manage_row)
        split.addWidget(left_card)

        # 中栏：消息流 + 输入框
        self.center_split = QSplitter(Qt.Orientation.Vertical)
        self.center_split.setChildrenCollapsible(False)
        self.center_split.setHandleWidth(16)
        self.center_split.setProperty('prismGutter', True)

        thread_container = QFrame()
        thread_container.setObjectName('agent-thread-card')
        self.thread_card = thread_container
        thread_l = QVBoxLayout(thread_container)
        thread_l.setContentsMargins(8, 8, 8, 8)
        thread_l.setSpacing(6)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_widget = QWidget()
        self.thread_layout = QVBoxLayout(self.thread_widget)
        self.thread_layout.setContentsMargins(0, 0, 0, 0)
        self.thread_layout.setSpacing(8)
        self.thread_layout.addStretch(1)
        self.scroll.setWidget(self.thread_widget)
        # 空对话时的上手引导（有消息后自动隐藏）
        from ui.page_chrome import make_empty_state
        self.thread_empty = make_empty_state(
            '让 Agent 读代码、改文件',
            '① 左侧「绑定目录」选项目文件夹　② 顶部选模型与执行方式　'
            '③ 在下方描述任务，例如“读取 README 并总结模块结构”',
        )
        thread_l.addWidget(self.thread_empty, 1)
        thread_l.addWidget(self.scroll, 1)
        self.scroll.hide()
        self.center_split.addWidget(thread_container)

        composer_container = QFrame()
        composer_container.setObjectName('agent-composer-card')
        self.composer_card = composer_container
        composer_l = QVBoxLayout(composer_container)
        composer_l.setContentsMargins(8, 8, 8, 8)
        composer_l.setSpacing(6)

        self.attachment_bar = QLabel()
        self.attachment_bar.setObjectName('field-hint')
        self.attachment_bar.setWordWrap(True)
        self.attachment_bar.hide()
        composer_l.addWidget(self.attachment_bar)

        self.context_budget_label = QLabel()
        self.context_budget_label.setObjectName('agent-context-budget-status')
        self.context_budget_label.setWordWrap(False)
        self.context_budget_label.setMinimumWidth(0)
        self.context_budget_label.hide()
        composer_l.addWidget(self.context_budget_label)

        self.input = QPlainTextEdit()
        self.input.setObjectName('agent-composer-input')
        self.input.setMinimumHeight(120)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.installEventFilter(self)
        composer_l.addWidget(self.input, 1)

        send_row = QHBoxLayout()
        send_row.setSpacing(8)

        # "+" 菜单按钮
        self.add_attachment_btn = QToolButton()
        self.add_attachment_btn.setText('+')
        self.add_attachment_btn.setToolTip('新建对话 / 添加工作区文件引用 / 清空附件')
        self.add_attachment_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        apply_button(self.add_attachment_btn, 'ghost', compact=True)
        self.add_attachment_btn.setObjectName('agent-attachment-btn')
        self.add_attachment_btn.setAccessibleName('对话与附件菜单')
        attach_menu = QMenu(self.add_attachment_btn)
        self.new_conv_action = attach_menu.addAction('新建对话', self._new_conversation)
        self.add_file_ref_action = attach_menu.addAction('添加工作区内文件引用', self._pick_workspace_file_ref)
        attach_menu.addSeparator()
        self.clear_ref_action = attach_menu.addAction('清空输入', lambda: self.input.clear())
        self.add_attachment_btn.setMenu(attach_menu)

        self.send_hint = QLabel()
        self.send_hint.setObjectName('field-hint')
        self.send_hint.setMinimumWidth(0)
        self.send_hint.setWordWrap(True)
        self.send_btn = QPushButton()
        apply_button(self.send_btn, 'primary', compact=True)
        self.send_btn.clicked.connect(self._on_action_clicked)
        self.stop_btn = QPushButton()
        apply_button(self.stop_btn, 'secondary', compact=True)
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.hide()

        send_row.addWidget(self.add_attachment_btn)
        send_row.addWidget(self.send_hint, 1)
        send_row.addWidget(self.send_btn)
        composer_l.addLayout(send_row)
        self.center_split.addWidget(composer_container)

        install_splitter_prefs(
            self.center_split,
            defaults=[520, 180],
            page_id='agent-workbench',
            tab_id='composer_v2',
            min_sizes=[200, 120],
            accessible_name='Agent 对话与输入区分隔',
        )
        split.addWidget(self.center_split)

        # 右栏：Context 面板（项目文件树 + 预览，可折叠/拖拽）
        self.context_panel = QFrame()
        self.context_panel.setObjectName('agent-context-card')
        context_l = QVBoxLayout(self.context_panel)
        context_l.setContentsMargins(8, 8, 8, 8)
        context_l.setSpacing(4)

        self.context_split = QSplitter(Qt.Orientation.Vertical)
        self.context_split.setChildrenCollapsible(False)
        self.context_split.setHandleWidth(16)
        self.context_split.setProperty('prismGutter', True)

        # 项目文件树
        tree_box = QWidget()
        tree_l = QVBoxLayout(tree_box)
        tree_l.setContentsMargins(0, 0, 0, 0)
        tree_l.setSpacing(4)
        self.project_title = QLabel('项目文件')
        self.project_title.setObjectName('field-hint')
        tree_l.addWidget(self.project_title)
        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderHidden(True)
        self.project_tree.setIndentation(14)
        self.project_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.project_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        tree_l.addWidget(self.project_tree, 1)
        self.context_split.addWidget(tree_box)

        # 预览区
        prev_box = QWidget()
        prev_l = QVBoxLayout(prev_box)
        prev_l.setContentsMargins(0, 0, 0, 0)
        prev_l.setSpacing(4)
        self.preview_title = QLabel('预览')
        self.preview_title.setObjectName('field-hint')
        prev_l.addWidget(self.preview_title)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName('detail-text')
        prev_l.addWidget(self.preview, 1)
        self.context_split.addWidget(prev_box)

        install_splitter_prefs(
            self.context_split,
            defaults=[260, 260],
            page_id='agent-workbench',
            tab_id='context_v2',
            min_sizes=[100, 100],
            accessible_name='Agent 项目文件与预览分隔',
        )
        context_l.addWidget(self.context_split, 1)
        split.addWidget(self.context_panel)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        install_splitter_prefs(
            split,
            defaults=[240, 680, 280],
            defaults_for_extent=lambda extent: [240, max(360, extent - 520), 280],
            page_id='agent-workbench',
            tab_id='main_v2',
            min_sizes=[140, 360, 180],
            accessible_name='工作台三栏分隔',
        )
        root.addWidget(split, 1)
        self._split = split

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                modifiers = event.modifiers()
                if modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier):
                    return False
                if not event.isAutoRepeat():
                    self._on_action_clicked()
                    return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._send_hint_resize_pending:
            return
        self._send_hint_resize_pending = True
        QTimer.singleShot(0, self._sync_send_hint_minimum)

    def _sync_send_hint_minimum(self):
        self._send_hint_resize_pending = False
        hint = getattr(self, 'send_hint', None)
        if hint is None:
            return
        width = int(hint.width())
        line_height = max(1, int(hint.fontMetrics().height()))
        if width > 0 and hint.hasHeightForWidth():
            wrapped_height = int(hint.heightForWidth(width))
        else:
            wrapped_height = line_height * 2
        # One line of slack covers style spacing and width changes between the
        # panel resize and Qt's subsequent child-layout pass.
        hint.setMinimumHeight(max(line_height * 2, wrapped_height + line_height))
        composer = getattr(self, 'composer_card', None)
        if composer is not None:
            layout = composer.layout()
            margins = layout.contentsMargins()
            row_height = max(
                hint.minimumHeight(),
                self.send_btn.minimumSizeHint().height(),
                self.add_attachment_btn.minimumSizeHint().height(),
            )
            required_height = (
                margins.top() + self.input.minimumHeight() + layout.spacing()
                + row_height + margins.bottom()
            )
            composer.setMinimumHeight(required_height)

    def _toggle_context_panel(self):
        visible = not self.context_panel.isVisible()
        self.context_panel.setVisible(visible)
        self.context_toggle_btn.setChecked(visible)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('Agent 工作台' if zh else 'Agent Workbench')
        self.page_subtitle.setText(
            '绑定项目目录，运行受控任务（只读写工作文件夹内文件）' if zh else
            'Bind a project folder and run controlled tasks (workspace files only)'
        )
        self.bind_dir_btn.setText('绑定目录' if zh else 'Bind folder')
        self.bind_dir_btn.setMinimumWidth(self.bind_dir_btn.sizeHint().width())
        ws_dir = self._workspace_session.get('workspace_dir') if self._workspace_session else ''
        self._set_workspace_dir_label(ws_dir)
        self.new_ws_btn.setToolTip('新建工作区' if zh else 'New workspace')
        self.new_btn.setText('新建工作区' if zh else 'New workspace')
        self.space_new_btn.setToolTip('新建对话' if zh else 'New conversation')
        self.context_toggle_btn.setText('项目文件' if zh else 'Files')
        self.context_toggle_btn.setToolTip('显示/隐藏项目文件与预览面板' if zh else 'Toggle project files and preview panel')
        self._set_conflict_button_count(self._conflict_count)
        self.space_title.setText('工作区' if zh else 'Workspaces')
        self.project_title.setText('项目文件' if zh else 'Files')
        self.preview_title.setText('预览' if zh else 'Preview')
        self.send_hint.setText('Enter 发送 · Alt+Enter 换行' if zh else 'Enter: send · Alt+Enter: newline')
        # Reserve the actual wrapped height so a narrow composer does not
        # overlap the input area when the keyboard hint spans multiple lines.
        self._sync_send_hint_minimum()
        self.new_conv_action.setText('新建对话' if zh else 'New conversation')
        self.add_file_ref_action.setText('添加文件引用' if zh else 'Reference file')
        self.clear_ref_action.setText('清空附件' if zh else 'Clear attachments')
        self.input.setPlaceholderText('描述任务…（Enter 发送，Alt+Enter 换行）' if zh else 'Describe task… (Enter to send, Alt+Enter for newline)')
        self._update_exec_mode_options()
        if self._context_budget_state is not None:
            self._show_context_budget_state(self._context_budget_state)
        self._sync_running_state()

    def _update_exec_mode_options(self):
        zh = self.language == 'zh'
        cur_val = bool(self._plan_confirm)
        self.exec_mode_combo.blockSignals(True)
        self.exec_mode_combo.clear()
        self.exec_mode_combo.addItem('直接执行' if zh else 'Run directly', False)
        self.exec_mode_combo.addItem('执行前确认计划' if zh else 'Plan first', True)
        self.exec_mode_combo.setCurrentIndex(1 if cur_val else 0)
        self.exec_mode_combo.blockSignals(False)
        # Keep every translated option readable at narrow window sizes. The
        # generic enum metric has a 160px cap, which clips the English labels.
        from PyQt6.QtGui import QFontMetrics
        self.exec_mode_combo.ensurePolished()
        longest_text = max(
            QFontMetrics(self.exec_mode_combo.font()).horizontalAdvance(
                self.exec_mode_combo.itemText(index)
            )
            for index in range(self.exec_mode_combo.count())
        )
        # 样式表固定占用约 83px：左内边距 + 右侧下拉区 38 + 边框/焦点环；
        # 不用 subControlRect（未布局时按默认 640px 宽计算，会把余量算成几百像素）。
        required_width = longest_text + 88
        self.exec_mode_combo.setMaximumWidth(max(160, required_width))
        self.exec_mode_combo.setMinimumWidth(required_width)
        # 工具条按钮至少保留文字宽度，窄窗口时由模型下拉收缩让位
        for button in (self.context_toggle_btn, self.conflicts_btn, self.new_btn):
            button.ensurePolished()
            button.setMinimumWidth(button.sizeHint().width())
        self._update_exec_mode_tooltips()

    def _set_workspace_dir_label(self, workspace_dir: str | None):
        """Show an explicit short suffix while keeping the complete path available."""
        path = str(workspace_dir or '').strip()
        zh = self.language == 'zh'
        if not path:
            visible = '（未绑定目录）' if zh else '(No folder bound)'
            self.dir_label.setText(visible)
            self.dir_label.setToolTip(visible)
            return

        normalized = os.path.normpath(path)
        prefix = '已绑定：' if zh else 'Bound: '
        # A visible ellipsis makes it clear that the compact label is a suffix,
        # not a complete path hidden by word-wrapping or elision.
        parts = [part for part in normalized.replace('\\', '/').split('/') if part]
        suffix = os.path.join(*parts[-2:]) if parts else normalized
        suffix = suffix.replace('/', os.sep).replace('\\', os.sep)
        if len(suffix) > 32:
            suffix = f'{suffix[:14]}…{suffix[-14:]}'
        visible = f'{prefix}…{os.sep}{suffix}' if len(normalized) > 36 else f'{prefix}{normalized}'
        self.dir_label.setText(visible)
        self.dir_label.setToolTip(f'{prefix}{normalized}')

    def _update_exec_mode_tooltips(self):
        zh = self.language == 'zh'
        tip_direct = (
            '按当前工具权限与安全规则直接执行任务，遇到需要确认的高风险操作仍遵循原有安全确认。'
            if zh else
            'Execute directly under safety rules; high-risk operations still require confirmation.'
        )
        tip_plan = (
            'Agent 生成执行计划后先请求确认，确认后才继续执行后续步骤。'
            if zh else
            'Agent generates an execution plan and requests confirmation before proceeding.'
        )
        self.exec_mode_combo.setItemData(0, tip_direct, Qt.ItemDataRole.ToolTipRole)
        self.exec_mode_combo.setItemData(1, tip_plan, Qt.ItemDataRole.ToolTipRole)
        current_tip = tip_plan if self._plan_confirm else tip_direct
        self.exec_mode_combo.setToolTip(current_tip)

    def _on_exec_mode_changed(self, index: int):
        self._plan_confirm = bool(self.exec_mode_combo.currentData())
        self._update_exec_mode_tooltips()
        if self._workspace_session:
            self._workspace_session['plan_confirm'] = self._plan_confirm
            save_workspace(self._workspace_session)

    def _toggle_context_panel(self, checked: bool | None = None):
        if checked is None:
            checked = not self.context_panel.isVisible()
        self.context_panel.setVisible(checked)
        self.context_toggle_btn.setChecked(checked)

    def _is_path_within_workspace(self, path: str, workspace_dir: str = '') -> bool:
        """检查路径（规范化真实物理路径）是否严格位于当前绑定的工作空间目录内。

        防止 Windows junction / symlink / 相对路径逃逸出工作区。
        """
        if not path:
            return False
        ws_dir = workspace_dir or (self._workspace_session.get('workspace_dir') if self._workspace_session else '')
        if not ws_dir and hasattr(self, 'project_tree') and self.project_tree.topLevelItemCount() > 0:
            root_item = self.project_tree.topLevelItem(0)
            root_path = root_item.data(0, Qt.ItemDataRole.UserRole)
            if root_path and isinstance(root_path, str):
                ws_dir = root_path
        if not ws_dir:
            return False
        try:
            real_ws = os.path.realpath(os.path.abspath(ws_dir))
            real_path = os.path.realpath(os.path.abspath(path))
            norm_ws = os.path.normcase(real_ws)
            norm_path = os.path.normcase(real_path)

            if norm_path == norm_ws:
                return True

            common = os.path.normcase(os.path.commonpath([norm_ws, norm_path]))
            if common != norm_ws:
                return False

            rel = os.path.relpath(norm_path, norm_ws)
            return not rel.startswith('..') and rel != '..'
        except (ValueError, OSError, TypeError):
            # 跨盘符（如 C:\ 和 D:\）或非法路径抛出异常时一律拒绝
            return False

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.page_subtitle, low_height)

    def _title(self) -> str:
        return 'Agent 工作台' if self.language == 'zh' else 'Agent Workbench'

    # ── 模型选择 ──────────────────────────────────────────────────────────

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

    def _on_model_changed(self, idx: int = 0):
        data = self._current_model()
        if data and data.get('id'):
            from config import load_settings, save_settings
            s = load_settings()
            s['model_chat_last_model_id'] = data.get('id')
            save_settings(s)

    def _current_model(self) -> dict | None:
        data = self.model_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    # ── 空间管理 ──────────────────────────────────────────────────────────

    def _select_workspace(self, ws_id: str, select_last_conv: bool = False):
        """按 id 加载一个空间到工作台（供进入/恢复用）。"""
        ws = load_workspace(ws_id)
        if not ws:
            return
        self._clear_context_budget_state()
        conv = self._active_conversation_of(ws)
        self._cancel_for_context_change(
            str(ws.get('id') or ''), str((conv or {}).get('id') or ''),
            str(ws.get('workspace_dir') or ''),
        )
        self._workspace_session = ws
        self._tool_calls = []
        self._plan_confirm = bool(ws.get('plan_confirm', False))
        self.exec_mode_combo.blockSignals(True)
        self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
        self.exec_mode_combo.blockSignals(False)
        self._update_exec_mode_tooltips()
        ws_dir = ws.get('workspace_dir') or ''
        self._set_workspace_dir_label(ws_dir)
        if ws_dir:
            self._refresh_tree(ws_dir)
        else:
            self.project_tree.clear()
        self._render_conversation(ws, conv)
        self._refresh_space_tree(select_ws_id=ws.get('id'),
                                 select_conv_id=conv.get('id') if select_last_conv else '')

    def _bind_directory(self):
        """绑定/更换工作文件夹。未建空间时自动先建一个并绑定。"""
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('agent_workspace', os.path.expanduser('~'))
        path = QFileDialog.getExistingDirectory(
            self, '选择工作文件夹' if self.language == 'zh' else 'Select folder', start,
        )
        if not path:
            return
        remember_dialog_path('agent_workspace', path, is_directory=True)
        if not self._workspace_session:
            ws = empty_workspace(title='新工作台' if self.language == 'zh' else 'New workspace')
            save_workspace(ws)
            self._workspace_session = ws
            self._tool_calls = []
            self._plan_confirm = ws.get('plan_confirm', False)
            self.exec_mode_combo.blockSignals(True)
            self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
            self.exec_mode_combo.blockSignals(False)
            self._update_exec_mode_tooltips()
        elif os.path.realpath(str(self._workspace_session.get('workspace_dir') or '')) != os.path.realpath(path):
            self._invalidate_active_task('workspace directory changed')
            self._clear_attachments()
        self._workspace_session['workspace_dir'] = path
        save_workspace(self._workspace_session)
        self._set_workspace_dir_label(path)
        self._refresh_tree(path)
        self._refresh_space_tree(select_ws_id=self._workspace_session.get('id'))

    def _refresh_tree(self, workspace_dir: str):
        """刷新项目文件树根节点并懒加载首层。"""
        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        if not workspace_dir or not os.path.isdir(workspace_dir) or not self._is_path_within_workspace(workspace_dir, workspace_dir):
            self.project_tree.blockSignals(False)
            return

        root = QTreeWidgetItem(self.project_tree)
        root_name = os.path.basename(workspace_dir) or workspace_dir
        root.setText(0, root_name + '/')
        root.setData(0, Qt.ItemDataRole.UserRole, workspace_dir)
        self.project_tree.insertTopLevelItem(0, root)
        self._populate_dir_item(root, workspace_dir)
        root.setExpanded(True)
        self.project_tree.blockSignals(False)

    def _populate_dir_item(self, parent_item: QTreeWidgetItem, dir_path: str):
        """填充一个目录节点的直接子节点。"""
        while parent_item.childCount() > 0:
            parent_item.removeChild(parent_item.child(0))

        if not dir_path or not os.path.isdir(dir_path) or not self._is_path_within_workspace(dir_path):
            return

        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return

        for name in entries:
            if name.startswith('.') or name in IGNORED_DIR_NAMES:
                continue
            full = os.path.join(dir_path, name)
            # 安全检查：canonical target 必须在当前工作区内，否则不向树中添加
            if not self._is_path_within_workspace(full):
                continue
            is_dir = os.path.isdir(full)
            ext = os.path.splitext(name)[1].lower()
            if not is_dir and ext not in WHITELIST_EXTENSIONS:
                continue

            item = QTreeWidgetItem(parent_item)
            item.setText(0, name + ('/' if is_dir else ''))
            item.setData(0, Qt.ItemDataRole.UserRole, full)
            if is_dir:
                placeholder = QTreeWidgetItem(item)
                placeholder.setData(0, Qt.ItemDataRole.UserRole, '')
                item.setExpanded(False)
            else:
                item.setToolTip(0, full)

    def _on_tree_item_expanded(self, item: QTreeWidgetItem):
        """目录节点展开时懒加载子节点。"""
        full = item.data(0, Qt.ItemDataRole.UserRole)
        if full and os.path.isdir(full) and self._is_path_within_workspace(full):
            if item.childCount() == 1 and not item.child(0).data(0, Qt.ItemDataRole.UserRole):
                self._populate_dir_item(item, full)

    def _on_tree_double_click(self, item: QTreeWidgetItem, column: int):
        """双击文件 → 在预览区显示内容（限白名单且 ≤200KB）；双击目录 → 切换展开/折叠。"""
        full = item.data(0, Qt.ItemDataRole.UserRole)
        if not full:
            return
        if not self._is_path_within_workspace(full):
            self.preview.setPlainText('该路径超出当前工作区，已拒绝读取。' if self.language == 'zh' else 'Path is outside current workspace; read denied.')
            return
        if os.path.isdir(full):
            item.setExpanded(not item.isExpanded())
            return
        workspace_dir = str((self._workspace_session or {}).get('workspace_dir') or '')
        try:
            size = os.path.getsize(full)
        except OSError:
            return
        if size > 200 * 1024:
            self.preview.setPlainText(
                f'文件超过 200KB 限制（{size // 1024}KB），拒绝读取。' if self.language == 'zh'
                else f'File exceeds the 200KB limit ({size // 1024}KB); read denied.'
            )
            return
        try:
            from tools.agent_runtime import execute_tool
            relative_path = os.path.relpath(
                os.path.realpath(full), os.path.realpath(workspace_dir)
            ).replace('\\', '/')
            result = execute_tool('read_file', {'path': relative_path}, workspace_dir)
            if not result.get('ok'):
                content = str(result.get('error') or ('读取失败' if self.language == 'zh' else 'Read failed'))
            else:
                content = str(result.get('content') or result.get('data') or '')
        except Exception as e:
            content = f'读取失败: {e}' if self.language == 'zh' else f'Read failed: {e}'
        self.preview.setPlainText(content)

    # ── 空间（工作台任务）列表树 ─────────────────────────────────────────

    def _refresh_space_tree(self, select_ws_id: str = '', select_conv_id: str = ''):
        """刷新左栏空间树：顶层=工作台，子级=该空间下的对话记录。"""
        self.space_tree.blockSignals(True)
        self.space_tree.clear()
        conflict_count = 0
        for meta in list_workspaces():
            ws_id = str(meta.get('id') or '')
            ws_dir = str(meta.get('workspace_dir') or '')
            cur = (' 📁' if ws_dir else '')
            top = QTreeWidgetItem([f"{meta.get('title') or '空间'}{cur}"])
            top.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'space', 'ws_id': ws_id})
            top.setToolTip(0, ws_dir or '（未绑定工作文件夹）')
            s = load_workspace(ws_id) or {}
            conflicts = s.get('conflicted_runs')
            if isinstance(conflicts, list):
                conflict_count += len(conflicts)
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
            self.space_tree.addTopLevelItem(top)
            if ws_id == select_ws_id:
                top.setExpanded(True)
                if select_conv_id:
                    for k in range(top.childCount()):
                        child = top.child(k)
                        if child.data(0, Qt.ItemDataRole.UserRole).get('conv_id') == select_conv_id:
                            self.space_tree.setCurrentItem(child)
                            break
                else:
                    self.space_tree.setCurrentItem(top)
        self.space_tree.blockSignals(False)
        self._set_conflict_button_count(conflict_count)

    def _set_conflict_button_count(self, count: int):
        count = count if type(count) is int and count > 0 else 0
        self._conflict_count = count
        if not hasattr(self, 'conflicts_btn'):
            return
        zh = self.language == 'zh'
        self.conflicts_btn.setText(f'冲突 {count}' if zh else f'Conflicts {count}')
        self.conflicts_btn.setMinimumWidth(self.conflicts_btn.sizeHint().width())
        detail = (
            '查看因对话更新而单独保留的旧运行结果；只读，不会重放或执行。'
            if zh else
            'View stale run results kept separately after a conversation update. Read-only; nothing is replayed or run.'
        )
        self.conflicts_btn.setToolTip(detail)
        self.conflicts_btn.setAccessibleName(
            f'{"冲突运行结果" if zh else "Conflicted run results"}: {count}'
        )
        self.conflicts_btn.setEnabled(count > 0)
        self.conflicts_btn.setVisible(count > 0)

    def _conflict_viewer_bounds(self) -> QRect:
        screen = self.screen() or QApplication.primaryScreen()
        screen_bounds = screen.availableGeometry() if screen is not None else QRect(0, 0, 1024, 768)
        parent_origin = self.mapToGlobal(self.contentsRect().topLeft())
        parent_bounds = QRect(parent_origin, self.contentsRect().size())
        bounded = parent_bounds.intersected(screen_bounds)
        return bounded if bounded.isValid() and not bounded.isEmpty() else screen_bounds

    def _place_conflict_viewer(self, dialog: QDialog, bounds: QRect):
        # Leave room for native window decorations and stay within both the
        # embedded panel and the active screen's usable work area.
        width = max(1, min(760, bounds.width() - 48))
        height = max(1, min(560, bounds.height() - 64))
        dialog.setMaximumSize(width, height)
        dialog.resize(width, height)
        dialog.show()
        dialog.ensurePolished()
        dialog.winId()
        frame = dialog.frameGeometry()
        rightmost = bounds.right() - frame.width() + 1
        bottommost = bounds.bottom() - frame.height() + 1
        x = max(bounds.left(), min(frame.left(), rightmost))
        y = max(bounds.top(), min(frame.top(), bottommost))
        dialog.move(x, y)

    def _conflict_history_text(self, workspaces) -> str:
        zh = self.language == 'zh'
        sections = []
        used = 0
        truncated = False
        truncation_marker = '\n\n[内容已截断]' if zh else '\n\n[Content truncated]'
        content_limit = max(0, _CONFLICT_VIEWER_MAX_CHARS - len(truncation_marker))

        def append_section(text: str):
            nonlocal used, truncated
            if used >= content_limit:
                truncated = True
                return
            remaining = content_limit - used
            piece = text[:remaining]
            sections.append(piece)
            used += len(piece)
            if len(piece) < len(text):
                truncated = True

        role_labels = (
            {'user': '用户', 'assistant': '助手', 'tool': '工具'} if zh else
            {'user': 'User', 'assistant': 'Assistant', 'tool': 'Tool'}
        )
        record_index = 0
        for meta in workspaces:
            if not isinstance(meta, dict):
                continue
            ws_id = str(meta.get('id') or '')
            if not ws_id:
                continue
            workspace = load_workspace(ws_id) or {}
            conflicts = workspace.get('conflicted_runs')
            if not isinstance(conflicts, list):
                continue
            title = str(workspace.get('title') or ('工作区' if zh else 'Workspace'))
            for conflict in conflicts:
                record_index += 1
                record_label = f'第 {record_index} 条' if zh else f'Record {record_index}'
                append_section(
                    f'\n\n{record_label} · {title}\n'
                    + ('旧运行结果（只读历史记录）\n' if zh else 'Stale run result (read-only history)\n')
                )
                if not isinstance(conflict, dict):
                    append_section('记录格式无效。\n' if zh else 'Record is malformed.\n')
                    continue
                messages = conflict.get('messages')
                if not isinstance(messages, list):
                    append_section('没有可显示的消息。\n' if zh else 'No readable messages.\n')
                    continue
                shown = 0
                for message in messages:
                    if (not isinstance(message, dict)
                            or 'reasoning_content' in message):
                        continue
                    role = message.get('role')
                    content = message.get('content')
                    if role not in role_labels or not isinstance(content, str) or not content:
                        continue
                    append_section(f'\n[{role_labels[role]}]\n{content}\n')
                    shown += 1
                    if truncated:
                        break
                if not shown:
                    append_section('没有可显示的公开消息。\n' if zh else 'No readable public messages.\n')
                if truncated:
                    break
            if truncated:
                break
        if not sections:
            return '当前没有可显示的冲突记录。' if zh else 'There are no readable conflict records.'
        if truncated:
            sections.append(truncation_marker)
        return ''.join(sections).lstrip()

    def _open_conflict_viewer(self):
        current = self._conflict_viewer
        if current is not None:
            try:
                if current.isVisible():
                    current.raise_()
                    current.activateWindow()
                    return current
            except RuntimeError:
                self._conflict_viewer = None

        workspaces = list_workspaces()
        text = self._conflict_history_text(workspaces)
        dialog = QDialog(self)
        dialog.setObjectName('agent-conflict-viewer')
        dialog.setWindowTitle('冲突运行结果' if self.language == 'zh' else 'Conflicted run results')
        dialog.setModal(False)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        explanation = QLabel(
            '这些旧结果只供查看；原始对话未被裁剪，历史工具调用不会重放。'
            if self.language == 'zh' else
            'These old results are for viewing only. Original conversations remain intact; historical tools are never replayed.'
        )
        explanation.setWordWrap(True)
        explanation.setObjectName('agent-conflict-viewer-explanation')
        layout.addWidget(explanation)
        viewer = QPlainTextEdit()
        viewer.setObjectName('agent-conflict-viewer-text')
        viewer.setReadOnly(True)
        viewer.setUndoRedoEnabled(False)
        viewer.setMinimumSize(0, 0)
        viewer.setPlainText(text)
        layout.addWidget(viewer, 1)
        close_button = QPushButton('关闭' if self.language == 'zh' else 'Close')
        close_button.clicked.connect(dialog.close)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self._conflict_viewer = dialog
        dialog.finished.connect(lambda _result, target=dialog: self._clear_conflict_viewer(target))
        self._place_conflict_viewer(dialog, self._conflict_viewer_bounds())
        return dialog

    def _clear_conflict_viewer(self, dialog: QDialog):
        if self._conflict_viewer is dialog:
            self._conflict_viewer = None

    @staticmethod
    def _parse_context_budget_progress(content):
        if not isinstance(content, str):
            return None
        try:
            if len(content.encode('utf-8')) > 512:
                return None
            payload = json.loads(content)
        except (TypeError, ValueError, UnicodeError):
            return None
        if type(payload) is not dict:
            return None
        schema = payload.get('schema')
        if type(schema) is not int:
            return None
        if schema == 2:
            if set(payload) != _CONTEXT_BUDGET_V2_KEYS:
                return None
            if type(payload.get('status')) is not str or payload['status'] != 'blocked':
                return None
            if (type(payload.get('estimate_kind')) is not str
                    or payload['estimate_kind'] != 'serialized_request_utf8_bytes'):
                return None
            limit = payload.get('request_byte_limit')
            if (type(limit) is not int or limit < 1
                    or limit > _CONTEXT_BUDGET_MAX_REQUEST_BYTES):
                return None
            return dict(payload)
        if schema != 1 or set(payload) != _CONTEXT_BUDGET_KEYS:
            return None
        status = payload.get('status')
        if type(status) is not str or status not in {'ready', 'blocked', 'deferred'}:
            return None
        estimate_kind = payload.get('estimate_kind')
        if type(estimate_kind) is not str or estimate_kind not in {'heuristic_utf8_bytes', 'not_estimated'}:
            return None
        if type(payload.get('context_window_known')) is not bool:
            return None
        limits = {
            'input_estimate': 10_000_000,
            'output_reserve': 10_000_000,
            'messages_kept': 100_000,
            'messages_dropped': 100_000,
        }
        for key, maximum in limits.items():
            value = payload.get(key)
            if type(value) is not int or value < 0 or value > maximum:
                return None
        reason = payload.get('reason')
        if type(reason) is not str or reason not in _CONTEXT_BUDGET_REASONS:
            return None
        if status == 'ready' and reason not in {
            'trimmed_to_known_input_budget',
            'trimmed_to_application_budget_window_unknown',
            'within_known_input_budget',
            'within_application_budget_window_unknown',
            'memory_compacted', 'memory_omitted',
        }:
            return None
        if status == 'blocked' and reason not in {
            'invalid_input', 'private_reasoning_content', 'incomplete_history',
            'estimator_error', 'invalid_estimate', 'context_anchors_too_large',
            'estimator_inconsistent',
        }:
            return None
        if status == 'deferred' and reason != 'pending_exchange':
            return None
        return dict(payload)

    def _clear_context_budget_state(self):
        self._context_budget_state = None
        if hasattr(self, 'context_budget_label'):
            self.context_budget_label.clear()
            self.context_budget_label.setToolTip('')
            self.context_budget_label.hide()

    def _show_context_budget_state(self, state):
        if not hasattr(self, 'context_budget_label'):
            return
        self._context_budget_state = dict(state)
        zh = self.language == 'zh'
        if state.get('schema') == 2:
            short = '上下文受限 · 原文保留' if zh else 'Context limited · history kept'
            limit = state['request_byte_limit']
            tooltip = (
                f'最终序列化模型请求超过本机 JSON UTF-8 字节限额（{limit} bytes）。'
                '后续模型请求未发送；此前已完成操作仍保留记录。'
                '此本机字节限额不能保证请求符合模型上下文窗口限制。'
                if zh else
                f'The final serialized model request exceeded the local JSON UTF-8 byte ceiling ({limit} bytes). '
                'The subsequent model request was not sent; earlier completed operations remain recorded. '
                'This local byte ceiling does not guarantee the request fits within the model context window.'
            )
            self.context_budget_label.setText(short)
            self.context_budget_label.setToolTip(tooltip)
            self.context_budget_label.setAccessibleName(short)
            self.context_budget_label.show()
            return
        status = state['status']
        dropped = state['messages_dropped']
        if status == 'blocked':
            short = '上下文受限 · 原文保留' if zh else 'Context limited · history kept'
        elif status == 'deferred':
            short = '等待本轮完成 · 原文保留' if zh else 'Waiting for turn · history kept'
        elif dropped:
            short = '请求已精简 · 原文保留' if zh else 'Request trimmed · history kept'
        else:
            short = '原文保留' if zh else 'History kept'

        estimate = state['input_estimate']
        if state['estimate_kind'] == 'heuristic_utf8_bytes':
            estimate_detail = (
                f'本机请求大小估值：{estimate}（UTF-8 字节启发式，不是供应商 tokenizer 计量）。'
                if zh else
                f'Local request-size estimate: {estimate} (UTF-8 byte heuristic, not the provider tokenizer).'
            )
        else:
            estimate_detail = '本轮未产生可用估值。' if zh else 'No usable estimate was produced for this turn.'
        if zh:
            window_detail = (
                '按已配置的窗口输入额度规划；估值仍为本机近似。'
                if state['context_window_known'] else
                '服务端上下文窗口未知；本轮只能按本机输入上限近似规划，不能据此确认远端 token fit。'
            )
        else:
            window_detail = (
                'Planned against a configured window input allowance; the estimate remains local and approximate.'
                if state['context_window_known'] else
                'The server context window is unknown; this local approximate plan uses the input ceiling and cannot confirm remote token fit.'
            )
        if status == 'blocked':
            if zh:
                reason_detail = {
                    'context_anchors_too_large': '固定原文已超过本机输入上限，本轮未发送模型请求。',
                    'incomplete_history': '历史工具交换未完成或格式无效，本轮未发送模型请求。',
                    'private_reasoning_content': '历史包含私有推理字段，本轮未发送模型请求。',
                }.get(state['reason'], '规划未通过，本轮未发送模型请求。')
            else:
                reason_detail = {
                    'context_anchors_too_large': 'Pinned history exceeds the local input ceiling; no model request was sent.',
                    'incomplete_history': 'Tool history is incomplete or malformed; no model request was sent.',
                    'private_reasoning_content': 'History contains a private reasoning field; no model request was sent.',
                }.get(state['reason'], 'Planning did not pass; no model request was sent.')
        else:
            if zh:
                reason_detail = (
                    f'从本轮请求副本中移除了 {dropped} 条旧消息；完整原始对话仍保留在本机历史中。'
                    if dropped else '完整原始对话仍保留在本机历史中。'
                )
            else:
                reason_detail = (
                    f'Removed {dropped} older messages from this request copy; the complete original conversation remains in local history.'
                    if dropped else 'The complete original conversation remains in local history.'
                )
        tooltip = ' '.join((estimate_detail, window_detail, reason_detail))
        self.context_budget_label.setText(short)
        self.context_budget_label.setToolTip(tooltip)
        self.context_budget_label.setAccessibleName(short)
        self.context_budget_label.show()

    def _on_space_clicked(self, item: QTreeWidgetItem, _column: int):
        """点击空间/对话节点：加载到中栏并设为 active。"""
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = meta.get('kind')
        target_ws_id = str(meta.get('ws_id') or '')
        target_conv_id = ''
        target_ws = load_workspace(target_ws_id) if target_ws_id else None
        if kind == 'space':
            target_conv = self._active_conversation_of(target_ws or {})
            target_conv_id = str((target_conv or {}).get('id') or '')
        elif kind == 'conversation':
            target_conv_id = str(meta.get('conv_id') or '')
        self._cancel_for_context_change(
            target_ws_id, target_conv_id, str((target_ws or {}).get('workspace_dir') or ''),
        )
        self._clear_context_budget_state()
        if kind == 'space':
            ws = load_workspace(meta.get('ws_id') or '')
            if not ws:
                return
            self._workspace_session = ws
            self._plan_confirm = bool(ws.get('plan_confirm', False))
            self.exec_mode_combo.blockSignals(True)
            self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
            self.exec_mode_combo.blockSignals(False)
            self._update_exec_mode_tooltips()
            ws_dir = ws.get('workspace_dir') or ''
            self._set_workspace_dir_label(ws_dir)
            if ws_dir:
                self._refresh_tree(ws_dir)
            else:
                self.project_tree.clear()
            conv = self._active_conversation_of(ws)
            self._render_conversation(ws, conv)
        elif kind == 'conversation':
            ws_id = meta.get('ws_id')
            conv_id = meta.get('conv_id')
            ws = load_workspace(ws_id)
            if not ws:
                return
            if ws.get('active_conv_id') != conv_id:
                ws = set_active_conversation(ws_id, conv_id) or ws
            self._workspace_session = ws
            self._plan_confirm = bool(ws.get('plan_confirm', False))
            self.exec_mode_combo.blockSignals(True)
            self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
            self.exec_mode_combo.blockSignals(False)
            self._update_exec_mode_tooltips()
            ws_dir = ws.get('workspace_dir') or ''
            self._set_workspace_dir_label(ws_dir)
            if ws_dir:
                self._refresh_tree(ws_dir)
            else:
                self.project_tree.clear()
            conv = self._active_conversation_of(ws)
            self._render_conversation(ws, conv)

    def _active_conversation_of(self, ws: dict) -> dict | None:
        convs = ws.get('conversations') or []
        active = ws.get('active_conv_id')
        if active:
            for c in convs:
                if c.get('id') == active:
                    return c
        return convs[0] if convs else None

    def _render_conversation(self, ws: dict, conv: dict | None):
        """把某对话的消息渲染到中栏。"""
        while self.thread_layout.count() > 1:
            child = self.thread_layout.takeAt(0)
            if child.widget():
                child.widget().hide()  # 先隐藏：deleteLater 前不再以默认尺寸闪现
                child.widget().deleteLater()
        self._sync_thread_empty()
        if not conv:
            self.input.clear()
            return
        for msg in conv.get('messages') or []:
            role = str(msg.get('role') or '')
            content = str(msg.get('content') or '')
            if role in ('user', 'assistant') and content:
                self._append_message(role, content)
            if role == 'assistant':
                for call in msg.get('tool_calls') or []:
                    function = call.get('function') if isinstance(call, dict) else None
                    if not isinstance(function, dict):
                        continue
                    try:
                        arguments = json.loads(function.get('arguments') or '{}')
                    except (TypeError, ValueError):
                        arguments = {}
                    self._append_step(function.get('name'), arguments)

    def _on_space_tree_menu(self, pos):
        item = self.space_tree.itemAt(pos)
        if item is None:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = meta.get('kind')
        ws_id = str(meta.get('ws_id') or '')
        zh = self.language == 'zh'
        menu = QMenu(self.space_tree)
        if kind == 'space':
            menu.addAction('新建对话' if zh else 'New conversation', lambda: self._new_conversation_for_ws(ws_id))
            menu.addAction('重命名工作区' if zh else 'Rename workspace', lambda: self._rename_workspace(ws_id))
            menu.addAction('更换目录' if zh else 'Change directory', lambda: self._change_workspace_dir(ws_id))
            menu.addSeparator()
            menu.addAction('删除工作区' if zh else 'Delete workspace', lambda: self._delete_workspace(ws_id))
        elif kind == 'conversation':
            conv_id = str(meta.get('conv_id') or '')
            menu.addAction('新建对话' if zh else 'New conversation', lambda: self._new_conversation_for_ws(ws_id))
            menu.addAction('重命名对话' if zh else 'Rename conversation', lambda: self._rename_conversation(ws_id, conv_id))
            menu.addSeparator()
            menu.addAction('删除对话' if zh else 'Delete conversation', lambda: self._delete_conversation(ws_id, conv_id))
        menu.exec(self.space_tree.viewport().mapToGlobal(pos))

    def _new_conversation_for_ws(self, ws_id: str):
        zh = self.language == 'zh'
        title, ok = QInputDialog.getText(self, '新建对话' if zh else 'New conversation', '对话名称：' if zh else 'Title:')
        if not ok or not title.strip():
            return
        task = self._active_task
        if task and task.get('workspace_id') == str(ws_id):
            self._invalidate_active_task('new conversation')
        ws, conv_id = create_conversation(ws_id, title.strip())
        if not ws:
            return
        if self._workspace_session and self._workspace_session.get('id') == ws_id:
            self._workspace_session = ws
            self._tool_calls = []
            self._render_conversation(ws, self._active_conversation_of(ws))
        self._refresh_space_tree(select_ws_id=ws_id, select_conv_id=conv_id)

    def _rename_workspace(self, ws_id: str):
        ws = load_workspace(ws_id)
        if not ws:
            return
        zh = self.language == 'zh'
        title, ok = QInputDialog.getText(
            self, '重命名工作区' if zh else 'Rename workspace',
            '工作区名称：' if zh else 'Workspace title:',
            text=str(ws.get('title') or ''),
        )
        if not ok or not title.strip():
            return
        updated = update_workspace(ws_id, {'title': title.strip()})
        if updated and self._workspace_session and self._workspace_session.get('id') == ws_id:
            self._workspace_session = updated
        self._refresh_space_tree(select_ws_id=ws_id)

    def _change_workspace_dir(self, ws_id: str):
        zh = self.language == 'zh'
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('agent_workspace', os.path.expanduser('~'))
        path = QFileDialog.getExistingDirectory(
            self, '更换工作目录' if zh else 'Change directory', start,
        )
        if not path:
            return
        remember_dialog_path('agent_workspace', path, is_directory=True)
        current = self._workspace_session or {}
        if current.get('id') == ws_id and os.path.realpath(str(current.get('workspace_dir') or '')) != os.path.realpath(path):
            self._invalidate_active_task('workspace directory changed')
            self._clear_attachments()
        updated = update_workspace(ws_id, {'workspace_dir': path})
        if updated and self._workspace_session and self._workspace_session.get('id') == ws_id:
            self._workspace_session = updated
            self._set_workspace_dir_label(path)
            self._refresh_tree(path)
        self._refresh_space_tree(select_ws_id=ws_id)

    def _delete_workspace(self, ws_id: str):
        ws = load_workspace(ws_id)
        if not ws:
            return
        zh = self.language == 'zh'
        if not confirm_action(
            self,
            '删除工作区' if zh else 'Delete workspace',
            f'确定要删除工作区「{ws.get("title") or "未命名"}」及其所有对话吗？' if zh else 'Delete this workspace and all conversations?',
            danger=True,
        ):
            return
        task = self._active_task
        if task and task.get('workspace_id') == str(ws_id):
            self._invalidate_active_task('workspace deleted')
        delete_workspace(ws_id)
        if self._workspace_session and self._workspace_session.get('id') == ws_id:
            metas = list_workspaces()
            if metas:
                self._select_workspace(str(metas[0].get('id') or ''), select_last_conv=True)
            else:
                self._workspace_session = None
                self.project_tree.clear()
                self._render_conversation({}, None)
                self._set_workspace_dir_label('')
        self._refresh_space_tree()

    def _rename_conversation(self, ws_id: str, conv_id: str):
        ws = load_workspace(ws_id)
        if not ws:
            return
        conv = None
        for c in ws.get('conversations') or []:
            if c.get('id') == conv_id:
                conv = c
                break
        cur_title = str((conv or {}).get('title') or '')
        zh = self.language == 'zh'
        title, ok = QInputDialog.getText(
            self, '重命名对话' if zh else 'Rename conversation',
            '对话名称：' if zh else 'Conversation title:',
            text=cur_title,
        )
        if not ok or not title.strip():
            return
        updated = rename_conversation(ws_id, conv_id, title.strip())
        if updated and self._workspace_session and self._workspace_session.get('id') == ws_id:
            self._workspace_session = updated
        self._refresh_space_tree(select_ws_id=ws_id, select_conv_id=conv_id)

    def _delete_conversation(self, ws_id: str, conv_id: str):
        ws = load_workspace(ws_id)
        if not ws:
            return
        conv = None
        for c in ws.get('conversations') or []:
            if c.get('id') == conv_id:
                conv = c
                break
        cur_title = str((conv or {}).get('title') or '当前对话')
        zh = self.language == 'zh'
        if not confirm_action(
            self,
            '删除对话' if zh else 'Delete conversation',
            f'确定要删除对话「{cur_title}」吗？' if zh else 'Delete this conversation?',
            danger=True,
        ):
            return
        task = self._active_task
        if task and task.get('workspace_id') == str(ws_id) and task.get('conversation_id') == str(conv_id):
            self._invalidate_active_task('conversation deleted')
        delete_conversation(ws_id, conv_id)
        updated = load_workspace(ws_id)
        if updated and self._workspace_session and self._workspace_session.get('id') == ws_id:
            self._workspace_session = updated
            active_c = self._active_conversation_of(updated)
            self._render_conversation(updated, active_c)
        self._refresh_space_tree(select_ws_id=ws_id)

    def _pick_workspace_file_ref(self):
        ws_dir = self._workspace_session.get('workspace_dir') if self._workspace_session else ''
        zh = self.language == 'zh'
        if not ws_dir or not os.path.isdir(ws_dir):
            show_warning(self, '请先绑定工作目录' if zh else 'Bind workspace first', '当前工作区尚未绑定有效工作文件夹。')
            return
        from tools.dialog_paths import remember_dialog_path
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择工作区内文件' if zh else 'Pick workspace file',
            ws_dir, 'All Files (*)',
        )
        if not paths:
            return
        remember_dialog_path('agent_file_ref', paths[0])
        added = False
        for p in paths:
            if not self._is_path_within_workspace(p, ws_dir):
                show_warning(self, '禁止引用工作区外文件', f'文件「{os.path.basename(p)}」超出当前绑定的工作区范围，已拒绝。')
                continue
            rel = os.path.relpath(os.path.realpath(p), os.path.realpath(ws_dir)).replace('\\', '/')
            if rel not in self._file_attachments:
                self._file_attachments.append(rel)
                added = True
        if added:
            self._refresh_attachment_bar()

    def _clear_attachments(self):
        self._file_attachments.clear()
        self._refresh_attachment_bar()

    def _refresh_attachment_bar(self):
        if self._file_attachments:
            chips = ' · '.join(f'📎 {f}' for f in self._file_attachments)
            self.attachment_bar.setText(f'已附加文件引用：{chips}')
            self.attachment_bar.show()
        else:
            self.attachment_bar.setText('')
            self.attachment_bar.hide()

    def _new_conversation(self):
        """在当前空间下新建一个对话。"""
        if not self._workspace_session:
            self._new_workspace()
            return
        self._new_conversation_for_ws(self._workspace_session.get('id'))

    def _new_workspace(self):
        """新建工作台（空间）。"""
        title, ok = QInputDialog.getText(self, '新建工作台', '工作台名称：')
        if not ok or not title.strip():
            return
        self._invalidate_active_task('workspace changed')
        self._clear_attachments()
        ws = empty_workspace(title=title.strip())
        save_workspace(ws)
        self._workspace_session = ws
        self._tool_calls = []
        self._plan_confirm = ws.get('plan_confirm', False)
        self.exec_mode_combo.blockSignals(True)
        self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
        self.exec_mode_combo.blockSignals(False)
        self._update_exec_mode_tooltips()
        self._set_workspace_dir_label('')
        self.project_tree.clear()
        self._render_conversation(ws, self._active_conversation_of(ws))
        self._append_message('assistant',
            f'工作台「{title}」已就绪。请先绑定工作文件夹，然后描述你的任务。')
        self._refresh_space_tree(select_ws_id=ws.get('id'))

    # ── 发送 / Agent 执行 ─────────────────────────────────────────────────

    def _sync_running_state(self):
        zh = self.language == 'zh'
        is_running = self._agent_worker is not None
        if is_running:
            self.send_btn.setText('停止' if zh else 'Stop')
            apply_button(self.send_btn, 'secondary', compact=True)
            self.send_btn.setEnabled(not self._closing)
        else:
            self.send_btn.setText('发送' if zh else 'Send')
            apply_button(self.send_btn, 'primary', compact=True)
            self.send_btn.setEnabled(not self._closing)
        self.send_btn.setMinimumWidth(self.send_btn.sizeHint().width())
        self.model_combo.setEnabled(not is_running and not self._closing)
        self.exec_mode_combo.setEnabled(not is_running and not self._closing)
        self.input.setEnabled(not self._closing)
        self.add_attachment_btn.setEnabled(not is_running and not self._closing)

    def _on_action_clicked(self):
        if self._agent_worker is not None:
            self._stop()
        else:
            self._send()

    def _send(self):
        """工作台发送：启动 ReAct 循环。"""
        if self._closing or self._agent_worker is not None:
            return
        user_text = self.input.toPlainText().strip()
        if not user_text and not self._file_attachments:
            return
        self.input.clear()

        if not self._workspace_session:
            self._new_workspace()
        ws = self._workspace_session
        if not ws:
            return
        ws_dir = str(ws.get('workspace_dir') or '')
        if not ws_dir:
            self._append_message('assistant', '请先绑定工作文件夹，再发起任务。' if self.language == 'zh' else 'Bind a workspace folder before starting a task.')
            return

        attachments = list(self._file_attachments)
        valid_attachments = []
        for rel in attachments:
            ok, resolved, _error = self._validate_workspace_relative_path(rel, ws_dir)
            if ok and os.path.isfile(resolved):
                valid_attachments.append(rel)
        if len(valid_attachments) != len(attachments):
            self._file_attachments = valid_attachments
            self._refresh_attachment_bar()
        if attachments and not valid_attachments and not user_text:
            self._append_message('assistant', '附件已失效，请重新选择工作区内文件。' if self.language == 'zh' else 'The file reference is no longer valid. Pick a workspace file again.')
            return
        if valid_attachments:
            if self.language == 'zh':
                refs_context = (
                    '以下是绑定工作区内的文件路径引用，目前尚未读取文件内容。请先对每个引用调用 read_file 工具获取内容，'
                    '再基于实际读取结果回答；不要声称已读取未成功读取的文件。\n' +
                    '\n'.join(f'- `{rel}`' for rel in valid_attachments)
                )
            else:
                refs_context = (
                    'The following are file path references inside the bound workspace; their contents have not been read yet. '
                    'Call the read_file tool for each reference before answering, and only claim successful reads returned by the tool.\n' +
                    '\n'.join(f'- `{rel}`' for rel in valid_attachments)
                )
            text = f'{user_text}\n\n{refs_context}' if user_text else refs_context
        else:
            text = user_text
        self._clear_attachments()

        conv = self._active_conversation_of(ws)
        if conv is None:
            self._append_message('assistant', '请先新建对话。' if self.language == 'zh' else 'Create a conversation before starting a task.')
            return
        import copy
        messages = copy.deepcopy(conv.get('messages') or [])
        tool_calls = copy.deepcopy(conv.get('tool_calls') or [])
        messages.append({'id': 'wb-user-' + uuid.uuid4().hex, 'role': 'user', 'content': text})
        conv['messages'] = messages
        conv['tool_calls'] = tool_calls
        save_workspace(ws)
        self._render_conversation(ws, conv)

        model_cfg = dict(self._current_model() or {})
        if not model_cfg.get('enabled') or not model_cfg.get('base_url'):
            self._append_message('assistant', '当前模型未启用或未配置 Base URL，请在设置中检查。' if self.language == 'zh' else 'The selected model is disabled or has no Base URL. Check Settings.')
            return

        from tools.agent_core import CancellationToken
        task_id = uuid.uuid4().hex
        cancellation = CancellationToken()
        workspace_id = str(ws.get('id') or '')
        conversation_id = str(conv.get('id') or '')
        profile_id = str(model_cfg.get('id') or '')
        model_adapter = self._build_model_adapter_for_task(model_cfg)
        from tools.agent_core.memory import MemoryBinding
        from tools.agent_core.memory_context import model_profile_revision
        memory_binding = MemoryBinding(
            workspace_id, conversation_id, ws['workspace_revision'],
            conv['conversation_revision'], model_profile_revision(model_cfg),
        )
        self._active_task = {
            'id': task_id,
            'workspace_id': workspace_id,
            'conversation_id': conversation_id,
            'workspace_dir': os.path.realpath(ws_dir),
            'profile_id': profile_id,
            'workspace_revision': ws['workspace_revision'],
            'conversation_revision': conv['conversation_revision'],
            'base_message_count': len(messages),
            'base_tool_call_count': len(tool_calls),
            'user_message': text,
            'cancellation': cancellation,
            'invalidated': False,
        }
        self._clear_context_budget_state()
        if self._bridge is None:
            self._bridge = _WorkbenchBridge(self)
        bridge = self._bridge
        confirm_cb = lambda title, content: bridge.confirm(task_id, title, content, cancellation)
        progress_cb = bridge.progress

        worker = _WorkbenchWorker(
            user_message=text,
            workspace_dir=ws_dir,
            model_cfg=model_cfg,
            messages=messages,
            tool_calls=tool_calls,
            plan_confirm=self._plan_confirm,
            confirm_cb=confirm_cb,
            progress_cb=progress_cb,
            task_id=task_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            cancellation=cancellation,
            model_adapter=model_adapter,
            memory_binding=memory_binding,
            context_memory=copy.deepcopy(conv.get('context_memory')),
        )
        self._agent_worker = worker
        self._set_transient_status('正在分析项目…' if self.language == 'zh' else 'Analyzing project…')
        worker.completed.connect(self._on_agent_completed)
        worker.failed.connect(self._on_agent_failed)
        worker.finished.connect(
            self._on_worker_thread_finished, Qt.ConnectionType.QueuedConnection,
        )
        worker.start()
        self._sync_running_state()

    def _validate_workspace_relative_path(self, relative_path: str, workspace_dir: str):
        """Use the runtime's canonical path validator for tool-bound workspace refs."""
        from tools.agent_runtime import validate_path
        return validate_path(str(relative_path or ''), workspace_dir)

    def _build_model_adapter_for_task(self, model_cfg: dict):
        """Optional host injection seam; production resolves the selected config in runtime."""
        return None

    def _task_is_current(self, task_id: str) -> bool:
        task = self._active_task
        if not task or task.get('id') != task_id or task.get('invalidated') or self._closing:
            return False
        token = task.get('cancellation')
        if token is not None and token.is_cancelled():
            return False
        ws = self._workspace_session or {}
        conv = self._active_conversation_of(ws) if ws else None
        return (
            str(ws.get('id') or '') == task.get('workspace_id')
            and str((conv or {}).get('id') or '') == task.get('conversation_id')
            and os.path.realpath(str(ws.get('workspace_dir') or '')) == task.get('workspace_dir')
        )

    def _cancel_for_context_change(self, workspace_id: str, conversation_id: str, workspace_dir: str):
        task = self._active_task
        if not task:
            return
        target_dir = os.path.realpath(str(workspace_dir or ''))
        if (str(workspace_id or '') != task.get('workspace_id')
                or str(conversation_id or '') != task.get('conversation_id')
                or target_dir != task.get('workspace_dir')):
            self._invalidate_active_task('workbench context changed')

    def _invalidate_active_task(self, reason: str):
        task = self._active_task
        if not task or task.get('invalidated'):
            return
        task['invalidated'] = True
        token = task.get('cancellation')
        if token is not None:
            token.cancel(reason)
        if self._bridge is not None:
            self._bridge.cancel_task(str(task.get('id') or ''))
        self._clear_transient_status()
        self._clear_stream_preview()
        self._sync_running_state()

    def _set_transient_status(self, text: str):
        """显示或更新执行中的瞬态状态指示器，不持久化到消息历史。"""
        if self._closing:
            return
        if self._transient_holder is None:
            from ui.thinking_indicator import ThinkingIndicator
            bubble = QFrame()
            bubble.setObjectName('detail-summary-card')
            bl = QVBoxLayout(bubble)
            bl.setContentsMargins(10, 6, 10, 6)
            indicator = ThinkingIndicator(bubble, text=text)
            indicator.setObjectName('agent-transient-indicator')
            indicator.start()
            bl.addWidget(indicator)

            wrap = QHBoxLayout()
            wrap.addWidget(bubble)
            wrap.addStretch(1)
            holder = QWidget()
            holder.setLayout(wrap)
            self._transient_holder = holder
            self._transient_indicator = indicator

            stretch = self.thread_layout.takeAt(self.thread_layout.count() - 1)
            del stretch
            self.thread_layout.addWidget(holder)
            self.thread_layout.addStretch(1)
            self._sync_thread_empty()
        else:
            if self._transient_indicator is not None:
                self._transient_indicator.set_text(text)
        self._follow_scroll_if_at_bottom()

    def _clear_transient_status(self):
        """清理并销毁瞬态指示器。"""
        if self._transient_holder is not None:
            if self._transient_indicator is not None:
                self._transient_indicator.stop()
            self._transient_holder.deleteLater()
            self._transient_holder = None
            self._transient_indicator = None

    def _stop(self):
        task = self._active_task
        if task and not task.get('invalidated'):
            task['invalidated'] = True
            token = task.get('cancellation')
            if token is not None:
                token.cancel('cancelled')
            if self._bridge is not None:
                self._bridge.cancel_task(str(task.get('id') or ''))
        elif self._agent_worker is not None:
            self._agent_worker.stop()
        self._clear_stream_preview()
        if not self._closing:
            self._set_transient_status('正在停止…' if self.language == 'zh' else 'Stopping…')
        self._sync_running_state()

    def _on_agent_completed(self, task_id: str, final_answer: str, messages: list, tool_calls: list):
        if not self._task_is_current(task_id):
            return
        # Drain transient progress while the worker still owns the current
        # task identity; a fast terminal run may finish before the timer tick.
        self._flush_progress()
        self._clear_transient_status()
        self._clear_stream_preview()
        task = self._active_task
        persisted_messages = list(messages or [])
        user_turn = max((i for i, item in enumerate(persisted_messages)
                         if isinstance(item, dict) and item.get('role') == 'user'
                         and item.get('content') == self._active_task.get('user_message')),
                        default=-1)
        answer_in_current_turn = any(
            isinstance(item, dict) and item.get('role') == 'assistant'
            and item.get('content') == final_answer
            for item in persisted_messages[user_turn + 1:]
        )
        if final_answer and not answer_in_current_turn:
            persisted_messages.append({'id': 'wb-final-' + uuid.uuid4().hex,
                                       'role': 'assistant', 'content': str(final_answer)})
        try:
            committed = commit_conversation_result(
                task['workspace_id'], task['conversation_id'],
                expected_workspace_revision=task['workspace_revision'],
                expected_conversation_revision=task['conversation_revision'],
                messages=persisted_messages, tool_calls=list(tool_calls or []),
                context_memory=getattr(self._agent_worker, 'context_memory', None),
                run_id=task_id, base_message_count=task['base_message_count'],
                base_tool_call_count=task['base_tool_call_count'],
            )
        except (OSError, TypeError, ValueError):
            self._set_transient_status('任务结果保存失败，请保留当前窗口重试。' if self.language == 'zh'
                                       else 'Could not save the result. Keep this window open.')
            return
        ws = committed['workspace']
        if not ws:
            return
        conv = next((item for item in ws.get('conversations') or []
                     if str(item.get('id') or '') == task['conversation_id']), None)
        if conv is None:
            return
        if committed['status'] == 'stale':
            self._set_transient_status('对话已更新；本轮结果已单独保留，未覆盖新记录。' if self.language == 'zh'
                                       else 'Conversation changed. This result was preserved separately.')
        if self._task_is_current(task_id):
            self._workspace_session = ws
            self._render_conversation(ws, conv)
            self._refresh_space_tree(select_ws_id=task['workspace_id'],
                                     select_conv_id=task['conversation_id'])

    def _on_agent_failed(self, task_id: str, error_text: str):
        """工作台 Agent 子线程异常兜底：在主线程复位按钮并提示，绝不闪退。"""
        if not self._task_is_current(task_id):
            return
        self._clear_transient_status()
        self._clear_stream_preview()
        zh = self.language == 'zh'
        self._append_message('assistant',
            (f'⚠️ 工作台任务异常：{error_text}' if zh
             else f'⚠️ Workbench task error: {error_text}'))
        show_error(self, self._title(),
                   '工作台任务执行出错' if zh else 'Workbench task error',
                   error_text)

    def _on_worker_thread_finished(self, task_id: str | None = None,
                                   worker: _WorkbenchWorker | None = None):
        """Drop the QThread reference only after Qt reports its native thread ended."""
        if worker is None:
            worker = self.sender()
        if not task_id:
            task_id = str(getattr(worker, 'task_id', '') or '')
        if self._agent_worker is worker:
            self._agent_worker = None
        if self._active_task and self._active_task.get('id') == task_id:
            self._active_task = None
        self._clear_transient_status()
        self._clear_stream_preview()
        if self._closing:
            if self._progress_timer is not None:
                self._progress_timer.stop()
            if self._bridge is not None:
                self._bridge.cancel_all()
                self._bridge.deleteLater()
                self._bridge = None
            self._shutdown_complete = True
            if self._close_pending:
                QTimer.singleShot(0, self.close)
        self._sync_running_state()

    def agent_is_running(self) -> bool:
        return self._agent_worker is not None

    def agent_shutdown_cleanup(self) -> bool:
        """Request cancellation and report completion without waiting on the GUI thread."""
        if self._shutdown_complete:
            return True
        if not self._closing:
            self._closing = True
            if self._progress_timer is not None:
                self._progress_timer.stop()
            if self._bridge is not None:
                self._bridge.cancel_all()
            task = self._active_task
            if task and task.get('cancellation') is not None:
                task['invalidated'] = True
                task['cancellation'].cancel('application shutdown')
            if self._agent_worker is not None:
                self._agent_worker.stop()
            else:
                if self._bridge is not None:
                    self._bridge.deleteLater()
                    self._bridge = None
                self._shutdown_complete = True
        self._sync_running_state()
        return self._shutdown_complete

    def shutdown(self) -> bool:
        return self.agent_shutdown_cleanup()

    def dispose(self) -> bool:
        return self.agent_shutdown_cleanup()

    def closeEvent(self, event):
        self._close_pending = True
        if self.agent_shutdown_cleanup():
            event.accept()
        else:
            event.ignore()

    def _flush_progress(self):
        if self._bridge is None or self._closing:
            return
        summary_parts = []
        body_parts = []

        def flush_deltas():
            if summary_parts:
                self._append_stream_delta('reasoning_delta', ''.join(summary_parts))
                summary_parts.clear()
            if body_parts:
                self._append_stream_delta('assistant_delta', ''.join(body_parts))
                body_parts.clear()

        for task_id, kind, content in self._bridge.take_progress():
            if not self._task_is_current(task_id):
                continue
            if kind == 'context_budget':
                flush_deltas()
                safe_state = self._parse_context_budget_progress(content)
                if safe_state is not None:
                    self._show_context_budget_state(safe_state)
            elif kind == 'status' and content.startswith('turn_started:'):
                flush_deltas()
                self._reset_stream_preview()
                self._set_transient_status('正在生成回复…' if self.language == 'zh' else 'Generating response…')
            elif kind == 'step':
                flush_deltas()
                self._reset_stream_preview()
                try:
                    step = json.loads(content)
                except (TypeError, ValueError):
                    step = None
                if isinstance(step, dict):
                    self._append_step(step.get('name'), step.get('arguments'))
            elif kind == 'reasoning_delta':
                summary_parts.append(content)
            elif kind == 'assistant_delta':
                body_parts.append(content)
            elif kind in ('tool', 'status'):
                if content == 'run_started':
                    self._set_transient_status('正在分析项目…' if self.language == 'zh' else 'Analyzing project…')
                elif content == 'budget_exhausted':
                    self._set_transient_status('已达到本轮预算上限' if self.language == 'zh' else 'Turn budget limit reached')
                elif content not in ('completed', 'cancelled', 'failed'):
                    self._set_transient_status(content)
        flush_deltas()

    def _ensure_stream_preview(self):
        if self._stream_holder is not None:
            return
        bubble = QFrame()
        bubble.setObjectName('detail-summary-card')
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        summary_title = QLabel('思考摘要' if self.language == 'zh' else 'Reasoning summary')
        summary_title.setObjectName('section-title')
        layout.addWidget(summary_title)
        self._stream_summary = QPlainTextEdit()
        self._stream_summary.setReadOnly(True)
        self._stream_summary.setObjectName('agent-reasoning-summary')
        self._stream_summary.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._stream_summary.setMaximumHeight(100)
        self._stream_summary.setMinimumHeight(36)
        layout.addWidget(self._stream_summary)
        body_title = QLabel('回复正文' if self.language == 'zh' else 'Response')
        body_title.setObjectName('section-title')
        layout.addWidget(body_title)
        self._stream_body = QPlainTextEdit()
        self._stream_body.setReadOnly(True)
        self._stream_body.setObjectName('agent-response-stream')
        self._stream_body.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._stream_body.setMaximumHeight(260)
        self._stream_body.setMinimumHeight(54)
        layout.addWidget(self._stream_body)
        holder = QWidget()
        wrap = QHBoxLayout(holder)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(bubble, 19)
        wrap.addStretch(1)
        self._stream_holder = holder
        self._insert_thread_widget(holder)

    def _append_stream_delta(self, kind: str, content: str):
        if not content:
            return
        self._ensure_stream_preview()
        limit = 512 * 1024
        if kind == 'reasoning_delta':
            remaining = max(0, limit - len(self._stream_summary_text))
            if remaining:
                chunk = content[:remaining]
                self._stream_summary_text += chunk
                cursor = self._stream_summary.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(chunk)
                self._stream_summary.setTextCursor(cursor)
                self._stream_summary.verticalScrollBar().setValue(self._stream_summary.verticalScrollBar().maximum())
        else:
            remaining = max(0, limit - len(self._stream_body_text))
            if remaining:
                chunk = content[:remaining]
                self._stream_body_text += chunk
                cursor = self._stream_body.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(chunk)
                self._stream_body.setTextCursor(cursor)
                self._stream_body.verticalScrollBar().setValue(self._stream_body.verticalScrollBar().maximum())
        self._follow_scroll_if_at_bottom()

    def _reset_stream_preview(self):
        self._stream_summary_text = ''
        self._stream_body_text = ''
        if self._stream_summary is not None:
            self._stream_summary.clear()
        if self._stream_body is not None:
            self._stream_body.clear()

    def _clear_stream_preview(self):
        if self._stream_holder is not None:
            self._stream_holder.deleteLater()
        self._stream_holder = None
        self._stream_summary = None
        self._stream_body = None
        self._reset_stream_preview()

    def _sync_thread_empty(self):
        has_items = any(
            self.thread_layout.itemAt(i).widget() is not None for i in range(self.thread_layout.count())
        )
        if hasattr(self, 'thread_empty'):
            self.thread_empty.setVisible(not has_items)
            self.scroll.setVisible(has_items)

    def _insert_thread_widget(self, widget: QWidget):
        at_bottom = self._is_thread_at_bottom()
        if self.thread_layout.count() and self.thread_layout.itemAt(self.thread_layout.count() - 1).spacerItem():
            stretch = self.thread_layout.takeAt(self.thread_layout.count() - 1)
            del stretch
        self.thread_layout.addWidget(widget)
        self.thread_layout.addStretch(1)
        self._sync_thread_empty()
        if at_bottom:
            self._scroll_to_bottom()

    def _is_thread_at_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _follow_scroll_if_at_bottom(self):
        if self._is_thread_at_bottom():
            self._scroll_to_bottom()

    def _append_step(self, name, arguments):
        """工具调用以一行步骤显示（如“读取 README.md”），不把原始 JSON 摆给用户。"""
        row = QLabel(describe_tool_step(name, arguments))
        row.setObjectName('agent-step-row')
        row.setWordWrap(True)
        row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._insert_thread_widget(row)

    def _append_message(self, role: str, content: str):
        """在工作台中栏追加一条消息气泡，滚动到底。"""
        text = _strip_role_prefix(content)
        bubble = QFrame()
        bubble.setObjectName('agent-user-bubble' if role == 'user' else 'agent-assistant-bubble')
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(2)
        if role != 'user':
            who = QLabel('Agent' if role == 'assistant' else '工具')
            who.setObjectName('agent-bubble-role')
            bl.addWidget(who)
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        if role == 'assistant':
            lbl.setTextFormat(Qt.TextFormat.MarkdownText)
        else:
            lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setText(text)
        bl.addWidget(lbl)

        wrap = QHBoxLayout()
        if role == 'user':
            wrap.addStretch(1)
            wrap.addWidget(bubble, 4)
        else:
            wrap.addWidget(bubble, 19)
            wrap.addStretch(1)

        holder = QWidget()
        holder.setLayout(wrap)

        self._insert_thread_widget(holder)
