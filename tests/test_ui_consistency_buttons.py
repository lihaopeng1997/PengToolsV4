# -*- coding: utf-8 -*-
"""界面一致性：紧凑按钮高度、ghost 角色、显隐切换文案。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SectionToggleTests(unittest.TestCase):
    def test_toggle_labels_zh_en(self):
        from ui.section_toggle import toggle_labels

        hide, show = toggle_labels('session_list', 'zh')
        self.assertEqual(hide, '隐藏列表')
        self.assertEqual(show, '显示列表')
        hide_en, show_en = toggle_labels('session_list', 'en')
        self.assertEqual(hide_en, 'Hide list')
        self.assertEqual(show_en, 'Show list')
        collapse, expand = toggle_labels('log', 'zh')
        self.assertIn('收起', collapse)
        self.assertIn('展开', expand)

    def test_apply_visibility_toggle_uses_btn_ghost_and_compact(self):
        from PyQt6.QtWidgets import QApplication, QPushButton
        from ui.section_toggle import apply_visibility_toggle

        app = QApplication.instance() or QApplication([])
        btn = QPushButton()
        apply_visibility_toggle(btn, content_visible=True, language='zh', kind='list')
        self.assertEqual(btn.objectName(), 'btn-ghost')
        self.assertTrue(btn.property('compactAction'))
        self.assertEqual(btn.text(), '隐藏')
        self.assertGreaterEqual(btn.minimumWidth(), 72)
        apply_visibility_toggle(btn, content_visible=False, language='zh', kind='list')
        self.assertEqual(btn.text(), '显示')
        btn.deleteLater()


class CompactButtonMetricsTests(unittest.TestCase):
    def test_size_compact_button_height_is_28(self):
        from PyQt6.QtWidgets import QApplication, QPushButton
        from ui.field_metrics import BTN_COMPACT_H, size_compact_button

        app = QApplication.instance() or QApplication([])
        btn = QPushButton('操作')
        size_compact_button(btn)
        self.assertEqual(BTN_COMPACT_H, 28)
        self.assertEqual(btn.height(), 28)
        self.assertTrue(btn.property('compactAction'))
        btn.deleteLater()

    def test_design_system_ghost_maps_to_btn_ghost(self):
        from PyQt6.QtWidgets import QApplication, QPushButton
        from ui.design_system import BUTTON_ROLES, apply_button

        self.assertEqual(BUTTON_ROLES['ghost'], 'btn-ghost')
        app = QApplication.instance() or QApplication([])
        btn = QPushButton('次要')
        apply_button(btn, 'ghost', compact=True)
        self.assertEqual(btn.objectName(), 'btn-ghost')
        self.assertEqual(btn.height(), 28)
        btn.deleteLater()


if __name__ == '__main__':
    unittest.main()
