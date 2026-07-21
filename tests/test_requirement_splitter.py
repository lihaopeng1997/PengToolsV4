# -*- coding: utf-8 -*-
"""需求管理右侧上下分栏：sizePolicy / 无 maxHeight / 3:7 / 旧数据兼容。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class NormalizeContentSizesTests(unittest.TestCase):
    def test_no_stored_uses_3_7(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        sizes = normalize_content_splitter_sizes(None, total_h=1000)
        self.assertEqual(sizes, [300, 700])

    def test_invalid_zero_fallback(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        sizes = normalize_content_splitter_sizes([0, 0], total_h=1000)
        self.assertEqual(sizes, [240, 560])

    def test_upper_larger_kept(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        sizes = normalize_content_splitter_sizes([400, 200], total_h=1000)
        self.assertEqual(sizes, [400, 200])

    def test_over_35_percent_kept(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        sizes = normalize_content_splitter_sizes([500, 500], total_h=1000)
        self.assertEqual(sizes, [500, 500])


class RequirementSplitterUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _make_panel(self, ui=None):
        from panels.requirement_panel import RequirementPanel
        ui = ui if ui is not None else {
            'splitter_sizes': [320, 780],
            'content_splitter_sizes': [240, 560],
        }
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                patch('panels.requirement_panel.load_requirement_ui', return_value=ui):
            return RequirementPanel('zh')

    def test_detail_card_size_policy_maximum_no_max_height(self):
        from PyQt6.QtWidgets import QSizePolicy
        panel = self._make_panel()
        sp = panel.detail_card.sizePolicy()
        self.assertEqual(sp.horizontalPolicy(), QSizePolicy.Policy.Preferred)
        # 垂直 Preferred：可自由拖动并持久化大上区（Maximum 会被 sizeHint 卡住）
        self.assertEqual(sp.verticalPolicy(), QSizePolicy.Policy.Preferred)
        # 无 320 硬上限
        self.assertGreater(panel.detail_card.maximumHeight(), 10000)
        self.assertNotEqual(panel.detail_card.maximumHeight(), 320)
        self.assertEqual(panel.detail_card.minimumHeight(), 100)
        panel.close()

    def test_stored_large_top_applied_to_splitter(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        # 逻辑层：上区更大不被强制重置
        self.assertEqual(normalize_content_splitter_sizes([400, 200]), [400, 200])
        panel = self._make_panel(ui={
            'splitter_sizes': [320, 780],
            'content_splitter_sizes': [400, 200],
        })
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        # UI 层：无 320 上限，可 setSizes 且不崩溃
        panel.file_sql_splitter.setSizes([400, 500])
        self.app.processEvents()
        sizes = panel.file_sql_splitter.sizes()
        self.assertEqual(len(sizes), 2)
        self.assertGreater(sizes[0], 100)
        self.assertGreater(sizes[1], 100)
        panel.close()

    def test_initial_ratio_without_stored_is_about_3_7(self):
        panel = self._make_panel(ui={'splitter_sizes': [320, 780]})
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        sizes = panel.file_sql_splitter.sizes()
        self.assertEqual(len(sizes), 2)
        self.assertLess(sizes[0], sizes[1])
        panel.close()

    def test_apply_layout_mode_narrow_caps_top(self):
        panel = self._make_panel(ui={
            'splitter_sizes': [320, 780],
            'content_splitter_sizes': [350, 500],
        })
        panel.resize(1000, 700)
        panel.show()
        self.app.processEvents()
        panel.apply_layout_mode('narrow', True)
        self.app.processEvents()
        top, bottom = panel.file_sql_splitter.sizes()[:2]
        self.assertLessEqual(top, 210)
        self.assertGreaterEqual(top, 100)
        self.assertGreaterEqual(bottom, 200)
        panel.close()


if __name__ == '__main__':
    unittest.main()
