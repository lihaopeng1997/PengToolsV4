# -*- coding: utf-8 -*-
"""整体软化 P2：局部视图与常驻图标随主题即时刷新。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from PyQt6.QtWidgets import QApplication
    from panels.format_panel import FormatToolsPanel
    from panels.interface_debug_panel import InterfaceDebugPanel
    from panels.personal_panel import KnowledgeTab
    from panels.requirement_panel import RequirementPanel
    from ui.theme_manager import ThemeManager
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class SoftThemeP2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        manager = ThemeManager.instance()
        manager.load_template(PROJECT_DIR)
        manager.apply(self.app, 'calm', font_size=12)

    def test_format_tab_icons_follow_current_theme(self):
        panel = FormatToolsPanel('zh')
        panel.refresh_theme()
        calm_image = panel.tabs.tabIcon(0).pixmap(20, 20).toImage()
        calm_colors = {
            calm_image.pixelColor(x, y).name()
            for x in range(calm_image.width()) for y in range(calm_image.height())
            if calm_image.pixelColor(x, y).alpha() > 0
        }

        ThemeManager.instance().apply(self.app, 'night', font_size=12)
        panel.refresh_theme()
        night_image = panel.tabs.tabIcon(0).pixmap(20, 20).toImage()
        night_colors = {
            night_image.pixelColor(x, y).name()
            for x in range(night_image.width()) for y in range(night_image.height())
            if night_image.pixelColor(x, y).alpha() > 0
        }

        self.assertNotEqual(calm_colors, night_colors)

    def test_interface_session_table_rebuilds_on_theme_change(self):
        panel = InterfaceDebugPanel('zh')
        panel._records = [{
            'id': 'theme-row', 'seq': 1, 'status': 200, 'method': 'GET',
            'url': 'https://example.test/orders', 'started_at': 1,
            'duration_ms': 25, 'response_body': 'ok',
        }]
        panel._records_by_id = {'theme-row': panel._records[0]}
        panel._rebuild_table()
        calm_color = panel.table.item(0, 1).foreground().color().name()

        ThemeManager.instance().apply(self.app, 'night', font_size=12)
        panel.refresh_theme()
        night_color = panel.table.item(0, 1).foreground().color().name()

        self.assertNotEqual(calm_color, night_color)

    def test_requirement_tree_refreshes_hand_painted_state_colors(self):
        with patch('panels.requirement_panel.load_requirements', return_value=[{
            'id': 'theme-requirement', 'title': '主题需求', 'system': '系统A',
            'status': '待开发', 'record_kind': '需求',
        }]), patch('panels.requirement_panel.load_systems', return_value=[]):
            panel = RequirementPanel('zh')
        panel.refresh_theme()
        calm_tree = panel.requirement_list.topLevelItem(0)
        self.assertIsNotNone(calm_tree)
        calm_color = calm_tree.foreground(0).color().name()

        ThemeManager.instance().apply(self.app, 'night', font_size=12)
        panel.refresh_theme()
        night_tree = panel.requirement_list.topLevelItem(0)
        self.assertIsNotNone(night_tree)
        self.assertNotEqual(calm_color, night_tree.foreground(0).color().name())

    def test_knowledge_workbook_highlight_rebuilds_on_theme_change(self):
        with patch('panels.personal_panel.load_seed_entries', return_value=[]), \
             patch('panels.personal_panel.load_custom_entries', return_value=[]):
            panel = KnowledgeTab('zh')
        entry = {
            'id': 'theme-sheet', 'title': '主题表格', 'content_type': 'workbook_sheet',
            'rows': [['列'], ['命中项']], 'column_count': 1, 'column_widths': [12],
            'header_rows': [0], 'cell_styles': {}, 'sheet_name': 'Sheet1',
        }
        panel._current = entry
        panel._show_workbook(entry)
        panel.search_edit.setText('命中')
        panel._filter_workbook_rows(entry)
        calm_color = panel.table_view.item(1, 0).background().color().name()

        ThemeManager.instance().apply(self.app, 'night', font_size=12)
        panel.refresh_theme()
        night_color = panel.table_view.item(1, 0).background().color().name()

        self.assertNotEqual(calm_color, night_color)


if __name__ == '__main__':
    unittest.main()
