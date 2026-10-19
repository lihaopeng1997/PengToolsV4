# -*- coding: utf-8 -*-
"""Home requirement-section navigation with synthetic IDs and no persistence."""
import os
import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from main_window import MainWindow
from ui.web_shell import HomeBridge, validate_requirement_section_target
from tools.dashboard_summary import build_dashboard_summary


class DashboardRequirementActionsTest(unittest.TestCase):
    def test_bridge_emits_only_valid_section_targets(self):
        bridge = HomeBridge()
        events = []
        navigations = []
        views = []
        bridge.openRequirementSectionRequested.connect(lambda req_id, section: events.append((req_id, section)))
        bridge.navigateRequested.connect(navigations.append)
        bridge.openRequirementRequested.connect(views.append)

        for req_id, section in (
            ('', 'completion'),
            ('   ', 'completion'),
            ('DEMO-1', 'test_points'),
            ('demo-1', 'completion'),
            ('REQ-1', 'wrong_section'),
            ('REQ-1', 'Completion'),
        ):
            bridge.openRequirementSection(req_id, section)
        self.assertEqual(events, [])

        bridge.openRequirementSection(' REQ-1 ', 'completion')
        bridge.openRequirementSection('REQ-2', 'test_points')
        self.assertEqual(events, [('REQ-1', 'completion'), ('REQ-2', 'test_points')])
        self.assertEqual(navigations, [])
        self.assertEqual(views, [])

    def test_host_rechecks_target_before_creating_panel(self):
        ensure_panel = Mock()
        show_panel = Mock()
        host = SimpleNamespace(_ensure_requirement_panel=ensure_panel, _show_panel=show_panel)

        for req_id, section in (('', 'completion'), ('DEMO-1', 'test_points'), ('REQ-1', 'unknown')):
            self.assertFalse(MainWindow._on_web_open_requirement_section(host, req_id, section))
        ensure_panel.assert_not_called()
        show_panel.assert_not_called()

    def test_host_navigates_only_after_exact_focus_succeeds(self):
        focus = Mock(side_effect=[False, True, True])
        panel = SimpleNamespace(focus_requirement_section=focus)
        show_panel = Mock()
        host = SimpleNamespace(_ensure_requirement_panel=Mock(return_value=panel), _show_panel=show_panel)

        self.assertFalse(MainWindow._on_web_open_requirement_section(host, 'MISSING', 'completion'))
        show_panel.assert_not_called()
        self.assertTrue(MainWindow._on_web_open_requirement_section(host, 'REQ-1', 'completion'))
        self.assertTrue(MainWindow._on_web_open_requirement_section(host, 'REQ-1', 'test_points'))
        self.assertEqual(focus.call_args_list, [
            unittest.mock.call('MISSING', 'completion'),
            unittest.mock.call('REQ-1', 'completion'),
            unittest.mock.call('REQ-1', 'test_points'),
        ])
        self.assertEqual(show_panel.call_args_list, [unittest.mock.call(10), unittest.mock.call(10)])

    def test_host_fail_closed_when_panel_cannot_focus(self):
        show_panel = Mock()
        panel = SimpleNamespace(focus_requirement_section=Mock(side_effect=RuntimeError('not ready')))
        host = SimpleNamespace(_ensure_requirement_panel=Mock(return_value=panel), _show_panel=show_panel)

        self.assertFalse(MainWindow._on_web_open_requirement_section(host, 'REQ-1', 'completion'))
        show_panel.assert_not_called()

    def test_host_reports_failed_page_switch(self):
        panel = SimpleNamespace(focus_requirement_section=Mock(return_value=True))
        host = SimpleNamespace(
            _ensure_requirement_panel=Mock(return_value=panel),
            _show_panel=Mock(return_value=False),
        )
        self.assertFalse(MainWindow._on_web_open_requirement_section(host, 'REQ-1', 'test_points'))
        host._show_panel.assert_called_once_with(10)

    def test_target_normalizes_id_without_changing_section(self):
        self.assertEqual(validate_requirement_section_target(' REQ-1 ', 'test_points'), ('REQ-1', 'test_points'))
        self.assertIsNone(validate_requirement_section_target('REQ-1', 'test_points '))

    def test_summary_status_and_test_points_follow_existing_fields(self):
        requirement = {
            'id': 'REQ-1', 'title': 'Synthetic release', 'status': '开发中',
            'actual_release_date': '2026-09-20',
            'test_points': [{'id': 'tp-1', 'text': 'Synthetic point', 'done': False}],
            'has_sql': True, 'flag_done': {'has_sql': True},
        }
        bridge = HomeBridge()
        bridge.set_summary_provider(lambda: build_dashboard_summary(
            requirements=[requirement], board={}, reports={}, tools=[],
            today=datetime.date(2026, 9, 30)))

        def task():
            return json.loads(bridge.dashboardSummary())['monthly_release_tasks'][0]

        self.assertFalse(task()['done'])
        self.assertEqual(task()['test_points'], '0/1')
        requirement['status'] = '已完成'
        requirement['test_points'][0]['done'] = True
        self.assertTrue(task()['done'])
        self.assertEqual(task()['test_points'], '1/1')


if __name__ == '__main__':
    unittest.main()
