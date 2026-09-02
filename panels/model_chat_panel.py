# -*- coding: utf-8 -*-
"""模型对话：网页式多轮聊天，用于验证内网模型配置。纯聊天交互。

只保留：模型 / 会话 / 消息 / 附件 / composer。
"""

from __future__ import annotations

import base64
import mimetypes
import os

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QToolButton, QToolTip, QVBoxLayout, QWidget,
)

from config import load_settings, save_settings
from tools import harness_project
from tools.intranet_llm import list_enabled_items, ping_model
from tools.model_chat_store import (
    append_message, create_session, delete_session, load_index,
    load_session, rename_session, search_sessions, trim_messages_for_request,
    update_message,
)
from tools.sql_guard import redact_error
from ui.confirm_dialog import confirm_action, show_error, show_warning
from ui.design_system import apply_button
from ui.field_metrics import size_line, size_pick_combo
from ui.page_chrome import make_empty_state, make_page_header, make_page_toolbar
from ui.splitter_prefs import install_splitter_prefs

MAX_TEXT_FILE_SIZE = 256 * 1024  # 256 KB
MAX_IMAGE_FILE_SIZE = 4 * 1024 * 1024  # 4 MB
MAX_ATTACHMENTS = 5
CHAT_BUBBLE_RATIO = 0.72
CHAT_BUBBLE_RATIO_CAP = 0.78


def chat_bubble_max_width(viewport_width: int, *, ratio: float = CHAT_BUBBLE_RATIO) -> int:
    """内容驱动气泡上限：约 72% 视口，且不超过 78%，并保留左右边距。"""
    vw = max(1, int(viewport_width or 0))
    capped = min(CHAT_BUBBLE_RATIO_CAP, max(0.45, float(ratio)))
    return max(120, min(int(vw * capped), vw - 24))


def format_chat_clock(value) -> str:
    text = str(value or '').strip()
    if 'T' in text:
        clock = text.split('T', 1)[1]
        return clock[:5]
    if len(text) >= 5 and ':' in text:
        return text[-5:]
    return text


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
        self._is_running = False
        self._text_attachments: list[dict] = []
        self._image_attachments: list[dict] = []
        self._bubble_width_lock = False
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
        self.ping_status = QLabel()
        self.ping_status.setObjectName('field-hint')
        self.ping_status.setWordWrap(True)
        top.addWidget(self.model_combo)
        top.addWidget(self.ping_btn)
        top.addWidget(self.skill_btn)
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

        # 附件状态栏
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
        self.send_hint = QLabel()
        self.send_hint.setObjectName('field-hint')

        # "+" 附件菜单按钮
        self.add_attachment_btn = QToolButton()
        self.add_attachment_btn.setText('+')
        self.add_attachment_btn.setToolTip('添加附件（文件/图片）')
        self.add_attachment_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        apply_button(self.add_attachment_btn, 'ghost', compact=True)
        attach_menu = QMenu(self.add_attachment_btn)
        self.add_file_action = attach_menu.addAction('添加文本文件', self._pick_text_file)
        self.add_img_action = attach_menu.addAction('添加图片', self._pick_image_file)
        attach_menu.addSeparator()
        self.clear_attach_action = attach_menu.addAction('清空附件', self._clear_attachments)
        self.add_attachment_btn.setMenu(attach_menu)

        self.retry_btn = QPushButton()
        apply_button(self.retry_btn, 'ghost', compact=True)
        self.retry_btn.clicked.connect(self._regenerate)

        self.send_btn = QPushButton()
        apply_button(self.send_btn, 'primary', compact=True)
        self.send_btn.clicked.connect(self._on_action_clicked)

        send_row.addWidget(self.add_attachment_btn)
        send_row.addWidget(self.send_hint, 1)
        send_row.addWidget(self.retry_btn)
        send_row.addWidget(self.send_btn)
        composer_l.addLayout(send_row)

        self.chat_vsplit.addWidget(composer_container)

        install_splitter_prefs(
            self.chat_vsplit,
            defaults=[520, 180],
            page_id='model-chat',
            tab_id='composer_v3',
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
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                modifiers = event.modifiers()
                # Alt+Enter 或 Shift+Enter: 换行
                if modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier):
                    return False
                # Enter 或 Ctrl+Enter: 发送
                if not event.isAutoRepeat():
                    self._on_action_clicked()
                    return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_bubble_widths()

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('模型对话' if zh else 'Model Chat')
        self.page_subtitle.setText('连续对话并验证内网模型配置' if zh else 'Multi-turn chat & intranet model verification')
        self.ping_btn.setText('连通性测试' if zh else 'Ping')
        self.skill_btn.setText('skill 管理' if zh else 'Skills')
        self.new_btn.setText('新建会话' if zh else 'New session')
        self.rename_btn.setText('重命名' if zh else 'Rename')
        self.delete_btn.setText('删除' if zh else 'Delete')
        self.search.setPlaceholderText('搜索会话标题/内容' if zh else 'Search sessions')
        self.input.setPlaceholderText('输入消息…（Enter 发送，Alt+Enter 换行）' if zh else 'Type message… (Enter to send, Alt+Enter for newline)')
        self.retry_btn.setText('重新生成' if zh else 'Regenerate')
        self.send_hint.setText('Enter 发送 · Alt+Enter 换行' if zh else 'Enter: send · Alt+Enter: newline')
        self.banner_text.setText('内网模型响应仅供参考，请勿用于非授权环境' if zh else 'Intranet model output is for reference only.')
        self.banner_close.setText('我知道了' if zh else 'Dismiss')
        self.empty.findChild(QLabel).setText('还没有对话' if zh else 'No conversation yet')
        self.add_file_action.setText('添加文本文件' if zh else 'Add text file')
        self.add_img_action.setText('添加图片' if zh else 'Add image')
        self.clear_attach_action.setText('清空附件' if zh else 'Clear attachments')
        self._sync_running_state()

    def _sync_running_state(self):
        zh = self.language == 'zh'
        if self._is_running:
            self.send_btn.setText('停止' if zh else 'Stop')
            apply_button(self.send_btn, 'secondary', compact=True)
            self.retry_btn.setEnabled(False)
        else:
            self.send_btn.setText('发送' if zh else 'Send')
            apply_button(self.send_btn, 'primary', compact=True)
            self.retry_btn.setEnabled(self._session is not None)
            self._sync_send_enabled()

    def _on_action_clicked(self):
        if self._is_running:
            self._stop()
        else:
            self._send()

    def _pick_text_file(self):
        zh = self.language == 'zh'
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('model_chat_attachment')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择文本文件' if zh else 'Pick text files',
            start, 'Text Files (*.txt *.sql *.py *.json *.md *.csv *.log *.yaml *.yml *.xml *.sh *.conf *.env);;All Files (*)',
        )
        if not paths:
            return
        remember_dialog_path('model_chat_attachment', paths[0])
        for p in paths:
            if len(self._text_attachments) + len(self._image_attachments) >= MAX_ATTACHMENTS:
                show_warning(self, '附件数量超限', f'最多添加 {MAX_ATTACHMENTS} 个附件。')
                break
            try:
                sz = os.path.getsize(p)
                if sz > MAX_TEXT_FILE_SIZE:
                    show_warning(self, '文件过大', f'文件「{os.path.basename(p)}」超过 {MAX_TEXT_FILE_SIZE // 1024} KB 限制。')
                    continue
                with open(p, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                self._text_attachments.append({'name': os.path.basename(p), 'content': content})
            except Exception as exc:
                show_warning(self, '读取失败', str(exc))
        self._refresh_attachment_bar()

    def _pick_image_file(self):
        zh = self.language == 'zh'
        model = self._current_model()
        if not model or not bool(model.get('supports_vision', False)):
            show_warning(
                self,
                '当前模型未开启视觉能力' if zh else 'Vision not supported',
                '当前选中的内网模型未配置或不支持图片识别能力。' if zh else 'The selected model does not support image input.',
            )
            return
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('model_chat_image')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择图片' if zh else 'Pick images',
            start, 'Image Files (*.png *.jpg *.jpeg *.webp *.gif)',
        )
        if not paths:
            return
        remember_dialog_path('model_chat_image', paths[0])
        for p in paths:
            if len(self._text_attachments) + len(self._image_attachments) >= MAX_ATTACHMENTS:
                show_warning(self, '附件数量超限', f'最多添加 {MAX_ATTACHMENTS} 个附件。')
                break
            try:
                sz = os.path.getsize(p)
                if sz > MAX_IMAGE_FILE_SIZE:
                    show_warning(self, '图片过大', f'图片「{os.path.basename(p)}」超过 {MAX_IMAGE_FILE_SIZE // (1024*1024)} MB 限制。')
                    continue
                mime, _ = mimetypes.guess_type(p)
                mime = mime or 'image/png'
                with open(p, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('ascii')
                data_uri = f'data:{mime};base64,{b64}'
                self._image_attachments.append({'name': os.path.basename(p), 'data_uri': data_uri, 'mime': mime})
            except Exception as exc:
                show_warning(self, '读取失败', str(exc))
        self._refresh_attachment_bar()

    def _clear_attachments(self):
        self._text_attachments.clear()
        self._image_attachments.clear()
        self._refresh_attachment_bar()

    def _refresh_attachment_bar(self):
        items = []
        for f in self._text_attachments:
            items.append(f'📄 {f["name"]}')
        for img in self._image_attachments:
            items.append(f'🖼️ {img["name"]}')
        if items:
            self.attachment_bar.setText('已附加：' + ' · '.join(items))
            self.attachment_bar.show()
        else:
            self.attachment_bar.setText('')
            self.attachment_bar.hide()
        self._sync_send_enabled()

    def _reload_models(self):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        items = list_enabled_items()
        for it in items:
            self.model_combo.addItem(f"{it.get('name') or ''} ({it.get('model') or ''})", it)
        settings = load_settings()
        last_id = settings.get('model_chat_last_model_id')
        if last_id:
            for idx in range(self.model_combo.count()):
                data = self.model_combo.itemData(idx)
                if isinstance(data, dict) and data.get('id') == last_id:
                    self.model_combo.setCurrentIndex(idx)
                    break
        self.model_combo.blockSignals(False)
        self._sync_send_enabled()

    def _current_model(self) -> dict | None:
        data = self.model_combo.currentData()
        return data if isinstance(data, dict) else None

    def _on_model_changed(self):
        model = self._current_model()
        if model:
            settings = load_settings()
            settings['model_chat_last_model_id'] = str(model.get('id') or '')
            save_settings(settings)
        self._sync_send_enabled()

    def _sync_send_enabled(self):
        has_model = self._current_model() is not None
        has_content = bool(self.input.toPlainText().strip()) or bool(self._text_attachments) or bool(self._image_attachments)
        if not self._is_running:
            self.send_btn.setEnabled(has_model and has_content)

    def _dismiss_banner(self):
        self.banner.hide()
        settings = load_settings()
        settings['model_chat_banner_dismissed'] = True
        save_settings(settings)

    def _maybe_show_banner(self):
        settings = load_settings()
        self.banner.setVisible(not bool(settings.get('model_chat_banner_dismissed', False)))

    def _reload_sessions(self):
        q = self.search.text().strip()
        sessions = search_sessions(q) if q else load_index()
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for item in sessions:
            title = item.get('title') or '未命名会话'
            model = item.get('model') or ''
            count = item.get('message_count', 0)
            lw = QListWidgetItem(f"{title} ({count})\n{model}")
            lw.setData(Qt.ItemDataRole.UserRole, item.get('id'))
            self.session_list.addItem(lw)
        self.session_list.blockSignals(False)
        self._highlight_active_session()

    def _highlight_active_session(self):
        if not self._session:
            return
        sid = self._session.get('id')
        for i in range(self.session_list.count()):
            it = self.session_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == sid:
                self.session_list.blockSignals(True)
                self.session_list.setCurrentItem(it)
                self.session_list.blockSignals(False)
                break

    def _on_session_changed(self, current, _previous):
        if not current:
            return
        sid = current.data(Qt.ItemDataRole.UserRole)
        if sid:
            self._session = load_session(sid)
            self._render_messages()
            self._sync_send_enabled()

    def _new_session(self):
        model = self._current_model()
        mid = str(model.get('id') or '') if model else ''
        mname = str(model.get('model') or '') if model else ''
        self._session = create_session(model_config_id=mid, model=mname)
        self._reload_sessions()
        self._render_messages()
        self._clear_attachments()
        self.input.clear()
        self.input.setFocus()
        self._sync_send_enabled()

    def _rename_session(self):
        if not self._session:
            return
        zh = self.language == 'zh'
        title, ok = QInputDialog.getText(
            self, '重命名会话' if zh else 'Rename session',
            '会话标题：' if zh else 'Title:',
            text=str(self._session.get('title') or ''),
        )
        if ok and title.strip():
            self._session = rename_session(self._session.get('id'), title.strip())
            self._reload_sessions()

    def _delete_session(self):
        if not self._session:
            return
        zh = self.language == 'zh'
        if not confirm_action(
            self,
            '删除会话' if zh else 'Delete session',
            f'确定删除会话「{self._session.get("title") or "当前会话"}」？' if zh else 'Delete current session?',
            danger=True,
        ):
            return
        sid = self._session.get('id')
        delete_session(sid)
        self._session = None
        self._reload_sessions()
        self._render_messages()
        self._sync_send_enabled()

    def _thread_at_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 32

    def _scroll_thread_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble_max_width(self) -> int:
        return chat_bubble_max_width(self.scroll.viewport().width() or self.scroll.width())

    def _apply_bubble_widths(self):
        if self._bubble_width_lock:
            return
        self._bubble_width_lock = True
        try:
            self._apply_bubble_widths_unlocked()
        finally:
            self._bubble_width_lock = False

    def _apply_bubble_widths_unlocked(self):
        max_w = self._bubble_max_width()
        inner = max(80, max_w - 28)
        for i in range(max(0, self.thread_layout.count() - 1)):
            item = self.thread_layout.itemAt(i)
            holder = item.widget() if item is not None else None
            if holder is None:
                continue
            for frame in holder.findChildren(QFrame):
                name = frame.objectName()
                if name in ('chat-user-bubble', 'chat-assistant-bubble'):
                    frame.setMaximumWidth(max_w)
                    body = frame.findChild(QLabel, 'chat-bubble-body')
                    if body is not None:
                        body.setMaximumWidth(inner)

    def _make_message_row(self, msg: dict) -> QWidget:
        zh = self.language == 'zh'
        role = msg.get('role')
        frame = QFrame()
        frame.setObjectName('chat-user-bubble' if role == 'user' else 'chat-assistant-bubble')
        frame.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 8, 14, 8)
        box.setSpacing(4)
        body = QLabel(str(msg.get('content') or ''))
        body.setObjectName('chat-bubble-body')
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if msg.get('status') == 'stopped':
            body.setText(body.text() + ('\n（已停止/未完成）' if zh else '\n(stopped)'))
        box.addWidget(body)
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        meta = QLabel()
        meta.setObjectName('field-hint')
        clock = format_chat_clock(msg.get('created_at'))
        if role == 'user':
            meta.setText(clock)
        else:
            mname = msg.get('model') or msg.get('config_name') or 'AI'
            meta.setText(f'{mname} · {clock}'.strip(' ·'))
        meta_row.addWidget(meta, 1)
        if role != 'user':
            copy = QPushButton('复制' if zh else 'Copy')
            copy.setObjectName('chat-copy-btn')
            apply_button(copy, 'ghost', compact=True)
            copy.clicked.connect(lambda _=False, text=str(msg.get('content') or ''): self._copy(text))
            meta_row.addWidget(copy, 0)
        box.addLayout(meta_row)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(8)
        if role == 'user':
            wrap.addStretch(1)
            wrap.addWidget(frame, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        else:
            wrap.addWidget(frame, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            wrap.addStretch(1)
        holder = QWidget()
        holder.setProperty('chatRole', role)
        holder.setLayout(wrap)
        return holder

    def _render_messages(self):
        follow = self._thread_at_bottom()
        while self.thread_layout.count() > 1:
            item = self.thread_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._session or not (self._session.get('messages') or []):
            self.empty.show()
            return
        self.empty.hide()
        for msg in self._session.get('messages') or []:
            holder = self._make_message_row(msg)
            self.thread_layout.insertWidget(self.thread_layout.count() - 1, holder)
        self._apply_bubble_widths()
        if follow:
            QTimer.singleShot(0, self._scroll_thread_to_bottom)

    def _copy(self, text: str):
        QApplication.clipboard().setText(text)
        zh = self.language == 'zh'
        QToolTip.showText(QCursor.pos(), '已复制' if zh else 'Copied')

    def _send(self):
        text = self.input.toPlainText().strip()
        model = self._current_model()
        zh = self.language == 'zh'
        if not text and not self._text_attachments and not self._image_attachments:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if model is None:
            show_warning(self, '请先选择模型' if zh else 'Pick a model', '请选择已启用的模型配置。')
            return

        # 组合文本与附件
        parts = []
        for att in self._text_attachments:
            parts.append(f"[附件: {att['name']}]\n```\n{att['content']}\n```")
        if text:
            parts.append(text)
        full_text = '\n\n'.join(parts)

        # 视觉图片处理
        has_images = bool(self._image_attachments)
        supports_vision = bool(model.get('supports_vision', False))
        if has_images and not supports_vision:
            show_warning(self, '当前模型未开启视觉能力', '当前选中的内网模型未配置或不支持图片识别能力。')
            return

        stored_text = full_text
        if has_images:
            img_tags = ', '.join(f'[{img["name"]}]' for img in self._image_attachments)
            stored_text = f"{stored_text}\n(已附加图片: {img_tags})" if stored_text else f"(已附加图片: {img_tags})"

        if self._session is None:
            self._session = create_session(model_config_id=str(model.get('id') or ''), model=str(model.get('model') or ''))

        self.input.clear()
        img_refs = list(self._image_attachments)
        self._clear_attachments()

        self._session = append_message(
            self._session.get('id'), 'user', stored_text,
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

        # 若带图片，只在当前向 LLM 发送的请求 payload 内存中组装 multi-modal blocks
        if has_images and supports_vision and payload:
            for p_msg in reversed(payload):
                if p_msg.get('role') == 'user':
                    blocks = []
                    if full_text:
                        blocks.append({'type': 'text', 'text': full_text})
                    for img in img_refs:
                        blocks.append({'type': 'image_url', 'image_url': {'url': img['data_uri']}})
                    p_msg['content'] = blocks
                    break

        self._render_messages()
        self._reload_sessions()
        self._start_worker(payload, model)

    def _start_worker(self, messages, model):
        cfg = dict(model)
        cfg['enabled'] = True
        self._is_running = True
        self._sync_running_state()
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
        self.ping_status.setText(redact_error(message))

    def _on_chat_done(self):
        self._is_running = False
        self._sync_running_state()

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
            '请求已停止' if zh else 'The request was stopped.'
        )
        self._is_running = False
        self._sync_running_state()

    def _regenerate(self):
        if not self._session or self._is_running:
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
            show_warning(self, '请先选择模型' if zh else 'Pick a model', '请选择已启用的模型配置。')
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
    return os.path.basename(str(path or ''))
