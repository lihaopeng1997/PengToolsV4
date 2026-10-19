from __future__ import annotations

import threading
import time
import unittest

from tools.data_center.manual_driver import ManualRelationalDriver, classify_relational_statement
from tools.data_center.manual_session import ManualSession, ManualSessionError, ManualSessionState, ManualSessionTarget, StatementKind
from tools.data_center.sql_script import SqlScriptError, selected_procedural_sql


class _Cursor:
    description = None
    rowcount = -1
    def __init__(self, connection):
        self.connection = connection
        self.probe = False
    def execute(self, sql, parameters=None):
        connection = self.connection
        connection.events.append(("execute", threading.get_ident(), sql, parameters))
        self.probe = "DBMS_TRANSACTION.LOCAL_TRANSACTION_ID" in sql
        if self.probe:
            if connection.probe_error:
                raise RuntimeError("synthetic unavailable function")
            return
        if connection.block:
            connection.entered.set()
            connection.release.wait(3)
        if connection.failure:
            raise RuntimeError("synthetic block failure")
        connection.server_status = 1 if connection.active else 0
        if hasattr(connection, "transaction_in_progress"):
            connection.transaction_in_progress = connection.active
    def fetchone(self):
        return ("1.2.3" if self.connection.active else None,)
    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _Connection:
    autocommit = False
    def __init__(self, *, sdk=True, active=True, flags=0, failure=False, probe_error=False, block=False):
        self.client_flag = flags
        self.server_status = 0
        self.active = active
        self.failure = failure
        self.probe_error = probe_error
        self.block = block
        self.entered, self.release = threading.Event(), threading.Event()
        self.events = []
        if sdk:
            self.transaction_in_progress = False
    def cursor(self):
        return _Cursor(self)
    def commit(self):
        self.events.append(("commit", threading.get_ident()))
    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))
    def close(self):
        self.events.append(("close", threading.get_ident()))


class ProceduralTests(unittest.TestCase):
    def _session(self, provider="oracle", **options):
        connections = []
        def factory(target):
            connection = _Connection(**options)
            connections.append(connection)
            return ManualRelationalDriver(connection, provider)
        session = ManualSession(ManualSessionTarget("block", "temp", "rev"), factory, lambda sql: classify_relational_statement(sql, provider))
        def cleanup():
            session.close(disposition="rollback").result(timeout=3)
            deadline = time.monotonic() + 3
            while not session.poll_cleanup().cleanup_complete and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertTrue(session.poll_cleanup().cleanup_complete)
        self.addCleanup(cleanup)
        return session, connections

    def test_client_terminators_normalize_only_outer_lines(self):
        oracle = "DECLARE v VARCHAR2(40) := q'[a;b]'; BEGIN NULL; END;"
        self.assertEqual(selected_procedural_sql(oracle + "\n/", "oracle"), oracle)
        mysql = "CREATE PROCEDURE p() BEGIN SELECT 'a;b'; SELECT 2; END"
        self.assertEqual(selected_procedural_sql("DELIMITER $$\n" + mysql + "$$\nDELIMITER ;", "mysql"), mysql)
        self.assertIsNone(selected_procedural_sql("SELECT 'BEGIN; END;'", "oracle"))
        for text in ("BEGIN", "BEGIN UPDATE t SET v=1;", "DELIMITER $$\nSELECT 1$$\nDELIMITER ;"):
            with self.assertRaises(SqlScriptError):
                selected_procedural_sql(text, "mysql")

    def test_complete_selected_block_executes_once_and_state_comes_from_driver(self):
        for provider in ("oracle", "oceanbase_oracle", "mysql", "oceanbase_mysql"):
            for active in (True, False):
                with self.subTest(provider=provider, active=active):
                    session, connections = self._session(provider, active=active)
                    sql = "BEGIN UPDATE t SET v=1; UPDATE t SET v=2; END;" if provider.endswith("oracle") else "CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END"
                    result = session.execute_block(sql, provider=provider, authorized=True).result(timeout=3)
                    self.assertEqual(result.kind, StatementKind.PROCEDURAL)
                    self.assertEqual(result.state, ManualSessionState.DIRTY if active else ManualSessionState.CLEAN)
                    executions = [event for event in connections[0].events if event[0] == "execute"]
                    self.assertEqual([event[2:] for event in executions], [(sql, None)])
                    self.assertNotEqual(executions[0][1], threading.get_ident())
                    self.assertFalse(any(event[0] in {"commit", "rollback"} for event in connections[0].events))

    def test_odbc_probe_fallback_reports_real_state_or_unknown(self):
        for active, probe_error, expected in ((True, False, ManualSessionState.DIRTY), (False, False, ManualSessionState.CLEAN), (True, True, ManualSessionState.UNKNOWN)):
            session, connections = self._session("oceanbase_oracle", sdk=False, active=active, probe_error=probe_error)
            result = session.execute_block("BEGIN NULL; END;", provider="oceanbase_oracle", authorized=True).result(timeout=3)
            self.assertEqual(result.state, expected)
            self.assertEqual(len([event for event in connections[0].events if event[0] == "execute"]), 2)
            if probe_error:
                with self.assertRaises(ManualSessionError):
                    session.commit().result(timeout=3)

    def test_confirmation_dirty_session_and_multi_statement_capability_are_guarded(self):
        session, connections = self._session()
        with self.assertRaises(ManualSessionError):
            session.execute_block("BEGIN NULL; END;", provider="oracle").result(timeout=3)
        self.assertEqual(connections, [])
        session.submit("UPDATE t SET v=1").result(timeout=3)
        before = list(connections[0].events)
        with self.assertRaises(ManualSessionError) as error:
            session.execute_block("BEGIN NULL; END;", provider="oracle", authorized=True).result(timeout=3)
        self.assertEqual(error.exception.code, "BLOCK_REQUIRES_CLEAN_TRANSACTION")
        self.assertEqual(connections[0].events, before)
        mysql, mysql_connections = self._session("mysql", flags=1 << 16)
        with self.assertRaises(ManualSessionError):
            mysql.execute_block("CREATE PROCEDURE p() BEGIN SELECT 1; END", provider="mysql", authorized=True).result(timeout=3)
        self.assertEqual(mysql_connections[0].events, [])
        self.assertEqual(mysql.state, ManualSessionState.CLEAN)
        other, other_connections = self._session("oracle")
        with self.assertRaises(ManualSessionError) as error:
            other.execute_block("CREATE PROCEDURE p() BEGIN SELECT 1; END", provider="mysql", authorized=True).result(timeout=3)
        self.assertEqual(error.exception.code, "BLOCK_TARGET_MISMATCH")
        self.assertEqual(other_connections[0].events, [])

    def test_execution_error_stays_unknown_without_retry_or_automatic_rollback(self):
        session, connections = self._session(failure=True)
        with self.assertRaises(ManualSessionError) as error:
            session.execute_block("BEGIN UPDATE t SET v=1; END;", provider="oracle", authorized=True).result(timeout=3)
        self.assertEqual(error.exception.code, "BLOCK_EXECUTION_FAILED")
        self.assertEqual(session.state, ManualSessionState.UNKNOWN)
        self.assertEqual(len([event for event in connections[0].events if event[0] == "execute"]), 1)
        with self.assertRaises(ManualSessionError):
            session.commit().result(timeout=3)
        self.assertFalse(any(event[0] == "rollback" for event in connections[0].events))

    def test_cancellation_keeps_future_and_owner_until_vendor_returns(self):
        session, connections = self._session(block=True)
        future = session.execute_block("BEGIN UPDATE t SET v=1; END;", provider="oracle", authorized=True)
        deadline = time.monotonic() + 2
        while not connections and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertTrue(connections[0].entered.wait(2))
        try:
            session.cancel_current()
            self.assertFalse(future.done())
            self.assertTrue(session.poll_cleanup().busy)
        finally:
            connections[0].release.set()
        with self.assertRaises(ManualSessionError) as error:
            future.result(timeout=3)
        self.assertEqual(error.exception.code, "CANCELLED")
        self.assertEqual(session.state, ManualSessionState.DIRTY)


if __name__ == "__main__":
    unittest.main()
