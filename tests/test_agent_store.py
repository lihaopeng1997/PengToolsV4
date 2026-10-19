# -*- coding: utf-8 -*-
"""Agent 工作台存储层定向测试：V1→V2 迁移 + 空间/对话二级 CRUD。"""
import os
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import agent_store as ag
from tools.agent_runtime import run_agent_loop


def _frame(payload):
    return ('data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode('utf-8')


def _native_tool_turn(call_id, name, arguments):
    return [
        _frame({'model': 'store-fake', 'choices': [{'delta': {'tool_calls': [{
            'index': 0,
            'id': call_id,
            'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)},
        }]}}]}),
        _frame({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}),
        b'data: [DONE]\n\n',
    ]


def _text_turn(text):
    return [
        _frame({'model': 'store-fake', 'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}]}),
        b'data: [DONE]\n\n',
    ]


class _SequenceTransport:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def stream(self, body, *, cancellation):
        self.requests.append(dict(body))
        return iter(self.turns.pop(0))


def _model_config():
    return {
        'id': 'store-model',
        'enabled': True,
        'base_url': 'http://127.0.0.1:8000/v1',
        'model': 'store-fake',
        'token': 'synthetic-store-token',
        'agent_capability': 'native_tools',
        'timeout_seconds': 2,
        'max_tokens': 512,
        'include_usage': False,
    }


class _IsolatedDirMixin:
    """把 agent 数据目录重定向到临时目录，避免污染开发态 data/。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Redirect every store operation before creating paths; these tests
        # must not touch a developer's real config or data directories.
        self.patches = [
            patch.object(ag, 'AGENT_WORKSPACES_DIR', os.path.join(self._tmp.name, 'workspaces')),
            patch.object(ag, 'AGENT_INDEX_FILE', os.path.join(self._tmp.name, 'index.json')),
            patch.object(ag, 'ensure_config_dir', lambda: None),
        ]
        for p in self.patches:
            p.start()
        os.makedirs(ag.AGENT_WORKSPACES_DIR, exist_ok=True)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()


class WorkspaceV1MigrationTests(_IsolatedDirMixin, unittest.TestCase):
    def test_v1_top_level_messages_migrate_to_conversations(self):
        """V1 顶层 messages/tool_calls 自动迁移为 conversations[0]，不丢数据。"""
        old = {
            'id': 'v1-ws', 'type': 'workspace', 'title': '旧空间',
            'workspace_dir': '', 'plan_confirm': False,
            'messages': [
                {'role': 'user', 'content': '你好'},
                {'role': 'assistant', 'content': 'Hi'},
            ],
            'tool_calls': [
                {'id': 't1', 'tool': 'read_file', 'args': {}, 'result': 'ok', 'error': '', 'timestamp': 'x'},
            ],
            'created_at': '2026-01-01T00:00:00', 'updated_at': '2026-01-01T00:00:00',
        }
        ag.save_workspace(old)
        loaded = ag.load_workspace('v1-ws')
        convs = loaded.get('conversations') or []
        self.assertEqual(len(convs), 1)
        self.assertEqual(len(convs[0].get('messages') or []), 2)
        self.assertEqual(convs[0]['messages'][0]['content'], '你好')
        self.assertEqual(len(convs[0].get('tool_calls') or []), 1)
        # active 指向迁移出的对话
        self.assertEqual(loaded.get('active_conv_id'), convs[0]['id'])

    def test_empty_workspace_has_default_conversation(self):
        ws = ag.empty_workspace(title='新空间')
        self.assertEqual(len(ws.get('conversations') or []), 1)
        self.assertEqual(ws['active_conv_id'], ws['conversations'][0]['id'])


class ConversationCrudTests(_IsolatedDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        ws = ag.empty_workspace(title='空间A')
        ag.save_workspace(ws)
        self.ws_id = ws['id']

    def _reload(self):
        return ag.load_workspace(self.ws_id)

    def test_create_conversation_sets_active(self):
        ws, cid = ag.create_conversation(self.ws_id, '第二个对话')
        self.assertTrue(cid)
        self.assertEqual(len(ws.get('conversations') or []), 2)
        self.assertEqual(ws.get('active_conv_id'), cid)
        self.assertEqual(self._reload().get('active_conv_id'), cid)

    def test_delete_conversation_reassigns_active(self):
        _, cid = ag.create_conversation(self.ws_id, '待删对话')
        self.assertTrue(ag.delete_conversation(self.ws_id, cid))
        ws = self._reload()
        self.assertEqual(len(ws.get('conversations') or []), 1)
        # active 回落到剩余第一个
        self.assertEqual(ws.get('active_conv_id'), ws['conversations'][0]['id'])

    def test_append_message_targets_active_conversation(self):
        _, cid = ag.create_conversation(self.ws_id, '当前对话')
        ok = ag.append_message(self.ws_id, {'role': 'user', 'content': '新消息'}, conv_id=cid)
        self.assertTrue(ok)
        ws = self._reload()
        conv = next(c for c in ws['conversations'] if c['id'] == cid)
        self.assertEqual(conv['messages'][-1]['content'], '新消息')

    def test_rename_and_list_conversations(self):
        _, cid = ag.create_conversation(self.ws_id, '旧名')
        ag.rename_conversation(self.ws_id, cid, '新名')
        convs = ag.list_conversations(self.ws_id)
        titles = {c['title'] for c in convs}
        self.assertIn('新名', titles)

    def test_load_conversation_returns_messages(self):
        _, cid = ag.create_conversation(self.ws_id, '带消息')
        ag.append_message(self.ws_id, {'role': 'user', 'content': 'A'}, conv_id=cid)
        conv = ag.load_conversation(self.ws_id, cid)
        self.assertEqual(conv['messages'][0]['content'], 'A')

    def test_native_tool_pair_roundtrips_and_is_sent_on_second_real_model_request(self):
        with tempfile.TemporaryDirectory() as project:
            with open(os.path.join(project, 'main.py'), 'w', encoding='utf-8') as stream:
                stream.write('value = 5\n')
            first_transport = _SequenceTransport([
                _native_tool_turn('persisted-read-1', 'read_file', {'path': 'main.py'}),
                _text_turn('第一轮已读取。'),
            ])
            first_final, first_messages, first_calls = run_agent_loop(
                '读取 main.py', project, _model_config(), [], [],
                model_transport=first_transport,
            )
            self.assertEqual(first_final, '第一轮已读取。')
            self.assertEqual(len(first_calls), 1)

            session = ag.load_workspace(self.ws_id)
            conversation = next(item for item in session['conversations'] if item['id'] == session['active_conv_id'])
            conversation['messages'] = first_messages
            conversation['tool_calls'] = first_calls
            ag.save_workspace(session)

            restored = ag.load_conversation(self.ws_id, conversation['id'])
            call_message = next(item for item in restored['messages'] if item.get('tool_calls'))
            tool_message = next(item for item in restored['messages'] if item.get('role') == 'tool')
            self.assertEqual(call_message['tool_calls'][0], {
                'id': 'persisted-read-1',
                'type': 'function',
                'function': {
                    'name': 'read_file',
                    'arguments': '{"path":"main.py"}',
                },
            })
            self.assertEqual(tool_message['tool_call_id'], 'persisted-read-1')

            second_transport = _SequenceTransport([_text_turn('第二轮已继续。')])
            second_final, _second_messages, _second_calls = run_agent_loop(
                '继续', project, _model_config(), restored['messages'], restored['tool_calls'],
                model_transport=second_transport,
            )
            self.assertEqual(second_final, '第二轮已继续。')
            sent_messages = second_transport.requests[0]['messages']
            sent_call = next(item for item in sent_messages if item.get('tool_calls'))
            sent_result = next(item for item in sent_messages if item.get('role') == 'tool')
            self.assertEqual(sent_call['tool_calls'][0]['id'], sent_result['tool_call_id'])
            self.assertIn('value = 5', sent_result['content'])
            self.assertFalse(any({'id', 'created_at', 'updated_at'} & set(item) for item in sent_messages))

    def test_bounded_request_history_never_starts_with_orphaned_tool_result(self):
        with tempfile.TemporaryDirectory() as project:
            messages = [
                {'role': 'user', 'content': 'older context'},
                {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'cut-at-context-boundary',
                    'type': 'function',
                    'function': {'name': 'read_file', 'arguments': '{"path":"main.py"}'},
                }]},
                {'role': 'tool', 'tool_call_id': 'cut-at-context-boundary', 'content': '{"ok":true}'},
            ]
            for index in range(14):
                messages.extend([
                    {'role': 'user', 'content': f'question {index}'},
                    {'role': 'assistant', 'content': f'answer {index}'},
                ])
            messages.append({'role': 'user', 'content': '继续'})
            self.assertEqual(len(messages), 32)

            transport = _SequenceTransport([_text_turn('继续完成。')])
            final, _messages, _calls = run_agent_loop(
                '继续', project, _model_config(), messages, [], model_transport=transport,
            )
            self.assertEqual(final, '继续完成。')
            sent_messages = transport.requests[0]['messages']
            self.assertIn('older context', [item.get('content') for item in sent_messages])
            sent_call = next(item for item in sent_messages if item.get('tool_calls'))
            sent_result = next(item for item in sent_messages if item.get('role') == 'tool')
            self.assertEqual(sent_call['tool_calls'][0]['id'], sent_result['tool_call_id'])
            self.assertEqual(sent_result['content'], '{"ok":true}')
            self.assertEqual(sent_messages[-1]['content'], '继续')

    def test_orphan_and_incomplete_tool_history_is_not_sent_or_saved(self):
        session = ag.load_workspace(self.ws_id)
        conversation = session['conversations'][0]
        conversation['messages'] = [
            {'role': 'tool', 'tool_call_id': 'orphan', 'content': 'orphan result'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'missing-result', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{"path":"main.py"}'},
            }]},
            {'role': 'tool', 'tool_call_id': 'wrong-id', 'content': 'wrong result'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'invalid-json', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{"path":"one","path":"two"}'},
            }]},
            {'role': 'tool', 'tool_call_id': 'invalid-json', 'content': 'invalid args result'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'invalid-unicode', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{"path":"\\ud800"}'},
            }]},
            {'role': 'tool', 'tool_call_id': 'invalid-unicode', 'content': 'invalid unicode result'},
            {'role': 'assistant', 'content': 'ordinary answer'},
        ]
        ag.save_workspace(session)
        restored = ag.load_conversation(self.ws_id, conversation['id'])
        self.assertEqual(
            [(item['role'], item.get('content')) for item in restored['messages']],
            [('assistant', 'ordinary answer')],
        )


if __name__ == '__main__':
    unittest.main()
