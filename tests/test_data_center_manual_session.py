from __future__ import annotations

from datetime import date
from decimal import Decimal
import threading
import time
import unittest

from tools.data_center.manual_session import (
    ManualSession,
    ManualSessionError,
    ManualSessionState,
    ManualSessionTarget,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)


def _classification(kind: StatementKind, effect: TransactionEffect) -> StatementClassification:
    return StatementClassification(kind, effect)


class _FakeCursor:
    def __init__(self, driver, *, rows=(), description=None, rowcount=0, fetch_error=None):
        self.driver = driver
        self._rows = list(rows)
        self._offset = 0
        self.description = description or (("value", "TEXT", None, None, None, None, True),)
        self.rowcount = rowcount
        self.fetch_error = fetch_error
        self.closed = False

    def fetchmany(self, size):
        self.driver.events.append(("fetchmany", threading.get_ident(), size))
        if self.fetch_error is not None:
            raise self.fetch_error
        result = self._rows[self._offset:self._offset + size]
        self._offset += len(result)
        return result

    def close(self):
        self.driver.events.append(("cursor_close", threading.get_ident()))
        self.closed = True


class _FakeDriver:
    def __init__(self, *, statements=None, block_execute=None, commit_error=None, rollback_to_error=None):
        self.statements = dict(statements or {})
        self.block_execute = block_execute
        self.commit_error = commit_error
        self.rollback_to_error = rollback_to_error
        self.events = []
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False
        self._savepoints = []

    def configure_manual_commit(self):
        self.events.append(("configure", threading.get_ident()))

    def execute(self, sql, parameters):
        self.events.append(("execute", threading.get_ident(), sql, parameters))
        if self.block_execute is not None:
            self.block_execute[0].set()
            if not self.block_execute[1].wait(3):
                raise TimeoutError("test execute unblock timed out")
        result = self.statements.get(sql, {})
        error = result.get("error")
        if error is not None:
            raise error
        return _FakeCursor(
            self,
            rows=result.get("rows", ()),
            description=result.get("description"),
            rowcount=result.get("rowcount", 1),
            fetch_error=result.get("fetch_error"),
        )

    def commit(self):
        self.events.append(("commit", threading.get_ident()))
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))
        self.rollback_count += 1

    def close(self):
        self.events.append(("connection_close", threading.get_ident()))
        self.closed = True

    def create_savepoint(self, name):
        self.events.append(("savepoint", threading.get_ident(), name))
        self._savepoints.append(name)

    def rollback_to_savepoint(self, name):
        self.events.append(("rollback_to_savepoint", threading.get_ident(), name))
        if self.rollback_to_error is not None:
            raise self.rollback_to_error
        if name not in self._savepoints:
            raise RuntimeError("missing savepoint")

    def release_savepoint(self, name):
        self.events.append(("release_savepoint", threading.get_ident(), name))
        if name in self._savepoints:
            self._savepoints.remove(name)


class ManualSessionTests(unittest.TestCase):
    def _session(self, driver=None, *, target="tab-1", page_size=50, budget=1024 * 1024):
        driver = driver or _FakeDriver()
        caller_ident = threading.get_ident()
        factories = []

        def factory(scope):
            factories.append((threading.get_ident(), scope))
            return driver

        session = ManualSession(
            ManualSessionTarget(target, f"conn-{target}", "rev-1", database="db", schema="schema"),
            factory,
            self._classify,
            page_size=page_size,
            result_budget_bytes=budget,
        )
        self.assertEqual(factories, [])
        return session, driver, factories, caller_ident

    @staticmethod
    def _classify(sql):
        sql = sql.strip()
        mapping = {
            "UPDATE t SET v = 1": _classification(StatementKind.DML, TransactionEffect.DIRTY),
            "UPDATE t SET v = 2": _classification(StatementKind.DML, TransactionEffect.DIRTY),
            "INSERT INTO t VALUES (1)": _classification(StatementKind.DML, TransactionEffect.DIRTY),
            "DELETE FROM t": _classification(StatementKind.DML, TransactionEffect.DIRTY),
            "WITH changed AS (DELETE FROM t RETURNING id) SELECT * FROM changed": _classification(
                StatementKind.DML, TransactionEffect.DIRTY
            ),
            "SELECT locked FOR UPDATE": _classification(StatementKind.QUERY, TransactionEffect.DIRTY),
            "SELECT v INTO new_table FROM t": _classification(StatementKind.QUERY, TransactionEffect.DIRTY),
            "SELECT values": _classification(StatementKind.QUERY, TransactionEffect.NO_CHANGE),
            "SELECT old": _classification(StatementKind.QUERY, TransactionEffect.NO_CHANGE),
            "SELECT broken": _classification(StatementKind.QUERY, TransactionEffect.NO_CHANGE),
            "SELECT budget": _classification(StatementKind.QUERY, TransactionEffect.NO_CHANGE),
            "CREATE TABLE t (id INT)": _classification(StatementKind.DDL, TransactionEffect.IMPLICIT_COMMIT),
            "GRANT SELECT ON t TO u": _classification(StatementKind.DCL, TransactionEffect.IMPLICIT_COMMIT),
            "CALL vendor_proc()": _classification(StatementKind.OTHER, TransactionEffect.UNKNOWN),
            "UPDATE batch_a": _classification(StatementKind.DML, TransactionEffect.DIRTY),
            "UPDATE batch_b": _classification(StatementKind.DML, TransactionEffect.DIRTY),
            "COMMIT": _classification(StatementKind.TRANSACTION_CONTROL, TransactionEffect.UNKNOWN),
            "MULTI": _classification(StatementKind.MULTI_STATEMENT, TransactionEffect.UNKNOWN),
        }
        return mapping.get(sql, _classification(StatementKind.OTHER, TransactionEffect.UNKNOWN))

    @staticmethod
    def _code(future, expected):
        with unittest.TestCase().assertRaises(ManualSessionError) as caught:
            future.result(timeout=3)
        if caught.exception.code != expected:
            raise AssertionError(f"expected {expected}, got {caught.exception.code}")

    def _close_clean(self, session):
        session.close().result(timeout=3)

    def test_factory_errors_preserve_only_stable_profile_codes(self):
        safe_codes = (
            "PROFILE_MISMATCH",
            "PROFILE_UNAVAILABLE",
            "MANUAL_PROVIDER_UNSUPPORTED",
        )
        target = ManualSessionTarget("tab-safe", "conn-safe", "rev-1")
        for code in safe_codes:
            with self.subTest(code=code):
                factory_calls = []

                def factory(scope, expected=code):
                    factory_calls.append((threading.get_ident(), scope))
                    raise ManualSessionError(expected)

                session = ManualSession(target, factory, self._classify)
                self._code(session.query("SELECT values"), code)
                self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
                self.assertEqual(len(factory_calls), 1)
                self.assertEqual(factory_calls[0][1], target)
                session.close().result(timeout=3)

    def test_factory_errors_do_not_leak_raw_messages_or_untrusted_codes(self):
        raw_secret = "api_key=private-test-value"
        errors = [ManualSessionError(raw_secret)]
        drift_error = RuntimeError(f"profile failed: {raw_secret}")
        drift_error.code = "PROFILE_MISMATCH"
        errors.append(drift_error)

        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                factory_calls = []

                def factory(scope, factory_error=error):
                    factory_calls.append((threading.get_ident(), scope))
                    raise factory_error

                session = ManualSession(
                    ManualSessionTarget("tab-fail", "conn-fail", "rev-1"),
                    factory,
                    self._classify,
                )
                with self.assertRaises(ManualSessionError) as caught:
                    session.query("SELECT values").result(timeout=3)
                self.assertEqual(caught.exception.code, "CONNECTION_FAILED")
                self.assertEqual(str(caught.exception), "CONNECTION_FAILED")
                self.assertNotIn(raw_secret, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
                self.assertEqual(len(factory_calls), 1)
                session.close().result(timeout=3)

    def test_dml_stays_dirty_until_one_explicit_commit(self):
        session, driver, factories, caller = self._session()
        result = session.submit("UPDATE t SET v = 1").result(timeout=3)
        self.assertEqual(result.state, ManualSessionState.DIRTY)
        self.assertEqual(driver.commit_count, 0)
        self.assertEqual(driver.rollback_count, 0)
        self.assertEqual(session.commit().result(timeout=3).state, ManualSessionState.CLEAN)
        self.assertEqual(driver.commit_count, 1)
        self.assertEqual(session.commit().result(timeout=3).applied, False)
        self.assertEqual(driver.commit_count, 1)
        self.assertEqual(len(factories), 1)
        self.assertEqual(factories[0][1], session.target)
        owner = session._owner_ident
        self.assertNotEqual(owner, caller)
        self.assertTrue(all(event[1] == owner for event in driver.events))
        self._close_clean(session)

    def test_two_tabs_have_separate_owner_and_transaction_state(self):
        first, driver_a, _, caller = self._session(target="a")
        second, driver_b, _, _ = self._session(target="b")
        first.submit("UPDATE t SET v = 1").result(timeout=3)
        second.query("SELECT values").result(timeout=3)
        self.assertEqual(first.state, ManualSessionState.DIRTY)
        self.assertEqual(second.state, ManualSessionState.CLEAN)
        first.commit().result(timeout=3)
        self.assertEqual(driver_a.commit_count, 1)
        self.assertEqual(driver_b.commit_count, 0)
        self.assertNotEqual(first._owner_ident, second._owner_ident)
        self.assertNotEqual(first._owner_ident, caller)
        self._close_clean(first)
        self._close_clean(second)

    def test_explicit_effects_cover_lock_select_into_cte_and_unknown_vendor_sql(self):
        for sql in ("SELECT locked FOR UPDATE", "SELECT v INTO new_table FROM t", "WITH changed AS (DELETE FROM t RETURNING id) SELECT * FROM changed"):
            session, driver, _, _ = self._session()
            future = session.query(sql) if sql.startswith("SELECT") else session.submit(sql)
            future.result(timeout=3)
            self.assertEqual(session.state, ManualSessionState.DIRTY, sql)
            self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), 1)
            session.rollback().result(timeout=3)
            self._close_clean(session)

        session, driver, _, _ = self._session()
        result = session.submit("CALL vendor_proc()").result(timeout=3)
        self.assertEqual(result.state, ManualSessionState.UNKNOWN)
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), 1)
        with self.assertRaises(ManualSessionError) as caught:
            session.submit("UPDATE t SET v = 1").result(timeout=3)
        self.assertEqual(caught.exception.code, "TRANSACTION_OUTCOME_UNKNOWN")
        rollback = session.rollback().result(timeout=3)
        self.assertFalse(rollback.outcome_known)
        self.assertEqual(session.state, ManualSessionState.UNKNOWN)
        close_result = session.close(disposition="cancel").result(timeout=3)
        self.assertTrue(close_result.transaction_outcome_unknown)

        session, driver, _, _ = self._session()
        session.submit("UPDATE t SET v = 1").result(timeout=3)
        before = len([event for event in driver.events if event[0] == "execute"])
        self._code(session.submit("CALL vendor_proc()"), "STATEMENT_EFFECT_UNSAFE_FOR_DIRTY_TRANSACTION")
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), before)
        session.rollback().result(timeout=3)
        self._close_clean(session)

    def test_ddl_and_dcl_need_authorization_and_clean_transaction(self):
        session, driver, _, _ = self._session()
        self._code(session.submit("CREATE TABLE t (id INT)"), "DDL_CONFIRMATION_REQUIRED")
        self.assertEqual(driver.events, [])
        session.submit("UPDATE t SET v = 1").result(timeout=3)
        self._code(
            session.submit("CREATE TABLE t (id INT)", ddl_authorized=True),
            "DDL_REQUIRES_CLEAN_TRANSACTION",
        )
        self._code(
            session.submit("GRANT SELECT ON t TO u", dcl_authorized=True),
            "DCL_REQUIRES_CLEAN_TRANSACTION",
        )
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), 1)
        session.rollback().result(timeout=3)
        ddl = session.submit("CREATE TABLE t (id INT)", ddl_authorized=True).result(timeout=3)
        self.assertEqual(ddl.state, ManualSessionState.CLEAN)
        dcl = session.submit("GRANT SELECT ON t TO u", dcl_authorized=True).result(timeout=3)
        self.assertEqual(dcl.state, ManualSessionState.CLEAN)
        self._close_clean(session)

    def test_transaction_control_sql_cannot_bypass_state_machine(self):
        blocked = (
            "COMMIT",
            "ROLLBACK",
            "SAVEPOINT x",
            "SAVE TRANSACTION x",
            "SET autocommit = 1",
            "SET SESSION autocommit = 0",
            "SET TRANSACTION READ ONLY",
            "-- comment\nCOMMIT WORK",
            "MULTI",
        )
        for sql in blocked:
            session, driver, factories, _ = self._session()
            future = session.submit(sql)
            error_code = "MULTI_STATEMENT_UNSUPPORTED" if sql == "MULTI" else "TRANSACTION_SQL_FORBIDDEN"
            self._code(future, error_code)
            self.assertEqual(factories, [])
            self.assertFalse(any(event[0] == "execute" for event in driver.events))
            session.close().result(timeout=3)

    def test_failed_commit_becomes_unknown_and_is_never_retried(self):
        driver = _FakeDriver(commit_error=ConnectionError("link dropped"))
        driver.commit_error.connection_lost = True
        session, driver, _, _ = self._session(driver)
        session.submit("UPDATE t SET v = 1").result(timeout=3)
        self._code(session.commit(), "COMMIT_OUTCOME_UNKNOWN")
        self.assertEqual(session.state, ManualSessionState.UNKNOWN)
        self._code(session.commit(), "TRANSACTION_OUTCOME_UNKNOWN")
        self.assertEqual(driver.commit_count, 1)
        close_result = session.close(disposition="cancel").result(timeout=3)
        self.assertTrue(close_result.transaction_outcome_unknown)
        self.assertEqual(driver.commit_count, 1)

    def test_query_preserves_typed_rows_and_bounded_lookahead_pagination(self):
        rows = [(None,), ("",), (Decimal("12.30"),), (date(2026, 10, 1),), (b"\x00\xff",)] + [(index,) for index in range(47)]
        driver = _FakeDriver(statements={"SELECT values": {"rows": rows}})
        session, _, _, _ = self._session(driver)
        first = session.query("SELECT values", page_size=50).result(timeout=3)
        self.assertEqual(len(first.rows), 50)
        self.assertTrue(first.has_more)
        self.assertIsNone(first.rows[0][0])
        self.assertEqual(first.rows[1][0], "")
        self.assertIsInstance(first.rows[2][0], Decimal)
        self.assertEqual(first.rows[3][0], date(2026, 10, 1))
        self.assertEqual(first.rows[4][0], b"\x00\xff")
        second = session.fetch_next_page(50).result(timeout=3)
        self.assertEqual(second.rows, ((45,), (46,)))
        self.assertFalse(second.has_more)
        self.assertEqual(first.page_number, 1)
        self.assertEqual(second.page_number, 2)
        self._close_clean(session)

    def test_budget_truncation_and_failed_fetch_keep_last_successful_page(self):
        rows = [("a",), ("b",)]
        driver = _FakeDriver(statements={
            "SELECT old": {"rows": rows},
            "SELECT budget": {"rows": [(b"x" * 1000,)]},
            "SELECT broken": {"rows": [("bad",)], "fetch_error": RuntimeError("read failed")},
        })
        session, _, _, _ = self._session(driver, budget=64)
        old_page = session.query("SELECT old", page_size=50).result(timeout=3)
        truncated = session.query("SELECT budget", page_size=50).result(timeout=3)
        self.assertTrue(truncated.truncated_by_budget)
        self.assertEqual(truncated.rows, ())
        # A fetch error after a prior successful result never replaces that result.
        last_success = session.last_page
        with self.assertRaises(ManualSessionError) as caught:
            session.query("SELECT broken", page_size=50).result(timeout=3)
        self.assertEqual(caught.exception.code, "RESULT_FETCH_FAILED")
        self.assertIs(session.last_page, last_success)
        self.assertIsNot(session.last_page, old_page)
        self._close_clean(session)

    def test_cancel_during_blocked_driver_call_keeps_owner_and_connection(self):
        entered = threading.Event()
        release = threading.Event()
        driver = _FakeDriver(block_execute=(entered, release))
        session, _, _, _ = self._session(driver)
        future = session.query("SELECT values")
        self.assertTrue(entered.wait(2))
        self.assertTrue(session.cancel_current())
        status = session.poll_cleanup()
        self.assertEqual(status.state, ManualSessionState.CANCEL_PENDING)
        self.assertTrue(status.owner_alive)
        self.assertTrue(status.connection_retained)
        self.assertFalse(future.done())
        release.set()
        self._code(future, "CANCELLED")
        self.assertEqual(session.state, ManualSessionState.CLEAN)
        close_result = session.close().result(timeout=3)
        self.assertTrue(close_result.closed)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not session.poll_cleanup().cleanup_complete:
            time.sleep(0.01)
        self.assertTrue(session.poll_cleanup().cleanup_complete)

    def test_close_cancel_during_blocked_write_waits_then_rolls_back(self):
        entered = threading.Event()
        release = threading.Event()
        driver = _FakeDriver(block_execute=(entered, release))
        session, _, _, _ = self._session(driver)
        statement = session.submit("UPDATE t SET v = 1")
        self.assertTrue(entered.wait(2))
        close_future = session.close(disposition="cancel")
        self.assertFalse(close_future.done())
        status = session.poll_cleanup()
        self.assertEqual(status.state, ManualSessionState.CANCEL_PENDING)
        self.assertTrue(status.connection_retained)
        release.set()
        self._code(statement, "CANCELLED")
        close_result = close_future.result(timeout=3)
        self.assertTrue(close_result.closed)
        self.assertFalse(close_result.transaction_outcome_unknown)
        self.assertEqual(driver.commit_count, 0)
        self.assertEqual(driver.rollback_count, 1)


    def test_close_dirty_requires_explicit_decision_and_cancel_rolls_back_on_owner(self):
        session, driver, _, caller = self._session()
        session.submit("UPDATE t SET v = 1").result(timeout=3)
        self._code(session.close(), "CLOSE_REQUIRES_TRANSACTION_DECISION")
        self.assertFalse(driver.closed)
        close_result = session.close(disposition="cancel").result(timeout=3)
        self.assertTrue(close_result.closed)
        self.assertEqual(driver.rollback_count, 1)
        owner = session._owner_ident
        self.assertNotEqual(owner, caller)
        self.assertTrue(all(event[1] == owner for event in driver.events))

    def test_savepoint_failure_never_rolls_back_preexisting_transaction(self):
        failure = RuntimeError("batch item failed")
        driver = _FakeDriver(statements={"UPDATE batch_b": {"error": failure}})
        session, driver, _, _ = self._session(driver)
        session.submit("UPDATE t SET v = 1").result(timeout=3)
        result = session.run_savepoint_batch((("UPDATE batch_a", None), ("UPDATE batch_b", None))).result(timeout=3)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.statements_completed, 1)
        self.assertEqual(result.state, ManualSessionState.DIRTY)
        self.assertEqual(driver.rollback_count, 0)
        self.assertTrue(any(event[0] == "rollback_to_savepoint" for event in driver.events))
        session.rollback().result(timeout=3)
        self.assertEqual(driver.rollback_count, 1)
        self._close_clean(session)

    def test_savepoint_rollback_failure_marks_unknown_without_full_rollback(self):
        driver = _FakeDriver(
            statements={"UPDATE batch_b": {"error": RuntimeError("batch item failed")}},
            rollback_to_error=RuntimeError("savepoint rollback failed"),
        )
        session, driver, _, _ = self._session(driver)
        session.submit("UPDATE t SET v = 1").result(timeout=3)
        self._code(
            session.run_savepoint_batch((("UPDATE batch_a", None), ("UPDATE batch_b", None))),
            "SAVEPOINT_ROLLBACK_FAILED",
        )
        self.assertEqual(session.state, ManualSessionState.UNKNOWN)
        self.assertEqual(driver.rollback_count, 0)
        self.assertTrue(session.close(disposition="cancel").result(timeout=3).transaction_outcome_unknown)
        self.assertEqual(driver.rollback_count, 1)  # only the explicit close decision

    def test_savepoint_expected_rowcount_conflicts_roll_back_only_batch(self):
        for actual in (0, 2, None):
            with self.subTest(actual=actual):
                driver = _FakeDriver(statements={"UPDATE batch_a": {"rowcount": actual}})
                session, driver, _, _ = self._session(driver)
                session.submit("UPDATE t SET v = 1").result(timeout=3)
                result = session.run_savepoint_batch(
                    (("UPDATE batch_a", None),), expected_rowcounts=(1,)
                ).result(timeout=3)
                self.assertFalse(result.succeeded)
                self.assertEqual(result.error_code, "ROWCOUNT_CONFLICT")
                self.assertEqual(result.conflict_index, 0)
                self.assertEqual(result.expected_rowcount, 1)
                self.assertEqual(result.actual_rowcount, actual)
                self.assertEqual(result.state, ManualSessionState.DIRTY)
                self.assertEqual(driver.rollback_count, 0)
                self.assertTrue(any(event[0] == "rollback_to_savepoint" for event in driver.events))
                session.rollback().result(timeout=3)
                self._close_clean(session)

    def test_savepoint_expected_rowcounts_are_validated_before_queueing(self):
        session, driver, factories, _ = self._session()
        self._code(
            session.run_savepoint_batch((("UPDATE batch_a", None),), expected_rowcounts=()),
            "EXPECTED_ROWCOUNT_LENGTH_MISMATCH",
        )
        self._code(
            session.run_savepoint_batch((("UPDATE batch_a", None),), expected_rowcounts=(True,)),
            "INVALID_EXPECTED_ROWCOUNT",
        )
        self.assertEqual(factories, [])
        self.assertEqual(driver.events, [])
        session.close().result(timeout=3)


if __name__ == "__main__":
    unittest.main()
