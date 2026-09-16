"""Boundary tests for the host-owned read-only NoSQL facades.

These tests deliberately use tiny fake drivers.  They exercise the seam from
an injected lease through the bounded model projection without opening a
socket, reading a profile, or invoking a database driver.
"""

from __future__ import annotations

import json
import threading
import unittest

from bson import ObjectId
from bson.code import Code

from tools.data_center.nosql_codec import decode_extended_json
from tools.data_center.readonly_nosql_clients import (
    MongoReadOnlyFacade,
    OpaqueCursorCodec,
    RedisReadOnlyFacade,
)
from tools.data_center.result_projection import ResultProjector


class _Cancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class _RedisFake:
    def __init__(self, pages=None) -> None:
        self.pages = pages or {0: (1, [b"orders:1"]), 1: (0, [b"orders:2"])}
        self.calls: list[tuple[str, dict]] = []
        self.dangerous_calls: list[tuple[tuple, dict]] = []

    def scan(self, cursor=0, match=None, count=None, target_nodes=None):
        self.calls.append(
            (
                "scan",
                {"cursor": cursor, "match": match, "count": count, "target_nodes": target_nodes},
            )
        )
        return self.pages.get(int(cursor), (0, []))

    def strlen(self, name):
        self.calls.append(("strlen", {"name": name}))
        return 5

    def get(self, name):
        self.calls.append(("get", {"name": name}))
        return b"value"

    def set(self, *args, **kwargs):  # pragma: no cover - must never run
        self.dangerous_calls.append((args, kwargs))
        raise AssertionError("write method reached")

    def execute_command(self, *args, **kwargs):  # pragma: no cover - must never run
        self.dangerous_calls.append((args, kwargs))
        raise AssertionError("generic command reached")


class _BlockingRedisFake(_RedisFake):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def type(self, name):
        self.entered.set()
        self.release.wait(timeout=2)
        return b"string"

    def close(self):
        self.closed = True


class _MongoCursor:
    def __init__(self, values, *, cancellation: _Cancellation | None = None, timeout: bool = False) -> None:
        self.values = list(values)
        self.cancellation = cancellation
        self.timeout = timeout
        self.closed = False

    def __iter__(self):
        for index, value in enumerate(self.values):
            if index == 1 and self.cancellation is not None:
                self.cancellation.cancelled = True
            yield value
        if self.timeout:
            raise TimeoutError("fake Mongo socket timeout")

    def close(self) -> None:
        self.closed = True


class _MongoCollection:
    def __init__(self, docs, *, timeout: bool = False, cancellation: _Cancellation | None = None) -> None:
        self.docs = list(docs)
        self.timeout = timeout
        self.cancellation = cancellation
        self.calls: list[tuple[str, dict]] = []
        self.cursors: list[_MongoCursor] = []
        self.dangerous_calls: list[tuple[tuple, dict]] = []

    def find(self, **kwargs):
        self.calls.append(("find", kwargs))
        cursor = _MongoCursor(self.docs, cancellation=self.cancellation, timeout=self.timeout)
        self.cursors.append(cursor)
        return cursor

    def count_documents(self, **kwargs):
        self.calls.append(("count_documents", kwargs))
        return len(self.docs)

    def aggregate(self, **kwargs):
        self.calls.append(("aggregate", kwargs))
        cursor = _MongoCursor(self.docs)
        self.cursors.append(cursor)
        return cursor

    def drop(self, *args, **kwargs):  # pragma: no cover - must never run
        self.dangerous_calls.append((args, kwargs))
        raise AssertionError("management method reached")


class _BlockingMongoCollection(_MongoCollection):
    def __init__(self) -> None:
        super().__init__([{"id": 1}])
        self.entered = threading.Event()
        self.release = threading.Event()

    def find(self, **kwargs):
        self.entered.set()
        self.release.wait(timeout=2)
        return super().find(**kwargs)


class _OwnerClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self):
        self.closed = True


def _mongo_facade(collection: _MongoCollection, **kwargs) -> MongoReadOnlyFacade:
    return MongoReadOnlyFacade(
        collections={"orders": collection},
        connection_id="mongo-lease-1",
        database_name="analytics",
        allowed_collection_ids=("orders",),
        **kwargs,
    )


class NoSQLIntegrationTests(unittest.TestCase):
    def test_mongo_result_reaches_model_only_after_bounded_projection(self) -> None:
        collection = _MongoCollection(
            [
                {
                    "_id": ObjectId("507f1f77bcf86cd799439011"),
                    "password": "do-not-send",
                    "profile": {"api_token": "do-not-send-either"},
                    "script": Code("db.orders.drop()"),
                    "payload": "x" * 20_000,
                }
            ]
        )
        facade = _mongo_facade(collection)
        result = facade.execute(
            {"collection_id": "orders", "operation": "find", "filter": {}, "limit": 1},
            query_id="mongo-call-1",
        )
        self.assertTrue(result.ok)

        projected = ResultProjector(max_result_bytes=4 * 1024, max_cumulative_bytes=4 * 1024).project(result)
        message = projected.as_model_message("call-mongo-1")
        payload = json.loads(message["content"])

        self.assertTrue(projected.ok)
        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call-mongo-1")
        self.assertEqual(payload["query_id"], "mongo-call-1")
        self.assertEqual(payload["scope"], {"connection_id": "mongo-lease-1", "database": "analytics", "collection_id": "orders"})
        # The NoSQL codec already truncated the document before projection;
        # the model envelope remains within the projector's byte budget.
        self.assertTrue(result.truncated)
        self.assertNotIn("do-not-send", message["content"])
        self.assertNotIn("db.orders.drop()", message["content"])
        self.assertLessEqual(len(message["content"].encode("utf-8")), 4 * 1024)

    def test_cursor_is_bound_to_the_host_lease_and_wrong_lease_does_zero_io(self) -> None:
        codec = OpaqueCursorCodec()
        first_client = _RedisFake()
        first = RedisReadOnlyFacade(
            first_client,
            connection_id="redis-lease-1",
            profile_revision="profile-r1",
            cursor_codec=codec,
        ).execute({"operation": "SCAN", "limit": 1}, query_id="redis-call-1")
        self.assertTrue(first.ok)
        self.assertTrue(first.data["next_cursor"])

        second_client = _RedisFake()
        second_facade = RedisReadOnlyFacade(
            second_client,
            connection_id="redis-lease-2",
            profile_revision="profile-r1",
            cursor_codec=codec,
        )
        result = second_facade.execute(
            {"operation": "SCAN", "cursor": first.data["next_cursor"], "limit": 1},
            query_id="redis-call-2",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "SCOPE_DENIED")
        self.assertEqual(second_client.calls, [])
        self.assertEqual(second_client.dangerous_calls, [])

    def test_timeout_closes_mongo_cursor_and_returns_stable_code(self) -> None:
        collection = _MongoCollection([{"id": 1}], timeout=True)
        facade = _mongo_facade(collection)

        result = facade.execute({"collection_id": "orders", "operation": "find", "limit": 1}, query_id="timeout-1")

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "TIMEOUT")
        self.assertEqual(len(collection.cursors), 1)
        self.assertTrue(collection.cursors[0].closed)

    def test_cancel_during_mongo_iteration_closes_cursor(self) -> None:
        cancellation = _Cancellation()
        collection = _MongoCollection([{"id": 1}, {"id": 2}], cancellation=cancellation)
        facade = _mongo_facade(collection)

        result = facade.execute(
            {"collection_id": "orders", "operation": "find", "limit": 2},
            cancellation=cancellation,
            query_id="cancel-1",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CANCELLED")
        self.assertTrue(collection.cursors[0].closed)

    def test_cancel_after_blocking_redis_call_returns_unknown_and_closes_owner(self) -> None:
        cancellation = _Cancellation()
        client = _BlockingRedisFake()
        facade = RedisReadOnlyFacade(client, connection_id="redis-blocking")
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                facade.execute({"operation": "TYPE", "key": "orders:1"}, cancellation=cancellation)
            )
        )
        worker.start()
        self.assertTrue(client.entered.wait(timeout=1))
        cancellation.cancelled = True
        client.release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].code, "CANCELLED")
        self.assertTrue(results[0].data["result_unknown"])
        self.assertTrue(client.closed)

    def test_cancel_after_blocking_mongo_factory_returns_unknown_and_closes_owner(self) -> None:
        cancellation = _Cancellation()
        collection = _BlockingMongoCollection()
        client = _OwnerClient()
        facade = _mongo_facade(collection, client=client)
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                facade.execute(
                    {"collection_id": "orders", "operation": "find", "limit": 1},
                    cancellation=cancellation,
                )
            )
        )
        worker.start()
        self.assertTrue(collection.entered.wait(timeout=1))
        cancellation.cancelled = True
        collection.release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].code, "CANCELLED")
        self.assertTrue(results[0].data["result_unknown"])
        self.assertTrue(client.closed)
        self.assertTrue(collection.cursors[0].closed)

    def test_host_deadline_requires_socket_timeout_and_is_not_model_overridable(self) -> None:
        collection = _MongoCollection([{"id": 1}])
        facade = _mongo_facade(collection, timeout_ms=10)
        result = facade.execute({"collection_id": "orders", "operation": "find", "limit": 1})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "TIMEOUT_UNAVAILABLE")
        self.assertEqual(collection.calls, [])

        class _SocketClient:
            socket_timeout = 1.0

            def close(self):
                pass

        bounded = _mongo_facade(collection, timeout_ms=500, client=_SocketClient())
        result = bounded.execute({"collection_id": "orders", "operation": "find", "limit": 1})
        self.assertTrue(result.ok)
        self.assertTrue(result.limits["client_socket_timeout_configured"])
        self.assertTrue(result.limits["timeout_enforced"])

    def test_invalid_types_and_connection_overrides_are_rejected_before_driver_io(self) -> None:
        redis_client = _RedisFake()
        redis = RedisReadOnlyFacade(redis_client, connection_id="redis-lease-1")
        for request in (
            {"operation": "SCAN", "limit": True},
            {"operation": "GET", "key": 123},
            {"operation": "GET", "key": "orders:1", "host": "model-host"},
            {"operation": "GET", "key": "orders:1", "command": "GET orders:1"},
        ):
            result = redis.execute(request)
            self.assertFalse(result.ok)
        self.assertEqual(redis_client.calls, [])
        self.assertEqual(redis_client.dangerous_calls, [])

        collection = _MongoCollection([{"id": 1}])
        mongo = _mongo_facade(collection)
        for request in (
            {"collection_id": "orders", "operation": "find", "database": "other"},
            {"collection_id": "orders", "operation": "find", "filter": {"$where": "this.id"}},
            {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$out": "archive"}]},
        ):
            result = mongo.execute(request)
            self.assertFalse(result.ok)
        self.assertEqual(collection.calls, [])
        self.assertEqual(collection.dangerous_calls, [])

    def test_extended_json_non_string_key_is_a_type_error(self) -> None:
        with self.assertRaises(Exception):
            decode_extended_json({1: "invalid field name"})

    def test_mongo_output_limits_reject_invalid_types(self) -> None:
        collection = _MongoCollection([{"id": 1}])
        for kwargs in (
            {"max_output_bytes": 0},
            {"max_output_bytes": True},
            {"max_document_bytes": 0},
            {"max_document_bytes": False},
        ):
            with self.assertRaises(ValueError):
                _mongo_facade(collection, **kwargs)


if __name__ == "__main__":
    unittest.main()
