from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.data_center.connection_store import (
    FORMAT_VERSION,
    ConnectionStore,
    ConnectionStoreError,
    _OSFileLock,
)


def _fixture_profiles() -> list[dict]:
    return [
        {
            "id": "fixture-oceanbase",
            "name": "OceanBase fixture",
            "dialect": "oceanbase",
            "mode": "oracle",
            "oceanbase_oracle_provider": "odbc",
            "password": "enc:synthetic-oceanbase-token",
            "group_path": "Payments/ledger",
            "environment": "test",
            "profile_revision": "fixture-revision-7",
            "vendor_extension": {"retained": True, "marker": "fixture-ocean"},
        },
        {
            "id": "fixture-redis",
            "name": "Redis fixture",
            "dialect": "redis",
            "mode": "cluster",
            "auth_mode": "acl",
            "seed_nodes": [
                {"host": "redis-a.fixture.invalid", "port": 6379},
                {"host": "redis-b.fixture.invalid", "port": 6380},
            ],
            "password": "enc:synthetic-redis-token",
            "username": "fixture-user",
            "vendor_option": {"retained": "redis"},
        },
        {
            "id": "fixture-mongo",
            "name": "Mongo fixture",
            "dialect": "mongodb",
            "mode": "cluster",
            "host": "mongodb://fixture.invalid/admin?replicaSet=fixture-rs",
            "replica_set_name": "fixture-rs",
            "seed_nodes": [{"host": "mongo-a.fixture.invalid", "port": 27017}],
            "password": "enc:synthetic-mongo-token",
            "vendor_blob": {"opaque": [1, 2, 3]},
        },
    ]


def _envelope(items: list[dict], *, version: int = FORMAT_VERSION) -> dict:
    return {
        "format_version": version,
        "metadata": {"fixture_owner": "connection-store-tests", "marker": 17},
        "vendor_envelope": {"keep": ["alpha", "beta"]},
        "items": items,
    }


def _write_json(path: Path, value: object, *, indent: int = 2) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def _backup_for(path: Path, original: bytes) -> Path:
    revision = hashlib.sha256(original).hexdigest()
    return path.with_name(f"{path.name}.pre-v{FORMAT_VERSION}-{revision}.bak")


class ConnectionStoreTests(unittest.TestCase):
    def test_load_does_not_create_parent_or_lock_or_fill_display_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "not-created" / "connections.json"
            store = ConnectionStore(path)

            snapshot = store.load()

            self.assertEqual(snapshot.items, ())
            self.assertIsNone(snapshot.revision)
            self.assertFalse(path.parent.exists())
            self.assertFalse(store.lock_path.exists())

            path.parent.mkdir()
            item = {"id": "legacy", "name": "Legacy fixture", "dialect": "mysql"}
            raw = _write_json(path, _envelope([item]))
            loaded = store.load()
            self.assertEqual(loaded.items[0], item)
            self.assertNotIn("group_path", loaded.items[0])
            self.assertNotIn("environment", loaded.items[0])
            self.assertEqual(path.read_bytes(), raw)
            self.assertFalse(store.lock_path.exists())

    def test_load_preserves_versioned_envelope_and_vendor_fields(self):
        profiles = _fixture_profiles()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, _envelope(profiles))

            snapshot = ConnectionStore(path).load()

            self.assertEqual(snapshot.revision, hashlib.sha256(raw).hexdigest())
            self.assertEqual(snapshot.format_version, FORMAT_VERSION)
            self.assertFalse(snapshot.needs_upgrade)
            self.assertEqual(list(snapshot.items), profiles)

    def test_upsert_merges_partial_patch_and_treats_secret_tokens_as_opaque(self):
        profiles = _fixture_profiles()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            store = ConnectionStore(path)
            store.save(profiles, expected_revision=None)

            snapshot = store.upsert({"id": "fixture-oceanbase", "name": "Renamed fixture"})
            ocean = snapshot.items[0]
            self.assertEqual(ocean["name"], "Renamed fixture")
            self.assertEqual(ocean["id"], "fixture-oceanbase")
            self.assertEqual(ocean["dialect"], "oceanbase")
            self.assertEqual(ocean["password"], "enc:synthetic-oceanbase-token")
            self.assertEqual(ocean["mode"], "oracle")
            self.assertEqual(ocean["group_path"], "Payments/ledger")
            self.assertEqual(ocean["environment"], "test")
            self.assertEqual(ocean["vendor_extension"], {"retained": True, "marker": "fixture-ocean"})
            self.assertEqual(ocean["profile_revision"], "fixture-revision-7")

            explicit_empty = store.upsert({"id": "fixture-oceanbase", "password": ""})
            self.assertEqual(explicit_empty.items[0]["password"], "")
            self.assertEqual(explicit_empty.items[0]["mode"], "oracle")

    def test_upsert_only_removes_fields_named_explicitly(self):
        profile = _fixture_profiles()[2]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            store = ConnectionStore(path)
            store.save([profile], expected_revision=None)

            preserved = store.upsert({"id": profile["id"], "name": "Mongo still intact"})
            self.assertEqual(preserved.items[0]["host"], profile["host"])
            self.assertEqual(preserved.items[0]["replica_set_name"], "fixture-rs")
            self.assertEqual(preserved.items[0]["seed_nodes"], profile["seed_nodes"])

            snapshot = store.upsert(
                {"id": profile["id"], "name": "Updated Mongo fixture"},
                remove_fields=("replica_set_name",),
            )

            updated = snapshot.items[0]
            self.assertEqual(updated["name"], "Updated Mongo fixture")
            self.assertNotIn("replica_set_name", updated)
            self.assertEqual(updated["host"], profile["host"])
            self.assertEqual(updated["seed_nodes"], profile["seed_nodes"])
            self.assertEqual(updated["vendor_blob"], profile["vendor_blob"])

    def test_full_save_keeps_order_and_explicit_deletions_while_merging_retained_rows(self):
        profiles = _fixture_profiles()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            _write_json(path, _envelope(profiles))
            store = ConnectionStore(path)
            before = store.load()

            saved = store.save(
                [
                    {"id": "fixture-redis", "name": "Redis renamed"},
                    {"id": "fixture-oceanbase", "name": "Ocean renamed"},
                ],
                expected_revision=before.revision,
            )

            self.assertEqual([item["id"] for item in saved.items], ["fixture-redis", "fixture-oceanbase"])
            self.assertEqual(saved.items[0]["name"], "Redis renamed")
            self.assertEqual(saved.items[0]["dialect"], "redis")
            self.assertEqual(saved.items[0]["auth_mode"], "acl")
            self.assertEqual(saved.items[0]["seed_nodes"], profiles[1]["seed_nodes"])
            self.assertEqual(saved.items[0]["password"], "enc:synthetic-redis-token")
            self.assertEqual(saved.items[1]["mode"], "oracle")
            self.assertEqual(saved.items[1]["vendor_extension"], profiles[0]["vendor_extension"])
            self.assertEqual(saved.items[1]["profile_revision"], "fixture-revision-7")
            self.assertNotIn("fixture-mongo", [item["id"] for item in saved.items])

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["metadata"], _envelope(profiles)["metadata"])
            self.assertEqual(raw["vendor_envelope"], _envelope(profiles)["vendor_envelope"])

    def test_legacy_list_load_is_read_only_and_explicit_save_backs_up_exact_bytes(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = ("[\n  " + json.dumps(profile, ensure_ascii=False) + "\n]\n").encode("utf-8")
            path.write_bytes(raw)
            store = ConnectionStore(path)

            loaded = store.load()
            self.assertTrue(loaded.needs_upgrade)
            self.assertIsNone(loaded.format_version)
            self.assertEqual(path.read_bytes(), raw)
            self.assertFalse(store.lock_path.exists())
            self.assertEqual(list(Path(temporary).glob("*.bak")), [])

            upgraded = store.upsert({"id": profile["id"], "name": "Legacy renamed"})

            backup = _backup_for(path, raw)
            self.assertEqual(backup.read_bytes(), raw)
            self.assertEqual(upgraded.format_version, FORMAT_VERSION)
            self.assertFalse(upgraded.needs_upgrade)
            self.assertEqual(upgraded.items[0]["mode"], "oracle")
            self.assertEqual(upgraded.items[0]["password"], profile["password"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["format_version"], FORMAT_VERSION)

    def test_unversioned_envelope_is_backed_up_then_upgraded_without_losing_metadata(self):
        profiles = _fixture_profiles()
        old_envelope = {"items": profiles, "metadata": {"old_marker": "keep"}, "vendor_top": [3, 4]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, old_envelope)
            store = ConnectionStore(path)
            self.assertTrue(store.load().needs_upgrade)

            saved = store.upsert({"id": "fixture-redis", "name": "Redis updated"})

            self.assertEqual(_backup_for(path, raw).read_bytes(), raw)
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["format_version"], FORMAT_VERSION)
            self.assertEqual(written["metadata"], old_envelope["metadata"])
            self.assertEqual(written["vendor_top"], old_envelope["vendor_top"])
            self.assertEqual(saved.items[1]["password"], profiles[1]["password"])

    def test_stale_revision_rejects_patch_without_clobbering_current_file(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            store = ConnectionStore(path)
            initial = store.save([profile], expected_revision=None)
            first = store.upsert(
                {"id": profile["id"], "vendor_a": "first"},
                expected_revision=initial.revision,
            )
            current_bytes = path.read_bytes()

            with self.assertRaises(ConnectionStoreError) as caught:
                store.upsert(
                    {"id": profile["id"], "vendor_b": "stale"},
                    expected_revision=initial.revision,
                )

            self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
            self.assertEqual(path.read_bytes(), current_bytes)
            self.assertEqual(first.items[0]["vendor_a"], "first")
            self.assertNotIn("vendor_b", json.loads(current_bytes)["items"][0])

    def test_concurrent_patches_to_different_fields_merge_under_os_file_lock(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            ConnectionStore(path).save([profile], expected_revision=None)
            barrier = threading.Barrier(3)
            failures: list[str] = []

            def apply_patch(field: str, value: str) -> None:
                try:
                    barrier.wait(timeout=2)
                    ConnectionStore(path, lock_timeout=2).upsert({"id": profile["id"], field: value})
                except Exception as error:
                    failures.append(type(error).__name__)

            threads = [
                threading.Thread(target=apply_patch, args=("vendor_parallel_a", "a")),
                threading.Thread(target=apply_patch, args=("vendor_parallel_b", "b")),
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=3)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            saved = ConnectionStore(path).load().items[0]
            self.assertEqual(saved["vendor_parallel_a"], "a")
            self.assertEqual(saved["vendor_parallel_b"], "b")
            self.assertEqual(saved["id"], profile["id"])
            self.assertEqual(saved["profile_revision"], profile["profile_revision"])

    def test_lock_timeout_is_bounded_and_does_not_change_source(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            store = ConnectionStore(path, lock_timeout=0.05)
            store.save([profile], expected_revision=None)
            original = path.read_bytes()
            locked = threading.Event()
            release = threading.Event()

            def hold_lock() -> None:
                with _OSFileLock(store.lock_path, 1):
                    locked.set()
                    release.wait(timeout=2)

            holder = threading.Thread(target=hold_lock)
            holder.start()
            try:
                self.assertTrue(locked.wait(1))
                with self.assertRaises(ConnectionStoreError) as caught:
                    store.upsert({"id": profile["id"], "name": "must-not-save"})
                self.assertEqual(caught.exception.code, "LOCK_TIMEOUT")
                self.assertEqual(path.read_bytes(), original)
            finally:
                release.set()
                holder.join(timeout=2)
            self.assertFalse(holder.is_alive())

    def test_atomic_replace_failure_preserves_original_and_cleans_temp(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, _envelope([profile]))
            store = ConnectionStore(path)

            with patch("tools.data_center.connection_store.os.replace", side_effect=OSError("synthetic replace failure")):
                with self.assertRaises(ConnectionStoreError) as caught:
                    store.upsert({"id": profile["id"], "name": "must-not-publish"})

            self.assertEqual(caught.exception.code, "WRITE_FAILED")
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(Path(temporary).glob(".connections.json.*.tmp")), [])

    def test_replace_that_commits_before_raising_is_reported_as_committed(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            _write_json(path, _envelope([profile]))
            store = ConnectionStore(path)
            real_replace = os.replace

            def replace_then_raise(source, destination):
                real_replace(source, destination)
                raise OSError("synthetic post-commit wrapper error")

            with patch("tools.data_center.connection_store.os.replace", side_effect=replace_then_raise):
                saved = store.upsert({"id": profile["id"], "name": "committed"})

            self.assertEqual(saved.items[0]["name"], "committed")
            self.assertEqual(saved.revision, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(list(Path(temporary).glob(".connections.json.*.tmp")), [])

    def test_failed_legacy_upgrade_keeps_exact_source_and_backup_without_temp(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = (json.dumps([profile], ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            path.write_bytes(raw)
            store = ConnectionStore(path)

            with patch("tools.data_center.connection_store.os.replace", side_effect=OSError("synthetic replace failure")):
                with self.assertRaises(ConnectionStoreError) as caught:
                    store.upsert({"id": profile["id"], "name": "must-not-publish"})

            self.assertEqual(caught.exception.code, "WRITE_FAILED")
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(_backup_for(path, raw).read_bytes(), raw)
            self.assertEqual(list(Path(temporary).glob(".connections.json.*.tmp")), [])

    def test_existing_mismatched_backup_is_never_overwritten(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, [profile])
            backup = _backup_for(path, raw)
            backup.write_bytes(b"pre-existing fixture backup")
            original_backup = backup.read_bytes()

            with self.assertRaises(ConnectionStoreError) as caught:
                ConnectionStore(path).upsert({"id": profile["id"], "name": "must-not-publish"})

            self.assertEqual(caught.exception.code, "BACKUP_CONFLICT")
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(backup.read_bytes(), original_backup)
            self.assertEqual(list(Path(temporary).glob(".connections.json.*.tmp")), [])

    def test_future_format_is_rejected_without_downgrade(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, _envelope([profile], version=FORMAT_VERSION + 1))
            store = ConnectionStore(path)

            with self.assertRaises(ConnectionStoreError) as loaded:
                store.load()
            self.assertEqual(loaded.exception.code, "UNSUPPORTED_VERSION")
            with self.assertRaises(ConnectionStoreError) as saved:
                store.upsert({"id": profile["id"], "name": "must-not-downgrade"})
            self.assertEqual(saved.exception.code, "UNSUPPORTED_VERSION")
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(Path(temporary).glob(".connections.json.*.tmp")), [])

    def test_explicit_null_format_version_is_invalid_not_legacy(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, {"format_version": None, "items": [profile]})
            with self.assertRaises(ConnectionStoreError) as caught:
                ConnectionStore(path).load()
            self.assertEqual(caught.exception.code, "INVALID_VERSION")
            self.assertEqual(path.read_bytes(), raw)

    def test_corrupt_document_is_not_loaded_as_empty_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = b'{"items":[{"id":"broken"}'
            path.write_bytes(raw)
            store = ConnectionStore(path)

            with self.assertRaises(ConnectionStoreError) as loaded:
                store.load()
            self.assertEqual(loaded.exception.code, "INVALID_DOCUMENT")
            with self.assertRaises(ConnectionStoreError) as saved:
                store.upsert({"id": "fixture", "name": "must-not-overwrite"})
            self.assertEqual(saved.exception.code, "INVALID_DOCUMENT")
            self.assertEqual(path.read_bytes(), raw)

    def test_document_and_record_limits_fail_closed_without_trimming(self):
        profile = _fixture_profiles()[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            raw = _write_json(path, _envelope([profile]))
            with patch("tools.data_center.connection_store.MAX_DOCUMENT_BYTES", len(raw) - 1):
                with self.assertRaises(ConnectionStoreError) as caught:
                    ConnectionStore(path).load()
            self.assertEqual(caught.exception.code, "DOCUMENT_TOO_LARGE")
            self.assertEqual(path.read_bytes(), raw)

            missing_path = Path(temporary) / "unwritten.json"
            with patch("tools.data_center.connection_store.MAX_CONNECTION_BYTES", 8):
                with self.assertRaises(ConnectionStoreError) as too_large:
                    ConnectionStore(missing_path).save([profile], expected_revision=None)
            self.assertEqual(too_large.exception.code, "CONNECTION_TOO_LARGE")
            self.assertFalse(missing_path.exists())

            with patch("tools.data_center.connection_store.MAX_CONNECTIONS", 1):
                with self.assertRaises(ConnectionStoreError) as too_many:
                    ConnectionStore(missing_path).save(
                        [{"id": "fixture-a"}, {"id": "fixture-b"}],
                        expected_revision=None,
                    )
            self.assertEqual(too_many.exception.code, "TOO_MANY_CONNECTIONS")
            self.assertFalse(missing_path.exists())

    def test_duplicate_full_save_ids_fail_closed_and_upsert_keeps_record_identity(self):
        profiles = _fixture_profiles()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "connections.json"
            store = ConnectionStore(path)
            store.save(profiles, expected_revision=None)
            raw = path.read_bytes()

            with self.assertRaises(ConnectionStoreError) as duplicate:
                store.save(
                    [profiles[0], {"id": profiles[0]["id"], "name": "ambiguous"}],
                    expected_revision=store.load().revision,
                )
            self.assertEqual(duplicate.exception.code, "DUPLICATE_ID")
            self.assertEqual(path.read_bytes(), raw)

            saved = store.upsert({"id": profiles[0]["id"], "name": "Same identity"})
            self.assertEqual(saved.items[0]["id"], profiles[0]["id"])
            self.assertEqual(saved.items[0]["profile_revision"], profiles[0]["profile_revision"])
