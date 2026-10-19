"""Combined memory pruning/native-tool/private-continuation synthetic transport."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.agent_core import AgentModelHostAdapter
from tools.agent_core.context import ContextLimits, ContextPlanner
from tools.agent_core.memory import MemoryBinding
from tools.agent_runtime import run_agent_loop
from tests.test_deepseek_private_reasoning import _config, _frame, _text_frames, _SequenceTransport


class MemoryPrivateContinuationTests(unittest.TestCase):
    def test_compaction_keeps_active_native_exchange_and_private_reply_only_on_wire(self):
        marker = 'synthetic-private-continuation'
        frames = [
            _frame({'choices': [{'delta': {'reasoning_content': marker}}]}),
            _frame({'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': 'read-1', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{"path":"main.py"}'},
            }]}}]}),
            _frame({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}),
            _frame('[DONE]'),
        ]
        transport = _SequenceTransport([frames, _text_frames('done')])
        config = _config()
        adapter = AgentModelHostAdapter('deepseek-profile', config=config, transport=transport)
        request_caps = []
        complete_turn = adapter.complete_turn

        def observe_cap(request, *, cancellation, on_delta):
            request_caps.append(request.max_request_bytes)
            return complete_turn(request, cancellation=cancellation, on_delta=on_delta)

        adapter.complete_turn = observe_cap
        history = [{'role': 'user', 'content': 'Only read the current workspace.'},
                   {'role': 'assistant', 'content': 'old detail ' * 12000},
                   {'role': 'user', 'content': 'read main.py'}]
        original = copy.deepcopy(history)
        memories = []
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'main.py').write_bytes(b'value = 1\r\n')
            final, messages, calls = run_agent_loop(
                'read main.py', directory, config, history, [], model_adapter=adapter,
                context_planner=ContextPlanner(ContextLimits(20000, output_reserve_tokens=128)),
                memory_binding=MemoryBinding('w', 'c', 1, 1, 'synthetic-profile'),
                memory_cb=memories.append,
            )
        self.assertEqual(final, 'done')
        self.assertEqual(len(transport.bodies), 2)
        self.assertEqual(request_caps, [20_000, 20_000])
        for body in transport.bodies:
            self.assertTrue(any('Untrusted historical Agent context' in item.get('content', '')
                                for item in body['messages']))
            self.assertFalse(any('old detail ' * 12000 == item.get('content') for item in body['messages']))
        sent = transport.bodies[1]['messages']
        assistant = next(item for item in sent if item.get('tool_calls'))
        self.assertEqual(assistant['reasoning_content'], marker)
        tool = next(item for item in sent if item.get('role') == 'tool')
        self.assertEqual(assistant['tool_calls'][0]['id'], tool['tool_call_id'])
        self.assertIn('value = 1', tool['content'])
        self.assertEqual(messages[:len(original)], original)
        self.assertNotIn(marker, json.dumps([messages, calls, memories]))
        self.assertEqual(calls[0]['tool'], 'read_file')
        self.assertEqual(memories[-1]['artifacts'][0]['path'], 'main.py')
        self.assertEqual(adapter._adapter._private_continuations, {})
        self.assertEqual(adapter._adapter._private_continuation_bytes, {})


if __name__ == '__main__':
    unittest.main()
