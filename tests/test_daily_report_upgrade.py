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
        chrome = (
            '<!DOCTYPE HTML><html><head><style type="text/css">'
            'p, li { white-space: pre-wrap; }</style></head>'
            '<body style="font-family:Microsoft YaHei UI;"><p><br></p></body></html>'
        )
        self.assertEqual(plain_from_html(chrome), '')
        empty = normalize_report({'completed_html': chrome, 'issues': '', 'tomorrow': '', 'notes': ''})
        self.assertEqual(empty['completed'], '')

    def test_qt_html_wrapper_is_not_dirty(self):
        from tools.daily_reports import is_report_dirty

        saved = {
            'completed': '做完 A',
            'completed_html': '<p>做完 A</p>',
            'issues': '',
            'tomorrow': '继续联调',
            'tomorrow_html': '<p>继续联调</p>',
            'notes': '',
        }
        current = {
            'completed': '做完 A',
            'completed_html': (
                '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
                '"http://www.w3.org/TR/REC-html40/strict.dtd">'
                '<html><body style="font-family:Microsoft YaHei UI;">'
                '<p style="margin-top:0px;">做完 A</p></body></html>'
            ),
            'issues': '',
            'issues_html': '<!DOCTYPE HTML><html><body><p><br></p></body></html>',
            'tomorrow': '继续联调',
            'tomorrow_html': '<p style="margin-top:0px;">继续联调</p>',
            'notes': '',
        }
        self.assertFalse(is_report_dirty(saved, current))
        current['completed'] = '做完 A 又改了一点'
        self.assertTrue(is_report_dirty(saved, current))

    def test_image_size_change_is_dirty(self):
        from tools.daily_reports import is_report_dirty

        saved = {
            'completed': '[图片]',
            'completed_html': '<img src="daily_assets/2026-08-13/a.png" width="200" height="100">',
            'issues': '',
            'tomorrow': '',
            'notes': '',
        }
        same = {
            'completed': '[图片]',
            'completed_html': '<img src="file:///C:/data/daily_assets/2026-08-13/a.png" width="200" height="100">',
            'issues': '',
            'tomorrow': '',
            'notes': '',
        }
        resized = {
            'completed': '[图片]',
            'completed_html': '<img src="daily_assets/2026-08-13/a.png" width="80" height="40">',
            'issues': '',
            'tomorrow': '',
            'notes': '',
        }
        self.assertFalse(is_report_dirty(saved, same))
        self.assertTrue(is_report_dirty(saved, resized))

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
            self.assertEqual(tab.expand_all_btn.text(), '全部展开')
            self.assertEqual(tab.collapse_all_btn.text(), '全部折叠')
            self.assertEqual(tab.expand_all_btn.objectName(), 'fold-action-btn')
            tab.completed.setPlainText('带图段落')
            plain, html, _assets = tab.completed.export_content()
            self.assertIn('带图段落', plain)
            tab.close()

    def test_completed_editor_takes_most_vertical_space(self):
        from unittest.mock import patch
        from panels.personal_panel import DailyReportTab

        with patch('panels.personal_panel.load_reports', return_value={}), \
                patch('panels.personal_panel.save_reports'), \
                patch('panels.personal_panel.load_drafts', return_value={}), \
                patch('panels.personal_panel.save_drafts'), \
                patch('panels.personal_panel.load_reminder_settings', return_value={
                    'enabled': False, 'time': '17:30', 'last_reminder_date': '',
                    'history_collapsed_months': [], 'history_expanded_months': [],
                    'history_expand_pinned': True,
                }):
            tab = DailyReportTab()
            form = tab.completed.parentWidget().layout()
            completed_stretch = form.stretch(form.indexOf(tab.completed))
            issues_stretch = form.stretch(form.indexOf(tab.issues))
            tomorrow_stretch = form.stretch(form.indexOf(tab.tomorrow))
            notes_stretch = form.stretch(form.indexOf(tab.notes))
            self.assertGreater(completed_stretch, issues_stretch)
            self.assertEqual(issues_stretch, tomorrow_stretch)
            self.assertEqual(issues_stretch, notes_stretch)
            self.assertGreaterEqual(tab.completed.minimumHeight(), 180)
            self.assertLessEqual(tab.issues.minimumHeight(), 72)
            self.assertLessEqual(tab.tomorrow.minimumHeight(), 72)
            self.assertLessEqual(tab.notes.minimumHeight(), 64)
            self.assertGreater(tab.completed.sizeHint().height(), tab.issues.sizeHint().height())
            self.assertGreater(tab.completed.sizeHint().height(), tab.notes.sizeHint().height())
            tab.close()

    def test_clicking_saved_date_is_not_marked_unsaved(self):
        from unittest.mock import patch
        from panels.personal_panel import DailyReportTab
        from PyQt6.QtCore import QDate

        seed = {
            '2026-08-13': {
                'completed': '昨天写过',
                'completed_html': '<p>昨天写过</p>',
                'issues': '',
                'tomorrow': '明天联调',
                'tomorrow_html': '<p>明天联调</p>',
                'notes': '',
            },
        }
        with patch('panels.personal_panel.load_reports', return_value=seed), \
                patch('panels.personal_panel.save_reports'), \
                patch('panels.personal_panel.load_drafts', return_value={}), \
                patch('panels.personal_panel.save_drafts'), \
                patch('panels.personal_panel.load_reminder_settings', return_value={
                    'enabled': False, 'time': '17:30', 'last_reminder_date': '',
                    'history_collapsed_months': [], 'history_expanded_months': [],
                    'history_expand_pinned': True,
                }), \
                patch('panels.personal_panel.save_reminder_settings', side_effect=lambda s: s):
            tab = DailyReportTab()
            tab._load_date(QDate(2026, 8, 13))
            self.assertFalse(tab._is_dirty('2026-08-13'))
            self.assertEqual(tab.unsaved_label.text(), '')
            self.assertNotIn('2026-08-13', tab._drafts)
            tab.close()

    def test_action_buttons_share_date_row(self):
        from unittest.mock import patch
        from panels.personal_panel import DailyReportTab

        with patch('panels.personal_panel.load_reports', return_value={}), \
                patch('panels.personal_panel.save_reports'), \
                patch('panels.personal_panel.load_drafts', return_value={}), \
                patch('panels.personal_panel.save_drafts'), \
                patch('panels.personal_panel.load_reminder_settings', return_value={
                    'enabled': False, 'time': '17:30', 'last_reminder_date': '',
                    'history_collapsed_months': [], 'history_expanded_months': [],
                    'history_expand_pinned': True,
                }):
            tab = DailyReportTab()
            date_row = tab.date_edit.parentWidget().layout().itemAt(0).layout()
            widgets = []
            for index in range(date_row.count()):
                item = date_row.itemAt(index)
                widget = item.widget() if item else None
                if widget is not None:
                    widgets.append(widget)
            self.assertIn(tab.date_edit, widgets)
            self.assertIn(tab.delete_btn, widgets)
            self.assertIn(tab.copy_btn, widgets)
            self.assertIn(tab.save_btn, widgets)
            self.assertIn(tab.import_yesterday_btn, widgets)
            self.assertEqual(tab.import_yesterday_btn.text(), '带入昨日计划')
            tab.close()

    def test_import_yesterday_plan_fills_today_completed(self):
        from unittest.mock import patch
        import datetime
        from panels.personal_panel import DailyReportTab

        today = datetime.date.today().isoformat()
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        seed = {
            yesterday: {
                'completed': '昨天做完了',
                'tomorrow': '今天要做的事',
                'tomorrow_html': '<p>今天要做的事</p>',
                'issues': '',
                'notes': '',
            },
        }
        with patch('panels.personal_panel.load_reports', return_value=seed), \
                patch('panels.personal_panel.save_reports'), \
                patch('panels.personal_panel.load_drafts', return_value={}), \
                patch('panels.personal_panel.save_drafts'), \
                patch('panels.personal_panel.load_reminder_settings', return_value={
                    'enabled': False, 'time': '17:30', 'last_reminder_date': '',
                    'history_collapsed_months': [], 'history_expanded_months': [],
                    'history_expand_pinned': True,
                }), \
                patch('panels.personal_panel.save_reminder_settings', side_effect=lambda s: s), \
                patch('panels.personal_panel.show_success'), \
                patch('panels.personal_panel.show_info'):
            tab = DailyReportTab()
            tab._import_yesterday_plan()
            self.assertEqual(tab._date_key(), today)
            self.assertIn('今天要做的事', tab.completed.toPlainText())
            self.assertTrue(tab._is_dirty(today))
            tab.close()

    def test_inserted_image_can_be_resized_and_persists_in_html(self):
        from PyQt6.QtGui import QTextImageFormat
        from ui.daily_rich_edit import DailyRichEdit

        editor = DailyRichEdit()
        editor.resize(480, 280)
        fmt = QTextImageFormat()
        fmt.setName('daily-demo')
        fmt.setWidth(200)
        fmt.setHeight(100)
        editor.textCursor().insertImage(fmt)
        hits = editor.list_images()
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['width'], 200)
        self.assertEqual(hits[0]['height'], 100)
        self.assertTrue(editor.apply_image_width(hits[0], 80))
        resized = editor.list_images()[0]
        self.assertEqual(resized['width'], 80)
        self.assertEqual(resized['height'], 40)
        self.assertTrue(editor.apply_image_scale(resized, 2.0))
        scaled = editor.list_images()[0]
        self.assertEqual(scaled['width'], 160)
        self.assertEqual(scaled['height'], 80)
        html = editor.toHtml()
        self.assertRegex(html, r'width="?160"?')
        self.assertRegex(html, r'height="?80"?')
        editor.close()

    def test_image_context_menu_uses_chinese_not_qt_english(self):
        from PyQt6.QtGui import QTextImageFormat
        from ui.daily_rich_edit import DailyRichEdit

        editor = DailyRichEdit()
        editor.language = 'zh'
        editor.resize(480, 280)
        fmt = QTextImageFormat()
        fmt.setName('daily-demo')
        fmt.setWidth(120)
        fmt.setHeight(60)
        editor.textCursor().insertImage(fmt)
        hit = editor.list_images()[0]
        menu = editor.build_context_menu(hit)
        texts = [action.text() for action in menu.actions() if action.text()]
        self.assertIn('查看大图', texts)
        self.assertIn('放大图片', texts)
        self.assertIn('缩小图片', texts)
        self.assertIn('适应编辑区宽度', texts)
        self.assertIn('原始大小', texts)
        self.assertIn('复制', texts)
        self.assertIn('粘贴', texts)
        self.assertIn('删除', texts)
        for english in ('Undo', 'Redo', 'Cut', 'Copy', 'Paste', 'Delete', 'Select All'):
            self.assertNotIn(english, texts)
        menu.deleteLater()
        editor.language = 'en'
        en_menu = editor.build_context_menu(hit)
        en_texts = [action.text() for action in en_menu.actions() if action.text()]
        self.assertIn('Enlarge image', en_texts)
        self.assertIn('Copy', en_texts)
        self.assertNotIn('Undo', en_texts)
        en_menu.deleteLater()
        editor.close()


if __name__ == '__main__':
    unittest.main()
