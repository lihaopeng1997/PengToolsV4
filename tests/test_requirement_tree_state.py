# -*- coding: utf-8 -*-
"""需求目录在完整刷新后应保留用户手动展开状态。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

GROUP_MONTH_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class RequirementTreeExpandStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_full_refresh_preserves_month_group_expand_state(self):
        from panels.requirement_panel import RequirementPanel

        requirements = [
            {
                "id": "req-july", "code": "REQ-JULY", "title": "七月需求",
                "online_month": "2026-07", "sql_parts": [], "source_files": [],
            },
            {
                "id": "req-august", "code": "REQ-AUGUST", "title": "八月需求",
                "online_month": "2026-08", "sql_parts": [], "source_files": [],
            },
        ]
        with patch("panels.requirement_panel.load_requirements", return_value=requirements), \
                patch("panels.requirement_panel.load_requirement_ui", return_value={}), \
                patch("panels.requirement_panel.RequirementPanel._refresh_file_tree", lambda self: None):
            panel = RequirementPanel("zh")
            july = next(
                panel.requirement_list.topLevelItem(i)
                for i in range(panel.requirement_list.topLevelItemCount())
                if panel.requirement_list.topLevelItem(i).data(0, GROUP_MONTH_ROLE) == "2026-07"
            )
            august = next(
                panel.requirement_list.topLevelItem(i)
                for i in range(panel.requirement_list.topLevelItemCount())
                if panel.requirement_list.topLevelItem(i).data(0, GROUP_MONTH_ROLE) == "2026-08"
            )
            july.setExpanded(False)
            august.setExpanded(True)

            panel._refresh()

            refreshed_july = next(
                panel.requirement_list.topLevelItem(i)
                for i in range(panel.requirement_list.topLevelItemCount())
                if panel.requirement_list.topLevelItem(i).data(0, GROUP_MONTH_ROLE) == "2026-07"
            )
            refreshed_august = next(
                panel.requirement_list.topLevelItem(i)
                for i in range(panel.requirement_list.topLevelItemCount())
                if panel.requirement_list.topLevelItem(i).data(0, GROUP_MONTH_ROLE) == "2026-08"
            )
            self.assertFalse(refreshed_july.isExpanded())
            self.assertTrue(refreshed_august.isExpanded())
            panel.close()

    def test_refresh_systems_skips_when_sources_unchanged(self):
        from panels.requirement_panel import RequirementPanel

        requirements = [{
            'id': 'req-1', 'code': 'REQ-1', 'title': '一项',
            'online_month': '2026-08', 'sql_parts': [], 'source_files': [],
        }]
        with patch('panels.requirement_panel.load_requirements', return_value=requirements), \
                patch('panels.requirement_panel.load_requirement_ui', return_value={}), \
                patch('panels.requirement_panel.RequirementPanel._refresh_file_tree', lambda self: None):
            panel = RequirementPanel('zh')
            with patch.object(panel, '_refresh') as refresh:
                panel.refresh_systems()
                refresh.assert_not_called()
                panel.refresh_systems(force=True)
                refresh.assert_called()
            panel.close()


if __name__ == "__main__":
    unittest.main()
