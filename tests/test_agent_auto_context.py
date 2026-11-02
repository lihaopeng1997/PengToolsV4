"""Default 500 KiB request planning, recovery and canonical-history regressions."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tools import agent_diag
from tools.agent_core.auto_context import default_context_planner, DEFAULT_CONTEXT_INPUT_BYTES
from tools.agent_core import AgentTurn
from tools.agent_core.context import _parse_units
from tools.agent_runtime import run_agent_loop


def exchange(size=180000, count=3):
    calls = [{'id': f'call-{i}', 'type': 'function',
              'function': {'name': 'read_file', 'arguments': '{"path":"sample.py"}'}}
             for i in range(count)]
    return [{'role': 'assistant', 'content': '', 'tool_calls': calls}] + [
        {'role': 'tool', 'tool_call_id': call['id'],
         'content': json.dumps({'ok': True, 'code': 'OK', 'data': '中' * size}, ensure_ascii=False)}
        for call in calls]


class AutoContextTests(unittest.TestCase):
    def test_small_request_preserves_every_message_and_500k_wire_ceiling(self):
        messages = [{'role': 'user', 'content': '必须保留接口'}, {'role': 'assistant', 'content': '收到'}]
        plan = default_context_planner().plan(system_prompt='', messages=messages, tools=[])
        self.assertEqual(plan.messages, messages)
        self.assertEqual(plan.max_request_bytes, 512000)

    def test_proactive_pruning_before_hard_ceiling_keeps_latest_exchange(self):
        messages = [{'role': 'user', 'content': '只读'}] + exchange(150000, 1)
        messages += [{'role': 'user', 'content': '继续'}, {'role': 'assistant', 'content': 'latest'}]
        original = copy.deepcopy(messages)
        plan = default_context_planner().plan(system_prompt='', messages=messages, tools=[])
        self.assertEqual(plan.status, 'ready')
        self.assertEqual(plan.reason, 'auto_compacted')
        self.assertLess(plan.estimate.tokens, DEFAULT_CONTEXT_INPUT_BYTES * .6)
        self.assertFalse(_parse_units(plan.messages)[1])
        self.assertEqual(messages, original)
        assistant = next(m for m in plan.messages if m.get('tool_calls'))
        self.assertEqual(assistant, messages[1])
        self.assertTrue(json.loads(next(m['content'] for m in plan.messages if m['role'] == 'tool'))['context_compacted'])

    def test_latest_oversized_tool_batch_is_shortened_without_splitting_calls(self):
        messages = [{'role': 'user', 'content': 'read sources'}] + exchange()
        original = copy.deepcopy(messages)
        plan = default_context_planner().plan(system_prompt='', messages=messages, tools=[])
        self.assertEqual(plan.status, 'ready')
        self.assertFalse(_parse_units(plan.messages)[1])
        self.assertEqual(plan.messages[1], messages[1])
        self.assertEqual(len([m for m in plan.messages if m['role'] == 'tool']), 3)
        for message in plan.messages[2:]:
            payload = json.loads(message['content'])
            self.assertTrue(payload['context_compacted'])
            self.assertEqual(payload['original_receipt']['code'], 'OK')
        self.assertEqual(messages, original)

    def test_older_user_attachment_compacts_but_current_request_and_constraints_remain(self):
        messages = [{'role': 'user', 'content': '必须保留接口\n' + 'old source\n' * 70000},
                    {'role': 'assistant', 'content': 'read'}, {'role': 'user', 'content': '现在只分析，不要写入'}]
        plan = default_context_planner().plan(system_prompt='', messages=messages, tools=[])
        self.assertEqual(plan.status, 'ready')
        self.assertEqual(plan.messages[-1], messages[-1])
        self.assertIn('必须保留接口', plan.messages[0]['content'])
        self.assertTrue(json.loads(plan.messages[0]['content'])['context_compacted'])

    def test_live_request_and_private_or_incomplete_history_are_never_silently_clipped(self):
        planner = default_context_planner()
        for messages, reason in [
            ([{'role': 'user', 'content': 'x' * 600000}], 'context_anchors_too_large'),
            ([{'role': 'assistant', 'content': 'x', 'reasoning_content': 'private'}], 'private_reasoning_content'),
            ([{'role': 'tool', 'tool_call_id': 'orphan', 'content': 'x'}], 'incomplete_history'),
        ]:
            plan = planner.plan(system_prompt='', messages=messages, tools=[])
            self.assertEqual(plan.reason, reason)
            self.assertEqual(plan.status, 'blocked')

    def test_host_resumes_large_history_without_changing_original_or_500k_gate(self):
        class Model:
            requests = []
            def complete_turn(self, request, **kwargs):
                self.requests.append(request)
                return AgentTurn(text='done')
        history = [{'role': 'user', 'content': '只读'}] + exchange()
        original = copy.deepcopy(history)
        model = Model()
        with tempfile.TemporaryDirectory() as temp, patch.object(agent_diag, '_LOG_DIR', temp):
            final, messages, calls = run_agent_loop('继续', temp, {'id': 'fixture'}, history, [], model_adapter=model)
        self.assertEqual(final, 'done')
        self.assertEqual(messages[:len(original)], original)
        self.assertEqual(history[:len(original)], original)
        self.assertEqual(model.requests[0].max_request_bytes, 512000)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
