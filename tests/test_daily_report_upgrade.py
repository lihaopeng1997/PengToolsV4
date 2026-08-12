# -*- coding: utf-8 -*-
"""日报：按月分组、资源图、旧数据兼容。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class DailyReportModelTests(unittest.TestCase):
    def test_normalize_legacy_plain(self):
        from tools.daily_reports import normalize_report, plain_from_html

        report = normalize_report({
            'completed': '做完 A\n做完 B',
            'issues': '',
            'tomorrow': '继续',
            'notes': '',
        })
        self.assertIn('做完 A', report['completed'])
        self.assertIn('做完 A', report['completed_html'])
        self.assertEqual(plain_from_html('<p>x<br/>y</p>').replace('\n', ' '), 'x y')

    def test_group_dates_by_month(self):
        from tools.daily_reports import group_dates_by_month, month_label

        groups = group_dates_by_month(['2026-08-12', '2026-08-01', '2026-07-30'])
        self.assertEqual(groups['2026-08'], ['2026-08-12', '2026-08-01'])
        self.assertIn('2026年8月', month_label('2026-08', 'zh'))

    def test_save_image_and_cleanup(self):
        from tools.daily_reports import (
            absolute_asset_path, cleanup_day_assets, save_image_bytes,
        )

        with tempfile.TemporaryDirectory() as temp:
            # 临时把资源根指到 temp：直接传 root
            png = (
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
                b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
                b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd'
                b'\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
            )
            rel = save_image_bytes('2026-08-12', png, ext='.png', root=os.path.join(temp, 'daily_assets'))
            # root 参数是 asset 根目录的父级？看实现 asset_dir_for uses root or DAILY_ASSETS_DIR
            # save_image_bytes root= is for DAILY_ASSETS_DIR equivalent
            self.assertTrue(rel.startswith('daily_assets/2026-08-12/'))
            abs_path = os.path.join(temp, 'daily_assets', '2026-08-12', os.path.basename(rel))
            # 实现里 asset_dir_for(date, root=root) joins root/date
            # so root should be .../daily_assets
            folder = os.path.join(temp, 'daily_assets', '2026-08-12')
            files = os.listdir(folder) if os.path.isdir(folder) else []
            self.assertTrue(files)
            cleanup_day_assets('2026-08-12', root=os.path.join(temp, 'daily_assets'))
            self.assertFalse(os.path.isdir(folder))


class DailyReportUiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_tree_groups_and_rich_export(self):
        from unittest.mock import patch
        from panels.personal_panel import DailyReportTab
        from PyQt6.QtCore import QDate

        seed = {
            '2026-08-11': {'completed': '昨天的活', 'issues': '', 'tomorrow': '', 'notes': ''},
            '2026-07-01': {'completed': '上月', 'issues': '', 'tomorrow': '', 'notes': ''},
        }
        with patch('panels.personal_panel.load_reports', return_value=seed), \
                patch('panels.personal_panel.save_reports'), \
                patch('panels.personal_panel.load_drafts', return_value={}), \
                patch('panels.personal_panel.save_drafts'), \
                patch('panels.personal_panel.load_reminder_settings', return_value={
                    'enabled': False, 'time': '17:30', 'last_reminder_date': '',
                    'history_collapsed_months': [], 'history_expanded_months': ['2026-07'],
                    'history_expand_pinned': True,
                }), \
                patch('panels.personal_panel.save_reminder_settings', side_effect=lambda s: s), \
                patch('panels.personal_panel.show_success'), \
                patch('panels.personal_panel.show_warning'):
            tab = DailyReportTab()
            keys = tab.list_date_keys()
            self.assertIn('2026-08-11', keys)
            self.assertIn('2026-07-01', keys)
            # 至少有一个月份组头
            self.assertGreaterEqual(tab.date_tree.topLevelItemCount(), 1)
            tab.completed.setPlainText('带图段落')
            plain, html, _assets = tab.completed.export_content()
            self.assertIn('带图段落', plain)
            tab.close()


if __name__ == '__main__':
    unittest.main()
