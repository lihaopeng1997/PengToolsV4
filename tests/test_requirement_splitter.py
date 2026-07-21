# -*- coding: utf-8 -*-
"""需求管理右侧上下：固定 3:7、不可拖动。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NormalizeContentSizesTests(unittest.TestCase):
    def test_always_3_7(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        self.assertEqual(normalize_content_splitter_sizes(None, total_h=1000), [300, 700])
        # 旧拖动数据也强制 3:7
        self.assertEqual(normalize_content_splitter_sizes([400, 200], total_h=1000), [300, 700])
        self.assertEqual(normalize_content_splitter_sizes([0, 0], total_h=1000), [300, 700])


class RequirementFixedSplitUiTests(unittest.TestCase):
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

    def test_detail_card_fill_and_no_320_cap(self):
        from PyQt6.QtWidgets import QSizePolicy
        panel = self._make_panel()
        sp = panel.detail_card.sizePolicy()
        self.assertEqual(sp.verticalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertGreater(panel.detail_card.maximumHeight(), 10000)
        self.assertEqual(panel.detail_card.minimumHeight(), 100)
        self.assertTrue(hasattr(panel, 'detail_scroll'))
        self.assertTrue(panel.detail_scroll.widgetResizable())
        panel.close()

    def test_splitter_not_draggable(self):
        panel = self._make_panel()
        self.assertEqual(panel.file_sql_splitter.handleWidth(), 0)
        if panel.file_sql_splitter.count() >= 2:
            handle = panel.file_sql_splitter.handle(1)
            self.assertFalse(handle.isEnabled())
        # 强制 setSizes 后仍应回到 3:7
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        panel.file_sql_splitter.setSizes([700, 100])
        panel.file_sql_splitter.moveSplitter(50, 1)
        self.app.processEvents()
        top, bottom = panel.file_sql_splitter.sizes()[:2]
        total = top + bottom or 1
        self.assertLess(top / total, 0.40)
        self.assertGreater(bottom / total, 0.55)
        panel.close()

    def test_fixed_ratio_about_3_7(self):
        panel = self._make_panel()
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        panel.file_sql_splitter.apply_fixed_ratio()
        self.app.processEvents()
        top, bottom = panel.file_sql_splitter.sizes()[:2]
        total = top + bottom or 1
        # 约 30% / 70%，允许 offscreen 少量偏差
        self.assertLess(top / total, 0.40)
        self.assertGreater(bottom / total, 0.55)
        panel.close()

    def test_old_stored_ratio_ignored(self):
        panel = self._make_panel(ui={
            'splitter_sizes': [320, 780],
            'content_splitter_sizes': [500, 200],
        })
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        panel.file_sql_splitter.apply_fixed_ratio()
        self.app.processEvents()
        top, bottom = panel.file_sql_splitter.sizes()[:2]
        self.assertLess(top, bottom)
        panel.close()


if __name__ == '__main__':
    unittest.main()
