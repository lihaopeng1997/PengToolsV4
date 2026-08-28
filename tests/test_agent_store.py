# -*- coding: utf-8 -*-
"""Agent 工作台存储层定向测试：V1→V2 迁移 + 空间/对话二级 CRUD。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import agent_store as ag


class _IsolatedDirMixin:
    """把 agent 数据目录重定向到临时目录，避免污染开发态 data/。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # 隔离 AGENT_WORKSPACES_DIR / AGENT_INDEX_FILE 所依赖的根目录
        self.patches = [
            patch.object(ag, 'AGENT_WORKSPACES_DIR', os.path.join(self._tmp.name, 'workspaces')),
            patch.object(ag, 'AGENT_INDEX_FILE', os.path.join(self._tmp.name, 'index.json')),
        ]
        os.makedirs(ag.AGENT_WORKSPACES_DIR, exist_ok=True)
        for p in self.patches:
            p.start()

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


if __name__ == '__main__':
    unittest.main()
