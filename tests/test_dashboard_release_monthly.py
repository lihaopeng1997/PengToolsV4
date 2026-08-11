# -*- coding: utf-8 -*-
"""待升级事项：月份归属、独立完成态、布局契约、刷新保留月份。"""

from __future__ import annotations

import datetime
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ReleaseMonthAttributionTests(unittest.TestCase):
    def test_release_month_for_rules(self):
        from tools.dashboard_release_items import collect_release_months, release_month_for

        today = datetime.date(2026, 8, 11)
        self.assertEqual(
            release_month_for(
                {"is_monthly_release": True, "online_month": "2026-09"},
                today=today,
            ),
            "2026-09",
        )
        self.assertEqual(
            release_month_for(
                {"is_monthly_release": True, "online_month": ""},
                fallback_current=True,
                today=today,
            ),
            "2026-08",
        )
        self.assertEqual(
            release_month_for(
                {"is_monthly_release": False, "online_month": "2026-08"},
                today=today,
            ),
            "",
        )
        months = collect_release_months(
            [
                {"is_monthly_release": True, "online_month": "2026-09"},
                {"is_monthly_release": True, "online_month": ""},
                {"is_monthly_release": False, "online_month": "2026-07"},
            ],
            today=today,
        )
        self.assertEqual(months, ["2026-09", "2026-08"])

    def test_board_completion_ignores_business_status(self):
        from tools.dashboard_release_items import is_board_item_completed

        item = {"id": "a", "status": "已上线"}
        self.assertFalse(is_board_item_completed(item, "2026-08", set()))
        self.assertTrue(is_board_item_completed(item, "2026-08", {"a@2026-08"}))


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

    def test_empty_month_checked_enters_current_month_and_combo(self):
        import datetime as dt
        from panels.dashboard_panel import DashboardPanel

        current = dt.date.today().strftime("%Y-%m")
        requirements = [
            {"id": "no-month", "title": "空月入选", "online_month": "", "is_monthly_release": True, "status": "开发中"},
            {"id": "off", "title": "未勾选", "online_month": current, "is_monthly_release": False, "status": "开发中"},
            {"id": "future", "title": "下月", "online_month": "2099-01", "is_monthly_release": True, "status": "待测试"},
        ]
        board = {"completed_requirement_keys": [], "ui_prefs": {"completed_section_collapsed": True}}
        with patch("panels.dashboard_panel.load_requirements", return_value=requirements), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            panel.refresh()
            months = [panel.release_month_combo.itemData(i) for i in range(panel.release_month_combo.count())]
            self.assertIn(current, months)
            self.assertIn("2099-01", months)
            self.assertEqual(panel.release_month_combo.currentData(), current)
            ids = [r._payload["id"] for r in self._task_rows(panel)]
            self.assertEqual(ids, ["no-month"])
            self.assertNotIn("off", ids)
            panel.close()

    def test_manual_month_selection_survives_plain_refresh(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "a", "title": "A", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"},
            {"id": "b", "title": "B", "online_month": "2026-09", "is_monthly_release": True, "status": "待测试"},
        ]
        board = {"completed_requirement_keys": [], "ui_prefs": {"completed_section_collapsed": True}}
        with patch("panels.dashboard_panel.load_requirements", return_value=requirements), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            panel.refresh()
            panel.release_month_combo.blockSignals(True)
            panel.release_month_combo.setCurrentIndex(panel.release_month_combo.findData("2026-09"))
            panel.release_month_combo.blockSignals(False)
            panel._fill_release_items(requirements, board)
            self.assertEqual([r._payload["id"] for r in self._task_rows(panel)], ["b"])
            panel.refresh(preferred_release_month=None)
            self.assertEqual(panel.release_month_combo.currentData(), "2026-09")
            self.assertEqual([r._payload["id"] for r in self._task_rows(panel)], ["b"])
            panel.close()

    def test_mixed_months_filter_correct(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "aug", "title": "八月", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"},
            {"id": "sep", "title": "九月", "online_month": "2026-09", "is_monthly_release": True, "status": "待测试"},
            {"id": "aug-off", "title": "未勾", "online_month": "2026-08", "is_monthly_release": False, "status": "开发中"},
        ]
        board = {"completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_requirements", return_value=requirements), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            panel._fill_release(requirements, preferred_release_month="2026-08")
            self.assertEqual([r._payload["id"] for r in self._task_rows(panel)], ["aug"])
            panel._fill_release(requirements, preferred_release_month="2026-09")
            self.assertEqual([r._payload["id"] for r in self._task_rows(panel)], ["sep"])
            panel.close()

    def test_board_complete_does_not_change_requirement_status(self):
        from panels.dashboard_panel import DashboardPanel

        requirement = {
            "id": "r1",
            "code": "REQ-1",
            "title": "独立完成",
            "online_month": "2026-08",
            "is_monthly_release": True,
            "status": "开发中",
        }
        board = {
            "completed_requirement_keys": [],
            "ui_prefs": {"completed_section_collapsed": False},
        }

        def persist(updated):
            board["completed_requirement_keys"] = list(updated.get("completed_requirement_keys") or [])
            board["ui_prefs"] = dict(updated.get("ui_prefs") or board.get("ui_prefs") or {})

        with patch("panels.dashboard_panel.load_requirements", return_value=[requirement]), \
                patch("panels.dashboard_panel.load_release_board", return_value=board), \
                patch("panels.dashboard_panel.save_release_board", side_effect=persist):
            panel = DashboardPanel("zh")
            panel._fill_release([requirement])
            panel._set_release_item_completed("requirement", requirement, "2026-08", True)
            self.assertEqual(requirement["status"], "开发中")
            self.assertIn("r1@2026-08", board["completed_requirement_keys"])
            panel.close()

    def test_layout_natural_height_single_and_many(self):
        from panels.dashboard_panel import DashboardPanel, TaskRow

        one = [{
            "id": "one", "title": "单条", "online_month": "2026-08",
            "is_monthly_release": True, "status": "开发中",
        }]
        many = [
            {
                "id": f"r{i}", "title": f"T{i}", "online_month": "2026-08",
                "is_monthly_release": True, "status": "开发中",
            }
            for i in range(12)
        ]
        board = {"completed_requirement_keys": [], "ui_prefs": {"completed_section_collapsed": True}}
        with patch("panels.dashboard_panel.load_release_board", return_value=board):
            with patch("panels.dashboard_panel.load_requirements", return_value=one):
                panel = DashboardPanel("zh")
                panel.resize(1200, 800)
                panel.show()
                self.app.processEvents()
                panel.refresh()
                self.app.processEvents()
                one_h = panel.release_scroll.height()
                self.assertLessEqual(one_h, TaskRow.ROW_HEIGHT + 8)
                self.assertEqual(panel.recent_card.height(), panel.release_card.height())
                panel.close()

            with patch("panels.dashboard_panel.load_requirements", return_value=many):
                panel = DashboardPanel("zh")
                panel.resize(1200, 800)
                panel.show()
                self.app.processEvents()
                panel.refresh()
                self.app.processEvents()
                max_h = 8 * TaskRow.ROW_HEIGHT + 7 * TaskRow.LIST_SPACING
                self.assertEqual(panel.release_scroll.height(), max_h)
                self.assertGreater(panel._count_task_rows(panel.release_list), 8)
                self.assertEqual(panel.recent_card.height(), panel.release_card.height())
                panel.close()

    def test_narrow_mode_independent_card_heights(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "a", "title": "A", "online_month": "2026-08", "is_monthly_release": True, "status": "开发中"},
        ]
        board = {"completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_requirements", return_value=requirements), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            panel.resize(900, 700)
            panel.apply_layout_mode("narrow", low_height=False)
            panel.show()
            self.app.processEvents()
            self.assertEqual(panel.tasks_row.direction().name, "TopToBottom")
            # 窄屏不强制两卡同高
            panel.recent_card.setMinimumHeight(0)
            panel.release_card.setMinimumHeight(0)
            panel._apply_list_geometry()
            panel.close()

    def test_saved_monthly_requirement_focuses_month(self):
        from panels.dashboard_panel import DashboardPanel

        requirements = [
            {"id": "future", "title": "下月", "online_month": "2026-09", "is_monthly_release": True, "status": "待测试"},
        ]
        saved = {
            "id": "cur", "title": "本月", "online_month": "2026-08",
            "is_monthly_release": True, "status": "开发中",
        }
        board = {"completed_requirement_keys": []}
        with patch("panels.dashboard_panel.load_requirements", side_effect=lambda: list(requirements)), \
                patch("panels.dashboard_panel.load_release_board", return_value=board):
            panel = DashboardPanel("zh")
            requirements.append(saved)
            panel.refresh_for_requirement(saved)
            self.assertEqual(panel.release_month_combo.currentData(), "2026-08")
            self.assertEqual([r._payload["id"] for r in self._task_rows(panel)], ["cur"])
            panel.close()


class FlagDoneWorkbenchSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont("Microsoft YaHei UI", 10))

    def test_toggle_flag_done_emits_requirements_changed_without_tree_rebuild(self):
        from panels.requirement_panel import RequirementPanel

        req = {
            "id": "f1",
            "title": "标记同步",
            "has_sql": True,
            "flag_done": {"has_sql": False, "needs_peripheral_upgrade": False,
                          "needs_interface_update": False, "temporary_upgrade": False},
            "status": "开发中",
            "updated_at": "2026-08-01T10:00:00",
        }
        with patch("panels.requirement_panel.load_requirements", return_value=[req]), \
                patch("panels.requirement_panel.save_requirements"):
            panel = RequirementPanel()
            panel._requirements = [req]
            panel._current = req
            panel._refresh()
            tree_rebuild = MagicMock()
            panel._refresh_impl = tree_rebuild
            changed = MagicMock()
            panel.requirements_changed.connect(changed)
            panel._toggle_flag_done(req, "has_sql")
            self.assertTrue(changed.called)
            tree_rebuild.assert_not_called()
            self.assertTrue(req["flag_done"]["has_sql"])
            panel.close()

    def test_set_all_flags_done_emits_requirements_changed(self):
        from panels.requirement_panel import RequirementPanel

        req = {
            "id": "f2",
            "title": "批量",
            "has_sql": True,
            "needs_interface_update": True,
            "flag_done": {"has_sql": False, "needs_peripheral_upgrade": False,
                          "needs_interface_update": False, "temporary_upgrade": False},
            "status": "开发中",
        }
        with patch("panels.requirement_panel.load_requirements", return_value=[req]), \
                patch("panels.requirement_panel.save_requirements"):
            panel = RequirementPanel()
            panel._requirements = [req]
            panel._current = req
            changed = MagicMock()
            panel.requirements_changed.connect(changed)
            panel._set_all_flags_done(req, True)
            self.assertTrue(changed.called)
            self.assertTrue(req["flag_done"]["has_sql"])
            panel.close()


if __name__ == "__main__":
    unittest.main()
