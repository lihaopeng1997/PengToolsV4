# -*- coding: utf-8 -*-
"""One chat message: thinking, tools, markdown, phase, or an error card."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QToolTip, QVBoxLayout, QWidget,
)

from tools.ai_events import ReasoningBuffer, phase_label, tool_label
from ui.chat_kit.markdown import MarkdownBody


class ThinkingBlock(QFrame):
    """In-memory thinking. There is no save method."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('chat-thinking')
        self.buffer = ReasoningBuffer()
        self._seconds = 0
        self._running = False
        self._user_opened = False
        self._expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 0, 4)
        layout.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._dot = QLabel()
        self._dot.setObjectName('chat-breath')
        self._dot.setFixedSize(8, 8)
        self.title = QLabel(self._title())
        self.title.setObjectName('chat-thinking-title')
        self.title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title.mousePressEvent = self._toggle  # noqa: assignment
        head.addWidget(self._dot, 0)
        head.addWidget(self.title, 1)
        layout.addLayout(head)
        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setObjectName('chat-thinking-body')
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.hide()
        layout.addWidget(self.body)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def add_delta(self, text: str, *, expand: bool = True) -> None:
        self.buffer.add(text)
        self.show()
        self._running = True
        self._dot.show()
        if not self._timer.isActive():
            self._timer.start()
        if expand:
            self._open_body()
        self._refresh()

    def collapse(self) -> None:
        self._running = False
        self._timer.stop()
        self._dot.hide()
        self._refresh()
        if self._user_opened:
            return
        self._expanded = False
        self.body.hide()

    def is_expanded(self) -> bool:
        return self._expanded

    def text(self) -> str:
        return self.buffer.text()

    def _toggle(self, event) -> None:
        if self.body.isVisible():
            self._user_opened = False
            self.body.hide()
        else:
            self._user_opened = True
            self._open_body()
        if event is not None:
            event.accept()

    def _open_body(self) -> None:
        self._expanded = True
        self.body.setText(self.buffer.text())
        self.body.show()
        self._refresh()

    def _tick(self) -> None:
        if self._running:
            self._seconds += 1
            self._refresh()

    def _refresh(self) -> None:
        self.title.setText(self._title())
        if self.body.isVisible():
            self.body.setText(self.buffer.text())

    def _title(self) -> str:
        return f'思考过程 · {self._seconds} 秒 · {len(self.buffer.text())} 字'


class ToolStepLine(QWidget):
    """One tool row. Internal ids are never shown."""

    def __init__(self, tool: str, target: str = '', state: str = 'running', seconds: float | None = 0,
                 call_id: str = '', parent=None, *, label: str = ''):
        super().__init__(parent)
        self.setObjectName('chat-tool-step')
        self._tool = str(tool or '')
        self._label = label
        self._target = str(target or '')
        self._seconds = seconds
        self._call_id = str(call_id or '')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)
        self.mark = QLabel(self._mark(state))
        self.mark.setObjectName('chat-tool-mark')
        self._state = self._normalize(state)
        self.setProperty('state', self._state)
        self.mark.setProperty('state', self._state)
        self.caption = QLabel(self._caption())
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        self.caption.setObjectName('chat-tool-caption')
        self.duration_label = QLabel(self._duration_text())
        self.duration_label.setObjectName('chat-tool-duration')
        layout.addWidget(self.mark, 0)
        layout.addWidget(self.caption, 1)
        layout.addWidget(self.duration_label, 0)
        self._polish(self)
        self._polish(self.mark)

    def text(self) -> str:
        return self.caption.text()

    def set_state(self, state: str, seconds: float | None = None) -> None:
        self._state = self._normalize(state)
        self._seconds = seconds
        self.setProperty('state', self._state)
        self.mark.setProperty('state', self._state)
        self.mark.setText(self._mark(state))
        self.caption.setText(self._caption())
        self.duration_label.setText(self._duration_text())
        self._polish(self)
        self._polish(self.mark)

    def _duration_text(self) -> str:
        if self._seconds is None:
            return ''
        return _duration(self._seconds)

    def _caption(self) -> str:
        label = self._label or tool_label(self._tool)
        target = self._target.strip()
        prefix = '工具调用完成：' if self._state == 'ok' else ('工具调用失败：' if self._state == 'failed' else '')
        pieces = [f'{prefix}{label}' if prefix else label]
        if target:
            pieces.append(target)
        return ' '.join(pieces)

    @staticmethod
    def _normalize(state: str) -> str:
        key = str(state or '').strip().lower()
        if key in {'ok', 'success', 'done', 'succeeded'}:
            return 'ok'
        if key in {'failed', 'error', 'fail'}:
            return 'failed'
        if key == 'recorded':
            return 'recorded'
        return 'running'

    @staticmethod
    def _mark(state: str) -> str:
        key = ToolStepLine._normalize(state)
        if key == 'ok':
            return '✓'
        if key == 'failed':
            return '!'
        if key == 'recorded':
            return '•'
        return '…'

    @staticmethod
    def _polish(widget) -> None:
        _polish(widget)


def _polish(widget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class PhaseLine(QWidget):
    """Running phase. The label is always the Chinese phase name."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('chat-phase-line')
        self._seconds = 0
        self._state = ''
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._dot = QLabel()
        self._dot.setObjectName('chat-breath')
        self._dot.setFixedSize(8, 8)
        self.caption = QLabel('')
        self.caption.setObjectName('chat-phase')
        layout.addWidget(self._dot, 0)
        layout.addWidget(self.caption, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_state(self, state: str) -> None:
        self._state = str(state or '')
        self._seconds = 0
        self.caption.setText(self.display_text())
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def display_text(self) -> str:
        return f'{phase_label(self._state)} · {self._seconds} 秒'

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._seconds += 1
        self.caption.setText(self.display_text())


class ErrorCard(QFrame):
    retry_requested = pyqtSignal()
    copy_requested = pyqtSignal(str)

    def __init__(self, reason: str = '', tip: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('chat-error-card')
        self._reason = str(reason or '')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel('没有得到回答')
        title.setObjectName('chat-error-title')
        self.reason = QLabel(self._reason)
        self.reason.setTextFormat(Qt.TextFormat.PlainText)
        self.reason.setObjectName('chat-error-reason')
        self.reason.setWordWrap(True)
        self.tip = QLabel(tip or '可以重试这一次，或换一种问法。')
        self.tip.setObjectName('chat-error-tip')
        self.tip.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(self.reason)
        layout.addWidget(self.tip)
        row = QHBoxLayout()
        retry = self.retry_btn = QPushButton('重试')
        retry.setObjectName('chat-error-retry')
        retry.clicked.connect(self.retry_requested.emit)
        copy = QPushButton('复制错误信息')
        copy.setObjectName('chat-copy-btn')
        copy.clicked.connect(lambda: self.copy_requested.emit(self._reason))
        row.addWidget(retry, 0)
        row.addWidget(copy, 0)
        row.addStretch(1)
        layout.addLayout(row)

    def set_reason(self, reason: str, tip: str = '') -> None:
        self._reason = str(reason or '')
        self.reason.setText(self._reason)
        if tip:
            self.tip.setText(tip)


class MessageBubble(QWidget):
    resized = pyqtSignal()
    retry_requested = pyqtSignal()
    copy_error_requested = pyqtSignal(str)
    apply_sql_requested = pyqtSignal(str)
    run_sql_requested = pyqtSignal(str)

    def __init__(self, message: dict, parent=None, *, sql_actions: bool = False):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.message = dict(message or {})
        self.message_id = str(self.message.get('id') or '')
        self.role = str(self.message.get('role') or 'assistant')
        self._viewport = 480
        from ui.chat_kit.view import format_chat_clock
        user = self.role == 'user'
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        clock = format_chat_clock(self.message.get('created_at'))
        self.meta = QLabel()
        self.meta.setTextFormat(Qt.TextFormat.PlainText)
        if user:
            self.meta.setObjectName('chat-user-meta')
            self.meta.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.meta.setText(str(self.message.get('label') or clock))
        else:
            self.meta.setObjectName('chat-meta')
            label = str(self.message.get('label') or ('系统提示' if self.role == 'system' else 'AI 助手'))
            self.meta.setText(f'{label} · {clock}' if clock else label)
            model = str(self.message.get('model') or self.message.get('config_name') or '')
            if model:
                self.meta.setToolTip(model)
        root.addWidget(self.meta)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName('chat-user-bubble' if user else 'chat-assistant-bubble')
        self.card.setProperty('failed', False)
        self.card.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        box = QVBoxLayout(self.card)
        box.setContentsMargins(12, 8, 12, 6 if not user else 8)
        box.setSpacing(4 if not user else 6)
        self.thinking = ThinkingBlock()
        self._tools_host = QWidget()
        self._tools_host.setObjectName('chat-tools-host')
        self.tools = QVBoxLayout(self._tools_host)
        self.tools.setContentsMargins(0, 0, 0, 0)
        self.tools.setSpacing(3)
        self._tools_host.hide()
        self.markdown = MarkdownBody(sql_actions=sql_actions)
        self.markdown.content_changed.connect(self._sync_assistant_width)
        if sql_actions:
            self.markdown.apply_sql_requested.connect(self.apply_sql_requested.emit)
            self.markdown.run_sql_requested.connect(self.run_sql_requested.emit)
        self.phase = PhaseLine()
        self.stopped = QLabel('已停止')
        self.stopped.setObjectName('chat-stopped')
        self.stopped.hide()
        self.error_card = ErrorCard()
        self.error_card.hide()
        self.error_card.retry_requested.connect(self.retry_requested.emit)
        self.error_card.copy_requested.connect(self.copy_error_requested.emit)
        self.copy_btn = QPushButton('复制')
        self.copy_btn.setObjectName('chat-copy-btn')
        self.copy_btn.clicked.connect(self._copy_body)
        if user:
            self.user_text = QLabel(str(self.message.get('content') or ''))
            self.user_text.setTextFormat(Qt.TextFormat.PlainText)
            self.user_text.setObjectName('chat-user-text')
            self.user_text.setWordWrap(True)
            self.user_text.setMinimumWidth(0)
            self.user_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(self.user_text)
            self.thinking.hide()
            self.copy_btn.hide()
            self.phase.hide()
        else:
            box.addWidget(self.thinking)
            box.addWidget(self._tools_host)
            box.addWidget(self.markdown)
            box.addWidget(self.phase)
            box.addWidget(self.stopped)
            box.addWidget(self.error_card)
            self._actions_host = QWidget()
            actions = QHBoxLayout(self._actions_host)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(4)
            actions.addStretch(1)
            actions.addWidget(self.copy_btn, 0)
            box.addWidget(self._actions_host)
            self.markdown.set_markdown(str(self.message.get('content') or ''))
            status = str(self.message.get('status') or 'complete')
            if status == 'pending':
                self.copy_btn.hide()
                self._actions_host.hide()
            elif status == 'stopped':
                self.stopped.show()
            elif status == 'failed':
                self.show_error(str(self.message.get('content') or ''))
        if user:
            row.addStretch(1)
            row.addWidget(self.card, 0)
        else:
            row.addWidget(self.card, 0)
            row.addStretch(1)
        root.addLayout(row)

    def _sync_user_width(self) -> None:
        if self.role != 'user':
            return
        eff_vp = max(320, int(self._viewport))
        max_card = max(200, min(int(eff_vp * 0.76), eff_vp - 24))
        max_text_w = max(140, max_card - 28)
        raw = self.user_text.text() or ''
        fm = self.user_text.fontMetrics()
        longest = max((fm.horizontalAdvance(line) for line in raw.splitlines()), default=0) + 8
        if longest <= max_text_w and '\n' not in raw:
            self.user_text.setWordWrap(False)
            self.user_text.setMinimumWidth(0)
            self.user_text.setMaximumWidth(max_text_w)
            self.card.setMinimumWidth(0)
            self.card.setMaximumWidth(max_card)
        elif longest <= max_text_w:
            self.user_text.setWordWrap(False)
            self.user_text.setFixedWidth(longest)
            self.card.setFixedWidth(longest + 24)
        else:
            self.user_text.setWordWrap(True)
            self.user_text.setFixedWidth(max_text_w)
            self.card.setFixedWidth(max_text_w + 24)
        self.card.updateGeometry()

    def _sync_assistant_width(self) -> None:
        if self.role == 'user' or getattr(self, '_syncing_width', False):
            return
        self._syncing_width = True
        try:
            eff_vp = max(320, int(self._viewport))
            max_card = max(220, min(int(eff_vp * 0.88), eff_vp - 16))
            full_mode = (
                self.markdown.has_code_blocks()
                or self.error_card.isVisible()
                or self.thinking.isVisible()
                or self._tools_host.isVisible()
            )
            self.markdown.set_width(max(120, max_card - 24))
            if full_mode:
                target = max_card
            else:
                natural = self.markdown.natural_width() + 28
                target = max(140, min(max_card, natural))
            self.card.setFixedWidth(target)
            self.markdown.set_width(max(100, target - 24))
            self.card.updateGeometry()
        finally:
            self._syncing_width = False

    def set_viewport_width(self, width: int) -> None:
        width = max(240, int(width or 0))
        if width == self._viewport and getattr(self, '_width_ready', False):
            return
        self._width_ready = True
        self._viewport = width
        self.setMaximumWidth(self._viewport)
        if self.role == 'user':
            self._sync_user_width()
        else:
            self._sync_assistant_width()
        self.resized.emit()

    def add_tool(self, tool: str, target: str = '', state: str = 'running', seconds: float | None = 0,
                 call_id: str = '') -> ToolStepLine:
        self._tools_host.show()
        line = ToolStepLine(tool, target, state, seconds, call_id)
        self.tools.addWidget(line)
        self._sync_assistant_width()
        return line

    def show_error(self, reason: str, tip: str = '') -> None:
        self.thinking.hide()
        self.markdown.hide()
        self.phase.stop()
        self.stopped.hide()
        self.copy_btn.hide()
        if hasattr(self, '_actions_host'):
            self._actions_host.hide()
        self.error_card.set_reason(reason, tip)
        self.error_card.show()
        self.card.setProperty('failed', True)
        self._sync_assistant_width()
        _polish(self.card)

    def mark_complete(self, text: str) -> None:
        if text:
            self.markdown.set_markdown(text)
        self.markdown.show()
        self.error_card.hide()
        self.phase.stop()
        self.thinking.collapse()
        self.copy_btn.show()
        if hasattr(self, '_actions_host'):
            self._actions_host.show()
        self.card.setProperty('failed', False)
        self._sync_assistant_width()
        _polish(self.card)

    def mark_stopped(self) -> None:
        self.phase.stop()
        self.thinking.collapse()
        self.stopped.show()
        self.copy_btn.show()
        if hasattr(self, '_actions_host'):
            self._actions_host.show()
        self._sync_assistant_width()

    def visible_text(self) -> str:
        if self.role == 'user':
            return self.user_text.text()
        return self.markdown.source()

    def _copy_body(self) -> None:
        QApplication.clipboard().setText(self.visible_text())
        QToolTip.showText(QCursor.pos(), '已复制')


def _duration(seconds: float) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0.0:
        return '<0.1 秒'
    if value < 0.1:
        return '<0.1 秒'
    if value.is_integer():
        return f'{int(value)} 秒'
    return f'{value:.1f} 秒'
