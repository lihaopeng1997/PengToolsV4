# -*- coding: utf-8 -*-
"""Agent 工作台：独立面板，绑定项目目录后运行受控 ReAct 任务。

从 ModelChatPanel 的工作台模式完整提取（v3.0 导航重构），
不再依赖聊天上下文，作为"模型 → 工作"子菜单的独立页面。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from config import load_settings
from tools.agent_store import (
    create_conversation,
    empty_workspace,
    list_workspaces,
    load_workspace,
    save_workspace,
    set_active_conversation,
)
from tools.intranet_llm import list_enabled_items
from tools.sql_guard import redact_error
from ui.confirm_dialog import show_error, show_warning
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
        self.dir_label.setWordWrap(False)
        self.plan_check = QCheckBox()
        self.plan_check.setObjectName('field-hint')
        self.plan_check.stateChanged.connect(self._on_plan_confirm_changed)
        self.new_btn = QPushButton()
        apply_button(self.new_btn, 'secondary', compact=True)
        self.new_btn.clicked.connect(self._new_workspace)
        top.addWidget(self.model_combo)
        top.addWidget(self.dir_btn)
        top.addWidget(self.dir_label, 1)
        top.addWidget(self.plan_check)
        top.addWidget(self.new_btn)
        root.addWidget(toolbar)

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
        self.space_new_btn = QPushButton('+')
        self.space_new_btn.setToolTip('新建对话')
        self.space_new_btn.setFixedSize(22, 22)
        apply_button(self.space_new_btn, 'ghost', compact=True)
        self.space_new_btn.clicked.connect(self._new_conversation)
        space_head.addWidget(space_title)
        space_head.addStretch(1)
        space_head.addWidget(self.space_new_btn)
        left_l.addLayout(space_head)
        self.space_tree = QTreeWidget()
        self.space_tree.setHeaderHidden(True)
        self.space_tree.setIndentation(14)
        self.space_tree.itemClicked.connect(self._on_space_clicked)
        left_l.addWidget(self.space_tree, 3)

        # 项目文件树
        left_l.addWidget(QLabel('项目文件'))
        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderHidden(True)
        self.project_tree.setIndentation(14)
        self.project_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        left_l.addWidget(self.project_tree, 1)
        split.addWidget(left)

        # 中栏：对话流 + 输入
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_host = QWidget()
        self.thread_layout = QVBoxLayout(self.thread_host)
        self.thread_layout.setContentsMargins(8, 8, 8, 8)
        self.thread_layout.addStretch(1)
        self.scroll.setWidget(self.thread_host)
        mid.addWidget(self.scroll, 1)
        self.input = QPlainTextEdit()
        self.input.setMinimumHeight(180)
        self.input.setMaximumHeight(300)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.installEventFilter(self)
        mid.addWidget(self.input)
        send_row = QHBoxLayout()
        self.send_btn = QPushButton()
        apply_button(self.send_btn, 'primary', compact=True)
        self.send_btn.clicked.connect(self._send)
        self.stop_btn = QPushButton()
        apply_button(self.stop_btn, 'secondary', compact=True)
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        send_row.addWidget(self.send_btn, 1)
        send_row.addWidget(self.stop_btn)
        mid.addLayout(send_row)
        mid_widget = QWidget()
        mid_widget.setLayout(mid)
        split.addWidget(mid_widget)

        # 右栏：预览（文件内容 / diff）
        right = QFrame()
        right.setObjectName('dashboard-task-card')
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(8, 8, 8, 8)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName('detail-text')
        right_l.addWidget(QLabel('预览'))
        right_l.addWidget(self.preview, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        install_splitter_prefs(
            split,
            defaults=[200, 560, 240],
            page_id='agent-workbench',
            tab_id='main',
            min_sizes=[120, 280, 160],
            accessible_name='工作台三栏分隔',
        )
        root.addWidget(split, 1)
        self._split = split

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
        self.page_title.setText('Agent 工作台' if zh else 'Agent Workbench')
        self.page_subtitle.setText(
            '绑定项目目录，运行受控任务（只读写工作文件夹内文件）' if zh else
            'Bind a project folder and run controlled tasks (workspace files only)'
        )
        self.dir_btn.setText('绑定目录' if zh else 'Bind folder')
        self.dir_label.setText(
            self._workspace_session.get('workspace_dir') if self._workspace_session else '（未绑定目录）'
        )
        self.plan_check.setText('确认计划' if zh else 'Confirm plan')
        self.new_btn.setText('新建工作台' if zh else 'New workspace')
        self.space_new_btn.setToolTip('新建对话' if zh else 'New conversation')
        self.send_btn.setText('发送' if zh else 'Send')
        self.stop_btn.setText('停止' if zh else 'Stop')
        self.input.setPlaceholderText('描述任务…' if zh else 'Describe task…')

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
        self.plan_check.setChecked(self._plan_confirm)
        self.dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
        if ws.get('workspace_dir'):
            self._refresh_tree(ws['workspace_dir'])
        else:
            self.project_tree.clear()
        conv = self._active_conversation_of(ws)
        self._render_conversation(ws, conv)
        self._refresh_space_tree(select_ws_id=ws.get('id'),
                                 select_conv_id=conv.get('id') if select_last_conv else '')

    def _bind_directory(self):
        """绑定/更换工作文件夹。未建空间时自动先建一个并绑定。"""
        path = QFileDialog.getExistingDirectory(
            self, '选择工作文件夹', os.path.expanduser('~'),
        )
        if not path:
            return
        if not self._workspace_session:
            ws = empty_workspace(title='新工作台')
            save_workspace(ws)
            self._workspace_session = ws
            self._tool_calls = []
            self._plan_confirm = ws.get('plan_confirm', False)
            self.plan_check.setChecked(self._plan_confirm)
        self._workspace_session['workspace_dir'] = path
        save_workspace(self._workspace_session)
        self.dir_label.setText(path)
        self._refresh_tree(path)
        self._refresh_space_tree(select_ws_id=self._workspace_session.get('id'))

    def _refresh_tree(self, workspace_dir: str):
        """刷新项目文件树（白名单扩展名，懒加载顶层）。"""
        self.project_tree.clear()
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
                    QTreeWidgetItem(item)
                    item.setExpanded(False)
                else:
                    item.setToolTip(0, full)

        root = QTreeWidgetItem(self.project_tree)
        root.setText(0, os.path.basename(workspace_dir) + '/')
        root.setData(0, Qt.ItemDataRole.UserRole, workspace_dir)
        add_items(root, workspace_dir, depth=0)
        self.project_tree.insertTopLevelItem(0, root)
        root.setExpanded(True)

    def _on_tree_double_click(self, item: QTreeWidgetItem, column: int):
        """双击文件 → 在预览区显示内容（限白名单且 ≤200KB）。"""
        full = item.data(0, Qt.ItemDataRole.UserRole)
        if not full or os.path.isdir(full):
            return
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
        try:
            size = os.path.getsize(full)
        except OSError:
            return
        if size > 200 * 1024:
            self.preview.setPlainText(f'文件超过 200KB 限制（{size // 1024}KB），拒绝读取。')
            return
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            content = f'读取失败: {e}'
        self.preview.setPlainText(content)

    def _on_plan_confirm_changed(self, state):
        self._plan_confirm = bool(state)
        if self._workspace_session:
            self._workspace_session['plan_confirm'] = self._plan_confirm
            save_workspace(self._workspace_session)

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
            self.plan_check.setChecked(self._plan_confirm)
            self.dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
            if ws.get('workspace_dir'):
                self._refresh_tree(ws['workspace_dir'])
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
            self.plan_check.setChecked(self._plan_confirm)
            self.dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
            if ws.get('workspace_dir'):
                self._refresh_tree(ws['workspace_dir'])
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

    def _new_conversation(self):
        """在当前空间下新建一个对话。"""
        if not self._workspace_session:
            self._new_workspace()
            return
        if self._agent_worker is not None:
            show_warning(self, self._title(), '当前任务运行中，请先停止。')
            return
        title, ok = QInputDialog.getText(self, '新建对话', '对话名称：')
        if not ok or not title.strip():
            return
        ws, conv_id = create_conversation(self._workspace_session.get('id'), title.strip())
        if not ws:
            return
        self._workspace_session = ws
        self._tool_calls = []
        self._render_conversation(ws, self._active_conversation_of(ws))
        self._refresh_space_tree(select_ws_id=ws.get('id'), select_conv_id=conv_id)

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
        self.plan_check.setChecked(self._plan_confirm)
        self.dir_label.setText(ws.get('workspace_dir') or '（未绑定目录）')
        if ws.get('workspace_dir'):
            self._refresh_tree(ws['workspace_dir'])
        else:
            self.project_tree.clear()
        self._render_conversation(ws, self._active_conversation_of(ws))
        self._append_message('assistant',
            f'工作台「{title}」已就绪。请先绑定工作文件夹，然后描述你的任务。')
        self._refresh_space_tree(select_ws_id=ws.get('id'))

    # ── 发送 / Agent 执行 ─────────────────────────────────────────────────

    def _send(self):
        """工作台发送：启动 ReAct 循环。"""
        if self._agent_worker is not None:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()

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
        self._append_message('user', text)

        model_cfg = self._current_model() or {}
        if not model_cfg.get('enabled') or not model_cfg.get('base_url'):
            self._append_message('assistant', '当前模型未启用或未配置 Base URL，请在设置中检查。')
            return

        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

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
        self._agent_worker.finished.connect(self._on_agent_done)
        self._agent_worker.failed.connect(self._on_agent_failed)
        self._agent_worker.start()

    def _stop(self):
        if self._agent_worker:
            self._agent_worker.stop()
            self.stop_btn.setEnabled(False)

    def _on_agent_done(self, final_answer: str, messages: list, tool_calls: list):
        self._agent_worker = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

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
        self._agent_worker = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
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
        bl.setContentsMargins(8, 6, 8, 6)
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        prefix = '🤖 ' if role == 'assistant' else '👤 '
        lbl.setText(prefix + content)
        bl.addWidget(lbl)
        stretch = self.thread_layout.takeAt(self.thread_layout.count() - 1)
        del stretch
        self.thread_layout.addWidget(bubble)
        self.thread_layout.addStretch(1)
        QApplication.processEvents()
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        )
