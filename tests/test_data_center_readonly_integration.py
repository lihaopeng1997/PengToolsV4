"""Integration boundary tests for the host-owned Agent query tool."""

from __future__ import annotations

import unittest
import json

from tools.data_center.agent import AgentRunner
from tools.data_center.agent_query_tool import AgentQueryTool
from tools.data_center.contracts import (
    AgentStatus,
    AgentTurn,
    CancellationToken,
    ModelDelta,
    RunContext,
    ToolCall,
)
from tools.data_center.query_executor import QueryExecutor
from tools.data_center.result_projection import ResultProjector
from tools.data_center.session_manager import SessionManager
from tools.data_center.sql_policy import SQLPolicy
from tools.data_center.tool_registry import DataCenterToolRegistry


class _FakeDriver:
    def __init__(self, result=None) -> None:
        self.read_calls: list[object] = []
        self.write_calls: list[object] = []
        self.result = result or {"columns": ["count"], "rows": [(12,)]}

    def execute_readonly(self, request):
        self.read_calls.append(request)
        return self.result

    def execute(self, *args, **kwargs):  # pragma: no cover - must never run
        self.write_calls.append((args, kwargs))
        raise AssertionError("generic execute must not be used")

    def close(self) -> None:
        pass


class _Factory:
    def __init__(self, driver: _FakeDriver) -> None:
        self.driver = driver

    def __call__(self, connection_id: str):
        return self.driver


class _FakeModel:
    """Two deterministic model turns for the real AgentRunner boundary."""

    def __init__(self) -> None:
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
        self.requests.append(request)
        if len(self.requests) == 1:
            on_delta(ModelDelta(kind="reasoning_delta", text="先执行只读统计"))
            return AgentTurn(
                tool_calls=(
                    ToolCall(
                        call_id="call-select-1",
                        name="query_readonly",
                        arguments={"sql": "SELECT COUNT(*) AS count FROM orders"},
                    ),
                ),
                finish_reason="tool_calls",
            )
        on_delta(ModelDelta(kind="text_delta", text="订单共有 12 条"))
        return AgentTurn(text="订单共有 12 条", finish_reason="stop")


class DataCenterReadonlyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _FakeDriver()
        self.manager = SessionManager(_Factory(self.driver))
        self.session = self.manager.create_session(
            "connection-1",
            session_id="tab-1",
            dialect="oracle",
        )
        self.executor = QueryExecutor(
            self.manager,
            SQLPolicy(),
            default_timeout=None,
        )
        self.addCleanup(self.executor.shutdown)
        self.context = RunContext(
            run_id="run-1",
            tab_id="tab-1",
            model_config_id="model-1",
            connection_id="connection-1",
            database="demo",
        )
        self.tool = AgentQueryTool(self.manager, self.executor)
        self.registry = DataCenterToolRegistry({"query_readonly": self.tool})

    def test_select_uses_current_tab_session_and_returns_count(self) -> None:
        result = self.tool(
            ToolCall(
                call_id="call-select-1",
                name="query_readonly",
                arguments={"sql": "SELECT COUNT(*) AS count FROM orders"},
            ),
            context=self.context,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.query_id, "call-select-1")
        self.assertEqual(result.data["rows"][0][0], 12)
        self.assertEqual(len(self.driver.read_calls), 1)
        request = self.driver.read_calls[0]
        self.assertEqual(request.query_id, "call-select-1")
        self.assertEqual(request.session_id, "tab-1")
        self.assertEqual(request.generation, self.session.generation)
        self.assertEqual(request.sql, "SELECT COUNT(*) AS count FROM orders")

    def test_delete_is_rejected_by_sql_policy_before_driver(self) -> None:
        result = self.tool(
            ToolCall(
                call_id="call-delete-1",
                name="query_readonly",
                arguments={"sql": "DELETE FROM orders"},
            ),
            context=self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "POLICY_DENIED")
        self.assertEqual(self.driver.read_calls, [])
        self.assertEqual(self.driver.write_calls, [])

    def test_connection_scope_mismatch_is_rejected_before_factory(self) -> None:
        context = RunContext(
            run_id="run-1",
            tab_id="tab-1",
            model_config_id="model-1",
            connection_id="another-connection",
            database="demo",
        )

        result = self.tool(
            ToolCall(
                call_id="call-scope-1",
                name="query_readonly",
                arguments={"sql": "SELECT 1 FROM dual"},
            ),
            context=context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "SCOPE_DENIED")
        self.assertEqual(self.driver.read_calls, [])
        self.assertEqual(self.driver.write_calls, [])

    def test_tool_is_explicitly_host_owned_and_verified_readonly(self) -> None:
        self.assertTrue(self.tool.host_only)
        self.assertTrue(self.tool.verified_readonly)

    def test_agent_runner_grounds_next_model_turn_in_verified_readonly_result(self) -> None:
        model = _FakeModel()
        result = AgentRunner(model, self.registry).run(self.context, "查询订单数量")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.final_text, "订单共有 12 条")
        self.assertEqual(len(model.requests), 2)
        tool_message = model.requests[1].messages[-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call-select-1")
        self.assertIn('"query_id":"call-select-1"', tool_message["content"])
        self.assertIn('"rows":[[{"type":"integer","value":12}]]', tool_message["content"])
        self.assertEqual(len(self.driver.read_calls), 1)
        self.assertEqual(self.driver.write_calls, [])

    def test_model_only_sees_redacted_and_bounded_query_projection(self) -> None:
        self.driver.result = {
            "columns": ["id", "api_token", "description"],
            "rows": [
                (index, f"secret-{index}", "长文本" * 300)
                for index in range(120)
            ],
        }

        model = _FakeModel()
        result = AgentRunner(model, self.registry).run(self.context, "查询订单明细")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        model_payload = json.loads(model.requests[1].messages[-1]["content"])
        projected = model_payload["data"]
        self.assertTrue(model_payload["truncated"])
        self.assertLessEqual(len(projected["rows"]), 50)
        self.assertEqual(projected["rows"][0][1], {"type": "redacted"})
        self.assertEqual(projected["rows"][0][2]["truncated"], True)
        self.assertNotIn("secret-", model.requests[1].messages[-1]["content"])
        self.assertLessEqual(
            len(model.requests[1].messages[-1]["content"].encode("utf-8")),
            12 * 1024,
        )

    def test_projection_budget_accumulates_per_run_and_resets_for_new_run(self) -> None:
        self.driver.result = {
            "columns": ["id", "description"],
            "rows": [(index, "x" * 800) for index in range(80)],
        }
        registry = DataCenterToolRegistry(
            {"query_readonly": self.tool},
            projector=ResultProjector(
                max_rows=50,
                max_result_bytes=12 * 1024,
                max_cumulative_bytes=12 * 1024,
            ),
        )

        first = registry.execute(
            ToolCall("budget-1", "query_readonly", {"sql": "SELECT id FROM orders"}),
            context=self.context,
            cancellation=CancellationToken(),
        )
        second = registry.execute(
            ToolCall("budget-2", "query_readonly", {"sql": "SELECT id FROM orders"}),
            context=self.context,
            cancellation=CancellationToken(),
        )
        new_run_context = RunContext(
            run_id="run-2",
            tab_id="tab-1",
            model_config_id="model-1",
            connection_id="connection-1",
            database="demo",
        )
        new_run = registry.execute(
            ToolCall("budget-3", "query_readonly", {"sql": "SELECT id FROM orders"}),
            context=new_run_context,
            cancellation=CancellationToken(),
        )

        self.assertTrue(first.ok)
        self.assertTrue(first.truncated)
        self.assertTrue(second.ok)
        self.assertTrue(second.truncated)
        self.assertEqual(second.data["rows"], [])
        self.assertTrue(new_run.ok)
        self.assertTrue(new_run.data["rows"])

        registry.release_run(self.context)
        released = registry.execute(
            ToolCall("budget-4", "query_readonly", {"sql": "SELECT id FROM orders"}),
            context=self.context,
            cancellation=CancellationToken(),
        )
        self.assertTrue(released.ok)
        self.assertTrue(released.data["rows"])

        registry.release_run(self.context)
        via_legacy_model_helper = registry.execute_for_model(
            ToolCall("budget-5", "query_readonly", {"sql": "SELECT id FROM orders"}),
            context=self.context,
            cancellation=CancellationToken(),
        )
        self.assertEqual(
            via_legacy_model_helper.data["rows"][0][1],
            {"type": "string", "value": "x" * 512, "truncated": True},
        )

    def test_registry_delete_and_wrong_context_do_zero_driver_io(self) -> None:
        delete = self.registry.execute(
            ToolCall(
                call_id="call-delete-registry-1",
                name="query_readonly",
                arguments={"sql": "DELETE FROM orders"},
            ),
            context=self.context,
            cancellation=CancellationToken(),
        )
        wrong_context = RunContext(
            run_id="run-1",
            tab_id="tab-1",
            model_config_id="model-1",
            connection_id="another-connection",
            database="demo",
        )
        wrong_scope = self.registry.execute(
            ToolCall(
                call_id="call-scope-registry-1",
                name="query_readonly",
                arguments={"sql": "SELECT 1 FROM dual"},
            ),
            context=wrong_context,
            cancellation=CancellationToken(),
        )

        self.assertFalse(delete.ok)
        self.assertEqual(delete.code, "POLICY_DENIED")
        self.assertFalse(wrong_scope.ok)
        self.assertEqual(wrong_scope.code, "SCOPE_DENIED")
        self.assertEqual(self.driver.read_calls, [])
        self.assertEqual(self.driver.write_calls, [])


if __name__ == "__main__":
    unittest.main()
