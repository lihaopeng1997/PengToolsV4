"""ChatKit adapter for the log analysis transcript and transient stream."""

from ui.chat_kit import ChatView as KitChatView


class ChatView(KitChatView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream = None
        self._preview_ids = []
        self.stream_role = ''

    def clear(self):
        self.end_stream()
        self._preview_ids.clear()
        super().clear()

    def append_message(self, message, follow=True):
        self.end_stream()
        return super().append_message(
            dict(message, content=message['text']), follow=follow,
        )

    def begin_stream(self, label, *, role='thinking'):
        self.end_stream()
        self.stream_role = role
        self._stream = super().append_message({
            'role': 'assistant',
            'label': '' if role == 'thinking' else label,
            'status': 'pending',
        })
        if role == 'thinking':
            self._stream.meta.hide()
            self._stream.markdown.hide()
        else:
            self._preview_ids.append(self._stream.message_id)

    def append_stream(self, text):
        if self._stream is None or not text:
            return
        if self.stream_role == 'thinking':
            self._stream.thinking.add_delta(text)
        else:
            self._stream.markdown.append_text(text)
        self.note_activity()

    def end_stream(self):
        if self._stream is not None and self.stream_role == 'thinking':
            self._stream.thinking.collapse()
        self._stream = None
        self.stream_role = ''

    def discard_answer_preview(self):
        if self.stream_role == 'assistant':
            self.end_stream()
        for message_id in self._preview_ids:
            self.remove_message(message_id)
        self._preview_ids.clear()
