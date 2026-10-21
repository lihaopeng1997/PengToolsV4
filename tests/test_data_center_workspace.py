"""Isolated behavior checks for the unified Data Center workspace."""

from __future__ import annotations

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from panels.data_center_panel import DataCenterPanel, _WorkspaceConnectionDialog
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.data_center.workspace_tree import (
    AMBIGUOUS_ID_ROLE,
    CONNECTION_ID_ROLE,
    NODE_KIND_ROLE,
)


APP = QApplication.instance() or QApplication([])


def _profile(connection_id: str, dialect: str = "mysql", **values) -> dict:
    item = {
        "id": connection_id,
        "name": f"{dialect}-{connection_id}",
        "dialect": dialect,
        "host": "db.invalid",
        "port": 3306,
        "database": "sample",
        "username": "test-user",
        "password": "enc:test-secret-token",
        "group_path": "Payments/ledger",
        "environment": "test",
        "vendor_extension": {"retained": True},
    }
    item.update(values)
    return item


class _FakeAgent(QWidget):
    def __init__(self, *, parent=None, **_kwargs):
        super().__init__(parent)
        self.target_change_count = 0
        self.cleanup_count = 0

    def target_changed(self, *_args):
        self.target_change_count += 1

    def sql_tab_visibility_changed(self):
        pass

    def set_language(self, _language):
        pass

    def agent_shutdown_cleanup(self):
        self.cleanup_count += 1
        return True


class _ManualWorkspaceHost(QWidget):
    def __init__(self, snapshot, preflight=(), *, action_result="ready", workbench_results=(), agent_results=()):
        super().__init__()
        self.snapshot = snapshot
        self.preflight = list(preflight)
        self.action_result = action_result
        self.workbench_results = list(workbench_results)
        self.agent_results = list(agent_results)
        self.manual_calls = []
        self.actions = []
        self.gates = []
        self.workbench_calls = 0
        self.agent_calls = 0
        self.query_count = 0

    def manual_shutdown_snapshot(self):
        return self.snapshot

    def manual_shutdown_cleanup(self, disposition=None):
        self.manual_calls.append(disposition)
        if disposition is None:
            return self.preflight.pop(0) if self.preflight else "ready"
        self.actions.append(disposition)
        return self.action_result

    def manual_shutdown_gate(self, enabled):
        self.gates.append(bool(enabled))
        return True

    def workbench_shutdown_cleanup(self):
        self.workbench_calls += 1
        return self.workbench_results.pop(0) if self.workbench_results else True

    def agent_shutdown_cleanup(self):
        self.agent_calls += 1
        return self.agent_results.pop(0) if self.agent_results else True

    def _new_sql_tab(self):
        self.query_count += 1


class DataCenterWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _profile("rel-1", "mysql", environment="production", favorite=True),
            _profile("redis-1", "redis", environment="stage"),
            _profile("mongo-1", "mongodb", environment="vendor-qa"),
        ]
        self.catalog = WorkspaceCatalog(loader=lambda: [dict(row) for row in self.rows])
        self.factories = []

        def factory(profile, language):
            host = QWidget()
            host.connection_id = str(profile["id"])
            host.language = language
            host.cleanup_count = 0
            host.cleanup_complete = True
            host.agent_running = True

            def cleanup():
                host.cleanup_count += 1
                return host.cleanup_complete

            host.agent_shutdown_cleanup = cleanup
            self.factories.append((dict(profile), host))
            return host

        self.panel = DataCenterPanel("zh", catalog=self.catalog, host_factory=factory)

    def tearDown(self):
        self.panel.deleteLater()
        APP.processEvents()

    def test_catalog_projection_preserves_profile_fields_without_exposing_them(self):
        summary = self.catalog.get_summary("rel-1")
        profile = self.catalog.get_profile("rel-1")
        self.assertIsNotNone(summary)
        self.assertTrue(summary.favorite)
        self.assertEqual(profile["password"], "enc:test-secret-token")
        self.assertEqual(profile["vendor_extension"], {"retained": True})
        visible = []
        model = self.panel.tree.source_model

        def collect(parent):
            for row in range(model.rowCount(parent)):
                index = model.index(row, 0, parent)
                visible.append(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
                collect(index)

        collect(model.invisibleRootItem().index())
        self.assertTrue(any("mysql-rel-1" in text for text in visible))
        self.assertFalse(any("test-secret-token" in text for text in visible))
        self.assertFalse(any("vendor_extension" in text for text in visible))

    def test_select_and_global_refresh_do_not_construct_or_retarget_workspaces(self):
        self.assertTrue(self.panel.tree.select_connection("rel-1"))
        self.assertEqual(self.factories, [])
        # The explicit open action constructs the page exactly once per ID.
        first = self.panel.open_selected_connection()
        second = self.panel._open_connection("rel-1")
        other = self.panel._open_connection("redis-1")
        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(len(self.factories), 2)
        self.assertEqual(first.connection_id, "rel-1")
        self.assertEqual(other.connection_id, "redis-1")
        self.assertEqual(first.profile["id"], "rel-1")
        self.assertEqual(other.profile["id"], "redis-1")

        active_before = first.connection_id
        self.panel.workspace_tabs.setCurrentWidget(first)
        self.panel.workspace_tabs.setCurrentWidget(other)
        self.panel.workspace_tabs.setCurrentWidget(first)
        self.assertEqual(first.connection_id, active_before)
        self.assertTrue(first.host.agent_running)
        self.assertEqual(first.host.cleanup_count, 0)
        self.assertEqual(len(self.panel._workspaces), 2)

    def test_shutdown_cleanup_visits_only_open_connection_hosts(self):
        first = self.panel._open_connection("rel-1")
        second = self.panel._open_connection("redis-1")
        self.assertEqual(len(self.factories), 2)
        first.host.cleanup_complete = False
        self.assertFalse(self.panel.agent_shutdown_cleanup())
        self.assertIs(self.panel._workspaces["rel-1"], first)
        first.host.cleanup_complete = True
        self.assertTrue(self.panel.agent_shutdown_cleanup())
        self.assertEqual(first.host.cleanup_count, 2)
        self.assertEqual(second.host.cleanup_count, 2)
        self.assertNotIn("mongo-1", self.panel._workspaces)

    def test_manual_aggregate_blocks_commit_for_unknown_before_any_host_action(self):
        clean = SimpleNamespace(
            tab_id="clean-tab", generation=1, state="DIRTY", busy=False, cleanup_complete=False,
        )
        unknown = SimpleNamespace(
            tab_id="unknown-tab", generation=2, state="UNKNOWN", busy=False, cleanup_complete=False,
        )
        hosts = {
            "rel-1": _ManualWorkspaceHost(
                SimpleNamespace(sessions=(clean,), can_commit=True),
                preflight=("decision_required", "decision_required", "decision_required"),
            ),
            "redis-1": _ManualWorkspaceHost(
                SimpleNamespace(sessions=(unknown,), can_commit=False),
                preflight=("decision_required", "decision_required", "decision_required"),
            ),
        }
        self.panel._host_factory = lambda profile, _language: hosts[profile["id"]]
        first = self.panel._open_connection("rel-1")
        second = self.panel._open_connection("redis-1")

        status = self.panel.manual_shutdown_cleanup("commit")

        self.assertEqual(getattr(status, "value", status), "decision_required")
        self.assertEqual(first.host.actions, [])
        self.assertEqual(second.host.actions, [])
        self.assertEqual(self.panel.manual_shutdown_snapshot().sessions, (clean, unknown))
        self.assertFalse(self.panel.manual_shutdown_snapshot().can_commit)

        self.assertTrue(self.panel.manual_shutdown_gate(True))
        status = self.panel.manual_shutdown_cleanup("rollback")
        self.assertEqual(getattr(status, "value", status), "ready")
        self.assertEqual(first.host.actions, ["rollback"])
        self.assertEqual(second.host.actions, ["rollback"])
        self.assertEqual(first.host.gates, [True])
        self.assertEqual(second.host.gates, [True])

    def test_manual_gate_blocks_new_workspace_and_query_without_disposing_current_host(self):
        session = SimpleNamespace(
            tab_id="tab-1", generation=1, state="CLEAN", busy=False, cleanup_complete=False,
        )
        host = _ManualWorkspaceHost(
            SimpleNamespace(sessions=(session,), can_commit=True),
            preflight=("ready_to_close",),
        )
        self.panel._host_factory = lambda _profile, _language: host
        workspace = self.panel._open_connection("rel-1")
        self.panel.manual_shutdown_gate(True)

        self.assertIsNone(self.panel._open_connection("redis-1"))
        self.panel._new_query()
        self.assertEqual(host.query_count, 0)
        self.assertIs(self.panel._workspaces["rel-1"], workspace)
        self.assertEqual(host.actions, [])
        self.assertEqual(host.gates, [True])

        self.panel.manual_shutdown_gate(False)
        self.panel._new_query()
        self.assertEqual(host.query_count, 1)
        self.assertEqual(host.gates, [True, False])

    def test_unknown_workspace_decision_is_compact_and_warns_rollback_is_not_proof(self):
        from PyQt6.QtWidgets import QLabel, QMessageBox, QPushButton

        unknown = SimpleNamespace(
            tab_id="unknown-tab", generation=1, state="UNKNOWN", busy=False, cleanup_complete=False,
        )
        snapshot = SimpleNamespace(sessions=(unknown,), can_commit=False)
        self.panel.set_language("en")
        captured = {}

        def fake_exec(box):
            captured["width"] = box.width()
            captured["text"] = box.text()
            captured["buttons"] = {
                button.text(): button.isEnabled()
                for button in box.findChildren(QPushButton)
            }
            label = box.findChild(QLabel, "qt_msgbox_label")
            captured["wraps"] = label.wordWrap() if label is not None else False

        with patch.object(QMessageBox, "exec", new=fake_exec):
            selected = self.panel._choose_workspace_manual_disposition(snapshot)

        self.assertIsNone(selected)
        self.assertLessEqual(captured["width"], 460)
        self.assertFalse(captured["buttons"]["Commit and close"])
        self.assertTrue(captured["buttons"]["Confirm rollback and close"])
        self.assertTrue(captured["buttons"]["Keep workspace"])
        self.assertTrue(captured["wraps"])
        self.assertIn("does not prove", captured["text"])

    def test_workspace_close_cancel_and_pending_cleanup_preserve_owner_until_agent_finishes(self):
        unknown = SimpleNamespace(
            tab_id="unknown-tab", generation=1, state="UNKNOWN", busy=False, cleanup_complete=False,
        )
        cancel_host = _ManualWorkspaceHost(
            SimpleNamespace(sessions=(unknown,), can_commit=False),
            preflight=("decision_required",),
        )
        self.panel._host_factory = lambda _profile, _language: cancel_host
        cancelled_workspace = self.panel._open_connection("rel-1")
        self.panel._choose_workspace_manual_disposition = Mock(return_value=None)
        cancelled_index = self.panel.workspace_tabs.indexOf(cancelled_workspace)
        self.panel._close_workspace_tab(cancelled_index)
        self.assertIs(self.panel._workspaces["rel-1"], cancelled_workspace)
        self.assertEqual(cancel_host.actions, ["cancel"])
        self.assertNotIn("close", cancel_host.actions)
        self.assertNotIn("commit", cancel_host.actions)
        self.assertNotIn("rollback", cancel_host.actions)
        self.assertEqual(cancel_host.gates, [])
        self.assertEqual(cancel_host.workbench_calls, 0)
        self.assertEqual(cancel_host.agent_calls, 0)
        self.assertIn("rel-1", self.panel._workspaces)

        clean = SimpleNamespace(
            tab_id="clean-tab", generation=1, state="CLEAN", busy=False, cleanup_complete=False,
        )
        pending_host = _ManualWorkspaceHost(
            SimpleNamespace(sessions=(clean,), can_commit=True),
            preflight=("ready_to_close", "ready_to_close", "pending", "ready"),
            action_result="pending",
            workbench_results=(False, True),
            agent_results=(False, True),
        )
        self.panel._host_factory = lambda _profile, _language: pending_host
        pending_workspace = self.panel._open_connection("redis-1")
        scheduled = []
        pending_index = self.panel.workspace_tabs.indexOf(pending_workspace)
        with patch(
            "panels.data_center_panel.QTimer.singleShot",
            side_effect=lambda ms, callback: scheduled.append((ms, callback)),
        ):
            self.panel._close_workspace_tab(pending_index)
            self.assertIs(self.panel._workspaces["redis-1"], pending_workspace)
            self.assertEqual(self.panel._workspace_close_states["redis-1"]["stage"], "manual")
            self.assertIn("正在等待", self.panel.catalog_warning.text())
            self.assertEqual(pending_host.workbench_calls, 0)
            self.panel._poll_workspace_close("redis-1")  # manual still pending
            self.assertIs(self.panel._workspaces["redis-1"], pending_workspace)
            self.panel._poll_workspace_close("redis-1")  # workbench still running
            self.assertIs(self.panel._workspaces["redis-1"], pending_workspace)
            self.assertEqual(pending_host.agent_calls, 0)
            self.panel._poll_workspace_close("redis-1")  # agent still running
            self.assertIs(self.panel._workspaces["redis-1"], pending_workspace)
            self.panel._poll_workspace_close("redis-1")  # final owner cleanup

        self.assertNotIn("redis-1", self.panel._workspaces)
        self.assertEqual(pending_host.actions, ["close"])
        self.assertEqual(pending_host.workbench_calls, 2)
        self.assertEqual(pending_host.agent_calls, 2)
        self.assertTrue(all(ms == 100 for ms, _callback in scheduled))

    def test_unknown_rollback_close_keeps_unknown_outcome_notice_after_owner_cleanup(self):
        unknown = SimpleNamespace(
            tab_id="unknown-tab", generation=1, state="UNKNOWN", busy=False, cleanup_complete=False,
        )
        host = _ManualWorkspaceHost(
            SimpleNamespace(sessions=(unknown,), can_commit=False),
            preflight=("decision_required", "decision_required"),
        )
        self.panel._host_factory = lambda _profile, _language: host
        workspace = self.panel._open_connection("rel-1")
        self.panel._choose_workspace_manual_disposition = Mock(return_value="rollback")
        self.panel._close_workspace_tab(self.panel.workspace_tabs.indexOf(workspace))

        self.assertNotIn("rel-1", self.panel._workspaces)
        self.assertEqual(host.actions, ["rollback"])
        self.assertEqual(host.gates, [True])
        self.assertIn("仍未知", self.panel.summary_text.text())
        self.assertIn("不证明", self.panel.summary_text.text())

    def test_unknown_import_ack_notice_survives_pending_cleanup(self):
        unknown_import = SimpleNamespace(
            tab_id="import-tab", generation=1, state="UNKNOWN", busy=False,
            cleanup_complete=False, owner_kind="import",
        )
        host = _ManualWorkspaceHost(
            SimpleNamespace(sessions=(unknown_import,), can_commit=False),
            preflight=("decision_required", "decision_required", "pending", "ready"),
            action_result="pending",
        )
        self.panel.language = "en"
        self.panel._host_factory = lambda _profile, _language: host
        workspace = self.panel._open_connection("rel-1")
        self.panel._choose_workspace_manual_disposition = Mock(return_value="rollback")
        scheduled = []
        with patch(
            "panels.data_center_panel.QTimer.singleShot",
            side_effect=lambda ms, callback: scheduled.append((ms, callback)),
        ):
            self.panel._close_workspace_tab(self.panel.workspace_tabs.indexOf(workspace))
            self.assertIs(self.panel._workspaces["rel-1"], workspace)
            self.assertEqual(self.panel._workspace_close_states["rel-1"]["stage"], "manual")
            self.assertTrue(self.panel._workspace_close_states["rel-1"]["unknown_import_ack"])
            self.panel._poll_workspace_close("rel-1")  # still waiting for importer retirement
            self.assertIs(self.panel._workspaces["rel-1"], workspace)
            self.panel._poll_workspace_close("rel-1")  # owner released, finish workspace close

        self.assertNotIn("rel-1", self.panel._workspaces)
        self.assertEqual(host.actions, ["rollback"])
        notice = self.panel.summary_text.text()
        self.assertIn("acknowledged and released", notice)
        self.assertIn("new database rollback", notice)
        self.assertIn("without", notice)
        self.assertIn("does not prove", notice)
        self.assertTrue(all(milliseconds == 100 for milliseconds, _callback in scheduled))

    def test_panel_close_event_refuses_unresolved_manual_owner_without_cleanup(self):
        unknown = SimpleNamespace(
            tab_id="unknown-tab", generation=1, state="UNKNOWN", busy=False, cleanup_complete=False,
        )
        host = _ManualWorkspaceHost(
            SimpleNamespace(sessions=(unknown,), can_commit=False),
            preflight=("decision_required",),
        )
        self.panel._host_factory = lambda _profile, _language: host
        workspace = self.panel._open_connection("rel-1")

        class CloseEvent:
            accepted = False
            ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        event = CloseEvent()
        self.panel.closeEvent(event)

        self.assertTrue(event.ignored)
        self.assertFalse(event.accepted)
        self.assertIs(self.panel._workspaces["rel-1"], workspace)
        self.assertEqual(host.actions, [])
        self.assertEqual(host.workbench_calls, 0)
        self.assertEqual(host.agent_calls, 0)

    def test_narrow_catalog_header_fits_without_clipping_visible_controls(self):
        from PyQt6.QtGui import QFont

        for language in ("zh", "en"):
            panel = DataCenterPanel(
                language,
                catalog=WorkspaceCatalog(loader=lambda: [_profile("layout")]),
                host_factory=lambda *_args: QWidget(),
            )
            panel.resize(960, 760)
            panel.setFont(QFont("Microsoft YaHei UI", 13))
            panel.show()
            APP.processEvents()
            # V1.1：没有全局工具条；目录头部只有 搜索 + 新建 + ⋯
            self.assertFalse(panel.findChild(QWidget, "data-center-toolbar").isVisible())
            pane = panel.catalog_pane
            visible = [panel.search_edit, panel.new_btn, panel.connection_menu_btn]
            for control in visible:
                self.assertTrue(control.isVisible(), control.toolTip())
                self.assertIs(control.parentWidget(), pane)
                self.assertTrue(pane.rect().contains(control.geometry()), control.toolTip())
            self.assertGreaterEqual(panel.search_edit.width(), 80)
            for index, left in enumerate(visible):
                for right in visible[index + 1:]:
                    self.assertFalse(left.geometry().intersects(right.geometry()))
            panel.hide()
            panel.deleteLater()
            APP.processEvents()

    def test_unknown_environment_is_localized_and_group_path_segments_are_safe(self):
        rows = [
            None,
            _profile("bad-path", group_path=["Core", None, 17], environment="vendor-qa"),
            _profile("good-sibling", group_path="Core/Payments", environment="test"),
        ]
        catalog = WorkspaceCatalog(loader=lambda: rows)
        summaries = catalog.refresh()
        summary = next(item for item in summaries if item.connection_id == "bad-path")
        self.assertEqual(len(summaries), 2)
        self.assertIsNotNone(catalog.get_profile("good-sibling"))
        self.assertEqual(summary.group_path, ("Core",))
        self.assertEqual(summary.environment_key, "unknown")
        self.assertIn("malformed project path", " ".join(catalog.record_errors))
        panel = DataCenterPanel("zh", catalog=catalog, host_factory=lambda *_args: QWidget())
        self.addCleanup(panel.deleteLater)
        tooltip = panel.tree.connection_source_item("bad-path").toolTip()
        self.assertIn("未知环境", tooltip)
        self.assertNotIn("Unknown environment", tooltip)

    def test_failed_loader_keeps_last_good_catalog_and_shows_local_notice(self):
        fail = {"enabled": False}

        def loader():
            if fail["enabled"]:
                raise OSError("isolated test failure")
            return [_profile("stable")]

        catalog = WorkspaceCatalog(loader=loader)
        panel = DataCenterPanel("en", catalog=catalog, host_factory=lambda *_args: QWidget())
        self.addCleanup(panel.deleteLater)
        fail["enabled"] = True
        panel.refresh_catalog()
        self.assertIsNotNone(catalog.get_summary("stable"))
        self.assertEqual(panel.tree.source_model.rowCount(), 1)
        self.assertFalse(panel.catalog_warning.isHidden())
        self.assertIn("OSError", panel.catalog_warning.text())

    def test_default_reader_keeps_last_good_data_on_json_shape_and_io_errors(self):
        import config

        with tempfile.TemporaryDirectory(prefix="dc04-reader-") as temp_dir:
            store_path = Path(temp_dir) / "connections.json"
            store_path.write_text(
                json.dumps({"items": [_profile("strict-stable")]}),
                encoding="utf-8",
            )
            saver = Mock(side_effect=lambda item, _password: dict(item))
            with patch.object(config, "HARNESS_CONNECTIONS_FILE", str(store_path)):
                catalog = WorkspaceCatalog(saver=saver, deleter=Mock())
                panel = DataCenterPanel("en", catalog=catalog, host_factory=lambda *_args: QWidget())
                self.addCleanup(panel.deleteLater)
                self.assertIsNotNone(catalog.get_summary("strict-stable"))

                store_path.write_text(json.dumps({"items": []}), encoding="utf-8")
                panel.refresh_catalog()
                self.assertIsNone(catalog.get_summary("strict-stable"))
                self.assertEqual(catalog.last_error, "")
                store_path.write_text(
                    json.dumps({"items": [_profile("strict-stable")]}),
                    encoding="utf-8",
                )
                panel.refresh_catalog()
                self.assertIsNotNone(catalog.get_summary("strict-stable"))

                failures = (
                    ("{invalid json", "JSONDecodeError"),
                    (json.dumps({"connections": []}), "ValueError"),
                )
                for body, expected_error in failures:
                    store_path.write_text(body, encoding="utf-8")
                    panel.refresh_catalog()
                    self.assertIsNotNone(catalog.get_summary("strict-stable"))
                    self.assertIn(expected_error, panel.catalog_warning.text())
                    self.assertNotIn(str(store_path), panel.catalog_warning.text())
                    self.assertNotIn("invalid json", panel.catalog_warning.text())

                self.assertEqual(catalog.save(_profile("must-not-overwrite")), {})
                saver.assert_not_called()

                with patch("builtins.open", side_effect=PermissionError("private path marker")):
                    panel.refresh_catalog()
                self.assertIsNotNone(catalog.get_summary("strict-stable"))
                self.assertIn("PermissionError", panel.catalog_warning.text())
                self.assertNotIn("private path marker", panel.catalog_warning.text())

                store_path.unlink()
                panel.refresh_catalog()
                self.assertIsNotNone(catalog.get_summary("strict-stable"))
                self.assertIn("FileNotFoundError", panel.catalog_warning.text())
                self.assertEqual(catalog.save(_profile("must-not-overwrite")), {})
                saver.assert_not_called()

                fresh_catalog = WorkspaceCatalog()
                self.assertEqual(fresh_catalog.refresh(), ())
                self.assertEqual(fresh_catalog.last_error, "")

    def test_new_default_catalog_can_refresh_until_file_is_created(self):
        import config

        with tempfile.TemporaryDirectory(prefix="dc-new-catalog-") as temp_dir:
            store_path = Path(temp_dir) / "connections.json"
            with patch.object(config, "HARNESS_CONNECTIONS_FILE", str(store_path)):
                catalog = WorkspaceCatalog(saver=Mock(), deleter=Mock())
                for _ in range(3):
                    self.assertEqual(catalog.refresh(), ())
                    self.assertEqual(catalog.last_error, "")
                self.assertFalse(store_path.exists())

                store_path.write_text(json.dumps({"items": []}), encoding="utf-8")
                self.assertEqual(catalog.refresh(), ())
                self.assertEqual(catalog.last_error, "")
                store_path.unlink()
                self.assertEqual(catalog.refresh(), ())
                self.assertIn("FileNotFoundError", catalog.last_error)

    def test_duplicate_ids_are_visible_but_never_openable(self):
        catalog = WorkspaceCatalog(loader=lambda: [
            _profile("duplicate", name="first"),
            _profile("duplicate", name="second"),
        ])
        self.assertEqual(catalog.refresh(), ())
        self.assertEqual(catalog.ambiguous_ids, ("duplicate",))
        self.assertIsNone(catalog.get_profile("duplicate"))
        made = []
        panel = DataCenterPanel(
            "en", catalog=catalog,
            host_factory=lambda *args: made.append(args) or QWidget(),
        )
        self.addCleanup(panel.deleteLater)
        found = None
        model = panel.tree.source_model

        def visit(parent):
            nonlocal found
            for row in range(model.rowCount(parent)):
                index = model.index(row, 0, parent)
                if index.data(NODE_KIND_ROLE) == "ambiguous":
                    found = index
                    return
                visit(index)

        visit(model.invisibleRootItem().index())
        self.assertIsNotNone(found)
        self.assertEqual(found.data(AMBIGUOUS_ID_ROLE), "duplicate")
        self.assertEqual(found.data(CONNECTION_ID_ROLE), None)
        self.assertEqual(panel.tree.source_model.itemFromIndex(found).rowCount(), 0)
        self.assertTrue(any("Duplicate ID" in text for text in self._tree_texts(panel)))
        self.assertEqual(made, [])

    def test_dialog_save_payload_keeps_unknown_vendor_and_secret_tokens(self):
        dialog = _WorkspaceConnectionDialog("en", _profile("dialog", vendor_option="x"))
        try:
            item, password = dialog.payload()
            self.assertEqual(password, "")
            self.assertEqual(item["password"], "enc:test-secret-token")
            self.assertEqual(item["vendor_extension"], {"retained": True})
            self.assertEqual(item["vendor_option"], "x")
            self.assertEqual(item["environment"], "test")
            self.assertEqual(item["group_path"], "Payments/ledger")
        finally:
            dialog.deleteLater()

    def test_production_factory_is_lazy_and_keeps_native_tools_connection_bound(self):
        from config import save_layout_splitter, save_settings
        import panels.ai_workbench_panel as sql_module
        import panels.db_mongodb_panel as mongo_module
        import panels.db_redis_panel as redis_module
        from ui.data_center import integration as agent_integration

        rows = [
            _profile("sql-real", "mysql"),
            _profile("redis-real", "redis", port=6379),
            _profile("mongo-real", "mongodb", port=27017),
        ]
        catalog = WorkspaceCatalog(loader=lambda: [dict(row) for row in rows])
        starts = []
        patches = [
            patch.object(sql_module, "load_connections", side_effect=lambda: [dict(r) for r in rows]),
            patch.object(sql_module, "load_snapshot", return_value=None),
            patch.object(redis_module, "load_connections", side_effect=lambda: [dict(r) for r in rows]),
            patch.object(mongo_module, "load_connections", side_effect=lambda: [dict(r) for r in rows]),
            patch.object(redis_module._RedisWorker, "start", lambda worker: starts.append(worker.kind)),
            patch.object(mongo_module._MongoWorker, "start", lambda worker: starts.append(worker.kind)),
            patch.object(redis_module, "open_connection", side_effect=AssertionError("unexpected Redis I/O")),
            patch.object(mongo_module, "open_connection", side_effect=AssertionError("unexpected Mongo I/O")),
            patch.object(agent_integration, "DataCenterWorkbenchAgent", _FakeAgent),
            patch("config.save_settings", side_effect=lambda value: value),
            patch("config.save_layout_splitter", side_effect=lambda *_args: None),
        ]
        for active in patches:
            active.start()
        panel = DataCenterPanel("en", catalog=catalog)
        try:
            panel.resize(1160, 760)
            panel.show()
            APP.processEvents()
            for connection_id in ("sql-real", "redis-real", "mongo-real"):
                self.assertTrue(panel.tree.select_connection(connection_id))
            self.assertEqual(starts, [])

            sql_workspace = panel._open_connection("sql-real")
            redis_workspace = panel._open_connection("redis-real")
            mongo_workspace = panel._open_connection("mongo-real")
            APP.processEvents()
            self.assertEqual(starts, [])
            sql_host = sql_workspace.host
            redis_host = redis_workspace.host
            mongo_host = mongo_workspace.host
            self.assertEqual(sql_host._connection_id, "sql-real")
            self.assertEqual(sql_host._current_conn()["id"], "sql-real")
            self.assertEqual(redis_host._data_center_connection_id, "redis-real")
            self.assertEqual(redis_host._current_conn()["id"], "redis-real")
            self.assertEqual(mongo_host._data_center_connection_id, "mongo-real")
            self.assertEqual(mongo_host._current_conn()["id"], "mongo-real")
            self.assertTrue(hasattr(sql_host, "_new_sql_tab"))
            self.assertTrue(sql_host._embedded_compact_mode)
            self.assertTrue(sql_host.page_header.isHidden())
            self.assertTrue(sql_host.connection_toolbar.isHidden())
            self.assertTrue(hasattr(redis_host, "refresh_btn"))
            self.assertTrue(hasattr(mongo_host, "query_btn"))

            # Only explicit refresh buttons enter the existing engine worker paths.
            redis_host.refresh_btn.click()
            mongo_host.refresh_btn.click()
            self.assertEqual(starts, ["bootstrap", "collections"])
            sql_host._new_sql_tab()
            self.assertEqual(sql_host._current_conn()["id"], "sql-real")
            self.assertEqual(len(panel._workspaces), 3)
        finally:
            panel.hide()
            panel.deleteLater()
            APP.processEvents()
            for active in reversed(patches):
                active.stop()

    def test_actual_default_host_compact_layout_keeps_agent_and_typed_result_reachable(self):
        """Exercise the real DataCenterPanel + native SQL host at narrow and wide sizes."""
        from decimal import Decimal
        from pathlib import Path
        import tempfile

        from tests.test_data_center_agent_layout import DataCenterAgentLayoutTests
        from tools.data_center.manual_session import ManualColumn, ManualQueryPage
        from PyQt6.QtCore import QPoint, QRect

        fixture = DataCenterAgentLayoutTests("test_same_production_host_moves_between_drawer_and_rail_without_reset")
        DataCenterAgentLayoutTests.setUpClass()
        setup_completed = False
        try:
            fixture.setUp()
            setup_completed = True
            panel = fixture.panel
            workspace = fixture.workspace
            host = fixture.host
            self.assertTrue(host._embedded_compact_mode)
            self.assertTrue(host.page_header.isHidden())
            self.assertTrue(host.connection_toolbar.isHidden())
            self.assertTrue(host.narrow_chrome.isHidden())

            manual = host.manual_query_host
            state = manual._current_state()
            page = ManualQueryPage(
                query_id="TEMP-layout-query",
                tab_id=state.target.tab_id,
                generation=state.generation,
                columns=(ManualColumn("id", "INTEGER"), ManualColumn("amount", "DECIMAL")),
                rows=((42, Decimal("19.25")),),
                page_number=1,
                page_size=50,
                has_more=False,
                result_bytes=16,
                rowcount=1,
            )
            manual.result_model.replace_page(page)
            manual.result_view.show()
            self.assertEqual(manual.result_model.rowCount(), 1)
            self.assertEqual(manual.result_model.columnCount(), 2)

            screenshot_dir = Path(tempfile.gettempdir()) / "pengtools-manual-lifecycle"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshots = []
            for language in ("zh", "en"):
                panel.set_language(language)
                for width, height in ((960, 640), (1160, 720)):
                    with self.subTest(language=language, size=(width, height)):
                        panel.resize(width, height)
                        panel.apply_layout_mode("wide", False)
                        APP.processEvents()
                        self.assertTrue(host._embedded_compact_mode)
                        self.assertTrue(host.page_header.isHidden())
                        self.assertTrue(host.connection_toolbar.isHidden())
                        self.assertTrue(host.narrow_chrome.isHidden())

                        # Show the same production Agent host in its workspace rail/drawer.
                        panel.agent_btn.click()
                        APP.processEvents()
                        self.assertTrue(workspace.agent_visible)
                        self.assertTrue(workspace.agent_widget.isVisible())
                        self.assertTrue(fixture.agent_panel.prompt_input.isVisible())
                        screenshot = screenshot_dir / f"data-center-{language}-{width}x{height}.png"
                        self.assertTrue(panel.grab().save(str(screenshot), "PNG"))
                        screenshots.append(str(screenshot))
                        panel.agent_btn.click()
                        APP.processEvents()

                        self.assertTrue(host.isVisible())
                        # 编辑器 ⋯ 已并入工作台「更多」：同一组动作从这一个按钮进入
                        self.assertFalse(host.editor_more_btn.isVisible())
                        self.assertTrue(manual.workbench_more_btn.isVisible())
                        self.assertIs(host.editor_format_action.parent(), host.editor_more_menu)
                        self.assertIs(host.editor_clear_action.parent(), host.editor_more_menu)
                        self.assertIs(host.editor_legacy_panels_action.parent(), host.editor_more_menu)
                        def click_overflow_action(action):
                            menu = manual.workbench_more_menu
                            manual._refresh_workbench_actions()
                            menu.popup(
                                manual.workbench_more_btn.mapToGlobal(manual.workbench_more_btn.rect().bottomLeft())
                            )
                            APP.processEvents()
                            self.assertTrue(menu.isVisible())
                            self.assertIn(action, menu.actions())
                            action_rect = menu.actionGeometry(action)
                            QTest.mouseClick(
                                menu,
                                Qt.MouseButton.LeftButton,
                                pos=action_rect.center(),
                            )
                            APP.processEvents()

                        editor = host._current_editor()
                        self.assertIsNotNone(editor)
                        editor.setPlainText("select 42 as id")
                        click_overflow_action(host.editor_format_action)
                        self.assertTrue(editor.toPlainText().strip())
                        click_overflow_action(host.editor_clear_action)
                        self.assertEqual(editor.toPlainText(), "")
                        editor.setPlainText("select 42 as id")

                        legacy_action = host.editor_legacy_panels_action
                        self.assertFalse(legacy_action.isChecked())
                        click_overflow_action(legacy_action)
                        self.assertTrue(legacy_action.isChecked())
                        self.assertTrue(host.side_tabs.isVisible())
                        self.assertGreaterEqual(host.side_tabs.count(), 2)
                        host.side_tabs.setCurrentIndex(1)
                        self.assertEqual(host.side_tabs.currentIndex(), 1)
                        click_overflow_action(legacy_action)
                        self.assertFalse(legacy_action.isChecked())
                        self.assertFalse(host.side_tabs.isVisible())

                        for button in (
                            host.run_btn,
                            host.new_tab_btn,
                            manual.commit_btn,
                            manual.rollback_btn,
                            manual.stop_btn,
                        ):
                            # 事务按钮（提交/回滚/停止）只在可用时出现
                            self.assertEqual(button.isVisible(), button.isEnabled(), button.text())
                            if button.isVisible():
                                self.assertGreaterEqual(
                                    button.width(), button.sizeHint().width(), button.text()
                                )
                        self.assertTrue(manual.result_view.isVisible())
                        self.assertGreaterEqual(manual.result_view.height(), 120)
                        scroll = host.workspace_scroll
                        content = scroll.widget()
                        result_origin = manual.result_view.mapTo(content, QPoint(0, 0))
                        result_rect = QRect(result_origin, manual.result_view.size())
                        self.assertTrue(content.rect().contains(result_rect))
                        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                        self.assertEqual(manual.result_model.data(manual.result_model.index(0, 0)), "42")
                        self.assertEqual(manual.result_model.data(manual.result_model.index(0, 1)), "19.25")
            self.actual_workspace_screenshots = tuple(screenshots)
        finally:
            if setup_completed:
                fixture.tearDown()
            fixture.doCleanups()

    @staticmethod
    def _tree_texts(panel):
        values = []
        model = panel.tree.source_model

        def collect(parent):
            for row in range(model.rowCount(parent)):
                index = model.index(row, 0, parent)
                values.append(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
                collect(index)

        collect(model.invisibleRootItem().index())
        return values


if __name__ == "__main__":
    unittest.main()
