# -*- coding: utf-8 -*-
"""需求管理右侧：摘要紧凑 + 文件库占满剩余。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NormalizeContentSizesTests(unittest.TestCase):
    def test_content_sized_top(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        self.assertEqual(normalize_content_splitter_sizes(None, total_h=1000, top_h=160), [160, 840])
        # 旧 3:7 存储不再强制比例，优先 top_h
        self.assertEqual(normalize_content_splitter_sizes([400, 200], total_h=1000, top_h=150), [150, 850])
        # 无 top_h 时用 stored 并夹紧
        sizes = normalize_content_splitter_sizes([500, 200], total_h=1000)
        self.assertLessEqual(sizes[0], 280)
        self.assertGreater(sizes[1], sizes[0])


class RequirementCompactStackUiTests(unittest.TestCase):
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

    def test_detail_card_is_content_sized(self):
        from PyQt6.QtWidgets import QSizePolicy
        panel = self._make_panel()
        sp = panel.detail_card.sizePolicy()
        self.assertEqual(sp.verticalPolicy(), QSizePolicy.Policy.Maximum)
        self.assertIsNone(panel.file_sql_splitter)
        panel.close()

    def test_file_tabs_take_remaining_space(self):
        panel = self._make_panel()
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        # 文件库区应明显大于摘要卡
        self.assertGreater(panel.detail_tabs.height(), panel.detail_card.height())
        self.assertLess(panel.detail_card.height(), panel.height() * 0.45)
        panel.close()

    def test_flags_single_row(self):
        from PyQt6.QtWidgets import QHBoxLayout
        panel = self._make_panel()
        self.assertIsInstance(panel.flag_chips_layout, QHBoxLayout)
        panel.close()


if __name__ == '__main__':
    unittest.main()
