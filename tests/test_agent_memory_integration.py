"""Synthetic acceptance of request memories, byte receipts, and revision commits."""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools import agent_store as store
from tools.agent_runtime import execute_tool, run_agent_loop
from tools.agent_core import AgentTurn, ToolCall
from tools.agent_core.context import ContextLimits, ContextPlanner
from tools.agent_core.memory import MemoryBinding, build_memory, decode_memory
from tools.agent_core.memory_context import MemoryContextPlanner, model_profile_revision
from tests.test_agent_store import _IsolatedDirMixin


class _Model:
    capability = 'native_tools'

    def __init__(self, turns=None):
        self.requests = []
        self.released = []
        self.turns = list(turns or [AgentTurn(text='done')])

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        return self.turns.pop(0)

    def release_run(self, context):
        self.released.append(context.run_id)


class MemoryRequestTests(unittest.TestCase):
    def test_host_refuses_private_history_before_model_and_returns_only_public_data(self):
        from tools.agent_core import CancellationToken
        marker = 'synthetic_private_marker'
        original = [{'role': 'assistant', 'content': 'public', 'reasoning_content': marker}]
        original_before = copy.deepcopy(original)
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                model = _Model()
                cancellation = CancellationToken()
                if cancelled:
                    cancellation.cancel('test cancellation')
                answer, messages, calls = run_agent_loop('go', directory, {'id': 'p'}, original,
                    [{'id': 't', 'reasoning_content': marker}], model_adapter=model,
                    cancellation=cancellation)
                self.assertIn('私有推理', answer)
                self.assertEqual(model.requests, [])
                self.assertNotIn(marker, json.dumps([messages, calls]))
                self.assertFalse(any('reasoning_content' in item for item in messages + calls))
        self.assertEqual(original, original_before)

    def test_memory_callback_failure_does_not_discard_final_result(self):
        with tempfile.TemporaryDirectory() as directory:
            model = _Model()
            def broken_callback(_):
                raise RuntimeError('synthetic callback failure')
            final, messages, _ = run_agent_loop('go', directory, {'id': 'p'}, [], [],
                model_adapter=model, memory_binding=MemoryBinding('w', 'c', 0, 0, 'p'),
                memory_cb=broken_callback)
            self.assertEqual(final, 'done')
            self.assertEqual(messages[-1]['content'], 'done')
            self.assertEqual(len(model.released), 1)

    def test_invalid_memory_binding_fails_before_model_or_cancel_monitor(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, \
                patch('tools.agent_runtime.threading.Thread') as monitor:
            model = _Model()
            with self.assertRaisesRegex(TypeError, 'INVALID_MEMORY_BINDING'):
                run_agent_loop('go', directory, {'id': 'p'}, [], [], model_adapter=model,
                               memory_binding={}, cancel_cb=lambda: False)
            self.assertEqual(model.requests, [])
            monitor.assert_not_called()

    def test_pruned_request_has_memory_but_canonical_history_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            original = [{'role': 'user', 'content': 'Only change after confirmation.'},
                        {'role': 'assistant', 'content': 'old detail ' * 12000}]
            history = copy.deepcopy(original)
            model = _Model()
            binding = MemoryBinding('workspace', 'conversation', 1, 2, 'synthetic-profile')
            memories = []
            final, messages, _ = run_agent_loop(
                'continue', directory, {'id': 'synthetic-profile'}, history, [],
                model_adapter=model,
                context_planner=ContextPlanner(ContextLimits(20000, output_reserve_tokens=32)),
                memory_binding=binding, memory_cb=memories.append,
            )
            self.assertEqual(final, 'done')
            sent = model.requests[0].messages
            memory_messages = [item for item in sent if 'untrusted' in item.get('content', '').lower()]
            self.assertEqual(len(memory_messages), 1)
            self.assertEqual(memory_messages[0]['role'], 'assistant')
            self.assertEqual(messages[:2], original)
            self.assertFalse(any('untrusted' in item.get('content', '').lower() for item in messages))
            cleaned = store._clean_message_history(messages)
            self.assertIsNotNone(decode_memory(memories[-1], messages=cleaned, binding=binding))
            self.assertEqual(len(model.released), 1)

    def test_memory_overflow_falls_back_to_valid_pinned_anchors(self):
        messages = [{'role': 'user', 'content': 'keep'},
                    {'role': 'assistant', 'content': 'old' * 3000},
                    {'role': 'user', 'content': 'next'}]
        planner = MemoryContextPlanner(ContextPlanner(ContextLimits(300)),
                                       MemoryBinding('w', 'c', 0, 0, 'p'))
        plan = planner.plan(system_prompt='', messages=messages, tools=[])
        self.assertEqual(plan.status, 'ready')
        self.assertEqual(plan.reason, 'memory_omitted')
        self.assertEqual([item['content'] for item in plan.messages], ['keep', 'next'])

    def test_real_registry_receipt_is_indexed_without_copying_file_body(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = b'private_file_body = 1\r\n'
            (Path(directory) / 'main.py').write_bytes(raw)
            model = _Model([AgentTurn(tool_calls=(ToolCall('read-1', 'read_file', {'path': 'main.py'}),)),
                            AgentTurn(text='done')])
            binding = MemoryBinding('w', 'c', 1, 1, 'p')
            memories = []
            _, messages, _ = run_agent_loop('read', directory, {'id': 'p'}, [], [],
                model_adapter=model, memory_binding=binding, memory_cb=memories.append)
            artifact = memories[-1]['artifacts'][0]
            self.assertEqual(artifact['path'], 'main.py')
            self.assertEqual(artifact['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertNotIn('private_file_body', json.dumps(memories[-1]))
            self.assertIn('private_file_body', json.dumps(messages))

    def test_public_revision_never_hashes_credentials_and_private_routes_do_not_reuse(self):
        config = {'id': 'p', 'model': 'm', 'base_url': 'https://example.invalid/v1', 'token': 'one'}
        revision = model_profile_revision(config)
        self.assertEqual(revision, model_profile_revision({**config, 'token': 'two', 'api_key': 'three'}))
        self.assertNotEqual(revision, model_profile_revision({**config, 'model': 'm2'}))
        self.assertNotEqual(revision, model_profile_revision({**config, 'base_url': 'https://other.invalid/v1'}))
        private = {**config, 'base_url': 'https://user:private@example.invalid/v1?token=private'}
        self.assertNotEqual(model_profile_revision(private), model_profile_revision(private))


class FileReceiptTests(unittest.TestCase):
    def test_raw_read_edit_write_delete_hashes_preserve_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.py'
            before = b'\xef\xbb\xbfvalue = 1\r\n'
            path.write_bytes(before)
            digest = lambda data: hashlib.sha256(data).hexdigest()
            read = execute_tool('read_file', {'path': 'main.py', 'char_count': 3}, directory)
            self.assertEqual(read['sha256'], digest(before))
            self.assertTrue(read['truncated'])
            edit = execute_tool('edit_file', {'path': 'main.py', 'old_str': 'value = 1',
                                            'new_str': 'value = 2'}, directory,
                                confirm_cb=lambda *_: True)
            self.assertTrue(edit['ok'])
            self.assertEqual(path.read_bytes(), b'\xef\xbb\xbfvalue = 2\r\n')
            self.assertEqual(edit['before_sha256'], digest(before))
            self.assertEqual(edit['precondition_sha256'], digest(before))
            self.assertEqual(edit['after_sha256'], digest(path.read_bytes()))
            write = execute_tool('write_file', {'path': 'new.py', 'content': 'x = 3\n'}, directory,
                                 confirm_cb=lambda *_: True)
            self.assertTrue(write['ok'])
            self.assertFalse(write['existed'])
            self.assertIsNone(write['before_sha256'])
            new_bytes = (Path(directory) / 'new.py').read_bytes()
            self.assertEqual(write['after_sha256'], digest(new_bytes))
            delete = execute_tool('delete_file', {'path': 'new.py'}, directory,
                                  confirm_cb=lambda *_: True)
            self.assertEqual(delete['before_sha256'], digest(new_bytes))
            self.assertEqual(delete['precondition_sha256'], digest(new_bytes))
            self.assertIsNone(delete['after_sha256'])
            self.assertFalse((Path(directory) / 'new.py').exists())

    def test_changed_file_during_confirmation_has_no_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.py'
            path.write_text('x = 1\n', encoding='utf-8')

            def confirm(*_):
                path.write_text('external = 2\n', encoding='utf-8')
                return True

            result = execute_tool('edit_file', {'path': 'main.py', 'old_str': 'x = 1',
                                              'new_str': 'x = 3'}, directory, confirm_cb=confirm)
            self.assertFalse(result['ok'])
            self.assertNotIn('after_sha256', result)
            self.assertEqual(path.read_text(encoding='utf-8'), 'external = 2\n')


class MemoryStoreTests(_IsolatedDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.workspace = store.empty_workspace(workspace_dir='synthetic-folder')
        self.conv = self.workspace['conversations'][0]
        self.conv['messages'] = [{'role': 'user', 'content': 'original requirement'}]
        store.save_workspace(self.workspace)

    def commit(self, text='done', **kwargs):
        return store.commit_conversation_result(
            self.workspace['id'], self.conv['id'],
            expected_workspace_revision=self.workspace['workspace_revision'],
            expected_conversation_revision=self.conv['conversation_revision'],
            messages=self.conv['messages'] + [{'role': 'assistant', 'content': text}],
            tool_calls=[], base_message_count=len(self.conv['messages']), **kwargs,
        )

    def test_only_one_concurrent_result_commits_other_is_retained_separately(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.commit, ['first result', 'second result']))
        self.assertEqual(sorted(row['status'] for row in results), ['committed', 'stale'])
        loaded = store.load_workspace(self.workspace['id'])
        self.assertEqual(len(loaded['conversations'][0]['messages']), 2)
        self.assertEqual(len(loaded['conflicted_runs']), 1)
        self.assertEqual(len(loaded['conflicted_runs'][0]['messages']), 1)
        self.assertNotEqual(loaded['conflicted_runs'][0]['messages'][0]['content'],
                            loaded['conversations'][0]['messages'][-1]['content'])

    def test_append_transaction_cannot_overwrite_a_concurrent_worker_result(self):
        import threading
        from unittest.mock import patch
        paused = threading.Event()
        resume = threading.Event()
        real_load = store.load_workspace
        def load_with_pause(session_id):
            loaded = real_load(session_id)
            if threading.current_thread().name.startswith('append-operation'):
                paused.set()
                self.assertTrue(resume.wait(2))
            return loaded
        with patch.object(store, 'load_workspace', side_effect=load_with_pause), \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix='append-operation') as append_pool, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix='worker-result') as commit_pool:
            append = append_pool.submit(store.append_message, self.workspace['id'],
                                        {'role': 'user', 'content': 'new requirement'})
            self.assertTrue(paused.wait(2))
            commit = commit_pool.submit(self.commit)
            resume.set()
            self.assertTrue(append.result(2))
            self.assertEqual(commit.result(2)['status'], 'stale')
        loaded = store.load_workspace(self.workspace['id'])
        self.assertEqual(loaded['conversations'][0]['messages'][-1]['content'], 'new requirement')
        self.assertEqual(loaded['conflicted_runs'][0]['messages'][0]['content'], 'done')

    def test_rename_does_not_invalidate_but_workspace_change_does(self):
        store.rename_conversation(self.workspace['id'], self.conv['id'], 'renamed')
        loaded = store.load_workspace(self.workspace['id'])
        self.assertEqual(loaded['conversations'][0]['conversation_revision'], self.conv['conversation_revision'])
        store.update_workspace(self.workspace['id'], {'workspace_dir': 'changed-folder'})
        self.assertEqual(self.commit()['status'], 'stale')
        loaded = store.load_workspace(self.workspace['id'])
        self.assertEqual(len(loaded['conversations'][0]['messages']), 1)

    def test_tool_record_cap_and_metadata_changes_do_not_cause_spurious_revision(self):
        self.conv['tool_calls'] = [{'id': str(i), 'tool': 'read_file', 'reasoning_content': 'private'}
                                  for i in range(205)]
        store.save_workspace(self.workspace)
        revision = self.conv['conversation_revision']
        store.save_workspace(self.workspace)
        self.assertEqual(self.conv['conversation_revision'], revision)
        self.assertEqual(len(self.conv['tool_calls']), 200)
        self.assertNotIn('reasoning_content', self.conv['tool_calls'][0])

    def test_long_transcript_keeps_first_user_and_complete_native_pair(self):
        self.conv['messages'].extend({'role': 'assistant', 'content': str(i)} for i in range(5000))
        self.conv['messages'].extend([
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'read-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'read-1', 'content': '{}'},
        ])
        store.save_workspace(self.workspace)
        messages = store.load_workspace(self.workspace['id'])['conversations'][0]['messages']
        self.assertEqual(len(messages), 5003)
        self.assertEqual(messages[0]['content'], 'original requirement')
        self.assertEqual(messages[-2]['tool_calls'][0]['id'], messages[-1]['tool_call_id'])

    def test_valid_memory_rebinds_on_commit_and_tampered_memory_is_dropped(self):
        messages = store._clean_message_history(self.conv['messages'] + [{'role': 'assistant', 'content': 'done'}])
        binding = MemoryBinding(self.workspace['id'], self.conv['id'], self.workspace['workspace_revision'],
                                self.conv['conversation_revision'], 'profile')
        memory = build_memory(messages=messages, binding=binding)
        result = self.commit(context_memory=memory.to_dict())
        self.assertEqual(result['status'], 'committed')
        loaded = result['workspace']
        conv = loaded['conversations'][0]
        stored = conv['context_memory']
        self.assertIsNotNone(stored)
        self.assertEqual(stored['binding']['conversation_revision'], conv['conversation_revision'])
        stored['assistant_notes'][0]['excerpt'] = 'fabricated authorization'
        payload = {key: value for key, value in stored.items() if key != 'memory_hash'}
        stored['memory_hash'] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        store.save_workspace(loaded)
        self.assertIsNone(store.load_workspace(self.workspace['id'])['conversations'][0]['context_memory'])


if __name__ == '__main__':
    unittest.main()
