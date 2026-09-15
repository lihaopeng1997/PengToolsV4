"""Focused DC-02 session and read-only query lifecycle tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import threading
import time
import unittest

from tools.data_center.contracts import CancellationToken, QueryRequest, QueryResult
from tools.data_center.query_executor import QueryExecutor
from tools.data_center.session_manager import SessionManager
from tools.data_center.sql_policy import SQLDecision


class _AllowPolicy:
    def validate(self, sql: str, dialect: str) -> SQLDecision:
        return SQLDecision(True, dialect=dialect, sql=sql.strip())


class _DenyPolicy:
    def validate(self, sql: str, dialect: str) -> SQLDecision:
        return SQLDecision(False, code="POLICY_DENIED", dialect=dialect, sql=sql.strip())


class _FakeDriver:
    def __init__(self, *, entered: threading.Event | None = None, release: threading.Event | None = None) -> None:
        self.entered = entered
        self.release = release
        self.calls: list[object] = []
        self.close_threads: list[int] = []
        self.execute_thread: int | None = None

    def execute_readonly(self, query, cancellation=None):
        self.execute_thread = threading.get_ident()
        self.calls.append(query)
        if self.entered is not None:
            self.entered.set()
        while self.release is not None and not self.release.wait(0.005):
            if cancellation is not None and cancellation.is_cancelled():
                return {"columns": ["value"], "rows": [("late",)]}
        return {
            "columns": ["id", "amount", "created", "empty"],
            "rows": [
                (1, Decimal("12.30"), date(2026, 9, 15), ""),
                (2, Decimal("0.00"), date(2026, 9, 16), None),
                (3, Decimal("9.00"), date(2026, 9, 17), "extra"),
            ],
        }

    def close(self) -> None:
        self.close_threads.append(threading.get_ident())


class _Factory:
    def __init__(self, driver: _FakeDriver) -> None:
        self.driver = driver
        self.calls: list[tuple[str, int]] = []

    def __call__(self, connection_id: str):
        self.calls.append((connection_id, threading.get_ident()))
        return self.driver


class _GenericOnlyDriver:
    """A driver with no declared read-only execution boundary."""

    def __init__(self) -> None:
        self.execute_calls = 0
        self.cursor_calls = 0
        self.close_calls = 0

    def execute(self, *args, **kwargs):  # pragma: no cover - must be blocked
        self.execute_calls += 1
        return {"columns": ["value"], "rows": [(1,)]}

    def cursor(self):  # pragma: no cover - must be blocked
        self.cursor_calls += 1
        return self

    def __iter__(self):
        return iter(())

    @property
    def description(self):
        return (("value",),)

    def close(self) -> None:
        self.close_calls += 1


class _SlowDriver:
    """Driver that ignores cooperative cancellation long enough to expose timeout races."""

    def __init__(self, duration: float = 0.25) -> None:
        self.duration = duration
        self.started = threading.Event()
        self.closed = threading.Event()
        self.close_threads: list[int] = []

    def execute_readonly(self, query, cancellation=None):
        self.started.set()
        time.sleep(self.duration)
        return {"columns": ["value"], "rows": [("late",)]}

    def close(self) -> None:
        self.close_threads.append(threading.get_ident())
        self.closed.set()


def _request(session, query_id: str = "q-1", **values) -> QueryRequest:
    return QueryRequest(
        session_id=session.session_id,
        query_id=query_id,
        generation=session.generation,
        sql=values.pop("sql", "SELECT id FROM orders"),
        parameters=values.pop("parameters", {"tenant": 7}),
        limits=values.pop("limits", {}),
        deadline=values.pop("deadline", None),
    )


class DataCenterSessionManagerTests(unittest.TestCase):
    def test_session_scope_is_fixed_and_factory_runs_and_closes_on_worker(self) -> None:
        main_thread = threading.get_ident()
        driver = _FakeDriver()
        factory = _Factory(driver)
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A", dialect="oracle")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=None, max_row_limit=2)
        self.addCleanup(executor.shutdown)

        result = executor.execute(_request(session), row_limit=2)

        self.assertTrue(result.ok)
        self.assertEqual(result.query_id, "q-1")
        self.assertEqual(len(result.rows), 2)
        self.assertTrue(result.truncated)
        self.assertIsInstance(result.rows[0][1], Decimal)
        self.assertIsInstance(result.rows[0][2], date)
        self.assertIsNone(result.rows[1][3])
        self.assertEqual(factory.calls[0][0], "conn-A")
        self.assertNotEqual(factory.calls[0][1], main_thread)
        self.assertEqual(driver.execute_thread, factory.calls[0][1])
        self.assertEqual(driver.close_threads, [factory.calls[0][1]])
        query = driver.calls[0]
        self.assertEqual(query.query_id, "q-1")
        self.assertEqual(query.connection_id, "conn-A")
        self.assertEqual(query.generation, session.generation)
        self.assertFalse(hasattr(executor, "commit"))
        self.assertFalse(hasattr(executor, "rollback"))
        self.assertFalse(hasattr(executor, "write"))

    def test_only_real_sql_decision_allow_can_reach_driver(self) -> None:
        driver = _FakeDriver()
        factory = _Factory(driver)
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A")
        executor = QueryExecutor(manager, _DenyPolicy(), default_timeout=None)
        self.addCleanup(executor.shutdown)

        denied = executor.execute(_request(session))
        caller_boolean = executor.execute(_request(session, "q-2"), decision=True)  # type: ignore[arg-type]

        self.assertFalse(denied.ok)
        self.assertEqual(denied.code, "POLICY_DENIED")
        self.assertFalse(caller_boolean.ok)
        self.assertEqual(caller_boolean.code, "POLICY_DENIED")
        self.assertEqual(factory.calls, [])

        class BooleanPolicy:
            def validate(self, sql: str, dialect: str):
                return True

        boolean_executor = QueryExecutor(manager, BooleanPolicy(), default_timeout=None)
        self.addCleanup(boolean_executor.shutdown)
        invalid = boolean_executor.execute(_request(session, "q-3"))
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.code, "POLICY_DENIED")
        self.assertEqual(factory.calls, [])

    def test_allow_decision_must_match_request_sql_and_dialect_before_factory(self) -> None:
        driver = _FakeDriver()
        factory = _Factory(driver)
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A", dialect="oracle")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=None)
        self.addCleanup(executor.shutdown)

        mismatched_sql = executor.execute(
            _request(session, "q-mismatch", sql="DELETE FROM orders"),
            decision=SQLDecision(True, dialect="oracle", sql="SELECT 1"),
        )
        mismatched_dialect = executor.execute(
            _request(session, "q-dialect"),
            decision=SQLDecision(True, dialect="mysql", sql="SELECT id FROM orders"),
        )

        self.assertFalse(mismatched_sql.ok)
        self.assertEqual(mismatched_sql.code, "POLICY_DENIED")
        self.assertFalse(mismatched_dialect.ok)
        self.assertEqual(mismatched_dialect.code, "POLICY_DENIED")
        self.assertEqual(factory.calls, [])

    def test_generic_execute_and_cursor_are_blocked_without_read_only_hook(self) -> None:
        driver = _GenericOnlyDriver()
        factory = _Factory(driver)  # type: ignore[arg-type]
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A", dialect="oracle")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=None)
        self.addCleanup(executor.shutdown)

        result = executor.execute(_request(session, "q-generic"))

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "READ_GUARD_NOT_READY")
        self.assertEqual(driver.execute_calls, 0)
        self.assertEqual(driver.cursor_calls, 0)
        self.assertEqual(driver.close_calls, 1)

    def test_deadline_and_cancel_stop_before_driver_and_suppress_late_callback(self) -> None:
        driver = _FakeDriver()
        factory = _Factory(driver)
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=None)
        self.addCleanup(executor.shutdown)

        expired = executor.execute(_request(session, "q-expired", deadline=time.monotonic() - 1))
        self.assertFalse(expired.ok)
        self.assertEqual(expired.code, "TIMEOUT")
        self.assertEqual(factory.calls, [])

        entered = threading.Event()
        release = threading.Event()
        driver.entered = entered
        driver.release = release
        delivered: list[object] = []
        future = executor.submit(_request(session, "q-late"), callback=delivered.append)
        self.assertTrue(entered.wait(1))
        self.assertTrue(manager.close_session(session.session_id, session.generation))
        release.set()
        late = future.result(timeout=2)
        self.assertFalse(late.ok)
        self.assertEqual(late.code, "STALE_RESULT")
        self.assertEqual(delivered, [])
        self.assertEqual(len(driver.close_threads), 1)

    def test_cancellation_is_reported_and_driver_is_still_closed(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        driver = _FakeDriver(entered=entered, release=release)
        factory = _Factory(driver)
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=None)
        self.addCleanup(executor.shutdown)
        token = CancellationToken()

        future = executor.submit(_request(session, "q-cancel"), cancellation=token)
        self.assertTrue(entered.wait(1))
        self.assertTrue(executor.cancel("q-cancel", reason="user_stop"))
        release.set()
        result = future.result(timeout=2)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CANCELLED")
        self.assertEqual(len(driver.close_threads), 1)

    def test_execute_returns_at_deadline_while_slow_worker_closes_and_cannot_reuse_query_id(self) -> None:
        driver = _SlowDriver()
        factory = _Factory(driver)  # type: ignore[arg-type]
        manager = SessionManager(factory)
        session = manager.create_session("conn-A", session_id="tab-A")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=0.05)
        self.addCleanup(executor.shutdown)

        started_at = time.perf_counter()
        result = executor.execute(_request(session, "q-timeout"))
        elapsed = time.perf_counter() - started_at

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "TIMEOUT")
        self.assertLess(elapsed, 0.18)
        self.assertTrue(driver.started.is_set())
        self.assertTrue(executor.is_running("q-timeout"))
        duplicate = executor.execute(_request(session, "q-timeout"))
        self.assertFalse(duplicate.ok)
        self.assertEqual(duplicate.code, "DUPLICATE_QUERY_ID")
        self.assertTrue(driver.closed.wait(1.0))
        self.assertEqual(len(driver.close_threads), 1)

        callback_driver = _SlowDriver()
        callback_factory = _Factory(callback_driver)  # type: ignore[arg-type]
        callback_session = manager.create_session("conn-B", callback_factory, session_id="tab-B")
        delivered: list[QueryResult] = []
        future = executor.submit(
            _request(callback_session, "q-callback"),
            callback=delivered.append,
        )
        self.assertTrue(callback_driver.started.wait(1.0))
        late = future.result(timeout=1.0)
        self.assertFalse(late.ok)
        self.assertEqual(late.code, "TIMEOUT")
        self.assertTrue(callback_driver.closed.wait(1.0))
        self.assertEqual(delivered, [])

    def test_generation_change_rejects_queued_request_without_opening_new_driver(self) -> None:
        old_driver = _FakeDriver()
        new_driver = _FakeDriver()
        old_factory = _Factory(old_driver)
        new_factory = _Factory(new_driver)
        manager = SessionManager(old_factory)
        session = manager.create_session("conn-A", session_id="tab-A")
        executor = QueryExecutor(manager, _AllowPolicy(), default_timeout=None)
        self.addCleanup(executor.shutdown)
        self.assertTrue(manager.close_session(session.session_id, session.generation))
        manager.create_session("conn-B", new_factory, session_id="tab-A")

        result = executor.execute(_request(session, "q-stale"))

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "STALE_GENERATION")
        self.assertEqual(old_factory.calls, [])
        self.assertEqual(new_factory.calls, [])


if __name__ == "__main__":
    unittest.main()
