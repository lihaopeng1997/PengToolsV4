# -*- coding: utf-8 -*-
"""Redis Overview / SCAN / prefix index — no live Redis."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.db_redis_ops import (
    RedisScanState, build_prefix_index, format_redis_bytes, keys_for_prefix,
    redis_overview, redis_scan_page, split_key_prefix,
)


def _info_map(mapping):
    def _info(section=None):
        if section is None:
            merged = {}
            for value in mapping.values():
                merged.update(value)
            return merged
        return dict(mapping.get(section) or {})
    return _info


class FormatAndPrefixTests(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(format_redis_bytes(1024), '1 KB')
        self.assertEqual(format_redis_bytes(1048576), '1 MB')
        self.assertEqual(format_redis_bytes(None), '—')

    def test_split_prefix_colon_not_dots(self):
        self.assertEqual(split_key_prefix('user:profile:123'), ['user', 'profile', '123'])
        self.assertEqual(split_key_prefix('a/b/c'), ['a', 'b', 'c'])
        self.assertEqual(split_key_prefix('cache.item.v1'), ['cache.item.v1'])

    def test_prefix_index_counts(self):
        keys = ['user:1', 'user:2', 'user:profile:1', 'order:1']
        index = build_prefix_index(keys, incomplete=False)
        self.assertEqual(index['counts']['user'], 3)
        self.assertEqual(index['counts']['user:profile'], 1)
        self.assertEqual(index['counts']['order'], 1)
        names = [node['name'] for node in index['prefixes']]
        self.assertEqual(names, ['order', 'user'])
        self.assertEqual(keys_for_prefix(keys, 'user:profile'), ['user:profile:1'])
        self.assertEqual(len(keys_for_prefix(keys, 'user')), 3)

    def test_incomplete_prefix_not_exact(self):
        index = build_prefix_index(['user:1'] * 10, incomplete=True)
        self.assertTrue(index['incomplete'])
        self.assertTrue(index['prefixes'][0]['incomplete'])


class OverviewStandaloneTests(unittest.TestCase):
    def test_standalone_exact_keys(self):
        conn = MagicMock()
        conn.info.side_effect = _info_map({
            'server': {'redis_version': '6.2.20', 'redis_mode': 'standalone', 'uptime_in_days': '3'},
            'memory': {'used_memory': 1048576, 'used_memory_human': '1M'},
            'cluster': {'cluster_enabled': '0'},
            'stats': {},
            'clients': {'connected_clients': '4'},
            'keyspace': {'db0': {'keys': 12, 'expires': 1, 'avg_ttl': 9}},
        })
        conn.dbsize.return_value = 12
        conn.get_nodes = None
        overview = redis_overview(conn)
        self.assertEqual(overview['mode'], 'standalone')
        self.assertEqual(overview['redis_version'], '6.2.20')
        self.assertEqual(overview['used_memory'], 1048576)
        self.assertEqual(overview['total_keys'], 12)
        self.assertTrue(overview['total_keys_exact'])


class OverviewClusterTests(unittest.TestCase):
    def _node(self, host, port, role, keys, *, fail=False):
        nconn = MagicMock()
        if fail:
            nconn.info.side_effect = ConnectionError('down')
            nconn.dbsize.side_effect = ConnectionError('down')
        else:
            nconn.info.side_effect = _info_map({
                'server': {'redis_version': '6.2.20'},
                'memory': {'used_memory': 2048, 'used_memory_human': '2K'},
                'keyspace': {'db0': {'keys': keys, 'expires': 2, 'avg_ttl': 15}},
                'replication': {'role': role},
            })
            nconn.dbsize.return_value = keys
        return SimpleNamespace(host=host, port=port, server_type=role, redis_connection=nconn)

    def test_three_nodes_roles_and_aggregation(self):
        conn = MagicMock()
        conn.info.side_effect = _info_map({
            'server': {'redis_version': '6.2.20', 'redis_mode': 'cluster'},
            'memory': {'used_memory': 4096},
            'cluster': {'cluster_enabled': '1'},
            'stats': {},
            'clients': {},
        })
        nodes = [
            self._node('10.128.24.52', 47005, 'master', 729),
            self._node('10.128.24.53', 47005, 'master', 561),
            self._node('10.128.24.54', 47005, 'slave', 729),
        ]
        conn.get_nodes.return_value = nodes
        overview = redis_overview(conn)
        self.assertEqual(overview['mode'], 'cluster')
        self.assertEqual(overview['cluster_node_count'], 3)
        roles = [n['role'] for n in overview['nodes']]
        self.assertEqual(roles.count('primary'), 2)
        self.assertEqual(roles.count('replica'), 1)
        self.assertEqual(overview['total_keys'], 729 + 561)
        self.assertTrue(overview['total_keys_exact'])
        self.assertEqual(overview['nodes'][0]['keys'], 729)

    def test_partial_node_failure(self):
        conn = MagicMock()
        conn.info.side_effect = _info_map({
            'server': {'redis_version': '6.2.20'},
            'memory': {},
            'cluster': {'cluster_enabled': '1'},
            'stats': {},
            'clients': {},
        })
        conn.get_nodes.return_value = [
            self._node('10.0.0.1', 6379, 'master', 10),
            self._node('10.0.0.2', 6379, 'master', 20),
            self._node('10.0.0.3', 6379, 'master', 0, fail=True),
        ]
        overview = redis_overview(conn)
        self.assertEqual(overview['mode'], 'cluster')
        self.assertNotEqual(overview.get('error'), 'all_nodes_failed')
        statuses = [n['status'] for n in overview['nodes']]
        self.assertEqual(statuses.count('online'), 2)
        self.assertEqual(statuses.count('unavailable'), 1)
        failed = [n for n in overview['nodes'] if n['status'] == 'unavailable'][0]
        self.assertIsNone(failed['keys'])
        self.assertFalse(overview['total_keys_exact'])


class ScanPageTests(unittest.TestCase):
    def test_cursor_pagination_unique_keys(self):
        pages = {
            0: (123, ['a', 'b']),
            123: (456, ['c', 'a']),
            456: (0, ['d']),
        }

        class Fake:
            def scan(self, cursor=0, match='*', count=500):
                return pages[int(cursor)]

        first = redis_scan_page(Fake(), cursor=0, count=10, limit=2)
        self.assertEqual(first['keys'], ['a', 'b'])
        self.assertEqual(first['cursor'], 123)
        self.assertFalse(first['finished'])
        self.assertTrue(first['incomplete'])
        state = RedisScanState()
        gen = state.start('*')
        self.assertTrue(state.apply(gen, first['keys'], first['cursor'], first['finished']))
        second = redis_scan_page(Fake(), cursor=123, count=10, limit=10)
        self.assertTrue(state.apply(gen, second['keys'], second['cursor'], second['finished']))
        third = redis_scan_page(Fake(), cursor=456, count=10, limit=10)
        self.assertTrue(state.apply(gen, third['keys'], third['cursor'], third['finished']))
        self.assertTrue(state.finished)
        self.assertEqual(state.keys, ['a', 'b', 'c', 'd'])

    def test_stale_generation_ignored(self):
        state = RedisScanState()
        old = state.start('*old*')
        new = state.start('*new*')
        self.assertFalse(state.apply(old, ['stale'], 0, True))
        self.assertEqual(state.keys, [])
        self.assertTrue(state.apply(new, ['fresh'], 0, True))
        self.assertEqual(state.keys, ['fresh'])


class PanelStaleGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_stale_scan_does_not_overwrite(self):
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = RedisWorkbenchPanel('zh')
        gen_b = panel._scan.start('*b*')
        worker = SimpleNamespace(cancelled=False, generation=gen_b - 1)
        panel._on_worker_done('scan', {
            'generation': gen_b - 1,
            'append': False,
            'scan': {'keys': ['from-A'], 'cursor': 0, 'finished': True},
        }, worker)
        self.assertNotIn('from-A', panel._key_cache)
        panel._on_worker_done('scan', {
            'generation': gen_b,
            'append': False,
            'scan': {'keys': ['from-B'], 'cursor': 0, 'finished': True},
        }, SimpleNamespace(cancelled=False, generation=gen_b))
        self.assertEqual(panel._key_cache, ['from-B'])
        panel.close()

    def test_source_has_no_keys_star(self):
        from pathlib import Path
        text = Path(ROOT, 'tools', 'db_redis_ops.py').read_text(encoding='utf-8')
        panel = Path(ROOT, 'panels', 'db_redis_panel.py').read_text(encoding='utf-8')
        self.assertNotRegex(text, r"\.keys\s*\(")
        self.assertNotRegex(panel, r"\.keys\s*\(")
        self.assertNotIn("execute_command('KEYS'", text)

    def test_overview_tab_default(self):
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = RedisWorkbenchPanel('zh')
        self.assertEqual(panel.side_tabs.tabText(0), 'Overview')
        self.assertEqual(panel.side_tabs.currentIndex(), 0)
        self.assertTrue(hasattr(panel, 'nodes_table'))
        self.assertTrue(hasattr(panel, 'key_list'))
        panel.close()


if __name__ == '__main__':
    unittest.main()
