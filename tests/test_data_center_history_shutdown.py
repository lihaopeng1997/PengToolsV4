"""Application history persistence and actual-worker-finished drain tests."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from tools.data_center.workspace_store import WorkspaceStore, WorkspaceTextKind, WorkspaceTabKind
from tools.data_center.manual_session import ManualColumn, ManualQueryPage
from ui.data_center.query_history_service import (
    DataCenterHistoryService,
    make_history_capture,
)


class _WorkerSignals(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()


class DataCenterHistoryShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, predicate, timeout: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.002)
        self.app.processEvents()
        return bool(predicate())

    def _capture(self):
        capture = make_history_capture(
            "SELECT 7",
            connection_id="temp-history-profile",
            profile_revision="revision-1",
            database="TEMP_SERVICE",
            schema="APP_OWNER",
            runtime_tab_id="temp-sql-tab",
            generation=3,
            text_kind=WorkspaceTextKind.SQL,
            tab_kind=WorkspaceTabKind.SQL,
        )
        self.assertIsNotNone(capture)
        return capture

    def _records(self, path: str):
        store = WorkspaceStore(path)
        try:
            return store.search_history("SELECT 7")
        finally:
            store.close()

    def test_constructor_is_lazy_and_real_worker_finish_saves_once_before_store_close(self):
        with tempfile.TemporaryDirectory(prefix="pengtools-history-shutdown-") as folder:
            path = os.path.join(folder, "workspace.sqlite3")
            service = DataCenterHistoryService(path)
            self.assertFalse(os.path.exists(path), "service construction must not initialize persistence")

            worker = _WorkerSignals()
            service.attach_legacy_worker(worker, self._capture())
            self.assertFalse(service.drain(), "an attached worker must keep the shared store open")
            self.assertFalse(service.closing)

            worker.completed.emit({"status": "OK", "rowcount": 7, "rows": [["not captured"]]})
            worker.failed.emit("TEMP raw error must not be captured")
            worker.completed.emit({"status": "ERROR", "rowcount": 99})
            worker.finished.emit()

            self.assertTrue(self._wait(service.drain))
            self.assertTrue(service._store_closed)
            rows = self._records(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].text, "SELECT 7")
            self.assertEqual(rows[0].state_code, "COMPLETED")
            self.assertEqual(rows[0].row_count, 7)
            self.assertEqual(rows[0].target.database, "TEMP_SERVICE")
            self.assertEqual(rows[0].target.schema, "APP_OWNER")
            self.assertEqual(rows[0].evidence_summary, ())

    def test_missing_terminal_signal_is_unknown_and_shutdown_waits_for_actual_finished(self):
        with tempfile.TemporaryDirectory(prefix="pengtools-history-unknown-") as folder:
            path = os.path.join(folder, "workspace.sqlite3")
            service = DataCenterHistoryService(path)
            worker = _WorkerSignals()
            service.attach_legacy_worker(worker, self._capture())

            self.assertFalse(service.drain())
            self.assertFalse(service.closing, "do not reject the late UNKNOWN record")
            self.assertFalse(service._store_closed)
            worker.finished.emit()

            self.assertTrue(self._wait(service.drain))
            rows = self._records(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].state_code, "UNKNOWN")
            self.assertIsNone(rows[0].row_count)

    def test_manual_future_is_persisted_by_app_sink_after_ui_owner_is_irrelevant(self):
        with tempfile.TemporaryDirectory(prefix="pengtools-history-manual-sink-") as folder:
            path = os.path.join(folder, "workspace.sqlite3")
            service = DataCenterHistoryService(path)
            future: Future = Future()
            capture = self._capture()
            service.track_manual_future(
                future,
                capture,
                target_tab_id="manual-session-tab",
                generation=3,
                expected_query_id="query-original",
            )
            self.assertFalse(service.drain(), "a registered operation must hold the drain barrier")
            self.assertFalse(service.closing)

            page = ManualQueryPage(
                query_id="query-original",
                tab_id="manual-session-tab",
                generation=3,
                columns=(ManualColumn("value", "INTEGER"),),
                rows=(("TEMP RESULT VALUE",),),
                page_number=1,
                page_size=50,
                has_more=False,
                result_bytes=18,
                rowcount=6,
            )
            producer = threading.Thread(target=lambda: future.set_result(page))
            producer.start()
            producer.join(2.0)
            self.assertFalse(producer.is_alive())
            self.assertTrue(self._wait(lambda: not service._manual_pending))

            self.assertTrue(self._wait(service.drain))
            rows = self._records(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].history_id, capture.history_id)
            self.assertEqual(rows[0].text, "SELECT 7")
            self.assertEqual(rows[0].state_code, "COMPLETED")
            self.assertEqual(rows[0].row_count, 6)
            self.assertNotIn("TEMP RESULT VALUE", repr(rows[0]))

    def test_two_borrowed_controllers_share_store_and_retirement_waits_for_qthread_finished(self):
        with tempfile.TemporaryDirectory(prefix="pengtools-history-borrowers-") as folder:
            path = os.path.join(folder, "workspace.sqlite3")
            service = DataCenterHistoryService(path)
            first = service.new_controller()
            second = service.new_controller()
            self.assertIs(first._store, service.store)
            self.assertIs(second._store, service.store)

            first_cleanup = service.retire_controller(first)
            self.assertTrue(self._wait(first_cleanup.done))
            self.assertFalse(first._thread.isRunning())
            self.assertFalse(service._store_closed, "retiring one borrower must not close the app-owned store")

            results = []
            second.records_ready.connect(results.append)
            self.assertIsNotNone(second.search_history(""))
            self.assertTrue(self._wait(lambda: bool(results)))
            self.assertTrue(self._wait(service.drain))
            self.assertFalse(second._thread.isRunning())
            self.assertTrue(service._store_closed)

    def test_failed_shutdown_request_retains_store_and_retries_for_finished_proof(self):
        with tempfile.TemporaryDirectory(prefix="pengtools-history-shutdown-retry-") as folder:
            path = os.path.join(folder, "workspace.sqlite3")
            service = DataCenterHistoryService(path)
            controller = service.new_controller()
            original_shutdown = controller.shutdown
            calls = 0

            def fail_once():
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("TEMP injected shutdown request failure")
                return original_shutdown()

            controller.shutdown = fail_once
            self.assertFalse(service.drain())
            self.assertEqual(service.cleanup_error_code, "CONTROLLER_CLEANUP_PENDING")
            self.assertIn(controller, service._controllers)
            self.assertFalse(service._store_closed)

            self.assertFalse(service.drain())
            self.assertTrue(self._wait(service.drain))
            self.assertGreaterEqual(calls, 2)
            self.assertTrue(controller._thread.isFinished())
            self.assertTrue(service._store_closed)


if __name__ == "__main__":
    unittest.main()
