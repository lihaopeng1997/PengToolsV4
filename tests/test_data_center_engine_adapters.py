"""Focused DC-02 tests for injected engine adapters.

The fakes below are deliberately small.  They make it observable that policy
rejections happen before a host resolver, read-only hook or driver method is
called, while successful operations still preserve the injected result shape.
"""

from __future__ import annotations

import unittest

from tools.data_center.sql_policy import SQLDecision
from tools.data_center.engine_adapters import (
    ENGINE_CAPABILITIES,
    MongoEngineAdapter,
    RedisEngineAdapter,
    ReadOnlyRequest,
    RelationalEngineAdapter,
    get_engine_adapter,
    normalize_engine,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get(self, key):
        self.calls.append(("get", key))
        return b"value"

    def scan(self, cursor=0, match=None, count=None):
        self.calls.append(("scan", (cursor, match, count)))
        return 0, [b"orders:1"]

    def set(self, key, value):  # pragma: no cover - must never be reached
        self.calls.append(("set", (key, value)))
        return True


class _FakeCollection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def find(self, filter=None, projection=None, sort=None):
        self.calls.append(("find", (filter, projection, sort)))
        return [{"_id": 1, "status": "open"}]

    def count_documents(self, filter=None):
        self.calls.append(("count", filter))
        return 1

    def aggregate(self, pipeline=None):
        self.calls.append(("aggregate", pipeline))
        return [{"status": "open", "count": 1}]

    def delete_many(self, filter):  # pragma: no cover - must never be reached
        self.calls.append(("delete_many", filter))
        return {"deleted_count": 1}


class EngineAdapterTests(unittest.TestCase):
    def test_capability_matrix_covers_all_provider_modes_and_dialects(self) -> None:
        self.assertEqual(
            set(ENGINE_CAPABILITIES),
            {
                "oracle",
                "mysql",
                "oceanbase_oracle",
                "oceanbase_mysql",
                "dameng",
                "redis",
                "mongodb",
            },
        )
        self.assertEqual(normalize_engine({"dialect": "oceanbase", "mode": "oracle"}), "oceanbase_oracle")
        self.assertEqual(normalize_engine({"dialect": "oceanbase", "mode": "mysql"}), "oceanbase_mysql")
        self.assertEqual(normalize_engine("mongo"), "mongodb")
        self.assertEqual(ENGINE_CAPABILITIES["oracle"].dialect, "oracle")
        self.assertEqual(ENGINE_CAPABILITIES["mysql"].dialect, "mysql")
        self.assertEqual(ENGINE_CAPABILITIES["oceanbase_oracle"].dialect, "oracle")
        self.assertEqual(ENGINE_CAPABILITIES["oceanbase_mysql"].dialect, "mysql")
        self.assertEqual(ENGINE_CAPABILITIES["dameng"].dialect, "oracle")

    def test_relation_uses_only_injected_hook_and_passes_parser_dialect(self) -> None:
        calls: list[ReadOnlyRequest] = []

        def read_hook(request: ReadOnlyRequest):
            calls.append(request)
            return {"columns": ["value"], "rows": [(1,)]}

        def sql_guard(sql: str, dialect: str) -> SQLDecision:
            return SQLDecision(True, dialect=dialect, sql=sql)

        adapter = get_engine_adapter(
            "oceanbase",
            mode="mysql",
            read_only_hook=read_hook,
            sql_policy=sql_guard,
        )
        result = adapter.execute_readonly("SELECT 1", {"bind": 1}, row_limit=7)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"columns": ["value"], "rows": [(1,)]})
        self.assertEqual(len(calls), 1)
        self.assertEqual(dict(calls[0]), {
            "sql": "SELECT 1",
            "parameters": {"bind": 1},
            "row_limit": 7,
            "dialect": "mysql",
        })

    def test_relation_without_verified_sql_guard_is_fail_closed(self) -> None:
        calls: list[object] = []
        adapter = RelationalEngineAdapter("oracle", lambda request: calls.append(request))
        result = adapter.execute_readonly("SELECT SLEEP(1)")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "READ_GUARD_NOT_READY")
        self.assertEqual(calls, [])

    def test_relation_trusted_guard_blocks_side_effect_functions_before_hook(self) -> None:
        calls: list[object] = []

        def sql_guard(sql: str, dialect: str) -> SQLDecision:
            upper = sql.upper()
            if any(token in upper for token in ("SLEEP", "GET_LOCK", "DBMS_LOCK")):
                return SQLDecision(False, code="POLICY_DENIED", dialect=dialect, sql=sql)
            return SQLDecision(True, dialect=dialect, sql=sql)

        adapter = RelationalEngineAdapter(
            "oracle",
            lambda request: calls.append(request),
            sql_policy=sql_guard,
        )
        for sql in ("SELECT SLEEP(1)", "SELECT GET_LOCK('x', 1)", "SELECT DBMS_LOCK.SLEEP(1) FROM DUAL"):
            result = adapter.execute_readonly(sql)
            self.assertFalse(result.ok, sql)
            self.assertEqual(result.code, "POLICY_DENIED")
        self.assertEqual(calls, [])

    def test_relation_rejects_write_unknown_and_connection_fields_before_hook(self) -> None:
        calls: list[object] = []
        def sql_guard(sql: str, dialect: str) -> SQLDecision:
            return SQLDecision(
                allowed=sql.lstrip().upper().startswith("SELECT"),
                code="POLICY_DENIED",
                dialect=dialect,
                sql=sql,
            )

        adapter = RelationalEngineAdapter(
            "oracle",
            lambda request: calls.append(request),
            sql_policy=sql_guard,
        )
        rejected = (
            adapter.execute_readonly("UPDATE orders SET status = 'x'"),
            adapter.execute_readonly("WITH changed AS (DELETE FROM orders RETURNING id) SELECT * FROM changed"),
            adapter.execute("explain", {"sql": "SELECT 1"}),
            adapter.execute("query_readonly", {"sql": "SELECT 1", "host": "model-supplied"}),
            adapter.execute_readonly("SELECT 1", {"password": "model-supplied"}),
        )
        self.assertTrue(all(not item.ok for item in rejected))
        self.assertEqual(len(calls), 0)

    def test_redis_structured_read_whitelist_and_zero_calls_on_write_or_unknown(self) -> None:
        driver = _FakeRedis()
        adapter = RedisEngineAdapter(driver)
        self.assertTrue(adapter.execute("GET", {"key": "orders:1"}).ok)
        self.assertTrue(adapter.execute("SCAN", {"pattern": "orders:*", "limit": 3}).ok)
        before = len(driver.calls)
        for operation, request in (
            ("SET", {"key": "orders:1", "value": "x"}),
            ("EVAL", {"key": "orders:1"}),
            ("GET", {"key": "orders:1", "password": "secret"}),
        ):
            result = adapter.execute(operation, request)
            self.assertFalse(result.ok, (operation, result))
        self.assertEqual(len(driver.calls), before)
        self.assertEqual(driver.calls[0], ("get", "orders:1"))

    def test_mongo_read_operations_and_recursive_policy_rejections(self) -> None:
        collection = _FakeCollection()
        adapter = MongoEngineAdapter(collections={"orders": collection})
        self.assertTrue(
            adapter.execute(
                "mongo_read",
                {"collection_id": "orders", "operation": "find", "filter": {"status": "open"}},
            ).ok
        )
        self.assertTrue(
            adapter.execute("mongo_read", {"collection_id": "orders", "operation": "count"}).ok
        )
        self.assertTrue(
            adapter.execute(
                "mongo_read",
                {
                    "collection_id": "orders",
                    "operation": "aggregate",
                    "pipeline": [{"$match": {"status": "open"}}, {"$count": "total"}],
                },
            ).ok
        )
        before = len(collection.calls)
        blocked = (
            {"collection_id": "orders", "operation": "remove"},
            {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$out": "archive"}]},
            {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$match": {"x": {"$where": "x"}}}]},
            {"collection_id": "orders", "operation": "find", "filter": {"x": {"$unknown": 1}}},
            {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$unknownStage": {}}]},
            {"collection_id": "orders", "operation": "find", "connection_id": "other"},
        )
        for request in blocked:
            result = adapter.execute("mongo_read", request)
            self.assertFalse(result.ok, request)
        self.assertEqual(len(collection.calls), before)

    def test_mongo_scope_resolver_is_not_called_for_denied_collection(self) -> None:
        calls: list[str] = []

        def resolver(collection_id: str):
            calls.append(collection_id)
            return _FakeCollection()

        adapter = MongoEngineAdapter(
            collection_resolver=resolver,
            allowed_collection_ids=("orders",),
        )
        denied = adapter.execute(
            "mongo_read",
            {"collection_id": "users", "operation": "find"},
        )
        self.assertFalse(denied.ok)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
