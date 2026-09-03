# -*- coding: utf-8 -*-
import types
import unittest
from unittest.mock import MagicMock, patch

from tools.db_connect import (
    DbError,
    OceanBaseOdbcConnection,
    OceanBaseOdbcCursor,
    escape_odbc_value,
    oceanbase_odbc_driver_status,
    oceanbase_oracle_provider_status,
    open_connection,
    probe_connection,
    resolve_db_provider,
    translate_numeric_binds,
    _oceanbase_oracle_error_message,
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

    # ── Requirement 22 Regressions A ~ O ──

    def test_regression_a_pyodbc_missing_raises_pyodbc_required(self):
        """A. pyodbc 缺失 -> 抛出 [PYODBC_REQUIRED]"""
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "OBORCL",
            "username": "user",
        }
        with patch.dict("sys.modules", {"pyodbc": None}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="pw")
            self.assertIn("[PYODBC_REQUIRED]", str(ctx.exception))

    def test_regression_b_driver_missing_raises_odbc_driver_required(self):
        """B. pyodbc 存在但缺少 OceanBase 驱动 -> 抛出 [ODBC_DRIVER_REQUIRED]"""
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "OBORCL",
            "username": "user",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["SQL Server", "MySQL ODBC Driver"]
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="pw")
            self.assertIn("[ODBC_DRIVER_REQUIRED]", str(ctx.exception))
            self.assertIn("SQL Server", str(ctx.exception))

    def test_regression_c_driver_selection_exact_match(self):
        """C. 驱动选择首选精确匹配 OceanBase ODBC 2.0 Driver"""
        drivers = ["OceanBase ODBC 1.0 Driver", "OceanBase ODBC 2.0 Driver", "Other ODBC Driver"]
        selected, all_d = oceanbase_odbc_driver_status(drivers)
        self.assertEqual(selected, "OceanBase ODBC 2.0 Driver")

    def test_regression_d_ambiguous_candidates_raises_deterministic_diagnostic(self):
        """D. 多个模糊候选且无精确匹配时 -> 明确报错 [AMBIGUOUS_ODBC_DRIVER] 严禁随机挑选"""
        drivers = ["OceanBase Custom ODBC Driver A", "OceanBase Custom ODBC Driver B"]
        with self.assertRaises(DbError) as ctx:
            oceanbase_odbc_driver_status(drivers)
        self.assertIn("[AMBIGUOUS_ODBC_DRIVER]", str(ctx.exception))

    def test_regression_e_connection_string_special_chars_escaping(self):
        """E. 连接串对特殊字符（分号、花括号、等号、空格）进行标准 ODBC 安全转义"""
        pw = "abc;123}xyz"
        escaped_pw = escape_odbc_value(pw)
        self.assertEqual(escaped_pw, "{abc;123}}xyz}")

        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "ob;db",
            "username": "user;tenant}cluster",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        captured_conn_str = []

        def fake_connect(conn_str, **kwargs):
            captured_conn_str.append(conn_str)
            return MagicMock()

        fake_pyodbc.connect.side_effect = fake_connect

        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            open_connection(item, plain_password=pw)

        self.assertEqual(len(captured_conn_str), 1)
        conn_str = captured_conn_str[0]
        self.assertIn("Driver={OceanBase ODBC 2.0 Driver}", conn_str)
        self.assertIn("Password={abc;123}}xyz}", conn_str)
        self.assertIn("Database={ob;db}", conn_str)
        self.assertIn("User={user;tenant}}cluster}", conn_str)
        self.assertIn("Option=3", conn_str)

    def test_regression_f_password_never_appears_in_dberror(self):
        """F. 发生连接异常时，明文密码绝不出现在的 DbError 中"""
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "database": "OB",
            "username": "user",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        fake_pyodbc.connect.side_effect = Exception("[08001] Could not connect to host with secret_password_999")

        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="secret_password_999")
            self.assertNotIn("secret_password_999", str(ctx.exception))

    def test_regression_g_numeric_bind_single(self):
        """G. :1 -> ? 转换"""
        sql, params = translate_numeric_binds("SELECT * FROM t WHERE id = :1", [123])
        self.assertEqual(sql, "SELECT * FROM t WHERE id = ?")
        self.assertEqual(params, (123,))

    def test_regression_h_numeric_bind_multiple(self):
        """H. :1/:2 -> ?/? 转换与保序"""
        sql, params = translate_numeric_binds("WHERE a = :1 AND b = :2", ["A", "B"])
        self.assertEqual(sql, "WHERE a = ? AND b = ?")
        self.assertEqual(params, ("A", "B"))

    def test_regression_i_repeated_numeric_bind(self):
        """I. 重复的 :1 正确复制对应参数"""
        sql, params = translate_numeric_binds("WHERE x = :1 OR y = :1", ["val"])
        self.assertEqual(sql, "WHERE x = ? OR y = ?")
        self.assertEqual(params, ("val", "val"))

    def test_regression_j_qmark_sql_remains_unchanged(self):
        """J. 已是 qmark 的 SQL 保持不变，不产生二次改写"""
        sql, params = translate_numeric_binds("WHERE a = ? AND b = ?", [1, 2])
        self.assertEqual(sql, "WHERE a = ? AND b = ?")
        self.assertEqual(params, [1, 2])

    def test_regression_k_probe_connection_production_operations(self):
        """K. probe_connection 必须执行整条业务验证链，且若失败直接抛错不吞"""
        executed = []
        mock_raw_cursor = MagicMock()

        def fake_execute(sql, params=None):
            executed.append(sql)

        mock_raw_cursor.execute.side_effect = fake_execute
        mock_raw_cursor.fetchone.side_effect = [
            (1,),
            ("TEST_SCHEMA", "TEST_USER"),
            (42,),
        ]
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_raw_cursor
        fake_conn = OceanBaseOdbcConnection(mock_raw_conn, driver_name="OceanBase ODBC 2.0 Driver")

        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "OB_SERVICE",
            "username": "test_user",
        }
        with patch("tools.db_connect.open_connection", return_value=fake_conn):
            res = probe_connection(item, plain_password="clear_password")

        self.assertTrue(res["ok"])
        self.assertIn("SELECT 1 FROM DUAL", executed)
        self.assertTrue(any("SYS_CONTEXT" in s for s in executed))
        self.assertTrue(any("USER_TABLES" in s for s in executed))
        self.assertIn("Provider: OceanBase ODBC", res["summary"])
        self.assertIn("Driver: OceanBase ODBC 2.0 Driver", res["summary"])
        self.assertNotIn("clear_password", res["summary"])

        # 验证若生产操作失败，probe 必须抛异常失败：
        mock_raw_cursor.execute.side_effect = [None, None, Exception("USER_TABLES query failed")]
        mock_raw_cursor.fetchone.side_effect = [(1,), ("SCHEMA", "USER")]
        with patch("tools.db_connect.open_connection", return_value=fake_conn):
            with self.assertRaises(DbError):
                probe_connection(item, plain_password="clear_password")

    def test_regression_l_scan_schema_production_path_through_adapter(self):
        """L. scan_schema 生产路径通过 adapter 正确执行，内部 :1 成功转换为 ?"""
        from tools.schema_snapshot import scan_schema
        executed_calls = []
        mock_raw_cursor = MagicMock()

        def fake_exec(sql, params=None):
            executed_calls.append((sql, params))

        mock_raw_cursor.execute.side_effect = fake_exec
        mock_raw_cursor.fetchall.return_value = []
        mock_raw_cursor.fetchone.return_value = ("APP",)
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_raw_cursor
        odbc_conn = OceanBaseOdbcConnection(mock_raw_conn, driver_name="OceanBase ODBC 2.0 Driver")

        item = {"dialect": "oceanbase", "mode": "oracle", "database": "OB", "username": "APP", "schema": "APP"}
        payload = scan_schema(odbc_conn, item)
        self.assertIn("objects", payload)
        converted_calls = [c for c in executed_calls if "?" in c[0]]
        self.assertTrue(len(converted_calls) > 0)
        for sql, p in executed_calls:
            self.assertNotIn(":1", sql)

    def test_regression_m_list_columns_production_path_through_adapter(self):
        """M. list_columns 生产路径通过 adapter 正确转换 :1/:2 并返回字段清单"""
        from tools.db_connect import list_columns
        executed_calls = []
        mock_raw_cursor = MagicMock()

        def fake_exec(sql, params=None):
            executed_calls.append((sql, params))

        mock_raw_cursor.execute.side_effect = fake_exec
        mock_raw_cursor.fetchall.return_value = [("ID",), ("NAME",)]
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_raw_cursor
        odbc_conn = OceanBaseOdbcConnection(mock_raw_conn)

        # 1. 普通表名
        cols = list_columns(odbc_conn, "oceanbase", "USERS")
        self.assertEqual(cols, ["ID", "NAME"])
        sql, params = executed_calls[-1]
        self.assertIn("WHERE table_name = ?", sql)
        self.assertEqual(params, ("USERS",))

        # 2. 带 Owner 的表名
        cols2 = list_columns(odbc_conn, "oceanbase", "HR.EMPLOYEES")
        self.assertEqual(cols2, ["ID", "NAME"])
        sql2, params2 = executed_calls[-1]
        self.assertIn("WHERE owner = ? AND table_name = ?", sql2)
        self.assertEqual(params2, ("HR", "EMPLOYEES"))

    def test_regression_n_ordinary_oracle_unaffected(self):
        """N. 普通 Oracle 严禁改用 ODBC，仍然调用 _connect_oracle / oracledb"""
        item = {"dialect": "oracle", "host": "192.168.1.10", "port": 1521, "database": "ORCL", "username": "scott"}
        fake_pyodbc = MagicMock()
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with patch("tools.db_connect._connect_oracle") as mock_oracle:
                open_connection(item, plain_password="tiger")
                mock_oracle.assert_called_once()
                fake_pyodbc.connect.assert_not_called()

    def test_regression_o_oceanbase_mysql_remains_pymysql(self):
        """O. OceanBase MySQL 模式保持原有 pymysql 路径，不走 pyodbc"""
        item = {"dialect": "oceanbase", "mode": "mysql", "host": "10.0.0.1", "port": 2883, "database": "test", "username": "root"}
        fake_pyodbc = MagicMock()
        fake_pymysql = MagicMock()
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc, "pymysql": fake_pymysql}):
            open_connection(item, plain_password="pw")
            fake_pymysql.connect.assert_called_once()
            fake_pyodbc.connect.assert_not_called()

    # ── Additional Query Execution Compatibility (Section 17) ──

    def test_query_execution_select_and_dml_transactions(self):
        """17. 查询与 DML 事务执行兼容性：SELECT 正常解析，UPDATE 提交，UPDATE 异常回滚"""
        from tools.db_connect import run_read_query, run_console_statement
        mock_raw_cursor = MagicMock()
        mock_raw_cursor.description = [("ID", 2, None, None, None, None, None), ("NAME", 1, None, None, None, None, None)]
        mock_raw_cursor.fetchall.return_value = [(1, "Alice"), (2, "Bob")]
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_raw_cursor
        odbc_conn = OceanBaseOdbcConnection(mock_raw_conn)

        # 1. SELECT
        res = run_read_query(odbc_conn, "oceanbase", "SELECT * FROM users")
        self.assertEqual(res["columns"], ["ID", "NAME"])
        self.assertEqual(res["rows"], [["1", "Alice"], ["2", "Bob"]])

        # 2. UPDATE commit
        mock_raw_cursor.description = None
        mock_raw_cursor.rowcount = 1
        run_console_statement(odbc_conn, "oceanbase", "UPDATE users SET name='Charlie' WHERE id=1")
        mock_raw_conn.commit.assert_called_once()

        # 3. UPDATE error rollback
        mock_raw_cursor.execute.side_effect = Exception("DML constraint violation")
        with self.assertRaises(DbError):
            run_console_statement(odbc_conn, "oceanbase", "UPDATE users SET name='Charlie' WHERE id=1")
        mock_raw_conn.rollback.assert_called_once()

    def test_error_classification_sqlstates(self):
        """13. ODBC SQLSTATE 错误分类解析验证"""
        item = {"host": "10.0.0.1", "port": 2883, "database": "OB", "username": "user"}

        # 28000 -> AUTH_ERROR
        e1 = Exception("('28000', '[28000] [OceanBase][ODBC 2.0 Driver]Invalid authorization specification')")
        m1 = _oceanbase_oracle_error_message(e1, item)
        self.assertIn("[AUTH_ERROR]", m1)

        # 08001 -> NETWORK / CONNECTION_ERROR
        e2 = Exception("('08001', '[08001] [OceanBase][ODBC 2.0 Driver]Could not connect to server')")
        m2 = _oceanbase_oracle_error_message(e2, item)
        self.assertIn("[NETWORK / CONNECTION_ERROR]", m2)

        # HYT00 -> TIMEOUT
        e3 = Exception("('HYT00', '[HYT00] Timeout expired')")
        m3 = _oceanbase_oracle_error_message(e3, item)
        self.assertIn("[TIMEOUT]", m3)

        # ORA-12569 -> TRANSPORT / TNS PACKET INTEGRITY
        e4 = Exception("ORA-12569: TNS:packet checksum failure")
        m4 = _oceanbase_oracle_error_message(e4, item)
        self.assertIn("[TRANSPORT / TNS PACKET INTEGRITY]", m4)

    def test_oceanbase_oracle_provider_status_helper(self):
        """19. oceanbase_oracle_provider_status 诊断 helper 契约验证"""
        status = oceanbase_oracle_provider_status()
        self.assertEqual(status["provider"], "odbc")
        self.assertTrue(status["pyodbc_available"])
        self.assertEqual(status["pyodbc_version"], "5.3.0")
        # 当前开发机未安装真实系统驱动
        self.assertFalse(status["driver_available"])
        self.assertFalse(status["ready"])


if __name__ == "__main__":
    unittest.main()
