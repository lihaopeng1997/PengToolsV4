from __future__ import annotations

import unittest

from bson import Binary, Decimal128, ObjectId
from bson.code import Code

from tools.data_center.nosql_codec import CodecError, decode_extended_json, encode_bounded
from tools.data_center.readonly_nosql_clients import (
    MongoReadOnlyFacade,
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
