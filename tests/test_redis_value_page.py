"""Bounded raw Redis value pages; no real service and no whole-value helpers."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.data_center.redis_value import RedisKeyContext, RedisValueError, page_display_value, read_value_page
from tools.data_center.workspace_store import WorkspaceTabKind, WorkspaceTarget


class RedisValuePageTests(unittest.TestCase):
    def setUp(self):
        self.context = RedisKeyContext(WorkspaceTarget('tab', 'fixture', 'revision', '2', 'standalone', WorkspaceTabKind.REDIS), b'\x00\xffkey', 'selection')

    def test_string_bytes_and_list_indices_are_bounded_and_zero_ttl_is_preserved(self):
        calls = []
        owner = self
        class Connection:
            kind = b'string'
            def type(self, key):
                owner.assertEqual(key, owner.context.key)
                return self.kind
            def ttl(self, key):
                return 0
            def strlen(self, key):
                return 9
            def getrange(self, key, start, stop):
                calls.append(('getrange', start, stop))
                return b'\x00\xff1234567'[start:stop + 1]
            def llen(self, key):
                return 5
            def lrange(self, key, start, stop):
                calls.append(('lrange', start, stop))
                return [str(i).encode() for i in range(start, stop + 1)]
        connection = Connection()
        with patch('tools.data_center.redis_value.STRING_PAGE_BYTES', 4), patch('tools.data_center.redis_value.COLLECTION_PAGE_ITEMS', 2):
            first = read_value_page(connection, self.context)
            self.assertEqual((first.data, first.ttl, first.next_position, first.total, first.has_more), (b'\x00\xff12', 0, 4, 9, True))
            last = read_value_page(connection, self.context, 8, expected_kind='string')
            self.assertEqual((last.data, last.has_more), (b'7', False))
            self.assertEqual(calls, [('getrange', 0, 3), ('getrange', 8, 8)])
            self.assertEqual(page_display_value(first)['raw'], first.data)
            connection.kind = b'list'
            page = read_value_page(connection, self.context, 2)
            self.assertEqual((page.data, page.position, page.next_position, page.has_more), ((b'2', b'3'), 2, 4, True))
            self.assertEqual(calls[-1], ('lrange', 2, 3))

    def test_cursor_pages_keep_empty_intermediate_pages_and_typed_binary_rows(self):
        calls = []
        class Connection:
            kind = b'hash'
            def type(self, key):
                return self.kind
            def ttl(self, key):
                return -1
            def hscan(self, key, **kwargs):
                calls.append(kwargs)
                return (7, {}) if kwargs['cursor'] == 0 else (0, {b'\xff': b'\x00'})
            def sscan(self, key, **kwargs):
                return 0, [b'\xff']
            def zscan(self, key, **kwargs):
                return 0, [(b'\xff', 1.25)]
        connection = Connection()
        first = read_value_page(connection, self.context)
        self.assertEqual((first.data, first.next_position, first.has_more, first.total), ((), 7, True, None))
        second = read_value_page(connection, self.context, 7)
        self.assertEqual((second.data, second.has_more), (((b'\xff', b'\x00'),), False))
        self.assertEqual(calls, [{'cursor': 0, 'count': 200}, {'cursor': 7, 'count': 200}])
        connection.kind = b'set'
        self.assertEqual(read_value_page(connection, self.context).data, (b'\xff',))
        connection.kind = b'zset'
        self.assertEqual(read_value_page(connection, self.context).data, ((b'\xff', '1.25'),))

    def test_stream_lookahead_keeps_boundary_and_never_drops_unread_entry(self):
        entries = [(str(i).encode() + b'-0', {b'\xff': b'\x00'}) for i in range(1, 5)]
        class Connection:
            def type(self, key):
                return b'stream'
            def ttl(self, key):
                return -1
            def xrange(self, key, *, min, max, count):
                return [entry for entry in entries if min == b'-' or entry[0] >= min][:count]
        with patch('tools.data_center.redis_value.COLLECTION_PAGE_ITEMS', 2):
            first = read_value_page(Connection(), self.context)
            second = read_value_page(Connection(), self.context, first.next_position)
        self.assertEqual([row[0] for row in first.data + second.data], [row[0] for row in entries])
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertIn('field_base64', page_display_value(second))

    def test_changed_kind_expiry_item_page_limits_and_cancel_never_return_false_complete_page(self):
        connection = SimpleNamespace(type=lambda key: b'hash', ttl=lambda key: -1,
                                     hscan=lambda key, **kwargs: (0, {b'field': b'value'}))
        with self.assertRaises(RedisValueError):
            read_value_page(connection, self.context, expected_kind='list')
        for option, value in (('MAX_ITEM_BYTES', 1), ('MAX_PAGE_BYTES', 1), ('MAX_PAGE_ITEMS', 0)):
            with self.subTest(option=option), patch('tools.data_center.redis_value.' + option, value), self.assertRaises(RedisValueError):
                read_value_page(connection, self.context)
        connection.ttl = lambda key: -2
        with self.assertRaises(RedisValueError):
            read_value_page(connection, self.context)
        with self.assertRaises(RedisValueError):
            read_value_page(SimpleNamespace(), self.context, cancel=lambda: True)

    def test_actual_redis_sdk_uses_raw_key_and_getrange_without_opening_connection(self):
        import redis
        client = redis.Redis(host='127.0.0.1', port=1, decode_responses=False)
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            return {'TYPE': b'string', 'TTL': -1, 'STRLEN': 3, 'GETRANGE': b'\x00\xffx'}[args[0]]
        try:
            with patch.object(client, 'execute_command', side_effect=command):
                page = read_value_page(client, self.context)
            self.assertEqual(page.data, b'\x00\xffx')
            self.assertEqual([call for call in calls if call[0] == 'GETRANGE'], [('GETRANGE', self.context.key, 0, 2)])
            self.assertEqual(client.connection_pool._available_connections, [])
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
