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

    def test_rich_format_table_font_color_and_small_default_image(self):
        from PyQt6.QtGui import QColor, QTextImageFormat
        from ui.daily_rich_edit import DailyRichEdit, _DEFAULT_INSERT_WIDTH

        editor = DailyRichEdit()
        editor.resize(480, 280)
        self.assertLessEqual(_DEFAULT_INSERT_WIDTH, 360)
        self.assertEqual(editor._max_image_width, _DEFAULT_INSERT_WIDTH)
        editor.setPlainText('字体样例')
        editor.selectAll()
        editor.apply_font_point_size(18)
        self.assertEqual(editor.current_font_point_size(), 18)
        editor.apply_text_color(QColor('#B85C5C'))
        self.assertEqual(editor.currentCharFormat().foreground().color().name().upper(), '#B85C5C')
        editor.toggle_bold()
        self.assertGreaterEqual(int(editor.currentCharFormat().fontWeight()), 600)
        self.assertTrue(editor.insert_table(2, 3))
        html = editor.toHtml().lower()
        self.assertIn('<table', html)
        fmt = QTextImageFormat()
        fmt.setName('missing-file')
        fmt.setWidth(80)
        fmt.setHeight(40)
        editor.textCursor().insertImage(fmt)
        hit = editor.list_images()[-1]
        self.assertTrue(editor.load_image_from_hit(hit).isNull())
        editor.close()

    def test_daily_tab_exposes_format_bar(self):
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
            self.assertEqual(tab.fmt_table_btn.text(), '表格')
            self.assertEqual(tab.fmt_color_btn.text(), '颜色')
            self.assertTrue(tab.completed.insert_table(2, 2))
            self.assertIn('<table', tab.completed.toHtml().lower())
            tab.close()


if __name__ == '__main__':
    unittest.main()
