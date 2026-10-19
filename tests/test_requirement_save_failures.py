# -*- coding: utf-8 -*-
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QDialog

from panels.requirement_panel import (
    RequirementPanel,
    _ACTIVE_SVN_WORKERS,
)
import tools.requirements


def _make_sample_requirement(req_id='REQ-001', **overrides):
    base = {
        'id': req_id,
        'code': req_id,
        'title': '业务需求A',
        'description': '需求说明正文\n- 测试点条目1\n- 测试点条目2',
        'record_kind': '需求',
        'category': '功能需求',
        'status': '待分析',
        'priority': '普通',
        'system': '统一监管接入平台',
        'systems': ['统一监管接入平台'],
        'owner': 'developer',
        'actual_release_date': '2026-09-18',
        'actual_online_date': '2026-09-18',
        'online_month': '2026-09',
        'has_sql': True,
        'needs_peripheral_upgrade': True,
        'needs_interface_update': False,
        'temporary_upgrade': False,
        'flag_done': {
            'has_sql': False,
            'needs_peripheral_upgrade': False,
            'needs_interface_update': False,
            'temporary_upgrade': False,
        },
        'test_points': [
            {'id': 'tp-1', 'text': '初始测试点1', 'done': False},
        ],
        'local_path': r'C:\mock_repo\req_dir',
        'svn_url': 'svn://mock/repo/req_dir',
        'sql_parts': [{'name': '01_init.sql', 'content': 'SELECT 1;'}],
        'source_files': [],
    }
    base.update(overrides)
    return base


class RequirementSaveFailuresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_req_path = os.path.join(self.temp_dir.name, 'requirements.json')

        self._simulate_save_failure = False
        self._real_save_requirements = tools.requirements.save_requirements

        def _fake_save_requirements(candidate, path=None):
            if self._simulate_save_failure:
                raise OSError('Simulated disk write failure (e.g. disk full or permission denied)')
            target = path or self.temp_req_path
            return self._real_save_requirements(candidate, path=target)

        self.mock_save_requirements = self.enterContext(patch(
            'panels.requirement_panel.save_requirements',
            side_effect=_fake_save_requirements,
        ))

        # 屏蔽所有用户持久化入口
        self.mock_load_requirements = self.enterContext(patch('panels.requirement_panel.load_requirements', return_value=[]))
        self.mock_load_systems = self.enterContext(patch('panels.requirement_panel.load_systems', return_value=[]))
        self.mock_load_requirement_ui = self.enterContext(patch('panels.requirement_panel.load_requirement_ui', return_value={}))
        self.mock_load_last_choices = self.enterContext(patch('panels.requirement_panel.load_last_choices', return_value={}))
        self.mock_save_requirement_ui = self.enterContext(patch('panels.requirement_panel.save_requirement_ui'))
        self.mock_update_last_choices = self.enterContext(patch('panels.requirement_panel.update_last_choices'))

        # 屏蔽弹窗
        self.mock_show_error = self.enterContext(patch('panels.requirement_panel.show_error'))
        self.mock_show_warning = self.enterContext(patch('panels.requirement_panel.show_warning'))
        self.mock_show_success = self.enterContext(patch('panels.requirement_panel.show_success'))
        self.mock_offer_next_steps = self.enterContext(patch('panels.requirement_panel.offer_next_steps'))
        self.mock_show_info = self.enterContext(patch('panels.requirement_panel.show_info'))

    def _dispose_widget(self, widget):
        if widget is None:
            return
        worker = getattr(widget, '_active_worker', None)
        if worker is not None and worker.isRunning():
            worker.wait(5000)
        widget.close()
        widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _panel(self, requirements=None, systems=None):
        reqs = requirements if requirements is not None else []
        sys_list = systems if systems is not None else []
        self.mock_load_requirements.return_value = [copy.deepcopy(item) for item in reqs]
        self.mock_load_systems.return_value = copy.deepcopy(sys_list)
        panel = RequirementPanel()
        self.addCleanup(self._dispose_widget, panel)
        panel.show()
        self.app.processEvents()
        return panel

    def test_single_flag_click_save_failure_rolls_back_and_shows_error(self):
        """真实 QPushButton.click 触发单 flag 保存失败：进程不 abort、内存未变、changed 信号不发、错误弹窗可见。"""
        req = _make_sample_requirement('REQ-SINGLE-FAIL')
        panel = self._panel([req])

        self.assertIsNotNone(panel._current)
        self.assertEqual(panel._current.get('id'), 'REQ-SINGLE-FAIL')
        self.assertIn('has_sql', panel._flag_buttons)

        btn = panel._flag_buttons['has_sql']
        self.assertFalse(btn.isHidden())
        self.assertIn('待完成', btn.text())

        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        # 模拟保存失败 (mock OSError)
        self._simulate_save_failure = True

        # 真实触发按钮点击
        btn.click()
        self.app.processEvents()

        # 1. 进程不 abort，且内存状态未变更
        self.assertFalse(panel._current['flag_done']['has_sql'])
        self.assertFalse(panel._requirements[0]['flag_done']['has_sql'])

        # 2. requirements_changed 信号不发出
        self.assertEqual(len(changed_emitted), 0)

        # 3. 错误提示可见
        self.mock_show_error.assert_called_once()
        self.assertEqual(self.mock_show_error.call_args.args[1], '保存失败')
        self.assertIn('无法写入完成标记', self.mock_show_error.call_args.args[2])

        # 4. 按钮文案依然保持待完成状态
        self.assertIn('待完成', btn.text())

    def test_single_flag_click_save_success_persists_data_and_notifies(self):
        """单 flag 保存成功：调用真实 save_requirements 写入临时文件，内存更新，发出通知，按钮文案变为已完成。"""
        req = _make_sample_requirement('REQ-SINGLE-OK')
        panel = self._panel([req])

        btn = panel._flag_buttons['has_sql']
        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        # 保存成功路径
        self._simulate_save_failure = False

        btn.click()
        self.app.processEvents()

        # 1. 内存更新
        self.assertTrue(panel._current['flag_done']['has_sql'])
        self.assertTrue(panel._requirements[0]['flag_done']['has_sql'])

        # 2. 信号发出
        self.assertEqual(len(changed_emitted), 1)

        # 3. 无报错，按钮更新
        self.mock_show_error.assert_not_called()
        self.assertIn('已完成', btn.text())

        # 4. 真实文件写入成功
        self.assertTrue(os.path.exists(self.temp_req_path))
        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            persisted = json.load(f)
        self.assertEqual(len(persisted), 1)
        self.assertTrue(persisted[0]['flag_done']['has_sql'])

    def test_set_all_flags_done_failure_preserves_state_and_shows_error(self):
        """全部 flags 标记失败：保存失败时保留原状态，不发通知，错误弹窗可见。"""
        req = _make_sample_requirement('REQ-ALL-FAIL')
        panel = self._panel([req])

        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        self._simulate_save_failure = True
        result = panel._set_all_flags_done(panel._current, True)

        self.assertFalse(result)
        self.assertFalse(panel._current['flag_done']['has_sql'])
        self.assertFalse(panel._current['flag_done']['needs_peripheral_upgrade'])
        self.assertEqual(len(changed_emitted), 0)
        self.mock_show_error.assert_called_once()
        self.assertIn('无法写入完成标记', self.mock_show_error.call_args.args[2])

    def test_set_all_flags_done_success_persists_data_and_notifies(self):
        """全部 flags 标记成功：真实保存至临时文件，内存和信号正确。"""
        req = _make_sample_requirement('REQ-ALL-OK')
        panel = self._panel([req])

        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        self._simulate_save_failure = False
        result = panel._set_all_flags_done(panel._current, True)

        self.assertTrue(result)
        self.assertTrue(panel._current['flag_done']['has_sql'])
        self.assertTrue(panel._current['flag_done']['needs_peripheral_upgrade'])
        self.assertEqual(len(changed_emitted), 1)
        self.mock_show_error.assert_not_called()

        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            persisted = json.load(f)
        self.assertTrue(persisted[0]['flag_done']['has_sql'])
        self.assertTrue(persisted[0]['flag_done']['needs_peripheral_upgrade'])

    def test_persist_test_points_failure_rolls_back_editor_and_preserves_state(self):
        """测试点保存失败：editor 自动回滚到原测试点内容，当前内存不被修改，不发 changed 信号。"""
        orig_points = [{'id': 'tp-1', 'text': '初始测试点1', 'done': False}]
        req = _make_sample_requirement('REQ-TP-FAIL', test_points=orig_points)
        panel = self._panel([req])

        self.assertEqual(panel.test_points_editor.points(), orig_points)

        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        # 模拟保存失败
        self._simulate_save_failure = True
        modified_points = [
            {'id': 'tp-1', 'text': '初始测试点1_已修改', 'done': True},
            {'id': 'tp-2', 'text': '新增待测项', 'done': False},
        ]
        result = panel._persist_test_points(modified_points)

        self.assertFalse(result)
        self.assertEqual(len(changed_emitted), 0)
        self.mock_show_error.assert_called_once()
        self.assertIn('无法写入测试点', self.mock_show_error.call_args.args[2])

        # 验证内存依然为原数据
        self.assertEqual(panel._current['test_points'], orig_points)

        # 验证 editor 内容已自动回滚
        editor_points = panel.test_points_editor.points()
        self.assertEqual(len(editor_points), 1)
        self.assertEqual(editor_points[0]['id'], 'tp-1')
        self.assertEqual(editor_points[0]['text'], '初始测试点1')
        self.assertFalse(editor_points[0]['done'])

    def test_persist_test_points_success_persists_data_and_notifies(self):
        """测试点保存成功：真实保存至临时文件，更新内存并通知。"""
        orig_points = [{'id': 'tp-1', 'text': '初始测试点1', 'done': False}]
        req = _make_sample_requirement('REQ-TP-OK', test_points=orig_points)
        panel = self._panel([req])

        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        self._simulate_save_failure = False
        new_points = [{'id': 'tp-1', 'text': '初始测试点1', 'done': True}]
        result = panel._persist_test_points(new_points)

        self.assertTrue(result)
        self.assertEqual(len(changed_emitted), 1)
        self.mock_show_error.assert_not_called()
        self.assertTrue(panel._current['test_points'][0]['done'])

        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            persisted = json.load(f)
        self.assertTrue(persisted[0]['test_points'][0]['done'])

    def test_scan_finished_success_duplicate_retains_title_status_and_actual_date(self):
        """扫描成功且命中已有条目：保留既有 title、status、actual_release_date、online_month，更新 SVN 元数据。"""
        existing = _make_sample_requirement(
            'REQ-SCAN-DUP',
            code='REQ-SCAN-01',
            title='已手工修改的业务标题',
            status='开发中',
            actual_release_date='2026-09-22',
            actual_online_date='2026-09-22',
            online_month='2026-09',
            local_path=r'C:\mock_repo\req_scan',
            svn_url='svn://mock/repo/req_scan',
            file_count=3,
            svn_revision='100',
        )
        panel = self._panel([existing])

        # 扫描得到相同路径/URL的工作副本（元数据带有新的文件数和版本，但 title 仅为默认目录名）
        scanned_copy = {
            'local_path': r'C:\mock_repo\req_scan',
            'svn_url': 'svn://mock/repo/req_scan',
            'relative_path': 'req_scan',
            'svn_revision': '250',
            'svn_status': 'normal',
            'workspace_kind': 'svn',
            'file_count': 15,
            'source_modified_at': '2026-09-28T12:00:00',
        }

        self._simulate_save_failure = False
        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        result = panel._scan_finished([scanned_copy])

        self.assertTrue(result)
        self.assertEqual(len(changed_emitted), 1)
        self.mock_show_success.assert_called_once()
        self.assertIn('需求文件夹扫描完成', self.mock_show_success.call_args.args[1])

        # 验证既有业务字段被完整保留
        target = panel._current
        self.assertEqual(target['title'], '已手工修改的业务标题')
        self.assertEqual(target['status'], '开发中')
        self.assertEqual(target['actual_release_date'], '2026-09-22')
        self.assertEqual(target['online_month'], '2026-09')

        # 验证 SVN 元数据被更新
        self.assertEqual(target['svn_revision'], '250')
        self.assertEqual(target['file_count'], 15)

        # 验证临时文件写入内容
        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            persisted = json.load(f)
        self.assertEqual(persisted[0]['title'], '已手工修改的业务标题')
        self.assertEqual(persisted[0]['status'], '开发中')
        self.assertEqual(persisted[0]['actual_release_date'], '2026-09-22')

    def test_scan_finished_save_failure_does_not_show_success_and_returns_false(self):
        """扫描保存失败：返回 False，show_error 可见，且绝无 show_success 和 offer_next_steps。"""
        panel = self._panel([])
        scanned_copy = {
            'local_path': r'C:\mock_repo\new_req',
            'svn_url': 'svn://mock/repo/new_req',
            'relative_path': 'new_req',
            'svn_revision': '10',
            'file_count': 1,
        }

        self._simulate_save_failure = True
        changed_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))

        result = panel._scan_finished([scanned_copy])

        self.assertFalse(result)
        self.assertEqual(len(changed_emitted), 0)
        self.mock_show_error.assert_called_once()
        self.mock_show_success.assert_not_called()
        self.mock_offer_next_steps.assert_not_called()

    def test_scan_finished_empty_or_invalid_candidate_no_success_message(self):
        """扫描空列表或非法项：空列表提示未识别到目录并返回 False，不触发保存和成功提示；非法项弹窗报错并返回 False。"""
        panel = self._panel([])

        # 1. 扫描空列表 []：应弹出 show_info『未识别到目录』，不调用 save，不弹 error，不弹 success
        save_calls_before = self.mock_save_requirements.call_count
        result_empty = panel._scan_finished([])

        self.assertFalse(result_empty)
        self.mock_show_info.assert_called_once()
        self.assertIn('未识别到', self.mock_show_info.call_args.args[2])
        self.mock_show_error.assert_not_called()
        self.mock_show_success.assert_not_called()
        self.assertEqual(self.mock_save_requirements.call_count, save_calls_before)

        # 2. 扫描非法项 [{}]：应弹出 show_error 提示无效目录项，不调用 save，不弹 success
        result_invalid = panel._scan_finished([{}])

        self.assertFalse(result_invalid)
        self.mock_show_error.assert_called_once()
        self.assertIn('无效目录项', self.mock_show_error.call_args.args[2])
        self.mock_show_success.assert_not_called()
        self.assertEqual(self.mock_save_requirements.call_count, save_calls_before)

    def test_worker_callback_returning_false_should_not_be_marked_success(self):
        """Worker 回调返回 False（如 _scan_finished 保存失败返回 False）不应被标记为成功。"""
        panel = self._panel()
        panel._task_shows_loading = True
        panel._task_failed = False

        # 模拟后台 worker 结束并调用 success_callback，若 callback 返回 False，系统不应判定为成功
        panel._task_succeeded('scan_result', lambda _res: False)

        self.assertTrue(
            panel._task_failed,
            'Worker success_callback 返回 False 时，_task_succeeded 应记录为失败状态而非处理完成',
        )

    def test_save_dialog_edit_existing_failure_preserves_state_and_shows_error(self):
        """编辑已存在需求时保存失败隔离：状态由待分析改已完成，save_requirements 抛 OSError 后保持原状，弹窗报错一次。"""
        req = _make_sample_requirement('REQ-EDIT-FAIL', title='原需求标题', status='待分析')
        panel = self._panel([req])

        # 写入初始磁盘数据以便比对磁盘未被脏数据覆盖
        tools.requirements.save_requirements([copy.deepcopy(req)], path=self.temp_req_path)
        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            disk_before = json.load(f)

        changed_emitted = []
        saved_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))
        panel.requirement_saved.connect(lambda obj: saved_emitted.append(obj))

        edited_values = copy.deepcopy(panel._current)
        edited_values['status'] = '已完成'
        edited_values['title'] = '已修改标题'

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
        mock_dialog.values.return_value = edited_values

        self.enterContext(patch('panels.requirement_panel.RequirementDialog', return_value=mock_dialog))

        # 模拟保存失败
        self._simulate_save_failure = True
        result = panel._save_dialog(panel._current, offer_next=False)

        # 1. 返回 None 标识保存未成功
        self.assertIsNone(result)

        # 2. 原 requirements 对象与列表保持原状
        self.assertEqual(panel._requirements[0]['status'], '待分析')
        self.assertEqual(panel._requirements[0]['title'], '原需求标题')

        # 3. _current 保持原状
        self.assertEqual(panel._current['status'], '待分析')
        self.assertEqual(panel._current['title'], '原需求标题')

        # 4. 磁盘数据未被修改
        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            disk_after = json.load(f)
        self.assertEqual(disk_after, disk_before)
        self.assertEqual(disk_after[0]['status'], '待分析')

        # 5. requirements_changed 与 requirement_saved 保持原状（不发出）
        self.assertEqual(len(changed_emitted), 0)
        self.assertEqual(len(saved_emitted), 0)

        # 6. show_error 被调用一次
        self.mock_show_error.assert_called_once()
        self.assertEqual(self.mock_show_error.call_args.args[1], '保存失败')
        self.assertIn('无法写入 requirements.json', self.mock_show_error.call_args.args[2])

    def test_save_dialog_rejected_does_not_save_or_modify_state(self):
        """编辑弹窗被取消（exec 返回 Rejected）：不调用 save_requirements，不修改原需求。"""
        req = _make_sample_requirement('REQ-CANCEL', status='待分析')
        panel = self._panel([req])

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.DialogCode.Rejected

        self.enterContext(patch('panels.requirement_panel.RequirementDialog', return_value=mock_dialog))

        changed_emitted = []
        saved_emitted = []
        panel.requirements_changed.connect(lambda: changed_emitted.append(True))
        panel.requirement_saved.connect(lambda obj: saved_emitted.append(obj))

        save_call_count_before = self.mock_save_requirements.call_count

        result = panel._save_dialog(panel._current, offer_next=False)

        self.assertIsNone(result)
        self.assertEqual(self.mock_save_requirements.call_count, save_call_count_before)
        self.mock_show_error.assert_not_called()
        self.assertEqual(len(changed_emitted), 0)
        self.assertEqual(len(saved_emitted), 0)
        self.assertEqual(panel._current['status'], '待分析')

    def test_save_dialog_edit_existing_success_persists_completed_and_emits_saved(self):
        """编辑已存在需求成功保存：状态待分析改已完成，写入 TEMP 磁盘，发出 requirement_saved 恰一次，支持 focus_section 聚焦。"""
        req = _make_sample_requirement('REQ-EDIT-OK', title='原需求标题', status='待分析')
        panel = self._panel([req])

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
        edited_values = copy.deepcopy(panel._current)
        edited_values['status'] = '已完成'
        mock_dialog.values.return_value = edited_values
        mock_dialog.status_combo = MagicMock()

        self.enterContext(patch('panels.requirement_panel.RequirementDialog', return_value=mock_dialog))

        saved_emitted = []
        panel.requirement_saved.connect(lambda obj: saved_emitted.append(obj))

        self._simulate_save_failure = False
        result = panel._save_dialog(panel._current, offer_next=False, focus_section='completion')

        # 1. 断言 status_combo.setFocus 被调用
        mock_dialog.status_combo.setFocus.assert_called_once()

        # 2. 断言返回目标且状态为已完成
        self.assertIsNotNone(result)
        self.assertEqual(result.get('id'), 'REQ-EDIT-OK')
        self.assertEqual(result.get('status'), '已完成')

        # 3. 断言 _requirements 与 _current 的状态均为已完成
        self.assertEqual(panel._requirements[0].get('status'), '已完成')
        self.assertEqual(panel._current.get('status'), '已完成')

        # 4. 断言磁盘 TEMP 文件写入且状态为已完成
        self.assertTrue(os.path.exists(self.temp_req_path))
        with open(self.temp_req_path, 'r', encoding='utf-8') as f:
            persisted = json.load(f)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].get('status'), '已完成')

        # 5. 断言 requirement_saved 恰好发出一次，携带最新对象
        self.assertEqual(len(saved_emitted), 1)
        self.assertEqual(saved_emitted[0].get('id'), 'REQ-EDIT-OK')
        self.assertEqual(saved_emitted[0].get('status'), '已完成')


if __name__ == '__main__':
    unittest.main()
