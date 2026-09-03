# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

import redis.exceptions as rexc

from tools.db_connect import DbError
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

    def test_cluster_initial_programming_error_raises_dberror(self):
        """A. 首次全集群 scan 发生 TypeError 等编程错误，必须直接抛出 DbError，严禁伪装成 partial。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.scan.side_effect = TypeError("bad api arguments")

        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("bad api arguments", str(ctx.exception))

    def test_cluster_targeted_node_network_error_marks_partial(self):
        """B. 具体 target node 发生 ConnectionError 网络错误，其他节点继续，failed_nodes 包含该节点，结果标记 partial=True。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A', 'node-B']
        # 首次广播拿到 node-A 与 node-B 的游标
        # 第二次对 node-A 扫描抛 ConnectionError，第三次对 node-B 正常返回 cursor=0
        conn.scan.side_effect = [
            ({'node-A': 10, 'node-B': 20}, ['k_init']),
            rexc.ConnectionError("Connection to node-A lost"),
            ({'node-B': 0}, ['k_b']),
        ]
        res = redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn('k_init', res['keys'])
        self.assertIn('k_b', res['keys'])
        self.assertIn('node-A', res['failed_nodes'])
        self.assertTrue(res['partial'])
        self.assertTrue(res['incomplete'])
        self.assertTrue(res['finished'])

    def test_cluster_targeted_programming_error_raises_dberror(self):
        """C. 具体 target node 发生 TypeError 等编程错误，必须向外抛出 DbError，严禁吞掉。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            TypeError("unexpected argument during node scan"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("unexpected argument", str(ctx.exception))

    def test_cluster_targeted_node_timeout_error_marks_partial(self):
        """B. 具体 target node 发生 TimeoutError，标记 partial=True，其他节点继续。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A', 'node-B']
        conn.scan.side_effect = [
            ({'node-A': 10, 'node-B': 20}, ['k_init']),
            rexc.TimeoutError("Timeout connecting to node-A"),
            ({'node-B': 0}, ['k_b']),
        ]
        res = redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn('node-A', res['failed_nodes'])
        self.assertTrue(res['partial'])
        self.assertTrue(res['finished'])

    def test_cluster_targeted_auth_error_raises_dberror(self):
        """C. 具体 target node 发生 AuthenticationError，必须抛出 DbError，严禁伪装 partial。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            rexc.AuthenticationError("AUTH failed on node-A"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("AUTH failed", str(ctx.exception))

    def test_cluster_targeted_authorization_error_raises_dberror(self):
        """D. 具体 target node 发生 AuthorizationError，必须抛出 DbError，严禁伪装 partial。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            rexc.AuthorizationError("NOPERM this user has no permissions to scan"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("NOPERM", str(ctx.exception))

    def test_cluster_targeted_max_connections_raises_dberror(self):
        """E. 具体 target node 发生 MaxConnectionsError 连接池耗尽，必须抛出 DbError，严禁伪装 partial。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            rexc.MaxConnectionsError("Too many connections"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("Too many connections", str(ctx.exception))

    def test_cluster_targeted_cluster_down_raises_dberror(self):
        """F. 具体 target node 发生 ClusterDownError，必须抛出 DbError，严禁伪装 partial。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            rexc.ClusterDownError("CLUSTERDOWN Hash slot not served"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("CLUSTERDOWN", str(ctx.exception))

    def test_cluster_targeted_readonly_raises_dberror(self):
        """G. 具体 target node 发生 ReadOnlyError，必须抛出 DbError，严禁伪装 partial。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            rexc.ReadOnlyError("READONLY You can't write against a read only replica"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("READONLY", str(ctx.exception))

    def test_cluster_targeted_runtime_error_not_heuristically_guessed_as_transport(self):
        """H. RuntimeError("connection state corrupted") 必须抛出 DbError，严禁靠字符串猜测判定为网络异常。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-A']
        conn.scan.side_effect = [
            ({'node-A': 10}, ['k_init']),
            RuntimeError("connection state corrupted"),
        ]
        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("connection state corrupted", str(ctx.exception))

    def test_standalone_scan_error_propagates_and_no_scan_iter(self):
        """D. Standalone scan 抛出 ConnectionError 时必须直接抛出 DbError，严禁调用 scan_iter 回退。"""
        conn = MagicMock()
        conn.is_cluster = False
        del conn.get_nodes
        conn.scan.side_effect = rexc.ConnectionError("Standalone connection lost")
        conn.scan_iter = MagicMock()

        with self.assertRaises(DbError) as ctx:
            redis_scan_page(conn, cursor=0, limit=10)
        self.assertIn("Standalone connection lost", str(ctx.exception))
        conn.scan_iter.assert_not_called()

    def test_redis_single_node_targeted_network_failure_terminal_case(self):
        """E. 集群单节点推进游标时遭遇真实 ConnectionError：cursor 为空，finished=True, partial=True, incomplete=True。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-solo']
        conn.scan.side_effect = [
            ({'node-solo': 10}, ['k1']),
            rexc.ConnectionError('node-solo connection lost'),
        ]
        res = redis_scan_page(conn, cursor=0, limit=10)
        self.assertEqual(res['cursor'], {})
        self.assertTrue(res['finished'])
        self.assertTrue(res['partial'])
        self.assertTrue(res['incomplete'])
        self.assertEqual(res['failed_nodes'], ['node-solo'])

    def test_redis_scan_state_partial_sticky_across_pages(self):
        """Page 1 发生节点失败后，Page 2 即使正常结束，partial 与 failed_nodes 也必须跨页保持。"""
        state = RedisScanState()
        gen = state.start('user:*')

        # Page 1: node-B cursor=100, node-A failed
        ok1 = state.apply(gen, ['k1'], {'node-B': 100}, finished=False, partial=True, failed_nodes=['node-A'])
        self.assertTrue(ok1)
        self.assertFalse(state.finished)
        self.assertTrue(state.partial)
        self.assertTrue(state.incomplete)
        self.assertEqual(state.failed_nodes, ['node-A'])

        # Page 2: node-B cursor=0, finished=True, partial=False, failed_nodes=[]
        ok2 = state.apply(gen, ['k2'], {'node-B': 0}, finished=True, partial=False, failed_nodes=[])
        self.assertTrue(ok2)
        # finished 为 True（游标耗尽，无需继续加载）
        self.assertTrue(state.finished)
        # partial 与 failed_nodes 跨页 sticky 保持
        self.assertTrue(state.partial)
        self.assertTrue(state.incomplete)
        self.assertEqual(state.failed_nodes, ['node-A'])

    def test_scan_batch_never_drops_keys_standalone(self):
        """Standalone: 剩余配额 1，SCAN 返回 5 个 Key 且 cursor != 0，必须完整保留全部 5 个 Key。"""
        fake_conn = MagicMock()
        fake_conn.is_cluster = False
        del fake_conn.get_nodes
        fake_conn.scan.return_value = (99, ['key_1', 'key_2', 'key_3', 'key_4', 'key_5'])

        res = redis_scan_page(fake_conn, cursor=0, limit=1)
        self.assertEqual(len(res['keys']), 5)
        self.assertEqual(res['keys'], ['key_1', 'key_2', 'key_3', 'key_4', 'key_5'])
        self.assertEqual(res['cursor'], 99)
        self.assertFalse(res['finished'])

    def test_scan_batch_never_drops_keys_cluster(self):
        """Cluster: 剩余配额 1，单批返回 5 个 Key 且 node cursor != 0，必须完整保留全部 5 个 Key。"""
        conn = FakeClusterConnection({
            'node-1': [(88, ['c_1', 'c_2', 'c_3', 'c_4', 'c_5'])],
        })
        res = redis_scan_page(conn, cursor=0, limit=1)
        self.assertEqual(len(res['keys']), 5)
        self.assertEqual(res['keys'], ['c_1', 'c_2', 'c_3', 'c_4', 'c_5'])
        self.assertEqual(res['cursor'], {'node-1': 88})
        self.assertFalse(res['finished'])

    def test_cluster_get_node_returns_none_guards_against_broadcast(self):
        """get_node 返回 None 时不得把 target_nodes=None 传给 cluster scan，且记录为 failed_nodes。"""
        conn = MagicMock()
        conn.is_cluster = True
        conn.get_nodes.return_value = ['node-ghost']
        conn.get_node.return_value = None  # 节点脱机或拓扑变更找不到 node_name

        res = redis_scan_page(conn, cursor={'node-ghost': 100}, limit=10)
        # scan 不得以 target_nodes=None 调用以防意外广播全集群
        conn.scan.assert_not_called()
        self.assertIn('node-ghost', res['failed_nodes'])
        self.assertTrue(res['partial'])
        self.assertTrue(res['finished'])
        self.assertTrue(res['incomplete'])

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
