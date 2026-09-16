"""Behavior tests for the legacy-config relational Agent host boundary."""

from __future__ import annotations

import threading
import unittest

from tools.data_center.host_relational import (
    LazyRelationalLeaseFactory,
    RelationalProfileError,
    RelationalReadOnlyHost,
    RelationalScopeError,
    compute_profile_revision,
)
from tools.data_center.query_executor import QueryExecutor
from tools.data_center.readonly_lease import ReadOnlyLeaseError, ReadOnlyLeaseFactory, ReadOnlyScopeError
from tools.data_center.session_manager import SessionManager
from tools.data_center.sql_policy import SQLPolicy


class _Cursor:
    description = (("value", "INTEGER"),)

    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection

    def execute(self, sql, parameters=None):
        self.connection.events.append(("execute", sql, parameters, threading.get_ident()))
        if str(sql).strip().upper().startswith("SELECT"):
            self.connection.rows = [(1,), (2,)]
        return self

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", size, threading.get_ident()))
        return self.connection.rows[:size]

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _Connection:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.rows: list[tuple] = []
        self._autocommit = True
        self.call_timeout = 0
        self.closed = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", value, threading.get_ident()))
        self._autocommit = value

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _Cursor(self)

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def commit(self):  # pragma: no cover - reaching this is a safety failure
        raise AssertionError("read-only host must never commit")

    def close(self):
        self.events.append(("close", threading.get_ident()))
        self.closed = True


def _profile(connection_id: str = "c1", **extra):
    result = {
        "id": connection_id,
        "dialect": "oracle",
        "host": "db.internal",
        "port": 1521,
        "database": "APP",
        "username": "agent",
        "password_ciphertext": "ciphertext-only",
    }
    result.update(extra)
    return result


class RelationalHostTests(unittest.TestCase):
    def test_factory_is_lazy_and_uses_one_selected_profile_in_worker(self) -> None:
        profile_calls: list[tuple[str, int]] = []
        secret_calls: list[int] = []
        connector_calls: list[tuple] = []
        connection = _Connection()

        def profile_loader(connection_id):
            profile_calls.append((connection_id, threading.get_ident()))
            return _profile(connection_id)

        def decryptor(token):
            secret_calls.append(threading.get_ident())
            self.assertEqual(token, "ciphertext-only")
            return "pw-hidden"

        def connector(provider, profile, secret, **options):
            connector_calls.append((provider, profile, secret, options, threading.get_ident()))
            return connection

        host = RelationalReadOnlyHost(
            profile_loader=profile_loader,
            decryptor=decryptor,
            connector=connector,
            read_timeout_seconds=4,
        )
        factory = host.create_factory("c1")
        self.assertIsInstance(factory, LazyRelationalLeaseFactory)
        self.assertEqual(profile_calls, [])
        self.assertNotIn("pw-hidden", repr(host))
        self.assertNotIn("pw-hidden", repr(factory))

        lease = factory("c1", session_id="tab-1", generation=1)
        self.assertEqual(profile_calls, [("c1", profile_calls[0][1])])
        self.assertEqual(len(secret_calls), 1)
        self.assertEqual(len(connector_calls), 1)
        self.assertEqual(secret_calls[0], connector_calls[0][4])
        self.assertEqual(connector_calls[0][0], "oracle")
        self.assertEqual(connector_calls[0][2], "pw-hidden")
        self.assertTrue(connector_calls[0][3]["read_only"])
        self.assertFalse(connector_calls[0][3]["autocommit"])
        self.assertEqual(connector_calls[0][3]["connect_timeout"], 8.0)
        self.assertEqual(connector_calls[0][3]["read_timeout"], 4.0)
        self.assertEqual(lease.target.provider, "oracle")
        self.assertEqual(lease.target.mode, "oracle")
        self.assertEqual(lease.target.dialect, "oracle")
        self.assertFalse(lease.supports_cancel)
        self.assertNotIn("pw-hidden", repr(lease))

        lease.execute_readonly("SELECT 1")
        lease.close()
        self.assertFalse(any(item[0] == "commit" for item in connection.events))

    def test_profile_revision_is_stable_and_snapshot_has_no_connection_details(self) -> None:
        first = _profile(name="one", group_path="a", environment="test")
        second = {
            "environment": "prod",
            "group_path": "b",
            "name": "two",
            "password_ciphertext": "ciphertext-only",
            "username": "agent",
            "database": "APP",
            "port": 1521,
            "host": "db.internal",
            "dialect": "oracle",
            "id": "c1",
        }
        self.assertEqual(compute_profile_revision(first), compute_profile_revision(second))
        changed = dict(first, host="another.internal")
        self.assertNotEqual(compute_profile_revision(first), compute_profile_revision(changed))

        host = RelationalReadOnlyHost(connections={"c1": first}, connector=lambda **_: _Connection())
        snapshot = host.profile_snapshot("c1")
        self.assertEqual(snapshot.connection_id, "c1")
        self.assertEqual(snapshot.provider, "oracle")
        self.assertNotIn("db.internal", repr(snapshot))
        self.assertNotIn("ciphertext-only", repr(snapshot))
        self.assertNotIn("pw-hidden", repr(snapshot))

        # A keyed registry may carry the id only in its map key.  The host
        # verifies that key on a copy and leaves the source mapping untouched.
        keyed = {"c2": {key: value for key, value in first.items() if key != "id"}}
        keyed_host = RelationalReadOnlyHost(connections=keyed, connector=lambda **_: _Connection())
        keyed_snapshot = keyed_host.profile_snapshot("c2")
        self.assertEqual(keyed_snapshot.connection_id, "c2")
        self.assertNotIn("id", keyed["c2"])

    def test_oceanbase_modes_are_fixed_and_unknown_combinations_fail_closed(self) -> None:
        mysql_connection = _Connection()
        calls: list[tuple] = []

        def connector(provider, profile, secret, **options):
            calls.append((provider, profile["mode"], profile["dialect"], secret, options))
            return mysql_connection

        host = RelationalReadOnlyHost(
            connections={"ob": _profile("ob", dialect="oceanbase", mode="mysql")},
            decryptor=lambda _token: "pw-hidden",
            connector=connector,
        )
        lease = host.create_factory("ob")("ob", session_id="tab", generation=1)
        self.assertEqual(lease.target.provider, "oceanbase_mysql")
        self.assertEqual(lease.target.mode, "mysql")
        self.assertEqual(lease.target.dialect, "mysql")
        self.assertEqual(calls[0][0:3], ("oceanbase_mysql", "mysql", "mysql"))
        lease.close()

        blocked = RelationalReadOnlyHost(
            connections={"bad": _profile("bad", dialect="postgres", mode="postgres")},
            decryptor=lambda _token: (_ for _ in ()).throw(AssertionError("secret must not be read")),
            connector=lambda **_: (_ for _ in ()).throw(AssertionError("connector must not run")),
        )
        with self.assertRaises(RelationalProfileError) as raised:
            blocked.profile_snapshot("bad")
        self.assertEqual(raised.exception.code, "UNSUPPORTED_COMBINATION")

    def test_expected_target_revision_and_scope_are_verified_before_secret(self) -> None:
        profile = _profile("c1")
        revision = compute_profile_revision(profile)
        secret_calls: list[int] = []
        host = RelationalReadOnlyHost(
            connections={"c1": profile},
            secret_loader=lambda _profile: (secret_calls.append(threading.get_ident()) or "pw-hidden"),
            connector=lambda **_: _Connection(),
        )
        factory = host.create_factory(
            "c1",
            profile_revision=revision,
            provider="oracle",
            mode="oracle",
            dialect="oracle",
        )
        self.assertIsInstance(factory, ReadOnlyLeaseFactory)
        lease = factory("c1", session_id="tab", generation=1)
        lease.close()
        self.assertEqual(len(secret_calls), 1)

        with self.assertRaises(ReadOnlyScopeError):
            factory("another", session_id="tab", generation=1)

        mismatch = host.create_factory(
            "c1",
            profile_revision="wrong-revision",
            provider="oracle",
            mode="oracle",
            dialect="oracle",
        )
        with self.assertRaises(Exception) as raised:
            mismatch("c1", session_id="tab-2", generation=1)
        self.assertIn(getattr(raised.exception, "code", None), {"PROFILE_MISMATCH", "PROFILE_UNAVAILABLE"})
        self.assertEqual(len(secret_calls), 1)

    def test_profile_revision_tracks_current_content_instead_of_legacy_field(self) -> None:
        current = _profile(
            "c1",
            profile_revision="legacy-fixed-revision",
            revision="another-old-revision",
        )
        secret_calls: list[int] = []
        host = RelationalReadOnlyHost(
            profile_loader=lambda _connection_id: current,
            secret_loader=lambda _profile: (secret_calls.append(threading.get_ident()) or "pw-hidden"),
            connector=lambda **_: _Connection(),
        )

        snapshot = host.profile_snapshot("c1")
        self.assertEqual(snapshot.profile_revision, compute_profile_revision(current))
        self.assertNotEqual(snapshot.profile_revision, "legacy-fixed-revision")

        factory = host.create_factory(
            "c1",
            profile_revision=snapshot.profile_revision,
            provider="oracle",
            mode="oracle",
            dialect="oracle",
        )
        current["host"] = "changed.internal"
        current["password_ciphertext"] = "rotated-ciphertext"
        with self.assertRaises(ReadOnlyLeaseError) as raised:
            factory("c1", session_id="tab", generation=1)
        self.assertEqual(raised.exception.code, "PROFILE_UNAVAILABLE")
        self.assertEqual(secret_calls, [])

    def test_default_legacy_connector_fails_closed(self) -> None:
        host = RelationalReadOnlyHost(
            connections={"c1": _profile()},
            secret_loader=lambda _profile: "pw-hidden",
        )
        with self.assertRaises(ReadOnlyLeaseError) as raised:
            host.create_factory("c1")("c1", session_id="tab", generation=1)
        self.assertEqual(raised.exception.code, "CONNECT_FAILED")

    def test_secret_stays_out_of_errors_representations_and_query_events(self) -> None:
        secret = "super-secret-value"
        connection = _Connection()
        host = RelationalReadOnlyHost(
            connections={"c1": _profile(password_ciphertext="encrypted-secret-token")},
            decryptor=lambda _token: secret,
            connector=lambda **_: connection,
        )
        lease = host.create_factory("c1")("c1", session_id="tab", generation=1)
        lease.execute_readonly("SELECT 1")
        self.assertNotIn(secret, repr(host))
        self.assertNotIn(secret, repr(lease))
        self.assertNotIn(secret, repr(connection.events))
        lease.close()

        def failing_decryptor(_token):
            raise RuntimeError(secret)

        failing_host = RelationalReadOnlyHost(
            connections={"c1": _profile(password_ciphertext="encrypted-secret-token")},
            decryptor=failing_decryptor,
            connector=lambda **_: _Connection(),
        )
        with self.assertRaises(ReadOnlyLeaseError) as raised:
            failing_host.create_factory("c1")("c1", session_id="tab", generation=1)
        self.assertEqual(raised.exception.code, "SECRET_UNAVAILABLE")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, repr(raised.exception))

    def test_public_surface_keeps_only_canonical_relational_host_entries(self) -> None:
        import tools.data_center.host_relational as module

        removed = (
            "HostRelationalAdapter",
            "ReadOnlyRelationalFactory",
            "ReadOnlyRelationalHost",
            "RelationalConnectionHost",
            "RelationalConnectionSnapshot",
            "RelationalHostAdapter",
            "RelationalHostFactory",
            "RelationalLeaseFactory",
            "RelationalLeaseHost",
            "ReadOnlyRelationalSnapshot",
            "build_readonly_lease_factory",
            "calculate_profile_revision",
            "create_readonly_lease_factory",
            "make_readonly_lease_factory",
            "profile_revision_for",
            "stable_profile_revision",
        )
        self.assertTrue(all(not hasattr(module, name) for name in removed))
        self.assertEqual(
            set(module.__all__),
            {
                "LazyRelationalLeaseFactory",
                "RelationalHostError",
                "RelationalProfileError",
                "RelationalProfileSnapshot",
                "RelationalReadOnlyHost",
                "RelationalScopeError",
                "RelationalSecretError",
                "compute_profile_revision",
            },
        )

    def test_raw_connection_cannot_be_used_as_a_manual_session(self) -> None:
        with self.assertRaises(TypeError):
            RelationalReadOnlyHost(connections={"c1": _profile()}, connector=_Connection())

    def test_profile_constructor_and_connect_are_compatible_with_session_factory_use(self) -> None:
        connection = _Connection()
        host = RelationalReadOnlyHost(
            _profile("c1"),
            connection_id="c1",
            decryptor=lambda _token: "pw-hidden",
            connector=lambda **_: connection,
        )
        lease = host.connect("c1", session_id="tab", generation=1)
        self.assertEqual(lease.target.connection_id, "c1")
        lease.close()

        # SessionManager passes these two fields while invoking its opaque
        # factory.  The host can serve that shape without sharing a manual
        # editor connection.
        session_lease = host("c1", session_id="tab-2", generation=2)
        self.assertEqual(session_lease.target.connection_id, "c1")
        session_lease.close()

    def test_session_manager_invokes_host_and_driver_only_in_query_worker(self) -> None:
        calls: list[tuple[str, int]] = []
        connection = _Connection()

        def profile_loader(connection_id):
            calls.append(("profile", threading.get_ident()))
            return _profile(connection_id)

        def decryptor(_token):
            calls.append(("secret", threading.get_ident()))
            return "pw-hidden"

        def connector(provider, profile, secret, **options):  # noqa: ARG001
            calls.append(("connector", threading.get_ident()))
            return connection

        host = RelationalReadOnlyHost(
            profile_loader=profile_loader,
            decryptor=decryptor,
            connector=connector,
        )
        manager = SessionManager(host)
        session = manager.create_session("c1", session_id="tab", dialect="oracle")
        executor = QueryExecutor(manager, SQLPolicy(), default_timeout=None)
        try:
            result = executor.execute({
                "session_id": session.session_id,
                "query_id": "q-host-worker",
                "generation": session.generation,
                "sql": "SELECT 1",
            })
        finally:
            executor.shutdown()
        self.assertTrue(result.ok)
        self.assertEqual([kind for kind, _thread in calls], ["profile", "secret", "connector"])
        self.assertEqual(len({thread for _kind, thread in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())


if __name__ == "__main__":
    unittest.main()
