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
            "oceanbase_oracle_provider": "odbc",
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
            "oceanbase_oracle_provider": "odbc",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["SQL Server", "MySQL ODBC Driver"]
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="pw")
            self.assertIn("[ODBC_DRIVER_REQUIRED]", str(ctx.exception))
            self.assertIn("OceanBase ODBC 2.0 Driver", str(ctx.exception))
            self.assertNotIn("SQL Server", str(ctx.exception))

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
            "oceanbase_oracle_provider": "odbc",
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
        """F. 发生连接异常或 ODBC 兜底错误时，明文密码与转义密码绝不出现在的 DbError 中"""
        pw = "abc;123}xyz"
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "database": "OB",
            "username": "user",
            "oceanbase_oracle_provider": "odbc",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        fake_pyodbc.connect.side_effect = Exception(f"[HY000] driver failed; Password={{{pw.replace('}', '}}')}}}; detail info")

        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password=pw)
            err_msg = str(ctx.exception)
            self.assertNotIn("abc;123}xyz", err_msg)
            self.assertNotIn("abc", err_msg)
            self.assertNotIn("123}}xyz", err_msg)

    # ── Section 9: Legacy Config Schema Confirmation Gate Tests ──

    def test_regression_legacy_missing_marker_blocked(self):
        """9A. 旧版本 OceanBase Oracle 配置（无 odbc marker）必须拒绝连接，不得调用 pyodbc.connect"""
        item = {
            "dialect": "oceanbase",
            "mode": "oracle",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "ORCL",
            "username": "user",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="pw")
            self.assertIn("[ODBC_SCHEMA_CONFIRM_REQUIRED]", str(ctx.exception))
            fake_pyodbc.connect.assert_not_called()

    def test_regression_legacy_missing_mode_blocked(self):
        """9B. 旧版本未配置 mode 的 OceanBase（默认 oracle 模式）若无 marker 同样拒绝连接"""
        item = {
            "dialect": "oceanbase",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "ORCL",
            "username": "user",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="pw")
            self.assertIn("[ODBC_SCHEMA_CONFIRM_REQUIRED]", str(ctx.exception))
            fake_pyodbc.connect.assert_not_called()

    def test_regression_legacy_standalone_mode_blocked(self):
        """9B-2. 旧版本 mode=standalone 的 OceanBase 无 marker 同样拒绝连接"""
        item = {
            "dialect": "oceanbase",
            "mode": "standalone",
            "host": "10.0.0.1",
            "port": 2883,
            "database": "ORCL",
            "username": "user",
        }
        fake_pyodbc = MagicMock()
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="pw")
            self.assertIn("[ODBC_SCHEMA_CONFIRM_REQUIRED]", str(ctx.exception))
            fake_pyodbc.connect.assert_not_called()

    def test_regression_g_numeric_bind_single(self):
        """G. :1 -> ? 转换"""
        sql, params = translate_numeric_binds("SELECT * FROM t WHERE id = :1", [123])
        self.assertEqual(sql, "SELECT * FROM t WHERE id = ?")
        self.assertEqual(params, (123,))

    def test_regression_h_numeric_bind_multiple(self):
        """H. :1/:2 -> ?/? 顺序绑定"""
        sql, params = translate_numeric_binds("WHERE a = :1 AND b = :2", ["A", "B"])
        self.assertEqual(sql, "WHERE a = ? AND b = ?")
        self.assertEqual(params, ("A", "B"))

    def test_regression_i_positional_bind_semantics(self):
        """I. Positional bind: placeholder 顺序决定参数，标签数字不决定下标；重复 placeholder 消耗对应参数"""
        # A. :1,:2 + [A,B] -> (?,?) + (A,B)
        sA, pA = translate_numeric_binds("WHERE a=:1 AND b=:2", ["A", "B"])
        self.assertEqual(sA, "WHERE a=? AND b=?")
        self.assertEqual(pA, ("A", "B"))

        # B. :2,:1 + [A,B] -> (?,?) + (A,B) (位置绑定优先于标签数字)
        sB, pB = translate_numeric_binds("WHERE a=:2 AND b=:1", ["A", "B"])
        self.assertEqual(sB, "WHERE a=? AND b=?")
        self.assertEqual(pB, ("A", "B"))

        # C. :1,:1 + [A,B] -> (?,?) + (A,B) (各消耗一个位置参数)
        sC, pC = translate_numeric_binds("WHERE x=:1 OR y=:1", ["A", "B"])
        self.assertEqual(sC, "WHERE x=? OR y=?")
        self.assertEqual(pC, ("A", "B"))

    def test_regression_j_qmark_and_quotes(self):
        """J. qmark 原样透传；引文字符串内部的 ':1' 绝不误改写"""
        sD, pD = translate_numeric_binds("WHERE a = ? AND b = ?", [1, 2])
        self.assertEqual(sD, "WHERE a = ? AND b = ?")
        self.assertEqual(pD, [1, 2])

        sE, pE = translate_numeric_binds("SELECT ':1 not bind' AS c FROM t WHERE x = :1", [99])
        self.assertEqual(sE, "SELECT ':1 not bind' AS c FROM t WHERE x = ?")
        self.assertEqual(pE, (99,))

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
            "oceanbase_oracle_provider": "odbc",
        }
        with patch("tools.db_connect.open_connection", return_value=fake_conn):
            res = probe_connection(item, plain_password="clear_password")

        self.assertTrue(res["ok"])
        self.assertIn("SELECT 1 FROM DUAL", executed)
        self.assertTrue(any("SYS_CONTEXT" in s for s in executed))
        self.assertTrue(any("USER_TABLES" in s for s in executed))
        self.assertIn("Provider: OceanBase ODBC", res["summary"])
        self.assertIn("Driver: OceanBase ODBC 2.0 Driver", res["summary"])
        self.assertIn("目标 Schema：OB_SERVICE", res["summary"])
        self.assertNotIn("clear_password", res["summary"])

        # 验证若生产操作失败，probe 必须抛异常失败：
        mock_raw_cursor.execute.side_effect = [None, None, Exception("USER_TABLES query failed")]
        mock_raw_cursor.fetchone.side_effect = [(1,), ("SCHEMA", "USER")]
        with patch("tools.db_connect.open_connection", return_value=fake_conn):
            with self.assertRaises(DbError):
                probe_connection(item, plain_password="clear_password")

    def test_regression_l_scan_schema_production_path_through_adapter(self):
        """L. scan_schema 生产真实已确认 item (含 marker): database 作为 schema 正确转换查询"""
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

        # 真实已确认 item shape：database="APP"，标记 oceanbase_oracle_provider="odbc"
        item = {"dialect": "oceanbase", "mode": "oracle", "database": "APP", "username": "APP", "oceanbase_oracle_provider": "odbc"}
        payload = scan_schema(odbc_conn, item)
        self.assertEqual(payload.get("status"), "ok")

        # 验证 all_tab_comments 查询：WHERE owner = ? 带 params: ("APP",)
        comments_calls = [c for c in executed_calls if "all_tab_comments" in c[0].lower()]
        self.assertTrue(len(comments_calls) > 0)
        self.assertIn("WHERE table_type IN ('TABLE', 'VIEW') AND owner = ?", comments_calls[0][0])
        self.assertEqual(comments_calls[0][1], ("APP",))

        # 验证 all_tab_columns 查询：WHERE col.owner = ? 带 params: ("APP",)
        columns_calls = [c for c in executed_calls if "all_tab_columns" in c[0].lower()]
        self.assertTrue(len(columns_calls) > 0)
        self.assertIn("WHERE col.owner = ?", columns_calls[0][0])
        self.assertEqual(columns_calls[0][1], ("APP",))

        # 确保没有 :1 传给底层 pyodbc
        for sql, _ in executed_calls:
            self.assertNotIn(":1", sql)

        # 同时验证 native Oracle 保持 database 绝不能当成 schema
        executed_oracle_calls = []
        mock_raw_cursor.execute.side_effect = lambda sql, params=None: executed_oracle_calls.append((sql, params))
        item_oracle = {"dialect": "oracle", "database": "ORCL", "username": "SCOTT"}
        scan_schema(odbc_conn, item_oracle)
        oracle_comments = [c for c in executed_oracle_calls if "all_tab_comments" in c[0].lower()]
        self.assertTrue(len(oracle_comments) > 0)
        # 绝不把 ORCL 当成 WHERE owner = ?
        self.assertIn("owner NOT IN", oracle_comments[0][0])

    def test_regression_scan_schema_legacy_direct_call_blocked(self):
        """9G. 未经确认的旧版 OceanBase Oracle 配置调用 scan_schema 必须返回 status=failed 且不得执行 cursor.execute"""
        from tools.schema_snapshot import scan_schema
        mock_raw_cursor = MagicMock()
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_raw_cursor
        odbc_conn = OceanBaseOdbcConnection(mock_raw_conn, driver_name="OceanBase ODBC 2.0 Driver")

        item = {"dialect": "oceanbase", "mode": "oracle", "database": "ORCL", "username": "APP"}
        payload = scan_schema(odbc_conn, item)
        self.assertEqual(payload.get("status"), "failed")
        self.assertIn("ODBC_SCHEMA_CONFIRM_REQUIRED", payload.get("warning", ""))
        mock_raw_cursor.execute.assert_not_called()

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
        """13. ODBC SQLSTATE 错误分类解析验证，包括 ORA-12569 优先于 08S01 状态码"""
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

        # ORA-12569 带 08S01 状态码，必须优先识别为 TRANSPORT / TNS PACKET INTEGRITY
        class CustomOdbcError(Exception):
            pass
        e4 = CustomOdbcError("08S01", "[08S01] [OceanBase][ODBC] ORA-12569: TNS:packet checksum failure")
        m4 = _oceanbase_oracle_error_message(e4, item)
        self.assertIn("[TRANSPORT / TNS PACKET INTEGRITY]", m4)
        self.assertNotIn("[NETWORK / CONNECTION_ERROR]", m4)

    def test_oceanbase_oracle_provider_status_helper_env_independent(self):
        """19. oceanbase_oracle_provider_status 诊断 helper 单元测试必须环境无关"""
        fake_pyodbc = MagicMock()

        # Case A: drivers=[] -> ready=False
        fake_pyodbc.drivers.return_value = []
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            st_a = oceanbase_oracle_provider_status()
            self.assertFalse(st_a["ready"])
            self.assertFalse(st_a["driver_available"])
            self.assertEqual(st_a["status_code"], "ODBC_DRIVER_REQUIRED")

        # Case B: drivers=["OceanBase ODBC 2.0 Driver"] -> ready=True
        fake_pyodbc.drivers.return_value = ["OceanBase ODBC 2.0 Driver"]
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            st_b = oceanbase_oracle_provider_status()
            self.assertTrue(st_b["ready"])
            self.assertTrue(st_b["driver_available"])
            self.assertEqual(st_b["driver"], "OceanBase ODBC 2.0 Driver")
            self.assertEqual(st_b["status_code"], "READY")

        # Case C: 多个模糊候选（均包含 oceanbase 与 odbc）-> 触发 production 歧义安全拒绝
        drivers_c = ["OceanBase Custom ODBC Driver A", "OceanBase Custom ODBC Driver B"]
        fake_pyodbc.drivers.return_value = drivers_c
        with patch.dict("sys.modules", {"pyodbc": fake_pyodbc}):
            st_c = oceanbase_oracle_provider_status()
            self.assertFalse(st_c["ready"])
            self.assertFalse(st_c["driver_available"])
            self.assertEqual(st_c["status_code"], "AMBIGUOUS_ODBC_DRIVER")
            with self.assertRaises(DbError) as ctx:
                oceanbase_odbc_driver_status(drivers_c)
            self.assertIn("[AMBIGUOUS_ODBC_DRIVER]", str(ctx.exception))

        # 真实环境只验证返回结构键契约，不写死 driver_available 为 False
        st_real = oceanbase_oracle_provider_status()
        self.assertEqual(st_real["provider"], "odbc")
        self.assertIn("ready", st_real)
        self.assertIn("pyodbc_available", st_real)
        self.assertIn("driver_available", st_real)
        self.assertIn("driver", st_real)
        self.assertIn("status_code", st_real)
        self.assertIn("message", st_real)


if __name__ == "__main__":
    unittest.main()
