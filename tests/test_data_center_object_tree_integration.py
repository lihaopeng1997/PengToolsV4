"""TEMP/fake-host integration of the lazy object tree and Data Center panel."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontInfo, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QPushButton, QWidget

from panels.data_center_panel import DataCenterPanel
from tools.data_center.host_nosql import NoSQLReadOnlyHost
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.object_tree import TreeNode, TreePage, TreeTarget
from tools.data_center.object_tree_composition import ObjectTreeComposition
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.theme_manager import ThemeManager
from ui.data_center.workspace_tree import (
    CONNECTION_ID_ROLE,
    NODE_KIND_ROLE,
    TREE_CLOSING_ROLE,
    TREE_GENERATION_ROLE,
    TREE_LOADED_ROLE,
    TREE_NODE_ROLE,
    TREE_STATE_ROLE,
    TREE_TARGET_ROLE,
)


APP = QApplication.instance() or QApplication([])


def _profile(connection_id: str, dialect: str = "mysql", **changes) -> dict:
    value = {
        "id": connection_id,
        "name": f"TEMP {connection_id}",
        "dialect": dialect,
        "mode": dialect,
        "host": "fake.invalid",
        "port": 3306 if dialect == "mysql" else 27017 if dialect == "mongodb" else 6379,
        "database": "profile-database",
        "username": "fixture-user",
        "password": "",
        "environment": "test",
        "group_path": "TEMP/ObjectTree",
    }
    if dialect == "redis":
        value["auth_mode"] = "none"
        value["mode"] = "standalone"
    elif dialect == "mongodb":
        value["mode"] = "standalone"
    value.update(changes)
    return value


class _SQLCursor:
    def __init__(self, connection: "_SQLConnection") -> None:
        self.connection = connection
        self.rows: list[tuple] = []
        self.offset = 0

    def execute(self, sql, parameters=()):
        self.connection.events.append(("execute", threading.get_ident(), str(sql)))
        folded = str(sql).casefold()
        values = tuple(parameters or ())
        try:
            self.offset = int(values[-1]) if len(values) >= 2 else 0
        except (TypeError, ValueError):
            self.offset = 0
        if "information_schema.schemata" in folded:
            self.rows = self.connection.database_rows
        elif "information_schema.tables" in folded:
            self.rows = self.connection.next_table_page()
        else:
            self.rows = []

    def fetchmany(self, size):
        return self.rows[self.offset:self.offset + int(size)]

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _SQLConnection:
    def __init__(
        self,
        events: list,
        *,
        database_rows: list[tuple] | None = None,
        table_rows: list[tuple] | None = None,
        table_gate: threading.Event | None = None,
        table_entered: threading.Event | None = None,
        table_responses: list[list[tuple]] | None = None,
    ) -> None:
        self.events = events
        self.database_rows = list(database_rows or [("fixture-db",)])
        self.table_rows = list(table_rows or [("orders", "BASE TABLE")])
        self.table_gate = table_gate
        self.table_entered = table_entered
        self.table_responses = list(table_responses or ())
        self.table_query_count = 0
        self.autocommit = True
        self.closed = False

    def cursor(self):
        return _SQLCursor(self)

    def next_table_page(self):
        self.table_query_count += 1
        if self.table_entered is not None:
            self.table_entered.set()
        if self.table_gate is not None:
            self.table_gate.wait(8)
        if self.table_responses:
            return self.table_responses.pop(0)
        return self.table_rows

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


class _MongoDatabase:
    def __init__(self, client: "_MongoClient", name: str) -> None:
        self.client = client
        self.name = name

    def list_collections(self, nameOnly=True, batchSize=0):
        self.client.events.append(("collections", threading.get_ident(), self.name))
        return [{"name": "fixture-collection"}]


class _MongoClient:
    def __init__(self, events: list) -> None:
        self.events = events

    def list_databases(self, nameOnly=True, authorizedDatabases=True, batchSize=0):
        self.events.append(("databases", threading.get_ident()))
        return [{"name": "snapshot-database"}]

    def get_database(self, name):
        return _MongoDatabase(self, str(name))

    def __getitem__(self, name):
        return self.get_database(name)

    def close(self):
        self.events.append(("mongo-close", threading.get_ident()))


class _RedisClient:
    def __init__(self, events: list) -> None:
        self.events = events
        self.connection_pool = type("Pool", (), {"connection_kwargs": {"socket_timeout": 3}})()

    def scan(self, cursor=0, match=None, count=None):
        self.events.append(("scan", threading.get_ident(), cursor, match, count))
        return 0, [b"fixture/key"]

    def type(self, name):
        self.events.append(("type", threading.get_ident(), name))
        return b"string"

    def ttl(self, name):
        self.events.append(("ttl", threading.get_ident(), name))
        return -1

    def close(self):
        self.events.append(("redis-close", threading.get_ident()))


class _PanelHost(QWidget):
    def __init__(self, _profile, _language):
        super().__init__()
        self.manual_calls: list = []
        self.gates: list[bool] = []
        self.workbench_calls = 0
        self.agent_calls = 0
        self.manual_snapshot_value = type("Snapshot", (), {"sessions": (), "can_commit": True})()

    def manual_shutdown_snapshot(self):
        return self.manual_snapshot_value

    def manual_shutdown_cleanup(self, disposition=None):
        self.manual_calls.append(disposition)
        return "cancelled" if disposition == "cancel" else "ready"

    def manual_shutdown_gate(self, enabled):
        self.gates.append(bool(enabled))
        return True

    def workbench_shutdown_cleanup(self):
        self.workbench_calls += 1
        return True

    def agent_shutdown_cleanup(self):
        self.agent_calls += 1
        return True


def _catalog(rows: list[dict]) -> WorkspaceCatalog:
    return WorkspaceCatalog(
        loader=lambda: [dict(row) for row in rows],
        saver=lambda item, secret=None: dict(item),
        deleter=lambda _connection_id: None,
    )


def _spin_until(predicate, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.004)
    APP.processEvents()
    return bool(predicate())


def _expand_connection(panel: DataCenterPanel, connection_id: str) -> None:
    item = panel.tree.connection_source_item(connection_id)
    if item is None:
        raise AssertionError(f"missing TEMP connection row: {connection_id}")
    index = panel.tree.proxy_model.mapFromSource(item.index())
    panel.tree.collapse(index)
    panel.tree.expand(index)
    APP.processEvents()


def _expand_first_database(panel: DataCenterPanel, connection_id: str) -> None:
    connection = panel.tree.connection_source_item(connection_id)
    database = _find_child_by_kind(connection, "database") if connection is not None else None
    if database is None:
        raise AssertionError(f"missing TEMP database row for {connection_id}")
    index = panel.tree.proxy_model.mapFromSource(database.index())
    panel.tree.expand(index)
    APP.processEvents()


def _find_child_by_kind(parent, kind: str):
    for row in range(parent.rowCount()):
        child = parent.child(row)
        if str(child.data(NODE_KIND_ROLE) or "") == kind:
            return child
    return None


def _all_text(item) -> list[str]:
    values = [str(item.text())]
    for row in range(item.rowCount()):
        values.extend(_all_text(item.child(row)))
    return values


class DataCenterObjectTreeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.panels: list[DataCenterPanel] = []
        self.release_events: list[threading.Event] = []

    def tearDown(self):
        for event in self.release_events:
            event.set()
        for panel in self.panels:
            try:
                panel.workbench_shutdown_cleanup()
            except Exception:
                pass
        _spin_until(lambda: all(not panel._tree_owners for panel in self.panels), timeout=4)
        for panel in self.panels:
            panel.deleteLater()
        APP.processEvents()

    def _panel(self, rows, composition_factory):
        panel = DataCenterPanel(
            "en",
            catalog=_catalog(rows),
            host_factory=lambda profile, language: _PanelHost(profile, language),
            object_tree_composition_factory=composition_factory,
        )
        panel.resize(960, 640)
        self.panels.append(panel)
        return panel

    def _mysql_composition_factory(
        self,
        profiles: dict[str, dict],
        *,
        connector_factory=None,
        database_rows_factory=None,
        table_responses_factory=None,
    ):
        calls = {connection_id: {"snapshots": 0, "connectors": 0, "events": []} for connection_id in profiles}
        hosts = {}

        def make(connection_id: str):
            profile = profiles[connection_id]
            events = calls[connection_id]["events"]

            def connector(provider, profile, secret, **options):
                del provider, secret, options
                calls[connection_id]["connectors"] += 1
                gate, entered = (None, None)
                if connector_factory is not None:
                    gate, entered = connector_factory(connection_id, profile)
                databases = (
                    database_rows_factory(connection_id)
                    if database_rows_factory is not None else [("fixture-db",)]
                )
                table_responses = (
                    table_responses_factory(connection_id)
                    if table_responses_factory is not None else None
                )
                return _SQLConnection(
                    events,
                    database_rows=databases,
                    table_gate=gate,
                    table_entered=entered,
                    table_responses=table_responses,
                )

            relational = RelationalReadOnlyHost(
                connections={connection_id: profile},
                secret_loader=lambda _profile: "",
                connector=connector,
            )
            original_snapshot = relational.profile_snapshot

            def profile_snapshot(selected_id):
                calls[connection_id]["snapshots"] += 1
                return original_snapshot(selected_id)

            relational.profile_snapshot = profile_snapshot
            nosql = NoSQLReadOnlyHost(connections={}, secret_loader=lambda _profile: "")
            composition = ObjectTreeComposition(relational_host=relational, nosql_host=nosql)
            hosts[connection_id] = (relational, nosql, composition)
            return composition

        return make, calls, hosts

    def test_actual_panel_first_expand_pages_filters_and_rebuilds_without_extra_io(self):
        row = _profile("literal-search")
        rows = [row]
        profile = _profile("literal-search")
        all_databases = [("ledger.v2[prod]",)] + [(f"db-{index}",) for index in range(1, 52)]
        make, calls, _hosts = self._mysql_composition_factory(
            {"literal-search": profile},
            database_rows_factory=lambda _cid: all_databases,
        )
        panel = self._panel(rows, make)
        panel.show()
        APP.processEvents()
        item = panel.tree.connection_source_item("literal-search")
        self.assertFalse(panel.tree.isExpanded(panel.tree.proxy_model.mapFromSource(item.index())))
        self.assertEqual(calls["literal-search"]["snapshots"], 0)
        self.assertEqual(calls["literal-search"]["connectors"], 0)
        self.assertEqual(panel.tree.selected_connection_id(), "")
        self.assertTrue(panel.tree.select_connection("literal-search"))
        panel.refresh_catalog()
        self.assertEqual(calls["literal-search"]["snapshots"], 0)
        self.assertEqual(calls["literal-search"]["connectors"], 0)

        _expand_connection(panel, "literal-search")
        item = panel.tree.connection_source_item("literal-search")
        self.assertTrue(
            _spin_until(lambda: bool(item.data(TREE_LOADED_ROLE))),
            f"status={_all_text(item)!r}; calls={calls['literal-search']!r}; "
            f"owner={panel._tree_owners.get('literal-search')!r}",
        )
        owner = panel._tree_owners["literal-search"]
        self.assertEqual(owner.target.database, "")  # Catalog display database never becomes scope.
        self.assertEqual(calls["literal-search"]["connectors"], 1)
        self.assertEqual(item.rowCount(), 51)  # 50 objects and one More row.
        database = _find_child_by_kind(item, "database")
        self.assertIsNotNone(database)
        self.assertIn("ledger.v2[prod]", database.text())
        database_node = database.data(TREE_NODE_ROLE)
        self.assertEqual(database.toolTip(), database_node.name)
        original_node_ref = database_node.node_ref
        self.assertTrue(panel.tree.select_connection("literal-search"))

        # The signed branch survives a catalog rebuild; rebuilding/filtering are local only.
        target_before = item.data(TREE_TARGET_ROLE)
        root_before = item.data(TREE_NODE_ROLE)
        count_before = calls["literal-search"]["connectors"]
        panel.refresh_catalog()
        item = panel.tree.connection_source_item("literal-search")
        database = _find_child_by_kind(item, "database")
        self.assertEqual(item.data(TREE_TARGET_ROLE), target_before)
        self.assertEqual(item.data(TREE_NODE_ROLE), root_before)
        self.assertEqual(database.toolTip(), database.data(TREE_NODE_ROLE).name)
        self.assertEqual(database.data(TREE_NODE_ROLE).node_ref, original_node_ref)
        panel.tree.set_filter_text("ledger.v2[prod]")
        self.assertTrue(_spin_until(lambda: panel.tree.proxy_model.rowCount() > 0))
        filtered_database = panel.tree.proxy_model.mapFromSource(database.index())
        self.assertTrue(filtered_database.isValid())
        self.assertEqual(calls["literal-search"]["connectors"], count_before)

        # Explicit More replaces the prior sibling page instead of accumulating pages.
        panel.tree.set_filter_text("")
        item = panel.tree.connection_source_item("literal-search")
        more = next(
            (item.child(index) for index in range(item.rowCount())
             if item.child(index).data(TREE_STATE_ROLE) == "more"),
            None,
        )
        self.assertIsNotNone(more)
        more_index = panel.tree.proxy_model.mapFromSource(more.index())
        panel.tree.expand(more_index)
        self.assertTrue(
            _spin_until(lambda: item.rowCount() == 2),
            f"rows={_all_text(item)!r}; calls={calls['literal-search']!r}",
        )
        self.assertIsNone(_find_child_by_kind(item, "tree-state"))
        self.assertEqual(calls["literal-search"]["connectors"], count_before + 1)

    def test_close_a_waits_for_its_directory_owner_while_b_finishes_independently(self):
        rows = [_profile("a"), _profile("b")]
        profiles = {str(row["id"]): dict(row) for row in rows}
        a_gate = threading.Event()
        a_entered = threading.Event()
        self.release_events.append(a_gate)

        def connector_gate(connection_id, _selected_profile):
            return (a_gate, a_entered) if connection_id == "a" else (None, None)

        make, calls, _hosts = self._mysql_composition_factory(
            profiles,
            connector_factory=connector_gate,
        )
        panel = self._panel(rows, make)
        panel.show()
        workspace_a = panel._open_connection("a")
        workspace_b = panel._open_connection("b")
        self.assertIsNotNone(workspace_a)
        self.assertIsNotNone(workspace_b)
        self.assertEqual(calls["a"]["snapshots"], 0)
        _expand_connection(panel, "a")
        self.assertTrue(_spin_until(
            lambda: bool(panel.tree.connection_source_item("a").data(TREE_LOADED_ROLE))
        ))
        _expand_first_database(panel, "a")
        self.assertTrue(a_entered.wait(2))
        _expand_connection(panel, "b")
        self.assertTrue(_spin_until(
            lambda: bool(panel.tree.connection_source_item("b").data(TREE_LOADED_ROLE))
        ))
        database_b = _find_child_by_kind(panel.tree.connection_source_item("b"), "database")
        self.assertIsNotNone(database_b)
        _expand_first_database(panel, "b")
        self.assertTrue(_spin_until(lambda: bool(database_b.data(TREE_LOADED_ROLE))))

        index_a = panel.workspace_tabs.indexOf(workspace_a)
        panel._close_workspace_tab(index_a)
        owner_a = panel._tree_owners["a"]
        owner_b = panel._tree_owners["b"]
        self.assertTrue(owner_a.is_closing)
        self.assertFalse(owner_a.close().done())
        self.assertFalse(owner_b.is_closing)
        self.assertIn("a", panel._workspaces)
        self.assertTrue(panel.tree.connection_source_item("b").data(TREE_LOADED_ROLE))
        self.assertGreater(calls["b"]["connectors"], 0)
        self.assertEqual(workspace_a.host.manual_calls, [None, None])  # Snapshot rechecked after new-work gate.

        a_gate.set()
        self.assertTrue(_spin_until(lambda: "a" not in panel._workspaces, timeout=5))
        self.assertNotIn("a", panel._tree_owners)
        self.assertIn("b", panel._tree_owners)
        self.assertFalse(panel._tree_owners["b"].is_closing)
        self.assertTrue(panel.tree.connection_source_item("b").data(TREE_LOADED_ROLE))

    def test_tree_refresh_rebuild_and_directory_close_preserve_active_workspace_context(self):
        rows = [_profile("context-a"), _profile("context-b")]
        profiles = {str(row["id"]): dict(row) for row in rows}
        make, calls, _hosts = self._mysql_composition_factory(profiles)
        panel = self._panel(rows, make)
        panel.show()
        workspace_a = panel._open_connection("context-a")
        workspace_b = panel._open_connection("context-b")
        self.assertIsNotNone(workspace_a)
        self.assertIsNotNone(workspace_b)

        # Model a live manual tab context without constructing a database session.
        manual_tab = object()
        manual_snapshot = type("Snapshot", (), {
            "sessions": (manual_tab,),
            "can_commit": False,
        })()
        workspace_a.host.manual_snapshot_value = manual_snapshot
        panel.workspace_tabs.setCurrentWidget(workspace_a)
        self.assertEqual(panel.active_connection_id(), "context-a")

        # Directory selection and metadata operations on B must not change A's workspace tab.
        panel.tree.select_connection("context-b")
        _expand_connection(panel, "context-b")
        self.assertTrue(_spin_until(
            lambda: bool(panel.tree.connection_source_item("context-b").data(TREE_LOADED_ROLE))
        ))
        self.assertIs(panel.workspace_tabs.currentWidget(), workspace_a)
        self.assertEqual(panel.active_connection_id(), "context-a")

        panel.refresh_catalog()  # Includes catalog rebuild and selection restoration.
        self.assertIs(panel.workspace_tabs.currentWidget(), workspace_a)
        self.assertEqual(panel.active_connection_id(), "context-a")
        self.assertEqual(panel.tree.selected_connection_id(), "context-b")
        self.assertIs(workspace_a.host.manual_shutdown_snapshot(), manual_snapshot)
        self.assertIs(workspace_a.host.manual_shutdown_snapshot().sessions[0], manual_tab)

        panel.close_selected_directory()
        self.assertTrue(_spin_until(lambda: "context-b" not in panel._tree_owners, timeout=5))
        self.assertIs(panel.workspace_tabs.currentWidget(), workspace_a)
        self.assertEqual(panel.active_connection_id(), "context-a")
        self.assertIs(workspace_a.host.manual_shutdown_snapshot(), manual_snapshot)
        self.assertEqual(workspace_a.host.manual_calls, [])
        self.assertEqual(workspace_a.host.workbench_calls, 0)
        self.assertEqual(workspace_a.host.agent_calls, 0)
        self.assertGreater(calls["context-b"]["connectors"], 0)

    def test_global_cleanup_covers_catalog_only_owner_and_waits_for_qt_completion(self):
        rows = [_profile("catalog-only")]
        profiles = {"catalog-only": dict(rows[0])}
        gate = threading.Event()
        entered = threading.Event()
        self.release_events.append(gate)
        make, _calls, _hosts = self._mysql_composition_factory(
            profiles,
            connector_factory=lambda _cid, _profile: (gate, entered),
        )
        panel = self._panel(rows, make)
        panel.show()
        _expand_connection(panel, "catalog-only")
        self.assertTrue(_spin_until(
            lambda: bool(panel.tree.connection_source_item("catalog-only").data(TREE_LOADED_ROLE))
        ))
        _expand_first_database(panel, "catalog-only")
        self.assertTrue(entered.wait(2))
        owner = panel._tree_owners["catalog-only"]
        cleanup_threads = []
        owner.cleanup_finished.connect(lambda *_args: cleanup_threads.append(threading.get_ident()))
        self.assertEqual(panel.manual_shutdown_cleanup(None), "ready")
        self.assertFalse(panel.workbench_shutdown_cleanup())
        self.assertTrue(panel._tree_shutdown_started)
        self.assertIs(panel._tree_owners.get("catalog-only"), owner)
        self.assertTrue(owner.is_closing)

        gate.set()
        completed = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            APP.processEvents()
            completed = panel.workbench_shutdown_cleanup()
            if completed:
                break
            time.sleep(0.004)
        self.assertTrue(completed)
        self.assertNotIn("catalog-only", panel._tree_owners)
        self.assertTrue(cleanup_threads)
        self.assertTrue(all(thread_id == int(APP.thread().currentThreadId()) for thread_id in cleanup_threads))

    def test_manual_cancel_keeps_directory_owner_and_redis_whole_keyspace_is_explicit(self):
        redis_row = _profile("redis-all", "redis")
        cancel_row = _profile("redis-cancel", "redis")
        for row in (redis_row, cancel_row):
            row.pop("username", None)
            row.pop("password", None)
            row["database"] = 0
        rows = [redis_row, cancel_row]
        redis_events: list = []
        client_factory_calls: list = []

        def redis_client_factory(**_kwargs):
            client_factory_calls.append(threading.get_ident())
            return _RedisClient(redis_events)

        nosql_profiles = {str(row["id"]): dict(row) for row in rows}

        def make(connection_id):
            nosql = NoSQLReadOnlyHost(
                connections=nosql_profiles,
                secret_loader=lambda _profile: "",
                redis_client_factory=redis_client_factory,
            )
            relational = RelationalReadOnlyHost(connections={}, secret_loader=lambda _profile: "")
            return ObjectTreeComposition(relational_host=relational, nosql_host=nosql)

        panel = self._panel(rows, make)
        panel.show()
        dialog_exec = QDialog.DialogCode.Accepted

        def choose_all(dialog):
            checkbox = dialog.findChild(QCheckBox)
            self.assertIsNotNone(checkbox)
            checkbox.setChecked(True)
            return dialog_exec

        with patch.object(QDialog, "exec", new=choose_all):
            _expand_connection(panel, "redis-all")
            self.assertTrue(
                _spin_until(lambda: bool(
                    panel.tree.connection_source_item("redis-all").data(TREE_LOADED_ROLE)
                )),
                f"rows={_all_text(panel.tree.connection_source_item('redis-all'))!r}; "
                f"events={redis_events!r}; target={getattr(panel._tree_owners.get('redis-all'), 'target', None)!r}",
            )
        redis_owner = panel._tree_owners["redis-all"]
        self.assertEqual(redis_owner.target.redis_prefix, "")
        self.assertEqual(len([entry for entry in redis_events if entry[0] == "scan"]), 1)
        self.assertTrue(redis_events[0][3] in {"*", None})

        # The rejected scope dialog does not issue capture or treat blank input as all-keyspace.
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            _expand_connection(panel, "redis-cancel")
            APP.processEvents()
        cancel_owner = panel._tree_owners["redis-cancel"]
        self.assertFalse(cancel_owner.capture_pending)
        self.assertIsNone(cancel_owner.target)
        self.assertEqual(len(client_factory_calls), 1)

        # Application-level manual cancellation does not close either metadata owner.
        self.assertEqual(panel.manual_shutdown_cleanup("cancel").value, "cancelled")
        self.assertFalse(redis_owner.is_closing)
        self.assertFalse(cancel_owner.is_closing)

    def test_mongo_scope_comes_from_snapshot_and_catalog_rebuild_does_not_retarget(self):
        catalog_row = _profile("mongo-id", "mongodb", database="catalog-copy")
        host_profile = _profile("mongo-id", "mongodb", database="snapshot-database")
        for row in (catalog_row, host_profile):
            row.pop("username", None)
            row.pop("password", None)
        events: list = []
        client_factory_calls: list = []

        def mongo_factory(**_kwargs):
            client_factory_calls.append(threading.get_ident())
            return _MongoClient(events)

        def make(connection_id):
            nosql = NoSQLReadOnlyHost(
                connections={connection_id: host_profile},
                secret_loader=lambda _profile: "",
                mongo_client_factory=mongo_factory,
            )
            relational = RelationalReadOnlyHost(connections={}, secret_loader=lambda _profile: "")
            return ObjectTreeComposition(relational_host=relational, nosql_host=nosql)

        panel = self._panel([catalog_row], make)
        panel.show()
        _expand_connection(panel, "mongo-id")
        self.assertTrue(_spin_until(
            lambda: bool(panel.tree.connection_source_item("mongo-id").data(TREE_LOADED_ROLE))
        ))
        item = panel.tree.connection_source_item("mongo-id")
        owner = panel._tree_owners["mongo-id"]
        self.assertEqual(owner.target.database, "snapshot-database")
        self.assertNotEqual(owner.target.database, panel.catalog.get_summary("mongo-id").database)
        self.assertEqual(item.data(TREE_TARGET_ROLE), owner.target)
        before_clients = len(client_factory_calls)
        panel.refresh_catalog()
        self.assertEqual(panel.tree.connection_source_item("mongo-id").data(TREE_TARGET_ROLE), owner.target)
        self.assertEqual(len(client_factory_calls), before_clients)

    def test_workspace_tree_enforces_page_and_cumulative_signed_node_budget(self):
        row = _profile("budget")
        panel = self._panel([row], lambda _connection_id: ObjectTreeComposition(
            relational_host=RelationalReadOnlyHost(connections={}, secret_loader=lambda _profile: ""),
            nosql_host=NoSQLReadOnlyHost(connections={}, secret_loader=lambda _profile: ""),
        ))
        target = TreeTarget("budget", "revision", "mysql", mode="mysql")
        root = TreeNode("root-ref", "root", "Connection")
        panel.tree.set_directory_root("budget", target, root, 1)
        databases = tuple(
            TreeNode(f"database-{index}", "database", f"db-{index}", parent_ref=root.node_ref)
            for index in range(50)
        )
        root_page = TreePage(target, root.node_ref, databases, None, 1, 1, 0.0)
        self.assertTrue(panel.tree.set_directory_page("budget", root_page, None))
        self.assertEqual(panel.tree.connection_source_item("budget").rowCount(), 50)

        first_parent = panel.tree._find_directory_item("budget", target, databases[0].node_ref)
        original = TreeNode("original-table", "table", "original", parent_ref=databases[0].node_ref)
        self.assertTrue(panel.tree.set_directory_page(
            "budget",
            TreePage(target, databases[0].node_ref, (original,), None, 1, 1, 0.0),
            None,
        ))

        for index, database in enumerate(databases[1:]):
            parent = panel.tree._find_directory_item("budget", target, database.node_ref)
            rows = tuple(
                TreeNode(
                    f"table-{index}-{item}", "table", f"table-{index}-{item}",
                    parent_ref=database.node_ref,
                )
                for item in range(50)
            )
            page = TreePage(target, database.node_ref, rows, None, 1, 1, 0.0)
            accepted = panel.tree.set_directory_page("budget", page, None)
            if not accepted:
                self.assertLessEqual(index, 39)
                break
        else:
            self.fail("the cumulative directory page budget was not enforced")

        # At this point the first signed page is still replaceable only if the
        # resulting subtree stays within the owner-wide budget. This growth is refused.
        prior_node_refs = tuple(
            first_parent.child(index).data(TREE_NODE_ROLE).node_ref
            for index in range(first_parent.rowCount())
            if first_parent.child(index).data(TREE_NODE_ROLE) is not None
        )
        growth = tuple(
            TreeNode(f"replacement-{index}", "table", f"replacement-{index}", parent_ref=databases[0].node_ref)
            for index in range(50)
        )
        self.assertFalse(panel.tree.set_directory_page(
            "budget",
            TreePage(target, databases[0].node_ref, growth, None, 1, 1, 0.0),
            None,
        ))
        self.assertEqual(
            tuple(
                first_parent.child(index).data(TREE_NODE_ROLE).node_ref
                for index in range(first_parent.rowCount())
                if first_parent.child(index).data(TREE_NODE_ROLE) is not None
            ),
            prior_node_refs,
        )

        # A page larger than the service's 50-row contract is also rejected without replacing data.
        before = tuple(first_parent.child(row).text() for row in range(first_parent.rowCount()))
        too_large = tuple(
            TreeNode(f"oversize-{index}", "table", f"oversize-{index}", parent_ref=databases[0].node_ref)
            for index in range(51)
        )
        rejected = TreePage(target, databases[0].node_ref, too_large, None, 1, 1, 0.0)
        self.assertFalse(panel.tree.set_directory_page("budget", rejected, None))
        self.assertEqual(
            tuple(first_parent.child(row).text() for row in range(first_parent.rowCount())),
            before,
        )

    def test_refresh_capture_discards_old_page_until_new_snapshot_completes(self):
        row = _profile("late-page")
        profile = dict(row)
        old_page_gate = threading.Event()
        old_page_entered = threading.Event()
        release_capture = threading.Event()
        capture_entered = threading.Event()
        self.release_events.extend((old_page_gate, release_capture))
        profile_lock = threading.Lock()
        block_capture = {"enabled": False}
        snapshot_count = {"value": 0}
        events: list = []

        def profile_loader(connection_id):
            with profile_lock:
                snapshot_count["value"] += 1
                count = snapshot_count["value"]
                should_block = block_capture["enabled"] and count >= 2
            if should_block:
                capture_entered.set()
                release_capture.wait(8)
            return dict(profile)

        def connector(provider, profile, secret, **options):
            del provider, secret, options
            return _SQLConnection(
                events,
                database_rows=[("fixture-db",)],
                table_rows=[("stale-table", "BASE TABLE")],
                table_gate=old_page_gate,
                table_entered=old_page_entered,
            )

        relational = RelationalReadOnlyHost(
            profile_loader=profile_loader,
            secret_loader=lambda _profile: "",
            connector=connector,
        )
        make = lambda _cid: ObjectTreeComposition(
            relational_host=relational,
            nosql_host=NoSQLReadOnlyHost(connections={}, secret_loader=lambda _profile: ""),
        )
        panel = self._panel([row], make)
        panel.show()
        _expand_connection(panel, "late-page")
        self.assertTrue(_spin_until(
            lambda: bool(panel.tree.connection_source_item("late-page").data(TREE_LOADED_ROLE))
        ))
        owner = panel._tree_owners["late-page"]
        connection_item = panel.tree.connection_source_item("late-page")
        database = _find_child_by_kind(connection_item, "database")
        self.assertIsNotNone(database)

        # Install an already-visible last-good object page before the held refresh.
        table = TreeNode("last-good-table", "table", "orders", parent_ref=database.data(TREE_NODE_ROLE).node_ref)
        initial_page = TreePage(
            owner.target,
            database.data(TREE_NODE_ROLE).node_ref,
            (table,),
            None,
            owner.generation,
            1,
            0.0,
        )
        self.assertTrue(panel.tree.set_directory_page("late-page", initial_page, None))
        database = _find_child_by_kind(connection_item, "database")
        database_index = panel.tree.proxy_model.mapFromSource(database.index())
        panel.tree.expand(database_index)
        # Hold an older signed-page request while the new target capture is pending.
        panel.tree.set_directory_loading("late-page", owner.target, database.data(TREE_NODE_ROLE), owner.generation, True)
        owner.load_children(owner.target, database.data(TREE_NODE_ROLE), generation=owner.generation)
        self.assertTrue(old_page_entered.wait(2))
        block_capture["enabled"] = True
        panel.tree.select_connection("late-page")
        panel.refresh_selected_directory()
        self.assertTrue(capture_entered.wait(2))
        old_page_gate.set()
        APP.processEvents()
        time.sleep(0.03)
        APP.processEvents()
        database = _find_child_by_kind(panel.tree.connection_source_item("late-page"), "database")
        self.assertTrue(any("orders" in text for text in _all_text(database)))
        self.assertFalse(any("stale-table" in text for text in _all_text(database)))
        self.assertTrue(owner.capture_pending)

        release_capture.set()
        self.assertTrue(_spin_until(lambda: not owner.capture_pending, timeout=4))


def capture_actual_panel_screenshots(output_directory: str) -> list[dict]:
    """Render the real panel and composition with production calm theme + fake hosts."""
    ThemeManager.instance().apply(APP, "calm", font_size=13)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for language, width, height in (("zh", 960, 640), ("en", 960, 640), ("zh", 1160, 720), ("en", 1160, 720)):
        row = _profile(f"screenshot-{language}-{width}")
        calls = []

        def composition_factory(connection_id, selected=dict(row), call_log=calls):
            def connector(provider, profile, secret, **options):
                del provider, secret, options
                call_log.append(("connector", threading.get_ident()))
                return _SQLConnection(call_log, database_rows=[("fixture-database",)])

            relational = RelationalReadOnlyHost(
                connections={connection_id: selected},
                secret_loader=lambda _profile: "",
                connector=connector,
            )
            nosql = NoSQLReadOnlyHost(connections={}, secret_loader=lambda _profile: "")
            return ObjectTreeComposition(relational_host=relational, nosql_host=nosql)

        panel = DataCenterPanel(
            language,
            catalog=_catalog([row]),
            host_factory=lambda profile, lang: _PanelHost(profile, lang),
            object_tree_composition_factory=composition_factory,
        )
        panel.resize(width, height)
        panel.show()
        APP.processEvents()
        _expand_connection(panel, str(row["id"]))
        if not _spin_until(
            lambda: bool(panel.tree.connection_source_item(str(row["id"])).data(TREE_LOADED_ROLE))
        ):
            raise RuntimeError(f"TEMP object tree did not load for {language}/{width}")
        if not panel.tree.select_connection(str(row["id"])):
            raise RuntimeError(f"TEMP connection could not be selected for {language}/{width}")
        APP.processEvents()
        connection_item = panel.tree.connection_source_item(str(row["id"]))
        database_item = _find_child_by_kind(connection_item, "database")
        if database_item is None:
            raise RuntimeError(f"TEMP database node did not load for {language}/{width}")
        signed_node = database_item.data(TREE_NODE_ROLE)
        if database_item.toolTip() != signed_node.name:
            raise RuntimeError(f"Full object name tooltip is missing for {language}/{width}")
        important_actions = {
            "new_connection": panel.new_btn,
            "refresh_catalog": panel.refresh_btn,
            "open_workspace": panel.open_btn,
            "refresh_objects": panel.directory_refresh_btn,
            "close_directory": panel.directory_close_btn,
        }
        action_geometry = {}
        for action_name, button in important_actions.items():
            if not button.isVisible() or not button.isEnabled():
                raise RuntimeError(f"TEMP action {action_name} is unavailable for {language}/{width}")
            bounds = button.geometry()
            hint = button.minimumSizeHint()
            if (
                bounds.width() < hint.width()
                or bounds.height() < hint.height()
                or bounds.left() < 0
                or bounds.top() < 0
                or bounds.right() >= panel.width()
                or bounds.bottom() >= panel.height()
            ):
                raise RuntimeError(f"TEMP action {action_name} is clipped for {language}/{width}")
            action_geometry[action_name] = {
                "rect": [bounds.x(), bounds.y(), bounds.width(), bounds.height()],
                "minimum_size_hint": [hint.width(), hint.height()],
            }
        APP.processEvents()
        image = QImage(panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        panel.render(painter)
        painter.end()
        path = output / f"data-center-object-tree-{language}-{width}x{height}.png"
        if not image.save(str(path)):
            raise RuntimeError("Qt could not save the TEMP screenshot")
        dialog_probe = QDialog(panel)
        dialog_probe.ensurePolished()
        first_button = panel.findChild(QPushButton)

        def font_evidence(widget):
            font = widget.font()
            info = QFontInfo(font)
            return {
                "family": info.family(),
                "font_point_size_f": float(font.pointSizeF()),
                "font_pixel_size": int(font.pixelSize()),
                "resolved_point_size_f": float(info.pointSizeF()),
                "resolved_pixel_size": int(info.pixelSize()),
            }

        records.append({
            "path": str(path),
            "size": f"{width}x{height}",
            "language": language,
            "qpa": APP.platformName(),
            "dpr": float(panel.devicePixelRatioF()),
            "theme": str(APP.property("ui_theme") or ""),
            "stylesheet_font_size_css_px": 13,
            "actual_fonts": {
                "app": font_evidence(APP),
                "panel": font_evidence(panel),
                "tree": font_evidence(panel.tree),
                "button": font_evidence(first_button) if first_button is not None else None,
                "dialog": font_evidence(dialog_probe),
            },
            "directory_node_tooltip": database_item.toolTip(),
            "directory_node_ref": signed_node.node_ref,
            "actions": action_geometry,
        })
        dialog_probe.deleteLater()
        panel.workbench_shutdown_cleanup()
        _spin_until(lambda: not panel._tree_owners, timeout=3)
        panel.deleteLater()
        APP.processEvents()
    return records


__all__ = ["DataCenterObjectTreeIntegrationTests", "capture_actual_panel_screenshots"]
