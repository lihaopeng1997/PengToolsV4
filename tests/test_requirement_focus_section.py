# -*- coding: utf-8 -*-
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from panels.requirement_panel import RequirementPanel


_app = QApplication.instance()
if _app is None:
    _app = QApplication(sys.argv or ['test'])


class TestRequirementFocusSection(unittest.TestCase):

    def setUp(self):
        self.patchers = []

        self.mock_systems = [
            {'name': '统一前端', 'code': 'PRPCAR', 'dir': 'front'},
            {'name': '后台服务', 'code': 'PRPBAC', 'dir': 'back'},
        ]
        self.synthetic_requirements = [
            {
                'id': 'REQ-2026-001',
                'code': 'REQ-001',
                'title': '登录模块优化',
                'record_kind': '需求',
                'status': '进行中',
                'system': '统一前端',
                'systems': ['统一前端'],
                'online_month': '2026-07',
                'has_sql': True,
                'sql_parts': [{'name': 'patch.sql', 'content': 'SELECT 1;'}],
                'test_points': [{'id': 'tp-1', 'text': '测试用户名验证', 'done': False}],
                'flag_done': {'has_sql': False},
            },
            {
                'id': 'REQ-2026-002',
                'code': 'REQ-002',
                'title': '界面微调无事项',
                'record_kind': '需求',
                'status': '待分析',
                'system': '统一前端',
                'systems': ['统一前端'],
                'online_month': '2026-07',
                'has_sql': False,
                'needs_peripheral_upgrade': False,
                'needs_interface_update': False,
                'temporary_upgrade': False,
                'sql_parts': [],
                'test_points': [{'id': 'tp-2', 'text': '检查界面边框', 'done': True}],
                'flag_done': {},
            },
            {
                'id': 'REQ-2026-003',
                'code': 'REQ-003',
                'title': '报表导出优化',
                'record_kind': '需求',
                'status': '待分析',
                'system': '后台服务',
                'systems': ['后台服务'],
                'online_month': '2026-08',
                'has_sql': True,
                'needs_peripheral_upgrade': True,
                'sql_parts': [{'name': 'report.sql', 'content': 'SELECT 2;'}],
                'test_points': [],
                'flag_done': {'has_sql': False, 'needs_peripheral_upgrade': False},
            },
        ]

        p_sys = patch('panels.requirement_panel.load_systems', return_value=self.mock_systems)
        p_load_req = patch('panels.requirement_panel.load_requirements', side_effect=lambda: [dict(r) for r in self.synthetic_requirements])
        self.mock_save_req = MagicMock(return_value=True)
        p_save_req = patch('panels.requirement_panel.save_requirements', self.mock_save_req)
        p_load_choices = patch('panels.requirement_panel.load_last_choices', return_value={})
        self.mock_update_choices = MagicMock()
        p_up_choices = patch('panels.requirement_panel.update_last_choices', self.mock_update_choices)
        p_load_ui = patch('panels.requirement_panel.load_requirement_ui', return_value={})
        self.mock_save_ui = MagicMock()
        p_save_ui = patch('panels.requirement_panel.save_requirement_ui', self.mock_save_ui)
        p_split_prefs = patch('ui.splitter_prefs.install_splitter_prefs', return_value=None)

        for p in (p_sys, p_load_req, p_save_req, p_load_choices, p_up_choices, p_load_ui, p_save_ui, p_split_prefs):
            p.start()
            self.patchers.append(p)

        self.panel = RequirementPanel()
        QApplication.processEvents()
        self.mock_save_req.reset_mock()
        self.mock_update_choices.reset_mock()
        self.mock_save_ui.reset_mock()

    def tearDown(self):
        if hasattr(self, 'panel') and self.panel is not None:
            self.panel.deleteLater()
            self.panel = None
        for p in reversed(self.patchers):
            p.stop()
        QApplication.processEvents()

    def test_valid_id_hidden_by_old_search_and_filters_navigation_success(self):
        """有效ID导航：旧搜索/系统/类型/状态过滤被阻断信号清空，并成功定位目标且零save。"""
        self.panel.search_edit.setText('nonexistent_keyword')
        if self.panel.kind_filter.count() > 1:
            self.panel.kind_filter.setCurrentIndex(1)
        if self.panel.status_filter.count() > 1:
            self.panel.status_filter.setCurrentIndex(1)

        ok = self.panel.focus_requirement_section('REQ-2026-001', 'completion')
        self.assertTrue(ok)
        QApplication.processEvents()

        self.assertEqual(self.panel.search_edit.text(), '')
        self.assertEqual(self.panel.kind_filter.currentIndex(), 0)
        self.assertEqual(self.panel.status_filter.currentIndex(), 0)
        self.assertEqual(self.panel.system_filter.currentIndex(), 0)

        self.assertIsNotNone(self.panel._current)
        self.assertEqual(self.panel._current.get('id'), 'REQ-2026-001')

        cur_item = self.panel.requirement_list.currentItem()
        self.assertIsNotNone(cur_item)
        cur_data = cur_item.data(0, Qt.ItemDataRole.UserRole)
        self.assertIsInstance(cur_data, dict)
        self.assertEqual(cur_data.get('id'), 'REQ-2026-001')

        self.mock_save_req.assert_not_called()

    def test_unknown_id_does_not_change_selection_or_filters(self):
        """未知需求ID不改变当前选择或过滤器，且零save。"""
        self.panel._current = self.synthetic_requirements[0]
        self.panel.search_edit.setText('existing_filter')

        ok = self.panel.focus_requirement_section('UNKNOWN-999', 'completion')
        self.assertFalse(ok)
        self.assertEqual(self.panel._current.get('id'), 'REQ-2026-001')
        self.assertEqual(self.panel.search_edit.text(), 'existing_filter')
        self.mock_save_req.assert_not_called()

    def test_demo_id_is_rejected_even_when_present(self):
        self.panel._requirements.append({**self.synthetic_requirements[1], 'id': 'DeMo-fixture'})
        self.panel._current = self.synthetic_requirements[0]
        self.panel.search_edit.setText('existing_filter')
        with patch('panels.requirement_panel.load_requirements') as load:
            self.assertFalse(self.panel.focus_requirement_section('  DeMo-fixture  ', 'completion'))
        load.assert_not_called()
        self.assertEqual(self.panel._current.get('id'), 'REQ-2026-001')
        self.assertEqual(self.panel.search_edit.text(), 'existing_filter')
        self.mock_save_req.assert_not_called()

    def test_invalid_section_does_not_change_selection_or_filters(self):
        """非法/未知 section（如 demo、invalidsection）不改变当前状态并返回False。"""
        self.panel._current = self.synthetic_requirements[0]
        self.panel.search_edit.setText('my_query')

        self.assertFalse(self.panel.focus_requirement_section('REQ-2026-001', 'demo'))
        self.assertFalse(self.panel.focus_requirement_section('REQ-2026-001', 'invalidsection'))
        self.assertFalse(self.panel.focus_requirement_section('REQ-2026-001', ''))
        self.assertFalse(self.panel.focus_requirement_section('REQ-2026-001', None))

        self.assertEqual(self.panel._current.get('id'), 'REQ-2026-001')
        self.assertEqual(self.panel.search_edit.text(), 'my_query')
        self.mock_save_req.assert_not_called()

    def test_completion_opens_status_dialog_and_cancel_zero_write(self):
        """completion 在面板可见时于下个事件循环打开 RequirementDialog 并在 exec 前聚焦 status_combo；取消零写、不自动改 status。"""
        self.panel.show()
        with patch.object(self.panel, '_save_dialog') as mock_sd:
            ok = self.panel.focus_requirement_section('REQ-2026-001', 'completion')
            self.assertTrue(ok)
            QApplication.processEvents()

            mock_sd.assert_called_once_with(self.panel._current, offer_next=False, focus_section='completion')

        # 真实测试 _save_dialog(focus_section='completion') 的 status_combo 聚焦与取消零写行为
        with patch('panels.requirement_panel.RequirementDialog.exec', return_value=QDialog.DialogCode.Rejected):
            with patch('panels.requirement_panel.RequirementDialog.__init__', return_value=None) as mock_init:
                mock_dlg = MagicMock()
                mock_dlg.status_combo = MagicMock()
                mock_dlg.exec.return_value = QDialog.DialogCode.Rejected
                with patch('panels.requirement_panel.RequirementDialog', return_value=mock_dlg):
                    res = self.panel._save_dialog(self.panel._current, offer_next=False, focus_section='completion')
                    self.assertIsNone(res)
                    mock_dlg.status_combo.setFocus.assert_called_once()
                    self.mock_save_req.assert_not_called()
                    self.assertEqual(self.panel._current.get('status'), '进行中')

    def test_completion_works_even_without_active_flags(self):
        """需求无 active flag 时，completion 仍然成功定位，并在下个事件循环触发打开 dialog。"""
        self.panel.show()
        # REQ-2026-002 无任何 active flags
        with patch.object(self.panel, '_save_dialog') as mock_sd:
            ok = self.panel.focus_requirement_section('REQ-2026-002', 'completion')
            self.assertTrue(ok)
            self.assertEqual(self.panel._current.get('id'), 'REQ-2026-002')

            QApplication.processEvents()
            mock_sd.assert_called_once_with(self.panel._current, offer_next=False, focus_section='completion')

    def test_completion_hidden_guard_does_not_open_dialog(self):
        """面板未显示（隐藏）时，completion 定位成功，但下个事件循环不弹窗。"""
        self.panel.hide()
        with patch.object(self.panel, '_save_dialog') as mock_sd:
            ok = self.panel.focus_requirement_section('REQ-2026-001', 'completion')
            self.assertTrue(ok)
            QApplication.processEvents()

            mock_sd.assert_not_called()

    def test_completion_switched_away_guard_does_not_open_dialog(self):
        """请求后切走又返回同一面板，旧 timer 的代际仍阻止弹窗。"""
        self.panel.show()
        with patch.object(self.panel, '_save_dialog') as mock_sd:
            ok = self.panel.focus_requirement_section('REQ-2026-001', 'completion')
            self.assertTrue(ok)
            self.panel.hide()
            self.panel.on_panel_deactivated()
            self.panel.show()
            QApplication.processEvents()

            mock_sd.assert_not_called()

    def test_completion_late_timer_token_guard_does_not_open_dialog(self):
        """另一请求先到达后，迟到的旧 timer 代际不匹配，不弹窗。"""
        self.panel.show()
        with patch.object(self.panel, '_save_dialog') as mock_sd:
            ok1 = self.panel.focus_requirement_section('REQ-2026-001', 'completion')
            self.assertTrue(ok1)
            # 立即触发第二个请求，代际递增
            ok2 = self.panel.focus_requirement_section('REQ-2026-003', 'test_points')
            self.assertTrue(ok2)

            QApplication.processEvents()
            mock_sd.assert_not_called()

    def test_completion_current_id_changed_guard_does_not_open_dialog(self):
        """当前选中的需求变更时，迟到 timer 守卫拦截，不弹窗。"""
        self.panel.show()
        with patch.object(self.panel, '_save_dialog') as mock_sd:
            ok = self.panel.focus_requirement_section('REQ-2026-001', 'completion')
            self.assertTrue(ok)
            self.panel._current = {'id': 'REQ-CHANGED'}

            QApplication.processEvents()
            mock_sd.assert_not_called()

    def test_test_points_switches_tab_and_focuses_add_edit(self):
        """test_points 模式切换现有详情 Tab 并聚焦 editor.add_edit，零写。"""
        self.panel.detail_tabs.setCurrentIndex(0)
        self.assertNotEqual(self.panel.detail_tabs.tabText(self.panel.detail_tabs.currentIndex()), '测试点')

        ok = self.panel.focus_requirement_section('REQ-2026-001', 'test_points')
        self.assertTrue(ok)

        self.panel.show()
        QApplication.processEvents()

        current_tab_text = self.panel.detail_tabs.tabText(self.panel.detail_tabs.currentIndex())
        self.assertIn('测试', current_tab_text)

        self.assertTrue(self.panel.test_points_editor.add_edit.hasFocus())
        self.mock_save_req.assert_not_called()

    def test_test_points_refocus_does_not_run_after_hiding_panel(self):
        self.panel.show()
        with patch.object(self.panel.test_points_editor.add_edit, 'setFocus') as focus:
            self.assertTrue(self.panel.focus_requirement_section('REQ-2026-001', 'test_points'))
            self.assertEqual(focus.call_count, 1)
            self.panel.hide()
            QApplication.processEvents()
            self.assertEqual(focus.call_count, 1)

    def test_load_requirements_fallback_when_not_in_memory(self):
        """精确ID在内存中找不到时可 load_requirements 一次，找到后替换并定位，零 save。"""
        new_item = {
            'id': 'REQ-2026-099',
            'code': 'REQ-099',
            'title': '外部新增需求',
            'record_kind': '需求',
            'status': '待分析',
            'system': '统一前端',
            'systems': ['统一前端'],
            'online_month': '2026-07',
            'has_sql': True,
            'sql_parts': [],
            'test_points': [],
            'flag_done': {'has_sql': False},
        }
        with patch('panels.requirement_panel.load_requirements', return_value=self.synthetic_requirements + [new_item]):
            ok = self.panel.focus_requirement_section('REQ-2026-099', 'test_points')
            self.assertTrue(ok)
            QApplication.processEvents()

            self.assertEqual(self.panel._current.get('id'), 'REQ-2026-099')
            self.mock_save_req.assert_not_called()

    def test_load_requirements_not_replaced_if_still_not_found(self):
        """load_requirements 一次仍找不到时，不替换 self._requirements，返回 False。"""
        orig_reqs = list(self.panel._requirements)
        other_list = [{'id': 'REQ-OTHER', 'title': '其它'}]
        with patch('panels.requirement_panel.load_requirements', return_value=other_list):
            ok = self.panel.focus_requirement_section('REQ-NEVER-EXISTS', 'test_points')
            self.assertFalse(ok)
            self.assertEqual(self.panel._requirements, orig_reqs)
            self.mock_save_req.assert_not_called()

    def test_save_dialog_failure_does_not_mutate_memory(self):
        """_save_dialog 保存失败时回滚 candidate，不得原地 mutate 污染原有内存需求和 flag_done。"""
        orig_item = dict(self.panel._requirements[0])
        orig_title = orig_item['title']
        orig_flag_done = dict(orig_item['flag_done'])

        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = QDialog.DialogCode.Accepted
        mock_dlg.values.return_value = {
            'title': '被篡改的标题',
            'status': '已上线',
            'has_sql': True,
        }

        with patch('panels.requirement_panel.RequirementDialog', return_value=mock_dlg):
            with patch.object(self.panel, '_save_requirements_candidate', return_value=False):
                res = self.panel._save_dialog(self.panel._requirements[0], offer_next=False)
                self.assertIsNone(res)

                # 验证内存台账保持原值未被污染
                matched = next(r for r in self.panel._requirements if r['id'] == orig_item['id'])
                self.assertEqual(matched['title'], orig_title)
                self.assertEqual(matched['flag_done'], orig_flag_done)

    def test_scan_finished_validations(self):
        """_scan_finished 在 merge 前验证 copies：空列表 show_info，非法项 show_error，零 success/零 save。"""
        with patch('panels.requirement_panel.show_info') as mock_info, \
             patch('panels.requirement_panel.show_error') as mock_error, \
             patch('panels.requirement_panel.show_success') as mock_success:

            res_empty = self.panel._scan_finished([])
            self.assertFalse(res_empty)
            mock_info.assert_called_once()
            mock_success.assert_not_called()
            self.mock_save_req.assert_not_called()

            mock_info.reset_mock()

            res_nonlist = self.panel._scan_finished('invalid')
            self.assertFalse(res_nonlist)
            mock_error.assert_called_once()
            mock_success.assert_not_called()
            self.mock_save_req.assert_not_called()

            mock_error.reset_mock()

            res_bad_item = self.panel._scan_finished([{'local_path': ''}])
            self.assertFalse(res_bad_item)
            mock_error.assert_called_once()
            mock_success.assert_not_called()
            self.mock_save_req.assert_not_called()

    def test_task_succeeded_returns_false_flow(self):
        """_task_succeeded 回调返回 False 时设 _task_failed=True、loading.fail、svn_activity显式失败文案，不覆盖明确失败文案。"""
        mock_success = MagicMock(return_value=False)
        self.panel._task_shows_loading = True
        self.panel.loading.fail = MagicMock()
        self.panel.loading.finish = MagicMock()

        self.panel._task_succeeded({}, success=mock_success)

        self.assertTrue(self.panel._task_failed)
        self.panel.loading.fail.assert_called_once_with('处理失败', token=None)
        self.panel.loading.finish.assert_not_called()
        self.assertIn('失败', self.panel.svn_activity.text())

        explicit_fail_text = self.panel.svn_activity.text()
        self.panel._task_finished()
        self.assertEqual(self.panel.svn_activity.text(), explicit_fail_text)

    def test_persist_test_points_rollback_blocks_signals(self):
        """_persist_test_points 保存失败回滚时使用 QSignalBlocker / 保留 blockSignals，传入 pending_seed=False 且无递归。"""
        self.panel._current = self.synthetic_requirements[0]
        with patch.object(self.panel, '_save_requirements_candidate', return_value=False):
            editor = self.panel.test_points_editor
            editor.set_source = MagicMock()

            res = self.panel._persist_test_points([{'id': 'new', 'text': 'new', 'done': False}])
            self.assertFalse(res)
            editor.set_source.assert_called_once()
            _, kwargs = editor.set_source.call_args
            self.assertFalse(kwargs.get('pending_seed'))


if __name__ == '__main__':
    unittest.main()
