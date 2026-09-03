# -*- coding: utf-8 -*-
import types
import unittest
from unittest.mock import MagicMock, patch

from tools.db_connect import (
    DbError,
    open_connection,
    probe_connection,
    resolve_db_provider,
)


class OceanBaseOracleContractTests(unittest.TestCase):

    def test_resolve_db_provider_explicit_separation(self):
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

    def test_oceanbase_oracle_blocks_thin_mode_with_diagnostic(self):
        fake_oracledb = types.SimpleNamespace(
            is_thin_mode=lambda: True,
            connect=MagicMock(),
        )
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "192.168.1.50",
            "port": 2883,
            "database": "OBORCL",
            "username": "app@tenant#cluster",
        }
        with patch.dict("sys.modules", {"oracledb": fake_oracledb}):
            with patch("tools.db_connect.ensure_oracle_client", return_value={"mode": "thin"}):
                with self.assertRaises(DbError) as ctx:
                    open_connection(item, plain_password="my_secret_password")

        err = str(ctx.exception)
        self.assertIn("[CLIENT_LIBRARY_REQUIRED]", err)
        self.assertIn("Thick", err)
        self.assertIn("ORA-12569", err)
        self.assertIn("192.168.1.50:2883/OBORCL", err)
        self.assertNotIn("my_secret_password", err)
        fake_oracledb.connect.assert_not_called()

    def test_oceanbase_oracle_thick_mode_connects_properly(self):
        captured = {}

        def mock_connect(**kwargs):
            captured.update(kwargs)
            mock_conn = MagicMock()
            return mock_conn

        fake_oracledb = types.SimpleNamespace(
            is_thin_mode=lambda: False,
            connect=mock_connect,
        )
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.20.30.40",
            "port": 2883,
            "database": "MY_SERVICE",
            "username": "usr@obtenant",
        }
        with patch.dict("sys.modules", {"oracledb": fake_oracledb}):
            with patch("tools.db_connect.ensure_oracle_client", return_value={"mode": "thick"}):
                conn = open_connection(item, plain_password="pw123")

        self.assertIsNotNone(conn)
        self.assertEqual(captured["user"], "usr@obtenant")
        self.assertEqual(captured["password"], "pw123")
        self.assertEqual(captured["dsn"], "10.20.30.40:2883/MY_SERVICE")

    def test_ora_12569_packet_checksum_classified_correctly(self):
        class Ora12569Error(Exception):
            pass

        def raise_ora():
            raise Ora12569Error("ORA-12569: TNS:packet checksum failure")

        fake_oracledb = types.SimpleNamespace(
            is_thin_mode=lambda: False,
            connect=lambda **kw: raise_ora(),
        )
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.10.10.1",
            "port": 2883,
            "database": "DB1",
            "username": "user1",
        }
        with patch.dict("sys.modules", {"oracledb": fake_oracledb}):
            with patch("tools.db_connect.ensure_oracle_client", return_value={"mode": "thick"}):
                with self.assertRaises(DbError) as ctx:
                    open_connection(item, plain_password="secret_pwd_999")

        err = str(ctx.exception)
        self.assertIn("[TRANSPORT / TNS PACKET INTEGRITY]", err)
        self.assertIn("ORA-12569", err)
        self.assertNotIn("secret_pwd_999", err)

    def test_oceanbase_oracle_probe_minimum_business_chain(self):
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

        fake_oracledb = types.SimpleNamespace(
            is_thin_mode=lambda: False,
            connect=lambda **kw: MockConn(),
        )
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "OB_SRV",
            "username": "admin@tenant",
        }
        with patch.dict("sys.modules", {"oracledb": fake_oracledb}):
            with patch("tools.db_connect.ensure_oracle_client", return_value={"mode": "thick"}):
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


if __name__ == "__main__":
    unittest.main()
