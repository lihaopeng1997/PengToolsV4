from __future__ import annotations

import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.data_center.connection_transport import ConnectionTransportError
from tools.data_center.host_nosql import NoSQLReadOnlyHost
from tools.data_center.nosql_transport import connect_nosql, open_data_center_nosql_connection


def _profile(provider, **changes):
    profile = dict(id="transport-fixture", dialect=provider, mode="standalone",
                   host="db.fixture.invalid", port=6379 if provider == "redis" else 27017,
                   database="0" if provider == "redis" else "fixture", auth_mode="none",
                   ssh_enabled=True, ssh_host="ssh.fixture.invalid", ssh_port=22,
                   ssh_username="fixture", ssh_auth_method="password",
                   ssh_password_token="enc:fixture", tls_enabled=False)
    profile.update(changes)
    return profile


class _Lease:
    dial_host = "127.0.0.1"
    dial_port = 49153

    def __init__(self, events):
        self.events = events
        self.results = []

    def release(self):
        self.events.append("release")
        return self.results.pop(0) if self.results else True


class _Manager:
    def __init__(self, events):
        self.lease = _Lease(events)
        self.calls = []

    def acquire(self, spec, **kwargs):
        self.calls.append((spec, kwargs))
        return self.lease


class _Database:
    def __getitem__(self, name):
        return SimpleNamespace(find=lambda **kwargs: iter(()))

    def command(self, name):
        return {"ok": 1}

    def list_collection_names(self):
        return []


class _Client:
    socket_timeout = 1.0

    def __init__(self, events):
        self.events = events

    def __getitem__(self, name):
        return _Database()

    def ping(self):
        return True

    def close(self):
        self.events.append("client.close")


class NoSQLTransportTests(unittest.TestCase):
    def test_host_lease_propagates_pending_tunnel_close_and_can_retry(self):
        from tools.data_center.host_nosql import NoSQLHostError
        for provider in ("redis", "mongodb"):
            with self.subTest(provider=provider):
                events = []
                manager = _Manager(events)
                manager.lease.results = [False, True]
                def build(*args, **kwargs):
                    return _Client(events)
                profile = _profile(provider)
                host = NoSQLReadOnlyHost(profile_loader=lambda **kwargs: profile, secret_loader=lambda **kwargs: "")
                with patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=build), "pymongo": SimpleNamespace(MongoClient=build)}), patch(
                    "tools.data_center.nosql_transport.default_ssh_tunnel_manager", return_value=manager
                ):
                    factory = host.create_factory(profile["id"], database_name="fixture" if provider == "mongodb" else None,
                                                  allowed_collection_ids=("items",) if provider == "mongodb" else None)
                    lease = factory(profile["id"])
                    with self.assertRaisesRegex(NoSQLHostError, "CLEANUP_PENDING"):
                        lease.close()
                    self.assertTrue(lease.closed)
                    self.assertEqual(lease.execute({"operation":"GET", "key":"fixture"}).code, "SESSION_NOT_FOUND")
                    lease.close()
                    lease.close()
                    self.assertEqual(events, ["client.close", "release", "release"])

    def test_native_redis_client_method_is_not_mistaken_for_mongo_owner(self):
        import redis
        from tools.data_center.host_nosql import _unpack_client
        from tools.data_center.connection_transport import LeasedConnection
        native = redis.Redis(host="fixture.invalid", socket_timeout=1)
        wrapped = LeasedConnection(native, _Lease([]))
        for client in (native, wrapped):
            unpacked, owner, database, collections = _unpack_client(client)
            self.assertIs(unpacked, client)
            self.assertIs(owner, client)
            self.assertIsNone(database)
        wrapped.close()

    def test_actual_default_agent_factories_dial_tunnel_and_close_database_first(self):
        for provider in ("redis", "mongodb"):
            with self.subTest(provider=provider):
                events, calls = [], []
                manager = _Manager(events)
                client = _Client(events)
                def build(*args, **kwargs):
                    calls.append((args, kwargs))
                    return client
                modules = {"redis": SimpleNamespace(Redis=build), "pymongo": SimpleNamespace(MongoClient=build)}
                profile = _profile(provider)
                host = NoSQLReadOnlyHost(profile_loader=lambda **kwargs: profile, secret_loader=lambda **kwargs: "")
                with patch.dict("sys.modules", modules), patch(
                    "tools.data_center.nosql_transport.default_ssh_tunnel_manager", return_value=manager
                ):
                    factory = host.create_factory(profile["id"], database_name="fixture" if provider == "mongodb" else None,
                                                  allowed_collection_ids=("items",) if provider == "mongodb" else None)
                    lease = factory(profile["id"])
                    self.assertEqual(calls[0][1]["host"], "127.0.0.1")
                    self.assertEqual(calls[0][1]["port"], 49153)
                    self.assertEqual(manager.calls[0][0].database_host, "db.fixture.invalid")
                    if provider == "mongodb":
                        self.assertIs(calls[0][1]["directConnection"], True)
                    lease.close()
                self.assertEqual(events, ["client.close", "release"])

    def test_unsupported_topology_and_tls_never_acquire_or_create_client(self):
        for provider, changes in (
            ("redis", {"mode": "cluster"}),
            ("redis", {"seed_nodes": [{"host": "a", "port": 1}, {"host": "b", "port": 2}]}),
            ("redis", {"tls_enabled": True}),
            ("mongodb", {"replica_set_name": "rs"}),
            ("mongodb", {"host": "mongodb+srv://srv.fixture.invalid/fixture"}),
            ("mongodb", {"host": "mongodb://a,b/fixture"}),
            ("mongodb", {"host": "mongodb://a/fixture?replicaSet=rs"}),
            ("mongodb", {"host": "mongodb://a/fixture?tls=true"}),
            ("mongodb", {"tls_enabled": True}),
        ):
            with self.subTest(provider=provider, changes=changes), patch(
                "tools.data_center.nosql_transport.default_ssh_tunnel_manager"
            ) as manager, patch.dict("sys.modules", {"redis": None, "pymongo": None}):
                with self.assertRaises(ConnectionTransportError):
                    connect_nosql(provider, _profile(provider, **changes), "")
                manager.assert_not_called()

    def test_mongo_single_uri_keeps_options_and_pins_tunnel_endpoint(self):
        calls, events = [], []
        manager = _Manager(events)
        def build(*args, **kwargs):
            calls.append((args, kwargs))
            return _Client(events)
        with patch.dict("sys.modules", {"pymongo": SimpleNamespace(MongoClient=build)}):
            client = connect_nosql("mongodb", _profile("mongodb", host="mongodb://db.fixture.invalid/fixture?authSource=admin"), "", ssh_manager=manager)
            self.assertEqual(calls[0][0], ("mongodb://127.0.0.1:49153/fixture?authSource=admin",))
            self.assertIs(calls[0][1]["directConnection"], True)
            manager.lease.results = [False, True]
            with self.assertRaisesRegex(ConnectionTransportError, "SSH_CLEANUP_PENDING"):
                client.close()
            client.close()
            self.assertEqual(events, ["client.close", "release", "release"])

    def test_direct_tls_clients_require_certificate_and_hostname_validation(self):
        for provider in ("redis", "mongodb"):
            with self.subTest(provider=provider):
                calls = []
                def build(**kwargs):
                    calls.append(kwargs)
                    return _Client([])
                profile = _profile(provider, ssh_enabled=False, tls_enabled=True)
                for key in list(profile):
                    if key.startswith("ssh_") and key != "ssh_enabled":
                        profile.pop(key)
                with patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=build), "pymongo": SimpleNamespace(MongoClient=build)}):
                    client = connect_nosql(provider, profile, "")
                    client.close()
                if provider == "redis":
                    self.assertEqual(calls[0]["ssl_cert_reqs"], ssl.CERT_REQUIRED)
                    self.assertIs(calls[0]["ssl_check_hostname"], True)
                else:
                    self.assertIs(calls[0]["tlsAllowInvalidCertificates"], False)
                    self.assertIs(calls[0]["tlsAllowInvalidHostnames"], False)

    def test_native_workers_use_injected_opener_and_close_owned_mongo_client(self):
        from panels.db_mongodb_panel import _MongoWorker
        from panels.db_redis_panel import _RedisWorker
        for provider, worker_type in (("redis", _RedisWorker), ("mongodb", _MongoWorker)):
            with self.subTest(provider=provider):
                events = []
                manager = _Manager(events)
                def build(*args, **kwargs):
                    return _Client(events)
                with patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=build), "pymongo": SimpleNamespace(MongoClient=build)}), patch(
                    "tools.data_center.nosql_transport.default_ssh_tunnel_manager", return_value=manager
                ):
                    worker = worker_type("test", _profile(provider), connection_opener=open_data_center_nosql_connection)
                    failures, results = [], []
                    worker.failed.connect(lambda *args: failures.append(args))
                    worker.completed.connect(lambda *args: results.append(args))
                    worker.run()
                    self.assertEqual(failures, [])
                    self.assertEqual(len(results), 1)
                    self.assertEqual(events, ["client.close", "release"])

    def test_connection_settings_worker_runs_actual_thread_and_probes_nosql(self):
        from PyQt6.QtCore import Qt
        from ui.data_center.connection_transport import ConnectionTransportWorker
        for provider in ("redis", "mongodb"):
            with self.subTest(provider=provider):
                events, results = [], []
                def build(*args, **kwargs):
                    return _Client(events)
                with patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=build), "pymongo": SimpleNamespace(MongoClient=build)}):
                    worker = ConnectionTransportWorker("connect", _profile(provider), manager=_Manager(events))
                    worker.completed.connect(lambda *args: results.append(args), Qt.ConnectionType.DirectConnection)
                    worker.start()
                    self.assertTrue(worker.wait(3000))
                    self.assertEqual(results, [("connect", {"state": "connected"})])
                    self.assertEqual(events, ["client.close", "release"])

    def test_failed_probe_retains_database_owner_until_close_succeeds(self):
        from tools.data_center.connection_transport import retry_default_transport_cleanup
        events = []
        class Client(_Client):
            def ping(self):
                raise RuntimeError("fixture probe failure")
            def close(self):
                self.events.append("client.close")
                if len(self.events) == 1:
                    raise RuntimeError("fixture pending close")
        with patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=lambda **kwargs: Client(events))}), patch(
            "tools.data_center.connection_transport._default_manager", None
        ):
            with self.assertRaisesRegex(ConnectionTransportError, "SSH_CLEANUP_PENDING"):
                connect_nosql("redis", _profile("redis"), "", ssh_manager=_Manager(events), probe=True)
            self.assertEqual(events, ["client.close"])
            self.assertTrue(retry_default_transport_cleanup())
            self.assertEqual(events, ["client.close", "client.close", "release"])


if __name__ == "__main__":
    unittest.main()
