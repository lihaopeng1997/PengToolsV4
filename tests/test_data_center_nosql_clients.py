from __future__ import annotations

import unittest

from bson import Binary, Decimal128, ObjectId
from bson.code import Code

from tools.data_center.nosql_codec import CodecError, decode_extended_json, encode_bounded
from tools.data_center.readonly_nosql_clients import (
    MongoReadOnlyFacade,
    NoSQLDirectoryPage,
    OpaqueCursorCodec,
    RedisReadOnlyFacade,
)


class _Cancel:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class _RedisFake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.dangerous_calls: list[tuple[tuple, dict]] = []
        self.pages = {0: (1, [b"orders:1", b"orders:2"]), 1: (0, [b"orders:3"])}

    def scan(self, cursor=0, match=None, count=None, target_nodes=None):
        self.calls.append(("scan", {"cursor": cursor, "match": match, "count": count, "target_nodes": target_nodes}))
        return self.pages.get(int(cursor), (0, []))

    def strlen(self, name):
        self.calls.append(("strlen", {"name": name}))
        return 4

    def getrange(self, name, start, end):
        self.calls.append(("getrange", {"name": name, "start": start, "end": end}))
        return b"preview"

    def get(self, name):
        self.calls.append(("get", {"name": name}))
        return b"value"

    def type(self, name):
        self.calls.append(("type", {"name": name}))
        return b"string"

    def ttl(self, name):
        self.calls.append(("ttl", {"name": name}))
        return -1

    def pttl(self, name):
        self.calls.append(("pttl", {"name": name}))
        return -2

    def hstrlen(self, name, key):
        self.calls.append(("hstrlen", {"name": name, "key": key}))
        return 3

    def hget(self, name, key):
        self.calls.append(("hget", {"name": name, "key": key}))
        return b"abc"

    def set(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.dangerous_calls.append((args, kwargs))
        raise AssertionError("write method reached")

    def execute_command(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.dangerous_calls.append((args, kwargs))
        raise AssertionError("generic command reached")

    def close(self):
        self.calls.append(("close", {}))


class _RedisLargeFake(_RedisFake):
    def strlen(self, name):
        self.calls.append(("strlen", {"name": name}))
        return 100

    def getrange(self, name, start, end):
        self.calls.append(("getrange", {"name": name, "start": start, "end": end}))
        return b"x" * 8

    def get(self, name):  # pragma: no cover - preflight must prevent this
        raise AssertionError("large GET must not fetch the full value")


class _ClusterRedisFake(_RedisFake):
    def __init__(self) -> None:
        super().__init__()
        self.target_calls: list[object] = []

    def scan(self, cursor=0, match=None, count=None, target_nodes=None):
        self.calls.append(("scan", {"cursor": cursor, "match": match, "count": count, "target_nodes": target_nodes}))
        if target_nodes is None:
            return {"node-a": 1, "node-b": 0}, [b"cluster:1"]
        self.target_calls.append(target_nodes)
        return {"node-a": 0}, [b"cluster:2"]

    def get_node(self, node_name=None):
        return object() if node_name == "node-a" else None


class RedisReadOnlyFacadeTests(unittest.TestCase):
    def test_scan_uses_opaque_cursor_and_preserves_pending_items(self) -> None:
        fake = _RedisFake()
        facade = RedisReadOnlyFacade(fake, connection_id="redis-1", profile_revision="r1")

        first = facade.execute({"operation": "SCAN", "pattern": "orders:*", "limit": 1}, query_id="q1")
        self.assertTrue(first.ok)
        self.assertEqual(first.data["keys"][0]["type"], "binary")
        self.assertTrue(first.data["next_cursor"].startswith("cursor_"))
        self.assertEqual(fake.calls[0][1]["count"], 1)

        second = facade.execute(
            {"operation": "SCAN", "cursor": first.data["next_cursor"], "limit": 1},
            query_id="q2",
        )
        self.assertTrue(second.ok)
        self.assertEqual(second.data["keys"][0]["preview_base64"], "b3JkZXJzOjI=")
        self.assertTrue(second.data["next_cursor"])

        third = facade.execute(
            {"operation": "SCAN", "cursor": second.data["next_cursor"], "limit": 1},
            query_id="q3",
        )
        self.assertTrue(third.ok)
        self.assertEqual(third.data["keys"][0]["preview_base64"], "b3JkZXJzOjM=")
        self.assertIsNone(third.data["next_cursor"])

    def test_cluster_scan_keeps_node_targeting_and_hides_node_addresses(self) -> None:
        fake = _ClusterRedisFake()
        facade = RedisReadOnlyFacade(
            fake,
            connection_id="redis-cluster",
            profile_revision="r1",
            mode="cluster",
            cursor_codec=OpaqueCursorCodec(),
            max_pages=4,
        )

        result = facade.execute({"operation": "SCAN", "limit": 1}, query_id="cluster-q")
        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertTrue(result.data["next_cursor"])
        self.assertNotIn("node-a", result.data["failed_nodes"])
        self.assertNotIn("node-a", result.data["next_cursor"])

        continuation = facade.execute(
            {"operation": "SCAN", "cursor": result.data["next_cursor"], "limit": 1},
            query_id="cluster-q-2",
        )
        self.assertTrue(continuation.ok)
        self.assertTrue(fake.target_calls)
        self.assertNotIn("node-a", continuation.data["next_cursor"] or "")

    def test_scope_mismatch_and_dangerous_operations_do_zero_driver_io(self) -> None:
        fake = _RedisFake()
        facade = RedisReadOnlyFacade(fake, connection_id="redis-1", profile_revision="r1")
        first = facade.execute({"operation": "SCAN", "limit": 1})
        calls_before = len(fake.calls)

        other = RedisReadOnlyFacade(_RedisFake(), connection_id="redis-2", profile_revision="r1")
        wrong_scope = other.execute({"operation": "SCAN", "cursor": first.data["next_cursor"]})
        self.assertFalse(wrong_scope.ok)
        self.assertEqual(wrong_scope.code, "ARGUMENT_INVALID")

        for request in (
            {"operation": "SET", "key": "orders:1"},
            {"operation": "EVAL", "key": "orders:1"},
            {"operation": "GET", "key": "orders:1", "host": "model-host"},
        ):
            result = facade.execute(request)
            self.assertFalse(result.ok)
        self.assertEqual(len(fake.calls), calls_before)
        self.assertEqual(fake.dangerous_calls, [])

    def test_large_get_preflights_length_and_retains_ttl_sentinels(self) -> None:
        fake = _RedisLargeFake()
        facade = RedisReadOnlyFacade(
            fake,
            connection_id="redis-1",
            profile_revision="r1",
            max_value_bytes=8,
        )
        result = facade.execute({"operation": "GET", "key": "large"}, query_id="large-q")
        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertEqual(result.data["size"], 100)
        self.assertEqual([name for name, _args in fake.calls], ["strlen", "getrange"])

        ttl = facade.execute({"operation": "TTL", "key": "missing"})
        pttl = facade.execute({"operation": "PTTL", "key": "gone"})
        self.assertEqual(ttl.data["value"]["value"], -1)
        self.assertEqual(pttl.data["value"]["value"], -2)

    def test_cancel_before_read_does_not_call_client(self) -> None:
        fake = _RedisFake()
        facade = RedisReadOnlyFacade(fake, connection_id="redis-1")
        result = facade.execute({"operation": "TYPE", "key": "x"}, cancellation=_Cancel(True))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CANCELLED")
        self.assertEqual(fake.calls, [])

    def test_directory_scan_keeps_binary_identity_private_and_checks_literal_prefix(self) -> None:
        first_key = b"tenant[1]/" + b"\xff" + b"x" * 52 + b"1"
        second_key = b"tenant[1]/" + b"\xff" + b"x" * 52 + b"2"
        oversized = b"tenant[1]/" + b"y" * 5000

        class DirectoryRedis(_RedisFake):
            def scan(self, cursor=0, match=None, count=None, target_nodes=None):
                self.calls.append(("scan", {"cursor": cursor, "match": match, "count": count, "target_nodes": target_nodes}))
                return 0, [first_key, second_key, b"tenant1/outside", oversized]

        fake = DirectoryRedis()
        facade = RedisReadOnlyFacade(
            fake,
            connection_id="redis-tree",
            profile_revision="revision-1",
            _directory_metadata=True,
        )
        page = facade._directory_children("redis_root", prefix="tenant[1]/", limit=10)

        self.assertIsInstance(page, NoSQLDirectoryPage)
        self.assertEqual(len(page.entries), 3)
        self.assertEqual(page.entries[0].name, page.entries[1].name)
        self.assertNotEqual(page.entries[0].identity, page.entries[1].identity)
        self.assertIsNone(page.entries[2].identity)
        self.assertEqual(page.entries[2].notice, "redis_key_identity_over_budget")
        self.assertIn("redis_key_outside_prefix_omitted", page.notices)
        self.assertEqual(fake.calls[0][1]["match"], r"tenant\[1\]/*")

        details = facade._directory_inspect_key(first_key)
        self.assertEqual(details, {"value_type": "string", "ttl_seconds": -1})
        self.assertIn(("type", {"name": first_key}), fake.calls)
        self.assertIn(("ttl", {"name": first_key}), fake.calls)

        blocked = facade.execute({"operation": "SCAN", "pattern": "tenant[1]/*"})
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.code, "POLICY_DENIED")

    def test_default_agent_scan_keeps_256_byte_preview_without_tree_identity(self) -> None:
        key = b"x" * 400

        class PreviewRedis(_RedisFake):
            def scan(self, cursor=0, match=None, count=None, target_nodes=None):
                self.calls.append(("scan", {"cursor": cursor, "match": match, "count": count, "target_nodes": target_nodes}))
                return 0, [key]

        facade = RedisReadOnlyFacade(PreviewRedis(), connection_id="redis-agent", profile_revision="r1")
        result = facade.execute({"operation": "SCAN", "limit": 1})
        self.assertTrue(result.ok)
        row = result.data["keys"][0]
        self.assertEqual(row["size"], len(key))
        self.assertEqual(len(row["preview_base64"]), 344)
        self.assertNotIn("identity", row)


class _MongoCursor:
    def __init__(self, values):
        self.values = list(values)
        self.closed = False

    def __iter__(self):
        return iter(self.values)

    def close(self):
        self.closed = True


class _MongoCollection:
    def __init__(self, docs=None, aggregate=None, count=0):
        self.docs = list(docs or [])
        self.aggregate_docs = list(aggregate if aggregate is not None else self.docs)
        self.count_value = count
        self.calls: list[tuple[str, dict]] = []
        self.cursors: list[_MongoCursor] = []
        self.dangerous_calls = []

    def find(self, **kwargs):
        self.calls.append(("find", kwargs))
        cursor = _MongoCursor(self.docs)
        self.cursors.append(cursor)
        return cursor

    def count_documents(self, **kwargs):
        self.calls.append(("count_documents", kwargs))
        return self.count_value

    def aggregate(self, **kwargs):
        self.calls.append(("aggregate", kwargs))
        cursor = _MongoCursor(self.aggregate_docs)
        self.cursors.append(cursor)
        return cursor

    def delete_many(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.dangerous_calls.append((args, kwargs))
        raise AssertionError("write method reached")


class _MongoTreeDatabase:
    def __init__(self, names) -> None:
        self.names = list(names)
        self.cursors = []

    def list_collections(self, **kwargs):
        cursor = _MongoCursor({"name": name} for name in self.names)
        self.cursors.append((kwargs, cursor))
        return cursor


class _MongoTreeClient:
    socket_timeout = 1.0

    def __init__(self) -> None:
        self.closed = 0
        self.calls = []
        self.database_names = ["admin", "app", "logs"]
        self.database = _MongoTreeDatabase(["users", "orders", "secrets"])

    def list_databases(self, **kwargs):
        self.calls.append(("list_databases", kwargs))
        return _MongoCursor({"name": name} for name in self.database_names)

    def get_database(self, name):
        self.calls.append(("get_database", name))
        return self.database

    def close(self):
        self.closed += 1


class MongoDirectoryFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _MongoTreeClient()
        self.facade = MongoReadOnlyFacade(
            database=self.client.database,
            client=self.client,
            connection_id="mongo-tree",
            database_name="configured",
            allowed_collection_ids=(),
            _directory_metadata=True,
        )

    def test_directory_database_collection_pages_are_bounded_and_agent_execute_is_closed(self) -> None:
        first = self.facade._directory_children("mongo_root", limit=1)
        self.assertEqual([entry.identity for entry in first.entries], ["admin"])
        self.assertTrue(first.next_cursor)
        second = self.facade._directory_children("mongo_root", cursor=first.next_cursor, limit=1)
        self.assertEqual([entry.identity for entry in second.entries], ["app"])
        self.assertTrue(second.next_cursor)

        collections = self.facade._directory_children("mongo_database", database="app", limit=2)
        self.assertEqual([entry.identity for entry in collections.entries], ["users", "orders"])
        self.assertTrue(collections.next_cursor)
        tail = self.facade._directory_children("mongo_database", database="app", cursor=collections.next_cursor, limit=2)
        self.assertEqual([entry.identity for entry in tail.entries], ["secrets"])
        self.assertIsNone(tail.next_cursor)
        self.assertEqual(
            self.client.calls[0],
            ("list_databases", {"nameOnly": True, "authorizedDatabases": True, "batchSize": 2}),
        )
        self.assertEqual(self.client.database.cursors[0][0], {"nameOnly": True, "batchSize": 3})

        blocked = self.facade.execute({"operation": "find", "collection_id": "users"})
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.code, "POLICY_DENIED")

    def test_mongo_cursor_cannot_cross_parent_and_bad_database_scope_is_rejected(self) -> None:
        first = self.facade._directory_children("mongo_root", limit=1)
        with self.assertRaises(Exception) as raised:
            self.facade._directory_children("mongo_database", database="admin", cursor=first.next_cursor, limit=1)
        self.assertEqual(getattr(raised.exception, "code", None), "SCOPE_DENIED")
        with self.assertRaises(Exception) as raised:
            self.facade._directory_children("mongo_database", database="a\x00b")
        self.assertEqual(getattr(raised.exception, "code", None), "ARGUMENT_INVALID")


class MongoReadOnlyFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collection = _MongoCollection(
            docs=[
                {"_id": ObjectId("507f1f77bcf86cd799439011"), "password": "hidden", "count": 1},
                {"_id": ObjectId("507f1f77bcf86cd799439012"), "payload": Binary(b"raw")},
                {"_id": ObjectId("507f1f77bcf86cd799439013"), "price": Decimal128("1.20"), "code": Code("return 1")},
            ],
            count=5,
        )
        self.facade = MongoReadOnlyFacade(
            collections={"orders": self.collection},
            connection_id="mongo-1",
            database_name="demo",
            allowed_collection_ids=("orders",),
            max_output_bytes=24 * 1024,
        )

    def test_find_is_server_bounded_and_bson_is_typed_or_omitted(self) -> None:
        result = self.facade.execute(
            {"collection_id": "orders", "operation": "find", "filter": {}, "limit": 2},
            query_id="find-q",
        )
        self.assertTrue(result.ok)
        call_name, kwargs = self.collection.calls[0]
        self.assertEqual(call_name, "find")
        self.assertEqual(kwargs["limit"], 3)
        self.assertEqual(kwargs["batch_size"], 3)
        self.assertEqual(kwargs["max_time_ms"], 15_000)
        self.assertFalse(kwargs["allow_disk_use"])
        self.assertTrue(result.truncated)
        self.assertEqual(result.data["documents"][0]["type"], "object")
        object_value = result.data["documents"][0]["value"]
        self.assertEqual(object_value["_id"]["type"], "object_id")
        self.assertEqual(object_value["password"]["type"], "redacted")
        self.assertTrue(self.collection.cursors[0].closed)

    def test_count_is_capped_and_aggregate_appends_server_limit(self) -> None:
        count = self.facade.execute({"collection_id": "orders", "operation": "count", "limit": 2})
        self.assertTrue(count.ok)
        self.assertEqual(count.data["count"], 2)
        self.assertTrue(count.truncated)
        self.assertEqual(self.collection.calls[0][1]["limit"], 3)
        self.assertEqual(self.collection.calls[0][1]["maxTimeMS"], 15_000)

        aggregate = self.facade.execute(
            {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$match": {}}], "limit": 2}
        )
        self.assertTrue(aggregate.ok)
        aggregate_call = self.collection.calls[1][1]
        self.assertEqual(aggregate_call["pipeline"][-1], {"$limit": 3})
        self.assertFalse(aggregate_call["allowDiskUse"])
        self.assertEqual(aggregate_call["maxTimeMS"], 15_000)
        self.assertEqual(aggregate_call["batchSize"], 3)

    def test_collection_scope_and_nested_unsafe_operations_are_fail_closed(self) -> None:
        calls_before = len(self.collection.calls)
        denied_collection = self.facade.execute(
            {"collection_id": "users", "operation": "find", "filter": {}}
        )
        denied_stage = self.facade.execute(
            {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$out": "archive"}]}
        )
        denied_nested = self.facade.execute(
            {"collection_id": "orders", "operation": "find", "filter": {"x": {"$where": "this.x"}}}
        )
        for result in (denied_collection, denied_stage, denied_nested):
            self.assertFalse(result.ok)
        self.assertEqual(denied_collection.code, "SCOPE_DENIED")
        self.assertEqual(len(self.collection.calls), calls_before)
        self.assertEqual(self.collection.dangerous_calls, [])

    def test_empty_allowlist_and_cancel_do_not_create_a_read_scope(self) -> None:
        with self.assertRaises(ValueError):
            MongoReadOnlyFacade(
                collections={"orders": self.collection},
                connection_id="mongo-1",
                database_name="demo",
                allowed_collection_ids=(),
            )
        calls_before = len(self.collection.calls)
        result = self.facade.execute(
            {"collection_id": "orders", "operation": "find"},
            cancellation=_Cancel(True),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CANCELLED")
        self.assertEqual(len(self.collection.calls), calls_before)


class NoSQLCodecTests(unittest.TestCase):
    def test_codec_is_bounded_and_does_not_return_code_source(self) -> None:
        encoded = encode_bounded(
            {"secret_token": "do-not-show", "payload": b"x" * 1000, "script": Code("secret source")},
            max_bytes=1024,
        )
        self.assertLessEqual(encoded.bytes_used, 1024)
        self.assertTrue(encoded.truncated)
        value = encoded.value["value"]
        self.assertEqual(value["secret_token"]["type"], "redacted")
        self.assertEqual(value["script"]["type"], "code")
        self.assertTrue(value["script"]["omitted"])
        self.assertNotIn("secret source", str(encoded.value))

    def test_extended_json_decoding_is_explicit_and_rejects_code(self) -> None:
        decoded = decode_extended_json({"id": {"$oid": "507f1f77bcf86cd799439011"}})
        self.assertEqual(str(decoded["id"]), "507f1f77bcf86cd799439011")
        with self.assertRaises(CodecError):
            decode_extended_json({"$code": "return db.users.drop()"})

    def test_codec_honors_minimum_byte_budget(self) -> None:
        encoded = encode_bounded("x" * 100, max_bytes=1)
        self.assertLessEqual(encoded.bytes_used, 1)
        self.assertTrue(encoded.truncated)

    def test_codec_redacts_camel_case_sensitive_fields_recursively(self) -> None:
        encoded = encode_bounded(
            {
                "apiToken": "top-level",
                "nested": {
                    "clientSecret": "nested-secret",
                    "passwordHash": "hashed-password",
                    "items": [{"refreshToken": "refresh-secret", "safe": "ok"}],
                },
            },
            max_bytes=8 * 1024,
        )
        value = encoded.value["value"]
        self.assertEqual(value["apiToken"]["type"], "redacted")
        nested = value["nested"]["value"]
        self.assertEqual(nested["clientSecret"]["type"], "redacted")
        self.assertEqual(nested["passwordHash"]["type"], "redacted")
        self.assertEqual(nested["items"]["value"][0]["value"]["refreshToken"]["type"], "redacted")
        self.assertEqual(nested["items"]["value"][0]["value"]["safe"]["value"], "ok")

    def test_opaque_cursor_state_rejects_oversized_state_before_json_copy(self) -> None:
        codec = OpaqueCursorCodec(max_state_bytes=128, max_state_items=4, max_state_depth=2)
        with self.assertRaises(ValueError):
            codec.encode({"pending": ["x", "y", "z", "too-many"]})
        with self.assertRaises(ValueError):
            codec.encode({"pending": "x" * 256})
        with self.assertRaises(ValueError):
            codec.encode({"pending": [[[{"too": "deep"}]]]})


if __name__ == "__main__":
    unittest.main()
