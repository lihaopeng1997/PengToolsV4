"""Single-key export uses raw bytes, bounded Redis calls and private files."""

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.data_center.redis_export import RedisExportError, export_key
from tools.data_center.workspace_store import WorkspaceTarget, WorkspaceTabKind
from tools.db_redis_ops import redis_scan_page


class RedisExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='pengtools-redis-export-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'key.json'
        self.target = WorkspaceTarget('tab', 'fixture', 'revision', '2', 'standalone', WorkspaceTabKind.REDIS)

    def test_six_types_preserve_binary_data_and_use_incremental_reads(self):
        calls = []
        owner = self
        values = [b'\x00\xff'] * 20
        class Connection:
            def type(self, key):
                owner.assertEqual(key, b'\x00\xffkey')
                return self.kind.encode()
            def ttl(self, key):
                return 0
            def strlen(self, key):
                return 11
            def getrange(self, key, start, stop):
                calls.append(('getrange', start, stop))
                return b'\x00\xff123456789'[start:stop + 1]
            def llen(self, key):
                return len(values)
            def lrange(self, key, start, stop):
                owner.assertGreaterEqual(stop, 0)
                calls.append(('lrange', start, stop))
                return values[start:stop + 1]
            def hscan(self, key, *, cursor, count):
                calls.append(('hscan', cursor, count))
                return (7, {b'\x00': b'\xff'}) if cursor == 0 else (0, {b'next': b'last'})
            def sscan(self, key, *, cursor, count):
                calls.append(('sscan', cursor, count))
                return (7, [b'\x00\xff']) if cursor == 0 else (0, [b'next'])
            def zscan(self, key, *, cursor, count):
                calls.append(('zscan', cursor, count))
                return 0, [(b'\x00\xff', 1.25)]
            def xrange(self, key, *, min, max, count):
                calls.append(('xrange', min, count))
                entries = [(b'1-0', {b'\x00': b'\xff'}), (b'2-0', {b'next': b'last'})]
                return [entry for entry in entries if min == b'-' or entry[0] >= min][:count]
        connection = Connection()
        for kind in ('string', 'hash', 'list', 'set', 'zset', 'stream'):
            with self.subTest(kind=kind), patch('tools.data_center.redis_export.CHUNK_BYTES', 5):
                calls.clear()
                connection.kind = kind
                receipt = export_key(connection, self.target, b'\x00\xffkey', self.path, overwrite=True)
                output = json.loads(self.path.read_text(encoding='utf-8'))
                self.assertEqual(base64.b64decode(output['key']['base64']), b'\x00\xffkey')
                self.assertEqual(output['ttl_seconds_at_start'], 0)
                self.assertEqual(output['database'], '2')
                self.assertTrue(output['view_only'])
                self.assertFalse(output['consistent_snapshot'])
                self.assertTrue(output['read_completed'])
                self.assertEqual(receipt['records'], len(output['records']))
                if kind == 'string':
                    self.assertEqual(calls, [('getrange', 0, 4), ('getrange', 5, 9), ('getrange', 10, 10)])
                    self.assertEqual(b''.join(base64.b64decode(row['chunk']['base64']) for row in output['records']), b'\x00\xff123456789')
                elif kind == 'list':
                    self.assertEqual(calls, [('lrange', 0, 15), ('lrange', 16, 19)])
                elif kind == 'hash':
                    self.assertEqual(base64.b64decode(output['records'][0]['value']['base64']), b'\xff')
                elif kind == 'zset':
                    self.assertEqual(output['records'][0]['score'], '1.25')

    def test_cancel_limits_changed_key_and_expiry_never_publish_false_complete_files(self):
        state = {'cancel': False, 'changed': False, 'ttl': -1}
        class Connection:
            def type(self, key):
                return b'string'
            def ttl(self, key):
                return state['ttl']
            def strlen(self, key):
                return 5
            def getrange(self, key, start, stop):
                if state['cancel']:
                    return b'12345'
                return b'' if state['changed'] else b'12345'
        connection = Connection()
        for mode in ('cancel', 'changed', 'limit', 'expired'):
            state.update(cancel=False, changed=mode == 'changed', ttl=-2 if mode == 'expired' else -1)
            self.path.write_bytes(b'previous')
            def progress_cancel():
                state['cancel'] = mode == 'cancel'
                return state['cancel']
            with self.subTest(mode=mode), patch('tools.data_center.redis_export.MAX_OUTPUT_BYTES', 1 if mode == 'limit' else 512 * 1024 * 1024):
                with self.assertRaises(RedisExportError):
                    export_key(connection, self.target, b'key', self.path, overwrite=True, cancel=progress_cancel)
                self.assertEqual(self.path.read_bytes(), b'previous')
                self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_scan_retains_distinct_raw_names_with_the_same_display_label(self):
        class Connection:
            def scan(self, **kwargs):
                return 0, ['中'.encode('utf-8'), '中'.encode('gb18030')]
        page = redis_scan_page(Connection())
        self.assertEqual(page['keys'], ['中'])
        self.assertEqual(set(page['raw_keys']['中']), {'中'.encode('utf-8'), '中'.encode('gb18030')})

    def test_large_collection_item_and_late_destination_are_not_published_or_overwritten(self):
        path = self.path
        class Connection:
            kind = b'hash'
            def type(self, key):
                return self.kind
            def ttl(self, key):
                return -1
            def hscan(self, key, **kwargs):
                return 0, {b'a': b'ab'}
            def strlen(self, key):
                return 1
            def getrange(self, key, start, stop):
                path.write_bytes(b'late destination')
                return b'x'
        connection = Connection()
        with patch('tools.data_center.redis_export.MAX_VALUE_BYTES', 1), self.assertRaises(RedisExportError):
            export_key(connection, self.target, b'', self.path)
        self.assertFalse(path.exists())
        connection.kind = b'string'
        with self.assertRaises(RedisExportError):
            export_key(connection, self.target, b'key', self.path)
        self.assertEqual(path.read_bytes(), b'late destination')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [path])

    def test_actual_redis_sdk_methods_receive_raw_key_and_bounded_range_without_connecting(self):
        import redis

        client = redis.Redis(host='127.0.0.1', port=1, decode_responses=False)
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            return {'TYPE': b'string', 'TTL': -1, 'STRLEN': 3, 'GETRANGE': b'\x00\xffx'}[args[0]]
        try:
            with patch.object(client, 'execute_command', side_effect=command):
                export_key(client, self.target, b'\xffkey', self.path)
            self.assertTrue(all(call[1] == b'\xffkey' for call in calls))
            self.assertEqual([call for call in calls if call[0] == 'GETRANGE'], [('GETRANGE', b'\xffkey', 0, 2)])
            self.assertEqual(client.connection_pool._available_connections, [])
            self.assertEqual(client.connection_pool._in_use_connections, set())
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
