# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SchemaSnapshotTests(unittest.TestCase):
    def test_fingerprint_excludes_password(self):
        from tools.schema_snapshot import connection_fingerprint
        a = {'dialect': 'oracle', 'host': '10.0.0.1', 'port': 1521, 'database': 'orcl', 'username': 'u', 'password': 'secret'}
        b = dict(a)
        b['password'] = 'other'
        self.assertEqual(connection_fingerprint(a), connection_fingerprint(b))
        b['host'] = '10.0.0.2'
        self.assertNotEqual(connection_fingerprint(a), connection_fingerprint(b))

    def test_mongo_scan_keeps_types_not_values(self):
        from tools.schema_snapshot import _scan_mongo
        coll = MagicMock()
        coll.find_one.return_value = {'_id': 'should-not-store', 'name': 'alice'}
        db = MagicMock()
        db.list_collection_names.return_value = ['user']
        db.__getitem__.return_value = coll
        objects, truncated = _scan_mongo(db)
        self.assertFalse(truncated)
        self.assertEqual(objects[0]['name'], 'user')
        names = {col['name'] for col in objects[0]['columns']}
        self.assertEqual(names, {'_id', 'name'})
        dumped = str(objects)
        self.assertNotIn('alice', dumped)
        self.assertNotIn('should-not-store', dumped)

    def test_redis_scan_no_values(self):
        from tools.schema_snapshot import _scan_redis
        client = MagicMock()
        client.scan_iter.return_value = ['user:1']
        client.type.return_value = 'string'
        objects, truncated = _scan_redis(client)
        self.assertEqual(objects[0]['name'], 'user:1')
        self.assertEqual(objects[0]['object_type'], 'string')
        client.get.assert_not_called()

    def test_save_load_roundtrip(self):
        from tools import schema_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(schema_snapshot, 'SCHEMA_SNAPSHOT_DIR', tmp):
                payload = schema_snapshot.empty_snapshot({'id': 'c1', 'name': 'db', 'dialect': 'oracle'})
                payload['objects'] = [{
                    'owner': 'U', 'name': 'T', 'object_type': 'TABLE', 'comment': 'c',
                    'columns': [{'name': 'A', 'data_type': 'VARCHAR2', 'nullable': True, 'position': 1, 'comment': ''}],
                }]
                schema_snapshot.save_snapshot(payload)
                loaded = schema_snapshot.load_snapshot('c1')
                self.assertEqual(loaded['objects'][0]['name'], 'T')
                self.assertNotIn('rows', loaded)


class OracleRuntimeTests(unittest.TestCase):
    def test_thin_does_not_init_client(self):
        import tools.oracle_runtime as runtime
        runtime._STATE.update({'initialized': False, 'mode': None, 'lib_dir': None, 'error': ''})
        with patch.dict('sys.modules', {'oracledb': MagicMock()}):
            state = runtime.ensure_oracle_client('thin', '')
            self.assertEqual(state['mode'], 'thin')
            state2 = runtime.ensure_oracle_client('thin', '')
            self.assertEqual(state2['mode'], 'thin')

    def test_client_config_is_global_not_per_connection(self):
        from tools.oracle_runtime import load_oracle_client_config, resolve_oci_lib_dir
        mode, lib_dir = load_oracle_client_config({
            'oracle_client_mode': 'thick',
            'oracle_client_lib_dir': r'C:\oracle\instantclient_19_23',
        })
        self.assertEqual(mode, 'thick')
        self.assertTrue(lib_dir.endswith('instantclient_19_23'))
        thin, empty = load_oracle_client_config({'oracle_client_mode': 'auto'})
        self.assertEqual(thin, 'auto')
        self.assertEqual(empty, '')
        self.assertEqual(
            resolve_oci_lib_dir(
                home=r'C:\oracle\product\19.0.0\client_1',
                oci_lib=r'C:\oracle\instantclient_19_23\oci.dll',
            ),
            r'C:\oracle\instantclient_19_23',
        )

    def test_mode_switch_requires_restart(self):
        import tools.oracle_runtime as runtime
        runtime._STATE.update({'initialized': True, 'mode': 'thin', 'lib_dir': '', 'error': ''})
        with self.assertRaises(runtime.OracleRuntimeError):
            runtime.ensure_oracle_client('thick', 'C:\\instantclient')


class AiSqlDraftTests(unittest.TestCase):
    def test_validate_join_warning_and_field_limit(self):
        from tools.ai_sql_draft import generate_sql_draft, validate_draft
        import inspect
        source = inspect.getsource(generate_sql_draft)
        self.assertNotIn('run_console_statement', source)
        self.assertNotIn('run_read_query', source)
        draft = validate_draft(
            {'sql': 'SELECT A, SECRET FROM T1, T2', 'summary': 'x', 'intent': 'q'},
            selected_tables=['T1', 'T2'],
            selected_fields=['A'],
        )
        joined = '\n'.join(draft['warnings'])
        self.assertIn('需人工补充 Join 条件', joined)
        self.assertEqual(draft['risk_level'], 'unknown')
        self.assertTrue(any('SECRET' in item or '未勾选' in item for item in draft['warnings']))


if __name__ == '__main__':
    unittest.main()
