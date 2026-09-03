# -*- coding: utf-8 -*-
"""Dashboard 共享 summary / 倒计时 / Native-Web 同源。"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class DashboardSummaryContractTests(unittest.TestCase):
    def _reqs(self):
        return [
            {
                "id": "a",
                "code": "REQ-A",
                "title": "九月实际上线",
                "status": "开发中",
                "actual_online_date": "2026-09-01",
                "planned_online_date": "2026-08-30",
                "test_points": [{"id": "1", "text": "t", "done": True}],
            },
            {
                "id": "b",
                "code": "REQ-B",
                "title": "九月计划",
                "status": "待测试",
                "planned_online_date": "2026-09-08",
                "test_points": [{"id": "1", "text": "t", "done": False}, {"id": "2", "text": "u", "done": True}],
            },
            {
                "id": "c",
                "code": "REQ-C",
                "title": "无日期",
                "status": "待分析",
                "is_monthly_release": True,
            },
            {
                "id": "d",
                "code": "REQ-D",
                "title": "下月",
                "status": "开发中",
                "planned_online_date": "2026-10-03",
            },
            {
                "id": "e",
                "code": "REQ-E",
                "title": "已完成无上线",
                "status": "已完成",
                "planned_online_date": "2026-09-25",
            },
        ]

    def test_summary_deterministic(self):
        from tools.dashboard_summary import build_dashboard_summary

        today = datetime.date(2026, 9, 2)
        reports = {"2026-09-01": {"content": "x"}, "2026-09-02": {"content": "y"}}
        summary = build_dashboard_summary(
            today=today,
            language="zh",
            username="tester",
            requirements=self._reqs(),
            board={},
            reports=reports,
        )
        self.assertEqual(summary["stats"]["req_open"], 4)  # 排除已完成
        self.assertEqual(summary["stats"]["daily_total"], 5)
        self.assertEqual(summary["stats"]["daily_done"], 2)
        self.assertEqual(summary["stats"]["daily_note"], "今日已完成")
        self.assertEqual(summary["release"]["total"], 3)  # a,b,e in September
        self.assertEqual(summary["release"]["done"], 1)  # only actual date
        self.assertEqual(summary["release"]["days_left"], 6)  # 09-08
        self.assertEqual(summary["release"]["countdown_state"], "future")
        self.assertIn("09-08", summary["release"]["date_text"])
        self.assertEqual(summary["username"], "tester")

    def test_manual_target_wins_and_clear_resumes_auto(self):
        from tools.dashboard_summary import resolve_release_countdown

        today = datetime.date(2026, 9, 2)
        reqs = self._reqs()
        manual = resolve_release_countdown(reqs, {"release_target_date": "2026-09-18"}, today)
        self.assertEqual(manual["target_date"], "2026-09-18")
        self.assertEqual(manual["source"], "manual")
        self.assertEqual(manual["days_left"], 16)
        auto = resolve_release_countdown(reqs, {"release_target_date": ""}, today)
        self.assertEqual(auto["target_date"], "2026-09-08")
        self.assertEqual(auto["source"], "auto")

    def test_auto_countdown_rules(self):
        from tools.dashboard_summary import auto_release_target_date

        today = datetime.date(2026, 9, 2)
        self.assertEqual(auto_release_target_date(self._reqs(), today), "2026-09-08")
        self.assertEqual(auto_release_target_date([], today), "")
        past_only = [{"planned_online_date": "2026-09-01"}]
        self.assertEqual(auto_release_target_date(past_only, today), "")

    def test_web_native_same_builder(self):
        from tools.dashboard_summary import build_dashboard_summary

        today = datetime.date(2026, 9, 2)
        kwargs = dict(
            today=today,
            language="zh",
            username="Lihp",
            requirements=self._reqs(),
            board={"release_target_date": ""},
            reports={},
        )
        web = build_dashboard_summary(**kwargs)
        native = build_dashboard_summary(**kwargs)
        for key in ("req_open", "daily_done", "daily_total", "daily_note"):
            self.assertEqual(web["stats"][key], native["stats"][key])
        for key in ("total", "done", "days_left", "date_text", "countdown_state"):
            self.assertEqual(web["release"][key], native["release"][key])
        self.assertEqual(web["monthly_release_tasks"], native["monthly_release_tasks"])


class TestPointsCompactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_requirement_edit_uses_compact_editor(self):
        from panels.requirement_panel import RequirementDialog

        dialog = RequirementDialog({
            "title": "t",
            "description": "",
            "test_points": [],
            "sql_parts": [],
            "source_files": [],
        })
        try:
            editor = dialog.test_points_editor
            self.assertTrue(editor._compact)
            self.assertLessEqual(editor.scroll.minimumHeight(), 80)
            self.assertLessEqual(editor.scroll.maximumHeight(), 180)
            self.assertGreater(editor.scroll.maximumHeight(), 80)
            self.assertEqual(editor.empty.text(), "暂无测试点")
            self.assertEqual(editor.points(), [])
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
