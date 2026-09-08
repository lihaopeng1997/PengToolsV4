# -*- coding: utf-8 -*-
"""PRISM-UI-P09A 交付管理工作台全页面板与子窗契约测试。

覆盖：
1. 需求管理 (RequirementPanel): 分栏 400/C-416, 左右几何限制 (320~560 / min 520), 窄布局自适应, Calm Token 消费;
2. 接口文档 (DocxUpdatePanel): 文档列表 280, SQL 编辑器最小高 220px, 响应式布局;
3. 个人日报 (DailyReportTab): 日期树 264, 三大主正文编辑区 preferred_height 120px / 间距规范, 响应式布局;
4. 发版联动 (SqlToolPanel): SQL 输入最小高 240px, 弱步骤条响应式收纳;
5. 测试点清单 (TestPointRow / TestPointsDialog): 行高 min 44, 图标及复选框命中 28x28, 弹窗几何限制。
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication


class TestP09ADeliveryPanels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_01_requirement_panel_splitter_and_bounds(self):
        """1. 需求管理：左栏 400/320~560，右栏 min 520，窄布局自适应，分组标题 Token。"""
        from panels.requirement_panel import RequirementPanel
        from ui.theme_manager import ThemeManager

        ui_state = {
            'splitter_sizes': [400, 820],
            'content_splitter_sizes': [240, 560],
        }
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                patch('panels.requirement_panel.load_requirement_ui', return_value=ui_state):
            panel = RequirementPanel('zh')

        try:
            sp = panel.detail_splitter
            self.assertIsNotNone(sp)
            left = sp.widget(0)
            right = sp.widget(1)
            self.assertIsNotNone(left)
            self.assertIsNotNone(right)

            # standard 模式
            panel.apply_layout_mode('standard')
            self.assertGreaterEqual(left.minimumWidth(), 320)
            self.assertEqual(left.maximumWidth(), 560)
            self.assertGreaterEqual(right.minimumWidth(), 520)

            # wide 模式：左栏受限 560
            panel.apply_layout_mode('wide')
            self.assertGreaterEqual(left.minimumWidth(), 320)
            self.assertEqual(left.maximumWidth(), 560)
            self.assertGreaterEqual(right.minimumWidth(), 520)

            # narrow 模式：解除 560 上限，左栏下调至 250，右栏 360
            panel.apply_layout_mode('narrow')
            self.assertGreaterEqual(left.maximumWidth(), 100000)
            self.assertEqual(left.minimumWidth(), 250)
            self.assertEqual(right.minimumWidth(), 360)

            # 月份分组标题使用 ThemeManager Token 而非裸色
            tm = ThemeManager.instance()
            text_strong = tm.token('TEXT_STRONG')
            surface_variant = tm.token('SURFACE_VARIANT')
            self.assertTrue(text_strong.startswith('#'))
            self.assertTrue(surface_variant.startswith('#'))
        finally:
            panel.close()

    def test_02_docx_panel_splitter_and_sql_editor(self):
        """2. 接口文档：左列表 280, SQL 编辑器最小高 220px。"""
        from panels.docx_panel import DocxUpdatePanel

        panel = DocxUpdatePanel()
        try:
            self.assertGreaterEqual(panel.sql_editor.minimumHeight(), 220)

            # standard 模式
            panel.apply_layout_mode('standard')
            left = panel.main_splitter.widget(0)
            self.assertIsNotNone(left)
            self.assertGreaterEqual(left.minimumWidth(), 280)
            self.assertGreaterEqual(panel.sql_editor.minimumHeight(), 220)

            # compact / narrow 模式
            panel.apply_layout_mode('narrow')
            self.assertGreaterEqual(panel.sql_editor.minimumHeight(), 220)
        finally:
            panel.close()

    def test_03_daily_report_tab_splitter_and_editors(self):
        """3. 个人日报：日期树 264, 三大编辑区 preferred_height 120px / 间距规范。"""
        from panels.personal_panel import DailyReportTab

        with patch('panels.personal_panel.load_reports', return_value={}), \
                patch('panels.personal_panel.save_reports'), \
                patch('panels.personal_panel.load_drafts', return_value={}), \
                patch('panels.personal_panel.save_drafts'), \
                patch('panels.personal_panel.load_reminder_settings', return_value={
                    'enabled': False, 'time': '17:30', 'last_reminder_date': '',
                    'history_collapsed_months': [], 'history_expanded_months': [],
                    'history_expand_pinned': True,
                }):
            tab = DailyReportTab()

        try:
            left = tab.splitter.widget(0)
            self.assertIsNotNone(left)
            self.assertGreaterEqual(left.minimumWidth(), 264)

            # standard 模式
            tab.apply_layout_mode('standard')
            self.assertGreaterEqual(left.minimumWidth(), 264)

            # 编辑器尺寸与伸缩
            self.assertGreaterEqual(tab.completed.minimumHeight(), 180)
            self.assertLessEqual(tab.issues.minimumHeight(), 72)
            self.assertLessEqual(tab.tomorrow.minimumHeight(), 72)
            self.assertLessEqual(tab.notes.minimumHeight(), 64)

            # preferred_height 为 120px，sizeHint 高度反映期望尺寸
            self.assertGreaterEqual(tab.issues.sizeHint().height(), 120)
            self.assertGreaterEqual(tab.tomorrow.sizeHint().height(), 120)
            self.assertGreater(tab.completed.sizeHint().height(), tab.issues.sizeHint().height())
        finally:
            tab.close()

    def test_04_sql_panel_input_and_steps_visibility(self):
        """4. 发版联动：SQL 输入最小高 240px，弱步骤条在 compact/narrow 下收起。"""
        from panels.sql_panel import SqlToolPanel

        panel = SqlToolPanel()
        try:
            self.assertGreaterEqual(panel.input_sql.minimumHeight(), 240)

            # standard 模式：步骤条可见
            panel.apply_layout_mode('standard')
            self.assertFalse(panel.release_steps.isHidden())
            self.assertGreaterEqual(panel.input_sql.minimumHeight(), 240)

            # wide 模式：步骤条可见
            panel.apply_layout_mode('wide')
            self.assertFalse(panel.release_steps.isHidden())

            # compact 模式：步骤条收起
            panel.apply_layout_mode('compact')
            self.assertTrue(panel.release_steps.isHidden())

            # narrow 模式：步骤条收起
            panel.apply_layout_mode('narrow')
            self.assertTrue(panel.release_steps.isHidden())
        finally:
            panel.close()

    def test_05_test_points_editor_and_dialog_spec(self):
        """5. 测试点清单：行高 min 44，命中区域 28x28，弹窗尺寸规范。"""
        from panels.test_points_editor import TestPointRow, TestPointsDialog

        point = {'id': 'pt-001', 'text': '这是一个长文测试任务点，需要支持自动折行与双击内联编辑', 'done': False}
        row = TestPointRow(point)
        try:
            self.assertGreaterEqual(row.minimumHeight(), 44)
            self.assertEqual(row.check.width(), 28)
            self.assertEqual(row.check.height(), 28)
            self.assertEqual(row.edit_btn.width(), 28)
            self.assertEqual(row.edit_btn.height(), 28)
            self.assertEqual(row.delete_btn.width(), 28)
            self.assertEqual(row.delete_btn.height(), 28)
            self.assertTrue(row.text_label.wordWrap())
        finally:
            row.close()

        req = {
            'id': 'REQ-2026-TEST',
            'title': '测试点弹窗测试',
            'description': '1. 测试点1\n2. 测试点2',
            'test_points': [point],
        }
        dlg = TestPointsDialog(req, persist=False)
        try:
            self.assertGreaterEqual(dlg.minimumWidth(), 520)
            self.assertGreaterEqual(dlg.minimumHeight(), 420)
        finally:
            dlg.close()


if __name__ == '__main__':
    unittest.main()
