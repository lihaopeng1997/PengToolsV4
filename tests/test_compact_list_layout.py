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

from PyQt6.QtWidgets import QApplication, QHeaderView, QSizePolicy

from panels.ops_log_panel import OpsLogPanel


class CompactListLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._prev_qss = cls.app.styleSheet()
        cls.app.setStyleSheet('')

    @classmethod
    def tearDownClass(cls):
        cls.app.setStyleSheet(cls._prev_qss)

    def test_ops_lists_stretch_name_columns_and_keep_metadata_resizable(self):
        panel = OpsLogPanel('zh')
        try:
            remote_header = panel.remote_tree.header()
            self.assertEqual(remote_header.sectionResizeMode(0), QHeaderView.ResizeMode.Stretch)
            for column in range(1, 4):
                self.assertEqual(remote_header.sectionResizeMode(column), QHeaderView.ResizeMode.Interactive)

            export_header = panel.export_server_list.header()
            self.assertEqual(export_header.sectionResizeMode(0), QHeaderView.ResizeMode.Interactive)
            self.assertEqual(export_header.sectionResizeMode(1), QHeaderView.ResizeMode.Stretch)
            self.assertEqual(export_header.sectionResizeMode(2), QHeaderView.ResizeMode.Interactive)
            self.assertGreaterEqual(panel.export_server_list.columnCount(), 3)
            self.assertIn('日志路径', panel.export_server_list.headerItem().text(1))
            self.assertIs(panel.export_btn.parentWidget(), panel.select_all_btn.parentWidget())
            self.assertEqual(
                panel.export_btn.sizePolicy().horizontalPolicy(),
                QSizePolicy.Policy.Maximum,
            )
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


class DensityPassPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._prev_qss = cls.app.styleSheet()
        cls.app.setStyleSheet('')

    @classmethod
    def tearDownClass(cls):
        cls.app.setStyleSheet(cls._prev_qss)

    def test_credit_qty_is_compact_spinbox(self):
        from PyQt6.QtWidgets import QSpinBox
        from panels.credit_panel import CreditCodePanel

        panel = CreditCodePanel()
        self.assertIsInstance(panel.personal_qty, QSpinBox)
        self.assertEqual(panel.personal_qty.width(), 56)
        self.assertEqual(panel.personal_qty.height(), 28)
        self.assertEqual(panel.personal_generate.height(), 28)
        panel.close()

    def test_vin_fills_visible_rows_and_keeps_vin_column_readable(self):
        from PyQt6.QtWidgets import QHeaderView
        from panels.vin_panel import VinPanel

        panel = VinPanel('zh')
        panel.resize(1100, 720)
        panel.show()
        self.app.processEvents()
        count = panel._visible_fill_count()
        self.assertGreaterEqual(count, 8)
        self.assertLessEqual(count, 40)
        panel._generate()
        self.assertEqual(panel.table.rowCount(), count)
        self.assertEqual(
            panel.table.horizontalHeader().sectionResizeMode(1),
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.assertEqual(
            panel.table.horizontalHeader().sectionResizeMode(4),
            QHeaderView.ResizeMode.Stretch,
        )
        panel.close()

    def test_docx_update_sits_on_date_author_row(self):
        from panels.docx_panel import DocxUpdatePanel

        panel = DocxUpdatePanel('zh')
        self.assertIs(panel.update_btn.parentWidget(), panel.date_card)
        self.assertEqual(panel.update_btn.text(), '一键更新文档')
        self.assertEqual(panel.doc_list.minimumHeight(), 72)
        self.assertEqual(panel.sql_editor.sizePolicy().verticalPolicy().name, 'Expanding')
        panel.close()

    def test_ops_export_offers_open_file_or_folder(self):
        panel = OpsLogPanel('zh')
        try:
            self.assertTrue(callable(panel._offer_open_export))
        finally:
            panel._executor.shutdown(wait=False, cancel_futures=True)
            panel.close()


if __name__ == '__main__':
    unittest.main()
