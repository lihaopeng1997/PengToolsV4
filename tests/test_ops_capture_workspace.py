"""日志排查「多机截取」工作区：模式切换、实时结果行、内容预览与关键字高亮。"""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class OpsCaptureWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved = {}
        settings = {'work_mode': 'export', 'context_lines': 20, 'export_dir': ''}
        for target, value in (
            ('panels.ops_log_panel.load_log_settings', lambda: dict(settings, **self.saved)),
            ('panels.ops_log_panel.save_log_settings', lambda data: self.saved.update(data)),
            ('panels.ops_log_panel.load_servers', lambda: []),
            ('config.load_layout_splitter', lambda *a, **k: None),
            ('config.save_layout_splitter', lambda *a, **k: None),
        ):
            mock = patch(target, side_effect=value)
            mock.start()
            self.addCleanup(mock.stop)
        from panels.ops_log_panel import OpsLogPanel
        self.panel = OpsLogPanel('zh')
        self.addCleanup(self.panel.deleteLater)

    def test_export_is_default_and_mode_switch_swaps_workspaces(self):
        p = self.panel
        self.assertEqual(p._work_mode, 'export')
        self.assertFalse(p.capture_workspace.isHidden())
        self.assertTrue(p.session_workspace.isHidden())
        self.assertTrue(p.connect_btn.isHidden())
        self.assertTrue(p.mode_export_btn.isChecked())
        p._set_work_mode('session')
        self.assertTrue(p.capture_workspace.isHidden())
        self.assertFalse(p.session_workspace.isHidden())
        self.assertFalse(p.connect_btn.isHidden())
        self.assertEqual(self.saved.get('work_mode'), 'session')

    def test_result_rows_update_in_place_and_preview_highlights_keyword(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        p = self.panel
        p.result_table.setRowCount(0)
        for row, key in enumerate(('s1::a', 's2::a')):
            p.result_table.insertRow(row)
            for col in range(6):
                p.result_table.setItem(row, col, QTableWidgetItem('…'))
        p._result_rows = {'s1::a': 0, 's2::a': 1}
        p._capture_terms = ['ORDER-42']
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 's2.log')
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write('line 1\nfound ORDER-42 here\nORDER-42 again\n')
            p._on_result_row({'job_key': 's2::a', 'ok': True, 'server_name': '二号机', 'service_name': 'app',
                              'line_count': 3, 'local_path': path})
            p._on_result_row({'job_key': 's1::a', 'ok': False, 'server_name': '一号机', 'service_name': 'app',
                              'message': '连接超时'})
            self.assertEqual(p.result_table.rowCount(), 2)  # 原地更新，不追加
            self.assertEqual(p.result_table.item(1, 0).text(), '二号机')
            self.assertIn('失败', p.result_table.item(0, 2).text())
            # 第一个成功结果自动进入预览，关键字两处高亮
            self.assertIn('ORDER-42', p.result_preview.toPlainText())
            self.assertEqual(len(p.result_preview.extraSelections()), 2)
            self.assertIn('s2.log', p.preview_title.text())

    def test_button_text_counts_selected_targets(self):
        p = self.panel
        with patch.object(type(p), '_selected_export_keys', return_value={'a', 'b', 'c'}):
            p._refresh_export_selection()
        self.assertIn('3 个目标', p.export_btn.text())
        self.assertEqual(p.export_selected_label.text(), '已选 3')



    def test_query_generation_busy_and_failure_cleanup(self):
        from PyQt6.QtCore import QObject, pyqtSignal
        class Worker(QObject):
            completed = pyqtSignal(object)
            failed = pyqtSignal(str)
            finished = pyqtSignal()
            def __init__(self, *args):
                super().__init__()
            def start(self):
                pass
        p = self.panel
        p._set_cmd_bar_text('inspect load')
        with patch('tools.intranet_llm.is_enabled', return_value=True), patch('tools.intranet_llm.load_ai_local', return_value={}), patch('panels.ops_log_panel._LinuxQueryWorker', Worker), patch('panels.ops_log_panel.show_error'):
            p._generate_linux_query()
            first = p._query_worker
            self.assertTrue(p.nl_query_btn._button_busy_ring.is_busy())
            self.assertFalse(p.nl_query_btn.isEnabled())
            p._generate_linux_query()
            self.assertIs(p._query_worker, first)
            first.failed.emit('test failure')
            first.finished.emit()
            self.assertFalse(p.nl_query_btn._button_busy_ring.is_busy())
            self.assertTrue(p.nl_query_btn.isEnabled())

    def test_query_wait_ends_on_response_disconnect_timeout_or_send_failure(self):
        from unittest.mock import MagicMock
        from ui.ssh_terminal import SshTerminalWidget
        p = self.panel
        term = SshTerminalWidget(p)
        term.view._connected = True
        term.view._shell = MagicMock(alive=True)
        with patch.object(p, '_current_terminal', return_value=term), patch.object(p, '_send_cmd_bar') as send, patch('panels.ops_log_panel.confirm_action', return_value=True), patch('panels.ops_log_panel.show_error'):
            for finish in (term.response_received.emit, term.connection_closed.emit, p._query_wait_timer.timeout.emit):
                p._set_cmd_bar_text('cat /proc/loadavg')
                p._execute_linux_query()
                self.assertTrue(p.exec_query_btn._button_busy_ring.is_busy())
                count = send.call_count
                p._execute_linux_query()
                self.assertEqual(send.call_count, count)
                finish()
                self.assertFalse(p.exec_query_btn._button_busy_ring.is_busy())
                self.assertIsNone(p._query_wait_term)
                self.assertFalse(p._query_wait_timer.isActive())
            self.assertIn('未自动重试', p.status_label.text())
            send.side_effect = RuntimeError('send unavailable')
            p._execute_linux_query()
            self.assertFalse(p.exec_query_btn._button_busy_ring.is_busy())
            self.assertIn('发送失败', p.status_label.text())
        term.detach()


if __name__ == '__main__':
    unittest.main()
