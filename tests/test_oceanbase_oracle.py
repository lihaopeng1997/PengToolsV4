# -*- coding: utf-8 -*-
import types
import unittest
from unittest.mock import MagicMock, patch

from tools.db_connect import (
    DbError,
    _oceanbase_oracle_error_message,
    open_connection,
    probe_connection,
    resolve_db_provider,
)


class OceanBaseOracleContractTests(unittest.TestCase):

    def test_resolve_db_provider_explicit_separation(self):
        """显式拆分 oceanbase_oracle 与 oracle，绝不在同一模糊分支。"""
        self.assertEqual(
            resolve_db_provider({"dialect": "oceanbase", "mode": "oracle"}),
            "oceanbase_oracle"
        )
        self.assertEqual(
            resolve_db_provider({"dialect": "oceanbase", "mode": "mysql"}),
            "oceanbase_mysql"
        )
        self.assertEqual(
            resolve_db_provider({"dialect": "oracle"}),
            "oracle"
        )
        self.assertEqual(
            resolve_db_provider({"dialect": "mysql"}),
            "mysql"
        )

    def test_oceanbase_oracle_blocked_dependency_status(self):
        """当前 Windows 运行环境缺少验证的 OBCI 驱动，状态明确为 BLOCKED_DEPENDENCY，坚决不误导用户。"""
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "192.168.1.50",
            "port": 2883,
            "database": "OBORCL",
            "username": "app@tenant#cluster",
        }
        with self.assertRaises(DbError) as ctx:
            open_connection(item, plain_password="my_secret_password")

        err = str(ctx.exception)
        self.assertIn("[BLOCKED_DEPENDENCY]", err)
        self.assertIn("192.168.1.50:2883/OBORCL", err)
        # 绝不泄漏密码
        self.assertNotIn("my_secret_password", err)
        # 不得声称配置 oci.dll/libobclient.dll 即可连接
        self.assertNotIn("配置说明：\n1. 请打开「设置", err)

    def test_ora_12569_packet_checksum_classified_correctly(self):
        """ORA-12569 错误分类为 TRANSPORT / TNS PACKET INTEGRITY，不归为密码错误。"""
        class Ora12569Error(Exception):
            pass

        err_exc = Ora12569Error("ORA-12569: TNS:packet checksum failure")
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.10.10.1",
            "port": 2883,
            "database": "DB1",
            "username": "user1",
        }
        err_msg = _oceanbase_oracle_error_message(err_exc, item)
        self.assertIn("[TRANSPORT / TNS PACKET INTEGRITY]", err_msg)
        self.assertIn("ORA-12569", err_msg)
        self.assertIn("10.10.10.1:2883/DB1", err_msg)

    def test_oceanbase_oracle_probe_minimum_business_chain(self):
        """生产测试链：connect -> SELECT 1 FROM DUAL -> current schema -> metadata query。"""
        executed_sqls = []

        class MockCursor:
            def execute(self, sql):
                executed_sqls.append(sql)

            def fetchone(self):
                if "SYS_CONTEXT" in executed_sqls[-1]:
                    return ("MY_SCHEMA", "MY_USER")
                if "USER_TABLES" in executed_sqls[-1]:
                    return (42,)
                return (1,)

            def close(self):
                pass

        class MockConn:
            def cursor(self):
                return MockCursor()

            def close(self):
                pass

        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "OB_SRV",
            "username": "admin@tenant",
        }
        with patch("tools.db_connect.open_connection", return_value=MockConn()):
            res = probe_connection(item, plain_password="clear_password")

        self.assertTrue(res.get("ok"))
        self.assertIn("SELECT 1 FROM DUAL", executed_sqls)
        self.assertTrue(any("SYS_CONTEXT" in sql for sql in executed_sqls))
        self.assertTrue(any("USER_TABLES" in sql for sql in executed_sqls))

        summary = res.get("summary") or ""
        self.assertIn("OceanBase (Oracle 模式)", summary)
        self.assertIn("MY_SCHEMA", summary)
        self.assertIn("42", summary)
        self.assertNotIn("clear_password", summary)

    def test_standard_oracle_unaffected(self):
        """普通 Oracle 行为不得改坏，仍然通过 _connect_oracle 与 load_oracle_paths 初始化。"""
        captured = {}

        def mock_connect(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        fake_oracledb = types.SimpleNamespace(
            connect=mock_connect,
        )
        item = {
            "dialect": "oracle",
            "host": "192.168.2.100",
            "port": 1521,
            "database": "ORCL",
            "username": "scott",
        }
        with patch.dict("sys.modules", {"oracledb": fake_oracledb}):
            with patch("tools.db_connect.ensure_oracle_client", return_value={"mode": "thin"}):
                conn = open_connection(item, plain_password="tiger")

        self.assertIsNotNone(conn)
        self.assertEqual(captured["user"], "scott")
        self.assertEqual(captured["password"], "tiger")
        self.assertEqual(captured["dsn"], "192.168.2.100:1521/ORCL")


if __name__ == "__main__":
    unittest.main()
