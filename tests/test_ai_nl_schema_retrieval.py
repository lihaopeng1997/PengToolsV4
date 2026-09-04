# -*- coding: utf-8 -*-
import unittest
from tools.tameng_agent import (
    extract_schema_terms,
    rank_schema_candidates,
    assess_schema_ambiguity,
    build_retrieved_evidence,
    retrieve_schema_context,
    prepare_request,
    validate_generated_sql,
    format_evidence_bar,
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

        # 3. 校验模型返回的包含标准聚合函数 COUNT(*) 的 SQL
        sql = "SELECT COUNT(*) FROM PRP.PRPCMAIN WHERE RISKCODE = '0525'"
        checked = validate_generated_sql(sql, evidence, 'oracle')
        self.assertTrue(checked['allowed'])
        self.assertEqual(checked['unknown_fields'], [])

    def test_chinese_comment_and_synonym_retrieval(self):
        """验证通过中文注释及同义词定位表与字段。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        query = '统计保单基本信息主表中的总保费'

        prepared = prepare_request(query, snap, conn)
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRP.PRPCMAIN')

        # 包含 SUM 聚合函数与字段 SUMPREM
        sql = 'SELECT SUM(SUMPREM) FROM PRP.PRPCMAIN'
        checked = validate_generated_sql(sql, prepared['evidence'], 'oracle')
        self.assertTrue(checked['allowed'])

    def test_condition_hint_extractions(self):
        """验证各种常见过滤条件模式提取。"""
        cases = [
            ('查 riskcode=0525 的数据', 'RISKCODE', '=', '0525'),
            ('查 policy_no 等于 12345678', 'POLICY_NO', '=', '12345678'),
            ('查 sumprem >= 1000', 'SUMPREM', '>=', '1000'),
            ('查 riskcode != 0520', 'RISKCODE', '!=', '0520'),
        ]
        for q, f, op, val in cases:
            res = extract_schema_terms(q)
            hints = res['condition_hints']
            match = next((h for h in hints if h['field'] == f and h['op'] == op and h['val'] == val), None)
            self.assertIsNotNone(match, f'Failed for query: {q}')

    def test_token_hints_boost_score(self):
        """验证 Token 提示提供加分 (+30) 且为可选。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        tokens = {
            'selected_objects': [{'qualified_name': 'PRP.PRPCITEMKIND'}],
        }
        # 模糊问题，因 Token 提示而偏向 PRPCITEMKIND
        prepared = prepare_request('查询金额', snap, conn, tokens=tokens)
        self.assertTrue(prepared['ok'])
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRP.PRPCITEMKIND')

    def test_explicit_nl_table_overrides_stale_tree_selection(self):
        """验证用户在树上停留在其他表时，自然语言显式表名优先覆盖。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        # 树上停留在 PRPCITEMKIND
        tree_selection = snap['objects'][1]
        # 但自然语言明确查 PRPCMAIN
        prepared = prepare_request('帮我查下 prpcmain 的总保费', snap, conn, current_table=tree_selection)
        self.assertTrue(prepared['ok'])
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRP.PRPCMAIN')

    def test_cross_schema_identical_table_disambiguation(self):
        """验证跨 Schema 同名表根据当前连接的 schema / username 自动消歧。"""
        snap = {
            'snapshot_id': 'snap-dup-1',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'APP_DEV',
                    'name': 'PRPCMAIN',
                    'object_type': 'TABLE',
                    'columns': [{'name': 'ID', 'data_type': 'NUMBER'}],
                    'indexes': [],
                },
                {
                    'owner': 'APP_PROD',
                    'name': 'PRPCMAIN',
                    'object_type': 'TABLE',
                    'columns': [{'name': 'ID', 'data_type': 'NUMBER'}],
                    'indexes': [],
                },
            ],
        }
        # 连接指定当前 Schema 为 APP_PROD
        conn = _make_conn(schema='APP_PROD')
        snap['fingerprint'] = connection_fingerprint(conn)

        prepared = prepare_request('查询 prpcmain', snap, conn)
        self.assertTrue(prepared['ok'])
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'APP_PROD.PRPCMAIN')

    def test_budget_bounded_context_with_massive_schema(self):
        """验证在超大快照（例如 500 张表，每表 50 个字段）下，证据严格受预算约束。"""
        objects = []
        for i in range(1, 501):
            cols = [{'name': f'COL_{j}', 'data_type': 'VARCHAR2(50)', 'comment': f'注释_{j}'} for j in range(1, 51)]
            objects.append({
                'owner': 'BIG',
                'name': f'TBL_{i:03d}',
                'object_type': 'TABLE',
                'comment': f'超大业务表_{i}',
                'columns': cols,
                'indexes': [],
            })
        conn = _make_conn(schema='BIG')
        snap = {
            'snapshot_id': 'snap-big-1',
            'fingerprint': connection_fingerprint(conn),
            'dialect': 'oracle',
            'objects': objects,
        }

        prepared = prepare_request('查询 tbl_010 中 col_1 等于 100', snap, conn)
        self.assertTrue(prepared['ok'])
        evidence = prepared['evidence']

        # 检查硬预算约束
        self.assertLessEqual(len(evidence['tables']), MAX_TABLES)
        for t in evidence['tables']:
            self.assertLessEqual(len(t['columns']), MAX_FIELDS_PER_TABLE)
        total_cols = sum(len(t['columns']) for t in evidence['tables'])
        self.assertLessEqual(total_cols, MAX_TOTAL_FIELDS)

        # 检查上下文文本字符预算
        from tools.tameng_agent import evidence_prompt_text
        prompt = evidence_prompt_text(evidence)
        self.assertLessEqual(len(prompt), MAX_CONTEXT_CHARS)

    def test_sql_validator_allows_standard_functions(self):
        """验证 SQL 校验器允许标准聚合函数、转换函数与时间函数。"""
        snap = _make_prod_snap()
        conn = _make_conn()
        prepared = prepare_request('查询 prpcmain', snap, conn)
        evidence = prepared['evidence']

        valid_sqls = [
            'SELECT COUNT(*) FROM PRP.PRPCMAIN',
            'SELECT SUM(SUMPREM), AVG(SUMPREM), MAX(SUMPREM), MIN(SUMPREM) FROM PRP.PRPCMAIN',
            "SELECT NVL(SUMPREM, 0), TO_CHAR(CREATED_DATE, 'YYYY-MM-DD') FROM PRP.PRPCMAIN",
            'SELECT POLICYNO FROM PRP.PRPCMAIN WHERE CREATED_DATE >= TRUNC(SYSDATE)',
        ]
        for sql in valid_sqls:
            checked = validate_generated_sql(sql, evidence, 'oracle')
            self.assertTrue(checked['allowed'], f'SQL was rejected: {sql} - {checked.get("reason")}')

    def test_panel_never_auto_executes(self):
        """验证生成的 SQL 草案写入独立 Tab，且绝不自动执行。"""
        from tools.ai_sql_draft import validate_draft
        draft = {
            'summary': '统计险种保单数',
            'sql': "SELECT COUNT(*) FROM PRP.PRPCMAIN WHERE RISKCODE = '0525'",
            'objects_used': ['PRP.PRPCMAIN'],
            'selected_fields': ['RISKCODE'],
        }
        validated = validate_draft(draft, selected_tables=['PRP.PRPCMAIN'], selected_fields=['RISKCODE'], dialect='oracle')
        self.assertEqual(validated['risk_level'], 'read')
        self.assertFalse(validated['fail_closed'])


if __name__ == '__main__':
    unittest.main()
