# -*- coding: utf-8 -*-
"""文件列表伸缩与行内紧凑按钮的定向回归测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from PyQt6.QtWidgets import QApplication, QHeaderView

from panels.ops_log_panel import OpsLogPanel


class CompactListLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ops_lists_stretch_name_columns_and_keep_metadata_resizable(self):
        panel = OpsLogPanel('zh')
        try:
            remote_header = panel.remote_tree.header()
            self.assertEqual(remote_header.sectionResizeMode(0), QHeaderView.ResizeMode.Stretch)
            for column in range(1, 4):
                self.assertEqual(remote_header.sectionResizeMode(column), QHeaderView.ResizeMode.Interactive)

            export_header = panel.export_server_list.header()
            self.assertEqual(export_header.sectionResizeMode(0), QHeaderView.ResizeMode.Stretch)
            for column in range(1, 3):
                self.assertEqual(export_header.sectionResizeMode(column), QHeaderView.ResizeMode.Interactive)
        finally:
            panel._executor.shutdown(wait=False, cancel_futures=True)
            panel.close()

    def test_ops_inline_actions_use_compact_28_pixel_height(self):
        panel = OpsLogPanel('zh')
        try:
            buttons = (
                panel.path_up_btn,
                panel.path_go_btn,
                panel.path_refresh_btn,
                panel.use_path_btn,
                panel.tail_btn,
                panel.run_grep_btn,
                panel.preview_btn,
                panel.session_export_btn,
            )
            self.assertTrue(all(button.height() == 28 for button in buttons))
            self.assertTrue(all(button.property('compactAction') is True for button in buttons))
        finally:
            panel._executor.shutdown(wait=False, cancel_futures=True)
            panel.close()


if __name__ == '__main__':
    unittest.main()
