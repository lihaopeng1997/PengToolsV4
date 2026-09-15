"""Behavioural checks for the pure data-center Agent contracts."""

from __future__ import annotations

import threading
import unittest

from tools.data_center.budget import Budget, BudgetLimits
from tools.data_center.contracts import (
    AgentEventEmitter,
    AgentEventType,
    AgentTurn,
    CancellationToken,
    ModelDelta,
    QueryRequest,
    RunContext,
    ToolCall,
    ToolResult,
)


class DataCenterAgentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = RunContext(
            run_id="run-1",
            tab_id="tab-1",
            model_config_id="model-1",
            connection_id="conn-1",
            database="demo",
            schema_allowlist=("public",),
            profile_revision="r1",
            policy_version="p1",
        )

    def test_run_context_has_scope_but_no_credentials(self) -> None:
        payload = self.context.as_dict()
        self.assertEqual(payload["connection_id"], "conn-1")
        self.assertEqual(payload["schema_allowlist"], ["public"])
        self.assertNotIn("password", payload)
        self.assertNotIn("token", payload)

    def test_tool_call_arguments_are_copied_and_have_stable_key(self) -> None:
        raw = {"sql": "select 1", "limit": 10}
        call = ToolCall("call-1", "query_readonly", raw)
        raw["limit"] = 999
        self.assertEqual(call.arguments["limit"], 10)
        self.assertEqual(call.argument_key, '{"limit":10,"sql":"select 1"}')
        self.assertEqual(call.as_dict()["call_id"], "call-1")

    def test_tool_result_message_preserves_call_id_and_evidence(self) -> None:
        result = ToolResult(
            ok=True,
            data={"count": 12},
            evidence_id="ev-1",
            query_id="query-1",
            truncated=False,
        )
        message = result.as_model_message("call-1")
        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call-1")
        self.assertIn('"query_id":"query-1"', message["content"])
        self.assertIn('"count":12', message["content"])

    def test_event_emitter_allocates_monotonic_sequences_concurrently(self) -> None:
        events = []
        emitter = AgentEventEmitter("run-1", "tab-1", events.append)

        threads = [
            threading.Thread(target=lambda: emitter.emit(AgentEventType.BUDGET, {"worker": idx}))
            for idx in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(events), 12)
        self.assertEqual(sorted(event.sequence for event in events), list(range(1, 13)))
        self.assertEqual({event.run_id for event in events}, {"run-1"})

    def test_model_delta_accepts_display_channels_without_exposing_hidden_state(self) -> None:
        reasoning = ModelDelta.from_value({"kind": "reasoning_delta", "text": "读取结构"})
        text = ModelDelta.from_value("12 条")
        self.assertEqual(reasoning.kind, "reasoning_delta")
        self.assertEqual(reasoning.text, "读取结构")
        self.assertEqual(text.kind, "text")
        self.assertEqual(text.text, "12 条")

    def test_turn_normalizes_mapping_tool_calls(self) -> None:
        turn = AgentTurn(
            text="",
            tool_calls=({"id": "call-1", "name": "search_objects", "arguments": {"keyword": "订单"}},),
        )
        self.assertEqual(turn.tool_calls[0].call_id, "call-1")
        self.assertEqual(turn.tool_calls[0].name, "search_objects")

    def test_query_request_copies_mutable_inputs(self) -> None:
        params = {"limit": 20}
        request = QueryRequest("session-1", "query-1", 2, sql="select 1", parameters=params)
        params["limit"] = 500
        self.assertEqual(request.parameters["limit"], 20)
        self.assertEqual(request.generation, 2)

    def test_cancellation_is_one_way(self) -> None:
        token = CancellationToken()
        token.cancel("user_stop")
        token.cancel("a_late_reason")
        self.assertTrue(token.is_cancelled())
        self.assertEqual(token.reason, "user_stop")

    def test_budget_limit_is_exhausted_for_the_next_boundary(self) -> None:
        budget = Budget(
            BudgetLimits(
                max_model_turns=1,
                max_tool_calls=10,
                max_queries=10,
                max_duration_s=None,
                max_bytes=None,
                max_input_chars=None,
                max_output_tokens=None,
            )
        )
        budget.reserve_model_turn()
        self.assertEqual(budget.model_turns, 1)
        self.assertEqual(budget.exhausted_reason(), "model_turns")

    def test_input_output_and_bytes_counters_are_capped_on_overflow(self) -> None:
        budget = Budget(
            BudgetLimits(
                max_duration_s=None,
                max_bytes=4,
                max_input_chars=5,
                max_output_tokens=3,
            )
        )
        self.assertFalse(budget.add_input_chars("abcdef"))
        self.assertFalse(budget.add_output_tokens(7))
        self.assertFalse(budget.add_bytes("abcdef"))
        self.assertEqual(budget.input_chars, 5)
        self.assertEqual(budget.output_tokens, 3)
        self.assertEqual(budget.bytes_used, 4)
        self.assertEqual(budget.exhausted_reason(), "bytes")


if __name__ == "__main__":
    unittest.main()
