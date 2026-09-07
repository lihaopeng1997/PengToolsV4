import datetime
import os
import sys
import unittest
from PyQt6.QtWidgets import QApplication

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

app = QApplication.instance()
if app is None:
    app = QApplication([])

from tools.dashboard_summary import build_dashboard_summary, resolve_release_countdown
from tools.requirements import normalize_requirement
from ui.web_shell import HomeBridge
from panels.requirement_panel import RequirementDialog, RequirementPanel, DateInput


class TestRound4V2EReviewFix1(unittest.TestCase):
    def test_data_center_quick_tool_navigates_to_oracle(self):
        summary = build_dashboard_summary(requirements=[], today=datetime.date(2026, 9, 7))
        tools = summary.get('tools', [])
        dc_tool = next((t for t in tools if t['zh'] == '数据中心'), None)
        self.assertIsNotNone(dc_tool)
        self.assertEqual(dc_tool['i'], 18)

    def test_home_bridge_signals_and_slots(self):
        bridge = HomeBridge()
        created = []
        opened = []
        summaries = []

        bridge.createRequirementRequested.connect(lambda: created.append(True))
        bridge.openRequirementRequested.connect(lambda req_id: opened.append(req_id))
        bridge.summaryChanged.connect(lambda payload: summaries.append(payload))

        bridge.createRequirement()
        self.assertEqual(created, [True])

        bridge.openRequirement('REQ-101')
        self.assertEqual(opened, ['REQ-101'])

        bridge.set_summary_provider(lambda: {'monthly_release_tasks': [{'id': 'REQ-101'}]})
        payload = bridge.push_summary()
        self.assertEqual(len(summaries), 1)
        self.assertIn('REQ-101', payload)

    def test_legacy_planned_only_migration_preserves_date(self):
        legacy = {
            'id': 'REQ-LEGACY-01',
            'title': '历史老需求',
            'planned_online_date': '2026-09-18',
        }
        normalized = normalize_requirement(legacy)
        self.assertEqual(normalized['actual_release_date'], '2026-09-18')
        self.assertEqual(normalized['actual_online_date'], '2026-09-18')
        self.assertEqual(normalized['online_month'], '2026-09')
        self.assertNotIn('planned_online_date', normalized)

    def test_requirement_dialog_single_actual_date_edit_source(self):
        dialog = RequirementDialog()
        date_inputs = dialog.findChildren(DateInput)
        self.assertEqual(len(date_inputs), 1)
        self.assertIs(date_inputs[0], dialog.actual_date)
        self.assertFalse(hasattr(dialog, 'online_month'))
        dialog.actual_date.edit.setText('2026-10-25')
        vals = dialog.values()
        self.assertEqual(vals['actual_release_date'], '2026-10-25')
        self.assertEqual(vals['online_month'], '2026-10')

    def test_production_summary_excludes_mock_when_real_items_exist(self):
        real_items = [
            {'id': 'REQ-REAL-1', 'title': '真实需求1', 'actual_release_date': '2026-09-20', 'status': '进行中'},
        ]
        summary = build_dashboard_summary(requirements=real_items, today=datetime.date(2026, 9, 7))
        req_ids = [r['id'] for r in summary.get('requirements', [])]
        recent_ids = [r['id'] for r in summary.get('recent', [])]
        monthly_ids = [r['id'] for r in summary.get('monthly_release_tasks', [])]
        for item_id in req_ids + recent_ids + monthly_ids:
            self.assertFalse(str(item_id).startswith('mock-'), f'Found mock item {item_id} in summary')

    def test_release_countdown_has_no_planned_words(self):
        today = datetime.date(2026, 9, 7)
        reqs = [{'actual_release_date': '2026-09-28'}]
        countdown = resolve_release_countdown(reqs, {}, today)
        self.assertNotIn('计划', countdown.get('date_text', ''))
        self.assertNotIn('计划', countdown.get('countdown_text', ''))
        empty_countdown = resolve_release_countdown([], {}, today)
        self.assertNotIn('计划', empty_countdown.get('date_text', ''))
        self.assertNotIn('计划', empty_countdown.get('countdown_text', ''))

    def test_more_menu_sync_toolbar_state(self):
        from unittest.mock import patch
        with patch('panels.requirement_panel.load_requirements', return_value=[]),              patch('panels.requirement_panel.load_systems', return_value=[]):
            panel = RequirementPanel()
            panel._sync_toolbar_more_menu()
            if hasattr(panel, '_action_checkout'):
                self.assertTrue(panel._action_checkout.isEnabled())
            if hasattr(panel, '_action_import'):
                self.assertTrue(panel._action_import.isEnabled())


if __name__ == '__main__':
    unittest.main()
