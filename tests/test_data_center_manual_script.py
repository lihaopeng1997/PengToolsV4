from __future__ import annotations

import threading
import time
import sqlite3
import unittest

from tools.data_center.manual_driver import classify_mysql_statement
from tools.data_center.manual_session import ManualSession, ManualSessionError, ManualSessionState, ManualSessionTarget
from tests.test_data_center_manual_session import _FakeDriver


class ManualScriptTests(unittest.TestCase):
    def _session(self, driver=None, budget=1024 * 1024):
        driver = driver or _FakeDriver()
        factories = []
        def connect(target):
            factories.append((target, threading.get_ident()))
            return driver
        session = ManualSession(ManualSessionTarget("script", "temp", "rev"), connect, classify_mysql_statement, page_size=50, result_budget_bytes=budget)
        def cleanup():
            session.close(disposition="rollback").result(timeout=3)
            deadline = time.monotonic() + 3
            while not session.poll_cleanup().cleanup_complete and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertTrue(session.poll_cleanup().cleanup_complete)
        self.addCleanup(cleanup)
        return session, driver, factories

    def test_mixed_script_single_owner_stops_at_failure_and_retains_dirty_writes(self):
        statements = ("UPDATE t SET v=1", "SELECT values FROM t", "DELETE FROM missing", "UPDATE t SET v=2")
        driver = _FakeDriver(statements={statements[1]: {"rows": [(i,) for i in range(80)]}, statements[2]: {"error": RuntimeError("private diagnostic")}})
        session, driver, factories = self._session(driver)
        result = session.run_script(statements).result(timeout=3)
        self.assertEqual(result.completed_count, 2)
        self.assertEqual([entry.error_code for entry in result.entries], ["", "", "STATEMENT_EXECUTION_FAILED", "SKIPPED"])
        self.assertEqual(len(result.entries[1].result.page.rows), 50)
        self.assertEqual([event[2] for event in driver.events if event[0] == "execute"], list(statements[:3]))
        self.assertEqual(len(factories), 1)
        self.assertEqual({event[1] for event in driver.events}, {factories[0][1]})
        self.assertNotEqual(factories[0][1], threading.get_ident())
        self.assertEqual(session.state, ManualSessionState.DIRTY)
        self.assertEqual((driver.commit_count, driver.rollback_count), (0, 0))

    def test_real_temporary_sqlite_effects_stay_uncommitted_until_explicit_rollback(self):
        class SqliteDriver:
            def configure_manual_commit(self):
                self.connection = sqlite3.connect(":memory:")
                self.connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            def execute(self, sql, parameters):
                return self.connection.execute(sql, parameters)
            def commit(self):
                self.connection.commit()
            def rollback(self):
                self.connection.rollback()
            def close(self):
                self.connection.close()
        session, _, _ = self._session(SqliteDriver())
        result = session.run_script(("INSERT INTO t VALUES (1)", "SELECT id FROM t", "INSERT INTO missing VALUES (2)", "INSERT INTO t VALUES (3)")).result(timeout=3)
        self.assertEqual(result.completed_count, 2)
        self.assertEqual(result.entries[1].result.page.rows, ((1,),))
        self.assertEqual(result.entries[-1].error_code, "SKIPPED")
        self.assertEqual(session.query("SELECT COUNT(*) FROM t").result(timeout=3).rows, ((1,),))
        session.rollback().result(timeout=3)
        self.assertEqual(session.query("SELECT COUNT(*) FROM t").result(timeout=3).rows, ((0,),))

    def test_last_query_continues_same_cursor_and_earlier_pages_are_retained(self):
        driver = _FakeDriver(statements={"SELECT first": {"rows": [(i,) for i in range(90)]}, "SELECT last": {"rows": [(i,) for i in range(120)]}})
        session, driver, _ = self._session(driver)
        result = session.run_script(("SELECT first", "SELECT last")).result(timeout=3)
        first, last = (entry.result.page for entry in result.entries)
        following = session.fetch_next_page(50).result(timeout=3)
        self.assertEqual(last.query_id, following.query_id)
        self.assertNotEqual(first.query_id, last.query_id)
        self.assertEqual([row[0] for row in last.rows + following.rows], list(range(100)))
        self.assertEqual(len(first.rows), 50)
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), 2)

    def test_preflight_rejects_controls_unapproved_ddl_and_limits_without_io(self):
        session, _driver, factories = self._session()
        for statements in (("UPDATE t SET v=1", "COMMIT"), ("UPDATE t SET v=1", "CREATE TABLE x (id INT)"), ("SELECT first; DELETE FROM t",), tuple("SELECT 1" for _ in range(257)), ("SELECT '" + "x" * (256 * 1024) + "'",), ("CREATE PROCEDURE p() BEGIN SELECT 1; END",)):
            with self.subTest(statements_count=len(statements)):
                with self.assertRaises(ManualSessionError):
                    session.run_script(statements).result(timeout=3)
        self.assertEqual(factories, [])

    def test_authorized_ddl_still_cannot_commit_preceding_script_writes(self):
        session, driver, _ = self._session()
        result = session.run_script(("UPDATE t SET v=1", "CREATE TABLE x (id INT)", "SELECT later"), ddl_authorized=True).result(timeout=3)
        self.assertEqual(result.error_code, "DDL_REQUIRES_CLEAN_TRANSACTION")
        self.assertEqual([event[2] for event in driver.events if event[0] == "execute"], ["UPDATE t SET v=1"])
        self.assertEqual(result.entries[2].error_code, "SKIPPED")
        self.assertEqual(session.state, ManualSessionState.DIRTY)
        self.assertEqual((driver.commit_count, driver.rollback_count), (0, 0))

    def test_script_pages_share_budget_and_new_single_query_resets_it(self):
        rows = [("x" * 100,) for _ in range(60)]
        driver = _FakeDriver(statements={"SELECT first": {"rows": rows}, "SELECT last": {"rows": rows}})
        session, driver, _ = self._session(driver, budget=700)
        result = session.run_script(("SELECT first", "SELECT last")).result(timeout=3)
        pages = [entry.result.page for entry in result.entries]
        self.assertLessEqual(sum(page.result_bytes for page in pages), 700)
        self.assertTrue(pages[-1].truncated_by_budget)
        self.assertTrue(session.result_source_status.can_export_full)
        single = session.query("SELECT first").result(timeout=3)
        self.assertEqual(len(single.rows), len(pages[0].rows))

    def test_cancellation_waits_for_blocked_owner_and_skips_remaining_statements(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingDriver(_FakeDriver):
            def execute(self, sql, parameters):
                if sql == "SELECT slow":
                    entered.set()
                    release.wait(3)
                return super().execute(sql, parameters)
        session, driver, _ = self._session(BlockingDriver())
        future = session.run_script(("UPDATE t SET v=1", "SELECT slow", "DELETE FROM t"))
        self.assertTrue(entered.wait(2))
        try:
            session.cancel_current()
            self.assertFalse(future.done())
            self.assertTrue(session.poll_cleanup().busy)
        finally:
            release.set()
        result = future.result(timeout=3)
        self.assertEqual(result.error_code, "CANCELLED")
        self.assertEqual(result.entries[-1].error_code, "SKIPPED")
        self.assertEqual([event[2] for event in driver.events if event[0] == "execute"], ["UPDATE t SET v=1", "SELECT slow"])
        self.assertEqual(session.state, ManualSessionState.DIRTY)


if __name__ == "__main__":
    unittest.main()
