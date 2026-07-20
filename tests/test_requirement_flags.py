# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QDialog

from panels.requirement_panel import RequirementDialog, RequirementPanel
from tools.requirements import (
    flag_status_text, load_requirements, normalize_requirement, save_requirements,
)


class RequirementFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont('Microsoft YaHei UI', 10))

    def test_dialog_values_keep_checkbox_flags(self):
        dialog = RequirementDialog({
            'title': '测试', 'code': 'REQ-FLAG',
            'has_sql': False, 'temporary_upgrade': False,
            'needs_peripheral_upgrade': False, 'needs_interface_update': False,
            'sql_parts': [], 'source_files': [],
        })
        dialog.title_edit.setText('标记保存测试')
        dialog.has_sql.setChecked(True)
        dialog.temporary.setChecked(True)
        dialog.peripheral.setChecked(True)
        dialog.interface_update.setChecked(True)
        values = dialog.values()
        self.assertTrue(values['has_sql'])
        self.assertTrue(values['temporary_upgrade'])
        self.assertTrue(values['needs_peripheral_upgrade'])
        self.assertTrue(values['needs_interface_update'])
        dialog.close()

    def test_save_dialog_persists_flags_and_tree_shows_red_dots(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'requirements.json')
            with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                    patch('panels.requirement_panel.save_requirements') as save_mock, \
                    patch('panels.requirement_panel.offer_next_steps', return_value=None):
                panel = RequirementPanel()

                class FakeDialog:
                    def __init__(self, *args, **kwargs):
                        pass

                    def exec(self):
                        return QDialog.DialogCode.Accepted

                    def values(self):
                        return {
                            'code': 'REQ-1', 'title': '红点需求', 'record_kind': '需求',
                            'description': '', 'category': '功能需求', 'status': '待分析',
                            'priority': '普通', 'system': '', 'owner': '',
                            'svn_url': '', 'local_path': '', 'workspace_kind': 'folder',
                            'planned_online_date': '', 'actual_online_date': '',
                            'online_month': '2026-07',
                            'has_sql': True, 'needs_peripheral_upgrade': True,
                            'temporary_upgrade': True, 'needs_interface_update': False,
                            'sql_parts': [], 'source_files': [],
                        }

                with patch('panels.requirement_panel.RequirementDialog', FakeDialog):
                    panel._save_dialog()
                self.assertTrue(save_mock.called)
                saved = panel._requirements[0]
                self.assertTrue(saved['has_sql'])
                self.assertTrue(saved['temporary_upgrade'])
                self.assertTrue(saved['needs_peripheral_upgrade'])
                text = flag_status_text(saved)
                self.assertIn('🔴SQL', text)
                self.assertIn('🔴临时', text)
                self.assertIn('🔴周边', text)
                self.assertNotIn('接口', text)

                # 点具体标记切换颜色
                panel._current = panel._requirements[0]
                panel._show_requirement(panel.requirement_list.topLevelItem(0).child(0), refresh_files=False)
                panel._on_flag_chip_clicked('temporary_upgrade')
                saved = panel._requirements[0]
                self.assertTrue(saved['flag_done']['temporary_upgrade'])
                self.assertIn('🟢临时', flag_status_text(saved))
                self.assertFalse(saved['flag_done']['has_sql'])
                self.assertIn('🔴SQL', flag_status_text(saved))
                panel.close()

    def test_json_roundtrip_keeps_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'requirements.json')
            item = normalize_requirement({
                'id': 'abc', 'title': 't', 'code': 'C1',
                'has_sql': True, 'temporary_upgrade': True,
                'needs_peripheral_upgrade': False, 'needs_interface_update': True,
                'sql_parts': [], 'source_files': [],
            })
            save_requirements([item], path)
            loaded = load_requirements(path)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0]['has_sql'])
            self.assertTrue(loaded[0]['temporary_upgrade'])
            self.assertTrue(loaded[0]['needs_interface_update'])
            self.assertFalse(loaded[0]['needs_peripheral_upgrade'])


if __name__ == '__main__':
    unittest.main()
