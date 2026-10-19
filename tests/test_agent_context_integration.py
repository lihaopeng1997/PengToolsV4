"""Context request planning against the real runner and TEMP programming host."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.agent_core.budget import BudgetLimits
from tools.agent_core.context import ContextLimits, ContextPlanner, TokenEstimate
from tools.agent_core import AgentModelHostAdapter
from tools.agent_core.contracts import AgentStatus, AgentTurn, RunContext, ToolCall, ToolResult
from tools.agent_core.request_budget import RequestInputLimitExceeded
from tools.agent_core.runner import AgentRunner
from tools.agent_runtime import run_agent_loop
from tools.agent_core.request_budget import RequestInputLimitExceeded
from tests.test_deepseek_private_reasoning import _config, _frame


class _Model:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.released = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        return self.turns.pop(0)

    def release_run(self, context):
        self.released.append(context.run_id)


class _Tools:
    def __init__(self, execute=None):
        self.calls = []
        self.callback = execute

    def definitions(self):
        return [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]

    def execute(self, call, context, cancellation):
        self.calls.append(call.call_id)
        if self.callback:
            self.callback()
        return ToolResult(ok=True, data={"path": "example.py", "content": "value = 1"})


class _CountingPlanner(ContextPlanner):
    def __init__(self, limits):
        super().__init__(limits)
        self.calls = 0

    def plan(self, **kwargs):
        self.calls += 1
        return super().plan(**kwargs)


class _GateAfterToolModel:
    def __init__(self, calls):
        self.calls = calls
        self.requests = []
        self.released = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        if len(self.requests) == 1:
            return AgentTurn(tool_calls=self.calls, finish_reason='tool_calls')
        raise RequestInputLimitExceeded(
            input_bytes=request.max_request_bytes + 1,
            limit_bytes=request.max_request_bytes,
        )

    def release_run(self, context):
        self.released.append(context.run_id)


class AgentContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.context = RunContext("context-run", "tab", "synthetic", domain="programming")

    def planner(self, limit=3000, reserve=32):
        return ContextPlanner(ContextLimits(
            input_token_limit=limit, output_reserve_tokens=reserve, safety_margin_tokens=16,
        ))

    def test_only_default_conservative_planner_dispatches_input_budget_as_byte_cap(self):
        limits = ContextLimits(
            input_token_limit=50_000,
            context_window_tokens=20_000,
            output_reserve_tokens=2_000,
            safety_margin_tokens=500,
            adapter_private_reserve_tokens=1_000,
        )
        default_model = _Model([AgentTurn(text='done')])
        default_result = AgentRunner(
            default_model, _Tools(), context_planner=ContextPlanner(limits),
        ).run(self.context, 'question')
        self.assertEqual(default_result.status, AgentStatus.COMPLETED)
        self.assertEqual(default_model.requests[0].max_request_bytes, 16_500)

        custom_planner = ContextPlanner(
            limits,
            estimator=lambda _system, _messages, _tools: TokenEstimate(1, 'custom_tokenizer'),
        )
        custom_model = _Model([AgentTurn(text='done')])
        custom_result = AgentRunner(
            custom_model, _Tools(), context_planner=custom_planner,
        ).run(self.context, 'question')
        self.assertEqual(custom_result.status, AgentStatus.COMPLETED)
        self.assertIsNone(custom_model.requests[0].max_request_bytes)

        no_planner_model = _Model([AgentTurn(text='done')])
        no_planner_result = AgentRunner(no_planner_model, _Tools()).run(self.context, 'question')
        self.assertEqual(no_planner_result.status, AgentStatus.COMPLETED)
        self.assertIsNone(no_planner_model.requests[0].max_request_bytes)

    def test_final_request_limit_keeps_three_completed_tool_calls_and_canonical_results(self):
        calls = tuple(
            ToolCall(f'read-{index}', 'read', {'path': f'synthetic-{index}.txt'})
            for index in range(1, 4)
        )
        model = _GateAfterToolModel(calls)
        tools = _Tools()
        events = []
        result = AgentRunner(
            model, tools, context_planner=self.planner(limit=5_000), on_event=events.append,
        ).run(self.context, 'read the three synthetic files')

        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error, 'final_request_input_bytes')
        self.assertEqual(len(model.requests), 2)
        self.assertEqual([request.max_request_bytes for request in model.requests], [5_000, 5_000])
        self.assertEqual(len(tools.calls), 3)
        assistant = next(message for message in result.messages if message.get('tool_calls'))
        self.assertEqual([call['id'] for call in assistant['tool_calls']], [f'read-{i}' for i in range(1, 4)])
        tool_messages = [message for message in result.messages if message.get('role') == 'tool']
        self.assertEqual([message['tool_call_id'] for message in tool_messages], [f'read-{i}' for i in range(1, 4)])
        self.assertEqual(len(result.tool_results), 3)
        self.assertEqual(model.released, [self.context.run_id])
        gate_event = next(
            event for event in events
            if event.event_type == 'budget' and 'request_budget_status' in event.payload
        )
        self.assertEqual(set(gate_event.payload), {
            'request_budget_status', 'measurement_kind', 'local_byte_limit',
        })
        self.assertEqual(gate_event.payload['request_budget_status'], 'blocked')
        self.assertEqual(gate_event.payload['measurement_kind'], 'serialized_request_utf8_bytes')
        self.assertEqual(gate_event.payload['local_byte_limit'], 5_000)
        self.assertNotIn('input_bytes', str(gate_event.payload))

    def test_private_final_body_blocks_second_send_after_three_real_temp_reads(self):
        private_marker = 'synthetic-private-continuation-' + ('x' * 70_000)
        tool_calls = [{
            'index': index,
            'id': f'temp-read-{index}',
            'type': 'function',
            'function': {
                'name': 'read_file',
                'arguments': json.dumps({'path': f'src/file-{index}.py'}),
            },
        } for index in range(1, 4)]
        first_frames = [
            _frame({'choices': [{'delta': {'reasoning_content': private_marker}}]}),
            _frame({'choices': [{'delta': {'tool_calls': tool_calls}}]}),
            _frame({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}),
            _frame('[DONE]'),
        ]

        class _SingleExchangeTransport:
            def __init__(self):
                self.bodies = []

            def stream(self, body, *, cancellation, deadline=None):
                self.bodies.append(deepcopy(dict(body)))
                return iter(first_frames)

        transport = _SingleExchangeTransport()
        config = _config(
            agent_max_reasoning_bytes=100_000,
            agent_max_raw_buffer_bytes=120_000,
        )
        adapter = AgentModelHostAdapter(
            'deepseek-profile', config=config, transport=transport, retry_count=0,
        )
        events = []
        measurements = []
        import tools.agent_core.model_adapter as model_adapter_module
        real_measure = model_adapter_module.measure_model_request

        def record_measurement(body, *, max_request_bytes):
            private_present = any(
                message.get('reasoning_content') == private_marker
                for message in body.get('messages', ())
                if isinstance(message, dict)
            )
            try:
                receipt = real_measure(body, max_request_bytes=max_request_bytes)
            except RequestInputLimitExceeded as error:
                measurements.append((error.input_bytes, error.limit_bytes, private_present))
                raise
            measurements.append((receipt.input_bytes, receipt.limit_bytes, private_present))
            return receipt

        with tempfile.TemporaryDirectory() as directory:
            source_dir = Path(directory) / 'src'
            source_dir.mkdir()
            for index in range(1, 4):
                (source_dir / f'file-{index}.py').write_text(
                    f'TEMP_READ_RECEIPT_{index} = "safe"\n', encoding='utf-8',
                )
            with patch.object(model_adapter_module, 'measure_model_request', side_effect=record_measurement):
                final, messages, calls = run_agent_loop(
                    'Read the three temporary files', directory, config, [], [],
                    model_adapter=adapter,
                    context_planner=ContextPlanner(ContextLimits(
                        input_token_limit=64_000,
                        context_window_tokens=None,
                        output_reserve_tokens=128,
                    )),
                    event_cb=events.append,
                )

        self.assertIn('后续模型请求未发送', final)
        self.assertEqual(len(transport.bodies), 1)
        self.assertEqual(len(measurements), 2)
        self.assertLessEqual(measurements[0][0], measurements[0][1])
        self.assertGreater(measurements[1][0], measurements[1][1])
        self.assertTrue(measurements[1][2])
        self.assertEqual(measurements[1][1], 64_000)
        self.assertEqual(sum(item.get('tool') == 'read_file' for item in calls), 3)
        tool_messages = [item for item in messages if item.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 3)
        self.assertEqual({item['tool_call_id'] for item in tool_messages}, {
            'temp-read-1', 'temp-read-2', 'temp-read-3',
        })
        serialized_public = json.dumps([final, messages, calls], ensure_ascii=False)
        self.assertNotIn(private_marker, serialized_public)
        self.assertTrue(all(f'TEMP_READ_RECEIPT_{index}' in serialized_public for index in range(1, 4)))
        planned = [
            event for event in events
            if event.event_type == 'budget' and event.payload.get('context_status') is not None
        ]
        self.assertEqual(len(planned), 2)
        self.assertTrue(all(event.payload['context_status'] == 'ready' for event in planned))
        self.assertEqual(adapter._adapter._private_continuations, {})
        self.assertEqual(adapter._adapter._private_continuation_bytes, {})

    def test_outgoing_prune_keeps_user_anchors_and_full_canonical_transcript(self):
        history = [
            {"role": "user", "content": "只改当前项目；修改必须确认。"},
            {"role": "assistant", "content": "old response " * 1000},
            {"role": "user", "content": "继续"},
        ]
        original = deepcopy(history)
        model = _Model([AgentTurn(text="done")])
        result = AgentRunner(model, _Tools(), context_planner=self.planner()).run(
            self.context, messages=history,
        )
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual([m["content"] for m in model.requests[0].messages],
                         [history[0]["content"], "继续"])
        self.assertEqual(list(result.messages[:-1]), original)
        self.assertEqual(history, original)
        context_events = [e for e in result.events if e.event_type == "budget"]
        self.assertEqual(context_events[0].payload["messages_dropped"], 1)
        self.assertFalse(context_events[0].payload["context_window_known"])
        self.assertEqual(model.released, ["context-run"])

    def test_anchor_overflow_stops_before_model_and_tools_and_releases(self):
        model, tools = _Model([AgentTurn(text="must not run")]), _Tools()
        question = "不能省略的用户要求" * 1000
        result = AgentRunner(model, tools, context_planner=self.planner(200)).run(
            self.context, question,
        )
        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error, "context_anchors_too_large")
        self.assertEqual(model.requests, [])
        self.assertEqual(tools.calls, [])
        self.assertEqual(model.released, ["context-run"])
        event = next(e for e in result.events if e.event_type == "budget")
        self.assertNotIn(question, str(event.payload))
        self.assertEqual(result.messages[0]["content"], question)

    def test_completed_tool_boundary_plans_once_per_turn_without_reexecution(self):
        planner = _CountingPlanner(ContextLimits(
            input_token_limit=3000, output_reserve_tokens=32, safety_margin_tokens=16,
        ))
        model = _Model([
            AgentTurn(tool_calls=(ToolCall("read-1", "read", {}),), usage={"completion_tokens": 3}),
            AgentTurn(text="done", usage={"completion_tokens": 2}),
        ])
        tools = _Tools(lambda: self.assertEqual(planner.calls, 1))
        result = AgentRunner(
            model, tools, context_planner=planner,
            budget_limits=BudgetLimits(max_output_tokens=32),
        ).run(self.context, "read")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(tools.calls, ["read-1"])
        self.assertEqual([r.max_output_tokens for r in model.requests], [32, 29])
        last = model.requests[-1].messages
        self.assertEqual([m["role"] for m in last], ["user", "assistant", "tool"])
        self.assertEqual(last[1]["tool_calls"][0]["id"], last[2]["tool_call_id"])
        self.assertEqual(model.released, ["context-run"])

    def test_unlimited_run_output_still_obeys_request_output_reserve(self):
        model = _Model([AgentTurn(text="done")])
        result = AgentRunner(
            model, _Tools(), context_planner=self.planner(reserve=17),
            budget_limits=BudgetLimits(max_output_tokens=None),
        ).run(self.context, "question")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(model.requests[0].max_output_tokens, 17)

    def test_exact_output_exhaustion_prevents_another_request(self):
        model = _Model([
            AgentTurn(tool_calls=(ToolCall("read-1", "read", {}),), usage={"completion_tokens": 8}),
            AgentTurn(text="must not request"),
        ])
        result = AgentRunner(model, _Tools(), budget_limits=BudgetLimits(max_output_tokens=8)).run(
            self.context, "read",
        )
        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error, "output_tokens")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.released, ["context-run"])

    def test_malformed_exchange_cannot_reach_adapter(self):
        model = _Model([AgentTurn(text="must not run")])
        result = AgentRunner(model, _Tools(), context_planner=self.planner()).run(
            self.context, messages=[{"role": "tool", "tool_call_id": "orphan", "content": "result"}],
        )
        self.assertEqual(result.error, "incomplete_history")
        self.assertEqual(model.requests, [])
        self.assertEqual(model.released, ["context-run"])

    def test_programming_host_preserves_user_requirement_older_than_thirty_messages(self):
        history = [{"id": "original", "role": "user", "content": "必须先确认修改"}]
        history += [{"id": str(i), "role": "assistant", "content": f"response {i}"} for i in range(35)]
        model = _Model([AgentTurn(text="done")])
        with tempfile.TemporaryDirectory() as directory:
            final, messages, calls = run_agent_loop(
                "继续分析", directory, {"id": "synthetic"}, history, [], model_adapter=model,
            )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(final, "done")
        self.assertEqual(calls, [])
        self.assertTrue(any(m["content"] == "必须先确认修改" for m in model.requests[0].messages))
        self.assertEqual(messages[0]["id"], "original")
        self.assertEqual(len(messages), 38)

    def test_programming_budget_stop_keeps_history_and_explains_context_limit(self):
        history = [{"id": "original", "role": "user", "content": "a" * 1000}]
        model = _Model([AgentTurn(text="must not run")])
        with tempfile.TemporaryDirectory() as directory:
            final, messages, calls = run_agent_loop(
                "继续", directory, {"id": "synthetic"}, history, [], model_adapter=model,
                context_planner=self.planner(200),
            )
        self.assertIn("上下文超出输入上限", final)
        self.assertEqual(messages[0]["content"], "a" * 1000)
        self.assertEqual(model.requests, [])
        self.assertEqual(calls, [])

    def test_programming_request_respects_selected_snapshot_output_limit(self):
        model = _Model([AgentTurn(text="done")])
        model.snapshot = SimpleNamespace(max_tokens=128)
        with tempfile.TemporaryDirectory() as directory:
            final, _messages, calls = run_agent_loop(
                "分析", directory, {"id": "synthetic"}, [], [], model_adapter=model,
            )
        self.assertEqual(final, "done")
        self.assertEqual(calls, [])
        self.assertEqual(model.requests[0].max_output_tokens, 128)

    def test_private_history_cannot_enter_model_or_public_result(self):
        model = _Model([AgentTurn(text="must not run")])
        private_text = "adapter-private-test-marker"
        history = [{"role": "assistant", "content": "public", "reasoning_content": private_text}]
        result = AgentRunner(model, _Tools(), context_planner=self.planner()).run(
            self.context, "question", messages=history,
        )
        self.assertEqual(result.error, "PRIVATE_REASONING_HISTORY")
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(model.requests, [])
        self.assertEqual(model.released, ["context-run"])
        self.assertNotIn(private_text, str(result.messages))
        self.assertNotIn(private_text, str(result.events))
        self.assertEqual(history[0]["reasoning_content"], private_text)

    def test_injected_planner_cannot_override_selected_profile_output_limit(self):
        model = _Model([AgentTurn(text="done")])
        model.snapshot = SimpleNamespace(max_tokens=128)
        planner = ContextPlanner(ContextLimits(input_token_limit=64000, output_reserve_tokens=4096))
        with tempfile.TemporaryDirectory() as directory:
            final, _messages, calls = run_agent_loop(
                "分析", directory, {"id": "synthetic"}, [], [], model_adapter=model,
                context_planner=planner,
            )
        self.assertEqual(final, "done")
        self.assertEqual(calls, [])
        self.assertEqual(model.requests[0].max_output_tokens, 128)


if __name__ == "__main__":
    unittest.main()
