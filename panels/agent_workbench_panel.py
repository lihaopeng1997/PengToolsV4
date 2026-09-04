# -*- coding: utf-8 -*-
"""Agent 工作台：独立面板，绑定项目目录后运行受控 ReAct 任务。

从 ModelChatPanel 的工作台模式完整提取（v3.0 导航重构），
不再依赖聊天上下文，作为"模型 → 工作"子菜单的独立页面。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeyEvent, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from config import load_settings
from tools.agent_store import (
    create_conversation,
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
from ui.page_chrome import make_page_header, make_page_toolbar
from ui.splitter_prefs import install_splitter_prefs


class _WorkbenchBridge(QObject):
    """把子线程里的 confirm/progress 转发到主线程执行，避免 PyQt6 跨线程操作 UI 闪退。

    - confirm 需要同步返回：用 BlockingQueuedConnection 让主线程执行弹窗并阻塞子线程直到返回。
    - progress 走异步信号，不阻塞。
    """

    progressEmitted = pyqtSignal(str, str)   # (role, content)

    def __init__(self, owner: 'AgentWorkbenchPanel'):
        super().__init__()
        self._owner = owner
        self.progressEmitted.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _confirm_slot(self):
        # 在主线程弹确认框，结果存到调用方容器，BlockingQueuedConnection 保证返回前已填好
        from ui.confirm_dialog import confirm_action
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
        if role in ('tool', 'status'):
            self._owner._set_transient_status(content)
        else:
            self._owner._append_message(role, content)


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
                cancel_cb=lambda: self._stopped,
            )
            self.finished.emit(final, msgs, tcs)
        except Exception as exc:
            # 关键：子线程任何异常都必须阻止进程崩溃，转发到主线程显示
            import traceback
            detail = redact_error(str(exc)) or exc.__class__.__name__
            trace = traceback.format_exc()
            self.failed.emit(f'{detail}\n{trace[-1200:]}')


class AgentWorkbenchPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._workspace_session = None   # 当前 workspace dict
        self._tool_calls = []            # 当前任务累计 tool_calls
        self._agent_worker = None        # _WorkbenchWorker 引用
        self._plan_confirm = False       # 始终先确认计划（默认关）
        self._bridge = None              # 跨线程桥（延迟创建）
        self._file_attachments: list[str] = []
        self._transient_holder = None
        self._transient_indicator = None
        self._setup_ui()
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
        header, self.page_title, self.page_subtitle = make_page_header(
            'Agent 工作台',
            '绑定项目目录，运行受控任务（只读写工作文件夹内文件）',
            'workbench',
        )
        root.addWidget(header)

        toolbar, top = make_page_toolbar(divided=True)
        self.model_combo = QComboBox()
        size_pick_combo(self.model_combo)
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

        self.new_btn = QPushButton()
        apply_button(self.new_btn, 'secondary', compact=True)
        self.new_btn.clicked.connect(self._new_workspace)
        top.addWidget(self.new_btn)

        root.addWidget(toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName('agent-workbench-splitter')
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)

        # 左栏：空间 / 对话管理树（支持右键管理）
        left_card = QFrame()
        left_card.setObjectName('dashboard-task-card')
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
        self.dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ws_manage_row.addWidget(self.dir_label, 1)
        left_l.addLayout(ws_manage_row)
        split.addWidget(left_card)

        # 中栏：消息流 + 输入框
        self.center_split = QSplitter(Qt.Orientation.Vertical)
        self.center_split.setChildrenCollapsible(False)
        self.center_split.setHandleWidth(8)

        thread_container = QFrame()
        thread_container.setObjectName('dashboard-task-card')
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
        thread_l.addWidget(self.scroll, 1)
        self.center_split.addWidget(thread_container)

        composer_container = QFrame()
        composer_container.setObjectName('dashboard-task-card')
        composer_l = QVBoxLayout(composer_container)
        composer_l.setContentsMargins(8, 8, 8, 8)
        composer_l.setSpacing(6)

        self.attachment_bar = QLabel()
        self.attachment_bar.setObjectName('field-hint')
        self.attachment_bar.setWordWrap(True)
        self.attachment_bar.hide()
        composer_l.addWidget(self.attachment_bar)

        self.input = QPlainTextEdit()
        self.input.setMinimumHeight(100)
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
        attach_menu = QMenu(self.add_attachment_btn)
        self.new_conv_action = attach_menu.addAction('新建对话', self._new_conversation)
        self.add_file_ref_action = attach_menu.addAction('添加工作区内文件引用', self._pick_workspace_file_ref)
        attach_menu.addSeparator()
        self.clear_ref_action = attach_menu.addAction('清空输入', lambda: self.input.clear())
        self.add_attachment_btn.setMenu(attach_menu)

        self.send_hint = QLabel()
        self.send_hint.setObjectName('field-hint')
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
        self.context_panel.setObjectName('dashboard-task-card')
        context_l = QVBoxLayout(self.context_panel)
        context_l.setContentsMargins(8, 8, 8, 8)
        context_l.setSpacing(4)

        self.context_split = QSplitter(Qt.Orientation.Vertical)
        self.context_split.setChildrenCollapsible(False)

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
            defaults=[220, 680, 280],
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
        ws_dir = self._workspace_session.get('workspace_dir') if self._workspace_session else ''
        bound = (f'已绑定：{ws_dir}' if zh else f'Bound: {ws_dir}') if ws_dir else (
            '（未绑定目录）' if zh else '(No folder bound)'
        )
        self.dir_label.setText(bound)
        self.dir_label.setToolTip(bound)
        self.new_ws_btn.setToolTip('新建工作区' if zh else 'New workspace')
        self.new_btn.setText('新建工作区' if zh else 'New workspace')
        self.space_new_btn.setToolTip('新建对话' if zh else 'New conversation')
        self.context_toggle_btn.setText('项目文件' if zh else 'Files')
        self.context_toggle_btn.setToolTip('显示/隐藏项目文件与预览面板' if zh else 'Toggle project files and preview panel')
        self.space_title.setText('工作区' if zh else 'Workspaces')
        self.project_title.setText('项目文件' if zh else 'Files')
        self.preview_title.setText('预览' if zh else 'Preview')
        self.send_hint.setText('Enter 发送 · Alt+Enter 换行' if zh else 'Enter: send · Alt+Enter: newline')
        self.new_conv_action.setText('新建对话' if zh else 'New conversation')
        self.add_file_ref_action.setText('添加文件引用' if zh else 'Reference file')
        self.clear_ref_action.setText('清空附件' if zh else 'Clear attachments')
        self.input.setPlaceholderText('描述任务…（Enter 发送，Alt+Enter 换行）' if zh else 'Describe task… (Enter to send, Alt+Enter for newline)')
        self._update_exec_mode_options()
        self._sync_running_state()

    def _update_exec_mode_options(self):
        zh = self.language == 'zh'
        cur_val = bool(self._plan_confirm)
        self.exec_mode_combo.blockSignals(True)
        self.exec_mode_combo.clear()
        self.exec_mode_combo.addItem('直接执行' if zh else 'Execute directly', False)
        self.exec_mode_combo.addItem('执行前确认计划' if zh else 'Confirm plan first', True)
        self.exec_mode_combo.setCurrentIndex(1 if cur_val else 0)
        self.exec_mode_combo.blockSignals(False)
        self._update_exec_mode_tooltips()

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
        self._workspace_session = ws
        self._tool_calls = []
        self._plan_confirm = bool(ws.get('plan_confirm', False))
        self.exec_mode_combo.blockSignals(True)
        self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
        self.exec_mode_combo.blockSignals(False)
        self._update_exec_mode_tooltips()
        ws_dir = ws.get('workspace_dir') or ''
        zh = self.language == 'zh'
        bound = (f'已绑定：{ws_dir}' if zh else f'Bound: {ws_dir}') if ws_dir else (
            '（未绑定目录）' if zh else '(No folder bound)'
        )
        self.dir_label.setText(bound)
        self.dir_label.setToolTip(bound)
        if ws_dir:
            self._refresh_tree(ws_dir)
        else:
            self.project_tree.clear()
        conv = self._active_conversation_of(ws)
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
        self._workspace_session['workspace_dir'] = path
        save_workspace(self._workspace_session)
        self.dir_label.setText(path)
        self.dir_label.setToolTip(path)
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
        try:
            size = os.path.getsize(full)
        except OSError:
            return
        if size > 200 * 1024:
            self.preview.setPlainText(f'文件超过 200KB 限制（{size // 1024}KB），拒绝读取。')
            return
        try:
            with open(full, 'rb') as f:
                raw_bytes = f.read()
            from tools.text_file_codec import decode_text_bytes
            res = decode_text_bytes(raw_bytes, filename=os.path.basename(full))
            if not res.get('ok') or res.get('binary'):
                content = f"（二进制文件或未知编码无法作为文本预览，大小: {size} 字节）" if self.language == 'zh' else f"(Binary file cannot be previewed, size: {size} bytes)"
            else:
                content = res.get('text', '')
        except Exception as e:
            content = f'读取失败: {e}'
        self.preview.setPlainText(content)

    # ── 空间（工作台任务）列表树 ─────────────────────────────────────────

    def _refresh_space_tree(self, select_ws_id: str = '', select_conv_id: str = ''):
        """刷新左栏空间树：顶层=工作台，子级=该空间下的对话记录。"""
        self.space_tree.blockSignals(True)
        self.space_tree.clear()
        for meta in list_workspaces():
            ws_id = str(meta.get('id') or '')
            ws_dir = str(meta.get('workspace_dir') or '')
            cur = (' 📁' if ws_dir else '')
            top = QTreeWidgetItem([f"{meta.get('title') or '空间'}{cur}"])
            top.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'space', 'ws_id': ws_id})
            top.setToolTip(0, ws_dir or '（未绑定工作文件夹）')
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

    def _on_space_clicked(self, item: QTreeWidgetItem, _column: int):
        """点击空间/对话节点：加载到中栏并设为 active。"""
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = meta.get('kind')
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
            self.dir_label.setText(ws_dir or ('（未绑定目录）' if self.language == 'zh' else '(No folder bound)'))
            self.dir_label.setToolTip(ws_dir or ('（未绑定目录）' if self.language == 'zh' else '(No folder bound)'))
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
            self.dir_label.setText(ws_dir or ('（未绑定目录）' if self.language == 'zh' else '(No folder bound)'))
            self.dir_label.setToolTip(ws_dir or ('（未绑定目录）' if self.language == 'zh' else '(No folder bound)'))
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
                child.widget().deleteLater()
        if not conv:
            self.input.clear()
            return
        for msg in conv.get('messages') or []:
            role = str(msg.get('role') or '')
            content = str(msg.get('content') or '')
            if role in ('user', 'assistant') and content:
                self._append_message(role, content)

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
        if self._agent_worker is not None:
            show_warning(self, self._title(), '当前任务运行中，请先停止。')
            return
        zh = self.language == 'zh'
        title, ok = QInputDialog.getText(self, '新建对话' if zh else 'New conversation', '对话名称：' if zh else 'Title:')
        if not ok or not title.strip():
            return
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
        updated = update_workspace(ws_id, {'workspace_dir': path})
        if updated and self._workspace_session and self._workspace_session.get('id') == ws_id:
            self._workspace_session = updated
            self.dir_label.setText(path)
            self.dir_label.setToolTip(path)
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
        delete_workspace(ws_id)
        if self._workspace_session and self._workspace_session.get('id') == ws_id:
            metas = list_workspaces()
            if metas:
                self._select_workspace(str(metas[0].get('id') or ''), select_last_conv=True)
            else:
                self._workspace_session = None
                self.project_tree.clear()
                self._render_conversation({}, None)
                self.dir_label.setText('（未绑定目录）' if zh else '(No folder bound)')
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
        ws = empty_workspace(title=title.strip())
        save_workspace(ws)
        self._workspace_session = ws
        self._tool_calls = []
        self._plan_confirm = ws.get('plan_confirm', False)
        self.exec_mode_combo.blockSignals(True)
        self.exec_mode_combo.setCurrentIndex(1 if self._plan_confirm else 0)
        self.exec_mode_combo.blockSignals(False)
        self._update_exec_mode_tooltips()
        self.dir_label.setText('（未绑定目录）' if self.language == 'zh' else '(No folder bound)')
        self.dir_label.setToolTip('（未绑定目录）' if self.language == 'zh' else '(No folder bound)')
        self.project_tree.clear()
        self._render_conversation(ws, self._active_conversation_of(ws))
        self._append_message('assistant',
            f'工作台「{title}」已就绪。请先绑定工作文件夹，然后描述你的任务。')
        self._refresh_space_tree(select_ws_id=ws.get('id'))

    # ── 发送 / Agent 执行 ─────────────────────────────────────────────────

    def _sync_running_state(self):
        zh = self.language == 'zh'
        is_running = self._agent_worker is not None and self._agent_worker.isRunning()
        if is_running:
            self.send_btn.setText('停止' if zh else 'Stop')
            apply_button(self.send_btn, 'secondary', compact=True)
            self.send_btn.setEnabled(True)
        else:
            self.send_btn.setText('发送' if zh else 'Send')
            apply_button(self.send_btn, 'primary', compact=True)
            self.send_btn.setEnabled(True)

    def _on_action_clicked(self):
        if self._agent_worker is not None and self._agent_worker.isRunning():
            self._stop()
        else:
            self._send()

    def _send(self):
        """工作台发送：启动 ReAct 循环。"""
        if self._agent_worker is not None and self._agent_worker.isRunning():
            return
        user_text = self.input.toPlainText().strip()
        if not user_text and not self._file_attachments:
            return
        self.input.clear()

        # 将附件作为明确 task context
        if self._file_attachments:
            refs_context = "已附加工作区文件引用：\n" + "\n".join(f"- `{f}`" for f in self._file_attachments)
            text = f"{user_text}\n\n{refs_context}" if user_text else refs_context
            self._clear_attachments()
        else:
            text = user_text

        if not self._workspace_session:
            self._new_workspace()

        ws_dir = self._workspace_session.get('workspace_dir') or ''
        if not ws_dir:
            self._append_message('assistant', '请先绑定工作文件夹，再发起任务。')
            return

        conv = self._active_conversation_of(self._workspace_session)
        if conv is None:
            self._append_message('assistant', '请先新建对话。')
            return
        messages = list(conv.get('messages') or [])
        tool_calls = list(conv.get('tool_calls') or [])
        messages.append({'id': 'wb-user-' + str(hash(text)), 'role': 'user', 'content': text})
        conv['messages'] = messages
        conv['tool_calls'] = tool_calls
        save_workspace(self._workspace_session)
        self._render_conversation(self._workspace_session, conv)

        model_cfg = self._current_model() or {}
        if not model_cfg.get('enabled') or not model_cfg.get('base_url'):
            self._append_message('assistant', '当前模型未启用或未配置 Base URL，请在设置中检查。')
            return

        if self._bridge is None:
            self._bridge = _WorkbenchBridge(self)
        confirm_cb = self._bridge.confirm
        progress_cb = self._bridge.progress

        self._agent_worker = _WorkbenchWorker(
            user_message=text,
            workspace_dir=ws_dir,
            model_cfg=model_cfg,
            messages=messages,
            tool_calls=tool_calls,
            plan_confirm=self._plan_confirm,
            confirm_cb=confirm_cb,
            progress_cb=progress_cb,
        )
        self._set_transient_status('正在分析项目…' if self.language == 'zh' else 'Analyzing project…')
        self._agent_worker.finished.connect(self._on_agent_done)
        self._agent_worker.failed.connect(self._on_agent_failed)
        self._agent_worker.start()
        self._sync_running_state()

    def _set_transient_status(self, text: str):
        """显示或更新执行中的瞬态状态指示器，不持久化到消息历史。"""
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
        else:
            if self._transient_indicator is not None:
                self._transient_indicator.set_text(text)
        QApplication.processEvents()
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        )

    def _clear_transient_status(self):
        """清理并销毁瞬态指示器。"""
        if self._transient_holder is not None:
            if self._transient_indicator is not None:
                self._transient_indicator.stop()
            self._transient_holder.deleteLater()
            self._transient_holder = None
            self._transient_indicator = None

    def _stop(self):
        if self._agent_worker:
            self._agent_worker.stop()
        self._clear_transient_status()
        self._sync_running_state()

    def _on_agent_done(self, final_answer: str, messages: list, tool_calls: list):
        self._clear_transient_status()
        self._agent_worker = None
        self._sync_running_state()

        if self._workspace_session:
            conv = self._active_conversation_of(self._workspace_session)
            if conv:
                if messages:
                    conv['messages'] = list(messages)
                if tool_calls:
                    conv['tool_calls'] = list(tool_calls)
                save_workspace(self._workspace_session)
                self._refresh_space_tree(select_ws_id=self._workspace_session.get('id'),
                                         select_conv_id=conv.get('id'))

        if final_answer:
            self._append_message('assistant', final_answer)

    def _on_agent_failed(self, error_text: str):
        """工作台 Agent 子线程异常兜底：在主线程复位按钮并提示，绝不闪退。"""
        self._clear_transient_status()
        self._agent_worker = None
        self._sync_running_state()
        zh = self.language == 'zh'
        self._append_message('assistant',
            (f'⚠️ 工作台任务异常：{error_text}' if zh
             else f'⚠️ Workbench task error: {error_text}'))
        show_error(self, self._title(),
                   '工作台任务执行出错' if zh else 'Workbench task error',
                   error_text)

    def _append_message(self, role: str, content: str):
        """在工作台中栏追加一条消息气泡，滚动到底。"""
        bubble = QFrame()
        bubble.setObjectName('detail-summary-card' if role == 'assistant' else 'page-filter-bar')
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(10, 8, 10, 8)
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if role == 'tool':
            prefix = '🔧 '
        elif role == 'assistant':
            prefix = '🤖 '
        else:
            prefix = '👤 '
        lbl.setText(prefix + content)
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

        stretch = self.thread_layout.takeAt(self.thread_layout.count() - 1)
        del stretch
        self.thread_layout.addWidget(holder)
        self.thread_layout.addStretch(1)
        QApplication.processEvents()
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        )
