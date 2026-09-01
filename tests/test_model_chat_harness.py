# -*- coding: utf-8 -*-
"""棒2 模型对话接入 Harness 定向测试（离屏）。

覆盖：意图分流、无连接/未扫描兜底、SQL 证据链、Linux 只读门禁、通用聊天不拦截。
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class IntentTests(unittest.TestCase):
    def test_intent_routing(self):
        from tools.chat_intent import detect_take_data_intent
        self.assertEqual(detect_take_data_intent('写一段周报'), 'none')
        self.assertEqual(detect_take_data_intent('你好，介绍一下你自己'), 'none')
        self.assertEqual(detect_take_data_intent('查车险主表创建日期'), 'sql')
        self.assertEqual(detect_take_data_intent('统计一下各表的记录数'), 'sql')
        self.assertEqual(detect_take_data_intent('查最近错误日志'), 'linux')
        self.assertEqual(detect_take_data_intent('看看磁盘和内存占用'), 'linux')


class LinuxGuardTests(unittest.TestCase):
    def test_inspect_commands_rejects_danger(self):
        from tools.linux_guard import inspect_commands
        allowed, rejected = inspect_commands(['tail -n 100 app.log', 'rm -rf /tmp/x', 'sudo reboot'])
        self.assertIn('tail -n 100 app.log', allowed)
        rejected_cmds = [cmd for cmd, _ in rejected]
        self.assertIn('rm -rf /tmp/x', rejected_cmds)
        self.assertIn('sudo reboot', rejected_cmds)

    def test_inspect_commands_rejects_redirect(self):
        from tools.linux_guard import inspect_commands
        allowed, rejected = inspect_commands(['echo hi > /tmp/a'])
        self.assertEqual(allowed, [])
        self.assertEqual(len(rejected), 1)


class ModelChatPanelHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from tools import model_chat_store, db_connect, schema_snapshot
        self._store_patch = patch.object(model_chat_store, 'MODEL_CHAT_DIR', self.tmp.name)
        self._store_patch.start()
        self.addCleanup(self._store_patch.stop)
        self._conn_patch = patch.object(db_connect, 'HARNESS_CONNECTIONS_FILE',
                                        os.path.join(self.tmp.name, 'connections.json'))
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)
        self._snap_patch = patch.object(schema_snapshot, 'SCHEMA_SNAPSHOT_DIR',
                                        os.path.join(self.tmp.name, 'snapshots'))
        self._snap_patch.start()
        self.addCleanup(self._snap_patch.stop)

    def _make_panel(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        from panels.model_chat_panel import ModelChatPanel
        self.panel = ModelChatPanel(language='zh')
        return self.panel

    def _seed_connection_and_snapshot(self):
        from tools.db_connect import upsert_connection
        from tools.schema_snapshot import empty_snapshot, save_snapshot
        item = upsert_connection({
            'id': 'conn1', 'name': '车险库', 'dialect': 'oracle',
            'host': '192.168.1.10', 'port': 1521, 'database': 'orcl', 'username': 'app',
        })
        snap = empty_snapshot(item, status='ok')
        snap['scanned_at'] = '2026-08-27T08:00:00+08:00'
        snap['objects'] = [{
            'owner': 'APP', 'name': 'prpmain', 'object_type': 'TABLE',
            'comment': '车险主表', 'inferred': False,
            'columns': [
                {'name': 'CREATE_DATE', 'data_type': 'DATE', 'nullable': True,
                 'position': 1, 'comment': '创建日期', 'primary_key': False, 'indexed': True},
                {'name': 'POLICY_NO', 'data_type': 'VARCHAR2', 'nullable': True,
                 'position': 2, 'comment': '保单号', 'primary_key': False, 'indexed': False},
            ],
            'indexes': [{'name': 'IDX_CREATE', 'unique': False, 'index_type': 'NORMAL',
                         'columns': [{'name': 'CREATE_DATE', 'position': 1}]}],
            'index_metadata_status': 'ok',
        }]
        snap['version'] = 2
        save_snapshot(snap)
        return item

    def test_sql_chain_end_to_end(self):
        from unittest.mock import patch as _patch
        item = self._seed_connection_and_snapshot()
        from tools.schema_snapshot import load_snapshot
        from tools.ai_sql_draft import generate_sql_draft
        snap = load_snapshot(item['id'])
        evidence = {
            'tables': [{'qualified_name': 'APP.prpmain', 'object_type': 'TABLE', 'comment': '车险主表', 'columns': [{'name': 'CREATE_DATE', 'data_type': 'DATE', 'comment': '创建日期'}]}],
            'confirmed_fields': ['CREATE_DATE'],
        }
        model = {'id': 'm1', 'name': '测试模型', 'model': 'qwen', 'enabled': True, 'base_url': 'http://127.0.0.1:8000/v1'}
        with _patch('tools.ai_sql_draft.chat_completions',
                    return_value='{"summary":"ok","sql":"SELECT CREATE_DATE FROM prpmain"}'):
            draft = generate_sql_draft(question='查车险主表创建日期', snapshot=snap, evidence=evidence, cfg=model)
        self.assertEqual(draft.get('summary'), 'ok')
        self.assertIn('CREATE_DATE', draft.get('sql', ''))
        self.assertIn('prpmain', draft.get('sql', '').lower())

    def test_sql_chain_rejects_unknown_field(self):
        from unittest.mock import patch as _patch
        item = self._seed_connection_and_snapshot()
        from tools.schema_snapshot import load_snapshot
        from tools.ai_sql_draft import generate_sql_draft, validate_draft
        snap = load_snapshot(item['id'])
        evidence = {
            'tables': [{'qualified_name': 'APP.prpmain', 'object_type': 'TABLE', 'comment': '车险主表', 'columns': [{'name': 'CREATE_DATE', 'data_type': 'DATE', 'comment': '创建日期'}]}],
            'confirmed_fields': ['CREATE_DATE'],
        }
        model = {'id': 'm1', 'name': '测试模型', 'model': 'qwen', 'enabled': True, 'base_url': 'http://127.0.0.1:8000/v1'}
        with _patch('tools.ai_sql_draft.chat_completions',
                    return_value='{"summary":"ok","sql":"SELECT NOT_A_REAL_FIELD FROM prpmain"}'):
            draft = generate_sql_draft(question='查车险主表创建日期', snapshot=snap, evidence=evidence, selected_fields=['CREATE_DATE'], cfg=model)
        validated = validate_draft(draft, selected_tables=['prpmain'], selected_fields=['CREATE_DATE'])
        joined = ' '.join(validated.get('warnings') or [])
        self.assertIn('NOT_A_REAL_FIELD', joined)


if __name__ == '__main__':
    unittest.main()
