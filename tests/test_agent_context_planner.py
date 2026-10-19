from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from tools.agent_core.context import (
    ConservativeTokenEstimator,
    ContextLimits,
    ContextPlanner,
    TokenEstimate,
)


class _MessageCountEstimator:
    def __init__(self) -> None:
        self.calls = 0

    def estimate_request(self, system_prompt, messages, tools):
        self.calls += 1
        return TokenEstimate(len(messages), "test_message_count")


class _WrappedDefaultEstimator:
    """Exercise the injected-estimator full-recount path as a reference."""

    def __init__(self) -> None:
        self.delegate = ConservativeTokenEstimator()

    def estimate_request(self, system_prompt, messages, tools):
        return self.delegate.estimate_request(system_prompt, messages, tools)


class AgentContextPlannerTests(unittest.TestCase):
    def test_default_estimator_counts_full_utf8_request_and_schemas(self) -> None:
        system_prompt = "请按原样核对"
        messages = [{"role": "user", "content": '{"value":"汉字"}'}]
        tools = [{"type": "function", "function": {"name": "calc", "parameters": {"type": "object"}}}]
        request = {"system_prompt": system_prompt, "messages": messages, "tools": tools}
        byte_count = len(json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8"))

        estimate = ConservativeTokenEstimator().estimate_request(system_prompt, messages, tools)

        self.assertEqual(estimate.tokens, byte_count + 4)
        self.assertEqual(estimate.kind, "heuristic_utf8_bytes")

    def test_untrimmed_system_prompt_and_tool_schema_are_budgeted(self) -> None:
        planner = ContextPlanner(ContextLimits(input_token_limit=20))
        for system_prompt, tools in (("long fixed prompt", []), ("", [{"schema": "large fixed schema"}])):
            with self.subTest(system_prompt=bool(system_prompt), tools=bool(tools)):
                plan = planner.plan(system_prompt=system_prompt, messages=[], tools=tools)
                self.assertEqual(plan.status, "blocked")
                self.assertEqual(plan.reason, "context_anchors_too_large")
                self.assertEqual(plan.messages, [])
                self.assertEqual(plan.estimate, TokenEstimate(0, "not_estimated"))

    def test_unknown_window_is_explicitly_only_an_application_budget(self) -> None:
        plan = ContextPlanner(ContextLimits(input_token_limit=1000)).plan(
            system_prompt="s", messages=[{"role": "user", "content": "你好"}], tools=[]
        )

        self.assertEqual(plan.status, "ready")
        self.assertFalse(plan.window_known)
        self.assertIn("window_unknown", plan.reason)
        self.assertEqual(plan.estimate.kind, "heuristic_utf8_bytes")

    def test_known_window_reserves_output_margin_and_private_capacity(self) -> None:
        estimator = _MessageCountEstimator()
        limits = ContextLimits(
            input_token_limit=100,
            context_window_tokens=8,
            output_reserve_tokens=2,
            safety_margin_tokens=1,
            adapter_private_reserve_tokens=2,
        )
        messages = [
            {"role": "user", "content": "anchor"},
            {"role": "assistant", "content": "old one"},
            {"role": "assistant", "content": "old two"},
            {"role": "assistant", "content": "latest"},
        ]

        plan = ContextPlanner(limits, estimator).plan(system_prompt="", messages=messages, tools=[])

        self.assertEqual(plan.status, "ready")
        self.assertTrue(plan.window_known)
        self.assertEqual(plan.output_reserve_tokens, 2)
        # The known input allowance is 8 - 2 - 1 - 2 = 3; one old
        # assistant message must be removed to honor all three reserves.
        self.assertEqual(plan.estimate.tokens, 3)
        self.assertEqual(plan.messages_dropped, 1)

    def test_all_system_and_user_messages_are_pinned(self) -> None:
        estimator = _MessageCountEstimator()
        messages = [
            {"role": "system", "content": "system anchor"},
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old assistant"},
            {"role": "user", "content": "latest user"},
        ]
        plan = ContextPlanner(ContextLimits(input_token_limit=3), estimator).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual([message["content"] for message in plan.messages], [
            "system anchor", "old user", "latest user"
        ])
        self.assertEqual(plan.messages_dropped, 1)

    def test_multi_tool_exchange_is_evicted_as_one_complete_unit(self) -> None:
        estimator = _MessageCountEstimator()
        messages = [
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call-a", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "call-b", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call-b", "content": "result b"},
            {"role": "tool", "tool_call_id": "call-a", "content": "result a"},
            {"role": "assistant", "tool_calls": [{"id": "fresh-call"}]},
            {"role": "tool", "tool_call_id": "fresh-call", "content": "fresh result"},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": "latest answer"},
        ]

        plan = ContextPlanner(ContextLimits(input_token_limit=5), estimator).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.messages_dropped, 3)
        self.assertEqual(
            [message["role"] for message in plan.messages],
            ["user", "assistant", "tool", "user", "assistant"],
        )
        self.assertEqual(plan.messages[1]["tool_calls"][0]["id"], "fresh-call")
        self.assertEqual(plan.messages[2]["tool_call_id"], "fresh-call")

    def test_latest_tool_exchange_stays_even_after_newer_ordinary_messages(self) -> None:
        messages = [
            {"role": "user", "content": "read request"},
            {"role": "assistant", "tool_calls": [{"id": "read-call"}]},
            {"role": "tool", "tool_call_id": "read-call", "content": "read result"},
            {"role": "assistant", "content": "read answer"},
            {"role": "user", "content": "new request"},
        ]

        plan = ContextPlanner(ContextLimits(input_token_limit=4), _MessageCountEstimator()).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.messages_dropped, 1)
        self.assertEqual([message["role"] for message in plan.messages], ["user", "assistant", "tool", "user"])
        self.assertEqual(plan.messages[1]["tool_calls"][0]["id"], "read-call")
        self.assertEqual(plan.messages[2]["tool_call_id"], "read-call")

    def test_default_incremental_selection_matches_stepwise_full_recount(self) -> None:
        messages = [
            {"role": "user", "content": "first anchor"},
            {"role": "assistant", "content": "old response"},
            {"role": "assistant", "tool_calls": [{"id": "old-call"}]},
            {"role": "tool", "tool_call_id": "old-call", "content": "old result"},
            {"role": "assistant", "content": "middle response"},
            {"role": "user", "content": "new anchor"},
            {"role": "assistant", "tool_calls": [{"id": "latest-call"}]},
            {"role": "tool", "tool_call_id": "latest-call", "content": "latest result"},
            {"role": "user", "content": "newest anchor"},
        ]
        anchors = [messages[0], messages[5], *messages[6:]]
        estimator = ConservativeTokenEstimator()
        limit = estimator.estimate_request("prompt", anchors, [{"name": "tool"}]).tokens
        limits = ContextLimits(input_token_limit=limit)

        optimized = ContextPlanner(limits).plan(
            system_prompt="prompt", messages=messages, tools=[{"name": "tool"}]
        )
        reference = ContextPlanner(limits, _WrappedDefaultEstimator()).plan(
            system_prompt="prompt", messages=messages, tools=[{"name": "tool"}]
        )

        self.assertEqual(optimized.status, "ready")
        self.assertEqual(reference.status, "ready")
        self.assertEqual(optimized.messages, reference.messages)
        self.assertEqual(optimized.messages_dropped, reference.messages_dropped)
        self.assertEqual(optimized.estimate, reference.estimate)

    def test_default_large_history_uses_constant_full_serialization_calls(self) -> None:
        messages = [
            {"role": "user", "content": "first user anchor"},
            *({"role": "assistant", "content": f"old response {index}"} for index in range(5000)),
            {"role": "assistant", "tool_calls": [{"id": "latest-call"}]},
            {"role": "tool", "tool_call_id": "latest-call", "content": "latest result"},
            {"role": "user", "content": "latest user anchor"},
        ]
        before = copy.deepcopy(messages)
        anchors = [messages[0], *messages[-3:]]
        estimator = ConservativeTokenEstimator()
        limit = estimator.estimate_request("", anchors, []).tokens
        call_count = 0
        original_estimate = ConservativeTokenEstimator.estimate_request

        def counted(instance, system_prompt, candidate_messages, tools):
            nonlocal call_count
            call_count += 1
            return original_estimate(instance, system_prompt, candidate_messages, tools)

        with patch.object(ConservativeTokenEstimator, "estimate_request", new=counted):
            plan = ContextPlanner(ContextLimits(input_token_limit=limit)).plan(
                system_prompt="", messages=messages, tools=[]
            )

        self.assertEqual(call_count, 2)
        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.estimate.tokens, limit)
        self.assertEqual(plan.messages_dropped, 5000)
        self.assertEqual(plan.messages, anchors)
        self.assertEqual(messages, before)

    def test_tool_call_ids_match_exactly_without_normalizing_source_values(self) -> None:
        call_id = " read-call "
        messages = [
            {"role": "assistant", "tool_calls": [{"id": call_id}]},
            {"role": "tool", "tool_call_id": call_id, "content": "read result"},
        ]

        plan = ContextPlanner(ContextLimits(input_token_limit=100), _MessageCountEstimator()).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.messages, messages)

    def test_pending_transcript_is_deferred_without_estimation_or_clipping(self) -> None:
        estimator = _MessageCountEstimator()
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "pending"},
            {"role": "assistant", "tool_calls": [{"id": "pending-call"}]},
        ]

        plan = ContextPlanner(ContextLimits(input_token_limit=1), estimator).plan(
            system_prompt="", messages=messages, tools=[], pending=True
        )

        self.assertEqual(plan.status, "deferred")
        self.assertEqual(plan.reason, "pending_exchange")
        self.assertEqual(plan.estimate, TokenEstimate(0, "not_estimated"))
        self.assertEqual(plan.messages, messages)
        self.assertEqual(plan.messages_dropped, 0)
        self.assertEqual(estimator.calls, 0)

    def test_incomplete_or_ambiguous_tool_histories_are_blocked(self) -> None:
        malformed_histories = [
            # Missing one result.
            [
                {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
                {"role": "tool", "tool_call_id": "a", "content": "one"},
            ],
            # Orphan result.
            [{"role": "tool", "tool_call_id": "a", "content": "orphan"}],
            # Duplicate call ID.
            [{"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "a"}]}],
            # Duplicate result.
            [
                {"role": "assistant", "tool_calls": [{"id": "a"}]},
                {"role": "tool", "tool_call_id": "a", "content": "one"},
                {"role": "tool", "tool_call_id": "a", "content": "duplicate"},
            ],
            # Unexpected result ID.
            [
                {"role": "assistant", "tool_calls": [{"id": "a"}]},
                {"role": "tool", "tool_call_id": "other", "content": "wrong"},
            ],
            # Whitespace is significant: IDs are validated, never normalized.
            [
                {"role": "assistant", "tool_calls": [{"id": "a"}]},
                {"role": "tool", "tool_call_id": " a ", "content": "mismatched"},
            ],
            # A new assistant message before all results.
            [
                {"role": "assistant", "tool_calls": [{"id": "a"}]},
                {"role": "assistant", "content": "interleaved"},
                {"role": "tool", "tool_call_id": "a", "content": "late"},
            ],
            # Unsupported transcript roles cannot be treated as pinned anchors.
            [{"role": "developer", "content": "unsupported"}],
        ]
        for messages in malformed_histories:
            with self.subTest(messages=messages):
                plan = ContextPlanner(ContextLimits(input_token_limit=100)).plan(
                    system_prompt="", messages=messages, tools=[]
                )
                self.assertEqual(plan.status, "blocked")
                self.assertEqual(plan.reason, "incomplete_history")
                self.assertEqual(plan.messages, [])

    def test_source_is_unchanged_and_returned_nested_data_is_deeply_isolated(self) -> None:
        source = [{"role": "user", "content": {"items": [1, {"value": "before"}]}}]
        before = copy.deepcopy(source)
        plan = ContextPlanner(ContextLimits(input_token_limit=10000)).plan(
            system_prompt="", messages=source, tools=[]
        )

        plan.messages[0]["content"]["items"][1]["value"] = "after"

        self.assertEqual(source, before)
        self.assertEqual(source[0]["content"]["items"][1]["value"], "before")

    def test_oversized_pinned_anchors_block_without_returning_candidate(self) -> None:
        estimator = _MessageCountEstimator()
        messages = [
            {"role": "user", "content": "pinned one"},
            {"role": "assistant", "content": "droppable"},
            {"role": "user", "content": "pinned two"},
        ]

        plan = ContextPlanner(ContextLimits(input_token_limit=1), estimator).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.reason, "context_anchors_too_large")
        self.assertEqual(plan.messages, [])

    def test_reserves_larger_than_known_window_block_even_zero_estimates(self) -> None:
        estimator = lambda system_prompt, messages, tools: TokenEstimate(0, "test_zero")
        plan = ContextPlanner(
            ContextLimits(
                input_token_limit=10,
                context_window_tokens=1,
                output_reserve_tokens=2,
                safety_margin_tokens=0,
                adapter_private_reserve_tokens=0,
            ),
            estimator,
        ).plan(system_prompt="", messages=[], tools=[])

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.reason, "context_anchors_too_large")
        self.assertEqual(plan.estimate, TokenEstimate(0, "not_estimated"))

    def test_estimator_exceptions_and_invalid_results_block_safely(self) -> None:
        def raises(system_prompt, messages, tools):
            raise RuntimeError("do not expose request contents")

        invalid_numeric = object.__new__(TokenEstimate)
        object.__setattr__(invalid_numeric, "tokens", True)
        object.__setattr__(invalid_numeric, "kind", "invalid")

        for estimator in (
            raises,
            lambda system_prompt, messages, tools: (True, "fake"),
            lambda system_prompt, messages, tools: TokenEstimate(True, "bad"),
            lambda system_prompt, messages, tools: invalid_numeric,
            object(),
        ):
            with self.subTest(estimator=type(estimator).__name__):
                plan = ContextPlanner(ContextLimits(input_token_limit=100), estimator).plan(
                    system_prompt="", messages=[{"role": "user", "content": "safe"}], tools=[]
                )
                self.assertEqual(plan.status, "blocked")
                self.assertIn(plan.reason, {"estimator_error", "invalid_estimate"})
                self.assertNotIn("do not expose", plan.reason)
                self.assertEqual(plan.messages, [])
                self.assertEqual(plan.estimate, TokenEstimate(0, "not_estimated"))

    def test_callable_estimator_is_supported(self) -> None:
        plan = ContextPlanner(
            ContextLimits(input_token_limit=10),
            lambda system_prompt, messages, tools: TokenEstimate(1, "callable_test"),
        ).plan(system_prompt="s", messages=[{"role": "user", "content": "c"}], tools=[])

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.estimate, TokenEstimate(1, "callable_test"))

    def test_reasoning_content_is_rejected_without_returning_private_data(self) -> None:
        messages = [{
            "role": "assistant",
            "content": "public",
            "reasoning_content": "private continuation",
        }]

        plan = ContextPlanner(ContextLimits(input_token_limit=100)).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.reason, "private_reasoning_content")
        self.assertEqual(plan.messages, [])
        self.assertEqual(plan.estimate, TokenEstimate(0, "not_estimated"))

    def test_reasoning_content_wording_in_public_text_is_not_a_private_field(self) -> None:
        messages = [{
            "role": "user",
            "content": "Please explain the field name reasoning_content in documentation.",
        }]

        plan = ContextPlanner(ContextLimits(input_token_limit=1000)).plan(
            system_prompt="", messages=messages, tools=[]
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.messages, messages)

    def test_typed_limits_and_estimates_reject_bool(self) -> None:
        with self.assertRaises(TypeError):
            ContextLimits(input_token_limit=True)
        with self.assertRaises(ValueError):
            ContextLimits(input_token_limit=0)
        with self.assertRaises(TypeError):
            ContextLimits(input_token_limit=10, context_window_tokens=False)
        with self.assertRaises(TypeError):
            ContextLimits(input_token_limit=10, output_reserve_tokens=False)
        self.assertEqual(
            ContextLimits(input_token_limit=10, output_reserve_tokens=0).output_reserve_tokens,
            0,
        )
        with self.assertRaises(ValueError):
            ContextLimits(input_token_limit=10, safety_margin_tokens=-1)
        with self.assertRaises(ValueError):
            ContextLimits(input_token_limit=10, adapter_private_reserve_tokens=-1)
        with self.assertRaises(TypeError):
            TokenEstimate(True, "heuristic_utf8_bytes")
        with self.assertRaises(ValueError):
            TokenEstimate(-1, "heuristic_utf8_bytes")


if __name__ == "__main__":
    unittest.main()
