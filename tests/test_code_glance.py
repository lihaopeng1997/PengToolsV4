# -*- coding: utf-8 -*-
"""CodeGlance 缩略导航条基础行为。"""

from __future__ import annotations

import sys
import unittest

from PyQt6.QtWidgets import QApplication


class CodeGlanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_foldable_has_glance_by_default(self):
        from ui.foldable_text_edit import FoldablePlainTextEdit

        edit = FoldablePlainTextEdit()
        edit.resize(640, 400)
        edit.show()
        self.app.processEvents()
        # 空内容时不显示右侧缩略条（避免错贴到左侧）
        self.assertIsNotNone(edit._glance)
        self.assertFalse(edit._glance.isVisible())
        self.assertEqual(edit.glance_width(), 0)

        sample = '{\n  "a": {\n    "b": 1\n  },\n  "c": [1, 2, 3]\n}\n'
        edit.setPlainText(sample * 20)
        self.app.processEvents()
        edit._layout_side_bars()
        self.assertTrue(edit._glance.isVisible())
        self.assertGreaterEqual(edit.glance_width(), 48)
        # 必须贴在右侧，不能侵入左折叠栏
        self.assertGreaterEqual(edit._glance.x(), edit.fold_margin_width())
        self.assertGreater(edit._glance.x(), edit.width() // 2)

        # 清空后再次隐藏
        edit.setPlainText('')
        self.app.processEvents()
        edit._layout_side_bars()
        self.assertFalse(edit._glance.isVisible())
        self.assertEqual(edit.glance_width(), 0)

        edit.setPlainText(sample * 5)
        self.app.processEvents()
        edit._layout_side_bars()
        # 关闭 / 打开
        edit.set_glance_visible(False)
        self.assertEqual(edit.glance_width(), 0)
        edit.set_glance_visible(True)
        edit._layout_side_bars()
        self.assertGreaterEqual(edit.glance_width(), 48)
        # 调宽
        edit._glance.set_glance_width(100)
        self.assertEqual(edit.glance_width(), 100)
        edit.close()

    def test_glance_line_color_heuristics(self):
        from ui.code_glance import CodeGlanceBar
        from ui.foldable_text_edit import FoldablePlainTextEdit

        edit = FoldablePlainTextEdit()
        bar = edit._glance
        pal = {'PRIMARY': '#112233', 'SUCCESS': '#00aa00', 'CYAN': '#00aaff', 'WARNING': '#cc8800', 'TEXT': '#333'}
        self.assertEqual(bar._line_color('  "name": "x",', pal).name(), '#112233')
        self.assertEqual(bar._line_color('  123,', pal).name(), '#00aaff')
        self.assertEqual(bar._line_color('  true', pal).name(), '#cc8800')
        edit.close()


if __name__ == '__main__':
    unittest.main()
