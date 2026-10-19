from __future__ import annotations

from concurrent.futures import Future
from dataclasses import FrozenInstanceError
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid
import weakref
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFontDatabase, QFontInfo
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from tools.data_center.workspace_store import (
    HistoryEvidenceSummary,
    HistoryRecord,
    WorkspaceStore,
    WorkspaceTarget,
    WorkspaceTabKind,
    WorkspaceTextKind,
)
from ui.theme_manager import ThemeManager
from ui.data_center.query_history import (
    MAX_HISTORY_PENDING_OPERATIONS,
    QueryHistoryBrowser,
    QueryHistoryController,
    QueryHistoryDialog,
    QueryHistoryFailure,
    QueryHistoryOperation,
    QueryHistoryResult,
)


def _history(
    history_id: str,
    text: str,
    *,
    connection_id: str = "conn-a",
    profile_revision: str = "profile-r1",
    created_at: str = "2026-10-06T10:00:00+08:00",
    state_code: str = "SUCCESS",
    row_count: int | None = None,
    evidence_summary: tuple[HistoryEvidenceSummary, ...] = (),
) -> HistoryRecord:
    return HistoryRecord(
        history_id,
        WorkspaceTarget(
            "manual-tab-a",
            connection_id,
            profile_revision,
            "sample_db",
            "app_owner",
            WorkspaceTabKind.SQL,
        ),
        text,
        WorkspaceTextKind.SQL,
        created_at,
        state_code,
        row_count=row_count,
        evidence_summary=evidence_summary,
    )


class _TrackingStore(WorkspaceStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.calls: list[tuple[str, int]] = []
        self.calls_lock = threading.Lock()

    def _record_call(self, operation: str) -> None:
        with self.calls_lock:
            self.calls.append((operation, threading.get_ident()))

    def save_history(self, record: HistoryRecord) -> None:
        self._record_call("save_history")
        super().save_history(record)

    def search_history(self, query="", *, connection_id=None, limit=100):
        self._record_call("search_history")
        return super().search_history(query, connection_id=connection_id, limit=limit)

    def list_favorites(self, *, limit=100):
        self._record_call("list_favorites")
        return super().list_favorites(limit=limit)

    def set_favorite(self, history_id: str, favorite: bool = True) -> None:
        self._record_call("set_favorite")
        super().set_favorite(history_id, favorite)

    def clear_history(self, *, include_favorites: bool = False) -> int:
        self._record_call("clear_history")
        return super().clear_history(include_favorites=include_favorites)


class _BlockingStore(_TrackingStore):
    def __init__(self, path: Path, *, blocked_query: str) -> None:
        super().__init__(path)
        self.blocked_query = blocked_query
        self.entered = threading.Event()
        self.release = threading.Event()

    def search_history(self, query="", *, connection_id=None, limit=100):
        self._record_call("search_history")
        if query == self.blocked_query:
            self.entered.set()
            if not self.release.wait(4.0):
                raise TimeoutError("TEMP history-store barrier timed out")
        return WorkspaceStore.search_history(
            self, query, connection_id=connection_id, limit=limit
        )


class QueryHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.registered_font_families: list[str] = []
        if (
            os.environ.get("QT_QPA_PLATFORM", "").casefold() == "windows"
            and cls.app.platformName().casefold() != "windows"
        ):
            raise AssertionError(
                f"requested Windows QPA but Qt selected {cls.app.platformName()}"
            )
        for font_path in (
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
        ):
            if font_path.is_file():
                font_id = QFontDatabase.addApplicationFont(str(font_path))
                if font_id < 0:
                    raise AssertionError(f"failed to register installed test font: {font_path.name}")
                cls.registered_font_families.extend(
                    QFontDatabase.applicationFontFamilies(font_id)
                )
        ThemeManager.instance().apply(cls.app, "calm")
        if cls.app.property("ui_theme") != "calm":
            raise AssertionError("ThemeManager did not apply the calm application style")

    def setUp(self) -> None:
        self._controllers: list[QueryHistoryController] = []
        self._widgets: list[QWidget] = []
        self._stores: list[WorkspaceStore] = []

    def tearDown(self) -> None:
        for widget in self._widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        for controller in self._controllers:
            future = controller.shutdown()
            self._wait_for(future.done, timeout=3.0)
            self.assertTrue(future.result(timeout=0).completed)
        for store in self._stores:
            store.close()
        self.app.processEvents()

    def _make_store(self, *, blocking_query: str | None = None):
        directory = tempfile.TemporaryDirectory(prefix="pengtools-history-ui-store-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "workspace.sqlite3"
        store = (
            _BlockingStore(path, blocked_query=blocking_query)
            if blocking_query is not None
            else _TrackingStore(path)
        )
        self._stores.append(store)
        return store, path

    def _controller(self, store: WorkspaceStore) -> QueryHistoryController:
        controller = QueryHistoryController(store)
        self._controllers.append(controller)
        return controller

    def _browser(self, controller: QueryHistoryController, *, language="zh") -> QueryHistoryBrowser:
        browser = QueryHistoryBrowser(controller, language=language)
        self._widgets.append(browser)
        return browser

    def _wait_for(self, predicate, *, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(predicate(), "timed out waiting for asynchronous history UI event")

    def _save_and_wait(self, controller: QueryHistoryController, record: HistoryRecord) -> None:
        completed: list[QueryHistoryOperation] = []
        controller.operation_completed.connect(completed.append)
        try:
            request_id = controller.save_history(record)
            self.assertIsInstance(request_id, str)
            self._wait_for(lambda: any(item.request_id == request_id for item in completed))
        finally:
            controller.operation_completed.disconnect(completed.append)

    def test_browser_and_controller_constructors_perform_no_sqlite_io(self) -> None:
        store, path = self._make_store()
        with patch(
            "tools.data_center.workspace_store.sqlite3.connect",
            side_effect=AssertionError("unexpected SQLite I/O"),
        ) as connect:
            controller = self._controller(store)
            self._browser(controller)
            self.app.processEvents()
            self.assertFalse(path.exists())
            connect.assert_not_called()
            future = controller.shutdown()
            self._wait_for(future.done)
            connect.assert_not_called()

    def test_temp_sqlite_search_favorite_clear_and_separate_favorite_confirmation(self) -> None:
        store, _path = self._make_store()
        controller = self._controller(store)
        browser = self._browser(controller)
        records: list[QueryHistoryResult] = []
        operations: list[QueryHistoryOperation] = []
        failures: list[QueryHistoryFailure] = []
        controller.records_ready.connect(records.append)
        controller.operation_completed.connect(operations.append)
        controller.request_failed.connect(failures.append)

        self.assertIsNone(controller.clear_history())
        self.assertIsNone(controller.clear_all_history())
        self.assertEqual(store.calls, [])
        self.assertEqual(
            {failure.code for failure in failures},
            {"CONFIRMATION_REQUIRED"},
        )

        browser.save_history(
            _history("history-account", "SELECT * FROM accounts", connection_id="conn-a")
        )
        browser.save_history(
            _history("history-customer", "SELECT * FROM customers", connection_id="conn-b")
        )
        browser.save_history(
            _history("history-orders", "SELECT * FROM orders", connection_id="conn-a")
        )
        self._wait_for(lambda: sum(item.operation == "save_history" for item in operations) == 3)

        browser.query_edit.setText("accounts")
        browser.connection_edit.setText("conn-a")
        browser.limit_spin.setValue(25)
        browser.search_button.click()
        self._wait_for(
            lambda: bool(records)
            and records[-1].records
            and records[-1].records[0].history_id == "history-account"
        )
        self.assertEqual(records[-1].records[0].target.connection_id, "conn-a")
        self.assertEqual(store.calls[-1][0], "search_history")

        limited_request = controller.search_history("", connection_id="conn-a", limit=1)
        self.assertIsInstance(limited_request, str)
        self._wait_for(lambda: records and records[-1].request_id == limited_request)
        self.assertEqual(len(records[-1].records), 1)
        self.assertEqual(records[-1].view, "history")

        browser.search_button.click()
        self._wait_for(
            lambda: browser.record_list.count() == 1
            and browser.record_list.item(0).text().startswith("SELECT * FROM accounts")
        )

        browser.record_list.setCurrentRow(0)
        self.assertTrue(browser.favorite_button.isEnabled())
        browser.favorite_button.click()
        self._wait_for(lambda: any(item.operation == "favorite" for item in operations))
        self._wait_for(
            lambda: browser.record_list.count() == 1
            and browser.record_list.item(0).data(Qt.ItemDataRole.UserRole).is_favorite
        )

        browser.favorites_button.click()
        self._wait_for(
            lambda: records
            and records[-1].view == "favorites"
            and len(records[-1].records) == 1
        )
        self.assertEqual(records[-1].records[0].history_id, "history-account")

        with patch(
            "ui.data_center.query_history.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            browser.clear_button.click()
        self._wait_for(lambda: any(item.operation == "clear_history" for item in operations))
        self._wait_for(lambda: browser.record_list.count() == 1)
        self.assertTrue(browser.record_list.item(0).data(Qt.ItemDataRole.UserRole).is_favorite)

        with patch(
            "ui.data_center.query_history.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            browser.clear_all_button.click()
        self._wait_for(lambda: any(item.operation == "clear_all" for item in operations))
        self._wait_for(
            lambda: records
            and records[-1].view == "favorites"
            and not records[-1].records
        )
        self.assertEqual(browser.record_list.count(), 0)
        self.assertTrue(store.calls)
        self.assertNotEqual(store.calls[0][1], threading.get_ident())

    def test_restore_emits_original_frozen_record_without_store_or_session_work(self) -> None:
        store, _path = self._make_store()
        controller = self._controller(store)
        browser = self._browser(controller, language="en")
        record = _history(
            "history-old-profile",
            "SELECT id FROM sample_table WHERE id = 7",
            profile_revision="captured-r-old",
            created_at="2026-10-01T04:03:02+08:00",
            state_code="RECORDED",
            row_count=1,
            evidence_summary=(
                HistoryEvidenceSummary("evidence-restored", "query_readonly", "OK", 1, False),
            ),
        )
        browser.save_history(record)
        browser.reload()
        self._wait_for(lambda: browser.record_list.count() == 1)
        browser.record_list.setCurrentRow(0)
        calls_before = tuple(store.calls)
        emitted: list[HistoryRecord] = []
        browser.open_as_new_draft_requested.connect(emitted.append)

        browser.restore_button.click()
        self.app.processEvents()

        self.assertEqual(emitted, [record])
        self.assertIs(emitted[0], browser._selected_record)
        self.assertTrue(emitted[0].__dataclass_params__.frozen)
        with self.assertRaises(FrozenInstanceError):
            emitted[0].text = "UPDATE unrelated_table"
        self.assertEqual(emitted[0].target.profile_revision, "captured-r-old")
        self.assertEqual(emitted[0].created_at, "2026-10-01T04:03:02+08:00")
        self.assertEqual(emitted[0].state_code, "RECORDED")
        self.assertEqual(emitted[0].text, "SELECT id FROM sample_table WHERE id = 7")
        self.assertEqual(emitted[0].row_count, 1)
        self.assertEqual(emitted[0].evidence_summary, record.evidence_summary)
        self.assertEqual(tuple(store.calls), calls_before)

    def test_blocked_owner_has_bounded_nonblocking_queue_and_only_one_sqlite_thread(self) -> None:
        store, _path = self._make_store(blocking_query="blocked")
        controller = self._controller(store)
        results: list[QueryHistoryResult] = []
        failures: list[QueryHistoryFailure] = []
        controller.records_ready.connect(results.append)
        controller.request_failed.connect(failures.append)

        first = controller.search_history("blocked")
        self.assertIsInstance(first, str)
        self.assertTrue(store.entered.wait(2.0))
        gui_thread = threading.get_ident()
        started = time.monotonic()
        request_ids = [
            controller.search_history(f"queued-{index}")
            for index in range(MAX_HISTORY_PENDING_OPERATIONS - 1)
        ]
        elapsed = time.monotonic() - started
        rejected = controller.search_history("one-too-many")
        self.assertLess(elapsed, 0.25)
        self.assertIsNone(rejected)
        self.assertEqual(controller.pending_operations, MAX_HISTORY_PENDING_OPERATIONS)
        self.assertTrue(any(failure.code == "QUEUE_FULL" for failure in failures))
        self.assertEqual(len(store.calls), 1)
        self.assertNotEqual(store.calls[0][1], gui_thread)

        store.release.set()
        self._wait_for(lambda: controller.pending_operations == 0, timeout=4.0)
        self.assertEqual(len({thread_id for _op, thread_id in store.calls}), 1)
        self.assertEqual(len(store.calls), MAX_HISTORY_PENDING_OPERATIONS)
        self.assertEqual(len(request_ids), MAX_HISTORY_PENDING_OPERATIONS - 1)
        self.assertTrue(all(isinstance(request_id, str) for request_id in request_ids))

    def test_late_search_generation_is_discarded_when_a_new_query_arrives(self) -> None:
        store, _path = self._make_store(blocking_query="old")
        controller = self._controller(store)
        self._save_and_wait(controller, _history("history-old", "SELECT old_value"))
        self._save_and_wait(controller, _history("history-new", "SELECT new_value"))
        results: list[QueryHistoryResult] = []
        controller.records_ready.connect(results.append)

        old_request = controller.search_history("old")
        self.assertTrue(store.entered.wait(2.0))
        new_request = controller.search_history("new")
        self.assertIsInstance(old_request, str)
        self.assertIsInstance(new_request, str)
        self.assertGreater(controller.current_generation, 1)
        store.release.set()
        self._wait_for(lambda: controller.pending_operations == 0, timeout=4.0)
        self.assertEqual(len(results), 1)
        self.assertEqual(
            tuple(record.history_id for record in results[0].records),
            ("history-new",),
        )
        self.assertEqual(results[0].request_id, new_request)

    def test_failure_is_visible_as_safe_code_without_query_body_or_exception_text(self) -> None:
        store, _path = self._make_store()
        controller = self._controller(store)
        browser = self._browser(controller, language="en")
        failures: list[QueryHistoryFailure] = []
        controller.request_failed.connect(failures.append)
        secret_marker = "TEMP_PRIVATE_SENTINEL_7264"
        bad_record = _history(
            "history-rejected",
            f"SELECT 1 /* password={secret_marker} */",
        )

        request_id = browser.save_history(bad_record)
        self.assertIsInstance(request_id, str)
        self._wait_for(lambda: failures and failures[-1].request_id == request_id)

        self.assertEqual(failures[-1].code, "CREDENTIAL_TEXT_REJECTED")
        self.assertNotIn(secret_marker, repr(failures[-1]))
        self.assertNotIn(secret_marker, browser.status_label.text())
        self.assertIn("credential", browser.status_label.text().casefold())

    def test_close_retains_worker_store_and_controller_until_blocked_owner_exits(self) -> None:
        store, _path = self._make_store(blocking_query="blocked")
        controller = self._controller(store)
        controller.search_history("blocked")
        self.assertTrue(store.entered.wait(2.0))
        worker = controller._worker
        thread = controller._thread
        cleanup_future: Future = controller.shutdown()
        self.assertTrue(cleanup_future.running())
        self.assertFalse(cleanup_future.cancel())
        self.assertFalse(cleanup_future.done())
        controller_reference = weakref.ref(controller)
        del controller
        gc.collect()

        self.assertIsNotNone(controller_reference())
        self.assertIsNotNone(worker)
        self.assertIsNotNone(store)
        self.assertTrue(thread.isRunning())
        self.assertFalse(cleanup_future.done())
        self.assertTrue(thread.isRunning())

        store.release.set()
        self._wait_for(cleanup_future.done, timeout=4.0)
        self.assertFalse(cleanup_future.cancel())
        self.assertTrue(cleanup_future.result(timeout=0).completed)
        self.assertFalse(thread.isRunning())
        self.assertIsInstance(store.search_history(""), tuple)

    def test_row_count_and_bounded_evidence_details_are_localized_and_complete_in_tooltip(self) -> None:
        store, _path = self._make_store()
        controller = self._controller(store)
        zh_browser = self._browser(controller, language="zh")
        en_browser = self._browser(controller, language="en")
        evidence = (
            HistoryEvidenceSummary("evidence-one", "query_readonly", "OK", 12, False),
            HistoryEvidenceSummary("evidence-two", "describe_object", "OK", None, True),
            HistoryEvidenceSummary("evidence-three", "search_objects", "OK", 3, False),
        )
        self._save_and_wait(
            controller,
            _history("history-unknown-count", "SELECT unknown_count"),
        )
        self._save_and_wait(
            controller,
            _history("history-zero-count", "SELECT zero_count", row_count=0),
        )
        self._save_and_wait(
            controller,
            _history(
                "history-count-evidence",
                "SELECT count_evidence",
                row_count=12,
                evidence_summary=evidence,
            ),
        )
        zh_browser.reload()
        en_browser.reload()
        self._wait_for(lambda: zh_browser.record_list.count() == 3)
        self._wait_for(lambda: en_browser.record_list.count() == 3)

        def select(browser: QueryHistoryBrowser, history_id: str) -> HistoryRecord:
            for index in range(browser.record_list.count()):
                item = browser.record_list.item(index)
                record = item.data(Qt.ItemDataRole.UserRole)
                if record.history_id == history_id:
                    browser.record_list.setCurrentItem(item)
                    self.app.processEvents()
                    return record
            self.fail(f"history record not found: {history_id}")

        unknown = select(zh_browser, "history-unknown-count")
        self.assertIsNone(unknown.row_count)
        self.assertIn("行数：未知", zh_browser.metadata_label.text())
        self.assertIn("证据摘要：无", zh_browser.metadata_label.text())

        zero = select(en_browser, "history-zero-count")
        self.assertEqual(zero.row_count, 0)
        self.assertIn("Row count: 0", en_browser.metadata_label.text())

        counted = select(zh_browser, "history-count-evidence")
        self.assertEqual(counted.row_count, 12)
        self.assertIn("行数：12", zh_browser.metadata_label.text())
        self.assertIn("证据摘要：", zh_browser.metadata_label.text())
        self.assertIn("evidence-one", zh_browser.metadata_label.text())
        self.assertIn("另有 1 项", zh_browser.metadata_label.text())
        self.assertIn("evidence-three", zh_browser.metadata_label.toolTip())
        self.assertIn("数量 12", zh_browser.metadata_label.toolTip())
        self.assertIn("截断 是", zh_browser.metadata_label.toolTip())
        self.assertIn("Evidence summary:", en_browser.record_list.item(0).toolTip())

    def test_calm_960_1160_zh_en_native_layout_captures_keep_controls_and_full_target(self) -> None:
        store, _path = self._make_store()
        controller = self._controller(store)
        record = _history(
            "history-long-target",
            "SELECT " + ", ".join(f"column_{index}" for index in range(500)) + " FROM sample_table",
            connection_id="connection-" + "C" * 72,
            profile_revision="captured-profile-revision-" + "R" * 72,
            row_count=120,
            evidence_summary=(
                HistoryEvidenceSummary("evidence-visible", "query_readonly", "OK", 120, True),
            ),
        )
        self._save_and_wait(controller, record)
        qpa = self.app.platformName().casefold()
        capture_dir = Path(tempfile.gettempdir()) / (
            f"pengtools-query-history-ui-captures-20261006-{qpa}-"
            + uuid.uuid4().hex[:10]
        )
        capture_dir.mkdir(parents=True, exist_ok=False)
        capture_facts: list[dict[str, object]] = []

        for language, width, height, point_size in (
            ("zh", 960, 640, 13),
            ("en", 960, 640, 13),
            ("zh", 1160, 720, 16),
            ("en", 1160, 720, 16),
        ):
            body_pixel_size = 17 if point_size == 13 else 21
            ThemeManager.instance().apply(
                self.app,
                "calm",
                font_size=body_pixel_size,
            )
            browser = self._browser(controller, language=language)
            browser.resize(width, height)
            browser.show()
            self.app.processEvents()
            self.assertEqual(browser.width(), width)
            self.assertEqual(browser.height(), height)
            self._wait_for(lambda: browser.record_list.count() == 1)
            browser.record_list.setCurrentRow(0)
            self.app.processEvents()

            self.assertEqual(browser.property("themeId"), "calm")
            tooltip = browser.target_summary.toolTip()
            self.assertIn(record.target.tab_id, tooltip)
            self.assertIn(record.target.connection_id, tooltip)
            self.assertIn(record.target.profile_revision, tooltip)
            self.assertIn(record.target.database, tooltip)
            self.assertIn(record.target.schema, tooltip)
            self.assertEqual(browser.text_preview.toPlainText(), record.text)
            self.assertIn(
                "行数：120" if language == "zh" else "Row count: 120",
                browser.metadata_label.text(),
            )
            self.assertIn("evidence-visible", browser.metadata_label.toolTip())
            control_fonts = {}
            for control_name, control in (
                ("search", browser.search_button),
                ("metadata", browser.metadata_label),
                ("sql_details", browser.text_preview),
                ("restore", browser.restore_button),
            ):
                resolved_font = QFontInfo(control.font())
                self.assertEqual(resolved_font.pixelSize(), body_pixel_size)
                control_fonts[control_name] = {
                    "family": resolved_font.family(),
                    "pointSize": resolved_font.pointSizeF(),
                    "pixelSize": resolved_font.pixelSize(),
                }
            self.assertLessEqual(browser.target_summary.maximumHeight(), 132)
            self.assertGreater(browser.text_preview.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(browser.search_button.geometry().right(), browser.rect().right())
            for control in (
                browser.favorite_button,
                browser.clear_button,
                browser.clear_all_button,
                browser.restore_button,
            ):
                origin = control.mapTo(browser, QPoint(0, 0))
                self.assertGreater(control.width(), 0)
                self.assertGreater(control.height(), 0)
                self.assertTrue(control.isVisibleTo(browser))
                self.assertGreaterEqual(origin.x(), browser.rect().left())
                self.assertGreaterEqual(origin.y(), browser.rect().top())
                self.assertLessEqual(origin.x() + control.width(), browser.rect().right() + 1)
                self.assertLessEqual(origin.y() + control.height(), browser.rect().bottom() + 1)

            image_path = capture_dir / f"query-history-{language}-{width}-font{point_size}.png"
            image = browser.grab()
            self.assertFalse(image.isNull())
            pixel_ratio = image.devicePixelRatio()
            self.assertEqual(
                (image.width(), image.height()),
                (round(width * pixel_ratio), round(height * pixel_ratio)),
            )
            self.assertTrue(image.save(str(image_path), "PNG"))
            self.assertGreater(image_path.stat().st_size, 1000)
            capture_facts.append(
                {
                    "language": language,
                    "logicalSize": [width, height],
                    "requestedFontPointSize": point_size,
                    "applicationFontSizePx": body_pixel_size,
                    "devicePixelRatio": pixel_ratio,
                    "physicalSize": [image.width(), image.height()],
                    "controls": control_fonts,
                    "path": str(image_path),
                    "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest().upper(),
                }
            )
            browser.close()
            self.app.processEvents()

        parent = QWidget()
        parent.resize(960, 640)
        parent.show()
        self._widgets.append(parent)
        dialog = QueryHistoryDialog(controller, language="en", parent=parent)
        self._widgets.append(dialog)
        dialog.show()
        self.app.processEvents()
        self.assertLessEqual(dialog.width(), parent.frameGeometry().width() - 24)
        self.assertLessEqual(dialog.height(), parent.frameGeometry().height() - 24)
        self.assertLessEqual(dialog.browser.width(), dialog.width() - 16)
        parent.close()
        capture_manifest = {
            "qpaRequested": os.environ.get("QT_QPA_PLATFORM", ""),
            "qpaSelected": self.app.platformName(),
            "applicationTheme": self.app.property("ui_theme"),
            "applicationStyleSheet": "ThemeManager.apply(calm, font_size=per-fixture-px)",
            "registeredApplicationFontFamilies": self.registered_font_families,
            "fixtures": capture_facts,
        }
        manifest_path = capture_dir / "capture-manifest.json"
        manifest_path.write_text(
            json.dumps(capture_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.assertTrue(any(capture_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
