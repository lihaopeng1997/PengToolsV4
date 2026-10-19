"""Focused evidence and query-budget regressions for the data-center Agent.

The model and tool handlers are entirely in memory.  Each test drives the
production ``AgentRunner`` through the closed ``DataCenterToolRegistry`` so
verification metadata and budget accounting are observed at the real boundary.
"""

from __future__ import annotations

import unittest

from tools.data_center.agent import AgentRunner
from tools.data_center.budget import Budget, BudgetLimits
from tools.data_center.contracts import AgentEventType, AgentStatus, AgentTurn, RunContext, ToolCall, ToolResult
from tools.data_center.tool_registry import DataCenterToolRegistry


def _context(run_id: str = "evidence-run") -> RunContext:
    return RunContext(
        run_id=run_id,
        tab_id="evidence-tab",
        model_config_id="evidence-model",
        connection_id="evidence-connection",
        database="demo",
        schema_allowlist=("public",),
        profile_revision="rev-1",
        policy_version="policy-1",
    )


class _ScriptedModel:
    def __init__(self, *turns: AgentTurn) -> None:
        self.turns = list(turns)
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("the scripted model made an unexpected request")
        return self.turns.pop(0)


class _ReadOnlyHandler:
    """Callable fake with the host-owned markers required for SQL execution."""

    host_only = True
    verified_readonly = True

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[ToolCall] = []

    def __call__(self, call, *, context, cancellation):  # noqa: ARG002
        self.calls.append(call)
        response = self.responses[call.name]
        if isinstance(response, list):
            if not response:
                raise AssertionError(f"no scripted result remains for {call.name}")
            return response.pop(0)
        return response


def _final_event(result):
    return next(event for event in result.events if event.event_type == AgentEventType.FINAL.value)


def _registry(handler: _ReadOnlyHandler, *names: str) -> DataCenterToolRegistry:
    return DataCenterToolRegistry({name: handler for name in names})


class DataCenterAgentEvidenceTests(unittest.TestCase):
    def test_metadata_only_final_is_unverified(self) -> None:
        handler = _ReadOnlyHandler(
            {
                "search_objects": ToolResult(
                    ok=True,
                    data={"objects": [{"name": "orders"}]},
                    evidence_id="metadata-evidence",
                    query_id="metadata-query",
                )
            }
        )
        model = _ScriptedModel(
            AgentTurn(
                tool_calls=(ToolCall("metadata-call", "search_objects", {"keyword": "orders"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(text="找到订单对象", finish_reason="stop"),
        )

        result = AgentRunner(model, _registry(handler, "search_objects")).run(_context(), "找订单对象")

        final = _final_event(result)
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertFalse(final.payload["verified"])
        self.assertEqual(final.payload["verification_status"], "unverified")
        self.assertEqual(final.payload["query_ids"], [])
        self.assertEqual(final.payload["evidence_ids"], ["metadata-evidence"])

    def test_failed_query_with_query_id_is_unverified(self) -> None:
        handler = _ReadOnlyHandler(
            {
                "query_readonly": ToolResult(
                    ok=False,
                    code="QUERY_FAILED",
                    data={"message": "query failed"},
                    evidence_id="failed-evidence",
                    query_id="query-failed",
                )
            }
        )
        model = _ScriptedModel(
            AgentTurn(
                tool_calls=(ToolCall("failed-call", "query_readonly", {"sql": "SELECT 1"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(text="查询失败", finish_reason="stop"),
        )

        result = AgentRunner(model, _registry(handler, "query_readonly")).run(_context(), "查询数据")

        final = _final_event(result)
        self.assertFalse(final.payload["verified"])
        self.assertEqual(final.payload["query_ids"], [])
        self.assertEqual(final.payload["verification_status"], "unverified")
        self.assertEqual(handler.calls, [ToolCall("failed-call", "query_readonly", {"sql": "SELECT 1"})])

    def test_successful_data_query_is_verified(self) -> None:
        handler = _ReadOnlyHandler(
            {
                "query_readonly": ToolResult(
                    ok=True,
                    data={"value": 12},
                    evidence_id="query-evidence",
                    query_id="query-success",
                )
            }
        )
        model = _ScriptedModel(
            AgentTurn(
                tool_calls=(ToolCall("success-call", "query_readonly", {"sql": "SELECT 12"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(text="结果为 12", finish_reason="stop"),
        )

        result = AgentRunner(model, _registry(handler, "query_readonly")).run(_context(), "查询结果")

        final = _final_event(result)
        self.assertTrue(final.payload["verified"])
        self.assertEqual(final.payload["verification_status"], "verified")
        self.assertEqual(final.payload["query_ids"], ["query-success"])

    def test_mixed_evidence_query_ids_exclude_failed_and_metadata(self) -> None:
        handler = _ReadOnlyHandler(
            {
                "search_objects": ToolResult(ok=True, data={"objects": []}, evidence_id="metadata-evidence", query_id="metadata-query"),
                "query_readonly": [
                    ToolResult(ok=False, code="QUERY_FAILED", data=None, evidence_id="failed-evidence", query_id="query-failed"),
                    ToolResult(ok=True, data={"value": 12}, evidence_id="success-evidence", query_id="query-success"),
                ],
            }
        )
        model = _ScriptedModel(
            AgentTurn(
                tool_calls=(ToolCall("metadata-call", "search_objects", {"keyword": "orders"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(
                tool_calls=(ToolCall("failed-call", "query_readonly", {"sql": "SELECT broken"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(
                tool_calls=(ToolCall("success-call", "query_readonly", {"sql": "SELECT 12"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(text="结果已核验", finish_reason="stop"),
        )

        result = AgentRunner(model, _registry(handler, "search_objects", "query_readonly")).run(_context(), "核验结果")

        final = _final_event(result)
        self.assertTrue(final.payload["verified"])
        self.assertEqual(final.payload["query_ids"], ["query-success"])
        self.assertEqual(
            final.payload["evidence_ids"],
            ["metadata-evidence", "failed-evidence", "success-evidence"],
        )

    def test_duplicate_call_id_executes_once_and_consumes_one_query_budget(self) -> None:
        call = ToolCall("duplicate-query", "query_readonly", {"sql": "SELECT 1"})
        handler = _ReadOnlyHandler(
            {"query_readonly": ToolResult(ok=True, data={"value": 1}, query_id="query-once")}
        )
        model = _ScriptedModel(
            AgentTurn(tool_calls=(call,), finish_reason="tool_calls"),
            AgentTurn(tool_calls=(call,), finish_reason="tool_calls"),
            AgentTurn(text="只执行一次", finish_reason="stop"),
        )
        budget = Budget(
            BudgetLimits(
                max_model_turns=4,
                max_tool_calls=4,
                max_queries=2,
                max_duration_s=None,
                max_bytes=None,
                max_input_chars=None,
                max_output_tokens=None,
            )
        )

        result = AgentRunner(model, _registry(handler, "query_readonly"), budget=budget).run(_context(), "执行查询")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(handler.calls, [call])
        self.assertEqual(result.budget["queries"], 1)
        self.assertEqual(_final_event(result).payload["query_ids"], ["query-once"])

    def test_metadata_calls_share_query_budget_and_block_excess_executor_call(self) -> None:
        handler = _ReadOnlyHandler(
            {
                "search_objects": ToolResult(ok=True, data={"objects": []}),
                "describe_object": ToolResult(ok=True, data={"name": "orders"}),
                "query_readonly": ToolResult(ok=True, data={"value": 1}, query_id="query-never-runs"),
            }
        )
        model = _ScriptedModel(
            AgentTurn(
                tool_calls=(ToolCall("search-call", "search_objects", {"keyword": "orders"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(
                tool_calls=(ToolCall("describe-call", "describe_object", {"object_id": "orders"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(
                tool_calls=(ToolCall("query-call", "query_readonly", {"sql": "SELECT 1"}),),
                finish_reason="tool_calls",
            ),
            AgentTurn(text="不应到达", finish_reason="stop"),
        )
        budget = Budget(
            BudgetLimits(
                max_model_turns=6,
                max_tool_calls=6,
                max_queries=2,
                max_duration_s=None,
                max_bytes=None,
                max_input_chars=None,
                max_output_tokens=None,
            )
        )

        result = AgentRunner(
            model,
            _registry(handler, "search_objects", "describe_object", "query_readonly"),
            budget=budget,
        ).run(_context(), "先查元数据再查询")

        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error, "queries")
        self.assertEqual(result.budget["queries"], 2)
        self.assertEqual([item.name for item in handler.calls], ["search_objects", "describe_object"])
        self.assertEqual(len(model.requests), 2)


if __name__ == "__main__":
    unittest.main()
