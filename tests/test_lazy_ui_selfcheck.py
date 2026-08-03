# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from panels.personal_panel import DailyReportTab
from panels.requirement_panel import IS_DIR_ROLE, RequirementPanel
from panels.sql_panel import SqlToolPanel
from tools.svn_workspace import workspace_files


class LazyUiSelfCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont('Microsoft YaHei UI', 10))

    def test_sql_tabs_are_consistent_and_buttons_live_inside_sql_sheet(self):
        panel = SqlToolPanel()
        self.assertEqual([panel.tabs.tabText(i) for i in range(3)], ['升级准备', '发版联动', '系统配置'])
        self.assertEqual(panel.release_date.displayFormat(), 'yyyy-MM-dd')
        self.assertEqual(panel.date_edit.displayFormat(), 'yyyy-MM-dd')
        self.assertIs(panel.load_btn.parent().parent(), panel.tabs.widget(1))
        self.assertEqual(panel.preview_tabs.tabText(0), '升级 SQL')
        self.assertEqual(panel.preview_tabs.tabText(2), '验证 SQL')
        panel.show()
        for width, height in ((1220, 780), (1000, 700), (960, 640)):
            panel.resize(width, height)
            self.app.processEvents()
            self.assertGreaterEqual(panel.width(), 900)
        panel.tabs.setCurrentIndex(1)
        self.app.processEvents()
        # offscreen 下父链可见性可能为 False，这里验证“属于 SQL sheet 且未隐藏”
        self.assertFalse(panel.load_btn.isHidden())
        self.assertEqual(panel.tabs.currentIndex(), 1)
        panel.close()

    def test_daily_report_date_list_and_edit_stay_synced(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('panels.personal_panel.load_reports', return_value={}), \
                    patch('panels.personal_panel.save_reports') as save_reports, \
                    patch('panels.personal_panel.load_reminder_settings', return_value={
                        'enabled': False, 'time': '18:00', 'last_reminder_date': '',
                    }), \
                    patch('panels.personal_panel.save_reminder_settings', side_effect=lambda settings: settings), \
                    patch('panels.personal_panel.show_success'), \
                    patch('panels.personal_panel.show_info'), \
                    patch('panels.personal_panel.show_warning'):
                tab = DailyReportTab()
                today = QDate.currentDate()
                self.assertEqual(tab.date_edit.displayFormat(), 'yyyy-MM-dd')
                self.assertGreaterEqual(tab.date_edit.minimumWidth(), 150)
                keys = [tab.date_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(tab.date_list.count())]
                self.assertIn(today.toString('yyyy-MM-dd'), keys)
                tab.completed.setPlainText('完成 A')
                tab._save_report()
                self.assertTrue(save_reports.called)
                yesterday = today.addDays(-1)
                tab.date_edit.setDate(yesterday)
                self.app.processEvents()
                self.assertEqual(tab.completed.toPlainText(), '')
                # 未保存草稿：写昨天内容后切走再切回应保留
                tab.completed.setPlainText('昨天未保存')
                tab.date_edit.setDate(today)
                self.app.processEvents()
                self.assertIn('完成 A', tab.completed.toPlainText())
                tab.date_edit.setDate(yesterday)
                self.app.processEvents()
                self.assertIn('昨天未保存', tab.completed.toPlainText())
                # 一键复制为今日
                tab._copy_as_today()
                self.assertEqual(tab.date_edit.date(), today)
                self.assertIn('昨天未保存', tab.completed.toPlainText())
                self.assertTrue(hasattr(tab, 'copy_as_today_btn'))
                tab.resize(900, 600)
                self.app.processEvents()
                tab.close()

    def test_requirement_folder_double_click_only_toggles_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            os.makedirs(os.path.join(temp, 'SQL'))
            Path(temp, 'SQL', 'a.sql').write_text('select 1', encoding='utf-8')
            with patch('panels.requirement_panel.load_requirements', return_value=[]):
                panel = RequirementPanel()
            panel._current = {'id': 'x', 'local_path': temp, 'workspace_kind': 'folder', 'svn_locks': {}}
            panel._file_tree_path = temp
            panel._file_tree_loaded(workspace_files(temp))
            folder = next(
                panel.file_tree.topLevelItem(i)
                for i in range(panel.file_tree.topLevelItemCount())
                if panel.file_tree.topLevelItem(i).data(0, IS_DIR_ROLE)
            )
            folder.setExpanded(False)
            panel._open_tree_item(folder, 0)
            self.assertTrue(folder.isExpanded())
            panel._open_tree_item(folder, 0)
            self.assertFalse(folder.isExpanded())
            panel.resize(960, 640)
            self.app.processEvents()
            panel.close()


if __name__ == '__main__':
    unittest.main()
