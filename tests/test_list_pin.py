# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.list_pin import (
    apply_namespace_pins,
    decorate_title,
    is_pinned,
    load_pin_store,
    ops_command_pin_id,
    set_namespace_pinned,
    set_pinned_fields,
    sort_with_pin,
)
from tools.requirements import normalize_requirement
from tools.iface_request_library import _normalize_api, filter_items, normalize_library


class ListPinTests(unittest.TestCase):
    def test_set_pinned_fields_and_sort(self):
        a = set_pinned_fields({'id': '1', 'title': 'A'}, True)
        b = {'id': '2', 'title': 'B', 'pinned': False}
        c = set_pinned_fields({'id': '3', 'title': 'C'}, True)
        ordered = sort_with_pin([b, a, c], secondary_key=lambda x: x.get('title'))
        self.assertTrue(is_pinned(ordered[0]))
        self.assertTrue(is_pinned(ordered[1]))
        self.assertFalse(is_pinned(ordered[2]))
        self.assertEqual(ordered[2]['id'], '2')

    def test_decorate_title(self):
        self.assertEqual(decorate_title('需求', True), '📌 需求')
        self.assertEqual(decorate_title('📌 需求', False), '需求')
        self.assertEqual(decorate_title('📌 需求', True), '📌 需求')

    def test_namespace_store(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'list_pins.json')
            set_namespace_pinned('knowledge', 'seed-1', True, path=path)
            items = apply_namespace_pins(
                [{'id': 'seed-1', 'title': 'T'}, {'id': 'seed-2', 'title': 'U'}],
                'knowledge',
                path=path,
            )
            self.assertTrue(items[0]['pinned'])
            self.assertFalse(items[1].get('pinned'))
            set_namespace_pinned('knowledge', 'seed-1', False, path=path)
            store = load_pin_store(path)
            self.assertNotIn('seed-1', store.get('knowledge') or {})

    def test_requirement_normalize_keeps_pin(self):
        item = normalize_requirement({'title': 'x', 'pinned': True, 'pinned_at': '2026-07-23T10:00:00'})
        self.assertTrue(item['pinned'])
        self.assertEqual(item['pinned_at'], '2026-07-23T10:00:00')
        plain = normalize_requirement({'title': 'y'})
        self.assertFalse(plain['pinned'])

    def test_iface_api_pin_and_filter_order(self):
        a = _normalize_api({
            'id': 'a', 'method': 'GET', 'url': 'http://x/a',
            'pinned': True, 'pinned_at': '2026-07-23T12:00:00', 'updated_at': '2026-07-20T00:00:00',
        })
        b = _normalize_api({
            'id': 'b', 'method': 'POST', 'url': 'http://x/b',
            'updated_at': '2026-07-22T00:00:00',
        })
        ordered = filter_items([b, a])
        self.assertEqual(ordered[0]['id'], 'a')
        self.assertEqual(ordered[1]['id'], 'b')
        lib = normalize_library({'apis': [b, a], 'categories': []})
        self.assertEqual(lib['apis'][0]['id'], 'a')

    def test_ops_command_pin_id_stable(self):
        cmd = {'command': 'uptime', 'title_zh': '运行时长与负载'}
        self.assertEqual(ops_command_pin_id(cmd), 'uptime\n运行时长与负载')


if __name__ == '__main__':
    unittest.main()
