# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _conn():
    return {'id': 'c1', 'name': 'quote-test', 'dialect': 'oracle', 'host': 'h', 'port': 1521, 'database': 'orcl', 'username': 'u'}


def _snap(**overrides):
    from tools.schema_snapshot import connection_fingerprint
    item = _conn()
    data = {
        'connection_id': 'c1',
        'alias': 'quote-test',
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
                {'name': 'CREATED_DATE', 'data_type': 'DATE', 'comment': '创建日期', 'indexed': True, 'primary_key': False},
                {'name': 'POLICYNO', 'data_type': 'VARCHAR2', 'comment': '保单号', 'indexed': True, 'primary_key': True},
            ],
            'indexes': [{
                'name': 'IDX_PRPCMAIN_CREATED_DATE',
                'unique': False,
                'index_type': 'NORMAL',
                'columns': [{'name': 'CREATED_DATE', 'position': 1}],
            }],
        }],
    }
    data.update(overrides)
    return data


class TamengAgentTests(unittest.TestCase):
    def test_created_date_auto_confirms(self):
        from tools.tameng_agent import format_evidence_bar, prepare_request, validate_generated_sql
        prepared = prepare_request('查询 prpcmain 中创建日期倒序', _snap(), _conn())
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        self.assertEqual(prepared['intent']['order'], 'DESC')
        names = prepared['evidence']['confirmed_fields']
        self.assertTrue(any(item.endswith('CREATED_DATE') for item in names))
        self.assertIn('IDX_PRPCMAIN_CREATED_DATE', format_evidence_bar(prepared['evidence']))
        sql = 'SELECT * FROM PRP.PRPCMAIN ORDER BY CREATED_DATE DESC'
        checked = validate_generated_sql(sql, prepared['evidence'], 'oracle')
        self.assertTrue(checked['allowed'])

    def test_ambiguous_fields_do_not_call_model(self):
        from tools.tameng_agent import prepare_request
        snap = _snap()
        snap['objects'][0]['columns'].append(
            {'name': 'CREATE_DATE', 'data_type': 'DATE', 'comment': '创建时间', 'indexed': False}
        )
        prepared = prepare_request('查询 prpcmain 中创建日期倒序', snap, _conn())
        self.assertFalse(prepared['call_model'])
        self.assertEqual(prepared['state'], 'NEEDS_SELECTION')

    def test_unknown_field_and_join_and_multistatement_blocked(self):
        from tools.tameng_agent import prepare_request, validate_generated_sql
        prepared = prepare_request('查询 prpcmain 中创建日期倒序', _snap(), _conn())
        evidence = prepared['evidence']
        bad = validate_generated_sql('SELECT CREATEDDATE FROM PRP.PRPCMAIN', evidence, 'oracle')
        self.assertFalse(bad['allowed'])
        self.assertIn('CREATEDDATE', bad['unknown_fields'])
        join = validate_generated_sql(
            'SELECT * FROM PRP.PRPCMAIN JOIN PRP.OTHER ON 1=1',
            evidence,
            'oracle',
        )
        self.assertFalse(join['allowed'])
        multi = validate_generated_sql(
            'SELECT * FROM PRP.PRPCMAIN; DELETE FROM PRP.PRPCMAIN',
            evidence,
            'oracle',
        )
        self.assertFalse(multi['allowed'])

    def test_stale_and_missing_snapshot(self):
        from tools.tameng_agent import prepare_request, snapshot_gate
        missing = snapshot_gate(_conn(), None)
        self.assertEqual(missing['state'], 'SNAPSHOT_MISSING')
        self.assertFalse(missing['ok'])
        stale = _snap(fingerprint='oracle|other|1|x|y')
        blocked = prepare_request('查询 prpcmain', stale, _conn())
        self.assertFalse(blocked['call_model'])
        self.assertEqual(blocked['state'], 'SNAPSHOT_STALE')
        none = snapshot_gate(None, _snap())
        self.assertEqual(none['state'], 'NO_CONNECTION')

    def test_v1_index_intent_requires_rescan(self):
        from tools.tameng_agent import snapshot_gate
        snap = _snap(version=1)
        gate = snapshot_gate(_conn(), snap, wants_index=True)
        self.assertFalse(gate['ok'])
        self.assertEqual(gate['state'], 'SNAPSHOT_V1')
        self.assertIn('索引信息不完整', gate['reason'])

    def test_truncated_unknown_table_does_not_claim_missing(self):
        from tools.tameng_agent import prepare_request
        snap = _snap(truncated=True)
        prepared = prepare_request('查询 not_in_snapshot', snap, _conn())
        self.assertFalse(prepared['call_model'])
        self.assertIn('截断', prepared['reason'])

    def test_two_tables_without_join_are_blocked(self):
        from tools.tameng_agent import prepare_request
        snap = _snap()
        snap['objects'].append({
            'owner': 'PRP', 'name': 'OTHER', 'object_type': 'TABLE', 'comment': '',
            'columns': [{'name': 'ID', 'data_type': 'NUMBER', 'comment': ''}],
            'indexes': [],
        })
        prepared = prepare_request('查询 prpcmain 和 other', snap, _conn())
        self.assertFalse(prepared['call_model'])
        self.assertIn('关联', prepared['reason'])

    def test_module_has_no_qt_db_or_llm(self):
        import inspect
        import tools.tameng_agent as mod
        source = inspect.getsource(mod)
        self.assertNotIn('PyQt', source)
        self.assertNotIn('intranet_llm', source)
        self.assertNotIn('open_connection', source)
        self.assertNotIn('ptools_harness', source)
        self.assertNotIn('chat_completions', source)

    def test_comment_uniquely_locates_table(self):
        from tools.tameng_agent import prepare_request
        snap = {
            'snapshot_id': 's1',
            'fingerprint': 'oracle|quote-test|1|h|1521',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [
                        {'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'},
                        {'name': 'APPLY_DATE', 'data_type': 'DATE', 'comment': '投保日期'},
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
        conn = {'id': 'c1', 'name': 'quote-test', 'dialect': 'oracle', 'host': 'h', 'port': 1521, 'database': 'orcl'}
        from tools.schema_snapshot import connection_fingerprint
        snap['fingerprint'] = connection_fingerprint(conn)

        prepared = prepare_request('帮我查一下保单主表的数据', snap, conn)
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        tables = prepared['evidence']['tables']
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]['qualified_name'], 'PRPCAR.T_POLICY')

    def test_current_table_selection_prioritized_with_fuzzy_query(self):
        from tools.tameng_agent import prepare_request
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
                        {'name': 'CREATED_DATE', 'data_type': 'DATE', 'comment': '创建日期'},
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
                        {'name': 'CREATED_DATE', 'data_type': 'DATE', 'comment': '创建日期'},
                    ],
                    'indexes': [],
                },
            ],
        }
        conn = {'id': 'c1', 'name': 'quote-test', 'dialect': 'oracle', 'host': 'h', 'port': 1521, 'database': 'orcl'}
        from tools.schema_snapshot import connection_fingerprint
        snap['fingerprint'] = connection_fingerprint(conn)

        # User currently has T_POLICY selected in UI and asks fuzzy query
        current_table = snap['objects'][0]
        prepared = prepare_request('按创建日期倒序查最近数据', snap, conn, current_table=current_table)
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        self.assertEqual(len(prepared['evidence']['tables']), 1)
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRPCAR.T_POLICY')

    def test_explicit_token_prioritized_over_current_tree_selection(self):
        from tools.tameng_agent import prepare_request
        snap = {
            'snapshot_id': 's1',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'}],
                    'indexes': [],
                },
                {
                    'owner': 'PRPCAR',
                    'name': 'T_CUSTOMER',
                    'object_type': 'TABLE',
                    'comment': '客户表',
                    'columns': [{'name': 'CUST_ID', 'data_type': 'VARCHAR2(30)', 'comment': '客户ID'}],
                    'indexes': [],
                },
            ],
        }
        conn = {'id': 'c1', 'name': 'quote-test', 'dialect': 'oracle', 'host': 'h', 'port': 1521, 'database': 'orcl'}
        from tools.schema_snapshot import connection_fingerprint
        snap['fingerprint'] = connection_fingerprint(conn)

        # UI has T_POLICY highlighted, but user added explicit Token for T_CUSTOMER
        current_table = snap['objects'][0]
        tokens = {'selected_objects': [{'qualified_name': 'PRPCAR.T_CUSTOMER'}]}
        prepared = prepare_request('查询数据', snap, conn, tokens=tokens, current_table=current_table)
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRPCAR.T_CUSTOMER')

    def test_cross_table_field_requires_selection_and_no_model_call(self):
        from tools.tameng_agent import prepare_request
        snap = {
            'snapshot_id': 's1',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'}],
                    'indexes': [],
                },
                {
                    'owner': 'PRPCAR',
                    'name': 'T_CLAIM',
                    'object_type': 'TABLE',
                    'comment': '理赔主表',
                    'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'}],
                    'indexes': [],
                },
            ],
        }
        conn = {'id': 'c1', 'name': 'quote-test', 'dialect': 'oracle', 'host': 'h', 'port': 1521, 'database': 'orcl'}
        from tools.schema_snapshot import connection_fingerprint
        snap['fingerprint'] = connection_fingerprint(conn)

        prepared = prepare_request('查询保单号', snap, conn)
        self.assertFalse(prepared['call_model'])
        self.assertEqual(prepared['state'], 'NEEDS_SELECTION')
        self.assertIn('多个', prepared['reason'])

    def test_user_confirmed_field_generates_successfully(self):
        from tools.tameng_agent import prepare_request
        snap = {
            'snapshot_id': 's1',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'}],
                    'indexes': [],
                },
                {
                    'owner': 'PRPCAR',
                    'name': 'T_CLAIM',
                    'object_type': 'TABLE',
                    'comment': '理赔主表',
                    'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2(30)', 'comment': '保单号'}],
                    'indexes': [],
                },
            ],
        }
        conn = {'id': 'c1', 'name': 'quote-test', 'dialect': 'oracle', 'host': 'h', 'port': 1521, 'database': 'orcl'}
        from tools.schema_snapshot import connection_fingerprint
        snap['fingerprint'] = connection_fingerprint(conn)

        # User confirms T_POLICY.POLICY_NO
        prepared = prepare_request('查询保单号', snap, conn, confirmed=['PRPCAR.T_POLICY.POLICY_NO'])
        self.assertTrue(prepared['ok'])
        self.assertTrue(prepared['call_model'])
        self.assertEqual(prepared['evidence']['tables'][0]['qualified_name'], 'PRPCAR.T_POLICY')

    def test_prompt_never_contains_secrets_or_raw_connection(self):
        from tools.ai_sql_draft import build_safe_context
        prompt = build_safe_context(
            dialect='oracle',
            alias='prod-db',
            question='查询保单号',
            action='generate',
            database='ORCL',
            schema_name='PRPCAR',
            oceanbase_mode='',
            evidence={
                'dialect': 'oracle',
                'snapshot_id': 's1',
                'scanned_at': '2026-09-01',
                'confirmed_fields': ['PRPCAR.T_POLICY.POLICY_NO'],
                'tables': [{
                    'qualified_name': 'PRPCAR.T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单表',
                    'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2', 'comment': '保单号'}],
                }],
            },
        )
        self.assertNotIn('password', prompt.lower())
        self.assertNotIn('secret', prompt.lower())
        self.assertNotIn('token', prompt.lower())
        self.assertNotIn('1521', prompt)
        self.assertNotIn('localhost', prompt)
        self.assertIn('PRPCAR.T_POLICY', prompt)
        self.assertIn('POLICY_NO', prompt)

    def test_unknown_field_from_model_is_blocked(self):
        from tools.tameng_agent import validate_generated_sql
        evidence = {
            'dialect': 'oracle',
            'tables': [{
                'qualified_name': 'PRPCAR.T_POLICY',
                'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2'}],
            }],
            'confirmed_fields': ['PRPCAR.T_POLICY.POLICY_NO'],
        }
        res = validate_generated_sql('SELECT GHOST_COLUMN FROM PRPCAR.T_POLICY', evidence, 'oracle')
        self.assertFalse(res['allowed'])
        self.assertIn('GHOST_COLUMN', res['unknown_fields'])

    def test_dialect_validation_oracle_and_mysql(self):
        from tools.tameng_agent import validate_generated_sql
        evidence = {
            'dialect': 'oracle',
            'tables': [{
                'qualified_name': 'PRPCAR.T_POLICY',
                'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2'}],
            }],
            'confirmed_fields': ['PRPCAR.T_POLICY.POLICY_NO'],
        }
        # LIMIT in Oracle is invalid
        oracle_bad = validate_generated_sql('SELECT POLICY_NO FROM PRPCAR.T_POLICY LIMIT 10', evidence, 'oracle')
        self.assertFalse(oracle_bad['allowed'])
        self.assertIn('方言', oracle_bad['reason'])

        # ROWNUM in MySQL is invalid
        mysql_evidence = dict(evidence, dialect='mysql')
        mysql_bad = validate_generated_sql('SELECT POLICY_NO FROM PRPCAR.T_POLICY WHERE ROWNUM <= 10', mysql_evidence, 'mysql')
        self.assertFalse(mysql_bad['allowed'])
        self.assertIn('方言', mysql_bad['reason'])

    def test_dml_ddl_remain_drafts_without_auto_exec(self):
        from tools.tameng_agent import validate_generated_sql
        evidence = {
            'dialect': 'oracle',
            'tables': [{
                'qualified_name': 'PRP.PRPCMAIN',
                'columns': [{'name': 'CREATED_DATE', 'data_type': 'DATE'}],
            }],
            'confirmed_fields': ['PRP.PRPCMAIN.CREATED_DATE'],
        }
        dml = validate_generated_sql('UPDATE PRP.PRPCMAIN SET CREATED_DATE = SYSDATE', evidence, 'oracle')
        self.assertTrue(dml['allowed'])
        self.assertEqual(dml['risk_level'], 'write')
        self.assertIn('草案', dml['reason'])


if __name__ == '__main__':
    unittest.main()
