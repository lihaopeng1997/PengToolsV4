from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import Workbook
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from tools.data_center.import_preview import (
    ColumnMapping,
    CsvSource,
    ImportLimits,
    ImportValueKind,
    NullRule,
    TargetColumn,
    XlsxSource,
)
from tools.data_center.manual_session import ManualQueryPage, ManualSessionTarget
from tools.data_center.object_tree import ObjectTreeService, TreeTarget
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.table_source import (
    TableColumnMetadata,
    TableSourceMetadata,
    TableSourceOpenResult,
)
from ui.data_center.import_controller import (
    ImportController,
    ImportSessionRegistry,
    ImportTableContext,
)
from ui.data_center.import_dialog import ManualImportDialog
import ui.data_center.import_controller as import_controller_module

from tests.test_data_center_import_session import (
    _DirectoryHost,
    _MySqlProviderConnection,
    _MySqlProviderCursor,
)


class _BlockingMySqlProviderCursor(_MySqlProviderCursor):
    def executemany(self, sql, rows):
        self.connection.started_insert.set()
        if not self.connection.release_insert.wait(5):
            raise TimeoutError("temporary provider insert wait timed out")
        return super().executemany(sql, rows)


class _BlockingMySqlProviderConnection(_MySqlProviderConnection):
    def __init__(self):
        super().__init__()
        self.started_insert = threading.Event()
        self.release_insert = threading.Event()

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _BlockingMySqlProviderCursor(self)


class _CloseFailingMySqlProviderConnection(_MySqlProviderConnection):
    def __init__(self):
        super().__init__()
        self.close_failures = 1

    def close(self):
        if self.close_failures:
            self.close_failures -= 1
            self.events.append(("connection_close_failed", threading.get_ident()))
            raise RuntimeError("temporary close failure")
        super().close()


class ImportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pengtools-import-ui-")
        self.root = Path(self.temp.name)
        self.registry = ImportSessionRegistry()
        self.profile = {
            "id": "fixture-import-connection",
            "provider": "mysql",
            "engine": "mysql",
            "mode": "mysql",
            "dialect": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "username": "TEMP-USER",
            "password": "TEMP-CIPHER",
            "database": "sales db",
        }
        self.profile_host = DataCenterProfileHost(
            connections_loader=lambda: [dict(self.profile)],
        )
        self.choice = self.profile_host.capture_connection(
            self.profile, database="sales db",
        )
        directory_host = _DirectoryHost()
        directory_host.connection_id = self.choice.connection_id
        directory_host.revision = self.choice.revision
        directory_host.database = self.choice.database
        self.tree = ObjectTreeService(relational_host=directory_host)
        self.tree_target = TreeTarget(
            self.choice.connection_id,
            self.choice.revision,
            "mysql",
            mode="mysql",
            database=self.choice.database,
        )
        root = self.tree.root(self.tree_target)
        databases = self.tree.submit_children(
            self.tree_target, root, generation=41,
        ).future.result(timeout=3)
        database = next(item for item in databases.nodes if item.kind == "database")
        tables = self.tree.submit_children(
            self.tree_target, database, generation=41,
        ).future.result(timeout=3)
        self.node = next(item for item in tables.nodes if item.kind == "table")
        self.table_target = ManualSessionTarget(
            "display-tab",
            self.choice.connection_id,
            self.choice.revision,
            database=self.choice.database,
            schema=self.choice.database,
            generation=7,
        )
        self.ref = self.tree.resolve_table_source(
            self.tree_target, self.node, generation=41,
        )
        self.columns = (
            TableColumnMetadata(
                "id", "bigint unsigned", False, True, True,
                False, True, False,
            ),
            TableColumnMetadata(
                "name", "varchar(20)", False, False, False,
                True, True, True, data_length=20,
            ),
            TableColumnMetadata(
                "amount", "decimal(6,2)", True, False, False,
                True, True, False, data_precision=6, data_scale=2,
            ),
            TableColumnMetadata(
                "created", "date", True, False, False,
                True, True, False,
            ),
        )
        metadata = TableSourceMetadata(
            self.table_target, self.ref, self.columns, (), True, None, "",
        )
        page = ManualQueryPage(
            "fixture-query", self.table_target.tab_id, self.table_target.generation,
            (), (), 1, 20, False, 0,
        )
        self.table_result = TableSourceOpenResult(metadata, page)
        self.connections: list[_MySqlProviderConnection] = []
        self.connector_calls: list[tuple[str, str]] = []

        def connector(profile, secret):
            self.connector_calls.append((profile["database"], secret))
            connection = self.connections.pop(0)
            return connection

        self.context = ImportTableContext(
            self.table_result,
            self.tree,
            self.tree_target,
            self.node,
            41,
            self.choice,
            self.profile_host,
            connector=connector,
            secret_resolver=lambda _opaque: "TEMP-SECRET",
        )
        self.controllers: list[ImportController] = []
        self.dialogs: list[ManualImportDialog] = []

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog.isVisible():
                dialog.close()
        for controller in self.controllers:
            controller.request_close()
        self.tree.close().result(timeout=3)
        self.temp.cleanup()

    def _controller(self, *, limits: ImportLimits | None = None) -> ImportController:
        controller = ImportController(
            self.context, registry=self.registry, limits=limits or ImportLimits(max_preview_rows=20),
        )
        self.controllers.append(controller)
        return controller

    def _dialog(self, controller: ImportController, language: str = "en") -> ManualImportDialog:
        dialog = ManualImportDialog(None, controller=controller, language=language)
        self.dialogs.append(dialog)
        return dialog

    def _pump_until(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(predicate(), "asynchronous import UI operation did not finish")

    def _inspect_csv(self, controller: ImportController, path: Path) -> None:
        self.assertTrue(controller.inspect_source(CsvSource(path, encoding="utf-8", delimiter=",")))
        self._pump_until(lambda: controller.progress().state in {"SOURCE_READY", "PREVIEW_FAILED"})
        self.assertEqual(controller.progress().state, "SOURCE_READY", controller.progress().safe_code)

    def _preview_default_mapping(self, controller: ImportController) -> None:
        preview = controller.source_preview
        self.assertIsNotNone(preview)
        candidates = {item.name: item.target for item in controller.target_candidates.columns}
        mapping = tuple(
            ColumnMapping(column.index, column.label)
            for column in preview.columns
            if column.label in candidates and candidates[column.label] is not None
        )
        targets = tuple(candidates[item.target_name] for item in mapping)
        self.assertTrue(controller.preview_mapping(
            mapping, targets, NullRule(("NULL",), False, ("NULL",), False, True),
        ))
        self._pump_until(lambda: controller.progress().state in {"PREVIEW_READY", "PREVIEW_FAILED"})

    def _start_import(self, controller: ImportController, path: Path) -> None:
        self._inspect_csv(controller, path)
        self._preview_default_mapping(controller)
        self.assertEqual(controller.progress().state, "PREVIEW_READY", controller.progress().safe_code)
        self.assertTrue(controller.approve_and_start(user_approved=True), controller.progress().safe_code)
        self._pump_until(lambda: controller.progress().state in {
            "READY_TO_COMMIT", "COMMITTED", "ROLLED_BACK", "FAILED", "UNKNOWN",
        })

    def test_csv_duplicate_headers_are_indexed_and_null_date_decimal_preview_is_typed(self):
        source_path = self.root / "duplicate.csv"
        source_path.write_text(
            "name,amount,amount,created\n"
            "Alpha,1.20,unused,2026-10-01\n"
            "Beta,NULL,unused,NULL\n"
            "Gamma,,unused,2026-10-02\n",
            encoding="utf-8",
            newline="",
        )
        controller = self._controller(limits=ImportLimits(max_preview_rows=10))
        callback_threads: list[int] = []
        controller.progress_changed.connect(lambda _progress: callback_threads.append(threading.get_ident()))
        dialog = self._dialog(controller)
        dialog.path_edit.setText(str(source_path))
        dialog._inspect_source()
        self._pump_until(lambda: controller.progress().state == "SOURCE_READY")
        self.assertEqual(dialog.mapping_table.rowCount(), 4)
        source_indexes = [
            dialog.mapping_table.item(row, 0).data(256)
            for row in range(dialog.mapping_table.rowCount())
        ]
        self.assertEqual(source_indexes, [0, 1, 2, 3])
        amount_combos = [
            dialog.mapping_table.cellWidget(row, 2)
            for row in (1, 2)
        ]
        self.assertEqual(amount_combos[0].currentData().name, "amount")
        self.assertIsNone(amount_combos[1].currentData())
        self.assertEqual(dialog.mapping_table.horizontalHeaderItem(2).text(), "Target field (* required)")
        self.assertEqual(dialog.mapping_table.cellWidget(0, 2).currentText(), "name · TEXT *")
        self.assertEqual(dialog.mapping_table.cellWidget(0, 2).currentData(Qt.ItemDataRole.ToolTipRole),
                         "name · TEXT · Required on insert")
        self.assertEqual(amount_combos[0].currentText(), "amount · DEC")
        self.assertEqual(amount_combos[0].currentData(Qt.ItemDataRole.ToolTipRole), "amount · DECIMAL(6,2)")

        dialog._preview_mapping()
        self._pump_until(lambda: controller.progress().state == "PREVIEW_READY")
        self.assertTrue(callback_threads)
        self.assertEqual(set(callback_threads), {threading.get_ident()})
        self.assertEqual(len(controller.preview.issues), 1)
        self.assertEqual(controller.preview.issues[0].row_index, 3)
        self.assertEqual(controller.preview.rows[0].values[0], "Alpha")
        self.assertEqual(controller.preview.rows[0].values[1], Decimal("1.20"))
        self.assertEqual(controller.preview.rows[0].values[2], date(2026, 10, 1))

        dialog.csv_empty_null.setChecked(True)
        self.assertEqual(controller.progress().state, "SOURCE_READY")
        dialog._preview_mapping()
        self._pump_until(lambda: controller.progress().state == "PREVIEW_READY")
        self.assertEqual(controller.preview.issues, ())
        self.assertIsNone(controller.preview.rows[1].values[1])
        self.assertIsNone(controller.preview.rows[1].values[2])
        self.assertIsNone(controller.preview.rows[2].values[1])

        replacement = self.root / "replacement.csv"
        replacement.write_text("name,amount\nDelta,4.50\n", encoding="utf-8")
        dialog.path_edit.setText(str(replacement))
        self.assertEqual(controller.progress().state, "EMPTY")
        self.assertIsNone(controller.source_preview)
        self.assertEqual(dialog.mapping_table.rowCount(), 0)

    def test_xlsx_named_sheet_preview_preserves_decimal_date_and_blank_null(self):
        path = self.root / "fixture.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "ImportData"
        sheet.append(("name", "amount", "created"))
        sheet.append(("Alpha", Decimal("2.35"), date(2026, 10, 2)))
        sheet.append(("Beta", None, date(2026, 10, 3)))
        sheet.append(("Gamma", Decimal("3.10"), None))
        book.save(path)
        controller = self._controller(limits=ImportLimits(max_preview_rows=10))
        dialog = self._dialog(controller, "zh")
        dialog.path_edit.setText(str(path))
        self.assertTrue(dialog.csv_options_label.isHidden())
        self.assertFalse(dialog.sheet_label.isHidden())
        dialog.sheet_edit.setText("ImportData")
        self.assertTrue(controller.inspect_source(XlsxSource(path, sheet_name="ImportData")))
        self._pump_until(lambda: controller.progress().state in {"SOURCE_READY", "PREVIEW_FAILED"})
        self.assertEqual(controller.progress().state, "SOURCE_READY", controller.progress().safe_code)
        self._preview_default_mapping(controller)
        self.assertEqual(controller.progress().state, "PREVIEW_READY", controller.progress().safe_code)
        values = controller.preview.rows[0].values
        self.assertEqual(values[0], "Alpha")
        self.assertEqual(values[1], Decimal("2.35"))
        self.assertEqual(values[2], date(2026, 10, 2))
        blank_values = controller.preview.rows[1].values
        self.assertIsNone(blank_values[1])
        self.assertEqual(blank_values[2], date(2026, 10, 3))
        self.assertEqual(controller.preview.rows[2].values, ("Gamma", Decimal("3.10"), None))
        self.assertEqual(dialog.windowTitle(), "导入到所选表")

    def test_public_provider_import_uses_fresh_unique_scope_and_explicit_decisions(self):
        path = self.root / "commit.csv"
        path.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        first_connection = _MySqlProviderConnection()
        self.connections.append(first_connection)
        first = self._controller()
        first_dialog = self._dialog(first)
        self._start_import(first, path)
        self.assertFalse(first_dialog.path_edit.isEnabled())
        self.assertFalse(first_dialog.mapping_table.isEnabled())
        first_target = first.session.request.target
        self.assertNotEqual(first_target.tab_id, self.table_target.tab_id)
        self.assertEqual(first.session.request.choice, self.choice)
        self.assertEqual(first.session.request.tree_target, self.tree_target)
        self.assertEqual(first.session.request.node, self.node)
        with self.assertRaises(FrozenInstanceError):
            first.context.choice = self.choice
        self.assertEqual(first.session.status.state, "READY_TO_COMMIT")
        snapshot = self.registry.snapshot()
        first_entry = next(entry for entry in snapshot.entries if entry.import_id == first.import_id)
        self.assertTrue(first_entry.decision_required)
        self.assertFalse(first_entry.failed_cleanup)
        self.assertTrue(first_entry.connection_retained)
        self.assertIn(first.import_id, snapshot.ready_to_commit_ids)
        self.assertNotIn(first.import_id, snapshot.failed_cleanup_ids)
        self.assertEqual(first_connection.inserts[0][1], (("Alpha", Decimal("1.25")),))
        self.assertTrue(first.commit())
        self._pump_until(lambda: first.session.cleanup_future.done() and not first.session.status.owner_alive)
        self.assertEqual(first_connection.commit_count, 1)

        second_path = self.root / "rollback.csv"
        second_path.write_text("name,amount\nBeta,4.50\n", encoding="utf-8", newline="")
        second_connection = _MySqlProviderConnection()
        self.connections.append(second_connection)
        second = self._controller()
        self._start_import(second, second_path)
        self.assertNotEqual(first.session.request.target.tab_id, second.session.request.target.tab_id)
        self.assertTrue(second.rollback())
        self._pump_until(lambda: second.session.cleanup_future.done() and not second.session.status.owner_alive)
        self.assertEqual(second_connection.rollback_count, 1)
        self.assertEqual((second_connection.commit_count, second_connection.rollback_count), (0, 1))
        self.assertEqual(self.connector_calls, [("sales db", "TEMP-SECRET"), ("sales db", "TEMP-SECRET")])

    def test_file_changed_after_preview_is_rejected_before_insert_and_rolled_back(self):
        path = self.root / "late-change.csv"
        path.write_text("name,amount\nBefore,1.25\n", encoding="utf-8", newline="")
        controller = self._controller()
        self._inspect_csv(controller, path)
        self._preview_default_mapping(controller)
        self.assertEqual(controller.progress().state, "PREVIEW_READY")
        path.write_text("name,amount\nAfter,9.99\n", encoding="utf-8", newline="")
        connection = _MySqlProviderConnection()
        self.connections.append(connection)
        self.assertTrue(controller.approve_and_start(user_approved=True))
        self._pump_until(lambda: controller.progress().state == "FAILED")
        self.assertEqual(controller.progress().safe_code, "SOURCE_CHANGED")
        self.assertEqual(len(self.connector_calls), 1)
        self.assertEqual(connection.inserts, [])
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.closed)

    def test_tree_invalidated_after_preview_fails_approval_and_closes_snapshot(self):
        path = self.root / "stale-table.csv"
        path.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        controller = self._controller()
        self._inspect_csv(controller, path)
        self._preview_default_mapping(controller)
        self.assertEqual(controller.progress().state, "PREVIEW_READY")
        source_preview = controller.source_preview
        self.assertIsNotNone(source_preview)
        snapshot = source_preview._snapshot
        self.assertIsNotNone(snapshot)
        snapshot_path = snapshot.path
        self.assertTrue(snapshot_path.exists())

        self.tree.invalidate(self.tree_target)

        self.assertFalse(controller.approve_and_start(user_approved=True))
        self.assertEqual(controller.progress().state, "FAILED")
        self.assertEqual(controller.progress().safe_code, "TABLE_SCOPE_STALE")
        self.assertTrue(snapshot.closed)
        self.assertFalse(snapshot_path.exists())
        self.assertIsNone(controller.session)
        self.assertEqual(self.connector_calls, [])
        self.assertEqual(self.registry.snapshot().entries, ())

    def test_close_during_preview_retains_worker_until_gui_terminal_delivery(self):
        path = self.root / "slow-preview.csv"
        path.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        controller = self._controller()
        started = threading.Event()
        release = threading.Event()
        inspect = import_controller_module.inspect_import_source

        def delayed_inspect(source, *, limits, cancel_check):
            started.set()
            self.assertTrue(release.wait(3))
            return inspect(source, limits=limits, cancel_check=cancel_check)

        with patch.object(import_controller_module, "inspect_import_source", delayed_inspect):
            self.assertTrue(controller.inspect_source(CsvSource(path, encoding="utf-8", delimiter=",")))
            self.assertTrue(started.wait(3))
            self.assertTrue(controller.request_close())
            pending = self.registry.snapshot()
            self.assertEqual(pending.active_preview_count, 1)
            self.assertFalse(pending.can_close)
            release.set()
            self._pump_until(lambda: controller.progress().state == "CANCELLED")
            self._pump_until(lambda: self.registry.snapshot().active_preview_count == 0)
        self.assertIsNone(controller.source_preview)

    def test_stop_waits_for_blocked_provider_insert_and_registry_does_not_call_it_failed_cleanup(self):
        path = self.root / "blocked.csv"
        path.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        connection = _BlockingMySqlProviderConnection()
        self.connections.append(connection)
        controller = self._controller()
        self._inspect_csv(controller, path)
        self._preview_default_mapping(controller)
        self.assertTrue(controller.approve_and_start(user_approved=True))
        self.assertTrue(connection.started_insert.wait(3))
        self.app.processEvents()
        snapshot = self.registry.snapshot()
        entry = next(item for item in snapshot.entries if item.import_id == controller.import_id)
        self.assertEqual(entry.state, "RUNNING")
        self.assertTrue(entry.connection_retained)
        self.assertFalse(entry.failed_cleanup)
        self.assertNotIn(controller.import_id, snapshot.failed_cleanup_ids)
        self.assertTrue(controller.stop())
        self.assertFalse(controller.session.cleanup_future.done())
        self.assertEqual(connection.rollback_count, 0)
        connection.release_insert.set()
        self._pump_until(lambda: controller.session.cleanup_future.done() and not controller.session.status.owner_alive)
        self.assertEqual(controller.session.status.state, "ROLLED_BACK")
        self.assertEqual(connection.rollback_count, 1)

    def test_close_failure_is_failed_cleanup_and_registry_keeps_owner_until_retry(self):
        path = self.root / "close-failure.csv"
        path.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        connection = _CloseFailingMySqlProviderConnection()
        self.connections.append(connection)
        controller = self._controller()
        self._start_import(controller, path)
        self.assertTrue(controller.commit())
        self._pump_until(lambda: (
            controller.session.status.state == "COMMITTED"
            and controller.session.status.connection_retained
        ))
        snapshot = self.registry.snapshot()
        entry = next(item for item in snapshot.entries if item.import_id == controller.import_id)
        self.assertTrue(entry.failed_cleanup)
        self.assertIn(controller.import_id, snapshot.failed_cleanup_ids)
        self.assertTrue(entry.owner_alive)
        self.assertTrue(controller.retry_cleanup())
        self._pump_until(lambda: controller.session.cleanup_future.done() and not controller.session.status.owner_alive)
        self.assertTrue(connection.closed)

    def test_completed_future_callback_cannot_release_live_owner(self):
        path = self.root / "callback.csv"
        path.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        connection = _MySqlProviderConnection()
        self.connections.append(connection)
        controller = self._controller()
        self._start_import(controller, path)
        session = controller.session
        self.assertIsNotNone(session)
        future = session.commit()
        callback_done = threading.Event()
        callback_snapshot = {}

        def observe_inline_completion(_future):
            self.registry.collect_finished()
            status = session.status
            callback_snapshot["owner_alive"] = status.owner_alive
            callback_snapshot["cleanup_done"] = session.cleanup_future.done()
            callback_snapshot["retained"] = any(
                entry.import_id == controller.import_id
                for entry in self.registry.snapshot().entries
            )
            callback_done.set()

        future.add_done_callback(observe_inline_completion)
        future.result(timeout=5)
        self.assertTrue(callback_done.wait(3))
        self.assertTrue(callback_snapshot["cleanup_done"])
        self.assertTrue(callback_snapshot["owner_alive"])
        self.assertTrue(callback_snapshot["retained"])
        self._pump_until(lambda: not session.status.owner_alive)
        self.registry.collect_finished()
        self.assertNotIn(controller.import_id, {
            item.import_id for item in self.registry.snapshot().entries
        })

    def test_dialog_geometry_is_clamped_and_action_controls_stay_present(self):
        controller = self._controller()
        with patch.object(Path, "open", side_effect=AssertionError("dialog constructor must not open files")):
            dialog = self._dialog(controller)
        dialog.show()
        self.app.processEvents()
        screen = dialog.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.assertLessEqual(dialog.width(), available.width())
            self.assertLessEqual(dialog.height(), available.height())
        self.assertTrue(dialog.content_scroll.isVisible())
        for button in (
            dialog.stop_button,
            dialog.start_button,
            dialog.commit_button,
            dialog.rollback_button,
            dialog.close_button,
        ):
            self.assertIsNotNone(button.parentWidget())
            position = button.mapTo(dialog, button.rect().topLeft())
            self.assertTrue(dialog.rect().contains(position))


if __name__ == "__main__":
    unittest.main()
