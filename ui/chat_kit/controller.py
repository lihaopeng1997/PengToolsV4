# -*- coding: utf-8 -*-
"""Apply AI events to the open bubble on the UI thread."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer

from ui.chat_kit.view import ChatView


class ChatController(QObject):
    """Worker threads only emit. This object updates widgets."""

    def __init__(self, view: ChatView, parent=None):
        super().__init__(parent)
        self.view = view
        self._bubble = None
        self._pending = ''
        self._collapsed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._flush)

    def attach(self, bubble) -> None:
        self._bubble = bubble
        self._pending = ''
        self._collapsed = False
        self._timer.stop()

    def detach(self) -> None:
        self._timer.stop()
        self._bubble = None
        self._pending = ''
        self._collapsed = False

    def handle(self, event) -> None:
        if self._bubble is None:
            return
        kind = str(getattr(event, 'kind', '') or '')
        if kind == 'phase':
            self._bubble.phase.set_state(getattr(event, 'state', '') or '')
            self.view.note_activity()
            return
        if kind == 'reasoning_delta':
            self._bubble.thinking.add_delta(str(getattr(event, 'text', '') or ''))
            self.view.note_activity()
            return
        if kind == 'text_delta':
            self._pending += str(getattr(event, 'text', '') or '')
            if not self._timer.isActive():
                self._timer.start(40)
            return
        if kind == 'tool_step':
            self._bubble.add_tool(
                getattr(event, 'tool', '') or '',
                getattr(event, 'text', '') or '',
                'running',
            )
            self.view.note_activity()
            return
        if kind == 'finished':
            self.complete(str(getattr(event, 'text', '') or ''))
            return
        if kind == 'failed':
            self.fail(str(getattr(event, 'text', '') or ''))

    def complete(self, text: str) -> None:
        self._timer.stop()
        self._pending = ''
        if self._bubble is None:
            return
        if text:
            self._bubble.markdown.set_markdown(text)
        self._collapse_thinking()
        self._bubble.phase.stop()
        self._bubble.mark_complete(text)
        self.view.note_activity()

    def fail(self, reason: str) -> None:
        self._timer.stop()
        self._pending = ''
        if self._bubble is None:
            return
        self._collapse_thinking()
        self._bubble.phase.stop()
        self._bubble.show_error(reason)
        self.view.note_activity()

    def stop(self) -> None:
        self._timer.stop()
        if self._pending and self._bubble is not None:
            self._bubble.markdown.append_text(self._pending)
            self._pending = ''
        if self._bubble is not None:
            self._collapse_thinking()
            self._bubble.mark_stopped()

    def _flush(self) -> None:
        if self._bubble is None or not self._pending:
            return
        self._bubble.markdown.append_text(self._pending)
        self._pending = ''
        self._collapse_thinking()
        self.view.note_activity()

    def _collapse_thinking(self) -> None:
        if self._bubble is None or self._collapsed:
            return
        self._bubble.thinking.collapse()
        self._collapsed = True
