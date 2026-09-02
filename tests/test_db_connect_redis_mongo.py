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
    normalize_redis_auth_mode,
    normalize_redis_seed_nodes,
    open_connection,
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

    def test_authentication_failed_classified(self):
        class AuthFailed(Exception):
            code = 18

        class FakeClient:
            def __init__(self, **kwargs):
                self.admin = MagicMock()
                self.admin.command.side_effect = AuthFailed("Authentication failed")

        pymongo_mod = types.SimpleNamespace(MongoClient=FakeClient)
        item = {
            "dialect": "mongodb",
            "host": "10.0.0.8",
            "port": 27017,
            "database": "biz",
            "username": "app",
        }
        with patch.dict(sys.modules, {"pymongo": pymongo_mod}):
            with self.assertRaises(DbError) as ctx:
                open_connection(item, plain_password="bad")
        self.assertIn("认证失败", str(ctx.exception))
        self.assertIn("code 18", str(ctx.exception))


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


if __name__ == "__main__":
    unittest.main()

