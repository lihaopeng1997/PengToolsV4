"""Focused tests for the NoSQL workspace text codec."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from bson import Decimal128, ObjectId, json_util

from tools.data_center.nosql_workspace import (
    NOSQL_WORKSPACE_TRAILER,
    NoSqlWorkspaceCodecError,
    capture_mongo_run,
    capture_redis_command,
    decode_nosql_workspace,
)
from tools.data_center.workspace_store import (
    HistoryRecord,
    WorkspaceStore,
    WorkspaceStoreError,
    WorkspaceTabKind,
    WorkspaceTarget,
    WorkspaceTextKind,
)


class NoSqlWorkspaceCodecTests(unittest.TestCase):
    def _redis_target(self, *, db_index: int = 3, mode: str = "standalone") -> WorkspaceTarget:
        return WorkspaceTarget(
            tab_id="redis-tab:1",
            connection_id="synthetic-redis",
            profile_revision="revision-redis-1",
            database=str(db_index),
            schema=mode,
            tab_kind=WorkspaceTabKind.REDIS,
        )

    def _mongo_target(
        self,
        *,
        database: str = "synthetic_db",
        collection: str = "events",
    ) -> WorkspaceTarget:
        return WorkspaceTarget(
            tab_id="mongo-tab:1",
            connection_id="synthetic-mongo",
            profile_revision="revision-mongo-1",
            database=database,
            schema=collection,
            tab_kind=WorkspaceTabKind.MONGO,
        )

    def _mongo_capture(self, **overrides):
        values = {
            "target": self._mongo_target(),
            "editor_text": '{"collection":"events","filter":{}}',
            "dispatched_text": '{"collection":"events","filter":{}}',
            "action": "query",
            "mode": "standalone",
            "database": "synthetic_db",
            "collection": "events",
            "filter": {},
            "projection": None,
            "sort": None,
            "skip": 0,
            "limit": 50,
        }
        values.update(overrides)
        return capture_mongo_run(**values)

    def _replace_metadata(self, body: str, mutate) -> str:
        marker_at = body.rfind(NOSQL_WORKSPACE_TRAILER)
        self.assertGreaterEqual(marker_at, 0)
        raw_text = body[:marker_at]
        trailer = body[marker_at + len(NOSQL_WORKSPACE_TRAILER) :]
        version, metadata_text = trailer.split(" ", 1)
        metadata = json_util.loads(metadata_text, json_options=json_util.CANONICAL_JSON_OPTIONS)
        mutate(metadata)
        changed = json_util.dumps(
            metadata,
            json_options=json_util.CANONICAL_JSON_OPTIONS,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return f"{raw_text}{NOSQL_WORKSPACE_TRAILER}{version} {changed}"

    def _assert_codec_error(self, expected_code: str, callable_) -> NoSqlWorkspaceCodecError:
        with self.assertRaises(NoSqlWorkspaceCodecError) as caught:
            callable_()
        self.assertEqual(caught.exception.code, expected_code)
        self.assertEqual(str(caught.exception), expected_code)
        return caught.exception

    def test_redis_keeps_raw_whitespace_and_frozen_effective_target(self) -> None:
        raw = "  SET sample:value 1  \n"
        snapshot = capture_redis_command(
            self._redis_target(),
            raw,
            raw.strip(),
            mode="standalone",
            db_index=3,
        )

        self.assertEqual(snapshot.editor_text, raw)
        self.assertEqual(snapshot.dispatched_text, "SET sample:value 1")
        self.assertEqual(snapshot.provider, "redis")
        self.assertEqual(snapshot.action, "command")
        arguments = snapshot.worker_arguments()
        self.assertEqual(arguments["database"], "3")
        self.assertEqual(arguments["db_index"], 3)
        self.assertEqual(arguments["mode"], "standalone")
        arguments["db_index"] = 99
        self.assertEqual(snapshot.worker_arguments()["db_index"], 3)

    def test_mongo_canonical_ejson_roundtrip_preserves_order_types_and_isolation(self) -> None:
        object_id = ObjectId("507f1f77bcf86cd799439011")
        timestamp = datetime(
            2026, 10, 7, 12, 34, 56, 123000,
            tzinfo=timezone(timedelta(hours=8)),
        )
        amount = Decimal128("1234567890.1200")
        query_filter = OrderedDict(
            [
                ("_id", object_id),
                ("created_at", {"$gte": timestamp}),
                ("amount", amount),
                ("tags", ["blue", "green"]),
            ]
        )
        projection = OrderedDict([("created_at", 1), ("amount", 1), ("_id", 0)])
        sort = OrderedDict([("created_at", -1), ("amount", 1)])
        raw = '  {"collection":"events","filter":{"amount":{"$gt":1}}}  '
        snapshot = capture_mongo_run(
            self._mongo_target(),
            raw,
            raw.strip(),
            action="query",
            mode="standalone",
            database="synthetic_db",
            collection="events",
            filter=query_filter,
            projection=projection,
            sort=sort,
            skip=7,
            limit=23,
        )

        query_filter["amount"] = Decimal128("999")
        query_filter["tags"].append("mutated")
        projection["amount"] = 0
        sort["created_at"] = 1

        first = snapshot.worker_arguments()
        self.assertEqual(first["filter"]["_id"], object_id)
        self.assertEqual(type(first["filter"]["_id"]), ObjectId)
        self.assertEqual(first["filter"]["amount"], amount)
        self.assertEqual(type(first["filter"]["amount"]), Decimal128)
        self.assertEqual(
            first["filter"]["created_at"]["$gte"].astimezone(timezone.utc),
            timestamp.astimezone(timezone.utc),
        )
        self.assertEqual(list(first["filter"]), ["_id", "created_at", "amount", "tags"])
        self.assertEqual(list(first["projection"]), ["created_at", "amount", "_id"])
        self.assertEqual(list(first["sort"]), ["created_at", "amount"])
        self.assertEqual((first["skip"], first["limit"]), (7, 23))

        first["filter"]["tags"].append("caller-change")
        first["sort"].clear()
        second = snapshot.worker_arguments()
        self.assertEqual(second["filter"]["tags"], ["blue", "green"])
        self.assertEqual(list(second["sort"]), ["created_at", "amount"])
        self.assertEqual(second["filter"]["amount"], amount)

    def test_empty_mongo_documents_query_is_a_valid_capture(self) -> None:
        snapshot = self._mongo_capture(
            editor_text=" \n\t",
            dispatched_text="",
            filter={},
            projection=None,
            sort=None,
            skip=0,
            limit=50,
        )

        self.assertEqual(snapshot.editor_text, " \n\t")
        self.assertEqual(snapshot.dispatched_text, "")
        self.assertEqual(snapshot.action, "query")
        self.assertEqual(snapshot.worker_arguments()["filter"], {})

    def test_capture_rejects_unknown_target_action_and_wrong_parameter_types(self) -> None:
        sql_target = WorkspaceTarget(
            tab_id="sql-tab:1",
            connection_id="synthetic-sql",
            profile_revision="revision-sql-1",
            database="db",
            schema="owner",
            tab_kind=WorkspaceTabKind.SQL,
        )
        self._assert_codec_error(
            "UNSUPPORTED_PROVIDER",
            lambda: capture_redis_command(
                sql_target, "PING", "PING", mode="standalone", db_index=0,
            ),
        )
        self._assert_codec_error(
            "UNSUPPORTED_ACTION",
            lambda: self._mongo_capture(action=["query"]),
        )
        self._assert_codec_error(
            "INVALID_PARAMETERS",
            lambda: self._mongo_capture(filter=[]),
        )
        self._assert_codec_error(
            "INVALID_PARAMETERS",
            lambda: self._mongo_capture(skip=True),
        )
        self._assert_codec_error(
            "INVALID_PARAMETERS",
            lambda: self._mongo_capture(filter={"field": "value\x00"}),
        )
        self._assert_codec_error(
            "INVALID_PARAMETERS",
            lambda: self._mongo_capture(sort=[("field", 1)]),
        )
        self._assert_codec_error(
            "INVALID_PARAMETERS",
            lambda: self._mongo_capture(filter={"date": {"$date": {"$numberLong": "1"}}}),
        )

    def test_decode_rejects_unknown_version_provider_action_and_fields(self) -> None:
        snapshot = capture_redis_command(
            self._redis_target(), "PING", "PING", mode="standalone", db_index=3,
        )

        bad_version = snapshot.body.replace(
            f"{NOSQL_WORKSPACE_TRAILER}1 ",
            f"{NOSQL_WORKSPACE_TRAILER}2 ",
            1,
        )
        version_error = self._assert_codec_error(
            "UNSUPPORTED_VERSION",
            lambda: decode_nosql_workspace(snapshot.target, bad_version),
        )
        self.assertEqual(version_error.display_text, bad_version)

        bad_provider = self._replace_metadata(
            snapshot.body,
            lambda metadata: metadata.__setitem__("provider", "sql"),
        )
        self._assert_codec_error(
            "UNSUPPORTED_PROVIDER",
            lambda: decode_nosql_workspace(snapshot.target, bad_provider),
        )

        bad_action = self._replace_metadata(
            snapshot.body,
            lambda metadata: metadata.__setitem__("action", "delete"),
        )
        self._assert_codec_error(
            "UNSUPPORTED_ACTION",
            lambda: decode_nosql_workspace(snapshot.target, bad_action),
        )

        unknown_field = self._replace_metadata(
            snapshot.body,
            lambda metadata: metadata.__setitem__("unexpected", 1),
        )
        self._assert_codec_error(
            "CORRUPT_ENVELOPE",
            lambda: decode_nosql_workspace(snapshot.target, unknown_field),
        )

        unknown_parameter = self._replace_metadata(
            snapshot.body,
            lambda metadata: metadata["parameters"].__setitem__("unexpected", 1),
        )
        self._assert_codec_error(
            "INVALID_PARAMETERS",
            lambda: decode_nosql_workspace(snapshot.target, unknown_parameter),
        )

    def test_corrupt_trailer_and_target_mismatch_are_non_runnable_and_copyable(self) -> None:
        snapshot = self._mongo_capture()
        damaged = snapshot.body[:-1] + "!"
        damaged_error = self._assert_codec_error(
            "CORRUPT_ENVELOPE",
            lambda: decode_nosql_workspace(snapshot.target, damaged),
        )
        self.assertEqual(damaged_error.display_text, damaged)

        other_target = self._mongo_target(database="other_db")
        mismatch = self._assert_codec_error(
            "INVALID_TARGET",
            lambda: decode_nosql_workspace(other_target, snapshot.body),
        )
        self.assertEqual(mismatch.display_text, snapshot.body)

    def test_total_body_limit_is_enforced(self) -> None:
        self._assert_codec_error(
            "TEXT_TOO_LARGE",
            lambda: capture_redis_command(
                self._redis_target(db_index=0),
                "x" * (256 * 1024),
                "x" * (256 * 1024),
                mode="standalone",
                db_index=0,
            ),
        )

    def test_existing_store_credential_gate_sees_plain_extended_json_metadata(self) -> None:
        snapshot = self._mongo_capture(
            filter={"password": "synthetic-test-value"},
        )
        record = HistoryRecord(
            history_id="synthetic-history-1",
            target=snapshot.target,
            text=snapshot.body,
            text_kind=WorkspaceTextKind.NOSQL,
        )
        self.assertIn('"password"', snapshot.body)
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = WorkspaceStore(Path(temporary_directory) / "workspace.sqlite3")
            try:
                with self.assertRaises(WorkspaceStoreError) as caught:
                    store.save_history(record)
                self.assertEqual(caught.exception.code, "CREDENTIAL_TEXT_REJECTED")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
