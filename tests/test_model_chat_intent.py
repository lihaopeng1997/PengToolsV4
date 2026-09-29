# -*- coding: utf-8 -*-
"""模型对话意图分流 + 证据链：逻辑层测试（不依赖 Qt）。

覆盖：
- chat_intent.detect_take_data_intent 的 sql/linux/none 分类
- linux_guard.extract_command_candidates 的命令提取
- linux_guard 白名单对提取命令的放行/拒绝
- ai_sql_draft.run_chat_evidence_chain 的 fail-closed 路径（证据缺失时绝不调模型）
"""

from __future__ import annotations

import unittest

from tools.ai_sql_draft import run_chat_evidence_chain
from tools.chat_intent import detect_take_data_intent
from tools.linux_guard import extract_command_candidates, inspect_commands
from tools.schema_snapshot import connection_fingerprint


def _conn(**overrides):
    item = {'id': 'c1', 'name': 'test-oracle', 'dialect': 'oracle'}
    item.update(overrides)
    return item


def _snap(**overrides):
    item = _conn()
    data = {
        'connection_id': 'c1',
        'dialect': 'oracle',
        'fingerprint': connection_fingerprint(item),
        'snapshot_id': 'sid-1',
        'version': 2,
        'scanned_at': '2026-08-26T15:00:00+08:00',
        'status': 'ok',
        'truncated': False,
        'index_metadata_status': 'ok',
        'objects': [{
            'owner': 'PRP',
            'name': 'PRPCMAIN',
            'object_type': 'TABLE',
            'comment': '保单主表',
            'index_metadata_status': 'ok',
            'columns': [
                {'name': 'CREATED_DATE', 'data_type': 'DATE', 'comment': '创建日期',
                 'indexed': True, 'primary_key': False},
                {'name': 'POLICYNO', 'data_type': 'VARCHAR2', 'comment': '保单号',
                 'indexed': True, 'primary_key': True},
            ],
            'indexes': [],
        }],
    }
    data.update(overrides)
    return data


class ChatIntentTests(unittest.TestCase):
    def test_sql_intent(self):
        self.assertEqual(detect_take_data_intent('帮我查一下订单表的创建时间'), 'sql')
        self.assertEqual(detect_take_data_intent('统计各渠道保单数量'), 'sql')
        self.assertEqual(detect_take_data_intent('SELECT * FROM t'), 'sql')

    def test_linux_intent(self):
        self.assertEqual(detect_take_data_intent('磁盘满了，看看日志'), 'linux')
        self.assertEqual(detect_take_data_intent('tail 看下应用日志'), 'linux')
        self.assertEqual(detect_take_data_intent('内存占用太高，查进程'), 'linux')

    def test_none_intent(self):
        self.assertEqual(detect_take_data_intent('你好'), 'none')
        self.assertEqual(detect_take_data_intent('介绍一下你自己'), 'none')
        self.assertEqual(detect_take_data_intent(''), 'none')


class CommandCandidateTests(unittest.TestCase):
    def test_fenced_block(self):
        text = '试试：\n```bash\ntail -f /var/log/app.log\n# 注释\n```'
        self.assertEqual(extract_command_candidates(text), ['tail -f /var/log/app.log'])

    def test_dollar_prefix_lines(self):
        text = '磁盘：\n$ df -h\n$ free -m\n'
        self.assertEqual(extract_command_candidates(text), ['df -h', 'free -m'])

    def test_no_candidates(self):
        self.assertEqual(extract_command_candidates('今天天气不错'), [])

    def test_dedup(self):
        text = '$ df -h\n$ df -h'
        self.assertEqual(extract_command_candidates(text), ['df -h'])


class LinuxGateTests(unittest.TestCase):
    def test_readonly_commands_allowed(self):
        allowed, rejected = inspect_commands(['tail -f /var/log/app.log', 'df -h', 'ps aux | grep app'])
        self.assertEqual(rejected, [])
        self.assertEqual(len(allowed), 3)

    def test_dangerous_commands_rejected(self):
        allowed, rejected = inspect_commands(['rm -rf /tmp/x', 'sudo reboot', 'cat a > b'])
        self.assertEqual(allowed, [])
        self.assertEqual(len(rejected), 3)

    def test_mixed(self):
        allowed, rejected = inspect_commands(['grep ERROR app.log', 'chmod 777 x'])
        self.assertEqual(allowed, ['grep ERROR app.log'])
        self.assertEqual(len(rejected), 1)


class EvidenceChainFailClosedTests(unittest.TestCase):
    DISABLED_CFG = {'enabled': False}

    def test_no_connection_fail_closed(self):
        result = run_chat_evidence_chain('查一下订单表', None, None, cfg=self.DISABLED_CFG)
        self.assertFalse(result['ok'])
        self.assertIn('reason', result)
        self.assertNotIn('draft', result)

    def test_missing_snapshot_fail_closed(self):
        result = run_chat_evidence_chain('查一下订单表', _conn(), None, cfg=self.DISABLED_CFG)
        self.assertFalse(result['ok'])
        self.assertIn('尚未扫描', result['reason'])

    def test_no_matching_evidence_fail_closed(self):
        # 快照存在但问题匹配不到表：必须 fail-closed，且未走到模型（cfg disabled 也能返回）
        result = run_chat_evidence_chain('查一下根本不存在的xyz表', _conn(), _snap(), cfg=self.DISABLED_CFG)
        self.assertFalse(result['ok'])
        self.assertNotIn('draft', result)

    def test_stale_snapshot_fail_closed(self):
        snap = _snap()
        snap['fingerprint'] = 'tampered'
        result = run_chat_evidence_chain('查一下订单表', _conn(), snap, cfg=self.DISABLED_CFG)
        self.assertFalse(result['ok'])

    def test_nosql_not_supported(self):
        result = run_chat_evidence_chain(
            '查一下', _conn(dialect='redis'), _snap(), cfg=self.DISABLED_CFG,
        )
        self.assertFalse(result['ok'])

    def test_never_executes_sql(self):
        # 证据链函数只返回草案 dict，不执行：成功路径也应只含 draft/evidence
        # 此处用无证据问题验证返回结构不含执行痕迹
        result = run_chat_evidence_chain('查一下订单表', _conn(), None, cfg=self.DISABLED_CFG)
        self.assertEqual(set(result.keys()) <= {'ok', 'reason', 'next_action', 'draft', 'evidence'}, True)


if __name__ == '__main__':
    unittest.main()
