# -*- coding: utf-8 -*-
"""LD-03: a busy button keeps its label and shows one 16px ring, not an overlay."""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication, QPushButton

from ui.aurora_progress import AuroraProgress
from ui.button_busy_ring import ButtonBusyRing, set_button_busy


_app = QApplication.instance() or QApplication([])


class ButtonBusyRingTests(unittest.TestCase):
    def test_ring_stays_16px_and_does_not_move_the_label(self):
        button = QPushButton('测试连接')
        button.resize(120, 32)
        button.show()
        _app.processEvents()
        before_text = button.text()
        before_size = button.size()
        ring = set_button_busy(button, True)
        _app.processEvents()
        self.assertIsInstance(ring, ButtonBusyRing)
        self.assertEqual(button.text(), before_text)
        self.assertEqual(button.size(), before_size)
        self.assertTrue(ring.is_busy())
        self.assertEqual((ring.width(), ring.height()), (16, 16))
        self.assertTrue(button.rect().contains(ring.geometry()))
        set_button_busy(button, False)
        self.assertFalse(ring.is_busy())
        self.assertFalse(ring.isVisible())
        self.assertEqual(button.text(), before_text)
        refused = set_button_busy(button, True, uses_overlay=True)
        self.assertIsNone(refused)
        self.assertFalse(ring.is_busy())
        button.deleteLater()

    def test_server_editor_test_uses_the_ring_not_a_busy_label(self):
        from panels.ops_log_panel import ServerEditorDialog, _SshTestWorker

        dialog = ServerEditorDialog('zh')
        dialog.host_edit.setText('10.0.0.8')
        dialog.user_edit.setText('root')
        dialog.password_edit.setText('secret')
        dialog.show()
        _app.processEvents()
        label = dialog.test_btn.text()
        size = dialog.test_btn.size()
        try:
            with patch.object(_SshTestWorker, 'start', lambda self: None):
                dialog._test()
            _app.processEvents()
            self.assertEqual(dialog.test_btn.text(), label)
            self.assertNotIn('测试中', dialog.test_btn.text())
            self.assertEqual(dialog.test_btn.size(), size)
            self.assertFalse(dialog.test_btn.isEnabled())
            ring = dialog.test_btn._button_busy_ring
            self.assertTrue(ring.is_busy())
            self.assertEqual((ring.width(), ring.height()), (16, 16))
            self.assertEqual(dialog.findChildren(AuroraProgress), [])
            dialog._restore_test_btn()
            self.assertTrue(dialog.test_btn.isEnabled())
            self.assertFalse(ring.is_busy())
            self.assertEqual(dialog.test_btn.text(), label)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_log_list_ssh_test_keeps_the_status_line_and_not_a_button_label(self):
        from panels.ops_log_panel import OpsLogPanel, _SshTestWorker

        panel = OpsLogPanel('zh')
        panel.test_btn.setText('测试连接')
        try:
            with patch.object(panel, '_current_server', return_value={'name': 'app', 'host': '10.0.0.8', 'password_token': 'saved'}):
                with patch('panels.ops_log_panel.decrypt_secret', return_value='secret'):
                    with patch('panels.ops_log_panel.paramiko_available', return_value=True):
                        with patch.object(_SshTestWorker, 'start', lambda self: None):
                            panel._test_selected_server()
            self.assertEqual(panel.test_btn.text(), '测试连接')
            self.assertNotIn('测试中', panel.test_btn.text())
            self.assertFalse(hasattr(panel.test_btn, '_button_busy_ring'))
            self.assertTrue(panel.session_status.text().startswith('正在测试 SSH'))
        finally:
            panel.deleteLater()

    def test_query_overlay_does_not_also_spin_the_status_band(self):
        from panels.ai_workbench_panel import AiWorkbenchPanel, _DbWorker

        panel = AiWorkbenchPanel('zh', connection_id='c1', dialect='mysql')
        panel._bound_conn_item = {'id': 'c1', 'dialect': 'mysql', 'host': '10.0.0.1', 'name': 'demo'}
        panel.result.setRowCount(3)
        run_label = panel.run_btn.text()
        try:
            with patch.object(_DbWorker, 'start', lambda self: None):
                panel._start_db('query', sql='select 1')
            self.assertTrue(panel.result_status.text().startswith('正在执行查询'))
            self.assertFalse(panel.result_status.is_querying())
            self.assertEqual(panel.result.rowCount(), 3)
            self.assertEqual(panel.run_btn.text(), run_label)
            self.assertFalse(hasattr(panel.run_btn, '_button_busy_ring'))
            with patch.object(_DbWorker, 'start', lambda self: None):
                panel._start_db('fetch_all', sql='select 1')
            self.assertTrue(panel.result_status.is_querying())
            self.assertFalse(hasattr(panel.all_btn, '_button_busy_ring'))
        finally:
            panel.deleteLater()

    def test_status_line_tasks_do_not_retitle_buttons_or_add_rings(self):
        from panels.model_chat_panel import ModelChatPanel, _PingWorker
        from panels.ops_log_panel import OpsLogPanel, _LinuxQueryWorker, _Worker
        from panels.settings_panel import SettingsPanel, _AiProbeWorker
        from config import DEFAULT_SETTINGS

        chat = ModelChatPanel('zh')
        ping_label = chat.ping_btn.text()
        try:
            with patch('panels.model_chat_panel.show_warning', return_value=None):
                with patch.object(chat, '_current_model', return_value={'id': 'm', 'name': 'demo', 'model': 'qwen'}):
                    with patch.object(_PingWorker, 'start', lambda self: None):
                        chat._ping_current()
            self.assertEqual(chat.ping_btn.text(), ping_label)
            self.assertEqual(chat.ping_status.text(), '正在测试…')
            self.assertFalse(hasattr(chat.ping_btn, '_button_busy_ring'))
        finally:
            chat.deleteLater()

        settings = SettingsPanel(dict(DEFAULT_SETTINGS), 'zh')
        settings.ai_base_url.setText('http://10.0.0.8/v1')
        probe_label = settings.ai_probe_btn.text()
        try:
            with patch('ui.confirm_dialog.show_error', return_value=None):
                with patch.object(_AiProbeWorker, 'start', lambda self: None):
                    settings._probe_intranet_model()
            self.assertEqual(settings.ai_probe_btn.text(), probe_label)
            self.assertEqual(settings.ai_status.text(), '正在探测…')
            self.assertFalse(hasattr(settings.ai_probe_btn, '_button_busy_ring'))
        finally:
            settings.deleteLater()

        panel = OpsLogPanel('zh')
        try:
            query_label = panel.nl_query_btn.text()
            connect_label = panel.connect_btn.text()
            export_label = panel.export_btn.text()
            with patch('panels.ops_log_panel.show_warning', return_value=None):
                with patch('tools.intranet_llm.is_enabled', return_value=True):
                    with patch('tools.intranet_llm.load_ai_local', return_value={}):
                        with patch.object(panel, '_cmd_bar_text', return_value='查错误日志'):
                            with patch.object(panel, '_linux_query_context', return_value=''):
                                with patch.object(_LinuxQueryWorker, 'start', lambda self: None):
                                    panel._generate_linux_query()
            self.assertEqual(panel.nl_query_btn.text(), query_label)
            self.assertEqual(panel.status_label.text(), '正在生成只读查询…')
            self.assertFalse(hasattr(panel.nl_query_btn, '_button_busy_ring'))

            with patch('panels.ops_log_panel.show_warning', return_value=None):
                with patch('panels.ops_log_panel.show_error', return_value=None):
                    with patch.object(panel, '_current_server', return_value={'id': 's', 'name': 'app', 'host': '10.0.0.8', 'port': 22, 'password_token': 'saved'}):
                        with patch('panels.ops_log_panel.decrypt_secret', return_value='secret'):
                            with patch('panels.ops_log_panel.paramiko_available', return_value=True):
                                with patch.object(_Worker, 'start', lambda self: None):
                                    panel._connect_session()
            self.assertEqual(panel.connect_btn.text(), connect_label)
            self.assertEqual(panel.session_status.text(), '正在连接本会话…')
            self.assertFalse(panel.connect_btn.isEnabled())
            self.assertFalse(hasattr(panel.connect_btn, '_button_busy_ring'))

            self.assertNotIn('正在', export_label)
            self.assertFalse(hasattr(panel.export_btn, '_button_busy_ring'))
        finally:
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
