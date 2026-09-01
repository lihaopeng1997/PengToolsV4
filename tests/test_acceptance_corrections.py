# -*- coding: utf-8 -*-
"""Targeted acceptance verification tests for Round 2 corrections."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from config import DEFAULT_SETTINGS
from panels.agent_workbench_panel import AgentWorkbenchPanel
from panels.interface_debug_panel import InterfaceDebugPanel
from panels.settings_panel import SettingsPanel
from tools.intranet_llm import DEFAULT_AI_LOCAL, normalize_ai_local
from ui.field_metrics import CompactStepper
from ui.quick_panel import QuickPanel
from ui.ssh_terminal import SshTerminalWidget


class _MainWindowStub:
    def __init__(self):
        self.navigated_to = []

    def showNormal(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def navigate_to(self, index: int):
        self.navigated_to.append(index)


class AcceptanceCorrectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_quick_panel_chat_mode_and_navigation(self):
        win = _MainWindowStub()
        panel = QuickPanel(win)
        try:
            panel.toggle_expanded()
            self.assertEqual(panel._mode, 'tools')
            self.assertTrue(panel.grid_host.isVisible())
            self.assertTrue(panel.chat_container.isHidden())

            # Switch to chat mode
            panel._set_mode('chat')
            self.assertEqual(panel._mode, 'chat')
            self.assertTrue(panel.chat_container.isVisible())
            self.assertTrue(panel.grid_host.isHidden())

            # Test Open Full Model Chat button triggers navigation index 16
            panel.open_full_chat_btn.click()
            self.assertIn(16, win.navigated_to)

            # Test chat send/stop cycle
            panel.chat_input.setText('你好')
            dummy_cfg = {'name': 'TestModel', 'model': 'test', 'enabled': True, 'base_url': 'http://127.0.0.1:8000/v1'}
            panel.chat_model_combo.addItem('TestModel', dummy_cfg)
            panel.chat_model_combo.setCurrentIndex(panel.chat_model_combo.count() - 1)

            with patch('ui.quick_panel._QuickChatWorker.start'):
                panel._on_chat_send_or_stop()
                self.assertIsNotNone(panel._chat_worker)
                self.assertEqual(panel.chat_send_btn.text(), '停止')
                panel._on_chat_completed('测试回复')
                self.assertEqual(panel.chat_send_btn.text(), '发送')
                self.assertIn('测试回复', panel.chat_history.toPlainText())

            # Clear chat
            panel._clear_chat()
            self.assertEqual(panel.chat_history.toPlainText(), '')
            self.assertEqual(len(panel._chat_messages), 0)
        finally:
            panel.close()

    def test_supports_vision_persistence_and_normalization(self):
        # Default has supports_vision = False
        self.assertIn('supports_vision', DEFAULT_AI_LOCAL)
        self.assertFalse(DEFAULT_AI_LOCAL['supports_vision'])

        normalized = normalize_ai_local({'name': 'VLM', 'supports_vision': True})
        self.assertTrue(normalized['supports_vision'])

        normalized_default = normalize_ai_local({'name': 'Standard'})
        self.assertFalse(normalized_default['supports_vision'])

        # Settings panel UI binding
        page = SettingsPanel(DEFAULT_SETTINGS)
        try:
            self.assertTrue(hasattr(page, 'ai_supports_vision'))
            page.ai_supports_vision.setChecked(True)
            cfg = page._ai_cfg_from_ui()
            self.assertTrue(cfg.get('supports_vision'))

            page.ai_supports_vision.setChecked(False)
            cfg2 = page._ai_cfg_from_ui()
            self.assertFalse(cfg2.get('supports_vision'))
        finally:
            page.close()

    def test_agent_workbench_file_attachment_refs(self):
        panel = AgentWorkbenchPanel()
        try:
            panel.show()
            self.app.processEvents()
            with tempfile.TemporaryDirectory() as tmpdir:
                # Mock workspace session bound to tmpdir
                file_a = os.path.join(tmpdir, 'code.py')
                with open(file_a, 'w', encoding='utf-8') as f:
                    f.write('print("hello")')

                panel._workspace_session = {'id': 'test-ws', 'workspace_dir': tmpdir, 'conversations': [{'id': 'c1', 'messages': []}]}
                panel.dir_label.setText(tmpdir)

                with patch('PyQt6.QtWidgets.QFileDialog.getOpenFileNames', return_value=([file_a], 'All Files (*)')):
                    panel._pick_workspace_file_ref()

                self.assertIn('code.py', panel._file_attachments)
                self.assertTrue(panel.attachment_bar.isVisible())
                self.assertIn('code.py', panel.attachment_bar.text())

                # Typing user prompt
                panel.input.setPlainText('请分析该文件')

                # Clearing attachment should not clear prompt text
                panel._clear_attachments()
                self.assertEqual(len(panel._file_attachments), 0)
                self.assertTrue(panel.attachment_bar.isHidden())
                self.assertEqual(panel.input.toPlainText(), '请分析该文件')

                # Re-add and send: attachment references should be appended into message
                with patch('PyQt6.QtWidgets.QFileDialog.getOpenFileNames', return_value=([file_a], 'All Files (*)')):
                    panel._pick_workspace_file_ref()

                with patch.object(panel, '_current_model', return_value={'enabled': True, 'base_url': 'http://test'}):
                    with patch('panels.agent_workbench_panel._WorkbenchWorker') as mock_worker_cls:
                        mock_worker = MagicMock()
                        mock_worker_cls.return_value = mock_worker
                        panel._send()
                        # Verify the text sent to worker contains both prompt and file ref
                        sent_prompt = mock_worker_cls.call_args[1]['user_message']
                        self.assertIn('请分析该文件', sent_prompt)
                        self.assertIn('code.py', sent_prompt)
        finally:
            panel.close()

    def test_interface_debug_button_visibility(self):
        panel = InterfaceDebugPanel()
        try:
            panel.show()
            panel.resize(1000, 700)
            panel.detail_tabs.setCurrentIndex(3)
            panel._update_responsive_workspace(left_width=500, right_width=600)
            self.app.processEvents()

            # Direct actions must be visible
            self.assertTrue(panel.rt_send_btn.isVisible())
            self.assertTrue(panel.rt_fill_btn.isVisible())
            self.assertTrue(panel.rt_save_api_btn.isVisible())

            # Secondary actions must be tucked in more menu
            self.assertTrue(panel.rt_form_more_btn.isVisible())
            self.assertTrue(panel.rt_io_more_btn.isVisible())
        finally:
            panel.close()

    def test_compact_stepper_and_terminal_metrics(self):
        stepper = CompactStepper(minimum=0, maximum=100, value=10, suffix='ms')
        try:
            self.assertEqual(stepper.value(), 10)
            self.assertEqual(stepper.suffix_label.text(), 'ms')
            stepper.plus_btn.click()
            self.assertEqual(stepper.value(), 11)
            stepper.minus_btn.click()
            self.assertEqual(stepper.value(), 10)
        finally:
            stepper.close()

        term = SshTerminalWidget()
        try:
            cw, lh = term.view._cell_dimensions()
            self.assertGreater(cw, 0)
            self.assertGreater(lh, 0)
        finally:
            term.close()


if __name__ == '__main__':
    unittest.main()
