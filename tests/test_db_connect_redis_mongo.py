# -*- coding: utf-8 -*-
"""Redis Cluster / MongoDB 认证合同 targeted tests（mock，不连真实服务）。"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.db_connect import (
    DbError,
    mongo_auth_mechanism,
    mongo_auth_source,
    normalize_mongo_seed_nodes,
    normalize_redis_auth_mode,
    normalize_redis_seed_nodes,
    open_connection,
    probe_connection,
    redis_auth_kwargs,
)


class RedisContractTests(unittest.TestCase):
    def test_old_standalone_config_compatibility(self):
        item = {"dialect": "redis", "host": "10.0.0.1", "port": 6379}
        self.assertEqual(normalize_redis_auth_mode(item), "none")
        seeds = normalize_redis_seed_nodes(item)
        self.assertEqual(seeds, [{"host": "10.0.0.1", "port": 6379}])

    def test_old_cluster_single_host_compatibility(self):
        item = {"dialect": "redis", "mode": "cluster", "host": "10.128.24.52", "port": 47005}
        seeds = normalize_redis_seed_nodes(item)
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["host"], "10.128.24.52")
        self.assertEqual(seeds[0]["port"], 47005)

    def test_new_multi_seed_serialization(self):
        item = {
            "seed_nodes": [
                {"host": "10.128.24.52", "port": 47005},
                {"host": "10.128.24.52", "port": 47006},
                {"host": "10.128.24.57", "port": 47005},
            ]
        }
        seeds = normalize_redis_seed_nodes(item)
        self.assertEqual(len(seeds), 3)
        self.assertEqual(seeds[2], {"host": "10.128.24.57", "port": 47005})

    def test_auth_mode_none_password_acl(self):
        self.assertEqual(normalize_redis_auth_mode({}), "none")
        self.assertEqual(normalize_redis_auth_mode({"password": "enc"}), "password")
        self.assertEqual(normalize_redis_auth_mode({"username": "app", "password": "enc"}), "acl")
        self.assertEqual(normalize_redis_auth_mode({"auth_mode": "none", "username": "x"}), "none")
        none_kw = redis_auth_kwargs({"auth_mode": "none", "username": "x"}, "secret")
        self.assertEqual(none_kw, {"username": None, "password": None})
        pwd_kw = redis_auth_kwargs({"auth_mode": "password", "username": "x"}, "secret")
        self.assertEqual(pwd_kw, {"username": None, "password": "secret"})
        acl_kw = redis_auth_kwargs({"auth_mode": "acl", "username": "app"}, "secret")
        self.assertEqual(acl_kw, {"username": "app", "password": "secret"})

    def test_invalid_port_rejected(self):
        with self.assertRaises(DbError):
            normalize_redis_seed_nodes({"host": "h", "port": 70000})
        with self.assertRaises(DbError):
            normalize_redis_seed_nodes({"seed_nodes": [{"host": "h", "port": 0}]})

    def test_cluster_uses_seed_nodes(self):
        captured = {}

        class ClusterNode:
            def __init__(self, host, port):
                self.host = host
                self.port = port

        class RedisCluster:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def ping(self):
                return True

        cluster_mod = types.SimpleNamespace(ClusterNode=ClusterNode, RedisCluster=RedisCluster)
        redis_mod = MagicMock()
        item = {
            "dialect": "redis",
            "mode": "cluster",
            "auth_mode": "acl",
            "username": "app",
            "seed_nodes": [
                {"host": "10.128.24.52", "port": 47005},
                {"host": "10.128.24.57", "port": 47005},
            ],
        }
        with patch.dict(sys.modules, {"redis": redis_mod, "redis.cluster": cluster_mod}):
            conn = open_connection(item, plain_password="pw")
        self.assertIsInstance(conn, RedisCluster)
        nodes = captured.get("startup_nodes") or []
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0].host, "10.128.24.52")
        self.assertEqual(nodes[0].port, 47005)
        self.assertEqual(captured.get("username"), "app")
        self.assertEqual(captured.get("password"), "pw")

    def test_auth_error_message_for_password_mode(self):
        class Boom(Exception):
            pass

        class RedisCluster:
            def __init__(self, **kwargs):
                pass

            def ping(self):
                raise Boom("invalid username-password pair or user is disabled")

        cluster_mod = types.SimpleNamespace(
            ClusterNode=lambda host, port: types.SimpleNamespace(host=host, port=port),
            RedisCluster=RedisCluster,
        )
        item = {
            "dialect": "redis",
            "mode": "cluster",
            "auth_mode": "password",
            "host": "10.128.24.52",
            "port": 47005,
        }
        with patch.dict(sys.modules, {"redis": MagicMock(), "redis.cluster": cluster_mod}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="x")
        msg = str(ctx.exception)
        self.assertIn("仅密码", msg)
        self.assertIn("ACL", msg)


class MongoContractTests(unittest.TestCase):
    def test_default_auth_source(self):
        self.assertEqual(mongo_auth_source({}), "admin")
        self.assertEqual(mongo_auth_source({"database": "prpcar"}), "prpcar")

    def test_explicit_auth_source(self):
        self.assertEqual(
            mongo_auth_source({"database": "prpcar", "auth_source": "admin"}),
            "admin",
        )

    def test_explicit_auth_mechanism(self):
        self.assertEqual(mongo_auth_mechanism({"auth_mechanism": "auto"}), "")
        self.assertEqual(mongo_auth_mechanism({"auth_mechanism": "SCRAM-SHA-256"}), "SCRAM-SHA-256")

    def test_uri_remains_untouched(self):
        captured = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                self.admin = MagicMock()
                self.admin.command.return_value = {"ok": 1}

            def __getitem__(self, name):
                box = MagicMock()
                box.name = name
                box.client = self
                return box

        pymongo_mod = types.SimpleNamespace(MongoClient=FakeClient)
        uri = "mongodb://u:p@10.0.0.1:27017/db?authSource=admin"
        item = {
            "dialect": "mongodb",
            "host": uri,
            "port": 1,
            "database": "appdb",
            "username": "ignored",
            "auth_source": "should-not-merge",
            "auth_mechanism": "SCRAM-SHA-1",
        }
        with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
            db = open_connection(item, plain_password="x")
        self.assertEqual(captured["args"], (uri,))
        self.assertNotIn("authSource", captured["kwargs"])
        self.assertNotIn("username", captured["kwargs"])
        self.assertEqual(db.name, "appdb")

    def test_host_mode_passes_auth_source_and_mechanism(self):
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.admin = MagicMock()
                self.admin.command.return_value = {"ok": 1}

            def __getitem__(self, name):
                box = MagicMock()
                box.name = name
                box.client = self
                return box

        pymongo_mod = types.SimpleNamespace(MongoClient=FakeClient)
        item = {
            "dialect": "mongodb",
            "host": "10.0.0.8",
            "port": 27017,
            "database": "biz",
            "username": "app",
            "auth_source": "admin",
            "auth_mechanism": "SCRAM-SHA-256",
        }
        with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
            open_connection(item, plain_password="secret")
        self.assertEqual(captured["host"], "10.0.0.8")
        self.assertEqual(captured["port"], 27017)
        self.assertEqual(captured["username"], "app")
        self.assertEqual(captured["authSource"], "admin")
        self.assertEqual(captured["authMechanism"], "SCRAM-SHA-256")

    def test_mongo_normalize_seed_nodes(self):
        # 1. 旧单机兼容
        single = normalize_mongo_seed_nodes({"host": "10.0.0.1", "port": 27017})
        self.assertEqual(single, [{"host": "10.0.0.1", "port": 27017}])

        # 2. 多 seed 规范化与持久化
        multi = normalize_mongo_seed_nodes({
            "seed_nodes": [
                {"host": "10.0.0.1", "port": 27017},
                {"host": "10.0.0.2", "port": 27018},
                {"host": "10.0.0.3", "port": 27019},
            ]
        })
        self.assertEqual(len(multi), 3)
        self.assertEqual(multi[1], {"host": "10.0.0.2", "port": 27018})

        # 3. 端口非法拦截
        with self.assertRaises(DbError):
            normalize_mongo_seed_nodes({"seed_nodes": [{"host": "h", "port": 0}]})
        with self.assertRaises(DbError):
            normalize_mongo_seed_nodes({"seed_nodes": [{"host": "h", "port": 70000}]})

    def test_mongo_cluster_multi_seed_client_call(self):
        """真实事故：3 个 seed、1 组用户名密码，证明 MongoClient 接收全部 seed 且 directConnection=False。"""
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.admin = MagicMock()
                self.admin.command.return_value = {"ok": 1}

            def __getitem__(self, name):
                box = MagicMock()
                box.name = name
                box.client = self
                return box

        pymongo_mod = types.SimpleNamespace(MongoClient=FakeClient)
        item = {
            "dialect": "mongodb",
            "mode": "cluster",
            "seed_nodes": [
                {"host": "10.0.0.1", "port": 27017},
                {"host": "10.0.0.2", "port": 27017},
                {"host": "10.0.0.3", "port": 27017},
            ],
            "database": "appdb",
            "username": "cluster_admin",
            "auth_source": "admin",
            "replica_set_name": "my-rs",
        }
        with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
            open_connection(item, plain_password="secure_password_123")

        self.assertEqual(captured.get("host"), [
            "10.0.0.1:27017",
            "10.0.0.2:27017",
            "10.0.0.3:27017",
        ])
        self.assertFalse(captured.get("directConnection"))
        self.assertEqual(captured.get("replicaSet"), "my-rs")
        self.assertEqual(captured.get("username"), "cluster_admin")
        self.assertEqual(captured.get("password"), "secure_password_123")
        self.assertEqual(captured.get("authSource"), "admin")

    def test_mongo_cluster_replica_set_optional(self):
        """Replica Set 为可选；不填写时交由 PyMongo 自动发现拓扑，不传 replicaSet 参数。"""
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.admin = MagicMock()
                self.admin.command.return_value = {"ok": 1}

            def __getitem__(self, name):
                box = MagicMock()
                box.name = name
                box.client = self
                return box

        pymongo_mod = types.SimpleNamespace(MongoClient=FakeClient)
        item = {
            "dialect": "mongodb",
            "mode": "cluster",
            "seed_nodes": [
                {"host": "10.0.0.1", "port": 27017},
                {"host": "10.0.0.2", "port": 27017},
            ],
            "database": "testdb",
        }
        with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
            open_connection(item)

        self.assertNotIn("replicaSet", captured)
        self.assertFalse(captured.get("directConnection"))

    def test_mongo_probe_connection_real_operations_and_metadata(self):
        """测试连接不仅构造 MongoClient，必须执行真实 ping 与至少一个 metadata operation，且密码不外露。"""
        ping_called = []
        metadata_called = []

        class FakeClient:
            def __init__(self, **kwargs):
                self.admin = MagicMock()
                self.admin.command.side_effect = lambda cmd: ping_called.append(cmd) or {"ok": 1}

            def list_database_names(self):
                metadata_called.append("list_database_names")
                return ["admin", "appdb", "config"]

            def __getitem__(self, name):
                box = MagicMock()
                box.name = name
                box.client = self
                box.command.side_effect = lambda cmd: ping_called.append(cmd) or {"ok": 1}
                box.list_collection_names.return_value = ["users", "orders"]
                return box

            def close(self):
                pass

        pymongo_mod = types.SimpleNamespace(MongoClient=FakeClient)
        item = {
            "dialect": "mongodb",
            "mode": "cluster",
            "seed_nodes": [
                {"host": "10.0.0.1", "port": 27017},
                {"host": "10.0.0.2", "port": 27017},
            ],
            "database": "appdb",
            "username": "my_user",
        }
        with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
            res = probe_connection(item, plain_password="super_secret_pwd")

        self.assertTrue(res.get("ok"))
        self.assertIn("ping", ping_called)
        self.assertIn("list_database_names", metadata_called)
        summary = res.get("summary") or ""
        self.assertIn("MongoDB 集群连接成功", summary)
        self.assertIn("10.0.0.1:27017", summary)
        self.assertIn("可访问数据库数：3", summary)
        # 密码绝对不得显示在 summary 中
        self.assertNotIn("super_secret_pwd", summary)

    def test_mongo_error_classification_all_categories(self):
        """测试错误分类：AUTH_ERROR, SERVER_SELECTION_ERROR, REPLICA_SET_MISMATCH, TLS_ERROR, INVALID_CONFIG。"""
        class MockAuthFail(Exception):
            code = 18

        class MockServerSelectionTimeout(Exception):
            pass

        class MockReplicaSetMismatch(Exception):
            pass

        class MockTlsError(Exception):
            pass

        class MockInvalidConfig(Exception):
            pass

        cases = [
            (MockAuthFail("auth failed"), "AUTH_ERROR"),
            (MockServerSelectionTimeout("ServerSelectionTimeoutError: timed out connecting to server"), "SERVER_SELECTION_ERROR"),
            (MockServerSelectionTimeout("ServerSelectionTimeoutError: No replica set members match selector ... ReplicaSetNoPrimary: could not find primary"), "SERVER_SELECTION_ERROR"),
            (MockReplicaSetMismatch("ConfigurationError: not a member of replica set 'rs0'"), "REPLICA_SET_MISMATCH"),
            (MockTlsError("SSLError: certificate verify failed"), "TLS_ERROR"),
            (MockInvalidConfig("ConfigurationError: invalid port configuration"), "INVALID_CONFIG"),
        ]

        for exc_instance, expected_tag in cases:
            class FailingClient:
                def __init__(self, **kwargs):
                    self.admin = MagicMock()
                    self.admin.command.side_effect = exc_instance

            pymongo_mod = types.SimpleNamespace(MongoClient=FailingClient)
            item = {
                "dialect": "mongodb",
                "mode": "cluster",
                "seed_nodes": [{"host": "10.0.0.1", "port": 27017}],
                "database": "biz",
                "username": "app",
            }
            with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
                with self.assertRaises(DbError) as ctx:
                    open_connection(item, plain_password="secret_password")
            err_msg = str(ctx.exception)
            self.assertIn(f"[{expected_tag}]", err_msg)
            # 确认不显示密码
            self.assertNotIn("secret_password", err_msg)

    def test_mongo_error_regression_abc(self):
        """明确回归测试 A, B, C:
        A. ServerSelectionTimeoutError + ReplicaSetNoPrimary -> SERVER_SELECTION_ERROR
        B. ServerSelectionTimeoutError + SSL/CERTIFICATE_VERIFY_FAILED -> TLS_ERROR
        C. 明确 not a member of replica set -> REPLICA_SET_MISMATCH
        """
        class ServerSelectionTimeoutError(Exception):
            pass

        class ConfigurationError(Exception):
            pass

        from tools.db_connect import _mongo_error_message

        # A: ServerSelectionTimeoutError + ReplicaSetNoPrimary -> SERVER_SELECTION_ERROR
        err_a = ServerSelectionTimeoutError("No replica set members match selector ... ReplicaSetNoPrimary: no primary available")
        msg_a = _mongo_error_message(err_a)
        self.assertIn("[SERVER_SELECTION_ERROR]", msg_a)
        self.assertNotIn("[REPLICA_SET_MISMATCH]", msg_a)

        # B: ServerSelectionTimeoutError + SSL/CERTIFICATE_VERIFY_FAILED -> TLS_ERROR
        err_b = ServerSelectionTimeoutError("10.0.0.1:27017: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        msg_b = _mongo_error_message(err_b)
        self.assertIn("[TLS_ERROR]", msg_b)
        self.assertNotIn("[SERVER_SELECTION_ERROR]", msg_b)

        # C: 明确 not a member of replica set -> REPLICA_SET_MISMATCH
        err_c = ConfigurationError("ConfigurationError: not a member of replica set 'prod-rs'")
        msg_c = _mongo_error_message(err_c)
        self.assertIn("[REPLICA_SET_MISMATCH]", msg_c)
        self.assertNotIn("[SERVER_SELECTION_ERROR]", msg_c)


class RedisInfoTests(unittest.TestCase):
    def test_server_info_structure(self):
        from tools.db_redis_ops import redis_server_info

        conn = MagicMock()
        conn.info.side_effect = lambda section: {
            "server": {"redis_version": "7.2.0"},
            "memory": {"used_memory_human": "1M"},
            "cluster": {"cluster_enabled": "1"},
        }[section]
        conn.dbsize.return_value = 3
        node = MagicMock()
        node.host = "10.128.24.52"
        node.port = 47005
        node.server_type = "master"
        node.redis_connection.dbsize.return_value = 2
        conn.get_nodes.return_value = [node]
        info = redis_server_info(conn)
        self.assertEqual(info["redis_version"], "7.2.0")
        self.assertEqual(info["mode"], "cluster")
        self.assertEqual(info["used_memory_human"], "1M")
        self.assertEqual(info["nodes"][0]["host"], "10.128.24.52")
        self.assertEqual(info["nodes"][0]["port"], 47005)


class StrictClusterConn:
    def __init__(self):
        self.is_cluster = True
        self.node_a = MagicMock(name="node-a-obj")
        self.node_b = MagicMock(name="node-b-obj")
        self.scan_calls = []

    def get_node(self, node_name=None):
        if node_name == "node-a":
            return self.node_a
        if node_name == "node-b":
            return self.node_b
        return None

    def scan(self, cursor=0, match="*", count=10, target_nodes=None):
        if isinstance(cursor, dict):
            raise AssertionError("Strict contract violation: conn.scan received dict cursor instead of scalar integer cursor!")
        self.scan_calls.append({
            "cursor": cursor,
            "match": match,
            "count": count,
            "target_nodes": target_nodes,
        })
        if target_nodes is None and cursor == 0:
            return ({"node-a": 100, "node-b": 0}, ["k1"])
        if target_nodes == self.node_a:
            if cursor == 100:
                return (50, ["k2"])
            elif cursor == 50:
                return (0, ["k3"])
        return (0, [])


class RedisConsoleScanContractTests(unittest.TestCase):
    def test_standalone_scan_connection_error_propagates_no_scan_iter(self):
        """A. Standalone conn.scan raises ConnectionError -> DbError -> scan_iter NOT called"""
        from tools.db_connect import run_read_query, run_console_statement, DbError
        conn = MagicMock()
        conn.scan.side_effect = ConnectionError("Connection refused by peer")
        conn.scan_iter = MagicMock()

        with self.assertRaises(DbError) as ctx:
            run_read_query(conn, 'redis', 'SCAN 0')
        self.assertIn('SCAN 失败', str(ctx.exception))
        self.assertIn('Connection refused by peer', str(ctx.exception))
        conn.scan_iter.assert_not_called()

        with self.assertRaises(DbError) as ctx:
            run_console_statement(conn, 'redis', 'SCAN 0')
        self.assertIn('SCAN 失败', str(ctx.exception))
        conn.scan_iter.assert_not_called()

    def test_standalone_scan_type_error_propagates_no_scan_iter(self):
        """B. Standalone conn.scan raises TypeError / programming error -> DbError -> scan_iter NOT called"""
        from tools.db_connect import run_read_query, run_console_statement, DbError
        conn = MagicMock()
        conn.scan.side_effect = TypeError("scan() takes 0 positional arguments but 1 was given")
        conn.scan_iter = MagicMock()

        with self.assertRaises(DbError) as ctx:
            run_read_query(conn, 'redis', 'SCAN 0')
        self.assertIn('SCAN 失败', str(ctx.exception))
        conn.scan_iter.assert_not_called()

        conn.scan.side_effect = ValueError("Invalid cursor format")
        with self.assertRaises(DbError) as ctx:
            run_console_statement(conn, 'redis', 'SCAN 0')
        self.assertIn('SCAN 失败', str(ctx.exception))
        conn.scan_iter.assert_not_called()

    def test_cluster_scan_first_page_returns_dict_cursor_has_more_true(self):
        """C1. First Cluster page: scan(cursor=0) -> ({"node-a": 100, "node-b": 0}, ["k1"]) -> offset 保持 dict, has_more=True"""
        from tools.db_connect import run_read_query
        conn = StrictClusterConn()

        res = run_read_query(conn, 'redis', 'SCAN 0', limit=1)
        self.assertEqual(res['rows'], [["k1"]])
        self.assertEqual(res['offset'], {"node-a": 100, "node-b": 0})
        self.assertTrue(res['has_more'])
        self.assertEqual(len(conn.scan_calls), 1)
        self.assertEqual(conn.scan_calls[0]["cursor"], 0)
        self.assertIsNone(conn.scan_calls[0]["target_nodes"])

    def test_cluster_scan_second_page_uses_scalar_cursor_and_target_node(self):
        """C2. Second Cluster page: 传入 dict offset -> get_node("node-a") -> scan(cursor=100, target_nodes=node_a) -> 严格标量游标"""
        from tools.db_connect import run_read_query, run_console_statement
        conn = StrictClusterConn()
        dict_cursor = {"node-a": 100, "node-b": 0}

        # Page 2 via run_read_query with dict offset
        res = run_read_query(conn, 'redis', 'SCAN 0', offset=dict_cursor, limit=1)
        self.assertEqual(res['rows'], [["k2"]])
        self.assertEqual(res['offset'], {"node-a": 50, "node-b": 0})
        self.assertTrue(res['has_more'])

        # 验证调用详情：必须是标量 cursor=100 与 target_nodes=node_a
        self.assertEqual(len(conn.scan_calls), 1)
        self.assertEqual(conn.scan_calls[0]["cursor"], 100)
        self.assertIsInstance(conn.scan_calls[0]["cursor"], int)
        self.assertEqual(conn.scan_calls[0]["target_nodes"], conn.node_a)

        # Page 2 via run_console_statement with dict offset
        conn.scan_calls.clear()
        res2 = run_console_statement(conn, 'redis', 'SCAN 0', offset=dict_cursor, limit=1)
        self.assertEqual(res2['rows'], [["k2"]])
        self.assertEqual(res2['offset'], {"node-a": 50, "node-b": 0})
        self.assertTrue(res2['has_more'])
        self.assertEqual(conn.scan_calls[0]["cursor"], 100)
        self.assertEqual(conn.scan_calls[0]["target_nodes"], conn.node_a)

    def test_cluster_scan_final_has_more_false(self):
        """C3. Final: node-a 返回 0 -> 全部节点耗尽 -> has_more=False"""
        from tools.db_connect import run_read_query
        conn = StrictClusterConn()
        res = run_read_query(conn, 'redis', 'SCAN 0', offset={"node-a": 50, "node-b": 0}, limit=1)
        self.assertEqual(res['rows'], [["k3"]])
        self.assertEqual(res['offset'], {"node-a": 0, "node-b": 0})
        self.assertFalse(res['has_more'])

    def test_cluster_scan_partial_not_silent(self):
        """C4. 节点故障时 partial/failed_nodes 不得静默，传播 warning 并在结果中暴露"""
        from tools.db_connect import run_read_query
        conn = StrictClusterConn()
        conn.get_node = lambda node_name=None: None  # 模拟 node 丢失

        res = run_read_query(conn, 'redis', 'SCAN 0', offset={"node-a": 100, "node-b": 0}, limit=1)
        self.assertTrue(res['partial'])
        self.assertTrue(res['incomplete'])
        self.assertIn("node-a", res['failed_nodes'])
        self.assertIn("node-a", res.get('warning', ''))


class MongoAndRedisRound4Tests(unittest.TestCase):
    """Round 4A: MongoDB 错误清洗、Code 13/18 区分、探针权限校验，及 Redis 多编码安全展示。"""

    def test_clean_mongo_error_message_strips_internal_noise(self):
        from tools.db_connect import clean_mongo_error_message
        raw = (
            "command listCollections requires authentication, full error: "
            "{'ok': 0.0, 'errmsg': 'command listCollections requires authentication', 'code': 13, 'codeName': 'Unauthorized', "
            "'$clusterTime': {'clusterTime': Timestamp(1725345600, 1), 'signature': {'hash': b'\\x00\\x01', 'keyId': 723456789}}, "
            "'operationTime': Timestamp(1725345600, 1), 'lsid': {'id': UUID('12345678-1234-5678-1234-567812345678')}}"
        )
        cleaned = clean_mongo_error_message(raw)
        self.assertNotIn('$clusterTime', cleaned)
        self.assertNotIn('operationTime', cleaned)
        self.assertNotIn('signature', cleaned)
        self.assertNotIn('lsid', cleaned)
        self.assertIn('requires authentication', cleaned)
        self.assertIn('code 13: Unauthorized', cleaned)

    def test_mongo_error_message_separates_code_18_and_13(self):
        from tools.db_connect import _mongo_error_message

        # Code 18: AuthenticationFailed
        exc_18 = Exception("Authentication failed.")
        setattr(exc_18, 'code', 18)
        msg_18 = _mongo_error_message(exc_18, {'username': 'app_user'})
        self.assertTrue(msg_18.startswith('[AUTH_ERROR]'))
        self.assertIn('code 18', msg_18)

        # Code 13 with username: Unauthorized (AUTHZ_ERROR)
        exc_13_user = Exception("not authorized on test_db to execute command")
        setattr(exc_13_user, 'code', 13)
        msg_13_user = _mongo_error_message(exc_13_user, {'username': 'app_user'})
        self.assertTrue(msg_13_user.startswith('[AUTHZ_ERROR]'))
        self.assertIn('授权不足', msg_13_user)

        # Code 13 without username: requires authentication (AUTH_REQUIRED)
        exc_13_no_user = Exception("command listCollections requires authentication")
        setattr(exc_13_no_user, 'code', 13)
        msg_13_no_user = _mongo_error_message(exc_13_no_user, {})
        self.assertTrue(msg_13_no_user.startswith('[AUTH_REQUIRED]'))
        self.assertIn('要求身份认证', msg_13_no_user)

    def test_mongo_probe_detects_unauthorized_database_permission(self):
        """B. ping PASS + listCollections code13 -> AUTHZ_ERROR FAIL"""
        from tools.db_connect import probe_connection

        mock_client = MagicMock()
        mock_client.list_database_names.return_value = ['app_db']
        mock_db = MagicMock()
        mock_db.name = 'app_db'
        mock_db.client = mock_client
        mock_db.command.return_value = {'ok': 1}
        exc_unauth = Exception("not authorized on app_db to execute command: listCollections")
        setattr(exc_unauth, 'code', 13)
        mock_db.list_collection_names.side_effect = exc_unauth
        mock_client.__getitem__.return_value = mock_db

        with patch('tools.db_connect.open_connection', return_value=mock_db):
            item = {'dialect': 'mongodb', 'host': '127.0.0.1', 'port': 27017, 'database': 'app_db', 'username': 'user1'}
            with self.assertRaises(DbError) as ctx:
                probe_connection(item)
            self.assertIn('[AUTHZ_ERROR]', str(ctx.exception))
            self.assertIn('app_db', str(ctx.exception))

    def test_mongo_probe_fails_on_timeout_and_generic_failure(self):
        """C & D. ping PASS 但 listCollections 超时或通用异常 -> 必须 FAIL，绝不能报连接成功。"""
        from tools.db_connect import probe_connection

        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_db.name = 'admin'
        mock_db.client = mock_client
        mock_db.command.return_value = {'ok': 1}
        mock_client.__getitem__.return_value = mock_db

        # C. Timeout
        mock_db.list_collection_names.side_effect = Exception("ServerSelectionTimeoutError: connection timed out")
        with patch('tools.db_connect.open_connection', return_value=mock_db):
            item = {'dialect': 'mongodb', 'host': '127.0.0.1', 'port': 27017, 'database': 'admin'}
            with self.assertRaises(DbError) as ctx:
                probe_connection(item)
            self.assertIn('[SERVER_SELECTION_ERROR]', str(ctx.exception))

        # D. Generic OperationFailure
        mock_db.list_collection_names.side_effect = Exception("OperationFailure: internal server error")
        with patch('tools.db_connect.open_connection', return_value=mock_db):
            item = {'dialect': 'mongodb', 'host': '127.0.0.1', 'port': 27017, 'database': 'admin'}
            with self.assertRaises(DbError) as ctx:
                probe_connection(item)
            self.assertIn('[MONGO_OPERATION_ERROR]', str(ctx.exception))

    def test_mongo_find_docs_unauthorized_classified_without_noise(self):
        """E. find code13 -> 抛出 AUTHZ_ERROR 且无 clusterTime / lsid 噪声。"""
        from tools.db_mongo_ops import find_docs

        mock_db = MagicMock()
        mock_db.name = 'order_db'
        exc = Exception("not authorized on order_db to execute command: find, $clusterTime: {ts: 123}, lsid: {id: 456}")
        setattr(exc, 'code', 13)
        mock_db.__getitem__.return_value.find.side_effect = exc

        item = {'dialect': 'mongodb', 'username': 'app_user', 'auth_source': 'admin'}
        with self.assertRaises(DbError) as ctx:
            find_docs(mock_db, 'orders', item=item)
        err_msg = str(ctx.exception)
        self.assertIn('[AUTHZ_ERROR]', err_msg)
        self.assertIn('order_db', err_msg)
        self.assertIn('orders', err_msg)
        self.assertNotIn('$clusterTime', err_msg)
        self.assertNotIn('lsid:', err_msg)

    def test_redis_raw_bytes_preserved_and_format_switching(self):
        """F, G, H. Redis 原始 bytes 完整保留在 inspect_redis_bytes，支持真实 GB18030、Hex、Base64 roundtrip。"""
        import base64
        from tools.db_redis_ops import inspect_redis_bytes

        # F. GB18030 raw bytes
        gb_raw = "系统运维数据看板".encode('gb18030')
        res = inspect_redis_bytes(gb_raw)
        self.assertEqual(res['kind'], 'gb18030')
        self.assertEqual(res['text'], "系统运维数据看板")
        # G. Hex: 必须是原始 GB18030 字节的 Hex，绝不是 UTF-8 重新编码的 Hex
        self.assertEqual(res['raw'], gb_raw)
        self.assertEqual(res['raw'].hex(' '), gb_raw.hex(' '))
        self.assertNotEqual(res['raw'].hex(' '), "系统运维数据看板".encode('utf-8').hex(' '))
        # H. Base64: 往返解密必须完全等于原始字节
        self.assertEqual(base64.b64decode(res['base64']), gb_raw)

    def test_redis_random_binary_heuristic_not_misidentified_as_text(self):
        """I. 随机二进制即使落在 GB18030 解码范围，也必须通过 is_probably_text 归为 binary。"""
        from tools.db_redis_ops import inspect_redis_bytes, is_probably_text

        # 构造包含非打印控制符与高频非字符字节的数据
        fake_binary = bytes([0x81, 0x30, 0x81, 0x30, 0x01, 0x02, 0x03, 0xff, 0xfe])
        res = inspect_redis_bytes(fake_binary)
        self.assertEqual(res['kind'], 'binary')
        self.assertIn('[Binary Data]', res['text'])
        self.assertNotIn('\ufffd', res['text'])

    def test_redis_java_serialization_bounded_preview_no_deserialization(self):
        """J. Java AC ED 00 05 识别为 java_serialized，安全提取类名，杜绝反序列化。"""
        from tools.db_redis_ops import inspect_redis_bytes

        java_payload = b'\xac\xed\x00\x05sr\x00\x1acom.pengtools.dto.TaskPlan' + bytes(range(64))
        res = inspect_redis_bytes(java_payload)
        self.assertEqual(res['kind'], 'java_serialized')
        self.assertIn('com.pengtools.dto.TaskPlan', res['text'])
        self.assertIn('Java Serialized Object', res['text'])

    def test_redis_hash_and_list_contain_inspected_cells(self):
        """K. Hash / List 单元格使用 inspect_redis_bytes，保留 raw 且无 \ufffd 破坏。"""
        from tools.db_redis_ops import redis_get_value

        mock_conn = MagicMock()
        mock_conn.type.return_value = 'hash'
        bin_val = bytes([0x00, 0x01, 0x02, 0x03, 0xff])
        mock_conn.hgetall.return_value = {b'k1': b'hello_utf8', b'k2': bin_val}

        val = redis_get_value(mock_conn, 'my_hash', kind='hash')
        self.assertIn('k1', val)
        self.assertIn('k2', val)
        self.assertEqual(val['k1']['kind'], 'utf8')
        self.assertEqual(val['k1']['text'], 'hello_utf8')
        self.assertEqual(val['k2']['kind'], 'binary')
        self.assertEqual(val['k2']['raw'], bin_val)

    def test_oceanbase_error_message_is_concise_without_driver_noise(self):
        """O. ODBC_DRIVER_REQUIRED 错误信息干净精简，严禁列出 SQL Server / Access / Excel 等系统技术噪音。"""
        from tools.db_connect import _connect_oceanbase_oracle

        item = {'dialect': 'oceanbase', 'mode': 'oracle', 'oceanbase_oracle_provider': 'odbc', 'host': '127.0.0.1', 'port': 2828, 'database': 'SYS', 'username': 'root'}
        # 模拟系统安装了其他驱动但无 OceanBase
        with patch('tools.db_connect.oceanbase_oracle_provider_status', return_value={
            'pyodbc_available': True, 'driver_available': False, 'status_code': 'ODBC_DRIVER_REQUIRED',
            'installed_drivers': ['SQL Server', 'Microsoft Access Driver (*.mdb, *.accdb)', 'Excel']
        }):
            with patch('pyodbc.drivers', return_value=['SQL Server', 'Microsoft Access Driver (*.mdb, *.accdb)', 'Excel']):
                with self.assertRaises(DbError) as ctx:
                    _connect_oceanbase_oracle(item)
                err = str(ctx.exception)
                self.assertIn('[ODBC_DRIVER_REQUIRED]', err)
                self.assertIn('OceanBase ODBC 2.0 Driver', err)
                self.assertNotIn('SQL Server', err)
                self.assertNotIn('Microsoft Access', err)
                self.assertNotIn('Excel', err)

    def test_oceanbase_provider_hint_dynamic_version_and_architecture(self):
        """P. OceanBase provider hint 动态读取 pyodbc 版本与系统架构。"""
        from tools.db_connect import oceanbase_oracle_provider_status

        st = oceanbase_oracle_provider_status()
        self.assertIn('pyodbc_available', st)
        self.assertIn('pyodbc_version', st)
        self.assertIn('driver_available', st)
        self.assertIn('status_code', st)
        if st['pyodbc_available']:
            self.assertTrue(len(st['pyodbc_version']) > 0)


if __name__ == "__main__":
    unittest.main()

