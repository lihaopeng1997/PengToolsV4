"""Production manual-query result export UI tests with TEMP-only DBAPI rows."""

from __future__ import annotations

import csv
import json
import os
from decimal import Decimal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.manual_session import ManualSessionState
from tools.data_center.result_export import (
    ExportScope,
    decode_csv_cell,
    decode_typed_cell,
    write_result_export,
)
from ui.data_center.manual_query import ManualShutdownStatus
from ui.data_center.result_export import ResultExportDialog


def _profile() -> dict:
    return {
        "id": "temp-export-profile",
        "name": "TEMP export connection",
        "provider": "mysql",
        "mode": "mysql",
        "dialect": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "username": "temp-user",
        "password": "TEMP_ENCRYPTED_MARKER",
        "database": "export_db",
    }


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql, parameters=None):
        text = str(sql)
        self.connection.executed.append(text)
        if "FAIL" in text.upper():
            raise RuntimeError("TEMP export query failed")
        if "PAGESET" in text.upper():
            self.description = (("id", "INTEGER", None, None, None, None, True),)
            self.rowcount = 51
            self._rows = [(index,) for index in range(51)]
        elif text.strip().upper().startswith("SELECT"):
            self.description = (
                ("id", "INTEGER", None, None, None, None, True),
                ("amount", "DECIMAL", None, None, None, None, True),
                ("label", "VARCHAR", None, None, None, None, True),
            )
            self.rowcount = 3
            self._rows = [
                (10, Decimal("1.2300"), "alpha"),
                (11, None, ""),
                (12, Decimal("9.99"), "beta"),
            ]
        else:
            self.description = None
            self.rowcount = 0
            self._rows = []

    def fetchmany(self, size):
        barrier = self.connection.next_fetch_barrier
        if barrier is not None:
            self.connection.next_fetch_barrier = None
            entered, release = barrier
            entered.set()
            if not release.wait(4.0):
                raise TimeoutError("TEMP fake fetch barrier timed out")
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows

    def close(self):
        pass


class _FakeConnection:
    def __init__(self):
        self.autocommit_enabled = True
        self.closed = False
        self.executed = []
        self.next_fetch_barrier = None

    def get_autocommit(self):
        return self.autocommit_enabled

    def autocommit(self, enabled):
        self.autocommit_enabled = bool(enabled)

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class ManualQueryResultExportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pengtools-export-ui-")
        self.root = Path(self.temp_dir.name)
        self.profile = _profile()
        self.connection = None
        self.panel = None
        self.host = None
        self._release_events: list[threading.Event] = []

        def connector(profile_row, _secret):
            self.assertEqual(profile_row["id"], self.profile["id"])
            self.connection = _FakeConnection()
            return self.connection

        self.connector = connector
        self.profile_host = DataCenterProfileHost(
            connections_loader=lambda: {"items": [dict(self.profile)]},
        )
        self._create_panel()

    def _create_panel(self, *, export_writer=write_result_export):
        from panels.ai_workbench_panel import AiWorkbenchPanel

        with (
            patch("panels.ai_workbench_panel.load_connections", return_value=[dict(self.profile)]),
            patch("panels.ai_workbench_panel.load_snapshot", return_value=None),
            patch("panels.ai_workbench_panel.is_enabled", return_value=False),
            patch("panels.ai_workbench_panel.load_ai_local", return_value={}),
            patch("panels.ai_workbench_panel.open_connection", side_effect=AssertionError("legacy connector called")),
            patch("panels.ai_workbench_panel.run_console_statement", side_effect=AssertionError("legacy SQL path called")),
        ):
            self.panel = AiWorkbenchPanel(
                connection_id=self.profile["id"],
                dialect="mysql",
                data_center_agent_options={"profile_host": self.profile_host},
                manual_query_options={
                    "profile_host": self.profile_host,
                    "connector": self.connector,
                    "secret_resolver": lambda _opaque: "TEMP_BOUNDARY_SECRET",
                    "page_size": 50,
                    "export_writer": export_writer,
                },
            )
        self.host = self.panel.manual_query_host

    def tearDown(self):
        for event in self._release_events:
            event.set()
        if self.host is not None:
            for _context, worker in tuple(self.host._export_jobs.values()):
                worker.cancel()
            self.assertTrue(self._wait(lambda: not self.host._export_jobs, 4.0), "export QThread must finish before teardown")
            for state in self.host._all_session_states():
                if state.busy:
                    state.session.cancel_current()
            self.assertTrue(
                self._wait(lambda: all(not state.busy for state in self.host._all_session_states()), 4.0),
                "manual owner operation must finish before teardown",
            )
            self.host.manual_shutdown_cleanup("close")
            self.assertTrue(
                self._wait(lambda: self.host.manual_shutdown_cleanup() is ManualShutdownStatus.READY, 4.0),
                "manual owner must finish before teardown",
            )
        if self.panel is not None:
            self.panel.close()
            self.panel.deleteLater()
        self._flush()
        self.temp_dir.cleanup()

    def _flush(self):
        self.app.processEvents()
        self.app.processEvents()

    def _wait(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._flush()
            try:
                if predicate():
                    return True
            except RuntimeError:
                pass
            time.sleep(0.002)
        return bool(predicate())

    def _query(self, sql="SELECT TYPEDSET"):
        state = self.host._states_by_tab[id(self.panel._current_tab())]
        self.panel._current_tab().editor.setPlainText(sql)
        self.host.run_current()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertIsNotNone(state.last_page)
        return state

    def _launch_export(self, *, scope, fmt, path, page_number=None):
        dialogs = []

        def accept(dialog):
            dialogs.append(dialog)
            scope_index = next(
                index for index in range(dialog.scope_combo.count())
                if dialog.scope_combo.itemData(index) == scope.value
            )
            dialog.scope_combo.setCurrentIndex(scope_index)
            format_index = next(
                index for index in range(dialog.format_combo.count())
                if dialog.format_combo.itemData(index) == fmt
            )
            dialog.format_combo.setCurrentIndex(format_index)
            if page_number is not None:
                page_index = next(
                    index for index in range(dialog.page_combo.count())
                    if dialog.page_combo.itemData(index) == page_number
                )
                dialog.page_combo.setCurrentIndex(page_index)
            dialog.path_edit.setText(str(path))
            dialog._accept_export()
            return dialog.result()

        with patch.object(ResultExportDialog, "exec", accept):
            self.host.export_current()
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0].result(), QDialog.DialogCode.Accepted)
        if scope is ExportScope.FULL_QUERY:
            self.assertTrue(self.host._current_state().busy)
        else:
            self.assertEqual(len(self.host._export_jobs), 1)
        return dialogs[0]

    def _wait_export(self):
        self.assertTrue(self._wait(lambda: not self.host._export_jobs, 4.0), "export worker must reach QThread.finished")

    def test_selected_rows_json_export_uses_typed_user_role_and_does_not_requery(self):
        state = self._query()
        view = self.host.result_view
        selection = view.selectionModel()
        selection.select(
            self.host.result_model.index(0, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection.select(
            self.host.result_model.index(2, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        path = self.root / "selected.json"
        dialog = self._launch_export(scope=ExportScope.SELECTED, fmt="json", path=path)
        self.assertEqual(dialog.plan.snapshot.rows, ((10, Decimal("1.2300"), "alpha"), (12, Decimal("9.99"), "beta")))
        self._wait_export()

        document = json.loads(path.read_text(encoding="utf-8"))
        decoded = [tuple(decode_typed_cell(cell) for cell in row) for row in document["rows"]]
        self.assertEqual(decoded, [(10, Decimal("1.2300"), "alpha"), (12, Decimal("9.99"), "beta")])
        self.assertEqual(document["scope"], "selected")
        self.assertEqual(self.connection.executed, ["SELECT TYPEDSET"])
        self.assertEqual(state.session.result_source_status.query_id, state.last_page.query_id)

    def test_loaded_page_selector_exports_only_requested_page_as_csv(self):
        state = self._query("SELECT PAGESET")
        self.host.fetch_next_page()
        self.assertTrue(self._wait(lambda: not state.busy and len(state.pages) == 2))
        path = self.root / "page.csv"
        dialog = self._launch_export(
            scope=ExportScope.CURRENT_PAGE,
            fmt="csv",
            path=path,
            page_number=2,
        )
        self.assertEqual(dialog.plan.snapshot.page_number, 2)
        self.assertEqual(dialog.plan.snapshot.rows, ((50,),))
        self.assertIn("第 2 页", dialog.preview_label.text())
        self._wait_export()

        with path.open("r", encoding="utf-8", newline="") as stream:
            records = list(csv.reader(stream))
        self.assertEqual(records[0], ["id"])
        self.assertEqual(decode_csv_cell(records[1][0]), 50)
        self.assertEqual(self.connection.executed, ["SELECT PAGESET"])

    def test_last_good_page_remains_exportable_after_a_later_query_fails(self):
        state = self._query()
        last_good_pages = state.pages
        self.panel._current_tab().editor.setPlainText("SELECT FAIL")
        self.host.run_current()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertIs(state.pages, last_good_pages)
        self.assertEqual(self.host.result_model.pages, last_good_pages)
        self.assertNotIn("TEMP export query failed", self.panel.result_status.text())

        path = self.root / "last-good.csv"
        self._launch_export(scope=ExportScope.CURRENT_PAGE, fmt="csv", path=path, page_number=1)
        self._wait_export()
        with path.open("r", encoding="utf-8", newline="") as stream:
            records = list(csv.reader(stream))
        self.assertEqual(decode_csv_cell(records[1][0]), 10)
        self.assertEqual(self.connection.executed, ["SELECT TYPEDSET", "SELECT FAIL"])

    def test_open_dialog_rejects_snapshot_after_visible_result_model_changes(self):
        state = self._query()
        path = self.root / "stale-dialog.csv"

        def accept_after_tab_switch(dialog):
            page_index = next(
                index for index in range(dialog.scope_combo.count())
                if dialog.scope_combo.itemData(index) == ExportScope.CURRENT_PAGE.value
            )
            dialog.scope_combo.setCurrentIndex(page_index)
            dialog.path_edit.setText(str(path))
            second_tab = self.panel._new_sql_tab()
            self.assertIs(self.panel._current_tab(), second_tab)
            self.assertEqual(self.host.result_model.pages, ())
            dialog._accept_export()
            return dialog.result()

        with patch.object(ResultExportDialog, "exec", accept_after_tab_switch):
            self.host.export_current()

        self.assertFalse(path.exists())
        self.assertEqual(self.host._export_jobs, {})
        self.assertIsNotNone(state.last_page)
        self.assertEqual(self.host.result_model.pages, ())

    def test_open_dialog_rejects_snapshot_after_a_new_query_replaces_the_result(self):
        state = self._query()
        old_query_id = state.last_page.query_id
        path = self.root / "replaced-query.csv"

        def accept_after_new_query(dialog):
            page_index = next(
                index for index in range(dialog.scope_combo.count())
                if dialog.scope_combo.itemData(index) == ExportScope.CURRENT_PAGE.value
            )
            dialog.scope_combo.setCurrentIndex(page_index)
            dialog.path_edit.setText(str(path))
            self.panel._current_tab().editor.setPlainText("SELECT PAGESET")
            self.host.run_current()
            self.assertTrue(self._wait(lambda: not state.busy))
            self.assertNotEqual(state.last_page.query_id, old_query_id)
            dialog._accept_export()
            return dialog.result()

        with patch.object(ResultExportDialog, "exec", accept_after_new_query):
            self.host.export_current()

        self.assertFalse(path.exists())
        self.assertEqual(self.host._export_jobs, {})
        self.assertEqual(self.connection.executed, ["SELECT TYPEDSET", "SELECT PAGESET"])
        self.assertEqual(self.host.result_model.pages, state.pages)

    def test_full_query_export_uses_the_original_manual_owner_cursor_without_reexecuting_sql(self):
        state = self._query("SELECT PAGESET")
        before = state.session.result_source_status
        self.assertTrue(before.can_export_full)
        self.assertEqual(before.query_id, state.last_page.query_id)
        path = self.root / "full.json"
        dialog = self._launch_export(
            scope=ExportScope.FULL_QUERY,
            fmt="json",
            path=path,
        )
        self.assertTrue(dialog.scope_combo.model().item(2).isEnabled())
        self.assertIsNone(dialog.plan.snapshot)
        self.assertTrue(dialog.preview_label.text().startswith("完整查询："))
        self.assertTrue(self._wait(lambda: not state.busy, 4.0))
        self.assertTrue(path.exists())

        document = json.loads(path.read_text(encoding="utf-8"))
        values = [decode_typed_cell(row[0]) for row in document["rows"]]
        self.assertEqual(values, list(range(51)))
        self.assertEqual(document["scope"], "full_query")
        self.assertEqual(self.connection.executed, ["SELECT PAGESET"])
        self.assertEqual(state.session.result_source_status.state.value, "EXPORTED")
        self.assertFalse(state.session.result_source_status.can_export_full)

    def test_full_query_preview_uses_plain_localized_scope_and_captured_target(self):
        state = self._query("SELECT TYPEDSET")
        self.panel.language = "en"
        self.host.refresh_language()
        context = self.host._capture_export_context(state)
        self.assertIsNotNone(context)
        self.assertTrue(context.full_query_available)

        dialog = ResultExportDialog(
            self.panel,
            target=context.target,
            pages=context.pages,
            selected_snapshot=context.selected_snapshot,
            connection_name=context.connection_name,
            database=context.database,
            schema=context.schema,
            language="en",
            full_query_available=context.full_query_available,
            full_preview=context.full_preview,
        )
        full_index = next(
            index for index in range(dialog.scope_combo.count())
            if dialog.scope_combo.itemData(index) == ExportScope.FULL_QUERY.value
        )
        dialog.scope_combo.setCurrentIndex(full_index)

        self.assertTrue(dialog.scope_combo.model().item(full_index).isEnabled())
        self.assertEqual(dialog.preview_label.text(), "Full query: 1 page, 3 rows.")
        self.assertIn("Connection: TEMP export connection", dialog.target_label.text())
        self.assertIn("Database: export_db", dialog.target_label.text())
        self.assertNotIn("Schema:", dialog.target_label.text())
        self.assertNotIn(context.target.query_id, dialog.target_label.text())
        self.assertNotIn(context.target.tab_id, dialog.target_label.text())
        self.assertIn("Schema: owner_a", dialog._target_text("Connection A", "service_a", "owner_a"))

    def test_xlsx_export_roundtrips_typed_sidecar_for_the_selected_page(self):
        self._query()
        path = self.root / "typed.xlsx"
        self._launch_export(scope=ExportScope.CURRENT_PAGE, fmt="xlsx", path=path, page_number=1)
        self._wait_export()

        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            metadata = list(workbook["_PT_EXPORT_V1"].iter_rows(values_only=True))
            entries = [row for row in metadata[2:] if row[0] == 0 and row[1] == 1]
            self.assertEqual(decode_typed_cell(json.loads(entries[0][2])), Decimal("1.2300"))
            self.assertEqual(workbook["_PT_EXPORT_V1"].sheet_state, "veryHidden")
        finally:
            workbook.close()

    def test_cancel_exit_keeps_export_owner_until_real_thread_finish_then_closes_session(self):
        started = threading.Event()
        release = threading.Event()
        self._release_events.append(release)

        def blocked_writer(*args, **kwargs):
            started.set()
            if not release.wait(4.0):
                raise TimeoutError("TEMP export test barrier timed out")
            return write_result_export(*args, **kwargs)

        self.host._export_writer = blocked_writer
        state = self._query()
        path = self.root / "cancelled.json"
        self._launch_export(scope=ExportScope.CURRENT_PAGE, fmt="json", path=path, page_number=1)
        self.assertTrue(started.wait(1.0))
        context, worker = next(iter(self.host._export_jobs.values()))
        self.assertTrue(worker.isRunning())
        snapshot = self.host.manual_shutdown_snapshot()
        self.assertTrue(any(entry.tab_id == state.target.tab_id and entry.busy for entry in snapshot.sessions))
        self.assertFalse(snapshot.can_commit)
        self.assertIs(self.host.manual_shutdown_cleanup(), ManualShutdownStatus.PENDING)

        self.assertIs(self.host.manual_shutdown_cleanup("cancel"), ManualShutdownStatus.CANCELLED)
        self.assertFalse(worker.cancel_event.is_set(), "cancelling application close must preserve the export job")
        self.assertFalse(self.connection.closed, "cancelled close must preserve the database session")
        self.assertTrue(self.host.request_tab_close(self.panel.sql_tabs.currentIndex()))
        self.assertEqual(self.panel.sql_tabs.count(), 1)

        self.host.cancel_current()
        self.assertTrue(worker.cancel_event.is_set())
        self.assertIs(self.host.manual_shutdown_cleanup("close"), ManualShutdownStatus.PENDING)
        self.assertIn(worker.request_id, self.host._export_jobs, "cancel request does not release the running QThread")
        release.set()
        self._wait_export()
        self.assertFalse(path.exists())
        self.assertIs(self.host.manual_shutdown_cleanup("close"), ManualShutdownStatus.PENDING)
        self.assertTrue(self._wait(lambda: self.host.manual_shutdown_cleanup() is ManualShutdownStatus.READY, 4.0))
        self.assertTrue(self.connection.closed)

    def test_full_export_stop_keeps_manual_owner_pending_until_driver_returns(self):
        self.panel.language = "en"
        self.host.refresh_language()
        state = self._query("SELECT PAGESET")
        entered, release = threading.Event(), threading.Event()
        self._release_events.append(release)
        self.connection.next_fetch_barrier = (entered, release)
        path = self.root / "stopped-full.json"
        self._launch_export(scope=ExportScope.FULL_QUERY, fmt="json", path=path)
        self.assertTrue(entered.wait(1.0))
        self.assertTrue(state.busy)
        self.assertIs(self.host.manual_shutdown_cleanup(), ManualShutdownStatus.PENDING)
        self.assertIs(self.host.manual_shutdown_cleanup("cancel"), ManualShutdownStatus.CANCELLED)
        self.assertIs(state.session.state, ManualSessionState.CLEAN)
        self.assertFalse(self.connection.closed)

        original_tab_index = self.panel.sql_tabs.indexOf(state.tab)
        self.assertTrue(self.host.request_tab_close(original_tab_index))
        self.assertEqual(self.panel.sql_tabs.count(), 1)
        second_tab = self.panel._new_sql_tab()
        self.assertIs(self.panel._current_tab(), second_tab)
        self.assertTrue(self.host.stop_btn.isEnabled())
        self.host.stop_btn.click()
        stop_status = self.panel.result_status.text()
        self.assertIn("another tab", stop_status)
        self.assertIn("current operation", stop_status)
        self.assertNotIn("driver", stop_status.casefold())
        self.assertNotIn("owner", stop_status.casefold())
        self.assertIs(self.host.manual_shutdown_cleanup("close"), ManualShutdownStatus.PENDING)
        release.set()
        self.assertTrue(self._wait(lambda: not state.busy, 4.0))
        self.assertFalse(path.exists())
        self.assertEqual(self.connection.executed, ["SELECT PAGESET"])
        self.assertFalse(self.connection.closed)

    def test_tab_switch_keeps_export_bound_to_original_result_and_status(self):
        started = threading.Event()
        release = threading.Event()
        self._release_events.append(release)

        def blocked_writer(*args, **kwargs):
            started.set()
            if not release.wait(4.0):
                raise TimeoutError("TEMP export test barrier timed out")
            return write_result_export(*args, **kwargs)

        self.host._export_writer = blocked_writer
        original_state = self._query()
        path = self.root / "switch.json"
        self._launch_export(scope=ExportScope.CURRENT_PAGE, fmt="json", path=path, page_number=1)
        self.assertTrue(started.wait(1.0))
        original_result_pages = original_state.pages

        second_tab = self.panel._new_sql_tab()
        self.assertIs(self.panel._current_tab(), second_tab)
        second_state = self.host._states_by_tab[id(second_tab)]
        current_status = self.panel.result_status.text()
        release.set()
        self._wait_export()

        self.assertTrue(path.exists())
        self.assertIs(self.panel._current_tab(), second_tab)
        self.assertEqual(second_state.export_status, "")
        self.assertEqual(self.panel.result_status.text(), current_status)
        self.assertEqual(original_state.pages, original_result_pages)

    def test_dialog_uses_user_scope_preview_and_requires_explicit_overwrite(self):
        state = self._query()
        view = self.host.result_view
        view.selectRow(0)
        context = self.host._capture_export_context(state)
        dialog = ResultExportDialog(
            self.panel,
            target=context.target,
            pages=context.pages,
            selected_snapshot=context.selected_snapshot,
            connection_name=context.connection_name,
            database=context.database,
            schema=context.schema,
            language="en",
            full_query_available=False,
        )
        self.assertTrue(dialog.scope_combo.model().item(2).isEnabled() is False)
        self.assertIn("Connection:", dialog.target_label.text())
        self.assertIn("Database: export_db", dialog.target_label.text())
        self.assertIn("selected rows", dialog.preview_label.text())
        self.assertNotIn(context.target.query_id, dialog.target_label.text())

        existing = self.root / "existing.csv"
        existing.write_text("keep", encoding="utf-8")

        def decline_replacement(replacement_dialog):
            page_scope = next(
                index for index in range(replacement_dialog.scope_combo.count())
                if replacement_dialog.scope_combo.itemData(index) == ExportScope.CURRENT_PAGE.value
            )
            replacement_dialog.scope_combo.setCurrentIndex(page_scope)
            replacement_dialog.path_edit.setText(str(existing))
            with patch(
                "ui.data_center.result_export.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ):
                replacement_dialog._accept_export()
            self.assertIsNone(replacement_dialog.plan)
            return replacement_dialog.result()

        with patch.object(ResultExportDialog, "exec", decline_replacement):
            self.host.export_current()
        self.assertEqual(self.host._export_jobs, {})
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
