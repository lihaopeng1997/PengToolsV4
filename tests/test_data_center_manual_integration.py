"""Production AiWorkbenchPanel manual-session adapter tests using TEMP DBAPI."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTableView

from tools.data_center.manual_session import (
    ManualColumn,
    ManualQueryPage,
    ManualSessionState,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.manual_driver import classify_mysql_statement
from tools.data_center.profile_host import DataCenterProfileHost
from ui.data_center.result_model import ManualResultTableModel
from ui.data_center.manual_query import ManualShutdownSnapshot, ManualShutdownStatus


def _profile() -> dict:
    return {
        "id": "temp-manual-mysql",
        "name": "TEMP manual MySQL",
        "dialect": "mysql",
        "provider": "mysql",
        "mode": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "username": "temp-user",
        "password": "TEMP_ENCRYPTED_SECRET",
        "database": "sales",
        "unknown_profile_field": {"keep": "unchanged"},
    }


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rowcount = -1
        self._rows = []
        self.closed = False

    def execute(self, sql, parameters=None):
        text = str(sql)
        self.connection.events.append(("execute", text, parameters, threading.get_ident()))
        if "FAIL" in text.upper():
            raise RuntimeError("TEMP fake statement failure with secret marker")
        if text.strip().upper().startswith("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')"):
            self._oracle_scope_read = True
            self.description = (("CURRENT_SCHEMA",),)
            self.rowcount = 1
            self._rows = [(self.connection.current_schema,)]
            return
        if text.strip().upper().startswith("ALTER SESSION SET CURRENT_SCHEMA ="):
            self.description = ()
            self.rowcount = 0
            match = re.fullmatch(
                r'(?is)\s*ALTER\s+SESSION\s+SET\s+CURRENT_SCHEMA\s*=\s*"((?:[^"]|"")*)"\s*',
                text,
            )
            if match and not self.connection.reject_schema_change:
                self.connection.current_schema = match.group(1).replace('""', '"')
            self._rows = []
            return
        if text.strip().upper().startswith("SELECT"):
            if "PAGED" in text.upper():
                self.description = (("id", "INTEGER", None, None, None, None, True),)
                self.rowcount = 51
                self._rows = [(index,) for index in range(51)]
            else:
                self.description = (("id", "INTEGER", None, None, None, None, True), ("amount", "DECIMAL", None, None, None, None, True))
                self.rowcount = 1
                self._rows = [(42, Decimal("19.25"))]
            if self.connection.block_query is not None and "SLOW" in text.upper():
                self.connection.entered.set()
                if not self.connection.release.wait(3):
                    raise TimeoutError("TEMP fake query was not released")
        else:
            self.description = None
            self.rowcount = 2
            self._rows = []

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", size, threading.get_ident()))
        result, self._rows = self._rows[:size], self._rows[size:]
        return result

    def fetchone(self):
        if not getattr(self, "_oracle_scope_read", False):
            return None
        self._oracle_scope_read = False
        result, self._rows = (self._rows[0] if self._rows else None), self._rows[1:]
        return result

    def close(self):
        self.closed = True
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _FakeConnection:
    def __init__(self, *, block_query=False, fail_commit=False, current_schema=None, reject_schema_change=False):
        self._autocommit = True
        self.events = []
        self.closed = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_query = object() if block_query else None
        self.fail_commit = fail_commit
        self.current_schema = current_schema
        self.reject_schema_change = bool(reject_schema_change)

    def get_autocommit(self):
        return self._autocommit

    def autocommit(self, enabled):
        self._autocommit = bool(enabled)
        self.events.append(("autocommit", self._autocommit, threading.get_ident()))

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _FakeCursor(self)

    def commit(self):
        self.events.append(("commit", threading.get_ident()))
        if self.fail_commit:
            raise RuntimeError("TEMP fake commit failure")

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("close", threading.get_ident()))


class ManualWorkbenchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.confirm_patch = patch("ui.confirm_dialog.confirm_action", return_value=True)
        self.confirm_patch.start()
        self.profile = _profile()
        self.profiles = [self.profile]
        self.profile_read_threads: list[int] = []
        self.secret_boundary: list[tuple[Any, int]] = []
        self.connections: list[_FakeConnection] = []
        self.connector_threads: list[int] = []
        self.oracle_schema_reject = False
        self.panel = None
        self.host = None
        self.panels = []
        self.hosts = []

        def load_profiles():
            self.profile_read_threads.append(threading.get_ident())
            return {"items": [dict(row) for row in self.profiles]}

        self.profile_host = DataCenterProfileHost(connections_loader=load_profiles)

    def tearDown(self):
        for host in tuple(self.hosts):
            host.manual_shutdown_cleanup("rollback")
        self.assertTrue(self._wait(
            lambda: all(host.manual_shutdown_cleanup() is ManualShutdownStatus.READY for host in self.hosts),
            4.0,
        ), "TEMP manual owner threads should be closed before test teardown")
        for panel in tuple(self.panels):
            panel.close()
            panel.deleteLater()
        self.panels.clear()
        self.hosts.clear()
        self.confirm_patch.stop()
        self.panel = None
        self.host = None
        self._flush()

    def _make_panel(self, *, block_query=False, fail_commit_on_connection=None):
        from panels.ai_workbench_panel import AiWorkbenchPanel

        profile = self.profile
        snapshot_none = None

        def secret_resolver(encrypted):
            self.secret_boundary.append((encrypted, threading.get_ident()))
            return "TEMP_PLAINTEXT_SECRET_AT_DRIVER_BOUNDARY"

        def connector(profile_row, secret):
            self.connector_threads.append(threading.get_ident())
            self.assertEqual(profile_row["id"], self.profile["id"])
            self.assertEqual(secret, "TEMP_PLAINTEXT_SECRET_AT_DRIVER_BOUNDARY")
            username = str(profile_row.get("username") or profile_row.get("user") or "").strip()
            if username.startswith('"') and username.endswith('"'):
                current_schema = username[1:-1].replace('""', '"')
            else:
                current_schema = username.upper()
            connection = _FakeConnection(
                block_query=block_query,
                fail_commit=fail_commit_on_connection == len(self.connections),
                current_schema=current_schema if str(profile_row.get("provider") or "").casefold() == "oracle" else None,
                reject_schema_change=self.oracle_schema_reject,
            )
            self.connections.append(connection)
            return connection

        with (
            patch("panels.ai_workbench_panel.load_connections", return_value=[dict(profile)]),
            patch("panels.ai_workbench_panel.load_snapshot", return_value=snapshot_none),
            patch("panels.ai_workbench_panel.is_enabled", return_value=False),
            patch("panels.ai_workbench_panel.load_ai_local", return_value={}),
            patch("panels.ai_workbench_panel.open_connection", side_effect=AssertionError("legacy DB connect used")),
            patch("panels.ai_workbench_panel.run_console_statement", side_effect=AssertionError("legacy auto-commit console used")),
        ):
            self.panel = AiWorkbenchPanel(
                connection_id=profile["id"],
                dialect=str(profile.get("dialect") or "mysql"),
                data_center_agent_options={"profile_host": self.profile_host},
                manual_query_options={
                    "profile_host": self.profile_host,
                    "connector": connector,
                    "secret_resolver": secret_resolver,
                    "page_size": 50,
                },
            )
        self.host = self.panel.manual_query_host
        self.panels.append(self.panel)
        self.hosts.append(self.host)
        return self.panel

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

    def _state_for(self, tab):
        return self.host._states_by_tab[id(tab)]

    def test_oracle_service_database_is_not_the_schema_and_scope_is_owner_verified(self):
        oracle = {
            "id": "temp-manual-oracle",
            "name": "TEMP Oracle",
            "provider": "oracle",
            "engine": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "host": "127.0.0.1",
            "port": 1521,
            "username": "APP",
            "password": "TEMP_ENCRYPTED_SECRET",
            "database": "ORCL",
            "service_name": "ORCL",
        }
        self.profile = oracle
        self.profiles = [oracle]
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        self.assertEqual(state.target.database, "ORCL")
        self.assertEqual(state.target.schema, "APP")

        panel._current_tab().editor.setPlainText("SELECT 1 FROM dual")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.CLEAN)
        connection = self.connections[0]
        statements = [event[1] for event in connection.events if event[0] == "execute"]
        self.assertIn("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL", statements)
        self.assertNotIn("ALTER SESSION SET CURRENT_SCHEMA = \"APP\"", statements)
        self.assertEqual(statements[-1], "SELECT 1 FROM dual")
        self.assertEqual(connection.current_schema, "APP")

    def test_oracle_explicit_quoted_owner_is_set_and_mismatch_closes_before_user_sql(self):
        owner_profile = {
            "id": "temp-manual-oracle-owner",
            "name": "TEMP Oracle owner",
            "provider": "oracle",
            "engine": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "host": "127.0.0.1",
            "port": 1521,
            "username": "APP",
            "password": "TEMP_ENCRYPTED_SECRET",
            "database": "ORCL",
            "service_name": "ORCL",
            "schema": '"AppCase"',
        }
        self.profile = owner_profile
        self.profiles = [owner_profile]
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        self.assertEqual(state.target.database, "ORCL")
        self.assertEqual(state.target.schema, "AppCase")
        panel._current_tab().editor.setPlainText("SELECT 1 FROM dual")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        successful_connection = self.connections[0]
        statements = [event[1] for event in successful_connection.events if event[0] == "execute"]
        self.assertIn('ALTER SESSION SET CURRENT_SCHEMA = "AppCase"', statements)
        self.assertEqual(successful_connection.current_schema, "AppCase")
        self.assertEqual(statements[-1], "SELECT 1 FROM dual")

        self.host.manual_shutdown_cleanup("close")
        self.assertTrue(self._wait(lambda: self.host.manual_shutdown_cleanup() is ManualShutdownStatus.READY))
        self.panel.close()
        self.panel.deleteLater()
        self.panel = None
        self.panels.clear()
        self.hosts.clear()
        self.host = None

        failing_profile = {**owner_profile, "id": "temp-manual-oracle-owner-rejected", "schema": "NOACCESS"}
        self.profile = failing_profile
        self.profiles = [failing_profile]
        self.oracle_schema_reject = True
        failed_panel = self._make_panel()
        failed_state = self._state_for(failed_panel._current_tab())
        failed_panel._current_tab().editor.setPlainText("SELECT 1 FROM dual")
        failed_panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not failed_state.busy))
        failed_connection = self.connections[-1]
        statements = [event[1] for event in failed_connection.events if event[0] == "execute"]
        self.assertEqual(failed_state.session.state, ManualSessionState.DISCONNECTED)
        self.assertIn("ALTER SESSION SET CURRENT_SCHEMA = \"NOACCESS\"", statements)
        self.assertNotIn("SELECT 1 FROM dual", statements)
        self.assertFalse(failed_connection.closed, "the configured owner handle must remain available for explicit cleanup")
        self.assertIn("MANUAL_COMMIT_CONFIGURATION_FAILED", failed_panel.result_status.text())
        self.assertNotIn("TEMP_ENCRYPTED_SECRET", failed_panel.result_status.text())
        failed_panel.manual_shutdown_cleanup("close")
        self.assertTrue(self._wait(lambda: failed_panel.manual_shutdown_cleanup() is ManualShutdownStatus.READY))
        self.assertTrue(failed_connection.closed)

    def test_oracle_quoted_login_owner_preserves_case_and_is_only_verified(self):
        oracle = {
            "id": "temp-manual-oracle-quoted-login",
            "name": "TEMP quoted Oracle login",
            "provider": "oracle",
            "engine": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "host": "127.0.0.1",
            "port": 1521,
            "username": '"AppLogin"',
            "password": "TEMP_ENCRYPTED_SECRET",
            "database": "ORCL",
            "service_name": "ORCL",
        }
        self.profile = oracle
        self.profiles = [oracle]
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        self.assertEqual(state.target.database, "ORCL")
        self.assertEqual(state.target.schema, "AppLogin")
        panel._current_tab().editor.setPlainText("SELECT 1 FROM dual")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        statements = [event[1] for event in self.connections[0].events if event[0] == "execute"]
        self.assertIn("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL", statements)
        self.assertNotIn('ALTER SESSION SET CURRENT_SCHEMA = "AppLogin"', statements)
        self.assertEqual(self.connections[0].current_schema, "AppLogin")

    def test_shutdown_snapshot_and_gate_are_safe_nonblocking_workbench_seams(self):
        panel = self._make_panel()
        host = self.host
        snapshot = panel.manual_shutdown_snapshot()
        self.assertIsInstance(snapshot, ManualShutdownSnapshot)
        self.assertTrue(snapshot.can_commit)
        self.assertEqual(len(snapshot.sessions), 1)
        entry = snapshot.sessions[0]
        self.assertEqual(entry.tab_id, self._state_for(panel._current_tab()).target.tab_id)
        self.assertEqual(entry.state, ManualSessionState.DISCONNECTED.value)
        self.assertFalse(entry.busy)
        self.assertFalse(entry.cleanup_complete)
        self.assertFalse(hasattr(entry, "sql"))

        self.assertTrue(panel.manual_shutdown_gate(True))
        self.assertTrue(host._shutdown_gated)
        self.assertFalse(panel.run_btn.isEnabled())
        self.assertFalse(panel.new_tab_btn.isEnabled())
        self.assertFalse(panel.conn_combo.isEnabled())
        self.assertIsNone(panel._new_sql_tab())
        host.run_current()
        self.assertEqual(panel.sql_tabs.count(), 1)
        self.assertEqual(self.connections, [])

        self.assertTrue(panel.manual_shutdown_gate(False))
        self.assertFalse(host._shutdown_gated)
        self.assertTrue(panel.run_btn.isEnabled())
        self.assertTrue(panel.new_tab_btn.isEnabled())
        self.assertEqual(self.connections, [])

    def test_production_panel_select_dml_dirty_commit_clean_uses_manual_owner(self):
        panel = self._make_panel()
        tab = panel._current_tab()
        state = self._state_for(tab)
        self.assertEqual(self.connections, [], "tab creation must not connect")
        self.assertEqual(self.connector_threads, [])
        self.assertFalse(hasattr(state.target, "password"))

        self.assertIsInstance(panel.manual_result_view, QTableView)
        self.assertIs(panel.manual_result_view.model(), panel.manual_result_model)
        self.assertFalse(panel.result.isVisible(), "manual results must not materialize QTableWidgetItems")
        tab.editor.setPlainText("SELECT id, amount FROM orders")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.CLEAN)
        model = panel.manual_result_model
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.index(0, 0).data(Qt.ItemDataRole.DisplayRole), "42")
        self.assertEqual(model.index(0, 1).data(Qt.ItemDataRole.DisplayRole), "19.25")
        self.assertEqual(model.index(0, 1).data(Qt.ItemDataRole.UserRole), Decimal("19.25"))
        connection = self.connections[0]
        self.assertFalse(connection.get_autocommit())
        self.assertFalse(any(event[0] == "commit" for event in connection.events))

        tab.editor.setPlainText("UPDATE orders SET amount = 20 WHERE id = 42")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.DIRTY)
        self.assertTrue(self.host.commit_btn.isEnabled())
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 0)

        self.host.commit_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.CLEAN)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 1)
        self.assertTrue(self.profile_read_threads)
        self.assertTrue(all(ident != threading.get_ident() for ident in self.profile_read_threads))
        self.assertTrue(all(ident != threading.get_ident() for ident in self.connector_threads))
        self.assertTrue(all(ident != threading.get_ident() for _, ident in self.secret_boundary))
        owner_ids = {event[-1] for event in connection.events if isinstance(event[-1], int)}
        self.assertEqual(len(owner_ids), 1, "all DBAPI calls must use one persistent owner thread")
        self.assertNotIn("TEMP_PLAINTEXT_SECRET_AT_DRIVER_BOUNDARY", repr(state.last_page))

    def test_each_sql_tab_owns_its_manual_session_and_switch_does_not_cancel_query(self):
        panel = self._make_panel(block_query=True)
        first_tab = panel._current_tab()
        first_state = self._state_for(first_tab)
        first_tab.editor.setPlainText("SELECT SLOW id FROM orders")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: bool(self.connections) and self.connections[0].entered.is_set()))

        second_tab = panel._new_sql_tab()
        second_state = self._state_for(second_tab)
        self.assertIsNot(first_state.session, second_state.session)
        self.assertNotEqual(first_state.target.tab_id, second_state.target.tab_id)
        second_tab.editor.setPlainText("UPDATE orders SET amount = 21 WHERE id = 42")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not second_state.busy))
        self.assertEqual(second_state.session.state, ManualSessionState.DIRTY)

        panel.sql_tabs.setCurrentIndex(0)
        self.assertTrue(first_state.session.poll_cleanup().busy)
        self.assertFalse(hasattr(first_state.session, "cancelled"))
        panel.sql_tabs.setCurrentIndex(1)
        self.connections[0].release.set()
        self.assertTrue(self._wait(lambda: not first_state.busy))
        self.assertIsNotNone(first_state.last_page)
        self.assertEqual(panel.manual_result_model.rowCount(), 0, "hidden-tab completion must not paint into the visible tab")

        panel.sql_tabs.setCurrentIndex(0)
        self.assertEqual(panel.manual_result_model.rowCount(), 1, "the completed result should remain owned by its originating tab")
        panel.sql_tabs.setCurrentIndex(1)
        self.host.rollback_btn.click()
        self.assertTrue(self._wait(lambda: not second_state.busy))
        self.assertEqual(second_state.session.state, ManualSessionState.CLEAN)
        self.assertNotEqual(first_state.session._owner_ident, second_state.session._owner_ident)

    def test_profile_revision_drift_refuses_before_connector_and_keeps_session_unopened(self):
        panel = self._make_panel()
        tab = panel._current_tab()
        state = self._state_for(tab)
        self.profiles[0] = {**self.profile, "database": "other_database"}
        tab.editor.setPlainText("SELECT id FROM orders")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(self.connections, [])
        self.assertEqual(self.connector_threads, [])
        self.assertEqual(state.session.state, ManualSessionState.DISCONNECTED)
        # Only this explicitly allowed profile drift code crosses the core
        # boundary; endpoint and driver errors remain CONNECTION_FAILED.
        self.assertEqual(state.last_error, "PROFILE_MISMATCH")

    def test_production_typed_model_keeps_pages_and_null_distinct_from_empty_string(self):
        page = ManualQueryPage(
            query_id="q-typed",
            tab_id="t-typed",
            generation=1,
            columns=(ManualColumn("nullable"), ManualColumn("empty"), ManualColumn("amount")),
            rows=((None, "", Decimal("1.20")),),
            page_number=1,
            page_size=50,
            has_more=False,
            result_bytes=16,
        )
        model = ManualResultTableModel()
        model.replace_page(page)
        self.assertIs(model.pages[0], page, "the model must retain typed pages instead of per-cell widgets")
        self.assertEqual(model.index(0, 0).data(Qt.ItemDataRole.DisplayRole), "NULL")
        self.assertIsNone(model.index(0, 0).data(Qt.ItemDataRole.UserRole))
        self.assertEqual(model.index(0, 1).data(Qt.ItemDataRole.DisplayRole), "")
        self.assertEqual(model.index(0, 1).data(Qt.ItemDataRole.UserRole), "")
        self.assertEqual(model.index(0, 2).data(Qt.ItemDataRole.UserRole), Decimal("1.20"))

    def test_production_manual_query_appends_typed_pages_without_qtablewidget_copy(self):
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        panel._current_tab().editor.setPlainText("SELECT PAGED id FROM orders")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(panel.manual_result_model.rowCount(), 50)
        self.assertEqual(len(state.pages), 1)
        self.assertTrue(state.last_page.has_more)

        panel.next_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(panel.manual_result_model.rowCount(), 51)
        self.assertEqual(len(state.pages), 2)
        self.assertEqual(state.pages[1].page_number, 2)
        self.assertEqual(panel.manual_result_model.index(50, 0).data(Qt.ItemDataRole.UserRole), 50)
        self.assertEqual(panel.result.rowCount(), 0, "manual rows must not be copied into the legacy table")

    def test_dangerous_dml_keeps_legacy_confirmation_and_cancel_is_zero_io(self):
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        editor = panel._current_tab().editor
        sql = "DELETE FROM orders"

        with patch("ui.confirm_dialog.confirm_action", return_value=False) as confirm:
            editor.setPlainText(sql)
            panel.run_btn.click()
            self.assertEqual(confirm.call_count, 1)
            self.assertTrue(confirm.call_args.kwargs["danger"])
            self.assertFalse(state.busy)
            self.assertEqual(self.connections, [])
            self.assertEqual(self.connector_threads, [])

        with patch("ui.confirm_dialog.confirm_action", return_value=True) as confirm:
            panel.run_btn.click()
            self.assertEqual(confirm.call_count, 1)
            self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.DIRTY)
        self.assertEqual(
            [event[1] for event in self.connections[0].events if event[0] == "execute"],
            [sql],
        )
        self.host.rollback_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))

    def test_manual_toolbar_and_typed_result_fit_embedded_1160x720_workspace(self):
        from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

        panel = self._make_panel()
        outer = QWidget()
        outer.setObjectName("temp-data-center-1160x720")
        outer.resize(1160, 720)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        app_chrome = QLabel("TEMP data-center header and toolbar")
        app_chrome.setFixedHeight(132)
        outer_layout.addWidget(app_chrome)
        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        tree = QLabel("TEMP connection tree")
        tree.setFixedWidth(260)
        workspace_layout.addWidget(tree)
        panel.set_embedded_compact_mode(True)
        panel.setMinimumSize(0, 0)
        workspace_layout.addWidget(panel, 1)
        outer_layout.addWidget(workspace, 1)
        panel._narrow_show_ai = True
        panel.apply_layout_mode("compact", low_height=False, content_width=900)
        panel.side_tabs.setMinimumWidth(340)
        panel.columns_splitter.setSizes([0, 560, 340])
        panel._current_tab().editor.setPlainText("SELECT id, amount FROM orders")

        outer.show()
        self.panels.append(outer)
        self._flush()
        panel.run_btn.click()
        state = self._state_for(panel._current_tab())
        self.assertTrue(self._wait(lambda: not state.busy))
        self._flush()

        self.assertFalse(panel.page_header.isVisible())
        self.assertFalse(panel.connection_toolbar.isVisible())
        self.assertFalse(panel.narrow_chrome.isVisible())
        self.host._refresh_workbench_actions()
        action_texts = {action.text() for action in self.host.workbench_more_menu.actions()}
        self.assertIn("扫描结构", action_texts, "embedded compaction must preserve schema scan")
        self.assertIn("保存草稿", action_texts, "embedded compaction must preserve draft saving")
        scan_calls = []
        old_start_db = panel._start_db
        panel._start_db = lambda kind, **_kwargs: scan_calls.append(kind)
        next(action for action in self.host.workbench_more_menu.actions() if action.text() == "扫描结构").trigger()
        panel._start_db = old_start_db
        self.assertEqual(scan_calls, ["scan"], "overflow actions must keep existing workbench handlers")
        self.assertFalse(panel.format_btn.isVisible(), "compact mode moves formatting into the editor menu")
        self.assertFalse(panel.clear_btn.isVisible(), "compact mode moves clearing into the editor menu")
        self.assertTrue(panel.editor_more_btn.isVisible())
        editor_actions = {action.text(): action for action in panel.editor_more_menu.actions()}
        self.assertIn("格式化", editor_actions)
        self.assertIn("清空", editor_actions)
        self.assertIn("显示 SQL 草稿与对象详情", editor_actions)
        editor = panel._current_tab().editor
        editor.setPlainText("select id,amount from orders")
        with patch("tools.sql_tool.format_sql", return_value="SELECT id, amount FROM orders") as formatter:
            editor_actions["格式化"].trigger()
        formatter.assert_called_once_with("select id,amount from orders")
        self.assertEqual(editor.toPlainText(), "SELECT id, amount FROM orders")
        editor_actions["清空"].trigger()
        self.assertEqual(editor.toPlainText(), "")
        panel.set_language("en")
        self.assertEqual(panel.editor_legacy_panels_action.text(), "Show SQL drafts / object details")
        panel.set_language("zh")
        self.assertEqual(panel.editor_legacy_panels_action.text(), "显示 SQL 草稿与对象详情")

        # 事务/停止按钮只在可用时出现（有未提交事务、执行中），其余常驻
        for button in (self.host.commit_btn, self.host.rollback_btn, self.host.stop_btn):
            self.assertEqual(button.isVisible(), button.isEnabled(), button.text())
        toolbar_buttons = [panel.run_btn, panel.new_tab_btn, panel.editor_more_btn]
        for button in toolbar_buttons:
            with self.subTest(button=button.text()):
                self.assertTrue(button.isVisible())
                self.assertGreaterEqual(button.width(), button.sizeHint().width())
        self.assertGreaterEqual(panel.manual_result_view.height(), 120)
        self.assertEqual(panel.workspace_scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(panel.manual_result_model.rowCount(), 1)
        output_dir = os.path.join(tempfile.gettempdir(), "pengtools-dc-ui-20261001")
        os.makedirs(output_dir, exist_ok=True)
        output = os.path.join(output_dir, "manual-workbench-zh-1160x720.png")
        self.assertTrue(outer.grab().save(output, "PNG"))
        self.manual_layout_screenshot = output

    def test_failed_query_keeps_last_successful_typed_pages_visible(self):
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        panel._current_tab().editor.setPlainText("SELECT id, amount FROM orders")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        previous_pages = state.pages
        self.assertEqual(panel.manual_result_model.rowCount(), 1)

        panel._current_tab().editor.setPlainText("SELECT FAIL FROM orders")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.last_error, "STATEMENT_EXECUTION_FAILED")
        self.assertEqual(state.pages, previous_pages)
        self.assertEqual(panel.manual_result_model.rowCount(), 1)

    def test_manual_query_uses_statement_at_cursor_and_blocks_routine_split(self):
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        editor = panel._current_tab().editor
        text = "SELECT id FROM orders;\nUPDATE orders SET amount = 9"
        editor.setPlainText(text)
        cursor = editor.textCursor()
        cursor.setPosition(text.index("UPDATE") + 3)
        editor.setTextCursor(cursor)
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.DIRTY)
        executed = [event[1] for event in self.connections[0].events if event[0] == "execute"]
        self.assertEqual(executed, ["UPDATE orders SET amount = 9"])

        second = panel._new_sql_tab()
        second_state = self._state_for(second)
        second.editor.setPlainText("CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END")
        panel.run_btn.click()
        self.assertFalse(second_state.busy)
        self.assertEqual(len(self.connections), 1, "routine blocks must not be naively split and executed")

    def test_ddl_dcl_require_separate_confirmation_and_clean_transaction(self):
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        editor = panel._current_tab().editor

        for sql in ("CREATE TABLE work_items (id INT)", "GRANT SELECT ON work_items TO reader"):
            connection_count = len(self.connections)
            with patch("ui.confirm_dialog.confirm_action", return_value=False) as confirm:
                editor.setPlainText(sql)
                panel.run_btn.click()
                self.assertEqual(confirm.call_count, 1)
                self.assertFalse(state.busy)
                self.assertEqual(len(self.connections), connection_count, "cancelled DDL/DCL must not open a new connection")

            with patch("ui.confirm_dialog.confirm_action", return_value=True) as confirm:
                editor.setPlainText(sql)
                panel.run_btn.click()
                self.assertEqual(confirm.call_count, 1)
                self.assertTrue(self._wait(lambda: not state.busy))
                self.assertEqual(state.session.state, ManualSessionState.CLEAN)
                self.assertEqual(state.last_statement.kind, StatementKind.DDL if sql.startswith("CREATE") else StatementKind.DCL)

        connection = self.connections[0]
        self.assertEqual(sum(event[0] == "execute" for event in connection.events), 2)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 0)

    def test_ddl_is_blocked_while_dirty_without_opening_confirmation(self):
        panel = self._make_panel()
        state = self._state_for(panel._current_tab())
        editor = panel._current_tab().editor
        editor.setPlainText("UPDATE work_items SET id = 2")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        connection = self.connections[0]
        event_count = sum(event[0] == "execute" for event in connection.events)

        with patch("ui.confirm_dialog.confirm_action", return_value=True) as confirm:
            editor.setPlainText("CREATE TABLE blocked_items (id INT)")
            panel.run_btn.click()
            self.assertEqual(confirm.call_count, 0)
        self.assertEqual(sum(event[0] == "execute" for event in connection.events), event_count)
        self.assertEqual(state.session.state, ManualSessionState.DIRTY)

    def test_shutdown_preflight_cancel_preserves_clean_and_dirty_sessions_across_hosts(self):
        clean_panel = self._make_panel()
        clean_host = self.host
        clean_state = self._state_for(clean_panel._current_tab())
        clean_panel._current_tab().editor.setPlainText("SELECT id FROM orders")
        clean_panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not clean_state.busy))

        dirty_panel = self._make_panel()
        dirty_host = self.host
        dirty_state = self._state_for(dirty_panel._current_tab())
        dirty_panel._current_tab().editor.setPlainText("UPDATE orders SET amount = 15")
        dirty_panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not dirty_state.busy))
        clean_connection, dirty_connection = self.connections
        self.assertEqual(clean_state.session.state, ManualSessionState.CLEAN)
        self.assertEqual(dirty_state.session.state, ManualSessionState.DIRTY)

        # All hosts are read-only preflighted before any close is requested.
        self.assertEqual(clean_host.manual_shutdown_cleanup("preflight"), ManualShutdownStatus.READY_TO_CLOSE)
        self.assertEqual(dirty_host.manual_shutdown_cleanup(), ManualShutdownStatus.DECISION_REQUIRED)
        self.assertEqual(clean_host.manual_shutdown_cleanup("cancel"), ManualShutdownStatus.CANCELLED)
        self.assertEqual(dirty_host.manual_shutdown_cleanup("cancel"), ManualShutdownStatus.CANCELLED)
        self.assertFalse(clean_connection.closed)
        self.assertFalse(dirty_connection.closed)
        self.assertEqual(sum(event[0] == "close" for event in clean_connection.events), 0)
        self.assertEqual(sum(event[0] == "rollback" for event in dirty_connection.events), 0)
        self.assertEqual(clean_state.session.state, ManualSessionState.CLEAN)
        self.assertEqual(dirty_state.session.state, ManualSessionState.DIRTY)

        clean_host.manual_shutdown_cleanup("rollback")
        dirty_host.manual_shutdown_cleanup("rollback")
        self.assertTrue(self._wait(lambda: (
            clean_host.manual_shutdown_cleanup() is ManualShutdownStatus.READY
            and dirty_host.manual_shutdown_cleanup() is ManualShutdownStatus.READY
        )))
        self.assertTrue(clean_connection.closed)
        self.assertFalse(any(event[0] in {"commit", "rollback"} for event in clean_connection.events))
        self.assertEqual(sum(event[0] == "rollback" for event in dirty_connection.events), 1)
        self.assertTrue(dirty_connection.closed)

    def test_mixed_dirty_unknown_blocks_shutdown_commit_before_any_io(self):
        panel = self._make_panel(fail_commit_on_connection=1)
        host = self.host
        first_tab = panel._current_tab()
        first_state = self._state_for(first_tab)
        first_tab.editor.setPlainText("UPDATE orders SET amount = 4")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not first_state.busy))

        second_tab = panel._new_sql_tab()
        second_state = self._state_for(second_tab)
        second_tab.editor.setPlainText("UPDATE orders SET amount = 5")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not second_state.busy))
        with self.assertRaises(Exception):
            second_state.session.commit().result(timeout=3)
        self.assertEqual(first_state.session.state, ManualSessionState.DIRTY)
        self.assertEqual(second_state.session.state, ManualSessionState.UNKNOWN)
        first_connection, second_connection = self.connections

        self.assertEqual(host.manual_shutdown_cleanup("commit"), ManualShutdownStatus.DECISION_REQUIRED)
        self.assertEqual(sum(event[0] == "commit" for event in first_connection.events), 0)
        self.assertEqual(sum(event[0] == "rollback" for event in first_connection.events), 0)
        self.assertEqual(sum(event[0] == "close" for event in first_connection.events), 0)
        self.assertEqual(sum(event[0] == "commit" for event in second_connection.events), 1)
        self.assertEqual(sum(event[0] == "rollback" for event in second_connection.events), 0)
        self.assertEqual(sum(event[0] == "close" for event in second_connection.events), 0)

        host.manual_shutdown_cleanup("rollback")
        self.assertTrue(self._wait(lambda: host.manual_shutdown_cleanup() is ManualShutdownStatus.READY))
        self.assertEqual(sum(event[0] == "commit" for event in second_connection.events), 1, "UNKNOWN must never auto-retry commit")
        self.assertEqual(sum(event[0] == "rollback" for event in second_connection.events), 1)
        self.assertTrue(first_connection.closed and second_connection.closed)

    def test_dirty_tab_close_cancel_retains_session_and_rollback_closes_owner(self):
        panel = self._make_panel()
        host = self.host
        tab = panel._current_tab()
        state = self._state_for(tab)
        tab.editor.setPlainText("UPDATE orders SET amount = 8")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        connection = self.connections[0]

        self.assertFalse(host.manual_dirty_close("cancel"))
        self.assertEqual(state.session.state, ManualSessionState.DIRTY)
        self.assertFalse(connection.closed)
        self.assertEqual(sum(event[0] in {"commit", "rollback", "close"} for event in connection.events), 0)

        self.assertTrue(host.manual_dirty_close("rollback"))
        self.assertTrue(self._wait(lambda: connection.closed))
        self.assertEqual(sum(event[0] == "rollback" for event in connection.events), 1)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 0)
        self.assertTrue(self._wait(lambda: host.manual_shutdown_cleanup("preflight") is ManualShutdownStatus.READY_TO_CLOSE))

    def test_unknown_rollback_close_releases_owner_without_claiming_known_rollback(self):
        panel = self._make_panel(fail_commit_on_connection=0)
        host = self.host
        tab = panel._current_tab()
        state = self._state_for(tab)
        tab.editor.setPlainText("UPDATE orders SET amount = 11")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: not state.busy))
        connection = self.connections[0]

        with self.assertRaises(Exception):
            state.session.commit().result(timeout=3)
        self.assertEqual(state.session.state, ManualSessionState.UNKNOWN)
        self.assertFalse(connection.closed)
        self.assertFalse(host.manual_dirty_close("commit"), "UNKNOWN cannot be committed")
        self.assertEqual(sum(event[0] == "close" for event in connection.events), 0)

        self.assertTrue(host.manual_dirty_close("rollback"))
        self.assertTrue(self._wait(lambda: connection.closed))
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 1)
        self.assertEqual(sum(event[0] == "rollback" for event in connection.events), 1)
        self.assertTrue(state.last_error == "TRANSACTION_OUTCOME_UNKNOWN")
        self.assertIn("事务结果仍未知", panel.result_status.text())
        self.assertNotIn("回滚成功", panel.result_status.text())
        self.assertTrue(self._wait(lambda: host.manual_shutdown_cleanup("preflight") is ManualShutdownStatus.READY_TO_CLOSE))

    def test_mysql_classifier_declares_transaction_effects_without_is_read_heuristic(self):
        cases = {
            "SELECT id FROM t": (StatementKind.QUERY, TransactionEffect.NO_CHANGE),
            "SELECT id FROM t FOR UPDATE": (StatementKind.QUERY, TransactionEffect.DIRTY),
            "SELECT id INTO @current_id FROM t": (StatementKind.QUERY, TransactionEffect.DIRTY),
            "WITH changed AS (SELECT id FROM t) UPDATE t SET v = 1": (StatementKind.DML, TransactionEffect.DIRTY),
            "CREATE TABLE t (id INT)": (StatementKind.DDL, TransactionEffect.IMPLICIT_COMMIT),
            "COMMIT": (StatementKind.TRANSACTION_CONTROL, TransactionEffect.UNKNOWN),
            "SET autocommit = 1": (StatementKind.TRANSACTION_CONTROL, TransactionEffect.UNKNOWN),
            "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED": (
                StatementKind.TRANSACTION_CONTROL, TransactionEffect.UNKNOWN,
            ),
            "SET NAMES utf8mb4": (StatementKind.OTHER, TransactionEffect.UNKNOWN),
            "SET SESSION time_zone = '+00:00'": (StatementKind.OTHER, TransactionEffect.UNKNOWN),
            "OPTIMIZE TABLE t": (StatementKind.OTHER, TransactionEffect.UNKNOWN),
            "SELECT 1; UPDATE t SET v = 2": (StatementKind.MULTI_STATEMENT, TransactionEffect.UNKNOWN),
        }
        for sql, expected in cases.items():
            with self.subTest(sql=sql):
                actual = classify_mysql_statement(sql)
                self.assertEqual((actual.kind, actual.effect), expected)


if __name__ == "__main__":
    unittest.main()
