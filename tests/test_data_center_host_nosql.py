"""Host profile to NoSQL facade tests using only in-memory fake clients."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from tools.data_center.host_nosql import (
    HostNoSQLFactory,
    HostNoSQLLease,
    HostNoSQLRouter,
    NoSQLReadOnlyHost,
    NoSQLClientBundle,
    NoSQLScopeError,
    NoSQLTarget,
    compute_profile_revision,
)
from tools.data_center.contracts import ToolResult


class _RedisClient:
    socket_timeout = 1.0

    def __init__(self) -> None:
        self.closed = 0
        self.calls: list[tuple[str, object]] = []

    def type(self, name):
        self.calls.append(("type", name))
        return b"string"

    def close(self) -> None:
        self.closed += 1


class _MongoCursor:
    def __init__(self, values) -> None:
        self.values = list(values)
        self.closed = False

    def __iter__(self):
        return iter(self.values)

    def close(self) -> None:
        self.closed = True


class _MongoCollection:
    def __init__(self, values) -> None:
        self.values = list(values)
        self.calls: list[tuple[str, dict]] = []
        self.cursors: list[_MongoCursor] = []

    def find(self, **kwargs):
        self.calls.append(("find", kwargs))
        cursor = _MongoCursor(self.values)
        self.cursors.append(cursor)
        return cursor


class _MongoDatabase:
    def __init__(self, collections) -> None:
        self.collections = dict(collections)

    def __getitem__(self, name):
        return self.collections[name]


class _MongoClient:
    socket_timeout = 1.0

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _MongoOwner:
    socket_timeout = 1.0

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _LeakyFacade:
    def __init__(self) -> None:
        self.closed = 0

    def execute(self, request, **kwargs):  # noqa: ARG002
        return ToolResult(
            ok=True,
            data={
                "uri": "mongodb://private-user:private-secret@db.internal/analytics",
                "password": "private-secret",
                "value": "safe",
            },
            scope={"uri": "redis://private-user:private-secret@db.internal"},
            limits={"dsn": "mongodb+srv://private-secret@cluster.example"},
        )

    def close(self) -> None:
        self.closed += 1


def _redis_profile(**changes):
    profile = {
        "id": "redis-1",
        "profile_revision": "rev-1",
        "dialect": "redis",
        "mode": "cluster",
        "host": "seed-a",
        "port": 6379,
        "seed_nodes": [
            {"host": "seed-a", "port": 6379},
            {"host": "seed-b", "port": 6380},
        ],
        "auth_mode": "acl",
        "username": "private-user",
        "password": "private-token",
    }
    profile.update(changes)
    if "profile_revision" not in changes and "revision" not in changes:
        profile["profile_revision"] = compute_profile_revision(profile)
    return profile


def _mongo_profile(**changes):
    profile = {
        "id": "mongo-1",
        "profile_revision": "rev-1",
        "dialect": "mongodb",
        "mode": "cluster",
        "host": "mongo-seed-a",
        "port": 27017,
        "database": "analytics",
        "seed_nodes": [
            {"host": "mongo-seed-a", "port": 27017},
            {"host": "mongo-seed-b", "port": 27018},
        ],
        "replica_set_name": "rs-analytics",
        "username": "private-user",
        "password": "private-token",
        "auth_source": "admin",
    }
    profile.update(changes)
    if "profile_revision" not in changes and "revision" not in changes:
        profile["profile_revision"] = compute_profile_revision(profile)
    return profile


class HostNoSQLFactoryTests(unittest.TestCase):
    def test_host_resolves_legacy_profile_lazily_and_computes_revision(self) -> None:
        profile = _redis_profile()
        profile.pop("profile_revision")
        calls = []
        host = NoSQLReadOnlyHost(
            connections={"redis-1": profile},
            redis_client_factory=lambda profile, secret, options: calls.append(options) or _RedisClient(),
        )
        snapshot = host.profile_snapshot("redis-1")
        self.assertTrue(snapshot.profile_revision)
        self.assertEqual(calls, [])
        factory = host.create_factory("redis-1")
        self.assertIsNone(factory.target)
        lease = factory("redis-1")
        self.assertEqual(factory.target.profile_revision, snapshot.profile_revision)
        self.assertEqual(lease.mode, "cluster")
        lease.close()

    def test_host_accepts_a_direct_profile_and_worker_session_metadata(self) -> None:
        profile = _redis_profile()
        client = _RedisClient()
        host = NoSQLReadOnlyHost(
            profile,
            secret_loader=lambda profile: "private-secret",
            redis_client_factory=lambda profile, secret, options: client,
        )
        lease = host.connect("redis-1")
        self.assertEqual(lease.connection_id, "redis-1")
        lease.close()
        lease = host("redis-1", session_id="tab-1", generation=1)
        self.assertEqual(lease.connection_id, "redis-1")
        lease.close()
        self.assertEqual(client.closed, 2)

    def test_redis_profile_is_bound_and_cluster_seeds_are_passed_without_leaking_private_values(self) -> None:
        client = _RedisClient()
        calls = []
        profile = _redis_profile()
        revision = compute_profile_revision(profile)

        def factory(profile, secret, options):
            calls.append((profile, secret, options))
            return client

        factory_host = HostNoSQLFactory(
            profile,
            secret_loader=lambda profile: "private-secret",
            redis_client_factory=factory,
        )
        lease = factory_host.connect()
        self.assertEqual(lease.connection_id, "redis-1")
        self.assertEqual(lease.profile_revision, revision)
        self.assertEqual(lease.provider, "redis")
        self.assertEqual(lease.mode, "cluster")
        self.assertEqual([item["host"] for item in calls[0][2]["seed_nodes"]], ["seed-a", "seed-b"])
        self.assertEqual(calls[0][1], "private-secret")
        self.assertNotIn("private-secret", repr(factory_host))
        self.assertNotIn("private-token", repr(factory_host))
        result = HostNoSQLRouter(lease).execute("redis_read", {"operation": "TYPE", "key": "orders:1"})
        self.assertTrue(result.ok)
        self.assertEqual(result.scope["profile_revision"], revision)
        self.assertNotIn("seed-a", repr(result))
        lease.close()
        lease.close()
        factory_host.close()
        self.assertEqual(client.closed, 1)

    def test_mongo_scope_and_allowlist_are_host_owned(self) -> None:
        client = _MongoClient()
        orders = _MongoCollection([{"id": 1}])
        audit = _MongoCollection([{"id": 2}])
        database = _MongoDatabase({"orders": orders, "audit": audit})
        calls = []

        def mongo_factory(profile, secret, options):
            calls.append(options)
            return NoSQLClientBundle(
                client=client,
                database=database,
                collections={"orders": orders, "audit": audit},
            )

        factory_host = HostNoSQLFactory(
            _mongo_profile(),
            allowed_collection_ids=("orders",),
            secret_loader=lambda profile: "private-secret",
            mongo_client_factory=mongo_factory,
        )
        lease = factory_host.connect()
        self.assertEqual([item["host"] for item in calls[0]["seed_nodes"]], ["mongo-seed-a", "mongo-seed-b"])
        self.assertEqual(calls[0]["replica_set"], "rs-analytics")
        self.assertFalse(calls[0]["direct_connection"])
        self.assertEqual(calls[0]["serverSelectionTimeoutMS"], 15_000)
        self.assertEqual(calls[0]["socketTimeoutMS"], 15_000)
        result = lease.execute({"collection_id": "orders", "operation": "find"})
        self.assertTrue(result.ok)
        self.assertEqual(result.scope["provider"], "mongodb")
        self.assertEqual(result.scope["mode"], "cluster")
        self.assertEqual(result.scope["database"], "analytics")
        denied = lease.execute({"collection_id": "audit", "operation": "find"})
        self.assertFalse(denied.ok)
        self.assertEqual(denied.code, "SCOPE_DENIED")
        self.assertEqual(audit.calls, [])
        lease.close()
        lease.close()
        self.assertEqual(client.closed, 1)

    def test_distinct_mongo_owner_is_closed_once(self) -> None:
        client = _MongoClient()
        owner = _MongoOwner()
        orders = _MongoCollection([{"id": 1}])
        database = _MongoDatabase({"orders": orders})
        factory_host = HostNoSQLFactory(
            _mongo_profile(),
            allowed_collection_ids=("orders",),
            secret_loader=lambda profile: "private-secret",
            mongo_client_factory=lambda profile, secret, options: NoSQLClientBundle(
                client=client,
                owner=owner,
                database=database,
                collections={"orders": orders},
            ),
        )
        lease = factory_host.connect()
        lease.close()
        self.assertEqual(owner.closed, 1)
        self.assertEqual(client.closed, 1)

    def test_profile_identity_mismatch_fails_before_secret_or_client_factory(self) -> None:
        secret_calls = []
        client_calls = []

        def secret_loader(profile):
            secret_calls.append(profile)
            return "private-secret"

        def client_factory(*args, **kwargs):
            client_calls.append((args, kwargs))
            return _RedisClient()

        factory_host = HostNoSQLFactory(
            _redis_profile(profile_revision="stale"),
            profile_revision="stale-target",
            secret_loader=secret_loader,
            redis_client_factory=client_factory,
        )
        with self.assertRaises(NoSQLScopeError):
            factory_host.connect()
        self.assertEqual(secret_calls, [])
        self.assertEqual(client_calls, [])

    def test_profile_revision_tracks_current_content_instead_of_legacy_field(self) -> None:
        current = _redis_profile(
            profile_revision="legacy-fixed-revision",
            revision="another-old-revision",
        )
        secret_calls = []
        host = NoSQLReadOnlyHost(
            profile_loader=lambda _connection_id: current,
            secret_loader=lambda _profile: secret_calls.append(True) or "private-secret",
            redis_client_factory=lambda **_: _RedisClient(),
        )

        snapshot = host.profile_snapshot("redis-1")
        self.assertEqual(snapshot.profile_revision, compute_profile_revision(current))
        self.assertNotEqual(snapshot.profile_revision, "legacy-fixed-revision")

        factory = host.create_factory(
            "redis-1",
            profile_revision=snapshot.profile_revision,
            provider="redis",
            mode="cluster",
        )
        current["host"] = "changed.internal"
        current["password"] = "rotated-secret-token"
        with self.assertRaises(Exception) as raised:
            factory("redis-1")
        self.assertIn(raised.exception.code, {"PROFILE_MISMATCH", "PROFILE_UNAVAILABLE"})
        self.assertEqual(secret_calls, [])

    def test_revision_normalizes_identity_spellings(self) -> None:
        profile = _redis_profile()
        alternate = dict(profile)
        alternate.pop("id")
        alternate.pop("dialect")
        alternate["connection_id"] = "redis-1"
        alternate["provider"] = "redis"
        self.assertEqual(compute_profile_revision(profile), compute_profile_revision(alternate))

    def test_mongo_replica_set_and_srv_topologies_keep_distinct_identity(self) -> None:
        orders = _MongoCollection([{"id": 1}])
        database = _MongoDatabase({"orders": orders})
        replica_profile = _mongo_profile(mode="replica_set")
        replica_calls = []
        replica_host = NoSQLReadOnlyHost(
            connections={"mongo-1": replica_profile},
            secret_loader=lambda _profile: "private-secret",
            mongo_client_factory=lambda profile, secret, options: replica_calls.append(options)
            or NoSQLClientBundle(
                client=_MongoClient(),
                database=database,
                collections={"orders": orders},
            ),
        )
        replica_snapshot = replica_host.profile_snapshot("mongo-1")
        self.assertEqual(replica_snapshot.mode, "replica_set")
        replica_lease = replica_host.create_factory(
            "mongo-1",
            profile_revision=replica_snapshot.profile_revision,
            provider="mongodb",
            mode="replica_set",
            database_name="analytics",
            allowed_collection_ids=("orders",),
        )("mongo-1")
        self.assertEqual(replica_calls[0]["mode"], "replica_set")
        self.assertEqual(replica_calls[0]["replica_set"], "rs-analytics")
        replica_lease.close()

        srv_profile = _mongo_profile(
            mode="",
            host="mongodb+srv://cluster.example/analytics",
            seed_nodes=[],
        )
        srv_calls = []
        srv_host = NoSQLReadOnlyHost(
            connections={"mongo-1": srv_profile},
            secret_loader=lambda _profile: "private-secret",
            mongo_client_factory=lambda profile, secret, options: srv_calls.append(options)
            or NoSQLClientBundle(
                client=_MongoClient(),
                database=database,
                collections={"orders": orders},
            ),
        )
        srv_snapshot = srv_host.profile_snapshot("mongo-1")
        self.assertEqual(srv_snapshot.mode, "srv")
        srv_lease = srv_host.create_factory(
            "mongo-1",
            profile_revision=srv_snapshot.profile_revision,
            provider="mongodb",
            mode="srv",
            database_name="analytics",
            allowed_collection_ids=("orders",),
        )("mongo-1")
        self.assertEqual(srv_calls[0]["mode"], "srv")
        self.assertEqual(srv_calls[0]["uri"], "mongodb+srv://cluster.example/analytics")
        srv_lease.close()

    def test_unknown_or_lossy_topology_combinations_are_rejected(self) -> None:
        invalid_profiles = (
            _redis_profile(mode="srv"),
            _mongo_profile(mode="srv"),
            _mongo_profile(mode="cluster", host="mongodb+srv://cluster.example/analytics", seed_nodes=[]),
            _mongo_profile(replica_set_name="rs-a", replicaSet="rs-b"),
            _redis_profile(mode="unknown-mode"),
            _redis_profile(mode="standalone"),
        )
        invalid_profiles[-1]["seed_nodes"] = [
            {"host": "seed-a", "port": 6379},
            {"host": "seed-b", "port": 6380},
        ]
        for profile in invalid_profiles:
            with self.assertRaises(Exception):
                NoSQLReadOnlyHost(profile).profile_snapshot(profile["id"])

    def test_default_factories_pass_real_timeout_values(self) -> None:
        import tools.data_center.host_nosql as module

        redis_seen = {}

        class FakeRedis:
            def __init__(self, **kwargs):
                redis_seen.update(kwargs)

        fake_redis = types.SimpleNamespace(Redis=FakeRedis)
        with patch.dict(sys.modules, {"redis": fake_redis}):
            module._default_redis_client_factory(
                _redis_profile(mode="standalone", seed_nodes=[{"host": "seed-a", "port": 6379}], auth_mode="none"),
                "",
                options={
                    "mode": "standalone",
                    "seed_nodes": [{"host": "seed-a", "port": 6379}],
                    "socket_timeout_ms": 2200,
                },
            )
        self.assertEqual(redis_seen["socket_connect_timeout"], 2.2)
        self.assertEqual(redis_seen["socket_timeout"], 2.2)

        mongo_seen = {}

        class FakeMongoClient:
            def __init__(self, *args, **kwargs):
                mongo_seen["args"] = args
                mongo_seen.update(kwargs)

        fake_pymongo = types.SimpleNamespace(MongoClient=FakeMongoClient)
        with patch.dict(sys.modules, {"pymongo": fake_pymongo}):
            module._default_mongo_client_factory(
                _mongo_profile(mode="standalone", seed_nodes=[{"host": "mongo-seed-a", "port": 27017}], username="", password=""),
                "",
                options={
                    "mode": "standalone",
                    "seed_nodes": [{"host": "mongo-seed-a", "port": 27017}],
                    "server_selection_timeout_ms": 2300,
                    "socket_timeout_ms": 2400,
                },
            )
        self.assertEqual(mongo_seen["serverSelectionTimeoutMS"], 2300)
        self.assertEqual(mongo_seen["socketTimeoutMS"], 2400)

    def test_failed_mongo_build_closes_distinct_client_and_owner(self) -> None:
        client = _MongoClient()
        owner = _MongoOwner()
        database = _MongoDatabase({})
        host = HostNoSQLFactory(
            _mongo_profile(),
            allowed_collection_ids=("orders",),
            secret_loader=lambda _profile: "private-secret",
            mongo_client_factory=lambda profile, secret, options: NoSQLClientBundle(
                client=client,
                owner=owner,
                database=database,
            ),
        )
        with self.assertRaises(Exception) as raised:
            host.connect()
        self.assertEqual(getattr(raised.exception, "code", None), "CLIENT_CREATE_FAILED")
        self.assertEqual(client.closed, 1)
        self.assertEqual(owner.closed, 1)

    def test_result_scope_and_payload_redact_sensitive_values(self) -> None:
        target = NoSQLTarget("redis-1", "revision", "redis", "standalone")
        facade = _LeakyFacade()
        lease = HostNoSQLLease(target, facade, facade, facade)
        result = lease.execute({}, query_id="safe-query")
        rendered = repr(result.as_dict())
        self.assertEqual(result.scope, target.scope)
        self.assertNotIn("mongodb://", rendered)
        self.assertNotIn("redis://", rendered)
        self.assertNotIn("private-secret", rendered)
        lease.close()

    def test_public_surface_keeps_only_canonical_nosql_entries(self) -> None:
        import tools.data_center.host_nosql as module

        removed = (
            "HostNoSQLAdapter",
            "NoSQLConnectionHost",
            "NoSQLConnectionTarget",
            "NoSQLFacadeFactory",
            "NoSQLHostAdapter",
            "NoSQLHostFactory",
            "NoSQLHostLease",
            "NoSQLHostRouter",
            "NoSQLLeaseHost",
            "ReadOnlyNoSQLFactory",
            "ReadOnlyNoSQLHost",
            "ReadOnlyNoSQLLease",
            "ReadOnlyNoSQLRouter",
            "LazyNoSQLFacadeFactory",
            "build_readonly_nosql_factory",
            "calculate_profile_revision",
            "create_readonly_nosql_factory",
            "make_readonly_nosql_factory",
            "profile_revision_for",
            "stable_profile_revision",
        )
        self.assertTrue(all(not hasattr(module, name) for name in removed))
        self.assertEqual(
            set(module.__all__),
            {
                "DEFAULT_TIMEOUT_MS",
                "HostNoSQLFactory",
                "HostNoSQLLease",
                "HostNoSQLRouter",
                "LazyNoSQLFactory",
                "NoSQLCapabilityError",
                "NoSQLClientBundle",
                "NoSQLClientError",
                "NoSQLHostError",
                "NoSQLProfileError",
                "NoSQLProfileSnapshot",
                "NoSQLReadOnlyHost",
                "NoSQLScopeError",
                "NoSQLSecretError",
                "NoSQLTarget",
                "compute_profile_revision",
            },
        )

    def test_conflicting_profile_aliases_and_missing_required_secret_fail_closed(self) -> None:
        with self.assertRaises(NoSQLScopeError):
            HostNoSQLFactory(
                _redis_profile(provider="redis", dialect="mongodb"),
                redis_client_factory=lambda **_: _RedisClient(),
            ).connect()

        calls = []
        host = NoSQLReadOnlyHost(
            _redis_profile(username="private-user", password=""),
            redis_client_factory=lambda **_: calls.append(True) or _RedisClient(),
        )
        with self.assertRaises(Exception) as raised:
            host.connect("redis-1")
        self.assertEqual(getattr(raised.exception, "code", None), "SECRET_UNAVAILABLE")
        self.assertEqual(calls, [])

    def test_deadline_without_a_client_timeout_fails_closed(self) -> None:
        client = object()
        factory_host = HostNoSQLFactory(
            _redis_profile(auth_mode="none", username="", password=""),
            redis_client_factory=lambda profile, secret, options: client,
        )
        lease = factory_host.connect()
        result = lease.execute(
            {"operation": "TYPE", "key": "orders:1"},
            deadline=10**12,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "TIMEOUT_UNAVAILABLE")
        lease.close()


class NoSQLTargetTests(unittest.TestCase):
    def test_mongo_target_requires_fixed_database_and_scope(self) -> None:
        with self.assertRaises(ValueError):
            NoSQLTarget("mongo-1", "rev-1", "mongodb", "cluster")
        target = NoSQLTarget("mongo-1", "rev-1", "mongo", "cluster", "analytics", ("Orders",))
        self.assertEqual(target.provider, "mongodb")
        self.assertEqual(target.allowed_collection_ids, ("orders",))
        self.assertNotIn("host", repr(target))


if __name__ == "__main__":
    unittest.main()
