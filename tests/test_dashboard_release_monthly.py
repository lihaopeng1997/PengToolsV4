# -*- coding: utf-8 -*-
"""待升级事项：分区展示、标记上线同步台账、折叠已上线。"""

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
    def test_board_roundtrip_keeps_monthly_completion_keys_and_prefs(self):
        from tools.dashboard_release_items import load_release_board, save_release_board
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "release_board.json")
            save_release_board(
                {
                    "manual_items": [],
                    "completed_requirement_keys": ["req-a@2026-08"],
                    "ui_prefs": {"completed_section_collapsed": False},
                },
                path,
            )
            board = load_release_board(path)
        self.assertEqual(board["completed_requirement_keys"], ["req-a@2026-08"])
        self.assertFalse(board["ui_prefs"]["completed_section_collapsed"])

    def test_is_release_item_completed_by_status_or_key(self):
        from tools.requirements import is_release_item_completed

        item = {"id": "a", "status": "开发中"}
        self.assertFalse(is_release_item_completed(item, "2026-08", set()))
        self.assertTrue(is_release_item_completed(item, "2026-08", {"a@2026-08"}))
        self.assertTrue(is_release_item_completed({"id": "b", "status": "已上线"}, "2026-08", set()))


class MonthlyReleaseBoardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _task_rows(self, panel):
        rows = []
        for i in range(panel.release_list.count()):
            widget = panel.release_list.itemAt(i).widget()
            if widget is not None and hasattr(widget, "_payload") and hasattr(widget, "title_label"):
                rows.append(widget)
        return rows

    def test_selected_month_only_shows_checked_requirements(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "aug-on", "title": "八月入选", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"},
            {"id": "aug-off", "title": "八月未勾选", "online_month": "2026-08", "is_monthly_release": False, "status": "开发中"},
            {"id": "sep-on", "title": "九月入选", "online_month": "2026-09", "is_monthly_release": True, "status": "待测试"},
        ]
        board = {
            "manual_items": [],
            "hidden_requirement_ids": [],
            "completed_requirement_keys": [],
            "ui_prefs": {"completed_section_collapsed": True},
        }
        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.save_release_board"):
            panel = DashboardPanel("zh")
            panel._fill_release(requirements)
            panel.release_month_combo.blockSignals(True)
            panel.release_month_combo.setCurrentIndex(panel.release_month_combo.findData("2026-08"))
            panel.release_month_combo.blockSignals(False)
            panel._fill_release_items(requirements)

            rows = self._task_rows(panel)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].title_label.text(), "八月入选")
            self.assertIn("待处理 1", panel.release_summary.text())
            self.assertEqual(
                [panel.release_month_combo.itemData(i) for i in range(panel.release_month_combo.count())],
                ["2026-09", "2026-08"],
            )
            panel.close()

    def test_mark_online_writes_status_and_board_key(self):
        from panels.dashboard_panel import DashboardPanel
        import tempfile
        from tools.requirements import load_requirements, save_requirements

        with tempfile.TemporaryDirectory() as temp:
            req_path = os.path.join(temp, "requirements.json")
            board_path = os.path.join(temp, "board.json")
            save_requirements(
                [{
                    "id": "aug-on",
                    "title": "八月入选",
                    "online_month": "2026-08",
                    "is_monthly_release": True,
                    "status": "开发中",
                }],
                path=req_path,
            )
            from tools.dashboard_release_items import save_release_board, load_release_board
            save_release_board({"completed_requirement_keys": []}, path=board_path)

            with patch("tools.requirements.REQUIREMENTS_FILE", req_path), \
                    patch("panels.dashboard_panel.load_requirements", side_effect=lambda: load_requirements(req_path)), \
                    patch("panels.dashboard_panel.load_release_board", side_effect=lambda: load_release_board(board_path)), \
                    patch("panels.dashboard_panel.save_release_board", side_effect=lambda b: save_release_board(b, board_path)), \
                    patch("tools.requirements.load_requirements", side_effect=lambda path=None: load_requirements(req_path)), \
                    patch("tools.requirements.save_requirements", side_effect=lambda items, path=None: save_requirements(items, req_path)):
                panel = DashboardPanel("zh")
                panel._set_release_item_completed(
                    "requirement",
                    {"id": "aug-on", "title": "八月入选"},
                    "2026-08",
                    True,
                )
                loaded = load_requirements(req_path)
                self.assertEqual(loaded[0]["status"], "已上线")
                self.assertTrue(str(loaded[0].get("actual_online_date") or ""))
                board = load_release_board(board_path)
                self.assertIn("aug-on@2026-08", board["completed_requirement_keys"])
                panel.close()

    def test_release_row_button_is_mark_live_and_status_online_shows_completed(self):
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
        board = {
            "manual_items": [],
            "hidden_requirement_ids": [],
            "completed_requirement_keys": [],
            "ui_prefs": {"completed_section_collapsed": False},
        }

        def persist(updated_board):
            # 勿 board.clear() 后再用同一 dict 做 update（会丢字段）
            snapshot = {
                "manual_items": list(updated_board.get("manual_items") or []),
                "hidden_requirement_ids": list(updated_board.get("hidden_requirement_ids") or []),
                "completed_requirement_keys": list(updated_board.get("completed_requirement_keys") or []),
                "ui_prefs": dict(updated_board.get("ui_prefs") or {"completed_section_collapsed": False}),
            }
            board.clear()
            board.update(snapshot)

        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.load_requirements", return_value=[requirement]), \
                patch("panels.dashboard_panel.save_release_board", side_effect=persist), \
                patch("panels.dashboard_panel.mark_requirement_online", return_value={**requirement, "status": "已上线"}), \
                patch("panels.dashboard_panel.confirm_action", return_value=True):
            panel = DashboardPanel("zh")
            panel._fill_release([requirement])
            row = self._task_rows(panel)[0]
            self.assertEqual(row.minimumHeight(), 64)
            self.assertEqual(row.identifier_label.text(), "BUG-20260811-101")
            row.title_label.setFixedWidth(120)
            row._update_title_elision()
            self.assertIn("…", row.title_label.text())

            mark_btn = next(button for button in row.findChildren(QPushButton) if button.text() == "标记上线")
            mark_btn.click()
            self.app.processEvents()
            # 完成后默认可能折叠；此处 board 已写 key，展开后可见
            board["ui_prefs"]["completed_section_collapsed"] = False
            requirement["status"] = "已上线"
            panel._fill_release_items([requirement], board)
            rows = self._task_rows(panel)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status_label.text(), "已上线")
            self.assertIn("bug-long@2026-08", board["completed_requirement_keys"])
            reopen = next(button for button in rows[0].findChildren(QPushButton) if button.text() == "恢复待办")
            self.assertTrue(reopen)
            panel.close()

    def test_completed_section_collapsed_by_default(self):
        from panels.dashboard_panel import DashboardPanel, SectionHeader

        requirements = [
            {"id": "open", "title": "未完成", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"},
            {"id": "done", "title": "已完成", "online_month": "2026-08", "is_monthly_release": True, "status": "已上线"},
        ]
        board = {
            "completed_requirement_keys": [],
            "ui_prefs": {"completed_section_collapsed": True},
        }
        with patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.load_requirements", return_value=requirements):
            panel = DashboardPanel("zh")
            panel._fill_release(requirements)
            ids = [row._payload["id"] for row in self._task_rows(panel)]
            self.assertEqual(ids, ["open"])
            self.assertIn("待处理 1", panel.release_summary.text())
            self.assertIn("已上线 1", panel.release_summary.text())
            headers = [
                panel.release_list.itemAt(i).widget()
                for i in range(panel.release_list.count())
                if isinstance(panel.release_list.itemAt(i).widget(), SectionHeader)
            ]
            self.assertTrue(any("已上线" in h.title_label.text() for h in headers))
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
            self.assertEqual(panel.recent_card.height(), panel.release_card.height())
            before = panel.release_card.height()
            requirements.append(
                {"id": "extra", "title": "额外", "online_month": "2026-08", "is_monthly_release": True, "status": "待测试"}
            )
            panel.refresh()
            self.app.processEvents()
            self.assertEqual(panel.release_card.height(), before)
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
            row_ids = [row._payload["id"] for row in self._task_rows(panel)]
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
            row_ids = [row._payload["id"] for row in self._task_rows(panel)]
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
            row_ids = [row._payload["id"] for row in self._task_rows(panel)]
            self.assertEqual(row_ids, ["no-month"])
            panel.close()


if __name__ == "__main__":
    unittest.main()
