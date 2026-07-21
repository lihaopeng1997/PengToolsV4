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
    flag_chip_text, flag_status_text, load_requirements, normalize_requirement, save_requirements,
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
                self.assertIn('SQL·待完成', text)
                self.assertIn('临时·待完成', text)
                self.assertIn('周边·待完成', text)
                self.assertNotIn('接口', text)

                # 点具体标记切换完成状态
                panel.show()
                self.app.processEvents()
                panel._current = panel._requirements[0]
                panel._show_requirement(panel.requirement_list.topLevelItem(0).child(0), refresh_files=False)
                self.app.processEvents()
                self.assertFalse(panel.flag_section.isHidden())
                self.assertFalse(panel._flag_buttons['temporary_upgrade'].isHidden())
                panel._on_flag_chip_clicked('temporary_upgrade')
                saved = panel._requirements[0]
                self.assertTrue(saved['flag_done']['temporary_upgrade'])
                self.assertIn('临时·已完成', flag_status_text(saved))
                self.assertFalse(saved['flag_done']['has_sql'])
                self.assertIn('SQL·待完成', flag_status_text(saved))
                # 完成标记文案完整可读
                chip = panel._flag_buttons['temporary_upgrade']
                self.assertIn('已完成', chip.text())
                self.assertGreaterEqual(chip.minimumHeight(), 28)
                panel.close()

    def test_json_roundtrip_keeps_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'requirements.json')
            item = normalize_requirement({
                'id': 'abc', 'title': 't', 'code': 'C1',
                'has_sql': True, 'temporary_upgrade': True,
                'needs_peripheral_upgrade': False, 'needs_interface_update': True,
                'sql_parts': [], 'source_files': [],
                'flag_done': {'has_sql': True, 'temporary_upgrade': False},
            })
            save_requirements([item], path)
            loaded = load_requirements(path)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0]['has_sql'])
            self.assertTrue(loaded[0]['temporary_upgrade'])
            self.assertTrue(loaded[0]['needs_interface_update'])
            self.assertFalse(loaded[0]['needs_peripheral_upgrade'])
            self.assertTrue(loaded[0]['flag_done']['has_sql'])

    def test_flag_chips_wrap_and_full_geometry(self):
        """四项完成标记：可见、完整几何、窄宽可单列。"""
        with patch('panels.requirement_panel.load_requirements', return_value=[{
            'id': 'f1', 'title': '四标记', 'code': 'REQ-F',
            'has_sql': True, 'needs_peripheral_upgrade': True,
            'needs_interface_update': True, 'temporary_upgrade': True,
            'flag_done': {}, 'sql_parts': [], 'source_files': [],
            'online_month': '2026-07', 'status': '开发中',
        }]):
            panel = RequirementPanel()
            panel.resize(960, 640)
            panel.show()
            self.app.processEvents()
            # 选中第一项
            root = panel.requirement_list.topLevelItem(0)
            self.assertIsNotNone(root)
            child = root.child(0)
            panel.requirement_list.setCurrentItem(child)
            panel._show_requirement(child, refresh_files=False)
            self.app.processEvents()
            self.assertTrue(panel.flag_section.isVisible())
            visible = [b for b in panel._flag_buttons.values() if b.isVisible()]
            self.assertEqual(len(visible), 4)
            for btn in visible:
                self.assertGreaterEqual(btn.minimumHeight(), 28)
                self.assertGreaterEqual(btn.minimumWidth(), 72)
                self.assertTrue(btn.geometry().width() > 0)
                self.assertIn('·', btn.text())
            panel._layout_flag_chips()
            self.app.processEvents()
            # 始终单行：四个按钮 y 对齐，x 递增，互不重叠
            ys = {btn.geometry().y() for btn in visible}
            self.assertEqual(len(ys), 1)
            rects = []
            for btn in sorted(visible, key=lambda b: b.geometry().x()):
                g = btn.geometry()
                self.assertGreaterEqual(g.height(), 26, btn.text())
                self.assertGreaterEqual(g.width(), 40, btn.text())
                for other in rects:
                    self.assertFalse(g.intersects(other), f'{g} overlaps {other}')
                rects.append(g)
            # 动态上线字段
            self.assertEqual(panel._detail_captions['online'].text(), '上线月份')
            panel.close()

    def test_dialog_uses_online_matter_labels(self):
        dialog = RequirementDialog({'title': 'x', 'code': 'c', 'sql_parts': [], 'source_files': []})
        self.assertEqual(dialog.has_sql.text(), '涉及 SQL')
        self.assertEqual(dialog.peripheral.text(), '通知周边系统')
        self.assertEqual(dialog.interface_update.text(), '更新接口文档')
        self.assertEqual(dialog.temporary.text(), '临时/紧急升级')
        dialog.close()
        self.assertEqual(flag_chip_text('has_sql', False), '○ SQL · 待完成')
        self.assertEqual(flag_chip_text('has_sql', True), '✓ SQL · 已完成')


if __name__ == '__main__':
    unittest.main()
