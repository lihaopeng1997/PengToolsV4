from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import db_connect
from tools.data_center.connection_store import ConnectionStore, ConnectionStoreError


def _row(connection_id: str = "ob-1") -> dict:
    return {
        "id": connection_id,
        "name": "OceanBase synthetic",
        "dialect": "oceanbase",
        "mode": "mysql",
        "host": "db.synthetic.invalid",
        "port": 2883,
        "database": "fixture_db",
        "username": "fixture_user",
        "password": "opaque.synthetic.token",
        "group_path": "Synthetic/Project",
        "environment": "test",
        "oceanbase_oracle_provider": "fixture-provider",
        "seed_nodes": [{"host": "redis.synthetic.invalid", "port": 6379}],
        "auth_mode": "acl",
        "mongo_uri": "mongodb://mongo.synthetic.invalid/fixture",
        "replicaSet": "fixture-rs",
        "profile_revision": "fixture-revision-7",
        "unknown_vendor_field": {"preserve": ["opaque", 17]},
    }


class ConnectionStoreGlueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "connections.json"
        self.path.parent.mkdir()
        self.path_patch = patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def _write_versioned(self, rows: list[dict], **envelope_fields) -> bytes:
        document = {"format_version": 1, **envelope_fields, "items": rows}
        raw = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
        self.path.write_bytes(raw)
        return raw

    def test_missing_load_is_empty_and_does_not_create_directory(self):
        path = self.path.parent / "missing" / "connections.json"
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(path)):
            self.assertEqual(db_connect.load_connections(), [])
        self.assertFalse(path.parent.exists())

    def test_partial_production_upsert_preserves_legacy_vendor_envelope_and_opaque_token(self):
        row = _row()
        document = {"items": [row], "unknown_envelope_field": {"keep": True}}
        original = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.path.write_bytes(original)
        encrypt = Mock(side_effect=AssertionError("unexpected encryption"))
        decrypt = Mock(side_effect=AssertionError("unexpected decryption"))

        with patch.object(db_connect, "_encrypt", encrypt), patch.object(db_connect, "_decrypt", decrypt):
            saved = db_connect.upsert_connection({"id": "ob-1", "name": "Renamed"})
            blank = db_connect.upsert_connection(
                {"id": "ob-1", "name": "Still saved", "password": ""}, plain_password=""
            )

        self.assertEqual(saved["id"], "ob-1")
        self.assertEqual(saved["name"], "Renamed")
        for key in (
            "dialect", "mode", "host", "port", "database", "username", "password",
            "group_path", "environment", "oceanbase_oracle_provider", "seed_nodes",
            "auth_mode", "mongo_uri", "replicaSet", "profile_revision", "unknown_vendor_field",
        ):
            self.assertEqual(saved[key], row[key], key)
        self.assertEqual(blank["password"], row["password"])
        encrypt.assert_not_called()
        decrypt.assert_not_called()

        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["format_version"], 1)
        self.assertEqual(persisted["unknown_envelope_field"], {"keep": True})
        self.assertEqual(persisted["items"][0]["unknown_vendor_field"], row["unknown_vendor_field"])
        digest = hashlib.sha256(original).hexdigest()
        backup = self.path.with_name(f"connections.json.pre-v1-{digest}.bak")
        self.assertEqual(backup.read_bytes(), original)

    def test_only_nonempty_plaintext_is_encrypted_once_and_new_profiles_keep_defaults(self):
        self._write_versioned([_row()])
        encrypt = Mock(return_value="opaque.new.synthetic.token")
        decrypt = Mock(side_effect=AssertionError("save must not decrypt"))
        with patch.object(db_connect, "_encrypt", encrypt), patch.object(db_connect, "_decrypt", decrypt):
            saved = db_connect.upsert_connection(
                {"id": "ob-1", "name": "Credential update", "password": ""},
                plain_password="synthetic plaintext only",
            )
            created = db_connect.upsert_connection({"id": "new-1", "name": "New profile"})

        self.assertEqual(saved["password"], "opaque.new.synthetic.token")
        encrypt.assert_called_once_with("synthetic plaintext only")
        decrypt.assert_not_called()
        self.assertEqual(created["dialect"], "oracle")
        self.assertEqual(created["host"], "")
        self.assertEqual(created["port"], 1521)
        self.assertEqual(created["database"], "")
        self.assertEqual(created["username"], "")
        self.assertEqual(created["password"], "")

    def test_full_save_uses_revision_preserves_rows_and_rejects_stale_revision(self):
        self._write_versioned(
            [{"id": "a", "name": "A", "opaque_a": "keep-a"},
             {"id": "b", "name": "B", "opaque_b": "keep-b"}],
            envelope_marker="preserve",
        )
        snapshot = ConnectionStore(self.path).load()
        items = [{"id": "b", "name": "B2"}, {"id": "a", "name": "A"}]
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)):
            returned = db_connect.save_connections(items, expected_revision=snapshot.revision)
        self.assertIs(returned, items)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in payload["items"]], ["b", "a"])
        self.assertEqual(payload["items"][0]["opaque_b"], "keep-b")
        self.assertEqual(payload["envelope_marker"], "preserve")

        after_write = self.path.read_bytes()
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)):
            with self.assertRaisesRegex(db_connect.DbError, "CONNECTION_STORE_REVISION_CONFLICT"):
                db_connect.save_connections([{"id": "replacement"}], expected_revision=snapshot.revision)
        self.assertEqual(self.path.read_bytes(), after_write)

    def test_delete_is_revision_checked_and_preserves_other_rows_and_envelope(self):
        self._write_versioned([_row(), {"id": "keep", "name": "Keep", "unknown": 3}], envelope_note="x")
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)):
            revision = ConnectionStore(self.path).load().revision
            db_connect.delete_connection("ob-1", expected_revision=revision)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in payload["items"]], ["keep"])
        self.assertEqual(payload["envelope_note"], "x")

    def test_concurrent_stale_upserts_conflict_instead_of_overwriting(self):
        self._write_versioned([{"id": "shared", "name": "Shared", "host": "original.invalid"}])
        original_load = ConnectionStore.load
        barrier = threading.Barrier(2)

        def synchronized_load(store):
            snapshot = original_load(store)
            barrier.wait(timeout=3)
            return snapshot

        def write(field, value):
            return db_connect.upsert_connection({"id": "shared", field: value})

        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)), \
                patch.object(ConnectionStore, "load", synchronized_load):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write, "name", "Concurrent name"),
                           pool.submit(write, "host", "concurrent.invalid")]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=4))
                    except db_connect.DbError as exc:
                        outcomes.append(str(exc))

        self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
        self.assertEqual(sum(isinstance(value, str) and "REVISION_CONFLICT" in value for value in outcomes), 1)
        final = ConnectionStore(self.path).load().items[0]
        self.assertIn(final["name"], {"Shared", "Concurrent name"})
        self.assertIn(final["host"], {"original.invalid", "concurrent.invalid"})

    def test_future_corrupt_and_atomic_write_failures_are_safe_and_leave_bytes(self):
        future = b'{"format_version":2,"items":[]}'
        self.path.write_bytes(future)
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)):
            with self.assertRaisesRegex(db_connect.DbError, "CONNECTION_STORE_UNSUPPORTED_VERSION") as caught:
                db_connect.load_connections()
        self.assertNotIn(str(self.path), str(caught.exception))
        self.assertEqual(self.path.read_bytes(), future)

        corrupt = b'{bad json'
        self.path.write_bytes(corrupt)
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)):
            with self.assertRaisesRegex(db_connect.DbError, "CONNECTION_STORE_"):
                db_connect.upsert_connection({"id": "x", "name": "Never replace"})
        self.assertEqual(self.path.read_bytes(), corrupt)

        valid = self._write_versioned([_row()])
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)), \
                patch.object(ConnectionStore, "_atomic_replace",
                             side_effect=ConnectionStoreError("WRITE_FAILED")):
            with self.assertRaisesRegex(db_connect.DbError, "CONNECTION_STORE_WRITE_FAILED"):
                db_connect.upsert_connection({"id": "ob-1", "name": "Atomic failure"})
        self.assertEqual(self.path.read_bytes(), valid)
        self.assertEqual(list(self.path.parent.glob(".connections.json.*.tmp")), [])


class WorkspaceCatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "connections.json"

    def _default_catalog(self, **kwargs):
        from tools.data_center.workspace_catalog import WorkspaceCatalog

        config_path = patch("config.HARNESS_CONNECTIONS_FILE", str(self.path))
        config_path.start()
        self.addCleanup(config_path.stop)
        db_path = patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path))
        db_path.start()
        self.addCleanup(db_path.stop)
        return WorkspaceCatalog(**kwargs)

    def test_default_missing_read_does_not_create_parent_and_future_keeps_last_good(self):
        from tools.data_center.workspace_catalog import WorkspaceCatalog

        catalog = self._default_catalog(saver=Mock(), deleter=Mock())
        self.assertEqual(catalog.refresh(), ())
        self.assertEqual(catalog.last_error, "")
        self.assertFalse(self.path.parent.exists())

        self.path.parent.mkdir(parents=True)
        profile = _row("last-good")
        self.path.write_text(json.dumps({"format_version": 1, "items": [profile]}), encoding="utf-8")
        summaries = catalog.refresh()
        self.assertEqual(len(summaries), 1)
        future = b'{"format_version":9,"items":[]}'
        self.path.write_bytes(future)
        self.assertEqual(catalog.refresh(), summaries)
        self.assertIn("ValueError", catalog.last_error)
        self.assertIsNotNone(catalog.get_summary("last-good"))
        self.assertEqual(catalog.save({"id": "blocked", "name": "Blocked"}), {})
        self.assertEqual(self.path.read_bytes(), future)

    def test_default_catalog_passes_observed_revision_to_save_and_rejects_stale_form(self):
        from tools.data_center.workspace_catalog import WorkspaceCatalog

        self.path.parent.mkdir(parents=True)
        profile = _row("catalog-id")
        self.path.write_text(json.dumps({"format_version": 1, "items": [profile]}), encoding="utf-8")
        catalog = self._default_catalog()
        catalog.refresh()
        stale_form = catalog.get_profile("catalog-id")
        self.assertIsNotNone(stale_form)

        store = ConnectionStore(self.path)
        latest = store.load()
        store.upsert({"id": "catalog-id", "external_revision_field": "written elsewhere"},
                     expected_revision=latest.revision)
        external_bytes = self.path.read_bytes()
        stale_form["name"] = "Stale editor"

        with self.assertRaisesRegex(db_connect.DbError, "CONNECTION_STORE_REVISION_CONFLICT"):
            catalog.save(stale_form)
        self.assertEqual(self.path.read_bytes(), external_bytes)

    def test_frozen_backend_loader_propagates_future_format_error(self):
        from tools.data_center.host_relational import _default_connections_loader

        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"format_version":2,"items":[]}', encoding="utf-8")
        with patch.object(db_connect, "HARNESS_CONNECTIONS_FILE", str(self.path)):
            with self.assertRaisesRegex(db_connect.DbError, "CONNECTION_STORE_UNSUPPORTED_VERSION"):
                _default_connections_loader()


class ProfileHostStrictDefaultLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "connections.json"
        self.config_path_patch = patch("config.HARNESS_CONNECTIONS_FILE", str(self.path))
        self.config_path_patch.start()
        self.addCleanup(self.config_path_patch.stop)

    def test_missing_default_profile_catalog_is_empty_without_filesystem_writes(self):
        from tools.data_center.profile_host import DataCenterProfileHost

        host = DataCenterProfileHost()
        self.assertEqual(host._connection_rows(), [])
        self.assertFalse(self.path.parent.exists())

    def test_future_and_corrupt_default_catalogs_fail_closed_through_public_api(self):
        from tools.data_center.profile_host import DataCenterProfileHost, ProfileHostError

        profile = {
            "id": "synthetic-profile",
            "dialect": "mysql",
            "host": "db.synthetic.invalid",
            "port": 3306,
            "database": "synthetic_db",
            "username": "synthetic_user",
            "password": "opaque.synthetic.token",
        }
        host = DataCenterProfileHost()
        choice = host.capture_connection(profile, database="synthetic_db")
        self.path.parent.mkdir(parents=True)
        for raw in (
            b'{"format_version":99,"items":[{"id":"synthetic-profile"}]}',
            b'{not-json',
        ):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(ProfileHostError) as error:
                    host.load_connection_profile(choice.connection_id, choice.revision)
                self.assertEqual(error.exception.code, "PROFILE_UNAVAILABLE")
                self.assertEqual(self.path.read_bytes(), raw)

    def test_legacy_and_current_default_catalogs_are_read_without_upgrade(self):
        from tools.data_center.profile_host import DataCenterProfileHost

        profile = {
            "id": "synthetic-profile",
            "dialect": "mysql",
            "host": "db.synthetic.invalid",
            "port": 3306,
            "database": "synthetic_db",
            "username": "synthetic_user",
            "password": "opaque.synthetic.token",
            "unknown_vendor_field": {"fixture": True},
        }
        host = DataCenterProfileHost()
        choice = host.capture_connection(profile, database="synthetic_db")
        self.path.parent.mkdir(parents=True)
        documents = (
            [profile],
            {"format_version": 1, "items": [profile]},
            {"items": [profile]},
        )
        for document in documents:
            with self.subTest(format=document.get("format_version") if isinstance(document, dict) else "legacy-list"):
                raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.path.write_bytes(raw)
                loaded = host.load_connection_profile(choice.connection_id, choice.revision)
                self.assertEqual(loaded, profile)
                self.assertEqual(self.path.read_bytes(), raw)


class PanelConnectionFailureTests(unittest.TestCase):
    @staticmethod
    def _panel_stub(**values):
        values.setdefault("language", "zh")
        values.setdefault("_title", lambda: "Connection settings")
        return SimpleNamespace(**values)

    def test_sql_redis_and_mongo_reload_failures_do_not_clear_selection(self):
        from panels import ai_workbench_panel, db_mongodb_panel, db_redis_panel

        cases = [
            (ai_workbench_panel, ai_workbench_panel.AiWorkbenchPanel, ""),
            (db_redis_panel, db_redis_panel.RedisWorkbenchPanel, None),
            (db_mongodb_panel, db_mongodb_panel.MongoDBWorkbenchPanel, None),
        ]
        for module, panel_type, connection_id in cases:
            with self.subTest(module=module.__name__):
                combo = Mock()
                stub = self._panel_stub(conn_combo=combo, _connection_id=connection_id)
                with patch.object(module, "load_connections", side_effect=db_connect.DbError("safe code")), \
                        patch.object(module, "show_error") as show_error:
                    panel_type._reload_connections(stub)
                combo.clear.assert_not_called()
                show_error.assert_called_once()
                self.assertNotIn("safe code", show_error.call_args.args[2])

    def test_legacy_bound_sql_reload_failure_preserves_state_and_skips_rebuild(self):
        from panels import ai_workbench_panel

        snapshot = object()
        profile = {"id": "fixed-id", "host": "old.synthetic.invalid"}
        stub = self._panel_stub(
            _connection_id="fixed-id",
            _bound_conn_item=profile,
            _snapshot=snapshot,
            _rebuild_tree=Mock(),
            _refresh_header=Mock(),
            _refresh_model_status=Mock(),
            _refresh_ai_pick_state=Mock(),
            _refresh_agent_status=Mock(),
        )
        stub._reload_bound_connection = ai_workbench_panel.AiWorkbenchPanel._reload_bound_connection.__get__(
            stub, ai_workbench_panel.AiWorkbenchPanel,
        )
        with patch.object(ai_workbench_panel, "load_connections", side_effect=db_connect.DbError("private marker")), \
                patch.object(ai_workbench_panel, "show_error") as show_error:
            ai_workbench_panel.AiWorkbenchPanel._reload_connections(stub)

        self.assertEqual(stub._connection_id, "fixed-id")
        self.assertIs(stub._bound_conn_item, profile)
        self.assertIs(stub._snapshot, snapshot)
        stub._rebuild_tree.assert_not_called()
        stub._refresh_header.assert_not_called()
        stub._refresh_model_status.assert_not_called()
        stub._refresh_ai_pick_state.assert_not_called()
        stub._refresh_agent_status.assert_not_called()
        show_error.assert_called_once()
        self.assertNotIn("private marker", show_error.call_args.args[2])

    def test_legacy_bound_sql_reload_never_falls_back_when_requested_id_is_absent(self):
        from panels import ai_workbench_panel

        other_profile = {"id": "other-id", "dialect": "mysql", "host": "other.synthetic.invalid"}
        cases = (
            ("missing-id", None, None),
            ("deleted-id", {"id": "deleted-id", "host": "old.synthetic.invalid"}, {"status": "old"}),
        )
        for wanted_id, previous_profile, previous_snapshot in cases:
            with self.subTest(wanted_id=wanted_id):
                snapshot_binding = Mock()
                schema_rebind = Mock()
                agent = SimpleNamespace(target_changed=Mock())
                stub = self._panel_stub(
                    _connection_id=wanted_id,
                    _bound_conn_item=previous_profile,
                    _snapshot=previous_snapshot,
                    nl_input=SimpleNamespace(bind_snapshot=snapshot_binding),
                    _bind_editors_schema=schema_rebind,
                    data_center_agent=agent,
                    _rebuild_tree=Mock(),
                    _refresh_header=Mock(),
                    _refresh_model_status=Mock(),
                    _refresh_ai_pick_state=Mock(),
                    _refresh_agent_status=Mock(),
                )
                stub._reload_bound_connection = ai_workbench_panel.AiWorkbenchPanel._reload_bound_connection.__get__(
                    stub, ai_workbench_panel.AiWorkbenchPanel,
                )
                with patch.object(ai_workbench_panel, "load_connections", return_value=[other_profile]), \
                        patch.object(ai_workbench_panel, "load_snapshot") as load_snapshot, \
                        patch.object(ai_workbench_panel, "show_error") as show_error:
                    ai_workbench_panel.AiWorkbenchPanel._reload_connections(stub)

                self.assertEqual(stub._connection_id, wanted_id)
                self.assertIsNone(stub._bound_conn_item)
                self.assertIsNone(stub._snapshot)
                load_snapshot.assert_not_called()
                snapshot_binding.assert_called_once_with(None)
                schema_rebind.assert_called_once_with()
                agent.target_changed.assert_called_once_with()
                stub._rebuild_tree.assert_not_called()
                stub._refresh_header.assert_not_called()
                stub._refresh_model_status.assert_not_called()
                stub._refresh_ai_pick_state.assert_not_called()
                stub._refresh_agent_status.assert_not_called()
                show_error.assert_called_once()
                self.assertNotIn(wanted_id, show_error.call_args.args[2])

    def test_sql_redis_and_mongo_save_delete_failures_do_not_reload(self):
        from PyQt6.QtWidgets import QDialog
        from panels import ai_workbench_panel, db_mongodb_panel, db_redis_panel

        cases = [
            (ai_workbench_panel, ai_workbench_panel.AiWorkbenchPanel, "_ConnectionDialog"),
            (db_redis_panel, db_redis_panel.RedisWorkbenchPanel, "ConnectionDialog"),
            (db_mongodb_panel, db_mongodb_panel.MongoDBWorkbenchPanel, "ConnectionDialog"),
        ]
        profile = {"id": "synthetic-id", "name": "synthetic", "dialect": "oracle"}
        for module, panel_type, dialog_name in cases:
            with self.subTest(module=module.__name__):
                dialog = Mock()
                dialog.exec.return_value = QDialog.DialogCode.Accepted
                dialog.payload.return_value = ({"id": "synthetic-id", "name": "patched"}, "")
                stub = self._panel_stub(
                    _current_conn=Mock(return_value=profile),
                    _reload_connections=Mock(),
                    _notify_main_sidebar_refresh=Mock(),
                    _dialect="oracle",
                )
                with patch.object(module, dialog_name, return_value=dialog), \
                        patch.object(module, "upsert_connection", side_effect=db_connect.DbError("safe code")), \
                        patch.object(module, "show_error") as show_error:
                    panel_type._edit_connection(stub)
                stub._reload_connections.assert_not_called()
                show_error.assert_called_once()
                self.assertNotIn("safe code", show_error.call_args.args[2])

                delete_stub = self._panel_stub(
                    _current_conn=Mock(return_value=profile),
                    _reload_connections=Mock(),
                    _notify_main_sidebar_refresh=Mock(),
                )
                with patch.object(module, "confirm_action", return_value=True), \
                        patch.object(module, "delete_connection", side_effect=db_connect.DbError("safe code")), \
                        patch.object(module, "show_error") as delete_error:
                    if module is ai_workbench_panel:
                        with patch.object(module, "delete_snapshot") as delete_snapshot:
                            panel_type._delete_connection(delete_stub)
                        delete_snapshot.assert_not_called()
                    else:
                        panel_type._delete_connection(delete_stub)
                delete_stub._reload_connections.assert_not_called()
                delete_stub._notify_main_sidebar_refresh.assert_not_called()
                delete_error.assert_called_once()

    def test_fixed_workspace_load_failure_preserves_bound_profile_and_snapshot(self):
        from panels import ai_workbench_panel
        from panels import data_center_panel

        profile = {"id": "fixed-id", "name": "last good"}
        snapshot = {"status": "ok"}
        stub = self._panel_stub(
            _connection_id="fixed-id",
            _bound_conn_item=profile,
            _snapshot=snapshot,
            nl_input=Mock(),
            _bind_editors_schema=Mock(),
        )
        with patch.object(ai_workbench_panel, "load_connections", side_effect=db_connect.DbError("safe code")), \
                patch.object(data_center_panel, "show_error") as show_error:
            result = data_center_panel._FixedSqlWorkbench._reload_bound_connection(stub)
        self.assertIs(result, False)
        self.assertEqual(stub._connection_id, "fixed-id")
        self.assertIs(stub._bound_conn_item, profile)
        self.assertIs(stub._snapshot, snapshot)
        stub.nl_input.bind_snapshot.assert_not_called()
        stub._bind_editors_schema.assert_not_called()
        show_error.assert_called_once()

    def test_unified_workspace_save_delete_errors_keep_selection_and_skip_refresh(self):
        from PyQt6.QtWidgets import QDialog
        from panels import data_center_panel

        def make_stub(catalog, connection_id="selected"):
            warning = SimpleNamespace(setText=Mock(), setVisible=Mock())
            tree = SimpleNamespace(selected_connection_id=Mock(return_value=connection_id))
            stub = self._panel_stub(
                catalog=catalog,
                catalog_warning=warning,
                tree=tree,
                _manual_shutdown_gated=False,
                _workspaces={},
                refresh_catalog=Mock(),
                _on_connection_selected=Mock(),
            )
            stub._show_catalog_operation_error = lambda action: data_center_panel.DataCenterPanel._show_catalog_operation_error(stub, action)
            return stub

        catalog = SimpleNamespace(last_error="", get_profile=Mock(return_value=_row("selected")),
                                  save=Mock(side_effect=OSError("do not display path")))
        stub = make_stub(catalog)
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.payload.return_value = (_row("selected"), "")
        with patch.object(data_center_panel, "_WorkspaceConnectionDialog", return_value=dialog):
            data_center_panel.DataCenterPanel._edit_connection(stub)
        catalog.save.assert_called_once()
        stub.refresh_catalog.assert_not_called()
        self.assertNotIn("do not display path", stub.catalog_warning.setText.call_args.args[0])

        summary = SimpleNamespace(name="synthetic")
        catalog = SimpleNamespace(last_error="", get_summary=Mock(return_value=summary),
                                  delete=Mock(side_effect=OSError("do not display path")))
        stub = make_stub(catalog)
        with patch.object(data_center_panel, "confirm_action", return_value=True):
            data_center_panel.DataCenterPanel._delete_connection(stub)
        catalog.delete.assert_called_once_with("selected")
        stub.refresh_catalog.assert_not_called()
        self.assertNotIn("do not display path", stub.catalog_warning.setText.call_args.args[0])

    def test_unified_workspace_read_error_blocks_new_profile_save(self):
        from panels import data_center_panel

        catalog = SimpleNamespace(last_error="ValueError", save=Mock())
        warning = SimpleNamespace(setText=Mock(), setVisible=Mock())
        stub = self._panel_stub(catalog=catalog, catalog_warning=warning, _manual_shutdown_gated=False)
        stub._show_catalog_operation_error = lambda action: data_center_panel.DataCenterPanel._show_catalog_operation_error(stub, action)
        with patch.object(data_center_panel, "_WorkspaceConnectionDialog") as dialog:
            data_center_panel.DataCenterPanel._new_connection(stub)
        dialog.assert_not_called()
        catalog.save.assert_not_called()
        self.assertNotIn("ValueError", warning.setText.call_args.args[0])

    def test_unified_workspace_new_and_favorite_write_failures_do_not_refresh(self):
        from PyQt6.QtWidgets import QDialog
        from panels import data_center_panel

        warning = SimpleNamespace(setText=Mock(), setVisible=Mock())
        catalog = SimpleNamespace(last_error="", save=Mock(side_effect=OSError("private path")))
        stub = self._panel_stub(
            catalog=catalog, catalog_warning=warning, _manual_shutdown_gated=False,
            refresh_catalog=Mock(), _on_connection_selected=Mock(),
        )
        stub._show_catalog_operation_error = lambda action: data_center_panel.DataCenterPanel._show_catalog_operation_error(stub, action)
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.payload.return_value = (_row("new"), "")
        with patch.object(data_center_panel, "_WorkspaceConnectionDialog", return_value=dialog):
            data_center_panel.DataCenterPanel._new_connection(stub)
        stub.refresh_catalog.assert_not_called()
        self.assertNotIn("private path", warning.setText.call_args.args[0])

        summary = SimpleNamespace(favorite=False)
        catalog = SimpleNamespace(last_error="", get_summary=Mock(return_value=summary),
                                  set_favorite=Mock(side_effect=OSError("private path")))
        warning = SimpleNamespace(setText=Mock(), setVisible=Mock())
        stub = self._panel_stub(
            catalog=catalog, catalog_warning=warning, _manual_shutdown_gated=False,
            tree=SimpleNamespace(selected_connection_id=Mock(return_value="selected")),
            refresh_catalog=Mock(), _on_connection_selected=Mock(),
        )
        stub._show_catalog_operation_error = lambda action: data_center_panel.DataCenterPanel._show_catalog_operation_error(stub, action)
        data_center_panel.DataCenterPanel._toggle_favorite(stub)
        stub.refresh_catalog.assert_not_called()
        self.assertNotIn("private path", warning.setText.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
