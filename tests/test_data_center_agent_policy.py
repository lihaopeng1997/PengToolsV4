# -*- coding: utf-8 -*-
"""DC-01 policy boundary tests.

These tests use only an in-memory ``RunContext`` and a call counter.  Policy
validation has no executor or database dependency; the counter makes the
zero-I/O property explicit for rejected model calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
import unittest

from tools.data_center.contracts import CancellationToken, RunContext, ToolCall, ToolResult
from tools.data_center.policy import (
    DataCenterPolicy,
    decide_tool_call,
    validate_tool_call,
)
from tools.data_center.result_projection import ProjectionBudget, ResultProjector
from tools.data_center.tool_registry import DataCenterToolRegistry


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self.received = []

    def execute(self, tool_name, arguments, *, context, cancellation):
        self.calls += 1
        self.received.append((tool_name, dict(arguments), context, cancellation))
        return ToolResult(ok=True, data={"columns": ["value"], "rows": [(1,)]}, query_id="q-1")


def _context() -> RunContext:
    return RunContext(
        run_id="run-1",
        tab_id="tab-1",
        model_config_id="model-1",
        connection_id="conn-1",
        database="demo",
        schema_allowlist=("app",),
        profile_revision="rev-1",
        intent="query",
        policy_version="dc-01",
    )


class DataCenterAgentPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DataCenterPolicy()
        self.context = _context()
        self.executor = _FakeExecutor()

    def _validate(self, name: str, arguments: dict) -> object:
        return self.policy.validate_tool_call(
            ToolCall(call_id="call-1", name=name, arguments=arguments), self.context
        )

    def test_all_six_tools_are_closed_and_query_is_only_shape_checked_in_dc01(self) -> None:
        calls = (
            ("search_objects", {"keyword": "订单"}),
            ("describe_object", {"object_id": "app.orders"}),
            ("query_readonly", {"sql": "DELETE FROM orders"}),
            ("redis_read", {"operation": "GET", "key": "orders:1"}),
            ("mongo_read", {"collection_id": "orders", "operation": "find"}),
            ("request_clarification", {"question": "请选择对象"}),
        )
        for name, arguments in calls:
            result = self._validate(name, arguments)
            self.assertTrue(result.ok, (name, result))

    def test_unknown_tool_and_shell_tool_are_rejected_without_executor_call(self) -> None:
        for name in ("exec_shell", "read_file", "delete_rows", "explain"):
            result = self._validate(name, {})
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "POLICY_DENIED")
        self.assertEqual(self.executor.calls, 0)

    def test_connection_host_file_and_command_overrides_are_rejected(self) -> None:
        override_cases = (
            {"sql": "SELECT 1", "connection_id": "other"},
            {"sql": "SELECT 1", "host": "other"},
            {"sql": "SELECT 1", "file_path": "C:/secret.txt"},
            {"sql": "SELECT 1", "command": "whoami"},
            {"sql": "SELECT 1", "shell": "echo pwned"},
        )
        for arguments in override_cases:
            result = self._validate("query_readonly", arguments)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "SCOPE_DENIED")
        self.assertEqual(self.executor.calls, 0)

    def test_empty_query_is_rejected_but_write_sql_is_not_parsed_in_dc01(self) -> None:
        for sql in ("", "   ", "\n\t"):
            result = self._validate("query_readonly", {"sql": sql})
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "ARGUMENT_INVALID")
        # The write rejection belongs to the DC-02 SQL policy.  DC-01 only
        # proves that a non-empty SQL value is structurally present.
        result = self._validate("query_readonly", {"sql": "DELETE FROM orders"})
        self.assertTrue(result.ok)

    def test_bad_arguments_are_rejected_and_never_reach_a_fake_executor(self) -> None:
        bad_calls = (
            ("search_objects", {"keyword": 12}),
            ("describe_object", {}),
            ("redis_read", {"operation": "EVAL", "key": "x"}),
            ("mongo_read", {"collection_id": "orders", "operation": "remove"}),
            ("query_readonly", {"sql": "SELECT 1", "row_limit": 101}),
        )
        for name, arguments in bad_calls:
            result = self._validate(name, arguments)
            self.assertFalse(result.ok, (name, result))
        self.assertEqual(self.executor.calls, 0)

    def test_authorized_object_and_collection_scope_is_host_injected(self) -> None:
        policy = DataCenterPolicy(
            allowed_object_ids=("app.orders",),
            allowed_collection_ids=("orders",),
        )
        self.assertTrue(
            policy.validate_tool_call(
                ToolCall("call-1", "describe_object", {"object_id": "app.orders"}), self.context
            ).ok
        )
        self.assertFalse(
            policy.validate_tool_call(
                ToolCall("call-2", "describe_object", {"object_id": "app.users"}), self.context
            ).ok
        )
        self.assertFalse(
            policy.validate_tool_call(
                ToolCall("call-3", "mongo_read", {"collection_id": "users", "operation": "find"}),
                self.context,
            ).ok
        )

    def test_module_helpers_and_explicit_decision_share_the_same_boundary(self) -> None:
        call = ToolCall("call-1", "query_readonly", {"sql": "SELECT 1"})
        self.assertTrue(validate_tool_call(call, self.context).ok)
        decision = decide_tool_call(call, self.context)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.result.ok)
        self.assertEqual(decision.tool_name, "query_readonly")

    def test_registry_dispatches_only_validated_calls_and_keeps_host_context(self) -> None:
        registry = DataCenterToolRegistry(self.executor, policy=self.policy)
        rejected = registry.execute(
            ToolCall("bad-1", "query_readonly", {"sql": "SELECT 1", "connection_id": "other"}),
            context=self.context,
            cancellation=CancellationToken(),
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(self.executor.calls, 0)

        # DC-01 has no SQL AST or engine read guard, so every SQL shape is
        # fail-closed at the registry boundary, including a harmless SELECT.
        for sql in ("SELECT 1", "DELETE FROM orders", "UPDATE orders SET x = 1", "CREATE TABLE x (id INT)"):
            accepted = registry.execute(
                ToolCall("good-1", "query_readonly", {"sql": sql, "row_limit": 2}),
                context=self.context,
                cancellation=CancellationToken(),
            )
            self.assertFalse(accepted.ok)
            self.assertEqual(accepted.code, "READ_GUARD_NOT_READY")
        self.assertEqual(self.executor.calls, 0)

        # A non-SQL read tool can still reach the injected fake executor; the
        # host context is passed separately and cannot be replaced by model
        # arguments.
        accepted = registry.execute(
            ToolCall("good-2", "redis_read", {"operation": "GET", "key": "orders:1"}),
            context=self.context,
            cancellation=CancellationToken(),
        )
        self.assertTrue(accepted.ok)
        self.assertEqual(self.executor.calls, 1)
        name, arguments, context, _cancellation = self.executor.received[0]
        self.assertEqual(name, "redis_read")
        self.assertEqual(arguments, {"operation": "GET", "key": "orders:1"})
        self.assertIs(context, self.context)

    def test_write_like_redis_and_mongo_requests_never_reach_executor(self) -> None:
        registry = DataCenterToolRegistry(self.executor, policy=self.policy)
        for call in (
            ToolCall("redis-1", "redis_read", {"operation": "EVAL", "key": "orders:1"}),
            ToolCall(
                "mongo-1",
                "mongo_read",
                {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$out": "archive"}]},
            ),
            ToolCall(
                "mongo-2",
                "mongo_read",
                {"collection_id": "orders", "operation": "aggregate", "pipeline": [{"$match": {"x": {"$merge": "archive"}}}]},
            ),
        ):
            result = registry.execute(call, context=self.context, cancellation=CancellationToken())
            self.assertFalse(result.ok)
        self.assertEqual(self.executor.calls, 0)

    def test_nested_unknown_mongo_expression_is_closed_and_does_not_execute(self) -> None:
        registry = DataCenterToolRegistry(self.executor, policy=self.policy)
        result = registry.execute(
            ToolCall(
                "mongo-3",
                "mongo_read",
                {
                    "collection_id": "orders",
                    "operation": "aggregate",
                    "pipeline": [{"$match": {"amount": {"$unknownExpression": 1}}}],
                },
            ),
            context=self.context,
            cancellation=CancellationToken(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "POLICY_DENIED")
        self.assertEqual(self.executor.calls, 0)

    def test_registry_definitions_are_the_same_six_closed_tools(self) -> None:
        registry = DataCenterToolRegistry(self.executor)
        definitions = registry.definitions()
        names = {item["function"]["name"] for item in definitions}
        self.assertEqual(
            names,
            {
                "search_objects",
                "describe_object",
                "query_readonly",
                "redis_read",
                "mongo_read",
                "request_clarification",
            },
        )
        forbidden = {"host", "connection_id", "password", "file_path", "path", "command", "shell"}
        for item in definitions:
            properties = set(item["function"]["parameters"]["properties"])
            self.assertTrue(properties.isdisjoint(forbidden), item)
            self.assertFalse(item["function"]["parameters"]["additionalProperties"])

    def test_projection_redacts_credentials_and_preserves_native_value_kinds(self) -> None:
        result = ToolResult(
            ok=True,
            query_id="query-1",
            evidence_id="evidence-1",
            data={
                "columns": ["id", "api_token", "note", "amount", "created", "blob"],
                "rows": [
                    (1, "do-not-send", None, Decimal("12.30"), date(2026, 9, 15), b"abc"),
                    (2, "still-secret", "", Decimal("0.00"), date(2026, 9, 16), b""),
                ],
            },
        )
        projected = ResultProjector().project(result)
        encoded = json.dumps(projected.as_dict(), ensure_ascii=False, sort_keys=True)
        self.assertNotIn("do-not-send", encoded)
        self.assertNotIn("still-secret", encoded)
        rows = projected.data["rows"]
        self.assertEqual(rows[0][0], {"type": "integer", "value": 1})
        self.assertEqual(rows[0][1], {"type": "redacted"})
        self.assertEqual(rows[0][2], {"type": "null"})
        self.assertEqual(rows[1][2], {"type": "string", "value": ""})
        self.assertEqual(rows[0][3], {"type": "decimal", "value": "12.30"})
        self.assertEqual(rows[0][4], {"type": "date", "value": "2026-09-15"})
        self.assertEqual(rows[0][5], {"type": "bytes", "size": 3})

    def test_projection_honors_host_marked_sensitive_columns(self) -> None:
        projected = ResultProjector().project(
            ToolResult(
                ok=True,
                data={
                    "columns": [{"name": "business_value", "sensitive": True}],
                    "rows": [("private",)],
                },
            )
        )
        self.assertEqual(projected.data["rows"], [[{"type": "redacted"}]])

    def test_projection_obeys_row_cell_result_and_cumulative_byte_budgets(self) -> None:
        result = ToolResult(
            ok=True,
            query_id="query-2",
            data={
                "columns": ["id", "description"],
                "rows": [(index, "长文本" * 400) for index in range(201)],
            },
        )
        budget = ProjectionBudget(max_bytes=48 * 1024)
        projector = ResultProjector(max_rows=100, max_cell_chars=512, max_result_bytes=12 * 1024)
        projected = projector.project(result, budget=budget)
        self.assertLessEqual(len(projected.data["rows"]), 100)
        self.assertTrue(projected.truncated)
        self.assertTrue(all(len(cell["value"]) <= 512 for cell in projected.data["rows"][0][1:]))
        self.assertLessEqual(projected.bytes_used, 12 * 1024)
        self.assertLessEqual(budget.used_bytes, 48 * 1024)

        # A second result shares the same run budget and can only consume the
        # remaining bytes; it must never silently exceed the cumulative cap.
        second = projector.project(result, budget=budget)
        self.assertLessEqual(budget.used_bytes, 48 * 1024)
        self.assertLessEqual(second.bytes_used, budget.max_bytes)


if __name__ == "__main__":
    unittest.main()
