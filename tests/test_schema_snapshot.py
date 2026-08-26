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
                self.assertEqual(payload['version'], 2)
                self.assertEqual(loaded['objects'][0]['index_metadata_status'], 'incomplete')

    def test_v2_keeps_indexes_and_v1_is_incomplete(self):
        from tools import schema_snapshot
        obj = schema_snapshot._clean_object({
            'owner': 'PRP', 'name': 'PRPCMAIN', 'object_type': 'TABLE', 'comment': '保单主表',
            'columns': [{'name': 'CREATED_DATE', 'data_type': 'DATE', 'comment': '创建日期'}],
            'indexes': [{
                'name': 'IDX_PRPCMAIN_CREATED_DATE', 'unique': False, 'index_type': 'NORMAL',
                'columns': [{'name': 'CREATED_DATE', 'position': 1}],
            }],
        })
        self.assertEqual(obj['indexes'][0]['name'], 'IDX_PRPCMAIN_CREATED_DATE')
        self.assertTrue(obj['columns'][0]['indexed'])
        v1 = schema_snapshot._clean_object({
            'name': 'OLD', 'columns': [{'name': 'A'}],
        })
        self.assertEqual(v1['indexes'], [])
        self.assertEqual(v1['index_metadata_status'], 'incomplete')
        self.assertFalse(schema_snapshot.dameng_index_scan_ready())

    def test_mysql_index_scan_from_statistics(self):
        from tools.schema_snapshot import _attach_mysql_indexes
        cur = MagicMock()
        cur.fetchall.return_value = [
            ('test', 'PRPCMAIN', 'IDX_CREATED', 1, 'BTREE', 'CREATED_DATE', 1),
        ]
        objects = {('test', 'PRPCMAIN'): {'owner': 'test', 'name': 'PRPCMAIN', 'columns': []}}
        _attach_mysql_indexes(cur, objects, 'test')
        self.assertEqual(objects[('test', 'PRPCMAIN')]['indexes'][0]['name'], 'IDX_CREATED')
        self.assertEqual(objects[('test', 'PRPCMAIN')]['index_metadata_status'], 'ok')

    def test_index_scan_failure_keeps_columns(self):
        from tools.schema_snapshot import _attach_oracle_indexes
        cur = MagicMock()
        cur.execute.side_effect = RuntimeError('password=secret ORA-01031')
        objects = {('PRP', 'T'): {'owner': 'PRP', 'name': 'T', 'columns': [{'name': 'A'}]}}
        _attach_oracle_indexes(cur, objects)
        self.assertEqual(objects[('PRP', 'T')]['index_metadata_status'], 'unavailable')
        self.assertEqual(objects[('PRP', 'T')]['indexes'], [])
        self.assertEqual(objects[('PRP', 'T')]['columns'][0]['name'], 'A')
        self.assertNotIn('secret', str(objects[('PRP', 'T')].get('index_warning') or ''))


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

    def test_non_ascii_path_and_incomplete_bundle_are_flagged(self):
        from tools.oracle_runtime import has_non_ascii, thick_client_error
        self.assertTrue(has_non_ascii(r'F:\AI\AI辅助编程\dist\22\instantclient_19_24'))
        self.assertFalse(has_non_ascii(r'C:\oracle\instantclient_19_24'))
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'oci.dll'), 'wb').close()
            message = thick_client_error('DPI-1072: unsupported', lib_dir=tmp, oci_lib=os.path.join(tmp, 'oci.dll'))
            self.assertIn('oraociei19.dll', message)

    def test_ensure_ascii_lib_dir_keeps_english_path(self):
        from tools.oracle_runtime import ensure_ascii_lib_dir, has_non_ascii
        with tempfile.TemporaryDirectory() as tmp:
            resolved = ensure_ascii_lib_dir(tmp)
            self.assertTrue(os.path.isdir(resolved))
            self.assertFalse(has_non_ascii(resolved))

    def test_ensure_ascii_lib_dir_junctions_chinese_path(self):
        from tools import oracle_runtime as runtime
        if os.name != 'nt':
            self.skipTest('Windows only')
        with tempfile.TemporaryDirectory() as tmp:
            chinese = os.path.join(tmp, 'AI辅助编程', 'instantclient_19_24')
            os.makedirs(chinese)
            link_root = os.path.join(tmp, 'data')
            with patch.object(runtime, 'ascii_client_link_path', return_value=os.path.join(link_root, 'oracle_thick_lib')):
                resolved = runtime.ensure_ascii_lib_dir(chinese)
            self.assertTrue(os.path.isdir(resolved))
            self.assertFalse(runtime.has_non_ascii(resolved))
            self.assertTrue(os.path.samefile(resolved, chinese))

    def test_detects_old_instant_client_and_32bit_dll(self):
        import struct
        import tempfile
        from tools.oracle_runtime import (
            detect_client_major, detect_pe_machine, prepare_thick_environment,
            thick_client_error, windows_short_path,
        )
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'oraociei11.dll'), 'wb').close()
            self.assertEqual(detect_client_major(tmp), 11)
            pe = bytearray(70)
            pe[0:2] = b'MZ'
            struct.pack_into('<I', pe, 60, 64)
            pe[64:68] = b'PE\x00\x00'
            struct.pack_into('<H', pe, 68, 0x14C)
            oci = os.path.join(tmp, 'oci.dll')
            with open(oci, 'wb') as stream:
                stream.write(pe)
            self.assertEqual(detect_pe_machine(oci), 'x86')
            message = thick_client_error('DPI-1072: the Oracle Client library version is unsupported', lib_dir=tmp, oci_lib=oci)
            self.assertIn('Instant Client 19', message)
            self.assertIn('PL/SQL Developer', message)
            foreign_home = os.path.join(tmp, 'old_home')
            os.makedirs(foreign_home)
            with patch.dict(os.environ, {'ORACLE_HOME': foreign_home, 'PATH': 'C:\\old'}, clear=False):
                prepare_thick_environment(tmp, foreign_home)
                self.assertNotEqual(os.environ.get('ORACLE_HOME', ''), os.path.abspath(foreign_home))
                first = os.environ.get('PATH', '').split(os.pathsep)[0]
                expected = {os.path.normcase(os.path.abspath(tmp)), os.path.normcase(windows_short_path(tmp))}
                self.assertIn(os.path.normcase(first), expected)

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
