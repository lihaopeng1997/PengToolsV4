# -*- coding: utf-8 -*-
"""内置使用说明：HTML 资源与对话框烟雾测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


class UserGuideTests(unittest.TestCase):
    def test_html_exists_and_has_sections(self):
        from ui.help_dialog import help_html_path
        path = help_html_path()
        self.assertTrue(os.path.isfile(path), path)
        text = open(path, encoding='utf-8').read()
        self.assertIn('PengToolsHub', text)
        self.assertIn('接口排查', text)
        self.assertIn('恢复系统代理', text)
        self.assertIn('待升级', text)

    def test_dialog_loads(self):
        from PyQt6.QtWidgets import QApplication
        from ui.help_dialog import UserGuideDialog
        app = QApplication.instance() or QApplication([])
        dlg = UserGuideDialog(language='zh')
        self.assertIn('使用说明', dlg.windowTitle())
        # QTextBrowser 应已加载本地 HTML
        self.assertTrue(bool(dlg.browser.toHtml()) or bool(dlg.browser.toPlainText()))
        dlg.close()


if __name__ == '__main__':
    unittest.main()
