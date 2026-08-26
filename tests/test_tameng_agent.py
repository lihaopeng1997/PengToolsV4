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

    def test_generate_without_evidence_does_not_call_llm(self):
        from tools.ai_sql_draft import generate_sql_draft
        with patch('tools.ai_sql_draft.chat_completions') as mocked:
            with patch('tools.ai_sql_draft.is_enabled', return_value=True):
                draft = generate_sql_draft('查询 prpcmain', stale=True, evidence=None)
        mocked.assert_not_called()
        self.assertTrue(draft['fail_closed'])
        self.assertFalse(draft.get('sql'))


if __name__ == '__main__':
    unittest.main()
