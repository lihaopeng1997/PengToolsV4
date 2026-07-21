# -*- coding: utf-8 -*-
"""本轮定向：拼音搜索、文件库、发版联动命名、加解密参数。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class PinyinSearchTests(unittest.TestCase):
    def test_initials_and_match_chinese(self):
        from tools.pinyin_search import build_search_blob, match_query, pinyin_initials, backend_name
        self.assertIn(backend_name(), ('pypinyin', 'builtin_initials'))
        # 车险承保 → cxcb（内置表）
        init = pinyin_initials('车险承保')
        self.assertTrue(init.startswith('c') or 'c' in init)
        blob = build_search_blob('车险承保中心', 'REQ-2026-001', 'prpcar.sql')
        self.assertTrue(match_query(blob, '车险'))
        self.assertTrue(match_query(blob, 'cx'))  # 首字母
        self.assertTrue(match_query(blob, 'REQ'))
        self.assertTrue(match_query(blob, 'sql'))
        self.assertFalse(match_query(blob, 'zzzznotfound'))

    def test_requirement_search_uses_pinyin(self):
        from tools.requirements import requirement_search_text
        from tools.pinyin_search import match_query
        req = {
            'code': 'REQ-001',
            'title': '车险承保改造',
            'system': '车险承保中心',
            'description': '',
            'sql_parts': [],
            'source_files': [],
        }
        blob = requirement_search_text(req)
        self.assertTrue(match_query(blob, 'cxcb') or match_query(blob, '车险'))

    def test_personal_search_pinyin(self):
        from tools.personal_knowledge import search_entries
        entries = [
            {'title': '车险接口说明', 'content': '说明', 'tags': '业务', 'category': 'note', 'source': '', 'sheet_name': '', 'rows': []},
            {'title': '运维手册', 'content': 'linux', 'tags': '', 'category': 'note', 'source': '', 'sheet_name': '', 'rows': []},
        ]
        hit = search_entries(entries, 'cx', 'all')
        self.assertTrue(any('车险' in e['title'] for e in hit))


class ReleaseLinkNamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_nav_and_sql_tabs(self):
        from ui.navigation_model import display_name
        from panels.sql_panel import SqlToolPanel
        self.assertEqual(display_name(2, 'zh'), '发版联动')
        p = SqlToolPanel()
        self.assertEqual(p.tabs.tabText(1), '发版联动')

    def test_requirement_file_tab_name(self):
        from panels.requirement_panel import RequirementPanel
        p = RequirementPanel('zh')
        texts = [p.detail_tabs.tabText(i) for i in range(p.detail_tabs.count())]
        self.assertIn('文件库', texts)
        self.assertEqual(p.sql_btn.text(), '打开发版联动')
        self.assertTrue(hasattr(p, 'file_search_edit'))
        self.assertTrue(hasattr(p, 'file_expand_btn'))
        self.assertEqual(p.file_tree.columnCount(), 5)


class FileLibraryFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_filter_from_cache_no_rescan(self):
        from panels.requirement_panel import RequirementPanel
        p = RequirementPanel('zh')
        p._file_entries_cache = [
            {
                'path': r'C:\tmp\req\a\note.txt', 'relative_path': 'a/note.txt',
                'is_dir': False, 'file_type': 'TXT', 'modified_at': '2026-07-01 10:00',
                'size': '12 B',
            },
            {
                'path': r'C:\tmp\req\a', 'relative_path': 'a',
                'is_dir': True, 'file_type': '文件夹', 'modified_at': '2026-07-01 10:00',
                'size': '',
            },
            {
                'path': r'C:\tmp\req\b.sql', 'relative_path': 'b.sql',
                'is_dir': False, 'file_type': 'SQL', 'modified_at': '2026-07-02 11:00',
                'size': '1 KB',
            },
        ]
        p.file_search_edit.setText('sql')
        p._filter_file_tree_local()
        # 至少应有 sql 文件节点
        found = False
        it = p.file_tree.invisibleRootItem()
        stack = [it.child(i) for i in range(it.childCount())]
        while stack:
            node = stack.pop()
            if 'sql' in (node.text(0) or '').lower() or 'sql' in (node.text(4) or '').lower():
                found = True
            for i in range(node.childCount()):
                stack.append(node.child(i))
        self.assertTrue(found)


class GatewayParamsStillVisible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_key_visible(self):
        from panels.gateway_panel import GatewayDecodePanel
        p = GatewayDecodePanel('zh')
        self.assertFalse(p.config_group.isHidden())
        self.assertIn('密钥', p.key_label.text())
        p.key_cipher.setPlainText('aabb')
        p.set_cipher_text('Ym9keQ==')
        self.assertEqual(p.key_cipher.toPlainText(), 'aabb')


if __name__ == '__main__':
    unittest.main()
