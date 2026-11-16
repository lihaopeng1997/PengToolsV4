"""Role-separated, offline rich-text transcript for log analysis."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (QColor, QFont, QTextBlockFormat, QTextCharFormat,
                        QTextCursor, QTextDocument, QTextFrameFormat, QTextOption)
from PyQt6.QtWidgets import QTextBrowser


class OfflineDocument(QTextDocument):
    def loadResource(self, resource_type, url):  # noqa: N802
        # Model output is content, never a request to load a local/remote image.
        return None


class ChatView(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(OfflineDocument(self))
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(10)
        self._stream = None

    @property
    def streaming(self):
        return self._stream is not None

    @property
    def stream_role(self):
        return self._stream['role'] if self._stream else ''

    def begin_stream(self, label, *, role='thinking'):
        """Open a bubble that later tokens append into (no full re-render)."""
        from ui.theme_manager import ThemeManager
        self.end_stream()
        palette = ThemeManager.instance().palette()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        frame_format = QTextFrameFormat()
        frame_format.setPadding(10)
        frame_format.setTopMargin(6)
        frame_format.setBottomMargin(8)
        frame_format.setLeftMargin(0)
        frame_format.setRightMargin(16)
        frame_format.setBorder(1)
        frame_format.setBorderBrush(QColor(palette['BORDER']))
        frame_format.setBackground(QColor(palette['PRIMARY_SOFT'] if role == 'thinking' else palette['SURFACE']))
        frame = cursor.insertFrame(frame_format)
        label_format = QTextCharFormat()
        label_format.setFontWeight(QFont.Weight.Bold)
        label_format.setForeground(QColor(palette['PRIMARY'] if role == 'assistant' else palette['TEXT']))
        cursor.insertText('思考过程' if role == 'thinking' else label, label_format)
        body_format = QTextCharFormat()
        body_format.setForeground(QColor(palette['TEXT']))
        body_format.setFontWeight(QFont.Weight.Normal)
        cursor.insertBlock(QTextBlockFormat(), body_format)
        self._stream = {
            'cursor': QTextCursor(cursor),
            'frame': frame,
            'format': body_format,
            'role': role,
        }
        self._ensure_cursor_visible()

    def append_stream(self, text):
        """Append one chunk to the open bubble; never re-sets the document."""
        if not self._stream:
            return
        chunk = str(text or '')
        if not chunk:
            return
        self._stream['cursor'].insertText(chunk, self._stream['format'])
        self._ensure_cursor_visible()

    def end_stream(self):
        if self._stream:
            self._stream = None

    def _ensure_cursor_visible(self):
        bar = self.verticalScrollBar()
        if bar.value() >= bar.maximum() - 40:
            bar.setValue(bar.maximum())

    def set_messages(self, messages):
        self.end_stream()
        self.clear()
        for message in messages:
            self.append_message(message)

    def append_message(self, message):
        from ui.theme_manager import ThemeManager
        self.end_stream()
        palette = ThemeManager.instance().palette()
        role = message['role']
        user = role == 'user'
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        frame_format = QTextFrameFormat()
        frame_format.setPadding(12)
        frame_format.setTopMargin(8)
        frame_format.setBottomMargin(10)
        frame_format.setLeftMargin(28 if user else 0)
        frame_format.setRightMargin(0 if user else 16)
        frame_format.setBorder(1)
        frame_format.setBorderBrush(QColor(palette['BORDER']))
        frame_format.setBackground(QColor(palette['PRIMARY_SOFT'] if user else palette['SURFACE']))
        frame = cursor.insertFrame(frame_format)
        block = QTextBlockFormat()
        block.setAlignment(Qt.AlignmentFlag.AlignRight if user else Qt.AlignmentFlag.AlignLeft)
        block.setBottomMargin(8)
        cursor.setBlockFormat(block)
        label_format = QTextCharFormat()
        label_format.setFontWeight(QFont.Weight.Bold)
        label_format.setForeground(QColor(palette['PRIMARY'] if user else palette['TEXT']))
        label = {'user': '你', 'assistant': 'AI 助手', 'system': '系统提示'}[role]
        cursor.insertText(label, label_format)
        body_format = QTextCharFormat()
        body_format.setForeground(QColor(palette['TEXT']))
        body_format.setFontWeight(QFont.Weight.Normal)
        cursor.insertBlock(QTextBlockFormat(), body_format)
        if role == 'assistant':
            cursor.insertMarkdown(message['text'], QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                                  QTextDocument.MarkdownFeature.MarkdownNoHTML)
        else:
            cursor.insertText(message['text'], body_format)
        # Leave the frame before appending the next message.
        cursor.setPosition(frame.lastPosition() + 1)
        cursor.insertBlock(QTextBlockFormat(), body_format)
