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

    def test_credit_qty_is_compact_stepper(self):
        from ui.field_metrics import CompactStepper
        from panels.credit_panel import CreditCodePanel

        panel = CreditCodePanel()
        self.assertIsInstance(panel.personal_qty, CompactStepper)
        self.assertEqual(panel.personal_qty.edit.width(), 56)
        self.assertEqual(panel.personal_qty.edit.height(), 28)
        self.assertEqual(panel.personal_qty.minus_btn.text(), '−')
        self.assertEqual(panel.personal_qty.plus_btn.text(), '+')
        panel.personal_qty.setValue(10)
        panel.personal_qty.plus_btn.click()
        self.assertEqual(panel.personal_qty.value(), 11)
        self.assertEqual(panel.personal_generate.height(), 28)
        self.assertEqual(
            panel.category_tabs.sizePolicy().verticalPolicy().name,
            'Maximum',
        )
        self.assertGreaterEqual(panel.table.minimumHeight(), 280)
        panel.close()

    def test_vin_fills_visible_rows_and_keeps_vin_column_readable(self):
        from PyQt6.QtWidgets import QHeaderView
        from panels.vin_panel import VinPanel

        panel = VinPanel('zh')
        panel.resize(1100, 720)
        panel.show()
        self.app.processEvents()
        self.assertEqual(panel.qty.value(), 10)
        count = panel._visible_fill_count()
        self.assertEqual(count, 10)
        panel._generate()
        self.assertEqual(panel.table.rowCount(), 10)
        self.assertGreaterEqual(panel.table.rowHeight(0), 28)
        self.assertGreaterEqual(panel.table.rowHeight(9), 28)
        self.assertGreaterEqual(panel.table.minimumHeight(), 200)
        self.assertEqual(
            panel.table.horizontalHeader().sectionResizeMode(1),
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.assertEqual(
            panel.table.horizontalHeader().sectionResizeMode(4),
            QHeaderView.ResizeMode.Stretch,
        )
        panel.close()

    def test_docx_date_and_author_align_with_form_rows(self):
        from panels.docx_panel import DocxUpdatePanel

        panel = DocxUpdatePanel('zh')
        self.assertIs(panel.update_btn.parentWidget(), panel.date_card)
        self.assertEqual(panel.update_btn.text(), '一键更新文档')
        self.assertEqual(panel.update_date.height(), 28)
        self.assertEqual(panel.author.height(), 28)
        self.assertEqual(panel.author.width(), 160)
        self.assertEqual(panel.date_row_label.objectName(), 'field-caption')
        self.assertEqual(panel.author_row_label.objectName(), 'field-caption')
        self.assertEqual(panel.folder_row_label.width(), 80)
        self.assertEqual(panel.doc_list.minimumHeight(), 72)
        self.assertEqual(panel.sql_editor.sizePolicy().verticalPolicy().name, 'Expanding')
        panel.close()

    def test_ops_numeric_fields_use_theme_stepper(self):
        from ui.field_metrics import CompactStepper
        panel = OpsLogPanel('zh')
        try:
            self.assertIsInstance(panel.context_spin, CompactStepper)
            self.assertIsInstance(panel.export_context, CompactStepper)
            self.assertEqual(panel.context_spin.minus_btn.text(), '−')
            self.assertTrue(callable(panel._offer_open_export))
            receivers = panel.keyword_edit.receivers(panel.keyword_edit.returnPressed)
            self.assertGreater(receivers, 0)
        finally:
            panel._executor.shutdown(wait=False, cancel_futures=True)
            panel.close()


if __name__ == '__main__':
    unittest.main()
