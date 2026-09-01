# -*- coding: utf-8 -*-
"""模型对话：网页式多轮聊天，用于验证内网模型配置。不带入 SQL 控制台上下文。

v3.0 导航重构：工作台模式已提取到 panels/agent_workbench_panel.py，
本面板为纯聊天模式（会话列表 + 多轮对话 + 模型选择 + 数据库上下文）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

import os

from config import load_settings, save_settings
from tools import harness_project
from tools.chat_intent import detect_take_data_intent
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
        top.addWidget(self.model_combo)
        top.addWidget(self.ping_btn)
        top.addWidget(self.skill_btn)
        top.addWidget(self.conn_combo)
        top.addWidget(self.ping_status, 1)
        root.addWidget(toolbar)

        # 聊天主内容区（左右 split）
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
        right_l.setSpacing(0)

        self.chat_vsplit = QSplitter(Qt.Orientation.Vertical)
        self.chat_vsplit.setChildrenCollapsible(False)

        top_chat_widget = QWidget()
        top_chat_l = QVBoxLayout(top_chat_widget)
        top_chat_l.setContentsMargins(0, 0, 0, 0)
        top_chat_l.setSpacing(4)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_host = QWidget()
        self.thread_layout = QVBoxLayout(self.thread_host)
        self.thread_layout.setContentsMargins(8, 8, 8, 8)
        self.thread_layout.addStretch(1)
        self.scroll.setWidget(self.thread_host)
        top_chat_l.addWidget(self.scroll, 1)

        self.empty = make_empty_state('还没有对话', '新建会话后即可向已启用的内网模型发消息')
        top_chat_l.addWidget(self.empty)
        self.trim_hint = QLabel()
        self.trim_hint.setObjectName('field-hint')
        self.trim_hint.hide()
        top_chat_l.addWidget(self.trim_hint)

        self.chat_vsplit.addWidget(top_chat_widget)

        composer_container = QWidget()
        composer_l = QVBoxLayout(composer_container)
        composer_l.setContentsMargins(0, 4, 0, 0)
        composer_l.setSpacing(6)

        self.input = QPlainTextEdit()
        self.input.setMinimumHeight(100)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.installEventFilter(self)
        composer_l.addWidget(self.input, 1)

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
        composer_l.addLayout(send_row)

        self.chat_vsplit.addWidget(composer_container)

        install_splitter_prefs(
            self.chat_vsplit,
            defaults=[520, 180],
            page_id='model-chat',
            tab_id='composer_v2',
            min_sizes=[200, 120],
            accessible_name='模型对话与输入区分隔',
        )

        right_l.addWidget(self.chat_vsplit, 1)
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
        root.addWidget(split, 1)

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

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.page_subtitle, low_height)

    def _title(self) -> str:
        return '模型对话' if self.language == 'zh' else 'Model Chat'

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
                wrap.addWidget(frame, 4)
            else:
                wrap.addWidget(frame, 19)
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
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('model_chat_skill_md')
        path, _filter = QFileDialog.getOpenFileName(
            self, '选择 .md skill 文件' if zh else 'Pick .md skill file',
            start, 'Markdown (*.md)',
        )
        if not path:
            return
        remember_dialog_path('model_chat_skill_md', path)
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
