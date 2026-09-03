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

    def test_oceanbase_legacy_item_displays_oracle_mode_and_normalizes_payload(self):
        from ui.connection_dialog import ConnectionDialog

        # 旧版没有 mode 或 mode='standalone'
        dialog = ConnectionDialog(
            language="zh",
            item={"name": "OB 测试", "dialect": "oceanbase", "host": "10.0.0.1", "mode": "standalone"},
        )
        try:
            self.assertEqual(dialog.mode.currentData(), "oracle")
            self.assertFalse(dialog.oracle_hint.isHidden())
            self.assertEqual(dialog.database_label.text(), "SID/服务名")

            item, _ = dialog.payload()
            self.assertEqual(item["mode"], "oracle")
        finally:
            dialog.close()

    def test_oceanbase_mode_switch_updates_hints_and_labels(self):
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={"name": "OB 模式切换测试", "dialect": "oceanbase", "host": "10.0.0.1", "mode": "oracle"},
        )
        try:
            # 初始为 Oracle 模式
            self.assertEqual(dialog.mode.currentData(), "oracle")
            self.assertFalse(dialog.oracle_hint.isHidden())
            self.assertEqual(dialog.database_label.text(), "SID/服务名")

            # 切换到 MySQL 模式
            mysql_idx = dialog.mode.findData("mysql")
            dialog.mode.setCurrentIndex(mysql_idx)
            self.assertEqual(dialog.mode.currentData(), "mysql")
            self.assertTrue(dialog.oracle_hint.isHidden())
            self.assertEqual(dialog.database_label.text(), "库名")

            # 切换回 Oracle 模式
            oracle_idx = dialog.mode.findData("oracle")
            dialog.mode.setCurrentIndex(oracle_idx)
            self.assertEqual(dialog.mode.currentData(), "oracle")
            self.assertFalse(dialog.oracle_hint.isHidden())
            self.assertEqual(dialog.database_label.text(), "SID/服务名")
        finally:
            dialog.close()

    def test_oceanbase_switching_mode_preserves_custom_port(self):
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={"name": "OB 自定义端口测试", "dialect": "oceanbase", "host": "10.0.0.1", "port": 33306, "mode": "oracle"},
        )
        try:
            self.assertEqual(dialog.port.text(), "33306")
            mysql_idx = dialog.mode.findData("mysql")
            dialog.mode.setCurrentIndex(mysql_idx)
            # 自定义端口不会被模式切换覆盖
            self.assertEqual(dialog.port.text(), "33306")
        finally:
            dialog.close()

    def test_redis_cluster_payload_keeps_seed_nodes_and_auth_mode(self):
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={
                "name": "cluster",
                "dialect": "redis",
                "mode": "cluster",
                "host": "10.128.24.52",
                "port": 47005,
                "username": "app",
                "password": "enc",
            },
            locked_dialect="redis",
        )
        try:
            self.assertEqual(dialog.mode.currentData(), "cluster")
            self.assertFalse(dialog.seed_host.isHidden())
            self.assertTrue(dialog.host.isHidden())
            self.assertEqual(dialog.auth_mode.currentData(), "acl")
            item, _ = dialog.payload()
            self.assertEqual(item["mode"], "cluster")
            self.assertEqual(item["auth_mode"], "acl")
            self.assertGreaterEqual(len(item["seed_nodes"]), 1)
            self.assertEqual(item["seed_nodes"][0]["host"], "10.128.24.52")
            self.assertEqual(item["seed_nodes"][0]["port"], 47005)
        finally:
            dialog.close()

    def test_redis_cluster_rejects_invalid_port(self):
        from tools.db_connect import DbError
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={"dialect": "redis", "mode": "cluster", "host": "10.0.0.1", "port": 6379},
            locked_dialect="redis",
        )
        try:
            dialog._seed_rows[0][1].setText("70000")
            with self.assertRaises(DbError):
                dialog.payload()
        finally:
            dialog.close()

    def test_mongodb_uri_hides_port_and_keeps_uri(self):
        from ui.connection_dialog import ConnectionDialog

        uri = "mongodb://u:p@10.0.0.1:27017/db?authSource=admin"
        dialog = ConnectionDialog(
            language="zh",
            item={"dialect": "mongodb", "host": uri, "database": "biz"},
            locked_dialect="mongodb",
        )
        try:
            self.assertTrue(dialog.port.isHidden())
            self.assertFalse(dialog.mongo_uri_hint.isHidden())
            item, _ = dialog.payload()
            self.assertEqual(item["host"], uri)
            self.assertIn("auth_source", item)
        finally:
            dialog.close()

    def test_mongodb_explicit_auth_fields_in_payload(self):
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={"dialect": "mongodb", "host": "10.0.0.8", "port": 27017, "database": "biz"},
            locked_dialect="mongodb",
        )
        try:
            dialog.mongo_auth_source.setText("admin")
            idx = dialog.mongo_auth_mech.findData("SCRAM-SHA-256")
            dialog.mongo_auth_mech.setCurrentIndex(idx)
            item, _ = dialog.payload()
            self.assertEqual(item["auth_source"], "admin")
            self.assertEqual(item["auth_mechanism"], "SCRAM-SHA-256")
        finally:
            dialog.close()

    def test_mongodb_cluster_multi_seed_and_replica_set_payload(self):
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={
                "dialect": "mongodb",
                "mode": "cluster",
                "seed_nodes": [
                    {"host": "192.168.1.10", "port": 27017},
                    {"host": "192.168.1.11", "port": 27017},
                ],
                "database": "admin",
                "replica_set_name": "rs-prod",
            },
            locked_dialect="mongodb",
        )
        try:
            self.assertFalse(dialog.seed_host.isHidden())
            self.assertTrue(dialog.host.isHidden())
            self.assertFalse(dialog.mongo_replica_set.isHidden())
            self.assertEqual(dialog.mongo_replica_set.text(), "rs-prod")
            item, _ = dialog.payload()
            self.assertEqual(item["mode"], "cluster")
            self.assertEqual(len(item["seed_nodes"]), 2)
            self.assertEqual(item["seed_nodes"][0]["host"], "192.168.1.10")
            self.assertEqual(item["seed_nodes"][1]["host"], "192.168.1.11")
            self.assertEqual(item["replica_set_name"], "rs-prod")
        finally:
            dialog.close()

    def test_mongodb_seed_default_port_and_error_label(self):
        from tools.db_connect import DbError
        from ui.connection_dialog import ConnectionDialog

        dialog = ConnectionDialog(
            language="zh",
            item={"dialect": "mongodb", "mode": "cluster", "host": "10.0.0.1", "port": 27017},
            locked_dialect="mongodb",
        )
        try:
            # 1. Mongo cluster row port 清空 -> default to 27017, not 6379
            dialog._seed_rows[0][1].setText("")
            seeds = dialog._collect_seed_nodes()
            self.assertEqual(seeds[0]["port"], 27017)

            # 2. 错误提示按 dialect 输出 "MongoDB 端口无效"
            dialog._seed_rows[0][1].setText("99999")
            with self.assertRaises(DbError) as ctx:
                dialog._collect_seed_nodes()
            self.assertIn("MongoDB 端口无效", str(ctx.exception))
            self.assertNotIn("Redis 端口无效", str(ctx.exception))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()

