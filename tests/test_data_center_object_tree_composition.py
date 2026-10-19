"""Composition tests using the public lazy hosts and synthetic clients only."""

from __future__ import annotations

import threading
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import tools.data_center.object_tree_composition as composition_module
from tools.data_center.host_nosql import NoSQLReadOnlyHost, compute_profile_revision as compute_nosql_revision
from tools.data_center.host_relational import RelationalReadOnlyHost, compute_profile_revision as compute_relational_revision
from tools.data_center.object_tree import ObjectTreeError, TreePage, TreeTarget
from tools.data_center.object_tree_composition import ObjectTreeComposition


def _mysql_profile(connection_id: str = "sql-1", **changes):
    profile = {
        "id": connection_id,
        "dialect": "mysql",
        "mode": "mysql",
        "database": "stored-database",
        "host": "synthetic-host",
        "port": 3306,
    }
    profile.update(changes)
    return profile


def _oceanbase_profile(connection_id: str = "ob-1", **changes):
    profile = {
        "id": connection_id,
        "dialect": "oceanbase",
        "mode": "oracle",
        "host": "synthetic-host",
        "port": 2881,
    }
    profile.update(changes)
    return profile


def _oracle_profile(connection_id: str = "oracle-1", **changes):
    profile = {
        "id": connection_id,
        "provider": "oracle",
        "engine": "oracle",
        "dialect": "oracle",
        "mode": "oracle",
        "database": "service.prod",
        "schema": "LOGIN",
        "host": "synthetic-host",
        "port": 1521,
        "username": "synthetic-user",
        "password": "TEMP-CIPHER",
    }
    profile.update(changes)
    return profile


def _redis_profile(connection_id: str = "redis-1", **changes):
    profile = {
        "id": connection_id,
        "dialect": "redis",
        "mode": "standalone",
        "host": "synthetic-host",
        "port": 6379,
        "auth_mode": "none",
    }
    profile.update(changes)
    return profile


def _mongo_profile(connection_id: str = "mongo-1", **changes):
    profile = {
        "id": connection_id,
        "dialect": "mongodb",
        "mode": "standalone",
        "host": "synthetic-host",
        "port": 27017,
        "database": "profile-database",
    }
    profile.update(changes)
    return profile


class _SQLCursor:
    def __init__(self, connection: "_SQLConnection") -> None:
        self.connection = connection
        self.description = ()
        self.rows = []

    def execute(self, sql, parameters=()):
        self.connection.events.append(("execute", threading.get_ident(), sql, tuple(parameters)))
        folded = sql.casefold()
        if "information_schema.schemata" in folded:
            self.rows = [("catalog-db",)]
        elif "information_schema.tables" in folded:
            self.rows = [("items", "BASE TABLE")]
        elif "select distinct owner" in folded:
            self.rows = [("APP",)]
        elif "select object_name, object_type" in folded:
            self.rows = [("items", "TABLE"), ("items_view", "VIEW")]
        else:
            self.rows = []

    def fetchmany(self, size):
        return self.rows[:size]

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _SQLConnection:
    def __init__(self, events) -> None:
        self.events = events
        self.autocommit = True
        self.closed = False

    def cursor(self):
        return _SQLCursor(self)

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


class _RedisClient:
    def __init__(self, events) -> None:
        self.events = events
        self.connection_pool = type("Pool", (), {"connection_kwargs": {"socket_timeout": 10}})()

    def scan(self, cursor=0, match=None, count=None):
        self.events.append(("scan", threading.get_ident(), cursor, match, count))
        key = b"tenant/item" if match and match.startswith("tenant/") else b"plain/item"
        return 0, [key]

    def type(self, name):
        self.events.append(("type", threading.get_ident(), name))
        return b"string"

    def ttl(self, name):
        self.events.append(("ttl", threading.get_ident(), name))
        return -1

    def close(self):
        self.events.append(("redis_close", threading.get_ident()))


class _MongoDatabase:
    def __init__(self, client: "_MongoClient", name: str) -> None:
        self.client = client
        self.name = name

    def list_collections(self, nameOnly=True, batchSize=0):
        self.client.events.append(("collections", threading.get_ident(), self.name, nameOnly, batchSize))
        return [{"name": "items"}]


class _MongoClient:
    def __init__(self, events) -> None:
        self.events = events

    def list_databases(self, nameOnly=True, authorizedDatabases=True, batchSize=0):
        self.events.append(("databases", threading.get_ident(), nameOnly, authorizedDatabases, batchSize))
        return [{"name": "listed-database"}]

    def get_database(self, name):
        self.events.append(("get_database", threading.get_ident(), name))
        return _MongoDatabase(self, name)

    def __getitem__(self, name):
        return self.get_database(name)

    def close(self):
        self.events.append(("mongo_close", threading.get_ident()))


class _ObservedNoSQLHost(NoSQLReadOnlyHost):
    def __init__(self, *args, **kwargs) -> None:
        self.snapshot_threads = []
        self.directory_factory_calls = []
        super().__init__(*args, **kwargs)

    def profile_snapshot(self, connection_id):
        self.snapshot_threads.append(threading.get_ident())
        return super().profile_snapshot(connection_id)

    def create_directory_factory(self, connection_id, **kwargs):
        self.directory_factory_calls.append(
            (threading.get_ident(), connection_id, kwargs.get("database_name", ""))
        )
        return super().create_directory_factory(connection_id, **kwargs)


class ObjectTreeCompositionTests(unittest.TestCase):
    def test_default_construction_does_not_resolve_profiles_or_open_connections(self):
        with (
            patch.object(
                composition_module.RelationalReadOnlyHost,
                "profile_snapshot",
                side_effect=AssertionError("construction must not resolve a profile"),
            ),
            patch.object(
                composition_module.NoSQLReadOnlyHost,
                "profile_snapshot",
                side_effect=AssertionError("construction must not resolve a profile"),
            ),
        ):
            composition = ObjectTreeComposition()
            self.assertIsNotNone(composition.service)
            self.assertTrue(composition.close().result(timeout=3).completed)

    def test_relational_capture_uses_public_profile_and_lazy_mysql_directory(self):
        profile = _mysql_profile()
        main_thread = threading.get_ident()
        loader_threads = []
        connector_calls = []
        connections = []
        secret_calls = []
        sql_events = []

        def profile_loader(connection_id):
            loader_threads.append(threading.get_ident())
            self.assertEqual(connection_id, "sql-1")
            return profile

        def connector(provider, profile, secret, **options):
            connector_calls.append((threading.get_ident(), provider, secret, dict(options)))
            connection = _SQLConnection(sql_events)
            connections.append(connection)
            return connection

        relational = RelationalReadOnlyHost(
            profile_loader=profile_loader,
            secret_loader=lambda _profile: (secret_calls.append(threading.get_ident()) or ""),
            connector=connector,
        )
        nosql = NoSQLReadOnlyHost(profile_loader=lambda _connection_id: self.fail("NoSQL host must not be probed after SQL success"))
        composition = ObjectTreeComposition(relational_host=relational, nosql_host=nosql)
        self.assertEqual(loader_threads, [])
        self.assertEqual(connector_calls, [])
        self.assertEqual(secret_calls, [])

        target = composition.submit_target_capture(
            "sql-1",
            database_scope="caller-database",
        ).result(timeout=3)
        self.assertEqual(target.connection_id, "sql-1")
        self.assertEqual(target.profile_revision, compute_relational_revision(profile))
        self.assertEqual((target.provider, target.mode, target.database), ("mysql", "mysql", "caller-database"))
        self.assertEqual(len(loader_threads), 1)
        self.assertNotEqual(loader_threads[0], main_thread)
        self.assertEqual(connector_calls, [])
        self.assertEqual(secret_calls, [])

        root = composition.service.root(target)
        self.assertEqual(connector_calls, [])  # Issuing the tree root remains zero I/O.
        databases = composition.service.submit_children(target, root).future.result(timeout=3)
        self.assertIsInstance(databases, TreePage)
        self.assertEqual([(node.kind, node.name) for node in databases.nodes], [("database", "catalog-db")])
        objects = composition.service.submit_children(target, databases.nodes[0]).future.result(timeout=3)
        self.assertEqual([(node.kind, node.name) for node in objects.nodes], [("table", "items")])
        self.assertEqual(len(connector_calls), 2)
        self.assertTrue(all(call[0] != main_thread for call in connector_calls))
        worker_ids = {thread_id for _kind, thread_id, *_rest in sql_events}
        worker_ids.update(call[0] for call in connector_calls)
        self.assertEqual(len(worker_ids), 1)
        self.assertEqual(len(connections), 2)
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertEqual(len(secret_calls), 2)
        self.assertTrue(all(thread_id in worker_ids for thread_id in secret_calls))
        self.assertTrue(composition.close().result(timeout=3).completed)

    def test_oracle_tree_service_uses_revision_checked_connector_scope_without_io(self):
        profile = _oracle_profile(schema="PROFILE-OWNER")
        main_thread = threading.get_ident()
        secret_calls = []
        connector_calls = []
        connections = []

        def connector(provider, profile, secret, **options):
            connector_calls.append((threading.get_ident(), provider, dict(profile), secret, dict(options)))
            connection = _SQLConnection([])
            connections.append(connection)
            return connection

        host = RelationalReadOnlyHost(
            profile_loader=lambda connection_id: profile if connection_id == profile["id"] else None,
            secret_loader=lambda _profile: (secret_calls.append(threading.get_ident()) or "TEMP-SECRET"),
            connector=connector,
        )
        composition = ObjectTreeComposition(relational_host=host)
        try:
            # The empty UI request binds to the actual direct-connector
            # service field, not the profile's independent default schema.
            target = composition.submit_target_capture(profile["id"]).result(timeout=3)
            self.assertEqual(target.database, "service.prod")
            self.assertEqual((secret_calls, connector_calls), ([], []))

            matching = composition.submit_target_capture(
                profile["id"], database_scope="service.prod",
            ).result(timeout=3)
            self.assertEqual(matching.database, "service.prod")
            wrong = composition.submit_target_capture(profile["id"], database_scope="other.service")
            with self.assertRaises(ObjectTreeError) as wrong_scope:
                wrong.result(timeout=3)
            self.assertEqual(wrong_scope.exception.code, "SCOPE_DENIED")
            self.assertEqual((secret_calls, connector_calls), ([], []))

            root = composition.service.root(target)
            schemas = composition.service.submit_children(target, root, generation=29).future.result(timeout=3)
            self.assertEqual([(node.kind, node.name, node.database) for node in schemas.nodes], [("schema", "APP", "service.prod")])
            objects = composition.service.submit_children(
                target, schemas.nodes[0], generation=29,
            ).future.result(timeout=3)
            self.assertEqual([(node.kind, node.name, node.database) for node in objects.nodes], [
                ("table", "items", "service.prod"), ("view", "items_view", "service.prod"),
            ])
            self.assertTrue(secret_calls)
            self.assertEqual(len(secret_calls), len(connector_calls))
            self.assertNotEqual(secret_calls[0], main_thread)
        finally:
            self.assertTrue(composition.close().result(timeout=3).completed)

    def test_oracle_service_capture_rejects_revision_race_empty_direct_service_and_preserves_ob_odbc_value(self):
        original = _oracle_profile()
        changed = dict(original, database="changed.service")
        load_count = [0]
        race_secrets = []
        race_connects = []

        def racing_loader(connection_id):
            del connection_id
            load_count[0] += 1
            return original if load_count[0] == 1 else changed

        race_host = RelationalReadOnlyHost(
            profile_loader=racing_loader,
            secret_loader=lambda _profile: (race_secrets.append(True) or "TEMP"),
            connector=lambda *args, **kwargs: (race_connects.append((args, kwargs)) or _SQLConnection([])),
        )
        race_composition = ObjectTreeComposition(relational_host=race_host)
        try:
            request = race_composition.submit_target_capture(original["id"])
            with self.assertRaises(ObjectTreeError) as raced:
                request.result(timeout=3)
            self.assertEqual(raced.exception.code, "PROFILE_MISMATCH")
            self.assertEqual(load_count[0], 2)
            self.assertEqual((race_secrets, race_connects), ([], []))
        finally:
            self.assertTrue(race_composition.close().result(timeout=3).completed)

        empty_service = _oracle_profile(database="", schema="fallback-is-not-the-connector-service")
        empty_calls = []
        empty_host = RelationalReadOnlyHost(
            profile_loader=lambda _connection_id: empty_service,
            secret_loader=lambda _profile: (empty_calls.append("secret") or "TEMP"),
            connector=lambda *args, **kwargs: (empty_calls.append("connect") or _SQLConnection([])),
        )
        empty_composition = ObjectTreeComposition(relational_host=empty_host)
        try:
            with self.assertRaises(ObjectTreeError) as absent:
                empty_composition.submit_target_capture(empty_service["id"]).result(timeout=3)
            self.assertEqual(absent.exception.code, "PROFILE_UNAVAILABLE")
            self.assertEqual(empty_calls, [])
        finally:
            self.assertTrue(empty_composition.close().result(timeout=3).completed)

        schema_only = _oracle_profile("oracle-schema-service")
        schema_only.pop("database")
        schema_only["schema"] = "schema-is-service"
        schema_composition = ObjectTreeComposition(
            relational_host=RelationalReadOnlyHost(profile_loader=lambda _connection_id: schema_only),
        )
        try:
            schema_target = schema_composition.submit_target_capture(schema_only["id"]).result(timeout=3)
            self.assertEqual(schema_target.database, "schema-is-service")
        finally:
            self.assertTrue(schema_composition.close().result(timeout=3).completed)

        # OceanBase Oracle uses the ODBC Database attribute, whose established
        # connector contract accepts service strings outside python-oracledb's
        # narrower service-name regex.
        ob_profile = _oceanbase_profile(
            "ob-oracle-scope", database="OB Service With Space", schema="LOGIN",
            username="synthetic-user", password="TEMP-CIPHER", oceanbase_oracle_provider="odbc",
        )
        ob_host = RelationalReadOnlyHost(profile_loader=lambda _connection_id: ob_profile)
        ob_composition = ObjectTreeComposition(relational_host=ob_host)
        try:
            with self.assertRaises(ObjectTreeError) as ob_mismatch:
                ob_composition.submit_target_capture(
                    ob_profile["id"], database_scope="another-odbc-database",
                ).result(timeout=3)
            self.assertEqual(ob_mismatch.exception.code, "SCOPE_DENIED")
            ob_target = ob_composition.submit_target_capture(ob_profile["id"]).result(timeout=3)
            self.assertEqual((ob_target.provider, ob_target.database), ("oceanbase_oracle", "OB Service With Space"))
        finally:
            self.assertTrue(ob_composition.close().result(timeout=3).completed)

    def test_redis_capture_requires_explicit_prefix_and_stale_profile_fails_closed(self):
        profile = _redis_profile()
        client_events = []
        client_factory_calls = []

        def client_factory(**kwargs):
            client_factory_calls.append((threading.get_ident(), kwargs.get("mode")))
            return _RedisClient(client_events)

        host = _ObservedNoSQLHost(
            connections={"redis-1": profile},
            redis_client_factory=client_factory,
        )
        relational = RelationalReadOnlyHost(connections={"redis-1": profile})
        composition = ObjectTreeComposition(relational_host=relational, nosql_host=host)

        missing_prefix = composition.submit_target_capture("redis-1")
        with self.assertRaises(ObjectTreeError) as captured:
            missing_prefix.result(timeout=3)
        self.assertEqual(captured.exception.code, "ARGUMENT_INVALID")
        self.assertEqual(client_factory_calls, [])

        target = composition.submit_target_capture("redis-1", redis_prefix="tenant/").result(timeout=3)
        self.assertEqual((target.provider, target.mode, target.redis_prefix), ("redis", "standalone", "tenant/"))
        self.assertEqual(target.profile_revision, compute_nosql_revision(profile))
        self.assertEqual(client_factory_calls, [])
        root = composition.service.root(target)
        self.assertEqual(client_factory_calls, [])
        page = composition.service.submit_children(target, root).future.result(timeout=3)
        self.assertEqual([(node.kind, node.name) for node in page.nodes], [("redis_key", "tenant/item")])
        self.assertEqual(client_factory_calls[0][1], "standalone")
        scan_events = [event for event in client_events if event[0] == "scan"]
        self.assertEqual(len(scan_events), 1)
        self.assertEqual(scan_events[0][3], "tenant/*")
        self.assertNotEqual(scan_events[0][1], threading.get_ident())
        self.assertTrue(host.directory_factory_calls)
        self.assertNotEqual(host.directory_factory_calls[0][0], threading.get_ident())

        # A later profile mutation invalidates the captured revision before a
        # new directory factory or client can be constructed.
        profile["host"] = "changed-synthetic-host"
        stale = composition.service.submit_children(target, root, refresh=True)
        with self.assertRaises(ObjectTreeError) as captured:
            stale.future.result(timeout=3)
        self.assertEqual(captured.exception.code, "PROFILE_MISMATCH")
        self.assertEqual(len(client_factory_calls), 1)

        snapshot = host.profile_snapshot("redis-1")
        directory_factory = host.create_directory_factory(
            "redis-1",
            profile_revision=snapshot.profile_revision,
            provider=snapshot.provider,
            mode=snapshot.mode,
        )
        agent_lease = directory_factory("redis-1")
        denied = agent_lease.execute({"operation": "scan"})
        self.assertFalse(denied.ok)
        self.assertEqual(denied.code, "POLICY_DENIED")
        agent_lease.close()
        self.assertEqual(len([event for event in client_events if event[0] == "scan"]), 1)
        self.assertTrue(composition.close().result(timeout=3).completed)

    def test_mongo_database_context_uses_snapshot_or_explicit_directory_scope(self):
        profile = _mongo_profile()
        client_events = []
        client_factory_calls = []

        def client_factory(**kwargs):
            client_factory_calls.append(
                (threading.get_ident(), kwargs.get("mode"), kwargs.get("database_name"))
            )
            return _MongoClient(client_events)

        host = _ObservedNoSQLHost(
            connections={"mongo-1": profile},
            mongo_client_factory=client_factory,
        )
        relational = RelationalReadOnlyHost(connections={"mongo-1": profile})
        composition = ObjectTreeComposition(relational_host=relational, nosql_host=host)
        self.assertEqual(client_factory_calls, [])

        default_target = composition.submit_target_capture("mongo-1").result(timeout=3)
        self.assertEqual(
            (default_target.provider, default_target.mode, default_target.database),
            ("mongodb", "standalone", "profile-database"),
        )
        explicit_target = composition.submit_target_capture(
            "mongo-1",
            database_scope="explicit-database",
        ).result(timeout=3)
        self.assertEqual(explicit_target.database, "explicit-database")
        self.assertEqual(client_factory_calls, [])

        root = composition.service.root(explicit_target)
        databases = composition.service.submit_children(explicit_target, root).future.result(timeout=3)
        self.assertEqual([(node.kind, node.name) for node in databases.nodes], [("mongo_database", "listed-database")])
        collections = composition.service.submit_children(explicit_target, databases.nodes[0]).future.result(timeout=3)
        self.assertEqual([(node.kind, node.name) for node in collections.nodes], [("mongo_collection", "items")])
        self.assertEqual(client_factory_calls[0][1:], ("standalone", "explicit-database"))
        self.assertEqual(host.directory_factory_calls[0][2], "explicit-database")
        self.assertIn(("get_database", host.directory_factory_calls[0][0], "listed-database"), client_events)
        self.assertTrue(composition.close().result(timeout=3).completed)

    def test_loader_failure_does_not_fall_through_to_other_host(self):
        no_sql_calls = []

        def broken_loader(_connection_id):
            raise RuntimeError("synthetic private diagnostic")

        relational = RelationalReadOnlyHost(profile_loader=broken_loader)
        nosql = NoSQLReadOnlyHost(
            profile_loader=lambda _connection_id: (no_sql_calls.append(threading.get_ident()) or _redis_profile())
        )
        composition = ObjectTreeComposition(relational_host=relational, nosql_host=nosql)
        request = composition.submit_target_capture("sql-1", redis_prefix="")
        with self.assertRaises(ObjectTreeError) as captured:
            request.result(timeout=3)
        self.assertEqual(captured.exception.code, "PROFILE_UNAVAILABLE")
        self.assertNotIn("synthetic private diagnostic", str(captured.exception))
        self.assertEqual(no_sql_calls, [])
        self.assertTrue(composition.close().result(timeout=3).completed)

    def test_capture_queue_cancel_and_close_wait_for_blocked_profile_owner(self):
        profile = _mysql_profile()
        started = threading.Event()
        release = threading.Event()
        loader_threads = []

        def blocking_loader(_connection_id):
            loader_threads.append(threading.get_ident())
            started.set()
            release.wait(3)
            return profile

        relational = RelationalReadOnlyHost(profile_loader=blocking_loader)
        nosql = NoSQLReadOnlyHost(profile_loader=lambda _connection_id: self.fail("NoSQL fallback must not run"))
        with patch.object(composition_module, "MAX_CAPTURE_PENDING", 1):
            composition = ObjectTreeComposition(relational_host=relational, nosql_host=nosql)
            first = composition.submit_target_capture("sql-1")
            self.assertTrue(started.wait(2))
            overflow = composition.submit_target_capture("sql-1")
            with self.assertRaises(ObjectTreeError) as captured:
                overflow.result(timeout=1)
            self.assertEqual(captured.exception.code, "CAPABILITY_UNAVAILABLE")
            self.assertFalse(first.cancel())  # Running loader remains represented by its Future.
            cleanup = composition.close()
            self.assertIs(composition.close(), cleanup)
            self.assertFalse(cleanup.done())
            release.set()
            with self.assertRaises(ObjectTreeError) as captured:
                first.result(timeout=3)
            self.assertEqual(captured.exception.code, "CANCELLED")
            self.assertTrue(cleanup.result(timeout=3).completed)
            self.assertNotEqual(loader_threads[0], threading.get_ident())

    def test_cancel_wins_before_capture_target_publication(self):
        started = threading.Event()
        release = threading.Event()

        def finish_after_cancel(_self, _token, *_args):
            started.set()
            release.wait(3)
            return TreeTarget("sql-1", "synthetic-revision", "mysql")

        relational = RelationalReadOnlyHost(profile_loader=lambda _connection_id: _mysql_profile())
        nosql = NoSQLReadOnlyHost(profile_loader=lambda _connection_id: _redis_profile())
        with patch.object(ObjectTreeComposition, "_capture_target", finish_after_cancel):
            composition = ObjectTreeComposition(relational_host=relational, nosql_host=nosql)
            captured = composition.submit_target_capture("sql-1")
            self.assertTrue(started.wait(2))
            cleanup = composition.close()
            self.assertFalse(cleanup.done())
            release.set()
            with self.assertRaises(ObjectTreeError) as error:
                captured.result(timeout=3)
            self.assertEqual(error.exception.code, "CANCELLED")
            self.assertTrue(cleanup.result(timeout=3).completed)

    def test_capture_done_callback_can_request_close_without_deadlock(self):
        script = r'''
import threading
from tools.data_center.host_nosql import NoSQLReadOnlyHost
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.object_tree_composition import ObjectTreeComposition

profile = {"id": "sql-1", "dialect": "mysql", "mode": "mysql", "host": "synthetic"}
started = threading.Event()
release = threading.Event()
callback_done = threading.Event()
cleanup_futures = []

def loader(_connection_id):
    started.set()
    if not release.wait(2):
        raise RuntimeError("synthetic test barrier timed out")
    return profile

composition = ObjectTreeComposition(
    relational_host=RelationalReadOnlyHost(profile_loader=loader),
    nosql_host=NoSQLReadOnlyHost(profile_loader=lambda _connection_id: None),
)
captured = composition.submit_target_capture("sql-1")
if not started.wait(1):
    raise RuntimeError("capture worker did not start")

def close_from_callback(_future):
    cleanup_futures.append(composition.close())
    callback_done.set()

captured.add_done_callback(close_from_callback)
release.set()
captured.result(timeout=2)
if not callback_done.wait(2):
    raise RuntimeError("capture completion callback did not return")
if not cleanup_futures[0].result(timeout=2).completed:
    raise RuntimeError("cleanup did not wait for the capture owner")
print("CALLBACK_CLOSE_OK")
'''
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=6,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("CALLBACK_CLOSE_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
