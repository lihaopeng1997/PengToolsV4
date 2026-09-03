# -*- coding: utf-8 -*-
"""Model Chat 气泡宽度与对齐 contract。"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel
from panels.model_chat_panel import (
    CHAT_BUBBLE_RATIO_CAP, ModelChatPanel, chat_bubble_max_width, format_chat_clock,
)


class BubbleHelperTests(unittest.TestCase):
    def test_max_width_ratio_and_resize(self):
        wide = chat_bubble_max_width(1000)
        narrow = chat_bubble_max_width(400)
        self.assertLessEqual(wide / 1000, CHAT_BUBBLE_RATIO_CAP)
        self.assertLess(narrow, wide)
        self.assertGreaterEqual(chat_bubble_max_width(80), 1)
        self.assertLess(chat_bubble_max_width(200), 200)

    def test_clock_format(self):
        self.assertEqual(format_chat_clock('2026-09-02T16:42:11'), '16:42')


class ModelChatBubbleLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from tools import model_chat_store
        self._patch = patch.object(model_chat_store, 'MODEL_CHAT_DIR', self.tmp.name)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_source_has_no_legacy_stretch_factors(self):
        src = inspect.getsource(ModelChatPanel._make_message_row)
        self.assertNotIn('addWidget(frame, 4)', src)
        self.assertNotIn('addWidget(frame, 19)', src)

    def test_user_right_assistant_left_and_short_hint(self):
        from tools.model_chat_store import append_message, create_session
        panel = ModelChatPanel(language='zh')
        session = create_session(model_config_id='m1', model='Qwen3.6')
        session = append_message(session['id'], 'user', 'hi', model='Qwen3.6')
        session = append_message(session['id'], 'assistant', 'ok', model='Qwen3.6')
        panel._session = session
        panel.resize(720, 520)
        panel._render_messages()
        QApplication.processEvents()
        rows = []
        for i in range(panel.thread_layout.count() - 1):
            w = panel.thread_layout.itemAt(i).widget()
            if w is not None:
                rows.append(w)
        self.assertEqual(len(rows), 2)
        user_wrap = rows[0].layout()
        asst_wrap = rows[1].layout()
        self.assertIsInstance(user_wrap, QHBoxLayout)
        self.assertIsInstance(asst_wrap, QHBoxLayout)
        self.assertEqual(user_wrap.stretch(0), 1)
        self.assertEqual(user_wrap.stretch(1), 0)
        self.assertEqual(asst_wrap.stretch(0), 0)
        self.assertEqual(asst_wrap.stretch(1), 1)
        user_frame = rows[0].findChild(QFrame, 'chat-user-bubble')
        asst_frame = rows[1].findChild(QFrame, 'chat-assistant-bubble')
        self.assertIsNotNone(user_frame)
        self.assertIsNotNone(asst_frame)
        max_w = panel._bubble_max_width()
        self.assertEqual(user_frame.maximumWidth(), max_w)
        body = user_frame.findChild(QLabel, 'chat-bubble-body')
        self.assertTrue(body.wordWrap())
        self.assertLessEqual(body.sizeHint().width(), max_w)
        self.assertLess(chat_bubble_max_width(400), chat_bubble_max_width(800))
        user_frame.setMaximumWidth(chat_bubble_max_width(400))
        panel._apply_bubble_widths()
        self.assertEqual(user_frame.maximumWidth(), panel._bubble_max_width())
        meta = asst_frame.findChildren(QLabel, 'field-hint')
        self.assertTrue(any('Qwen3.6' in (m.text() or '') for m in meta))
        self.assertFalse(any(m.text() == '助手' for m in meta))
        panel.deleteLater()

    def test_pending_bubble_shows_thinking_indicator_and_hides_copy(self):
        """M1, M2. pending 状态立即展示 ThinkingIndicator，且 Copy 按钮隐藏，绝无空气泡。"""
        from tools.model_chat_store import append_message, create_session
        from ui.thinking_indicator import ThinkingIndicator
        from PyQt6.QtWidgets import QPushButton

        panel = ModelChatPanel(language='zh')
        try:
            session = create_session(model_config_id='m1', model='Qwen3.6')
            session = append_message(session['id'], 'user', '帮我写个脚本')
            session = append_message(session['id'], 'assistant', '', status='pending', model='Qwen3.6')
            panel._session = session
            panel._render_messages()
            self.app.processEvents()

            asst_row = panel.thread_layout.itemAt(1).widget()
            asst_frame = asst_row.findChild(QFrame, 'chat-assistant-bubble')
            self.assertIsNotNone(asst_frame)

            indicator = asst_frame.findChild(ThinkingIndicator)
            self.assertIsNotNone(indicator)
            self.assertTrue(indicator.is_running())
            self.assertIn('正在思考', indicator.text())

            copy_btn = asst_frame.findChild(QPushButton, 'chat-copy-btn')
            self.assertIsNotNone(copy_btn)
            self.assertTrue(copy_btn.isHidden())
        finally:
            panel.deleteLater()

    def test_complete_bubble_hides_indicator_and_shows_copy(self):
        """M3. 成功后恢复正常文本展示，ThinkingIndicator 不存在，Copy 按钮可见。"""
        from tools.model_chat_store import append_message, create_session
        from ui.thinking_indicator import ThinkingIndicator
        from PyQt6.QtWidgets import QPushButton

        panel = ModelChatPanel(language='zh')
        try:
            session = create_session(model_config_id='m1', model='Qwen3.6')
            session = append_message(session['id'], 'user', '测试')
            session = append_message(session['id'], 'assistant', '这里是正常模型回答', status='complete', model='Qwen3.6')
            panel._session = session
            panel._render_messages()
            self.app.processEvents()

            asst_row = panel.thread_layout.itemAt(1).widget()
            asst_frame = asst_row.findChild(QFrame, 'chat-assistant-bubble')
            indicator = asst_frame.findChild(ThinkingIndicator)
            self.assertIsNone(indicator)

            body = asst_frame.findChild(QLabel, 'chat-bubble-body')
            self.assertIsNotNone(body)
            self.assertIn('这里是正常模型回答', body.text())

            copy_btn = asst_frame.findChild(QPushButton, 'chat-copy-btn')
            self.assertIsNotNone(copy_btn)
            self.assertFalse(copy_btn.isHidden())
        finally:
            panel.deleteLater()

    def test_stopped_and_failed_bubbles_display_state_never_empty(self):
        """M4. stopped 与 failed 状态即使原 content 为空也必须有明确状态文字，杜绝空气泡。"""
        from tools.model_chat_store import append_message, create_session

        panel = ModelChatPanel(language='zh')
        try:
            session = create_session(model_config_id='m1', model='Qwen3.6')
            session = append_message(session['id'], 'assistant', '', status='stopped', model='Qwen3.6')
            session = append_message(session['id'], 'assistant', '', status='failed', model='Qwen3.6')
            panel._session = session
            panel._render_messages()
            self.app.processEvents()

            row0 = panel.thread_layout.itemAt(0).widget()
            body0 = row0.findChild(QLabel, 'chat-bubble-body')
            self.assertIn('已停止', body0.text())

            row1 = panel.thread_layout.itemAt(1).widget()
            body1 = row1.findChild(QLabel, 'chat-bubble-body')
            self.assertIn('失败', body1.text())
        finally:
            panel.deleteLater()

    def test_stop_lifecycle_prevents_stale_overwrite(self):
        """M5. Stop 触发后设置 _stop_requested，旧 worker 延后返回的 ok/fail 不得覆盖 stopped 消息。"""
        from tools.model_chat_store import append_message, create_session

        panel = ModelChatPanel(language='zh')
        try:
            session = create_session(model_config_id='m1', model='Qwen3.6')
            session = append_message(session['id'], 'assistant', '', status='pending', model='Qwen3.6')
            panel._session = session
            panel._pending_id = session['messages'][0]['id']
            panel._is_running = True

            # 用户点击停止
            panel._stop()
            self.assertTrue(panel._stop_requested)
            self.assertEqual(panel._session['messages'][0]['status'], 'stopped')

            # 模拟旧 worker 延后返回结果
            panel._on_chat_ok('延迟到达的回答')
            # 状态和内容不应被覆盖
            self.assertEqual(panel._session['messages'][0]['status'], 'stopped')
            self.assertNotIn('延迟到达的回答', panel._session['messages'][0]['content'])

            # worker 结束
            panel._on_chat_done()
            self.assertFalse(panel._is_running)
            self.assertFalse(panel._stop_requested)
        finally:
            panel.deleteLater()
