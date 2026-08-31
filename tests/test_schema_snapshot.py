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
        coll.find.return_value.limit.return_value = [
            {'_id': 'should-not-store', 'name': 'alice', 'profile': {'age': 30}},
            {'_id': 'x', 'name': 'bob', 'tags': ['a', 'b']},
        ]
        db = MagicMock()
        db.list_collection_names.return_value = ['user']
        db.__getitem__.return_value = coll
        objects, truncated = _scan_mongo(db)
        self.assertFalse(truncated)
        self.assertEqual(objects[0]['name'], 'user')
        names = {col['name'] for col in objects[0]['columns']}
        # 递归展平后应包含嵌套字段点号路径
        self.assertEqual(names, {'_id', 'name', 'profile.age', 'tags'})
        dumped = str(objects)
        self.assertNotIn('alice', dumped)
        self.assertNotIn('bob', dumped)
        self.assertNotIn('should-not-store', dumped)
        self.assertNotIn("'a'", dumped)

    def test_mongo_scan_empty_collection_has_id_placeholder(self):
        from tools.schema_snapshot import _scan_mongo
        coll = MagicMock()
        coll.find.return_value.limit.return_value = []
        db = MagicMock()
        db.list_collection_names.return_value = ['empty']
        db.__getitem__.return_value = coll
        objects, truncated = _scan_mongo(db)
        names = {col['name'] for col in objects[0]['columns']}
        self.assertEqual(names, {'_id'})

    def test_redis_scan_no_values(self):
        from tools.schema_snapshot import _scan_redis
        client = MagicMock()
        client.scan_iter.return_value = ['user:1']
        client.type.return_value = 'string'
        objects, truncated = _scan_redis(client)
        self.assertEqual(objects[0]['name'], 'user:1')
        self.assertEqual(objects[0]['object_type'], 'STRING')
        # string 类型不读值，绝不落盘数据
        client.get.assert_not_called()
        dumped = str(objects)
        self.assertNotIn('alice', dumped)

    def test_redis_hash_columns_only_field_names(self):
        from tools.schema_snapshot import _scan_redis
        client = MagicMock()
        client.scan_iter.return_value = ['user:1']
        client.type.return_value = 'hash'
        client.hkeys.return_value = ['name', 'age']
        client.hget.side_effect = ['secret-value', '30']
        objects, truncated = _scan_redis(client)
        cols = objects[0]['columns']
        self.assertEqual({c['name'] for c in cols}, {'name', 'age'})
        dumped = str(objects)
        self.assertNotIn('secret-value', dumped)

    def test_redis_scan_binary_key_does_not_crash(self):
        from tools.schema_snapshot import _scan_redis
        client = MagicMock()
        client.scan_iter.return_value = [b'\xac\xed\x00\x05key']
        client.type.return_value = 'string'
        objects, truncated = _scan_redis(client)
        self.assertEqual(objects[0]['object_type'], 'STRING')
        # 二进制 key 用 errors='replace' 解码，不抛 UnicodeDecodeError
        self.assertIsInstance(objects[0]['name'], str)
        self.assertNotIn('\xac', objects[0]['name'])

    def test_redis_hash_binary_field_does_not_crash(self):
        from tools.schema_snapshot import _scan_redis
        client = MagicMock()
        client.scan_iter.return_value = [b'user:1']
        client.type.return_value = 'hash'
        client.hkeys.return_value = [b'\xac\xedfield']
        client.hget.side_effect = ['v']
        objects, truncated = _scan_redis(client)
        cols = objects[0]['columns']
        self.assertEqual(len(cols), 1)
        self.assertIsInstance(cols[0]['name'], str)
        self.assertNotIn('\xac', cols[0]['name'])

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

    def test_mysql_scan_with_empty_database_queries_accessible_schemas(self):
        from tools.schema_snapshot import _scan_information_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('app_db', 'users', 'BASE TABLE', '用户表'), ('order_db', 'orders', 'BASE TABLE', '订单表')],
            [
                ('app_db', 'users', 'id', 'int', 'NO', 1, '主键', 'PRI'),
                ('app_db', 'users', 'username', 'varchar(64)', 'NO', 2, '用户名', ''),
                ('order_db', 'orders', 'order_id', 'int', 'NO', 1, '订单ID', 'PRI'),
            ],
            [
                ('app_db', 'users', 'PRIMARY', 0, 'BTREE', 'id', 1),
                ('order_db', 'orders', 'PRIMARY', 0, 'BTREE', 'order_id', 1),
            ],
        ]
        item = {'dialect': 'mysql', 'database': ''}
        objects, truncated = _scan_information_schema(conn, item)
        self.assertFalse(truncated)
        self.assertEqual(len(objects), 2)
        schemas = {obj['owner'] for obj in objects}
        self.assertEqual(schemas, {'app_db', 'order_db'})
        first_call_sql = cur.execute.call_args_list[0][0][0]
        self.assertIn("NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')", first_call_sql)
        self.assertNotIn("= DATABASE()", first_call_sql)

    def test_mysql_scan_with_specified_database(self):
        from tools.schema_snapshot import _scan_information_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('app_db', 'users', 'BASE TABLE', '用户表')],
            [('app_db', 'users', 'id', 'int', 'NO', 1, '主键', 'PRI')],
            [('app_db', 'users', 'PRIMARY', 0, 'BTREE', 'id', 1)],
        ]
        item = {'dialect': 'mysql', 'database': 'app_db'}
        objects, truncated = _scan_information_schema(conn, item)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]['owner'], 'app_db')
        first_call_sql, first_call_params = cur.execute.call_args_list[0][0]
        self.assertIn("WHERE TABLE_SCHEMA = %s", first_call_sql)
        self.assertEqual(first_call_params, ('app_db',))

    def test_mysql_server_level_table_and_column_lookup_preserves_schema(self):
        from tools.db_connect import list_tables, list_columns, schema_summary
        conn = MagicMock()
        cur = conn.cursor.return_value

        # 模拟 SHOW TABLES 失败（无默认库），fallback 查询返回两个 schema 下同名表
        def execute_side_effect(sql, params=None):
            if 'SHOW TABLES' in sql:
                raise Exception('1046: No database selected')

        cur.execute.side_effect = execute_side_effect
        cur.fetchall.side_effect = [
            [('app_db.users',), ('order_db.users',)],  # list_tables
            [('id',), ('name',)],                      # list_columns for app_db.users
            [('id',), ('created_at',)],                # list_columns for order_db.users
        ]

        tables = list_tables(conn, 'mysql')
        self.assertEqual(tables, ['app_db.users', 'order_db.users'])

        cols_app = list_columns(conn, 'mysql', 'app_db.users')
        self.assertEqual(cols_app, ['id', 'name'])

        cols_order = list_columns(conn, 'mysql', 'order_db.users')
        self.assertEqual(cols_order, ['id', 'created_at'])

    def test_oceanbase_mysql_mode_uses_information_schema(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('ob_db', 't_account', 'BASE TABLE', '账户表')],
            [('ob_db', 't_account', 'id', 'bigint', 'NO', 1, '主键', 'PRI')],
            [('ob_db', 't_account', 'PRIMARY', 0, 'BTREE', 'id', 1)],
        ]
        item = {'id': 'c_ob', 'dialect': 'oceanbase', 'mode': 'mysql', 'database': 'ob_db'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(len(payload['objects']), 1)
        self.assertEqual(payload['objects'][0]['name'], 't_account')
        self.assertEqual(payload['objects'][0]['owner'], 'ob_db')

    def test_oceanbase_driver_routing_open_connection(self):
        from tools.db_connect import open_connection
        with patch('pymysql.connect') as mock_pymysql:
            item_mysql = {
                'dialect': 'oceanbase',
                'mode': 'mysql',
                'host': '127.0.0.1',
                'port': 2883,
                'database': 'test_db',
                'username': 'root',
                'password': '',
            }
            open_connection(item_mysql)
            mock_pymysql.assert_called_once()

        with patch('oracledb.connect') as mock_oracle, \
             patch('tools.db_connect.ensure_oracle_client'):
            item_oracle = {
                'dialect': 'oceanbase',
                'mode': 'oracle',
                'host': '127.0.0.1',
                'port': 2883,
                'database': 'SYS',
                'username': 'sys',
                'password': '',
            }
            open_connection(item_oracle)
            mock_oracle.assert_called_once()

    def test_oracle_scan_service_is_not_treated_as_owner(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('SCOTT', 'EMP', 'TABLE', '员工表')],
            [('SCOTT', 'EMP', 'EMPNO', 'NUMBER(4)', 'N', 1, '员工号')],
            [], [], [], [],
        ]
        # database='ORCL' is SID/service, schema is not set -> must NOT query owner = 'ORCL'
        item = {'id': 'c_ora_dsn', 'dialect': 'oracle', 'database': 'ORCL', 'username': 'scott'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['objects'][0]['owner'], 'SCOTT')
        first_sql = cur.execute.call_args_list[0][0][0]
        self.assertNotIn("owner = :1", first_sql)
        self.assertIn("owner NOT IN", first_sql)

    def test_oracle_scan_with_explicit_schema(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('PRPCAR', 'PRPCMAIN', 'TABLE', '车险主表')],
            [('PRPCAR', 'PRPCMAIN', 'POLICY_NO', 'VARCHAR2(32)', 'N', 1, '保单号')],
            [], [], [], [],
        ]
        item = {'id': 'c_ora_schema', 'dialect': 'oracle', 'database': 'ORCL', 'schema': 'PRPCAR', 'username': 'scott'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['objects'][0]['owner'], 'PRPCAR')
        first_sql, first_params = cur.execute.call_args_list[0][0]
        self.assertIn("owner = :1", first_sql)
        self.assertEqual(first_params, ('PRPCAR',))

    def test_dameng_scan_with_specified_schema_success(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('SYSDBA', 'DM_TABLE', 'TABLE', '达梦业务表')],
            [('SYSDBA', 'DM_TABLE', 'C1', 'INT', 'N', 1, '主键')],
        ]
        item = {'id': 'c_dm', 'dialect': 'dameng', 'database': 'SYSDBA', 'username': 'sysdba'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(len(payload['objects']), 1)
        self.assertEqual(payload['objects'][0]['owner'], 'SYSDBA')
        self.assertEqual(payload['objects'][0]['name'], 'DM_TABLE')

    def test_dameng_scan_with_specified_schema_failure_does_not_fallback(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.execute.side_effect = Exception('DM-0010: permission denied on ALL_TABLES')
        item = {'id': 'c_dm_fail', 'dialect': 'dameng', 'database': 'RESTRICTED_SCHEMA', 'username': 'app'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'failed')
        self.assertIn('permission denied', payload['warning'])
        # 确保没有发生 fallback 到 USER_TABLES
        for call in cur.execute.call_args_list:
            self.assertNotIn('USER_TABLES', call[0][0])

    def test_dameng_scan_blank_schema_queries_user_tables(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.side_effect = [
            [('SYSDBA', 'MY_TBL', 'TABLE', '')],
            [('SYSDBA', 'MY_TBL', 'ID', 'INT', 'N', 1, '')],
        ]
        item = {'id': 'c_dm_blank', 'dialect': 'dameng', 'database': '', 'username': 'sysdba'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'ok')
        first_sql = cur.execute.call_args_list[0][0][0]
        self.assertIn('USER_TABLES', first_sql)

    def test_scan_schema_query_failure_propagates_failed_status(self):
        from tools.schema_snapshot import scan_schema
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.execute.side_effect = Exception('ORA-00942: table or view does not exist (password=secret123)')
        item = {'id': 'c_err', 'dialect': 'oracle', 'database': 'TEST', 'username': 'user'}
        payload = scan_schema(conn, item)
        self.assertEqual(payload['status'], 'failed')
        self.assertIn('ORA-00942', payload['warning'])
        self.assertNotIn('secret123', payload['warning'])


if __name__ == '__main__':
    unittest.main()
