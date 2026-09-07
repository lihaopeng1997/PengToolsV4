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
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
             patch('panels.requirement_panel.load_systems', return_value=[]):
            panel = RequirementPanel()
            panel._sync_toolbar_more_menu()
            if hasattr(panel, '_action_checkout'):
                self.assertTrue(panel._action_checkout.isEnabled())
            if hasattr(panel, '_action_import'):
                self.assertTrue(panel._action_import.isEnabled())

    def test_requirement_toolbar_modes_and_no_orphan_buttons(self):
        """测试 4 种响应式布局下工具栏固定 4 主按钮，无 orphan secondary button，More 始终可达。"""
        from unittest.mock import patch
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
             patch('panels.requirement_panel.load_systems', return_value=[]):
            panel = RequirementPanel()
            self.assertFalse(hasattr(panel, 'checkout_btn'))
            self.assertFalse(hasattr(panel, 'import_btn'))
            self.assertFalse(hasattr(panel, 'system_config_btn'))
            more_actions = panel.toolbar_more_menu.actions()
            self.assertIn(panel._action_checkout, more_actions)
            self.assertIn(panel._action_import, more_actions)
            self.assertIn(panel._action_syscfg, more_actions)

            for mode in ('wide', 'standard', 'compact', 'narrow'):
                panel.apply_layout_mode(mode)
                self.assertFalse(panel.scan_btn.isHidden())
                self.assertFalse(panel.update_all_btn.isHidden())
                self.assertFalse(panel.bug_btn.isHidden())
                self.assertFalse(panel.toolbar_more_btn.isHidden())

    def test_native_dashboard_save_refresh(self):
        """测试 MainWindow._push_dashboard_summary 传入 requirement 字典时调用 refresh_for_requirement 保留月份聚焦。"""
        from unittest.mock import MagicMock, patch
        from main_window import MainWindow
        with patch.object(MainWindow, '__init__', return_value=None):
            mw = MainWindow()
            mock_dash = MagicMock()
            mw.dashboard_panel = mock_dash
            mock_bridge = MagicMock()
            mw._dash_bridge = mock_bridge

            req = {'id': 'REQ-1', 'actual_release_date': '2026-10-15'}
            mw._push_dashboard_summary(req)

            mock_dash.refresh_for_requirement.assert_called_once_with(req)
            mock_bridge.push_summary.assert_called_once()

    def test_requirement_more_method_count_is_one(self):
        """静态断言 RequirementPanel 中 _sync_toolbar_more_menu 方法定义数量必须严格为 1。"""
        import inspect
        from panels import requirement_panel
        source = inspect.getsource(requirement_panel)
        matches = [line for line in source.splitlines() if line.strip().startswith('def _sync_toolbar_more_menu(')]
        self.assertEqual(len(matches), 1, f'_sync_toolbar_more_menu 出现多份定义: {len(matches)}')

    def test_no_planned_field_writes_in_requirements_module(self):
        """静态断言 tools/requirements.py 中不得有任何 planned_* 字段的写入赋值。"""
        import inspect
        from tools import requirements
        source = inspect.getsource(requirements)
        lines = source.splitlines()
        write_matches = []
        for idx, line in enumerate(lines, 1):
            if any(key in line for key in ("'planned_", '"planned_', 'planned_online_date', 'planned_release_date')):
                stripped = line.strip()
                if '=' in stripped and not stripped.startswith('#'):
                    # 允许只读 fallback: e.g. req.get('planned_...')
                    if '==' in stripped or '!=' in stripped:
                        continue
                    if 'get(' in stripped and stripped.find('=') < stripped.find('get('):
                        # This is a read: var = req.get('planned_...')
                        continue
                    write_matches.append(f'L{idx}: {stripped}')
        self.assertEqual(write_matches, [], f'发现生产写入 planned_* 字段: {write_matches}')


if __name__ == '__main__':
    unittest.main()

