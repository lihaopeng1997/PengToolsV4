# -*- coding: utf-8 -*-
"""需求与月度上线任务数据契约验收测试（Round 4-V2E 10 项标准）。

1. actual_release_date=当前月 => 出现在本月上线任务
2. actual_release_date=下个月 => 不出现在本月上线任务
3. 无 actual_release_date => 不进入任何月份上线任务
4. 不存在 is_monthly_release 也能完整判断本月任务
5. 拖拽 2026-09-18 -> 2026-10 => actual_release_date == 2026-10-18
6. 拖拽 2026-01-31 -> 2026-02 => 日期自动 clamp 到 2 月最后一天 (2026-02-28, 闰年 2024-02-29)
7. 拖出当前月 => 首页本月上线任务移除
8. 拖入当前月 => 首页本月上线任务加入
9. 保存 / 重载后月份与日期仍正确且无 is_monthly_release / planned 字段
10. 编辑页 RequirementDialog 不存在“是否本月上线”和“计划上线日期”
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class RequirementReleaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_01_actual_date_current_month_appears_in_monthly_tasks(self):
        """1. actual_release_date=当前月 => 出现在本月上线任务"""
        from tools.dashboard_summary import monthly_release_tasks

        today = datetime.date(2026, 9, 7)
        reqs = [
            {
                "id": "req-cur",
                "code": "REQ-001",
                "title": "当前月需求",
                "actual_release_date": "2026-09-15",
                "status": "开发中",
            }
        ]
        tasks = monthly_release_tasks(reqs, "2026-09", today)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "req-cur")
        self.assertEqual(tasks[0]["actual_release_date"], "2026-09-15")

    def test_02_actual_date_next_month_does_not_appear_in_current_month_tasks(self):
        """2. actual_release_date=下个月 => 不出现在本月上线任务"""
        from tools.dashboard_summary import monthly_release_tasks

        today = datetime.date(2026, 9, 7)
        reqs = [
            {
                "id": "req-next",
                "code": "REQ-002",
                "title": "下个月需求",
                "actual_release_date": "2026-10-15",
                "status": "开发中",
            }
        ]
        tasks = monthly_release_tasks(reqs, "2026-09", today)
        self.assertEqual(len(tasks), 0)

    def test_03_no_actual_release_date_does_not_enter_any_monthly_tasks(self):
        """3. 无 actual_release_date => 不进入任何月份上线任务"""
        from tools.dashboard_summary import monthly_release_tasks
        from tools.dashboard_release_items import collect_release_months, effective_release_month

        today = datetime.date(2026, 9, 7)
        reqs = [
            {
                "id": "req-none",
                "code": "REQ-003",
                "title": "无日期需求",
                "status": "待分析",
            }
        ]
        self.assertEqual(effective_release_month(reqs[0]), "")
        self.assertEqual(collect_release_months(reqs), [])
        self.assertEqual(monthly_release_tasks(reqs, "2026-09", today), [])
        self.assertEqual(monthly_release_tasks(reqs, "2026-10", today), [])

    def test_04_absence_of_is_monthly_release_supported(self):
        """4. 不存在 is_monthly_release 也能完整判断本月任务"""
        from tools.dashboard_summary import monthly_release_tasks
        from tools.dashboard_release_items import effective_release_month

        today = datetime.date(2026, 9, 7)
        req = {
            "id": "req-pure",
            "code": "REQ-004",
            "title": "无任何废弃字段的需求",
            "actual_release_date": "2026-09-20",
            "status": "测试中",
        }
        self.assertNotIn("is_monthly_release", req)
        self.assertEqual(effective_release_month(req), "2026-09")
        tasks = monthly_release_tasks([req], "2026-09", today)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "req-pure")

    def test_05_update_month_keeps_day(self):
        """5. 拖拽 2026-09-18 -> 2026-10 => actual_release_date == 2026-10-18"""
        from tools.requirements import update_release_date_month

        result = update_release_date_month("2026-09-18", "2026-10")
        self.assertEqual(result, "2026-10-18")

    def test_06_update_month_clamps_to_month_end(self):
        """6. 拖拽 2026-01-31 -> 2026-02 => 日期自动 clamp 到 2 月最后一天"""
        from tools.requirements import update_release_date_month

        # 平年 2026
        result_2026 = update_release_date_month("2026-01-31", "2026-02")
        self.assertEqual(result_2026, "2026-02-28")

        # 闰年 2024
        result_2024 = update_release_date_month("2024-01-31", "2024-02")
        self.assertEqual(result_2024, "2024-02-29")

        # 31 号拖到 30 天月份 (如 2026-03-31 -> 2026-04)
        result_30 = update_release_date_month("2026-03-31", "2026-04")
        self.assertEqual(result_30, "2026-04-30")

    def test_07_and_08_drag_out_and_drag_in_syncs_home_monthly_tasks(self):
        """7. 拖出当前月 => 首页本月上线任务移除; 8. 拖入当前月 => 首页本月上线任务加入"""
        from panels.requirement_panel import RequirementPanel
        from tools.dashboard_summary import build_dashboard_summary

        today = datetime.date(2026, 9, 7)
        req = {
            "id": "req-sync",
            "code": "REQ-SYNC",
            "title": "同步测试需求",
            "actual_release_date": "2026-09-18",
            "status": "开发中",
        }

        with patch("panels.requirement_panel.load_requirements", return_value=[req]), \
                patch("panels.requirement_panel.save_requirements"):
            panel = RequirementPanel()
            panel._requirements = [req]
            panel._current = req

            signal_spy = MagicMock()
            panel.requirements_changed.connect(signal_spy)

            # 初始状态：在 2026-09
            summary_init = build_dashboard_summary(today=today, requirements=panel._requirements)
            cur_ids = [t["id"] for t in summary_init["monthly_release_tasks"]]
            self.assertIn("req-sync", cur_ids)

            # 7. 拖拽移出当前月 -> 2026-10
            panel._move_requirements([req], "2026-10")
            self.assertEqual(req["actual_release_date"], "2026-10-18")
            self.assertEqual(req.get("actual_online_date"), "2026-10-18")
            self.assertTrue(signal_spy.called)

            summary_after_move = build_dashboard_summary(today=today, requirements=panel._requirements)
            cur_ids_after = [t["id"] for t in summary_after_move["monthly_release_tasks"]]
            self.assertNotIn("req-sync", cur_ids_after)

            # 8. 拖拽移回当前月 -> 2026-09
            signal_spy.reset_mock()
            panel._move_requirements([req], "2026-09")
            self.assertEqual(req["actual_release_date"], "2026-09-18")
            self.assertTrue(signal_spy.called)

            summary_after_return = build_dashboard_summary(today=today, requirements=panel._requirements)
            cur_ids_return = [t["id"] for t in summary_after_return["monthly_release_tasks"]]
            self.assertIn("req-sync", cur_ids_return)

            panel.close()

    def test_09_save_and_reload_preserves_correct_contract(self):
        """9. 保存 / 重载后月份仍正确且无 is_monthly_release / planned_date 废弃字段"""
        from tools.requirements import normalize_requirement, save_requirements, load_requirements

        legacy_input = {
            "id": "req-legacy",
            "code": "REQ-LEGACY",
            "title": "旧格式输入",
            "status": "开发中",
            "actual_release_date": "2026-09-22",
            "is_monthly_release": True,
            "planned_online_date": "2026-09-10",
            "planned_release_date": "2026-09-10",
            "plan_release_date": "2026-09-10",
        }

        normalized = normalize_requirement(legacy_input)
        self.assertEqual(normalized["actual_release_date"], "2026-09-22")
        self.assertEqual(normalized["actual_online_date"], "2026-09-22")
        self.assertNotIn("is_monthly_release", normalized)
        self.assertNotIn("planned_online_date", normalized)
        self.assertNotIn("planned_release_date", normalized)
        self.assertNotIn("plan_release_date", normalized)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_file = tf.name

        try:
            save_requirements([normalized], path=temp_file)
            loaded = load_requirements(temp_file)
            self.assertEqual(len(loaded), 1)
            loaded_item = loaded[0]
            self.assertEqual(loaded_item["actual_release_date"], "2026-09-22")
            self.assertEqual(loaded_item["actual_online_date"], "2026-09-22")
            self.assertNotIn("is_monthly_release", loaded_item)
            self.assertNotIn("planned_online_date", loaded_item)
            self.assertNotIn("planned_release_date", loaded_item)
            self.assertNotIn("plan_release_date", loaded_item)
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def test_10_edit_dialog_has_no_is_monthly_release_and_no_planned_date(self):
        """10. 编辑页不存在“是否本月上线”和“计划上线日期”"""
        from panels.requirement_panel import RequirementDialog

        req = {
            "title": "测试对话框",
            "actual_release_date": "2026-09-15",
            "status": "开发中",
        }
        dialog = RequirementDialog(req)

        # 检查内部属性不存在
        self.assertFalse(hasattr(dialog, "monthly_release"))
        self.assertFalse(hasattr(dialog, "planned_date"))
        self.assertTrue(hasattr(dialog, "actual_date"))

        # 检查对话框导出的数据字典
        data = dialog.values()
        self.assertNotIn("is_monthly_release", data)
        self.assertNotIn("planned_online_date", data)
        self.assertNotIn("planned_release_date", data)
        self.assertNotIn("plan_release_date", data)
        self.assertEqual(data.get("actual_release_date"), "2026-09-15")
        self.assertEqual(data.get("actual_online_date"), "2026-09-15")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
