# -*- coding: utf-8 -*-
"""XML 工作区搜索：高亮 + 跳转。"""

from __future__ import annotations

import sys
import unittest

from PyQt6.QtWidgets import QApplication


class XmlSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_search_output_and_navigate(self):
        from ui.xml_workspace import XmlWorkspace

        w = XmlWorkspace('zh')
        w.resize(900, 500)
        w.show()
        self.app.processEvents()
        raw = '<?xml version="1.0" encoding="UTF-8"?><root><name>demo_user</name><city>Shanghai</city></root>'
        w.set_input_text(raw, auto_format=True)
        self.app.processEvents()
        self.assertIn('demo_user', w.output_text())

        w.search_edit.setText('demo_user')
        self.app.processEvents()
        self.assertGreaterEqual(len(w._search_hits), 1)
        self.assertEqual(w._search_index, 0)
        # 当前应在输出区
        edit, start, end = w._search_hits[0]
        self.assertIs(edit, w.output_edit)
        self.assertLess(start, end)

        w.search_edit.setText('city')
        self.app.processEvents()
        self.assertGreaterEqual(len(w._search_hits), 1)
        w._next_match()
        self.assertGreaterEqual(w._search_index, 0)
        w.close()

    def test_search_empty_clears(self):
        from ui.xml_workspace import XmlWorkspace

        w = XmlWorkspace('zh')
        w.set_input_text('<a>x</a>', auto_format=True)
        w.search_edit.setText('x')
        self.assertTrue(w._search_hits)
        w.search_edit.setText('')
        self.assertEqual(w._search_hits, [])
        w.close()


if __name__ == '__main__':
    unittest.main()
