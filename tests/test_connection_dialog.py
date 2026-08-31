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

    def test_password_field_uses_eye_control_and_toggles_echo_mode(self):
        """密码字段使用标准眼睛图标按钮，尺寸紧凑且支持切换明文。"""
        from PyQt6.QtWidgets import QLineEdit
        from ui.field_metrics import FIELD_H, wrap_secret_field

        edit = QLineEdit("super_secret")
        row, btn = wrap_secret_field(edit, reveal_text="查看", hide_text="隐藏")

        self.assertEqual(edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(btn.text(), "")  # icon-only, 无大文字
        self.assertEqual(btn.width(), FIELD_H)
        self.assertEqual(btn.height(), FIELD_H)
        self.assertEqual(btn.toolTip(), "显示密码")
        self.assertFalse(btn.icon().isNull())

        btn.setChecked(True)
        self.assertEqual(edit.echoMode(), QLineEdit.EchoMode.Normal)
        self.assertEqual(btn.toolTip(), "隐藏密码")

        btn.setChecked(False)
        self.assertEqual(edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(btn.toolTip(), "显示密码")

    def test_sql_workbench_action_order_prioritizes_run_before_format(self):
        """SQL 工作台执行主操作位于格式化之前并靠近编辑器。"""
        from panels.ai_workbench_panel import AiWorkbenchPanel

        panel = AiWorkbenchPanel()
        try:
            self.assertTrue(hasattr(panel, "run_btn"))
            self.assertTrue(hasattr(panel, "format_btn"))
            self.assertEqual(panel.run_btn.objectName(), "primary-btn")
            self.assertEqual(panel.format_btn.objectName(), "btn-ghost")

            # 验证两者在同一父级容器中，且在 editor_row 中 run_btn 排在 format_btn 前面
            parent = panel.format_btn.parentWidget()
            self.assertIsNotNone(parent)
            self.assertEqual(parent, panel.run_btn.parentWidget())
            mid_l = parent.layout()
            self.assertIsNotNone(mid_l)
            editor_row = mid_l.itemAt(0).layout()
            self.assertIsNotNone(editor_row)

            run_idx = -1
            fmt_idx = -1
            for i in range(editor_row.count()):
                item = editor_row.itemAt(i)
                if item and item.widget() == panel.run_btn:
                    run_idx = i
                elif item and item.widget() == panel.format_btn:
                    fmt_idx = i

            self.assertGreaterEqual(run_idx, 0)
            self.assertGreaterEqual(fmt_idx, 0)
            self.assertLess(run_idx, fmt_idx)
        finally:
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
