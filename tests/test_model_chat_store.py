# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ModelChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from tools import model_chat_store
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(model_chat_store, 'MODEL_CHAT_DIR', self.tmp.name)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_crud_and_auto_title(self):
        from tools.model_chat_store import (
            append_message, create_session, delete_session, load_index, load_session, rename_session,
        )
        session = create_session(model_config_id='m1', model='qwen')
        session = append_message(session['id'], 'user', '帮我解释一下这段报错信息')
        self.assertEqual(session['title'][:20], '帮我解释一下这段报错信息'[:20])
        session = rename_session(session['id'], '手工标题')
        session = append_message(session['id'], 'user', '第二条不应改标题')
        self.assertEqual(session['title'], '手工标题')
        loaded = load_session(session['id'])
        self.assertEqual(len(loaded['messages']), 2)
        self.assertNotIn('token', loaded)
        self.assertNotIn('base_url', loaded)
        delete_session(session['id'])
        self.assertEqual(load_index(), [])

    def test_corrupt_file_does_not_break_index(self):
        from tools import model_chat_store
        from tools.model_chat_store import create_session, load_index, load_session
        good = create_session()
        bad_path = os.path.join(self.tmp.name, 'broken.json')
        with open(bad_path, 'w', encoding='utf-8') as stream:
            stream.write('{not-json')
        self.assertIsNone(load_session('broken'))
        ids = [row['id'] for row in load_index()]
        self.assertIn(good['id'], ids)

    def test_trim_keeps_local_full_history(self):
        from tools.model_chat_store import SYSTEM_PROMPT, trim_messages_for_request
        messages = []
        for i in range(20):
            messages.append({'role': 'user', 'content': f'u{i}' * 400})
            messages.append({'role': 'assistant', 'content': f'a{i}' * 400})
        payload, trimmed = trim_messages_for_request(messages, max_messages=4, max_chars=500)
        self.assertTrue(trimmed)
        self.assertEqual(payload[0]['role'], 'system')
        self.assertEqual(payload[0]['content'], SYSTEM_PROMPT)
        self.assertLessEqual(len(payload), 5)
        self.assertEqual(len(messages), 40)


class ModelChatPanelSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_constructs_and_has_header(self):
        from PyQt6.QtWidgets import QFrame
        from config import DEFAULT_SETTINGS
        from panels.model_chat_panel import ModelChatPanel
        settings = dict(DEFAULT_SETTINGS)
        settings['model_chat_banner_dismissed'] = False
        with patch('panels.model_chat_panel.load_settings', return_value=settings):
            panel = ModelChatPanel('zh')
        self.assertIsNotNone(panel.findChild(QFrame, 'page-header'))
        panel.show()
        self.app.processEvents()
        self.assertFalse(panel.banner.isHidden())
        panel.close()


if __name__ == '__main__':
    unittest.main()
