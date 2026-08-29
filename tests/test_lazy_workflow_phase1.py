# -*- coding: utf-8 -*-
"""懒人工作流第一阶段：推断、下一步建议、搜索定位、升级日期自动加载。"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QDialog

from panels.requirement_panel import RequirementPanel
from panels.sql_panel import SqlToolPanel
from tools.requirements import (
    apply_auto_inference, infer_online_month_from_text, infer_system_name,
    infer_upgrade_flags, load_requirements, requirement_from_text, save_requirements,
)
from ui.confirm_dialog import NextStepDialog, offer_next_steps


class LazyWorkflowPhase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont('Microsoft YaHei UI', 10))

    def test_infer_system_from_title_and_svn(self):
        self.assertEqual(
            infer_system_name('REQ-1 车险承保中心随车出单优化'),
            '车险承保中心',
        )
        self.assertEqual(
            infer_system_name('svn://10/x/DEV_prpcar_20260715-REQ-1'),
            '车险承保中心',
        )
        self.assertEqual(
            infer_system_name('客户信息平台字段同步 update t_ecif set x=1'),
            '客户信息平台（ECIF）',
        )
        self.assertEqual(infer_system_name('普通说明文字'), '')

    def test_infer_flags_and_month(self):
        flags = infer_upgrade_flags('临时升级并整理接口文档，需通知周边系统；update t set a=1')
        self.assertTrue(flags['has_sql'])
        self.assertTrue(flags['temporary_upgrade'])
        self.assertTrue(flags['needs_interface_update'])
        self.assertTrue(flags['needs_peripheral_upgrade'])
        self.assertEqual(infer_online_month_from_text('REQ-20260715-0001 标题'), '2026-07')
        self.assertEqual(infer_online_month_from_text('计划 2026年8月 上线'), '2026-08')

    def test_requirement_from_text_auto_fills_empty_fields(self):
        item = requirement_from_text(
            'REQ-20260601-0002 车险承保中心接口联调\n需通知周边系统升级\ninsert into t values(1);',
            'paste',
        )
        self.assertEqual(item['system'], '车险承保中心')
        self.assertEqual(item['systems'], ['车险承保中心'])
        self.assertTrue(item['has_sql'])
        self.assertTrue(item['needs_interface_update'])
        self.assertTrue(item['needs_peripheral_upgrade'])
        self.assertEqual(item['online_month'], '2026-06')
        # 已有 system 不被覆盖
        kept = apply_auto_inference(
            {'title': '车险承保中心', 'system': '共享中心', 'description': 'prpcar'},
            only_empty=True,
        )
        self.assertEqual(kept['system'], '共享中心')

    def test_only_empty_does_not_override_explicit_false_flags_or_other_category(self):
        """用户关掉的标记与「其他」分类不得被 only_empty 推断勾回。"""
        item = apply_auto_inference(
            {
                'title': '接口联调并通知周边系统临时升级',
                'description': '需整理接口文档，通知周边系统；update t set a=1; 临时升级',
                'has_sql': False,
                'needs_interface_update': False,
                'needs_peripheral_upgrade': False,
                'temporary_upgrade': False,
                'category': '其他',
                'system': '共享中心',
            },
            only_empty=True,
        )
        self.assertFalse(item['has_sql'])
        self.assertFalse(item['needs_interface_update'])
        self.assertFalse(item['needs_peripheral_upgrade'])
        self.assertFalse(item['temporary_upgrade'])
        self.assertEqual(item['category'], '其他')
        self.assertEqual(item['system'], '共享中心')

        # 键缺失时仍可补全
        filled = apply_auto_inference(
            {
                'title': '接口联调',
                'description': '更新接口文档，通知周边系统升级；insert into t values(1)',
            },
            only_empty=True,
        )
        self.assertTrue(filled['has_sql'])
        self.assertTrue(filled['needs_interface_update'])
        self.assertTrue(filled['needs_peripheral_upgrade'])

    def test_save_requirements_is_atomic_and_roundtrips(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'requirements.json')
            payload = [
                {
                    'id': 'a1',
                    'title': '原子写入',
                    'has_sql': False,
                    'needs_interface_update': False,
                    'category': '其他',
                }
            ]
            save_requirements(payload, path=path)
            self.assertTrue(os.path.isfile(path))
            loaded = load_requirements(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]['title'], '原子写入')
            self.assertFalse(loaded[0]['has_sql'])
            self.assertEqual(loaded[0]['category'], '其他')
            # 临时文件不应残留
            leftovers = [name for name in os.listdir(tmp) if name.endswith('.tmp')]
            self.assertEqual(leftovers, [])

    def test_next_step_dialog_layout_and_selection(self):
        dialog = NextStepDialog(
            '已保存',
            '推荐：加入今日日报',
            actions=[
                ('daily', '加入今日日报', True),
                ('release', '准备本次升级', False),
            ],
            recommended='daily',
        )
        dialog.show()
        self.app.processEvents()
        self.assertIsNone(dialog.selected_action())
        dialog._choose('daily')
        self.assertEqual(dialog.selected_action(), 'daily')
        dialog.close()

        with patch.object(NextStepDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            self.assertIsNone(offer_next_steps(
                None, 't', 'm', [('daily', '日报', True)], recommended='daily',
            ))

    def test_sql_panel_refresh_button_and_auto_load(self):
        panel = SqlToolPanel()
        self.assertEqual(panel.refresh_release_btn.text(), '刷新候选')
        self.assertFalse(panel._release_loaded)
        self.assertTrue(hasattr(panel, 'draft_btn'))
        panel._load_release_candidates()
        self.assertEqual(panel._release_date_confirmed, panel.release_date.date().toString('yyyy-MM-dd'))
        # 生成时若日期不同会自动重载
        panel.release_date.setDate(QDate(2026, 1, 15))
        panel._release_date_confirmed = '2099-01-01'
        with patch.object(panel, '_load_release_candidates') as loader, \
                patch('panels.sql_panel.show_warning'):
            panel._generate_release_materials()
            loader.assert_called()
        panel.close()

    def test_requirement_search_selects_first_match(self):
        seed = [
            {
                'id': 'a', 'code': 'REQ-AAA', 'title': '阿尔法需求', 'record_kind': '需求',
                'status': '待分析', 'online_month': '2026-06', 'system': '',
                'description': '', 'sql_parts': [], 'source_files': [],
            },
            {
                'id': 'b', 'code': 'BUG-BBB', 'title': '贝塔缺陷', 'record_kind': 'BUG',
                'status': '开发中', 'online_month': '2026-07', 'system': '',
                'description': '修复报错', 'sql_parts': [], 'source_files': [],
            },
        ]
        with patch('panels.requirement_panel.load_requirements', return_value=seed), \
                patch('panels.requirement_panel.save_requirements'):
            panel = RequirementPanel()
        panel.search_edit.setText('贝塔')
        self.app.processEvents()
        current = panel.requirement_list.currentItem()
        self.assertIsNotNone(current)
        data = current.data(0, Qt.ItemDataRole.UserRole)
        self.assertEqual(data.get('id'), 'b')
        self.assertEqual(panel.detail_title.text(), '贝塔缺陷')
        # 月份组应展开
        parent = current.parent()
        if parent is not None:
            self.assertTrue(parent.isExpanded())
        panel.close()

    def test_confirm_action_used_for_svn_commit_preview(self):
        """提交 SVN 走 confirm_action，并保留预览文本。"""
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                patch('panels.requirement_panel.save_requirements'):
            panel = RequirementPanel()
        panel._current = {
            'id': 'x', 'local_path': 'C:\\fake', 'workspace_kind': 'svn', 'title': 't',
        }
        with patch('panels.requirement_panel.os.path.isdir', return_value=True), \
                patch('panels.requirement_panel.svn_status', return_value={
                    'clean': False, 'text': 'M file.sql\nA readme.md',
                }), \
                patch('panels.requirement_panel.QInputDialog.getText', return_value=('fix bug', True)), \
                patch('panels.requirement_panel.confirm_action', return_value=False) as confirm, \
                patch('panels.requirement_panel.show_info') as show_info, \
                patch.object(panel, '_selected_file_paths', return_value=['C:\\fake\\file.sql']), \
                patch.object(panel, '_start_task') as start_task:
            panel._commit_svn()
        show_info.assert_not_called()
        confirm.assert_called_once()
        message = confirm.call_args[0][2]
        self.assertIn('file.sql', message)
        self.assertIn('fix bug', message)
        start_task.assert_not_called()
        panel.close()


if __name__ == '__main__':
    unittest.main()
