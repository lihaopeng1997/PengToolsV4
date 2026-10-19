"""TEMP-only regressions for legacy database workbench worker cleanup."""

from __future__ import annotations

import os
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from panels import ai_workbench_panel as ai_module
from panels import db_mongodb_panel as mongo_module
from panels import db_redis_panel as redis_module


class _FakeAgentHost(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.cleanup_calls = 0

    def target_changed(self, *_args):
        pass

    def agent_shutdown_cleanup(self):
        self.cleanup_calls += 1
        return True


class _ClosedConnection:
    def __init__(self):
        self.closed = False
        self.close_thread = None


class WorkbenchCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait_until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.003)
        self.app.processEvents()
        self.assertTrue(predicate(), "condition did not become true before timeout")

    def _wait_thread_stopped_without_dispatch(self, worker, timeout=3.0):
        deadline = time.monotonic() + timeout
        while worker.isRunning() and time.monotonic() < deadline:
            time.sleep(0.003)
        self.assertFalse(worker.isRunning(), "TEMP worker did not return from its fake operation")

    def _make_panel(self, module, panel_type):
        with (
            patch.object(module, "load_connections", return_value=[]),
            patch("ui.data_center.integration.DataCenterWorkbenchAgent", _FakeAgentHost),
        ):
            panel = panel_type()
        self.addCleanup(panel.deleteLater)
        return panel

    def test_ai_workbench_cleanup_stays_pending_until_db_worker_really_finishes(self):
        panel = self._make_panel(ai_module, ai_module.AiWorkbenchPanel)
        entered_query = threading.Event()
        release = threading.Event()
        connection = _ClosedConnection()
        worker_thread_ids = []

        def open_fake(_item):
            worker_thread_ids.append(threading.get_ident())
            return connection

        def run_fake(_conn, _dialect, _sql, **_kwargs):
            entered_query.set()
            if not release.wait(5):
                raise TimeoutError("TEMP fake query was not released")
            return {"columns": ["value"], "rows": [["late-result"]], "rowcount": 1}

        def close_fake(conn):
            if conn is not None:
                conn.closed = True
                conn.close_thread = threading.get_ident()

        panel._current_conn = lambda: {"id": "TEMP-db", "dialect": "mysql"}
        panel.result.setRowCount(1)
        panel.result.setColumnCount(1)
        from PyQt6.QtWidgets import QTableWidgetItem
        panel.result.setItem(0, 0, QTableWidgetItem("preserved-row"))

        with (
            patch.object(ai_module, "open_connection", side_effect=open_fake),
            patch.object(ai_module, "close_connection", side_effect=close_fake),
            patch.object(ai_module, "run_console_statement", side_effect=run_fake),
        ):
            panel._start_db("query", sql="SELECT TEMP")
            self.assertTrue(entered_query.wait(2), "TEMP worker did not enter fake query")
            worker = panel._worker
            self.assertIsNotNone(worker)
            self.assertTrue(worker.isRunning())

            panel.on_panel_deactivated()
            self.assertFalse(panel._workbench_shutdown_requested, "panel deactivation is not app cleanup")
            self.assertIs(panel._worker, worker, "deactivation must retain the active QThread reference")

            started = time.monotonic()
            self.assertFalse(panel.workbench_shutdown_cleanup())
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertFalse(panel.workbench_shutdown_cleanup(), "polling must be idempotently pending")
            self.assertTrue(worker.cancelled)
            self.assertIs(panel._worker, worker, "running worker reference must be retained")
            self.assertIn(worker, panel._legacy_workers)
            self.assertIn(worker, ai_module._ACTIVE_WORKBENCH_WORKERS)
            self.assertEqual(panel.result.item(0, 0).text(), "preserved-row")
            self.assertEqual(panel.data_center_agent.cleanup_calls, 0, "Agent cleanup remains independent")

            release.set()
            self._wait_thread_stopped_without_dispatch(worker)
            self.assertFalse(panel.workbench_shutdown_cleanup(), "isRunning false precedes delivered finished cleanup")
            self.assertIn(worker, panel._legacy_workers)
            self.app.processEvents()
            self.assertTrue(panel.workbench_shutdown_cleanup())

        self.assertFalse(worker.isRunning())
        self.assertTrue(connection.closed)
        self.assertEqual(worker_thread_ids, [connection.close_thread])
        self.assertIsNone(panel._worker)
        self.assertNotIn(worker, panel._legacy_workers)
        self.assertNotIn(worker, ai_module._ACTIVE_WORKBENCH_WORKERS)
        self.assertEqual(panel.result.item(0, 0).text(), "preserved-row", "late query results must not repaint after cleanup")
        self.assertEqual(panel.data_center_agent.cleanup_calls, 0, "legacy cleanup must not dispose the Agent host")
        self.assertTrue(panel.workbench_shutdown_cleanup())

    def test_redis_replaced_background_worker_remains_owned_until_both_finish(self):
        panel = self._make_panel(redis_module, redis_module.RedisWorkbenchPanel)
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        connections = [_ClosedConnection(), _ClosedConnection()]
        lock = threading.Lock()
        calls = []

        def open_fake(_item):
            with lock:
                index = len(calls)
                calls.append(index)
            entered[index].set()
            if not release[index].wait(5):
                raise TimeoutError("TEMP Redis worker was not released")
            return connections[index]

        def close_fake(conn):
            if conn is not None:
                conn.closed = True
                conn.close_thread = threading.get_ident()

        with patch.object(redis_module, "open_connection", side_effect=open_fake), patch.object(
            redis_module, "close_connection", side_effect=close_fake
        ):
            panel._run_bg("bootstrap", {"id": "TEMP-redis", "dialect": "redis"})
            self.assertTrue(entered[0].wait(2))
            first = panel._bg_worker

            panel._run_bg("scan", {"id": "TEMP-redis", "dialect": "redis"})
            self.assertTrue(entered[1].wait(2))
            second = panel._bg_worker
            self.assertIsNot(first, second)
            self.assertTrue(first.cancelled)
            self.assertTrue(first.isRunning())
            self.assertIn(first, panel._legacy_workers)
            self.assertIn(second, panel._legacy_workers)

            self.assertFalse(panel.workbench_shutdown_cleanup())
            self.assertTrue(first.cancelled)
            self.assertTrue(second.cancelled)
            self.assertIs(panel._bg_worker, second)
            release[0].set()
            release[1].set()
            self._wait_thread_stopped_without_dispatch(first)
            self._wait_thread_stopped_without_dispatch(second)
            self.assertFalse(panel.workbench_shutdown_cleanup(), "both refs remain until finished slots are delivered")
            self.assertIn(first, panel._legacy_workers)
            self.assertIn(second, panel._legacy_workers)
            self.app.processEvents()
            self.assertTrue(panel.workbench_shutdown_cleanup())

        self.assertFalse(first.isRunning())
        self.assertFalse(second.isRunning())
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertIsNone(panel._bg_worker)
        self.assertEqual(panel._legacy_workers, set())
        self.assertTrue(panel.workbench_shutdown_cleanup())

    def test_mongo_shutdown_cleanup_keeps_blocked_worker_reference(self):
        panel = self._make_panel(mongo_module, mongo_module.MongoDBWorkbenchPanel)
        entered = threading.Event()
        release = threading.Event()
        connection = _ClosedConnection()

        def open_fake(_item):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("TEMP Mongo worker was not released")
            return connection

        def close_fake(conn):
            if conn is not None:
                conn.closed = True
                conn.close_thread = threading.get_ident()

        with patch.object(mongo_module, "open_connection", side_effect=open_fake), patch.object(
            mongo_module, "close_connection", side_effect=close_fake
        ):
            panel._run_worker("test", {"id": "TEMP-mongo", "dialect": "mongodb"})
            self.assertTrue(entered.wait(2))
            worker = panel._worker
            self.assertFalse(panel.workbench_shutdown_cleanup())
            self.assertIs(panel._worker, worker)
            self.assertIn(worker, panel._legacy_workers)
            release.set()
            self._wait_thread_stopped_without_dispatch(worker)
            self.assertFalse(panel.workbench_shutdown_cleanup(), "Mongo ref survives until its finished slot")
            self.assertIn(worker, panel._legacy_workers)
            self.app.processEvents()
            self.assertTrue(panel.workbench_shutdown_cleanup())

        self.assertFalse(worker.isRunning())
        self.assertTrue(connection.closed)
        self.assertIsNone(panel._worker)
        self.assertEqual(panel._legacy_workers, set())

    def test_ai_panel_deactivation_does_not_permanently_gate_later_work(self):
        panel = self._make_panel(ai_module, ai_module.AiWorkbenchPanel)
        panel._browse_conn = lambda: {"id": "TEMP-db", "dialect": "mysql"}
        connection = _ClosedConnection()

        def close_fake(conn):
            if conn is not None:
                conn.closed = True
                conn.close_thread = threading.get_ident()

        panel.on_panel_deactivated()
        self.assertFalse(panel._workbench_shutdown_requested)
        with patch.object(ai_module, "open_connection", return_value=connection), patch.object(
            ai_module, "close_connection", side_effect=close_fake
        ), patch.object(ai_module, "show_info") as show_info:
            panel._start_db("test")
            self._wait_until(lambda: panel._worker is None)
            self.assertEqual(show_info.call_count, 1)

        self.assertTrue(connection.closed)
        self.assertFalse(panel._workbench_shutdown_requested)
        self.assertEqual(panel.data_center_agent.cleanup_calls, 0)

    def test_ai_legacy_draft_worker_is_cancelled_and_retained_until_finished(self):
        panel = self._make_panel(ai_module, ai_module.AiWorkbenchPanel)
        entered = threading.Event()
        release = threading.Event()
        worker_thread_ids = []

        def generate_fake(**_kwargs):
            worker_thread_ids.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise TimeoutError("TEMP fake draft generation was not released")
            return {"sql": "SELECT TEMP"}

        with patch("tools.ai_sql_draft.generate_sql_draft", side_effect=generate_fake):
            panel._start_agent_task({"question": "TEMP"}, "generate")
            self.assertTrue(entered.wait(2), "TEMP AI worker did not enter fake generator")
            worker = panel._ai_worker
            tab_count = panel.sql_tabs.count()
            self.assertFalse(panel.workbench_shutdown_cleanup())
            self.assertTrue(worker.cancelled)
            self.assertIs(panel._ai_worker, worker)
            self.assertIn(worker, panel._legacy_workers)
            self.assertFalse(panel._agent_timer.isActive())

            release.set()
            self._wait_thread_stopped_without_dispatch(worker)
            self.assertFalse(panel.workbench_shutdown_cleanup())
            self.assertIn(worker, panel._legacy_workers)
            self.app.processEvents()
            self.assertTrue(panel.workbench_shutdown_cleanup())

        self.assertFalse(worker.isRunning())
        self.assertIsNone(panel._ai_worker)
        self.assertEqual(panel.sql_tabs.count(), tab_count, "cancelled late draft must not create a SQL tab")
        self.assertEqual(len(worker_thread_ids), 1)
        self.assertNotEqual(worker_thread_ids[0], threading.get_ident(), "draft generation must run off the UI thread")
        self.assertEqual(panel.data_center_agent.cleanup_calls, 0, "legacy AI cleanup remains separate from Agent cleanup")


if __name__ == "__main__":
    unittest.main()
