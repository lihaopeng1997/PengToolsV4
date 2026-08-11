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
            panel._fill_release_items(requirements)

            rows = [
                panel.release_list.itemAt(i).widget()
                for i in range(panel.release_list.count())
                if panel.release_list.itemAt(i).widget() is not None
                and hasattr(panel.release_list.itemAt(i).widget(), "title_label")
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].title_label.text(), "八月入选")
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
            row = next(
                panel.release_list.itemAt(i).widget()
                for i in range(panel.release_list.count())
                if panel.release_list.itemAt(i).widget() is not None
                and hasattr(panel.release_list.itemAt(i).widget(), "identifier_label")
            )
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
            self.app.processEvents()
            refreshed_row = next(
                panel.release_list.itemAt(i).widget()
                for i in range(panel.release_list.count())
                if panel.release_list.itemAt(i).widget() is not None
                and hasattr(panel.release_list.itemAt(i).widget(), "status_label")
            )
            self.assertIsNot(refreshed_row, row)
            self.assertEqual(refreshed_row.status_label.text(), "已完成")
            self.assertIn("bug-long@2026-08", save_board.call_args.args[0]["completed_requirement_keys"])
            panel.close()

    def test_task_cards_keep_fixed_height_when_items_grow(self):
        from panels.dashboard_panel import DashboardPanel, TaskRow

        requirements = [
            {
                "id": f"r{i}",
                "code": f"REQ-{i}",
                "title": f"任务{i}",
                "online_month": "2026-08",
                "is_monthly_release": True,
                "status": "开发中",
                "updated_at": f"2026-08-0{min(i, 9)}T10:00:00",
            }
            for i in range(1, 8)
        ]
        board = {"manual_items": [], "hidden_requirement_ids": [], "completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.load_requirements", return_value=requirements):
            panel = DashboardPanel("zh")
            panel.resize(1200, 800)
            panel.show()
            self.app.processEvents()
            expected_list_h = 5 * TaskRow.ROW_HEIGHT + 4 * TaskRow.LIST_SPACING
            self.assertEqual(panel.release_scroll.height(), expected_list_h)
            self.assertEqual(panel.recent_scroll.height(), expected_list_h)
            self.assertEqual(panel.recent_card.height(), panel.release_card.height())
            before = panel.release_card.height()
            # 再追加条目后高度仍不变
            requirements.append(
                {"id": "extra", "title": "额外", "online_month": "2026-08", "is_monthly_release": True, "status": "待测试"}
            )
            panel.refresh()
            self.app.processEvents()
            self.assertEqual(panel.release_card.height(), before)
            self.assertEqual(panel.recent_card.height(), panel.release_card.height())
            self.assertGreaterEqual(panel.release_list.count(), 7)
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
            # count includes empty label + stretch; 任务行至少 1
            row_ids = [
                panel.release_list.itemAt(i).widget()._payload["id"]
                for i in range(panel.release_list.count())
                if panel.release_list.itemAt(i).widget() is not None
                and hasattr(panel.release_list.itemAt(i).widget(), "_payload")
            ]
            self.assertEqual(row_ids, ["current-release"])
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
            row_ids = [
                panel.release_list.itemAt(i).widget()._payload["id"]
                for i in range(panel.release_list.count())
                if panel.release_list.itemAt(i).widget() is not None
                and hasattr(panel.release_list.itemAt(i).widget(), "_payload")
            ]
            self.assertEqual(row_ids, ["current-release"])
            panel.close()

    def test_monthly_flag_without_month_defaults_to_current_and_shows_on_board(self):
        import datetime
        from panels.dashboard_panel import DashboardPanel
        from panels.requirement_panel import RequirementDialog

        current_month = datetime.date.today().strftime("%Y-%m")
        dialog = RequirementDialog(
            {"title": "仅勾选本月", "sql_parts": [], "source_files": [], "is_monthly_release": True}
        )
        self.assertTrue(dialog.monthly_release.isChecked())
        values = dialog.values()
        self.assertTrue(values["is_monthly_release"])
        self.assertEqual(values["online_month"], current_month)
        dialog.close()

        requirements = [
            {
                "id": "no-month",
                "title": "缺月份入选",
                "online_month": "",
                "is_monthly_release": True,
                "status": "开发中",
            }
        ]
        board = {"completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_requirements", return_value=requirements), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            panel.refresh()
            self.assertEqual(panel.release_month_combo.currentData(), current_month)
            row_ids = [
                panel.release_list.itemAt(i).widget()._payload["id"]
                for i in range(panel.release_list.count())
                if panel.release_list.itemAt(i).widget() is not None
                and hasattr(panel.release_list.itemAt(i).widget(), "_payload")
            ]
            self.assertEqual(row_ids, ["no-month"])
            panel.close()

    def test_requirement_dialog_roundtrip_keeps_monthly_release_flag(self):
        from panels.requirement_panel import RequirementDialog
        from PyQt6.QtWidgets import QDialogButtonBox
        from ui.dialog_buttons import localize_button_box

        dialog = RequirementDialog({"title": "按月上线", "online_month": "2026-08", "is_monthly_release": True, "sql_parts": [], "source_files": []})
        self.assertTrue(dialog.monthly_release.isChecked())
        self.assertTrue(dialog.values()["is_monthly_release"])
        boxes = dialog.findChildren(QDialogButtonBox)
        self.assertTrue(boxes)
        for box in boxes:
            localize_button_box(box, 'zh')  # 幂等
            cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
            if cancel is not None:
                self.assertEqual(cancel.text(), '取消')
        dialog.close()


if __name__ == "__main__":
    unittest.main()
