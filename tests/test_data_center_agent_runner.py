"""In-memory DC-01 runner tests.

The fakes below deliberately expose only the injected model/tool boundaries;
no network, database, Qt, or user configuration is touched.
"""

from __future__ import annotations

import threading
import time
import unittest

from tools.data_center.agent import AgentRunner
from tools.data_center.budget import Budget, BudgetLimits
from tools.data_center.contracts import (
    AgentEventType,
    AgentStatus,
    AgentTurn,
    CancellationToken,
    ModelDelta,
    RunContext,
    ToolCall,
    ToolResult,
)
from tools.data_center.tool_registry import DataCenterToolRegistry


def _context() -> RunContext:
    return RunContext(
        run_id="run-test",
        tab_id="tab-test",
        model_config_id="model-test",
        connection_id="conn-test",
        database="demo",
        schema_allowlist=("public",),
        profile_revision="rev-1",
        policy_version="policy-1",
    )


class _FakeRegistry:
    def __init__(self, results: dict[str, ToolResult] | None = None) -> None:
        self.calls: list[ToolCall] = []
        self.results = results or {}
        self.started = threading.Event()
        self.release = threading.Event()
        self.wait_for_release = False

    def definitions(self):
        return tuple({"name": name, "description": "test"} for name in (
            "search_objects",
            "describe_object",
            "query_readonly",
            "request_clarification",
        ))

    def execute(self, call, *, context, cancellation):  # noqa: ARG002
        self.calls.append(call)
        self.started.set()
        if self.wait_for_release:
            self.release.wait(2.0)
        return self.results.get(call.name, ToolResult(ok=True, data={"ok": True}))


class _ScriptedModel:
    def __init__(self, script):
        self.script = script
        self.requests = []
        self.callbacks = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        self.callbacks.append(on_delta)
        return self.script(len(self.requests) - 1, request, cancellation, on_delta)


class DataCenterAgentRunnerTests(unittest.TestCase):
    def test_multi_turn_tool_results_are_paired_and_final_text_is_grounded(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            if index == 0:
                on_delta(ModelDelta(kind="reasoning_delta", text="先查对象"))
                return AgentTurn(
                    tool_calls=(ToolCall("call-search", "search_objects", {"keyword": "订单"}),),
                    finish_reason="tool_calls",
                )
            if index == 1:
                self.assertEqual(request.messages[-2]["role"], "assistant")
                self.assertEqual(request.messages[-1]["role"], "tool")
                self.assertEqual(request.messages[-1]["tool_call_id"], "call-search")
                return AgentTurn(
                    tool_calls=(ToolCall("call-describe", "describe_object", {"object_id": "orders"}),),
                    finish_reason="tool_calls",
                )
            if index == 2:
                self.assertEqual(request.messages[-1]["tool_call_id"], "call-describe")
                return AgentTurn(
                    tool_calls=(ToolCall("call-query", "query_readonly", {"sql": "SELECT COUNT(*) FROM orders"}),),
                    finish_reason="tool_calls",
                )
            self.assertEqual(request.messages[-1]["tool_call_id"], "call-query")
            on_delta(ModelDelta(kind="reasoning_delta", text="结果来自查询"))
            on_delta(ModelDelta(kind="text_delta", text="订单共有 12 条"))
            return AgentTurn(text="订单共有 12 条", finish_reason="stop", usage={"completion_tokens": 6})

        registry = _FakeRegistry(
            {
                "search_objects": ToolResult(ok=True, data=[{"id": "orders"}], evidence_id="ev-search"),
                "describe_object": ToolResult(ok=True, data={"columns": ["id"]}, evidence_id="ev-desc"),
                "query_readonly": ToolResult(
                    ok=True,
                    data={"count": 12},
                    evidence_id="ev-query",
                    query_id="query-1",
                ),
            }
        )
        model = _ScriptedModel(script)
        result = AgentRunner(model, registry).run(_context(), "查询订单数量")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.final_text, "订单共有 12 条")
        self.assertEqual([call.name for call in registry.calls], [
            "search_objects",
            "describe_object",
            "query_readonly",
        ])
        self.assertEqual(len(model.requests), 4)
        text_events = [event for event in result.events if event.event_type == AgentEventType.MODEL_TEXT_DELTA.value]
        self.assertEqual([event.payload["text"] for event in text_events], ["订单共有 12 条"])
        self.assertEqual(
            [event.sequence for event in result.events],
            list(range(1, len(result.events) + 1)),
        )

        # Every assistant tool call has its exact role=tool response directly
        # after it, retaining the ID needed by the next model request.
        for index, message in enumerate(result.messages):
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            for offset, call in enumerate(message["tool_calls"], 1):
                tool_message = result.messages[index + offset]
                self.assertEqual(tool_message["role"], "tool")
                self.assertEqual(tool_message["tool_call_id"], call["id"])

    def test_same_call_id_reuses_completed_result_without_second_execution(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            if index < 2:
                return AgentTurn(
                    tool_calls=(ToolCall("same-id", "search_objects", {"keyword": "订单"}),),
                    finish_reason="tool_calls",
                )
            return AgentTurn(text="已复用查询结果", finish_reason="stop")

        registry = _FakeRegistry({"search_objects": ToolResult(ok=True, data=["orders"], evidence_id="ev-1")})
        model = _ScriptedModel(script)
        result = AgentRunner(model, registry).run(_context(), "找订单对象")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(registry.calls), 1)
        reused = [
            event for event in result.events
            if event.event_type == AgentEventType.TOOL_RESULT.value and event.payload.get("reused")
        ]
        self.assertEqual(len(reused), 1)
        self.assertEqual(len(result.tool_results), 1)

    def test_same_call_id_with_different_arguments_is_protocol_failure(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            if index == 0:
                return AgentTurn(
                    tool_calls=(ToolCall("same-id", "search_objects", {"keyword": "订单"}),),
                    finish_reason="tool_calls",
                )
            return AgentTurn(
                tool_calls=(ToolCall("same-id", "search_objects", {"keyword": "客户"}),),
                finish_reason="tool_calls",
            )

        registry = _FakeRegistry({"search_objects": ToolResult(ok=True, data=["orders"])})
        result = AgentRunner(_ScriptedModel(script), registry).run(_context(), "查对象")

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.error, "TOOL_CALL_CONFLICT")
        self.assertEqual(len(registry.calls), 1)
        self.assertNotIn("客户", " ".join(str(message) for message in result.messages))

    def test_unknown_tool_is_blocked_before_executor(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            return AgentTurn(
                tool_calls=(ToolCall("bad", "exec_shell", {"command": "whoami"}),),
                finish_reason="tool_calls",
            )

        registry = _FakeRegistry()
        result = AgentRunner(_ScriptedModel(script), registry).run(_context(), "执行命令")
        self.assertEqual(result.status, AgentStatus.POLICY_BLOCKED)
        self.assertEqual(result.error, "POLICY_DENIED")
        self.assertEqual(registry.calls, [])

    def test_request_clarification_pauses_without_invoking_executor(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            return AgentTurn(
                tool_calls=(ToolCall(
                    "clarify",
                    "request_clarification",
                    {"question": "请选择订单表", "candidate_ids": ["orders", "order_items"]},
                ),),
                finish_reason="tool_calls",
            )

        registry = _FakeRegistry()
        result = AgentRunner(_ScriptedModel(script), registry).run(_context(), "查订单")
        self.assertEqual(result.status, AgentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.final_text, "请选择订单表")
        self.assertEqual(registry.calls, [])
        clarification = [
            event for event in result.events
            if event.event_type == AgentEventType.NEEDS_CLARIFICATION.value
        ]
        self.assertEqual(clarification[0].payload["candidate_ids"], ["orders", "order_items"])

    def test_dc01_registry_blocks_query_until_read_guard_and_model_gets_evidence(self) -> None:
        class HostExecutor:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self, *args, **kwargs):  # noqa: ARG002
                self.calls += 1
                return {"ok": True, "data": {"count": 999}}

        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            if index == 0:
                return AgentTurn(
                    tool_calls=(ToolCall("guarded", "query_readonly", {"sql": "SELECT 12"}),),
                    finish_reason="tool_calls",
                )
            self.assertIn("READ_GUARD_NOT_READY", request.messages[-1]["content"])
            return AgentTurn(text="查询未执行：只读门禁尚未就绪", finish_reason="stop")

        host = HostExecutor()
        registry = DataCenterToolRegistry(host)
        result = AgentRunner(_ScriptedModel(script), registry).run(_context(), "查询数据")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(host.calls, 0)
        self.assertEqual(result.tool_results[0].code, "READ_GUARD_NOT_READY")
        self.assertIn("未执行", result.final_text)

    def test_tool_exception_is_failure_without_exception_text_leak(self) -> None:
        class ExplodingRegistry(_FakeRegistry):
            def execute(self, call, *, context, cancellation):  # noqa: ARG002
                self.calls.append(call)
                raise RuntimeError("password=super-secret")

        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            if index == 0:
                return AgentTurn(
                    tool_calls=(ToolCall("query", "query_readonly", {"sql": "select 1"}),),
                    finish_reason="tool_calls",
                )
            return AgentTurn(text="查询失败", finish_reason="stop")

        registry = ExplodingRegistry()
        result = AgentRunner(_ScriptedModel(script), registry).run(_context(), "查数据")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.tool_results[0].code, "TOOL_FAILED")
        self.assertNotIn("super-secret", str(result))
        self.assertTrue(any(event.payload.get("error") == "TOOL_EXECUTION_FAILED:RuntimeError" for event in result.events))

    def test_cancelled_run_suppresses_late_tool_result_and_further_model_turn(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            return AgentTurn(
                tool_calls=(ToolCall("slow", "query_readonly", {"sql": "select 1"}),),
                finish_reason="tool_calls",
            )

        registry = _FakeRegistry({"query_readonly": ToolResult(ok=True, data={"count": 1}, query_id="q-1")})
        registry.wait_for_release = True
        model = _ScriptedModel(script)
        token = CancellationToken()
        holder = {}

        def run() -> None:
            holder["result"] = AgentRunner(model, registry).run(_context(), "慢查询", cancellation=token)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(registry.started.wait(1.0))
        token.cancel("user_stop")
        registry.release.set()
        worker.join(2.0)

        result = holder["result"]
        self.assertEqual(result.status, AgentStatus.CANCELLED)
        self.assertEqual(len(model.requests), 1)
        self.assertFalse(any(event.event_type == AgentEventType.TOOL_RESULT.value for event in result.events))
        self.assertEqual(result.tool_results, ())

    def test_late_model_delta_after_completed_run_is_ignored(self) -> None:
        callbacks = []

        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            callbacks.append(on_delta)
            return AgentTurn(text="完成", finish_reason="stop")

        model = _ScriptedModel(script)
        result = AgentRunner(model, _FakeRegistry()).run(_context(), "完成任务")
        before = len(result.events)
        callbacks[0](ModelDelta(kind="text_delta", text="迟到内容"))
        self.assertEqual(len(result.events), before)
        self.assertNotIn("迟到内容", result.final_text)

    def test_usage_from_stream_and_turn_is_accounted_once(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            on_delta(ModelDelta(kind="usage", usage={"completion_tokens": 3}))
            on_delta(ModelDelta(kind="text_delta", text="答复"))
            return AgentTurn(text="答复", finish_reason="stop", usage={"completion_tokens": 3})

        budget = Budget(
            BudgetLimits(
                max_model_turns=2,
                max_tool_calls=2,
                max_queries=2,
                max_duration_s=None,
                max_bytes=None,
                max_input_chars=None,
                max_output_tokens=3,
            )
        )
        result = AgentRunner(_ScriptedModel(script), _FakeRegistry(), budget=budget).run(_context(), "回答")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.budget["output_tokens"], 3)

    def test_budget_stops_before_next_model_request_with_partial_evidence(self) -> None:
        def script(index, request, cancellation, on_delta):  # noqa: ARG001
            return AgentTurn(
                tool_calls=(ToolCall("one", "query_readonly", {"sql": "select 1"}),),
                finish_reason="tool_calls",
            )

        registry = _FakeRegistry({"query_readonly": ToolResult(ok=True, data={"count": 12}, query_id="q-1")})
        model = _ScriptedModel(script)
        budget = Budget(
            BudgetLimits(
                max_model_turns=1,
                max_tool_calls=2,
                max_queries=2,
                max_duration_s=None,
                max_bytes=None,
                max_input_chars=None,
                max_output_tokens=None,
            )
        )
        result = AgentRunner(model, registry, budget=budget).run(_context(), "统计")
        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result.tool_results[0].query_id, "q-1")
        self.assertEqual(result.error, "model_turns")


if __name__ == "__main__":
    unittest.main()
