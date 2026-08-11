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

    def test_release_row_has_fixed_height_identifier_and_safe_deferred_refresh(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QPushButton
        from panels.dashboard_panel import DashboardPanel

        requirement = {
            "id": "bug-long",
            "code": "BUG-20260811-101",
            "record_kind": "BUG",
            "title": "这是一个用于验证待升级事项标题过长时必须省略且不会挤压完成按钮的超长缺陷标题",
            "online_month": "2026-08",
            "is_monthly_release": True,
            "status": "开发中",
        }
        board = {"manual_items": [], "hidden_requirement_ids": [], "completed_requirement_keys": []}

        def persist(updated_board):
            saved = dict(updated_board)
            saved["completed_requirement_keys"] = list(updated_board.get("completed_requirement_keys", []))
            board.clear()
            board.update(saved)

        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.load_requirements", return_value=[requirement]), \
                patch("panels.dashboard_panel.save_release_board", side_effect=persist) as save_board:
            panel = DashboardPanel("zh")
            panel._fill_release([requirement])
            row = panel.release_list.itemAt(0).widget()
            self.assertEqual(row.minimumHeight(), 64)
            self.assertEqual(row.maximumHeight(), 64)
            self.assertEqual(row.identifier_label.text(), "BUG-20260811-101")
            self.assertEqual(row.title_label.textInteractionFlags(), Qt.TextInteractionFlag.NoTextInteraction)
            self.assertEqual(row.title_label.wordWrap(), False)
            self.assertEqual(row.title_label.toolTip(), requirement["title"])
            row.title_label.setFixedWidth(120)
            row._update_title_elision()
            self.assertNotEqual(row.title_label.text(), requirement["title"])
            self.assertIn("…", row.title_label.text())

            complete_button = next(button for button in row.findChildren(QPushButton) if button.text() == "已完成")
            complete_button.click()
            self.assertEqual(panel.release_list.itemAt(0).widget(), row)
            self.app.processEvents()
            refreshed_row = panel.release_list.itemAt(0).widget()
            self.assertIsNot(refreshed_row, row)
            self.assertEqual(refreshed_row.status_label.text(), "已完成")
            self.assertIn("bug-long@2026-08", save_board.call_args.args[0]["completed_requirement_keys"])
            panel.close()

    def test_release_area_shrinks_to_actual_item_height(self):
        from panels.dashboard_panel import DashboardPanel

        requirement = {
            "id": "single-release",
            "code": "REQ-ONE",
            "title": "单条待升级任务",
            "online_month": "2026-08",
            "is_monthly_release": True,
            "status": "开发中",
        }
        board = {"manual_items": [], "hidden_requirement_ids": [], "completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.load_requirements", return_value=[requirement]):
            panel = DashboardPanel("zh")
            panel.resize(1200, 800)
            panel.show()
            self.app.processEvents()
            self.assertEqual(panel.release_list.count(), 1)
            self.assertLessEqual(panel.release_scroll.height(), 80)
            self.assertLess(panel.tasks_row.geometry().height(), 180)
            panel.close()

    def test_current_month_is_preferred_and_task_cards_stay_aligned(self):
        import datetime
        from panels.dashboard_panel import DashboardPanel

        current_month = datetime.date.today().strftime("%Y-%m")
        next_month = (datetime.date.today().replace(day=28) + datetime.timedelta(days=4)).replace(day=1).strftime("%Y-%m")
        requirements = [
            {"id": "current-release", "title": "本月升级", "online_month": current_month, "is_monthly_release": True, "status": "开发中"},
            {"id": "future-release", "title": "下月升级", "online_month": next_month, "is_monthly_release": True, "status": "待测试"},
            {"id": "recent-two", "title": "最近需求二", "status": "待分析"},
            {"id": "recent-three", "title": "最近需求三", "status": "待分析"},
        ]
        board = {"completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_requirements", return_value=requirements), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            panel.resize(1200, 800)
            panel.show()
            self.app.processEvents()
            self.assertEqual(panel.release_month_combo.currentData(), current_month)
            self.assertEqual(panel.release_list.count(), 1)
            self.assertEqual(panel.release_list.itemAt(0).widget()._payload["id"], "current-release")
            self.assertEqual(panel.recent_card.height(), panel.release_card.height())
            panel.close()

    def test_saved_monthly_requirement_refreshes_and_focuses_its_month(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "future-release", "title": "下月升级", "online_month": "2026-09", "is_monthly_release": True, "status": "待测试"},
        ]
        saved = {"id": "current-release", "title": "本月升级", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"}
        board = {"completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_requirements", side_effect=lambda: list(requirements)), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            requirements.append(saved)
            panel.refresh_for_requirement(saved)
            self.assertEqual(panel.release_month_combo.currentData(), "2026-08")
            self.assertEqual(panel.release_list.count(), 1)
            self.assertEqual(panel.release_list.itemAt(0).widget()._payload["id"], "current-release")
            panel.close()

    def test_requirement_dialog_roundtrip_keeps_monthly_release_flag(self):
        from panels.requirement_panel import RequirementDialog

        dialog = RequirementDialog({"title": "按月上线", "online_month": "2026-08", "is_monthly_release": True, "sql_parts": [], "source_files": []})
        self.assertTrue(dialog.monthly_release.isChecked())
        self.assertTrue(dialog.values()["is_monthly_release"])
        dialog.close()


if __name__ == "__main__":
    unittest.main()
