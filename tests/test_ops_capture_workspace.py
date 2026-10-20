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


if __name__ == '__main__':
    unittest.main()
