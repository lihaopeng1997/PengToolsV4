# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from tools.tameng_agent import (
    extract_schema_terms,
    rank_schema_candidates,
    assess_schema_ambiguity,
    build_retrieved_evidence,
    build_evidence_context,
    retrieve_schema_context,
    prepare_request,
    validate_generated_sql,
    format_evidence_bar,
    evidence_prompt_text,
    MAX_TABLES,
    MAX_FIELDS_PER_TABLE,
    MAX_TOTAL_FIELDS,
    MAX_CONTEXT_CHARS,
)
from tools.schema_snapshot import connection_fingerprint


def _make_conn(dialect='oracle', schema='PRP', name='test-conn'):
    return {
        'id': 'c1',
        'name': name,
        'dialect': dialect,
        'host': '127.0.0.1',
        'port': 1521,
        'database': 'orcl',
        'schema': schema,
        'username': schema,
    }


def _make_prod_snap():
    conn = _make_conn()
    return {
        'snapshot_id': 'snap-prod-1',
        'fingerprint': connection_fingerprint(conn),
        'dialect': 'oracle',
        'scanned_at': '2026-09-04 08:00:00',
        'objects': [
            {
                'owner': 'PRP',
                'name': 'PRPCMAIN',
                'object_type': 'TABLE',
                'comment': '保单基本信息主表',
                'columns': [
                    {'name': 'POLICYNO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号', 'primary_key': True},
                    {'name': 'RISKCODE', 'data_type': 'VARCHAR2(10)', 'comment': '险种代码', 'indexed': True},
                    {'name': 'SUMPREM', 'data_type': 'NUMBER(14,2)', 'comment': '总保费'},
                    {'name': 'APPLI_NAME', 'data_type': 'VARCHAR2(120)', 'comment': '投保人名称'},
                    {'name': 'INSURED_NAME', 'data_type': 'VARCHAR2(120)', 'comment': '被保险人名称'},
                    {'name': 'START_DATE', 'data_type': 'DATE', 'comment': '起保日期'},
                    {'name': 'END_DATE', 'data_type': 'DATE', 'comment': '终保日期'},
                    {'name': 'CREATED_DATE', 'data_type': 'DATE', 'comment': '创建日期', 'indexed': True},
                ],
                'indexes': [
                    {'name': 'PK_PRPCMAIN', 'columns': ['POLICYNO']},
                    {'name': 'IDX_PRPCMAIN_RISK', 'columns': ['RISKCODE']},
                ],
            },
            {
                'owner': 'PRP',
                'name': 'PRPCITEMKIND',
                'object_type': 'TABLE',
                'comment': '保单险别标的表',
                'columns': [
                    {'name': 'POLICYNO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'},
                    {'name': 'KINDCODE', 'data_type': 'VARCHAR2(10)', 'comment': '险别代码'},
                    {'name': 'AMOUNT', 'data_type': 'NUMBER(14,2)', 'comment': '保险金额'},
                ],
                'indexes': [],
            },
        ],
    }


class AiNlSchemaRetrievalTests(unittest.TestCase):

    def test_build_evidence_context_no_name_error(self):
        """Item 1: 验证 build_evidence_context 直接调用不报 NameError (base_d)。"""
        snap = _make_prod_snap()
        resolution = {
            'objects': [{'object': snap['objects'][0]}],
            'fields': [{'object': snap['objects'][0], 'column': snap['objects'][0]['columns'][0]}],
        }
        res = build_evidence_context(resolution, snap, dialect='oracle', oceanbase_mode='')
        self.assertEqual(res['effective_dialect'], 'oracle')
        self.assertEqual(len(res['tables']), 1)

    def test_production_incident_nl_retrieval_without_tokens(self):
        """验证生产典型场景：用户未点击任何 Token，纯自然语言提问生成 SQL 草案。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        query = '帮我查下 prpcmain 中 riskcode 等于 0525 的数据有多少条'

        # 1. 提取结构术语与条件
        terms = extract_schema_terms(query)
        self.assertIn('prpcmain', terms['identifiers'])
        self.assertIn('riskcode', terms['identifiers'])
        self.assertTrue(terms['aggregate'])
        self.assertTrue(any(
            h.get('field') == 'RISKCODE' and h.get('op') == '=' and h.get('val') == '0525'
            for h in terms['condition_hints']
        ))

        # 2. 端到端准备请求
        prepared = prepare_request(query, snap, conn)
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        self.assertEqual(prepared['state'], 'READY')

        evidence = prepared['evidence']
        self.assertEqual(len(evidence['tables']), 1)
        self.assertEqual(evidence['tables'][0]['qualified_name'], 'PRP.PRPCMAIN')

        # 验证证据条与置信度显示
        bar = format_evidence_bar(evidence)
        self.assertIn('PRP.PRPCMAIN', bar)
        self.assertIn('高置信', bar)
        self.assertIn('RISKCODE', bar)

        # Item 5: 验证 VARCHAR2 字段 0525 保留引号与前导零
        prompt_str = evidence_prompt_text(evidence)
        self.assertIn("RISKCODE = '0525'", prompt_str)

        # 3. 校验模型返回的包含标准聚合函数 COUNT(*) 的 SQL
        sql = "SELECT COUNT(*) FROM PRP.PRPCMAIN WHERE RISKCODE = '0525'"
        checked = validate_generated_sql(sql, evidence, 'oracle')
        self.assertTrue(checked['allowed'])
        self.assertEqual(checked['unknown_fields'], [])

    def test_current_table_is_ranking_hint_not_hard_selector(self):
        """Item 3: 树停在 T_CLAIM，问题查保单主表中的险种代码，T_POLICY 必须基于排名胜出。"""
        snap = {
            'snapshot_id': 's1',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [
                        {'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'},
                        {'name': 'RISK_CODE', 'data_type': 'VARCHAR2(10)', 'comment': '险种代码'},
                    ],
                    'indexes': [],
                },
                {
                    'owner': 'PRPCAR',
                    'name': 'T_CLAIM',
                    'object_type': 'TABLE',
                    'comment': '理赔主表',
                    'columns': [
                        {'name': 'CLAIM_NO', 'data_type': 'VARCHAR2(30)', 'comment': '赔案号'},
                    ],
                    'indexes': [],
                },
            ],
        }
        conn = _make_conn(dialect='oracle', schema='PRPCAR')
        snap['fingerprint'] = connection_fingerprint(conn)

        # 当前树选中 T_CLAIM
        current_table = snap['objects'][1]
        self.assertEqual(current_table['name'], 'T_CLAIM')

        # 用户自然语言输入包含保单主表和险种代码
        prepared = prepare_request('统计保单主表中的险种代码', snap, conn, current_table=current_table)
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        # T_POLICY 必须胜出！而不是被 current_table 硬覆盖为 T_CLAIM
        self.assertEqual(len(prepared['evidence']['tables']), 1)
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRPCAR.T_POLICY')

    def test_sql_validator_fails_closed_on_unknown_function(self):
        """Item 4: 合法函数通过，未知函数 fail-closed 拦截。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        prepared = prepare_request('查询 prpcmain', snap, conn)
        evidence = prepared['evidence']

        # 合法函数 PASS
        pass_sqls = [
            'SELECT COUNT(RISKCODE) FROM PRP.PRPCMAIN',
            "SELECT NVL(RISKCODE, '') FROM PRP.PRPCMAIN",
        ]
        for sql in pass_sqls:
            checked = validate_generated_sql(sql, evidence, 'oracle')
            self.assertTrue(checked['allowed'], f'Expected allowed for {sql}')

        # 未知函数 FAIL
        bad_sql = 'SELECT TOTALLY_UNKNOWN_FUNCTION(RISKCODE) FROM PRP.PRPCMAIN'
        bad_check = validate_generated_sql(bad_sql, evidence, 'oracle')
        self.assertFalse(bad_check['allowed'])
        self.assertIn('TOTALLY_UNKNOWN_FUNCTION', bad_check['unknown_fields'])

    def test_condition_literal_varchar_leading_zero(self):
        """Item 5: 验证条件值按字段类型格式化，VARCHAR 保留引号与前导零，数值不加引号。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        prepared = prepare_request('查询 prpcmain 中 riskcode 等于 0525 且 sumprem >= 1000', snap, conn)
        self.assertTrue(prepared['ok'])
        evidence = prepared['evidence']
        prompt = evidence_prompt_text(evidence)
        # RISKCODE VARCHAR2 => '0525'
        self.assertIn("RISKCODE = '0525'", prompt)
        # SUMPREM NUMBER => 1000
        self.assertIn("SUMPREM >= 1000", prompt)

    def test_nosql_redis_and_mongo_isolated(self):
        """Item 7: Redis 和 Mongo 隔离，不进入关系型检索。"""
        snap = _make_prod_snap()
        conn_redis = {'id': 'r1', 'dialect': 'redis', 'name': 'my-redis'}
        conn_mongo = {'id': 'm1', 'dialect': 'mongo', 'name': 'my-mongo'}

        prep_r = prepare_request('查询数据', snap, conn_redis)
        self.assertFalse(prep_r['ok'])
        self.assertEqual(prep_r['state'], 'NOSQL_NOT_SUPPORTED')

        prep_m = prepare_request('查询数据', snap, conn_mongo)
        self.assertFalse(prep_m['ok'])
        self.assertEqual(prep_m['state'], 'NOSQL_NOT_SUPPORTED')

    def test_pathological_table_comment_hard_cap(self):
        """Item 8: 单表超长注释（20,000 字符），最终 prompt 必须 <= MAX_CONTEXT_CHARS (12,000)。"""
        conn = _make_conn()
        huge_comment = '超长业务表注释' + ('X' * 20000)
        snap = {
            'snapshot_id': 'snap-huge-1',
            'fingerprint': connection_fingerprint(conn),
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRP',
                    'name': 'PRPCMAIN',
                    'object_type': 'TABLE',
                    'comment': huge_comment,
                    'columns': [
                        {'name': 'POLICYNO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'},
                        {'name': 'RISKCODE', 'data_type': 'VARCHAR2(10)', 'comment': '险种代码'},
                    ],
                    'indexes': [],
                }
            ],
        }
        prepared = prepare_request('查询 prpcmain', snap, conn)
        self.assertTrue(prepared['ok'])
        evidence = prepared['evidence']
        prompt = evidence_prompt_text(evidence)
        self.assertLessEqual(len(prompt), MAX_CONTEXT_CHARS)
        # 仍保留表与字段名
        self.assertIn('PRP.PRPCMAIN', prompt)
        self.assertIn('RISKCODE', prompt)

    def test_datatype_first_numeric_and_varchar_formatting(self):
        """Review Fix 2: 验证数据类型第一优先级（VARCHAR 引号+前导零，NUMBER 去前导零纯数字，未知类型保守加引号）。"""
        from tools.tameng_agent import format_condition_hint
        from tools.ai_sql_draft import _format_condition_hint

        # 验证两处为同一单源 formatter
        self.assertIs(_format_condition_hint, format_condition_hint)

        tables = [{
            'qualified_name': 'PRP.PRPCMAIN',
            'columns': [
                {'name': 'RISKCODE', 'data_type': 'VARCHAR2(10)'},
                {'name': 'SEQNO', 'data_type': 'NUMBER(10)'},
                {'name': 'SUMPREM', 'data_type': 'NUMBER(14,2)'},
            ],
        }]

        # A. VARCHAR2: RISKCODE = 0525 -> '0525'
        h_a = {'field': 'RISKCODE', 'op': '=', 'val': '0525'}
        self.assertEqual(format_condition_hint(h_a, tables), "RISKCODE = '0525'")

        # B. NUMBER: SEQNO = 0525 -> 525 (leading zero stripped for true numeric type)
        h_b = {'field': 'SEQNO', 'op': '=', 'val': '0525'}
        self.assertEqual(format_condition_hint(h_b, tables), "SEQNO = 525")

        # C. NUMBER: SUMPREM >= 1000 -> SUMPREM >= 1000
        h_c = {'field': 'SUMPREM', 'op': '>=', 'val': '1000'}
        self.assertEqual(format_condition_hint(h_c, tables), "SUMPREM >= 1000")

        # D. unknown datatype: CODE = 0525 -> CODE = '0525' (conservative quoted)
        h_d = {'field': 'CODE', 'op': '=', 'val': '0525'}
        self.assertEqual(format_condition_hint(h_d, tables), "CODE = '0525'")

    def test_pathological_condition_value_hard_cap(self):
        """Review Fix 2: 超长条件值（20,000 字符），prompt 与 safe_context 必须 <= MAX_CONTEXT_CHARS 并保留关键标识符。"""
        from tools.tameng_agent import bounded_evidence_prompt_text
        from tools.ai_sql_draft import build_safe_context

        huge_val = 'A' * 20000
        evidence = {
            'dialect': 'oracle',
            'snapshot_id': 'snap-huge-val',
            'scanned_at': '2026-09-04 08:00:00',
            'confirmed_fields': ['PRP.PRPCMAIN.RISKCODE'],
            'tables': [
                {
                    'qualified_name': 'PRP.PRPCMAIN',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [
                        {'name': 'POLICYNO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'},
                        {'name': 'RISKCODE', 'data_type': 'VARCHAR2(10)', 'comment': '险种代码'},
                    ],
                    'indexes': [{'name': 'PK_PRPCMAIN', 'columns': ['POLICYNO']}],
                }
            ],
            'condition_hints': [
                {'field': 'RISKCODE', 'op': '=', 'val': huge_val},
            ],
        }

        # E. evidence_prompt_text / bounded_evidence_prompt_text <= MAX_CONTEXT_CHARS
        prompt = evidence_prompt_text(evidence)
        self.assertLessEqual(len(prompt), MAX_CONTEXT_CHARS)
        self.assertEqual(prompt, bounded_evidence_prompt_text(evidence))

        # F. 超限后仍包含关键标识符
        self.assertIn('PRP.PRPCMAIN', prompt)
        self.assertIn('RISKCODE', prompt)

        # 验证 build_safe_context 不重复展开 20k 条件或膨胀 Schema
        safe_ctx = build_safe_context(
            dialect='oracle',
            alias='prod-db',
            question='查询 prpcmain',
            action='generate',
            evidence=evidence,
        )
        self.assertLessEqual(len(safe_ctx), MAX_CONTEXT_CHARS + 500)
        self.assertIn('PRP.PRPCMAIN', safe_ctx)
        self.assertIn('RISKCODE', safe_ctx)
        self.assertNotIn(huge_val, safe_ctx)

    def test_panel_no_token_true_e2e(self):
        """Item 6: 真实实例化 AiWorkbenchPanel 跑无 Token 自然语言生成 E2E，验证绝不自动执行。"""
        app = QApplication.instance() or QApplication([])
        from panels.ai_workbench_panel import AiWorkbenchPanel
        panel = AiWorkbenchPanel()
        snap = _make_prod_snap()
        conn = _make_conn()

        # 模拟已连接与已加载快照
        panel._browse_conn = MagicMock(return_value=conn)
        panel._snapshot = snap
        panel.nl_input.clear()
        panel.nl_input.setPlainText('帮我查下 prpcmain 中 riskcode 等于 0525 的数据有多少条')
        # 确保 context tokens 为空
        panel.nl_input.context['selected_objects'] = []
        panel.nl_input.context['selected_fields'] = []

        # Mock 关键状态与分支追踪
        panel._show_agent_candidates = MagicMock()
        panel._block_agent = MagicMock()
        panel._start_agent_task = MagicMock()
        panel._run_sql = MagicMock()
        panel.run_console_statement = MagicMock()

        with patch('panels.ai_workbench_panel.is_enabled', return_value=True):
            panel._run_ai('generate')

        # 验证：未限定 token 高置信直接进 task，不弹候选，不拦截
        panel._show_agent_candidates.assert_not_called()
        panel._block_agent.assert_not_called()
        panel._start_agent_task.assert_called_once()

        # 模拟 Worker 返回 draft
        fake_draft = {
            'summary': '统计险种0525保单数',
            'sql': "SELECT COUNT(*) FROM PRP.PRPCMAIN WHERE RISKCODE = '0525'",
            'objects_used': ['PRP.PRPCMAIN'],
            'selected_fields': ['RISKCODE'],
        }
        panel._on_ai_ok(fake_draft)

        # 验证：新建未执行 Tab，绝不自动执行 SQL
        self.assertEqual(panel.result_status.text(), 'SQL 草案 · 未执行')
        panel._run_sql.assert_not_called()
        panel.run_console_statement.assert_not_called()
        curr_editor = panel._current_editor()
        self.assertIsNotNone(curr_editor)
        self.assertIn('SELECT COUNT(*)', curr_editor.toPlainText())


if __name__ == '__main__':
    unittest.main()
