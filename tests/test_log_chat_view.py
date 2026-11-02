"""Role attribution, rich-text boundaries, and per-task reading position."""
import os
import tempfile
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import QApplication
from ui.log_analysis.chat_view import ChatView
from ui.log_analysis.workspace import LogAnalysisWorkspace
from tools.log_analysis.report import Store


class LogChatViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_roles_keep_plain_user_text_and_render_model_markdown(self):
        view = ChatView()
        try:
            view.append_message({'role': 'user', 'text': 'Agent: ## my literal question'})
            view.append_message({'role': 'assistant', 'text': '## Findings\n\n**Important**\n\n- first\n- second'})
            text = view.toPlainText()
            self.assertIn('你', text)
            self.assertIn('AI 助手', text)
            self.assertIn('Agent: ## my literal question', text)
            self.assertNotIn('## Findings', text)
            self.assertNotIn('**Important**', text)
            self.assertEqual(len(view.document().rootFrame().childFrames()), 2)
            self.assertIn('Findings', text)
        finally:
            view.deleteLater()

    def test_untrusted_reply_cannot_load_resources_or_navigate(self):
        view = ChatView()
        try:
            view.append_message({'role': 'assistant', 'text': '<b>raw HTML</b>\n\n![sample](file:///private/example.png)'})
            self.assertFalse(view.openLinks())
            self.assertFalse(view.openExternalLinks())
            self.assertIn('<b>', view.toPlainText())
            self.assertIsNone(view.document().loadResource(QTextDocument.ResourceType.ImageResource, QUrl('file:///private/example.png')))
            self.assertIsNone(view.document().loadResource(QTextDocument.ResourceType.ImageResource, QUrl('https://example.invalid/image.png')))
        finally:
            view.deleteLater()

    def test_task_switch_and_reply_preserve_roles_and_reading_position(self):
        with tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            w = LogAnalysisWorkspace(lambda: [], store=Store(root))
            try:
                w.resize(1100, 740); w.show()
                w.tasks.addItem('first', 'first'); w.tasks.addItem('second', 'second')
                w.tasks.setCurrentIndex(1)
                w.append_chat('first', 'first question', role='user', force=True)
                w.append_chat('first', '\n\n'.join('paragraph ' + str(i) for i in range(80)), role='assistant')
                self.app.processEvents()
                bar = w.chat.verticalScrollBar()
                self.assertGreater(bar.maximum(), 0)
                bar.setValue(0)
                w.append_chat('first', 'new answer', role='assistant')
                self.assertEqual(bar.value(), 0)
                self.assertFalse(w.jump.isHidden())
                w.append_chat('second', 'separate question', role='user')
                self.assertNotIn('separate question', w.chat.toPlainText())
                w.tasks.setCurrentIndex(2)
                self.assertIn('separate question', w.chat.toPlainText())
                self.assertNotIn('first question', w.chat.toPlainText())
                w.tasks.setCurrentIndex(1)
                self.assertIn('first question', w.chat.toPlainText())
                self.assertIn('AI 助手', w.chat.toPlainText())
                self.assertIn('new answer', w.chat.toPlainText())
            finally:
                w.shutdown(); w.close(); w.deleteLater()
