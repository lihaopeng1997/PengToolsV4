# -*- coding: utf-8 -*-
"""待升级事项：按选择月份展示与独立完成态。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class MonthlyReleaseBoardStoreTests(unittest.TestCase):
    def test_board_roundtrip_keeps_monthly_completion_keys(self):
        from tools.dashboard_release_items import load_release_board, save_release_board
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "release_board.json")
            save_release_board(
                {"manual_items": [], "completed_requirement_keys": ["req-a@2026-08"]},
                path,
            )
            board = load_release_board(path)
        self.assertEqual(board["completed_requirement_keys"], ["req-a@2026-08"])


class MonthlyReleaseBoardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_month_only_shows_checked_requirements_and_completion_is_independent(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "aug-on", "title": "八月入选", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"},
            {"id": "aug-off", "title": "八月未勾选", "online_month": "2026-08", "is_monthly_release": False, "status": "开发中"},
            {"id": "sep-on", "title": "九月入选", "online_month": "2026-09", "is_monthly_release": True, "status": "待测试"},
        ]
        board = {"manual_items": [{"id": "old-manual", "title": "旧手工事项", "planned_date": "2026-10-01"}], "hidden_requirement_ids": [], "completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.save_release_board") as save_board:
            panel = DashboardPanel("zh")
            panel._fill_release(requirements)
            panel.release_month_combo.blockSignals(True)
            panel.release_month_combo.setCurrentIndex(panel.release_month_combo.findData("2026-08"))
            panel.release_month_combo.blockSignals(False)
            panel._fill_release(requirements)

            self.assertEqual(panel.release_list.count(), 1)
            row = panel.release_list.itemAt(0).widget()
            self.assertEqual(row.title_label.text(), "八月入选")
            self.assertEqual([panel.release_month_combo.itemData(i) for i in range(panel.release_month_combo.count())], ["2026-09", "2026-08"])
            self.assertFalse(hasattr(panel, "release_add_requirement_btn"))
            self.assertFalse(hasattr(panel, "release_add_manual_btn"))

            panel._set_release_item_completed("requirement", requirements[0], "2026-08", True)
            self.assertEqual(requirements[0]["status"], "开发中")
            saved = save_board.call_args.args[0]
            self.assertIn("aug-on@2026-08", saved["completed_requirement_keys"])
            panel.close()

    def test_requirement_dialog_roundtrip_keeps_monthly_release_flag(self):
        from panels.requirement_panel import RequirementDialog

        dialog = RequirementDialog({"title": "按月上线", "online_month": "2026-08", "is_monthly_release": True, "sql_parts": [], "source_files": []})
        self.assertTrue(dialog.monthly_release.isChecked())
        self.assertTrue(dialog.values()["is_monthly_release"])
        dialog.close()


if __name__ == "__main__":
    unittest.main()
