# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from tools.db_redis_ops import (
    RedisScanState,
    redis_scan_page,
)


class FakeClusterConnection:
    def __init__(self, node_responses: dict):
        self.is_cluster = True
        self.node_responses = node_responses
        self.node_indexes = {k: 0 for k in node_responses}
        self.executed_commands = []

    def get_node(self, node_name: str):
        return f'NodeObj({node_name})'

    def get_nodes(self):
        return [f'NodeObj({k})' for k in self.node_responses]

    def scan(self, cursor=0, match=None, count=None, target_nodes=None, **kwargs):
        self.executed_commands.append(('scan', cursor, match, count, target_nodes))
        if target_nodes is None:
            cursors = {}
            keys = []
            for name in self.node_responses:
                idx = self.node_indexes[name]
                resp = self.node_responses[name]
                if idx < len(resp):
                    nxt, batch = resp[idx]
                    self.node_indexes[name] += 1
                    cursors[name] = nxt
                    keys.extend(batch)
                else:
                    cursors[name] = 0
            return cursors, keys
        else:
            name = str(target_nodes).replace('NodeObj(', '').replace(')', '')
            idx = self.node_indexes[name]
            resp = self.node_responses[name]
            if idx < len(resp):
                nxt, batch = resp[idx]
                self.node_indexes[name] += 1
                return {name: nxt}, batch
            return {name: 0}, []


class RedisClusterScanRegressionTest(unittest.TestCase):

    def test_standalone_scan_integer_cursor(self):
        fake_conn = MagicMock()
        fake_conn.is_cluster = False
        del fake_conn.get_nodes
        fake_conn.scan.side_effect = [
            (123, ['k1', 'k2']),
            (0, ['k3']),
        ]
        res1 = redis_scan_page(fake_conn, cursor=0, count=10, limit=2)
        self.assertEqual(res1['cursor'], 123)
        self.assertFalse(res1['finished'])
        self.assertEqual(res1['keys'], ['k1', 'k2'])

        res2 = redis_scan_page(fake_conn, cursor=res1['cursor'], count=10, limit=2)
        self.assertEqual(res2['cursor'], 0)
        self.assertTrue(res2['finished'])
        self.assertEqual(res2['keys'], ['k3'])

    def test_cluster_first_scan_dict_cursor(self):
        conn = FakeClusterConnection({
            'node-1': [(123, ['user:1', 'user:2'])],
            'node-2': [(456, ['order:1'])],
        })
        res = redis_scan_page(conn, cursor=0, count=10, limit=3)
        self.assertIsInstance(res['cursor'], dict)
        self.assertEqual(res['cursor'], {'node-1': 123, 'node-2': 456})
        self.assertFalse(res['finished'])
        self.assertEqual(set(res['keys']), {'user:1', 'user:2', 'order:1'})

    def test_cluster_one_node_cursor_zero(self):
        conn = FakeClusterConnection({
            'node-1': [(0, ['a1'])],
            'node-2': [(200, ['b1']), (0, ['b2'])],
        })
        res1 = redis_scan_page(conn, cursor=0, limit=2)
        self.assertEqual(res1['cursor']['node-1'], 0)
        self.assertEqual(res1['cursor']['node-2'], 200)
        self.assertFalse(res1['finished'])

        res2 = redis_scan_page(conn, cursor=res1['cursor'], limit=2)
        self.assertEqual(res2['cursor']['node-1'], 0)
        self.assertEqual(res2['cursor']['node-2'], 0)
        self.assertTrue(res2['finished'])
        self.assertIn('b2', res2['keys'])

    def test_cluster_scan_all_zero_marks_finished(self):
        conn = FakeClusterConnection({
            'n1': [(0, ['k1'])],
            'n2': [(0, ['k2'])],
        })
        res = redis_scan_page(conn, cursor=0)
        self.assertTrue(res['finished'])
        self.assertFalse(res['incomplete'])
        self.assertEqual(res['cursor'], {'n1': 0, 'n2': 0})

    def test_deduplicate_keys(self):
        conn = FakeClusterConnection({
            'n1': [(0, ['dup_key', 'unique_1'])],
            'n2': [(0, ['dup_key', 'unique_2'])],
        })
        res = redis_scan_page(conn, cursor=0)
        self.assertEqual(len(res['keys']), 3)
        self.assertEqual(set(res['keys']), {'dup_key', 'unique_1', 'unique_2'})

    def test_search_match_pattern_passthrough(self):
        conn = FakeClusterConnection({
            'n1': [(0, ['prefix:100'])],
        })
        res = redis_scan_page(conn, pattern='prefix:*', cursor=0)
        self.assertEqual(res['pattern'], 'prefix:*')
        self.assertEqual(conn.executed_commands[0][2], 'prefix:*')
        for cmd in conn.executed_commands:
            self.assertNotEqual(cmd[0], 'KEYS')

    def test_redis_scan_state_load_more_with_dict_cursor(self):
        state = RedisScanState()
        gen = state.start('user:*')
        self.assertEqual(state.generation, gen)
        self.assertEqual(state.cursor, 0)
        self.assertFalse(state.finished)

        ok = state.apply(gen, ['user:1'], {'node1': 10, 'node2': 20}, False)
        self.assertTrue(ok)
        self.assertEqual(state.cursor, {'node1': 10, 'node2': 20})
        self.assertFalse(state.finished)

        ok = state.apply(gen, ['user:2'], {'node1': 0, 'node2': 0}, True)
        self.assertTrue(ok)
        self.assertEqual(state.cursor, {'node1': 0, 'node2': 0})
        self.assertTrue(state.finished)
        self.assertEqual(state.keys, ['user:1', 'user:2'])

    def test_generation_stale_guard(self):
        state = RedisScanState()
        gen1 = state.start('old:*')
        gen2 = state.start('new:*')
        self.assertNotEqual(gen1, gen2)

        applied = state.apply(gen1, ['old_key'], {'node1': 5}, False)
        self.assertFalse(applied)
        self.assertEqual(state.keys, [])

        applied2 = state.apply(gen2, ['new_key'], {'node1': 0}, True)
        self.assertTrue(applied2)
        self.assertEqual(state.keys, ['new_key'])

    def test_partial_node_error_does_not_crash_scan(self):
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-good', 'node-bad']
        conn.scan.side_effect = [
            ({'node-good': 10, 'node-bad': 20}, ['k_init']),
            ({'node-good': 0}, ['k_good']),
            Exception('Connection to node-bad failed'),
        ]
        res = redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn('k_init', res['keys'])
        self.assertIn('k_good', res['keys'])
        self.assertTrue(res['finished'])

    def test_worker_production_chain_with_dict_cursor(self):
        from panels.db_redis_panel import _RedisWorker
        fake_cluster = FakeClusterConnection({
            'node-A': [(99, ['cluster_k1'])],
        })

        worker = _RedisWorker('scan', {'dialect': 'redis', 'mode': 'cluster'},
                              cursor={'node-A': 99}, count=50, limit=100, generation=1)
        results = []
        worker.completed.connect(lambda kind, payload: results.append((kind, payload)))

        with patch('panels.db_redis_panel.open_connection', return_value=fake_cluster):
            worker.run()

        self.assertEqual(len(results), 1)
        kind, payload = results[0]
        self.assertEqual(kind, 'scan')
        scan_res = payload.get('scan') or {}
        self.assertIsInstance(scan_res.get('cursor'), dict)
        self.assertEqual(scan_res.get('keys'), ['cluster_k1'])


if __name__ == '__main__':
    unittest.main()
