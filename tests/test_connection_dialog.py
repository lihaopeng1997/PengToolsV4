# -*- coding: utf-8 -*-
"""共享数据库连接编辑对话框的回归测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ConnectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_locked_dialect_is_preserved_in_payload(self):
        """锁定方言的页面不能被连接编辑控件改写为其他数据库类型。"""
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={"name": "Redis 测试", "dialect": "redis", "host": "127.0.0.1"},
            locked_dialect="redis",
        )
        try:
            self.assertFalse(dialog.dialect.isEnabled())
            item, _password = dialog.payload()
            self.assertEqual(item["dialect"], "redis")
            self.assertEqual(item["port"], 6379)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
