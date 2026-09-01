# -*- coding: utf-8 -*-
"""业务列表搜索：仅过滤，展示与未搜索时一致（无【】/黄底/命中摘要行）。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class RequirementSearchDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self._last_choices_patcher = patch('panels.requirement_panel.load_last_choices', return_value={})
        self._last_choices_patcher.start()
        self.addCleanup(self._last_choices_patcher.stop)

    def _seed(self):
        return [
            {
                'id': 'a', 'code': 'REQ-AAA', 'title': '阿尔法需求', 'record_kind': '需求',
                'status': '待分析', 'online_month': '2026-06', 'system': '',
                'description': '无关描述', 'sql_parts': [], 'source_files': [],
                'file_count': 0, 'updated_at': '2026-06-01T10:00:00',
            },
            {
                'id': 'b', 'code': 'BUG-BBB', 'title': '贝塔缺陷', 'record_kind': 'BUG',
                'status': '开发中', 'online_month': '2026-07', 'system': '',
                'description': '修复报错', 'sql_parts': [], 'source_files': [],
                'file_count': 1, 'updated_at': '2026-07-01T10:00:00',
            },
        ]

    def _leaf_items(self, panel):
        from PyQt6.QtCore import Qt
        leaves = []
        tree = panel.requirement_list
        for i in range(tree.topLevelItemCount()):
            header = tree.topLevelItem(i)
            for j in range(header.childCount()):
                child = header.child(j)
                data = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    leaves.append(child)
        return leaves

    def test_search_filters_without_highlight_markup(self):
        from panels.requirement_panel import RequirementPanel
        from PyQt6.QtCore import Qt

        seed = self._seed()
        with patch('panels.requirement_panel.load_requirements', return_value=seed), \
                patch('panels.requirement_panel.save_requirements'):
            panel = RequirementPanel()
        panel.search_edit.setText('贝塔')
        panel._refresh()  # 跳过 debounce，直接验证过滤结果
        self.app.processEvents()
        leaves = self._leaf_items(panel)
        self.assertEqual(len(leaves), 1)
        item = leaves[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        self.assertEqual(data.get('id'), 'b')
        code = item.text(0)
        title_block = item.text(1)
        self.assertNotIn('【', code)
        self.assertNotIn('【', title_block)
        self.assertNotIn('命中：', title_block)
        self.assertEqual(code, 'BUG-BBB')
        self.assertIn('贝塔缺陷', title_block)
        # 行高固定，与未搜索一致（44）
        self.assertEqual(item.sizeHint(1).height(), 44)
        panel.close()

    def test_clear_search_restores_all(self):
        from panels.requirement_panel import RequirementPanel

        seed = self._seed()
        with patch('panels.requirement_panel.load_requirements', return_value=seed), \
                patch('panels.requirement_panel.save_requirements'):
            panel = RequirementPanel()
        panel.search_edit.setText('贝塔')
        panel._refresh()
        self.app.processEvents()
        self.assertEqual(len(self._leaf_items(panel)), 1)
        panel.search_edit.clear()
        panel._refresh()
        self.app.processEvents()
        self.assertEqual(len(self._leaf_items(panel)), 2)
        for item in self._leaf_items(panel):
            self.assertNotIn('【', item.text(0))
            self.assertNotIn('【', item.text(1))
        panel.close()

    def test_detail_title_no_brackets(self):
        from panels.requirement_panel import RequirementPanel

        seed = self._seed()
        with patch('panels.requirement_panel.load_requirements', return_value=seed), \
                patch('panels.requirement_panel.save_requirements'):
            panel = RequirementPanel()
        panel.search_edit.setText('贝塔')
        panel._refresh()
        self.app.processEvents()
        self.assertEqual(panel.detail_title.text(), '贝塔缺陷')
        self.assertNotIn('【', panel.detail_title.text())
        panel.close()


class SearchCacheTests(unittest.TestCase):
    def test_requirement_search_text_cached(self):
        from tools.requirements import (
            clear_requirement_search_cache,
            requirement_search_text,
        )

        clear_requirement_search_cache()
        req = {
            'id': 'c1', 'code': 'REQ-1', 'title': '缓存测试', 'updated_at': '2026-01-01',
            'sql_parts': [], 'source_files': [],
        }
        a = requirement_search_text(req)
        b = requirement_search_text(req)
        self.assertEqual(a, b)
        self.assertIn('缓存测试', a.split('\n', 1)[0])


class InterfaceSessionCapTests(unittest.TestCase):
    def test_clip_and_evict(self):
        from panels.interface_debug_panel import (
            InterfaceDebugPanel,
            MAX_BODY_CHARS,
            MAX_SESSION_RECORDS,
        )

        self.assertGreater(MAX_SESSION_RECORDS, 100)
        long_body = 'x' * (MAX_BODY_CHARS + 50)
        clipped = InterfaceDebugPanel._clip_body(long_body)
        self.assertLess(len(clipped), len(long_body))
        self.assertIn('truncated', clipped)


if __name__ == '__main__':
    unittest.main()
