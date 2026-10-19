from __future__ import annotations

from concurrent.futures import Future
import json
import threading
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.data_center.manual_session import (
    ManualResultSourceState,
    ManualSession,
    ManualSessionError,
    ManualSessionState,
    ManualSessionTarget,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.result_export import ExportTarget


def _classify(sql: str) -> StatementClassification:
    text = sql.strip().upper()
    if text.startswith("UPDATE") or text.startswith("DELETE") or text.startswith("INSERT"):
        return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)
    if text.startswith("SELECT"):
        effect = TransactionEffect.DIRTY if "FOR UPDATE" in text else TransactionEffect.NO_CHANGE
        return StatementClassification(StatementKind.QUERY, effect)
    return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)


class _Cursor:
    def __init__(self, driver, spec):
        self.driver = driver
        self.rows = list(spec.get("rows", ()))
        self.offset = 0
        self.description = spec.get(
            "description",
            (("value", "INTEGER", None, None, 64, 0, True),),
        )
        self.rowcount = spec.get("rowcount", len(self.rows))
        self.fetch_calls = 0
        self.fetch_error_at = spec.get("fetch_error_at")
        self.block_fetch_at = spec.get("block_fetch_at")
        self.closed = False

    def fetchmany(self, size):
        self.fetch_calls += 1
        call_number = self.fetch_calls
        self.driver.events.append(("fetchmany", threading.get_ident(), call_number, size))
        if self.block_fetch_at is not None and call_number == self.block_fetch_at[0]:
            entered, release = self.block_fetch_at[1]
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fake fetch release timed out")
        if self.fetch_error_at == call_number:
            raise RuntimeError("fake result read failed")
        result = self.rows[self.offset:self.offset + size]
        self.offset += len(result)
        return result

    def close(self):
        self.closed = True
        self.driver.events.append(("cursor_close", threading.get_ident()))


class _Driver:
    def __init__(self, statements):
        self.statements = dict(statements)
        self.events = []
        self.execute_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def configure_manual_commit(self):
        self.events.append(("configure", threading.get_ident()))

    def execute(self, sql, parameters):
        self.execute_count += 1
        self.events.append(("execute", threading.get_ident(), sql, parameters))
        return _Cursor(self, self.statements[sql])

    def commit(self):
        self.commit_count += 1
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.rollback_count += 1
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


class ManualResultExportTests(unittest.TestCase):
    def _session(self, driver, *, budget=1024 * 1024, page_size=50, generation=3):
        factory_calls = []

        def factory(target):
            factory_calls.append((threading.get_ident(), target))
            return driver

        target = ManualSessionTarget(
            "manual-tab",
            "connection-a",
            "profile-rev-7",
            database="db_a",
            schema="owner_a",
            generation=generation,
        )
        session = ManualSession(
            target,
            factory,
            _classify,
            page_size=page_size,
            result_budget_bytes=budget,
        )
        return session, target, factory_calls

    @staticmethod
    def _export_target(page):
        return ExportTarget(page.query_id, page.tab_id, page.generation)

    @staticmethod
    def _read_json_ints(path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return [int(row[0]["v"]) for row in data["rows"]]

    @staticmethod
    def _assert_future_code(test, future, expected):
        with test.assertRaises(ManualSessionError) as caught:
            future.result(timeout=4)
        test.assertEqual(caught.exception.code, expected, repr(caught.exception.__cause__))

    def _close(self, session):
        state = session.state
        disposition = "rollback" if state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN} else None
        return session.close(disposition=disposition).result(timeout=4)

    def test_three_page_json_export_continues_same_cursor_once(self):
        rows = [(index,) for index in range(130)]
        driver = _Driver({"SELECT values": {"rows": rows}})
        session, _session_target, factory_calls = self._session(driver)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "full.json"
            page_one = session.query("SELECT values", page_size=50).result(timeout=4)
            page_two = session.fetch_next_page(50).result(timeout=4)
            status_before = session.result_source_status
            self.assertEqual(status_before.state, ManualResultSourceState.AVAILABLE)
            self.assertTrue(status_before.can_continue)
            self.assertTrue(status_before.can_export_full)
            self.assertEqual(status_before.page_count, 2)

            receipt = session.export_full_query(
                destination,
                "json",
                self._export_target(page_one),
            ).result(timeout=4)

            self.assertEqual(receipt.rows, 130)
            self.assertEqual(self._read_json_ints(destination), list(range(130)))
            self.assertEqual(driver.execute_count, 1)
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
            status_after = session.result_source_status
            self.assertEqual(status_after.state, ManualResultSourceState.EXPORTED)
            self.assertFalse(status_after.can_continue)
            self.assertFalse(status_after.can_export_full)
            self.assertFalse(status_after.cursor_open)
            owner_ids = {event[1] for event in driver.events}
            self.assertEqual(len(owner_ids), 1)
            self.assertNotEqual(next(iter(owner_ids)), threading.get_ident())
            self.assertTrue(factory_calls)
            self.assertEqual(factory_calls[0][0], next(iter(owner_ids)))
            self.assertEqual(page_two.page_number, 2)
        self._close(session)

    def test_budget_truncation_exports_candidate_and_fetched_batch_tail(self):
        rows = [(f"row-{index:03d}-abcdefgh",) for index in range(120)]
        driver = _Driver({"SELECT values": {"rows": rows}})
        session, _target, _factory_calls = self._session(driver, budget=300)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "continued.json"
            first = session.query("SELECT values", page_size=50).result(timeout=4)
            self.assertTrue(first.truncated_by_budget)
            self.assertFalse(first.has_more)
            self.assertEqual(len(first.rows), 2)
            status = session.result_source_status
            self.assertEqual(status.state, ManualResultSourceState.BUDGET_LIMITED)
            self.assertFalse(status.can_continue)
            self.assertTrue(status.can_export_full)
            self.assertGreater(status.pending_rows, 1)
            self.assertLessEqual(status.pending_rows, 501)
            self.assertLessEqual(status.pending_bytes, 50 * 1024 * 1024)

            receipt = session.export_full_query(
                destination,
                "json",
                self._export_target(first),
            ).result(timeout=4)

            data = json.loads(destination.read_text(encoding="utf-8"))
            exported = [row[0]["v"] for row in data["rows"]]
            self.assertEqual(receipt.rows, len(rows))
            self.assertEqual(exported, [row[0] for row in rows])
            self.assertEqual(driver.execute_count, 1)
            self.assertEqual(session.result_source_status.state, ManualResultSourceState.EXPORTED)
            self.assertFalse(session.result_source_status.can_export_full)
        self._close(session)

    def test_exact_budget_boundary_is_complete_without_lookahead_duplication(self):
        rows = [(1,), (2,), (3,)]
        exact_budget = sum(sys_getsizeof(row) for row in rows)
        driver = _Driver({"SELECT values": {"rows": rows}})
        session, _target, _factory_calls = self._session(driver, budget=exact_budget)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "exact.json"
            page = session.query("SELECT values", page_size=50).result(timeout=4)
            self.assertFalse(page.truncated_by_budget)
            self.assertFalse(page.has_more)
            self.assertEqual(session.result_source_status.state, ManualResultSourceState.COMPLETE)
            receipt = session.export_full_query(destination, "json", self._export_target(page)).result(timeout=4)
            self.assertEqual(receipt.rows, 3)
            self.assertEqual(self._read_json_ints(destination), [1, 2, 3])
            self.assertEqual(driver.execute_count, 1)
        self._close(session)

    def test_prefetch_byte_limit_fails_closed_without_claiming_full_export(self):
        rows = [(f"r{index}",) for index in range(10)]
        driver = _Driver({"SELECT values": {"rows": rows}})
        session, _target, _factory_calls = self._session(driver, budget=1)
        with patch("tools.data_center.manual_session.MAX_PENDING_PREFETCH_BYTES", 8):
            page = session.query("SELECT values", page_size=50).result(timeout=4)
        self.assertTrue(page.truncated_by_budget)
        status = session.result_source_status
        self.assertEqual(status.state, ManualResultSourceState.UNAVAILABLE)
        self.assertEqual(status.reason_code, "PREFETCH_LIMIT")
        self.assertFalse(status.can_export_full)
        self.assertFalse(status.can_continue)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "must-not-exist.json"
            self._assert_future_code(
                self,
                session.export_full_query(destination, "json", self._export_target(page)),
                "RESULT_SOURCE_UNAVAILABLE",
            )
            self.assertFalse(destination.exists())
        self._close(session)

    def test_dirty_dml_rowset_exports_same_cursor_without_commit_or_rollback(self):
        rows = [(index,) for index in range(120)]
        driver = _Driver({"UPDATE t SET v = v + 1 RETURNING v": {"rows": rows}})
        session, _target, _factory_calls = self._session(driver)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "returning.json"
            statement = session.submit("UPDATE t SET v = v + 1 RETURNING v", page_size=50).result(timeout=4)
            self.assertEqual(statement.state, ManualSessionState.DIRTY)
            self.assertIsNotNone(statement.page)
            self.assertEqual(session.state, ManualSessionState.DIRTY)

            receipt = session.export_full_query(
                destination,
                "json",
                self._export_target(statement.page),
            ).result(timeout=4)

            self.assertEqual(receipt.rows, len(rows))
            self.assertEqual(self._read_json_ints(destination), list(range(120)))
            self.assertEqual(driver.execute_count, 1)
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
            self.assertEqual(session.state, ManualSessionState.DIRTY)
        session.close(disposition="rollback").result(timeout=4)
        self.assertEqual(driver.rollback_count, 1)

    def test_dml_without_rowset_has_no_export_source(self):
        driver = _Driver({"UPDATE t SET v = 1": {"rows": (), "description": ()}})
        session, _target, _factory_calls = self._session(driver)
        statement = session.submit("UPDATE t SET v = 1").result(timeout=4)
        self.assertIsNone(statement.page)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "none.json"
            target = ExportTarget(statement.query_id, statement.tab_id, statement.generation)
            self._assert_future_code(
                self,
                session.export_full_query(destination, "json", target),
                "RESULT_SOURCE_UNAVAILABLE",
            )
            self.assertFalse(destination.exists())
            self.assertEqual(driver.execute_count, 1)
            self.assertEqual(session.state, ManualSessionState.DIRTY)
        session.close(disposition="rollback").result(timeout=4)

    def test_dispatch_rechecks_identity_after_queued_query_replaces_source(self):
        driver = _Driver({
            "SELECT old": {"rows": [(1,), (2,)]},
            "SELECT new": {"rows": [(9,)]},
        })
        session, _target, _factory_calls = self._session(driver)
        old_page = session.query("SELECT old").result(timeout=4)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "stale.json"
            replacement_future = session.query("SELECT new")
            stale_export = session.export_full_query(destination, "json", self._export_target(old_page))
            replacement_future.result(timeout=4)
            self._assert_future_code(self, stale_export, "RESULT_EXPORT_TARGET_MISMATCH")
            self.assertFalse(destination.exists())
            self.assertEqual(driver.execute_count, 2)
            self.assertEqual(session.result_source_status.state, ManualResultSourceState.COMPLETE)
        self._close(session)

    def test_export_serializes_queued_commit_and_does_not_change_dirty_state(self):
        entered = threading.Event()
        release = threading.Event()
        rows = [(index,) for index in range(120)]
        driver = _Driver({
            "UPDATE t SET v = v + 1 RETURNING v": {
                "rows": rows,
                "block_fetch_at": (2, (entered, release)),
            }
        })
        session, _target, _factory_calls = self._session(driver)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "serialized.json"
            statement = session.submit("UPDATE t SET v = v + 1 RETURNING v", page_size=50).result(timeout=4)
            export_future = session.export_full_query(destination, "json", self._export_target(statement.page))
            self.assertTrue(entered.wait(2))
            status = session.result_source_status
            self.assertEqual(status.state, ManualResultSourceState.EXPORTING)
            self.assertTrue(status.cursor_open)
            self.assertFalse(status.can_continue)
            self.assertFalse(status.can_export_full)
            commit_future = session.commit()
            self.assertFalse(commit_future.done())
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(session.state, ManualSessionState.DIRTY)
            release.set()
            self.assertEqual(export_future.result(timeout=4).rows, len(rows))
            self.assertTrue(commit_future.result(timeout=4).applied)
            self.assertEqual(driver.commit_count, 1)
            self.assertEqual(driver.execute_count, 1)
        session.close().result(timeout=4)

    def test_close_during_blocked_export_waits_for_owner_and_removes_partial(self):
        entered = threading.Event()
        release = threading.Event()
        driver = _Driver({
            "UPDATE t SET v = v + 1 RETURNING v": {
                "rows": [(index,) for index in range(120)],
                "block_fetch_at": (2, (entered, release)),
            }
        })
        session, _target, _factory_calls = self._session(driver)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cancelled.json"
            statement = session.submit("UPDATE t SET v = v + 1 RETURNING v", page_size=50).result(timeout=4)
            export_future = session.export_full_query(destination, "json", self._export_target(statement.page))
            self.assertTrue(entered.wait(2))
            close_future = session.close(disposition="cancel")
            self.assertFalse(close_future.done())
            cleanup = session.poll_cleanup()
            self.assertTrue(cleanup.owner_alive)
            self.assertTrue(cleanup.connection_retained)
            self.assertFalse(cleanup.cleanup_complete)
            release.set()
            try:
                export_future.result(timeout=4)
            except ManualSessionError as error:
                self.assertEqual(error.code, "CANCELLED", repr(driver.events))
            else:
                self.fail("cancelled export unexpectedly completed")
            close_result = close_future.result(timeout=4)
            self.assertTrue(close_result.closed)
            self.assertTrue(driver.closed)
            self.assertEqual(driver.rollback_count, 1)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temporary).glob(".pengtools-export-*.tmp")), [])
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not session.poll_cleanup().cleanup_complete:
                time.sleep(0.01)
            self.assertTrue(session.poll_cleanup().cleanup_complete)

    def test_failed_cursor_continuation_marks_source_unavailable_and_leaves_no_partial(self):
        driver = _Driver({
            "SELECT values": {
                "rows": [(index,) for index in range(120)],
                "fetch_error_at": 2,
            }
        })
        session, _target, _factory_calls = self._session(driver)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "failed.json"
            page = session.query("SELECT values", page_size=50).result(timeout=4)
            self._assert_future_code(
                self,
                session.export_full_query(destination, "json", self._export_target(page)),
                "RESULT_EXPORT_PAGE_SOURCE_FAILED",
            )
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temporary).glob(".pengtools-export-*.tmp")), [])
            status = session.result_source_status
            self.assertEqual(status.state, ManualResultSourceState.UNAVAILABLE)
            self.assertEqual(status.reason_code, "PAGE_SOURCE_FAILED")
            self.assertFalse(status.can_export_full)
            self.assertEqual(driver.execute_count, 1)
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
        self._close(session)

    def test_export_future_callbacks_run_without_session_lock(self):
        driver = _Driver({"SELECT values": {"rows": [(1,), (2,)]}})
        session, _target, _factory_calls = self._session(driver)
        page = session.query("SELECT values").result(timeout=4)
        callback_observed = threading.Event()
        callback_snapshot = []

        def done_callback(_future: Future):
            reader = threading.Thread(
                target=lambda: (callback_snapshot.append(session.result_source_status), callback_observed.set()),
                daemon=True,
            )
            reader.start()
            reader.join(timeout=1)

        with tempfile.TemporaryDirectory() as temporary:
            future = session.export_full_query(
                Path(temporary) / "callback.json",
                "json",
                self._export_target(page),
            )
            future.add_done_callback(done_callback)
            future.result(timeout=4)
            self.assertTrue(callback_observed.wait(1))
            self.assertEqual(callback_snapshot[0].state, ManualResultSourceState.COMPLETE)
        self._close(session)

    def test_running_export_requires_cooperative_cancellation(self):
        entered = threading.Event()
        release = threading.Event()
        driver = _Driver({
            "UPDATE t SET v = v + 1 RETURNING v": {
                "rows": [(index,) for index in range(120)],
                "block_fetch_at": (2, (entered, release)),
            }
        })
        session, _target, _factory_calls = self._session(driver)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "cooperatively-cancelled.json"
                statement = session.submit(
                    "UPDATE t SET v = v + 1 RETURNING v",
                    page_size=50,
                ).result(timeout=4)
                self.assertEqual(statement.state, ManualSessionState.DIRTY)
                future = session.export_full_query(
                    destination,
                    "json",
                    self._export_target(statement.page),
                )

                self.assertTrue(entered.wait(2))
                self.assertTrue(future.running())
                self.assertFalse(future.cancel())
                self.assertTrue(session.cancel_current())
                self.assertFalse(future.done())
                self.assertFalse(future.cancel())
                self.assertEqual(session.result_source_status.state, ManualResultSourceState.EXPORTING)
                self.assertFalse(session.result_source_status.can_continue)
                self.assertFalse(session.result_source_status.can_export_full)

                release.set()
                with self.assertRaises(ManualSessionError) as caught:
                    future.result(timeout=4)
                self.assertEqual(caught.exception.code, "CANCELLED")
                self.assertTrue(future.done())
                self.assertFalse(destination.exists())
                self.assertEqual(list(Path(temporary).glob(".pengtools-export-*.tmp")), [])

                status = session.result_source_status
                self.assertEqual(status.state, ManualResultSourceState.UNAVAILABLE)
                self.assertEqual(status.reason_code, "CANCELLED")
                self.assertFalse(status.can_continue)
                self.assertFalse(status.can_export_full)
                self.assertFalse(status.cursor_open)
                self.assertEqual(driver.execute_count, 1)
                self.assertEqual(driver.commit_count, 0)
                self.assertEqual(driver.rollback_count, 0)
                self.assertEqual(session.state, ManualSessionState.DIRTY)
        finally:
            release.set()
            session.close(disposition="rollback").result(timeout=4)

    def test_cancel_queued_export_before_start_preserves_completed_source(self):
        driver = _Driver({"SELECT values": {"rows": [(1,), (2,)]}})
        session, _target, _factory_calls = self._session(driver)
        page = session.query("SELECT values").result(timeout=4)
        dispatch_entered = threading.Event()
        dispatch_release = threading.Event()
        original_dispatch = session._dispatch

        def pause_clean_commit(operation, *arguments):
            if operation == "commit":
                dispatch_entered.set()
                if not dispatch_release.wait(5):
                    raise TimeoutError("fake commit dispatch timed out")
            return original_dispatch(operation, *arguments)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cancel-before-start.json"
            with patch.object(session, "_dispatch", side_effect=pause_clean_commit):
                commit_future = session.commit()
                self.assertTrue(dispatch_entered.wait(2))
                export_future = session.export_full_query(
                    destination,
                    "json",
                    self._export_target(page),
                )
                self.assertTrue(export_future.cancel())
                dispatch_release.set()
                commit_future.result(timeout=4)

            self.assertTrue(export_future.cancelled())
            self.assertFalse(destination.exists())
            status = session.result_source_status
            self.assertEqual(status.state, ManualResultSourceState.COMPLETE)
            self.assertTrue(status.can_export_full)
            self.assertEqual(driver.execute_count, 1)

            receipt = session.export_full_query(
                destination,
                "json",
                self._export_target(page),
            ).result(timeout=4)
            self.assertEqual(receipt.rows, 2)
            self.assertEqual(self._read_json_ints(destination), [1, 2])
            self.assertEqual(driver.execute_count, 1)
        self._close(session)


def sys_getsizeof(row):
    import sys

    return sys.getsizeof(row) + sum(sys.getsizeof(value) for value in row)


if __name__ == "__main__":
    unittest.main()
