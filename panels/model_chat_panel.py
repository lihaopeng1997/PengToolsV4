# -*- coding: utf-8 -*-
"""模型对话：网页式多轮聊天，用于验证内网模型配置。不带入 SQL 控制台上下文。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from config import load_settings, save_settings
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
from ui.page_chrome import make_empty_state, make_page_header


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


class ModelChatPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._worker = None
        self._ping_worker = None
        self._session = None
        self._pending_id = ''
        self._setup_ui()
        self.set_language(language)
        self._reload_models()
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

        top = QHBoxLayout()
        self.model_combo = QComboBox()
        size_pick_combo(self.model_combo)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.ping_btn = QPushButton()
        apply_button(self.ping_btn, 'secondary', compact=True)
        self.ping_btn.clicked.connect(self._ping_current)
        self.ping_status = QLabel()
        self.ping_status.setObjectName('field-hint')
        self.ping_status.setWordWrap(True)
        top.addWidget(self.model_combo)
        top.addWidget(self.ping_btn)
        top.addWidget(self.ping_status, 1)
        root.addLayout(top)

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
