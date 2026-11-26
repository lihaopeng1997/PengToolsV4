# -*- coding: utf-8 -*-
"""可增量更新的消息滚动区。"""

from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from ui.chat_kit.bubble import MessageBubble


def format_chat_clock(value) -> str:
    text = str(value or '').strip()
    if 'T' in text:
        clock = text.split('T', 1)[1]
        return clock[:5]
    if len(text) >= 5 and ':' in text:
        return text[-5:]
    return text


class ChatView(QScrollArea):
    """Message list. Horizontal scrolling stays off; code blocks scroll themselves."""

    apply_sql_requested = pyqtSignal(str)
    run_sql_requested = pyqtSignal(str)

    def __init__(self, parent=None, *, sql_actions: bool = False):
        super().__init__(parent)
        self._sql_actions = bool(sql_actions)
        self.setObjectName('chat-view')
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host.setObjectName('chat-thread-host')
        self._host.setMinimumWidth(0)
        self._host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(12)
        self._layout.addStretch(1)
        self.setWidget(self._host)
        self._bubbles: dict[str, MessageBubble] = {}
        self._stick = True
        self._programmatic = False
        self._fitting = False
        self._jump = QToolButton(self.viewport())
        self._jump.setObjectName('chat-jump-bottom')
        self._jump.setText('↓')
        self._jump.setFixedSize(34, 34)
        self._jump.setCursor(Qt.CursorShape.PointingHandCursor)
        self._jump.clicked.connect(self.scroll_to_bottom)
        self._jump.hide()
        self._dot = QLabel(self._jump)
        self._dot.setObjectName('chat-jump-dot')
        self._dot.setFixedSize(8, 8)
        self._dot.move(24, 2)
        self._dot.hide()
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_range)
        self.viewport().installEventFilter(self)

    def _effective_viewport_width(self) -> int:
        vp_w = self.viewport().width()
        if vp_w >= 220:
            return vp_w
        own_w = self.width()
        if own_w >= 220:
            return own_w - 16
        parent = self.parentWidget()
        if parent is not None and parent.width() >= 240:
            return parent.width() - 24
        return 620

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_all_bubble_widths)

    def _sync_all_bubble_widths(self) -> None:
        width = self._effective_viewport_width()
        self._host.setMaximumWidth(max(1, width))
        for bubble in list(self._bubbles.values()):
            bubble.set_viewport_width(width)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.viewport() and event.type() == QEvent.Type.Resize and not self._fitting:
            self._fitting = True
            try:
                self._place_jump()
                self._sync_all_bubble_widths()
            finally:
                self._fitting = False
        return super().eventFilter(watched, event)

    def set_messages(self, messages) -> None:
        self.clear()
        for message in messages or []:
            self.append_message(message, follow=False)
        self._stick = True
        QTimer.singleShot(0, self.scroll_to_bottom)

    def set_sql_actions_enabled(self, enabled: bool) -> None:
        self._sql_actions = bool(enabled)

    def append_message(self, message, follow=True) -> MessageBubble:
        if not message.get('id'):
            message = dict(message, id=uuid4().hex)
        bubble = MessageBubble(message, sql_actions=self._sql_actions)
        if self._sql_actions:
            bubble.apply_sql_requested.connect(self.apply_sql_requested.emit)
            bubble.run_sql_requested.connect(self.run_sql_requested.emit)
        self.append_widget(bubble, follow=follow)
        return bubble

    def append_widget(self, widget: QWidget, *, follow=True) -> None:
        if isinstance(widget, MessageBubble):
            widget.set_viewport_width(self._effective_viewport_width())
            self._bubbles[widget.message_id] = widget
        index = max(0, self._layout.count() - 1)
        self._layout.insertWidget(index, widget)
        if follow:
            QTimer.singleShot(0, self._after_append)

    def bubble(self, msg_id) -> MessageBubble | None:
        return self._bubbles.get(str(msg_id or ''))

    def remove_message(self, msg_id: str) -> None:
        bubble = self._bubbles.get(msg_id)
        if bubble is not None:
            self.remove_widget(bubble)

    def remove_widget(self, widget: QWidget) -> None:
        self._layout.removeWidget(widget)
        if isinstance(widget, MessageBubble):
            self._bubbles.pop(widget.message_id, None)
        widget.hide()
        widget.deleteLater()

    def has_items(self) -> bool:
        return self._layout.count() > 1

    def restore_scroll(self, value: int) -> None:
        self._stick = False
        self.verticalScrollBar().setValue(value)
        self._on_scroll(self.verticalScrollBar().value())

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                self._bubbles.pop(getattr(widget, 'message_id', ''), None)
                widget.hide()
                widget.deleteLater()
        self._bubbles.clear()
        self._stick = True
        self._jump.hide()
        self._dot.hide()

    def note_activity(self) -> None:
        """New tokens arrived. Follow the bottom, or mark the jump button."""

        if self._stick:
            QTimer.singleShot(0, self.scroll_to_bottom)
            return
        self._stick = False
        self._jump.show()
        self._dot.show()
        self._place_jump()

    def scroll_to_bottom(self) -> None:
        self._stick = True
        bar = self.verticalScrollBar()
        self._programmatic = True
        bar.setValue(bar.maximum())
        self._programmatic = False
        self._dot.hide()
        self._jump.hide()
        self._place_jump()

    def _after_append(self) -> None:
        """Follow only while the reader is already at the bottom."""

        self._after_grow(self._stick)

    def _after_grow(self, follow: bool) -> None:
        width = self.viewport().width()
        self._host.setMaximumWidth(max(1, width))
        if follow:
            self.scroll_to_bottom()
            return
        self._stick = False
        self._jump.show()
        self._dot.show()
        self._place_jump()

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.maximum() - bar.value() <= 32

    def _on_scroll(self, _value: int) -> None:
        if self._programmatic:
            return
        at_bottom = self._at_bottom()
        self._stick = at_bottom
        self._jump.setVisible(not at_bottom)
        if at_bottom:
            self._dot.hide()
        self._place_jump()

    def _on_range(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self._programmatic = True
            self.verticalScrollBar().setValue(maximum)
            self._programmatic = False
            self._jump.hide()
            self._dot.hide()
        self._place_jump()

    def _place_jump(self) -> None:
        view = self.viewport()
        x = max(0, view.width() - 34 - 16)
        y = max(0, view.height() - 34 - 16)
        self._jump.move(x, y)
        self._jump.raise_()
