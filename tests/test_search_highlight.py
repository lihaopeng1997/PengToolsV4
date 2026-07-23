# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.pinyin_search import find_term_spans, highlight_terms, match_snippet, first_match_line


class SearchHighlightUnitTests(unittest.TestCase):
    def test_find_term_spans_and_highlight(self):
        text = '车险承保中心 SQL 升级脚本'
        spans = find_term_spans(text, 'SQL 承保')
        self.assertTrue(spans)
        marked = highlight_terms(text, 'SQL')
        self.assertIn('【SQL】', marked)

    def test_match_snippet_contains_mark(self):
        body = '前面一堆无关内容 ' + ('x' * 40) + ' 需要定位关键词ERROR并展示 ' + ('y' * 40)
        sn = match_snippet(body, 'ERROR', radius=12)
        self.assertIn('【ERROR】', sn)
        self.assertTrue(sn.startswith('…') or 'ERROR' in sn)

    def test_first_match_line(self):
        text = 'line0\nline ERROR here\nline2'
        idx, line = first_match_line(text, 'ERROR')
        self.assertEqual(idx, 1)
        self.assertIn('ERROR', line)


class SearchHighlightUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont('Microsoft YaHei UI', 10))

    def test_text_edit_highlight_and_scroll(self):
        from PyQt6.QtWidgets import QPlainTextEdit
        from ui.search_highlight import apply_text_highlights, clear_text_highlights
        edit = QPlainTextEdit()
        edit.setPlainText('alpha\nbeta KEYWORD gamma\ndelta')
        count = apply_text_highlights(edit, 'KEYWORD')
        self.assertEqual(count, 1)
        self.assertTrue(edit.extraSelections())
        clear_text_highlights(edit)
        self.assertFalse(edit.extraSelections())

    def test_list_item_paint(self):
        from PyQt6.QtWidgets import QListWidgetItem
        from ui.search_highlight import paint_list_item
        item = QListWidgetItem('hello world')
        paint_list_item(item, matched=True, current=True)
        self.assertTrue(item.background().color().isValid())


if __name__ == '__main__':
    unittest.main()
