# -*- coding: utf-8 -*-
"""格式化文本折叠区域计算（HiJson 风格）。"""

from __future__ import annotations

import unittest

from tools.code_folding import (
    compute_bracket_fold_regions,
    compute_fold_regions,
    compute_indent_fold_regions,
    leading_indent,
    lines_hidden_by_collapsed,
)


class CodeFoldingTests(unittest.TestCase):
    def test_leading_indent(self):
        self.assertEqual(leading_indent('  foo'), 2)
        self.assertEqual(leading_indent('\tbar'), 2)
        self.assertIsNone(leading_indent(''))
        self.assertIsNone(leading_indent('   '))

    def test_json_pretty_has_nested_folds(self):
        text = '''{
  "a": {
    "b": 1
  },
  "c": [1, 2]
}'''
        regions = compute_fold_regions(text, mode='auto')
        starts = {s for s, _ in regions}
        # 根对象与嵌套 object / array
        self.assertIn(0, starts)
        self.assertTrue(any(s > 0 for s, e in regions if e > s))
        # 根折叠应覆盖到最后一行
        root_end = dict(regions)[0]
        self.assertEqual(root_end, len(text.splitlines()) - 1)

    def test_collapse_hides_inner_lines(self):
        text = '''{
  "a": 1,
  "b": 2
}'''
        regions = compute_fold_regions(text)
        hidden = lines_hidden_by_collapsed(regions, {0})
        self.assertIn(1, hidden)
        self.assertIn(2, hidden)
        self.assertIn(3, hidden)
        self.assertNotIn(0, hidden)

    def test_bracket_ignores_braces_in_strings(self):
        text = '''{
  "x": "{ not a fold }",
  "y": 1
}'''
        regions = compute_bracket_fold_regions(text)
        # 不应为字符串内的 { 单独建错误区间导致 start>end 之类；至少根有效
        self.assertTrue(any(s == 0 for s, e in regions))
        for s, e in regions:
            self.assertLess(s, e)

    def test_indent_xml_like(self):
        text = '''<root>
  <child>
    <leaf>1</leaf>
  </child>
</root>'''
        regions = compute_indent_fold_regions(text)
        self.assertTrue(regions)
        starts = {s for s, _ in regions}
        self.assertIn(0, starts)

    def test_empty_and_single_line(self):
        self.assertEqual(compute_fold_regions(''), [])
        self.assertEqual(compute_fold_regions('{"a":1}'), [])


if __name__ == '__main__':
    unittest.main()
