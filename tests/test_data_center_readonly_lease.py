"""Focused tests for the credential-free relational read-only lease."""

from __future__ import annotations

import threading
import unittest

from tools.data_center.query_executor import QueryExecutor
from tools.data_center.session_manager import SessionManager
from tools.data_center.sql_policy import SQLPolicy
from tools.data_center.readonly_driver_adapters import (
    CancellationStatus,
    ReadOnlyCancelled,
    ReadOnlyInitializationError,
    ReadOnlyQueryError,
    ReadOnlyThreadOwnershipError,
)
from tools.data_center.readonly_lease import (
    ReadOnlyLeaseError,
    ReadOnlyLeaseFactory,
    ReadOnlyTarget,
)


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection
        self.description = (("id", "INTEGER"), ("label", "VARCHAR"))
        self.closed = False

    def execute(self, sql, parameters=None):
        self.connection.events.append(("execute", sql, parameters, threading.get_ident()))
        if self.connection.fail_statement and sql == self.connection.fail_statement:
            raise RuntimeError(f"driver password={self.connection.secret}")
        if sql.lstrip().upper().startswith("SELECT"):
            self.connection.result_rows = [(1, "one"), (2, "two"), (3, "three")]
        return self

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", size, threading.get_ident()))
        return self.connection.result_rows[:size]

    def close(self):
        self.closed = True
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _FakeConnection:
    def __init__(self, *, secret="pw-token", fail_statement=None, with_cancel=False) -> None:
        self.secret = secret
        self.fail_statement = fail_statement
        self.events: list[tuple] = []
        self.result_rows: list[tuple] = []
        self._autocommit = True
        self.closed = False
        self.with_cancel = with_cancel

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", value, threading.get_ident()))
        self._autocommit = value

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _FakeCursor(self)

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def commit(self):  # pragma: no cover - reaching this proves a bug
        raise AssertionError("read-only lease must never commit")

    def close(self):
        self.events.append(("close", threading.get_ident()))
        self.closed = True

    def cancel(self):
        if not self.with_cancel:  # pragma: no cover - only installed for Oracle tests
            raise AssertionError("unverified cancel must not be called")
        self.events.append(("cancel", threading.get_ident()))


class _FactoryRecorder:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[str, int]] = []

    def __call__(self, provider, profile, secret, **options):
        self.calls.append((provider, threading.get_ident()))
        self.options = options
        self.received_secret = secret
        return self.connection


class ReadOnlyLeaseTests(unittest.TestCase):
    def _factory(self, connection: _FakeConnection, *, provider="oracle", revision="r7"):
        profile_calls: list[tuple] = []
        secret_calls: list[tuple] = []
        connector = _FactoryRecorder(connection)

        def load_profile(connection_id, profile_revision):
            profile_calls.append((connection_id, profile_revision, threading.get_ident()))
            return {
                "id": connection_id,
                "connection_id": connection_id,
                "provider": provider,
                "profile_revision": profile_revision,
                "mode": "mysql" if provider.endswith("mysql") else ("dameng" if provider == "dameng" else "oracle"),
                "dialect": "mysql" if provider.endswith("mysql") else "oracle",
                "password_ciphertext": "encrypted-only",
            }

        def load_secret(profile):
            secret_calls.append((profile["id"], threading.get_ident()))
            return connection.secret

        factory = ReadOnlyLeaseFactory(
            ReadOnlyTarget("connection-1", revision, provider),
            profile_loader=load_profile,
            secret_loader=load_secret,
            connector=connector,
        )
        return factory, connector, profile_calls, secret_calls

    def test_factory_snapshot_and_query_are_worker_owned_and_never_commit(self) -> None:
        connection = _FakeConnection(with_cancel=True)
        factory, connector, profile_calls, secret_calls = self._factory(connection)
        manager = SessionManager(factory)
        session = manager.create_session("connection-1", session_id="tab-1", dialect="oracle")
        executor = QueryExecutor(manager, SQLPolicy(), default_timeout=None, max_row_limit=3)
        self.addCleanup(executor.shutdown)
        main_thread = threading.get_ident()

        result = executor.execute(
            {
                "session_id": session.session_id,
                "query_id": "q-lease-1",
                "generation": session.generation,
                "sql": "SELECT id, label FROM orders",
                "parameters": {"tenant": 7},
            },
            row_limit=2,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rows, ((1, "one"), (2, "two")))
        self.assertTrue(result.truncated)
        self.assertEqual(factory.target.as_dict(), {
            "connection_id": "connection-1",
            "profile_revision": "r7",
            "provider": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
        })
        self.assertNotIn("password", repr(factory).lower())
        self.assertNotIn(connection.secret, repr(factory))
        self.assertNotIn(connection.secret, repr(factory.target))
        self.assertEqual(profile_calls[0][:2], ("connection-1", "r7"))
        self.assertEqual(secret_calls[0][0], "connection-1")
        self.assertNotEqual(profile_calls[0][2], main_thread)
        self.assertEqual(profile_calls[0][2], connector.calls[0][1])
        self.assertEqual({event[3] for event in connection.events if event[0] == "execute"}, {connector.calls[0][1]})
        self.assertEqual([event[0] for event in connection.events], [
            "autocommit", "cursor", "execute", "cursor_close",
            "cursor", "execute", "fetchmany", "cursor_close",
            "rollback", "close",
        ])
        self.assertFalse(any(event[0] == "commit" for event in connection.events))

    def test_initialization_failure_closes_connection_and_factory_has_no_credential_error(self) -> None:
        connection = _FakeConnection(fail_statement="SET TRANSACTION READ ONLY")
        factory, _, _, _ = self._factory(connection)
        with self.assertRaises(ReadOnlyInitializationError) as raised:
            factory()
        self.assertEqual(raised.exception.code, "READONLY_INIT_FAILED")
        self.assertNotIn(connection.secret, str(raised.exception))
        self.assertTrue(connection.closed)
        self.assertEqual([event[0] for event in connection.events], [
            "autocommit", "cursor", "execute", "cursor_close", "rollback", "close",
        ])

    def test_connector_error_is_redacted_and_does_not_return_profile_or_secret(self) -> None:
        secret = "super-secret-credential"
        profile = {
            "connection_id": "connection-1",
            "profile_revision": "r1",
            "provider": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "password": secret,
        }

        def load_profile(connection_id, profile_revision):  # noqa: ARG001
            return profile

        def load_secret(profile_value):  # noqa: ARG001
            return secret

        def connector(provider, profile_value, secret_value):  # noqa: ARG001
            raise RuntimeError(f"cannot connect password={secret_value}")

        factory = ReadOnlyLeaseFactory(
            ReadOnlyTarget("connection-1", "r1", "oracle"),
            profile_loader=load_profile,
            secret_loader=load_secret,
            connector=connector,
        )
        with self.assertRaises(ReadOnlyLeaseError) as raised:
            factory()
        self.assertEqual(raised.exception.code, "CONNECT_FAILED")
        self.assertNotIn(secret, repr(factory))
        self.assertNotIn(secret, str(raised.exception))

    def test_cancel_remains_unsupported_without_owner_safe_executor_channel(self) -> None:
        oracle_connection = _FakeConnection(with_cancel=True)
        oracle_factory, _, _, _ = self._factory(oracle_connection, provider="oracle")
        oracle_lease = oracle_factory()
        self.assertFalse(oracle_lease.supports_cancel)
        status = oracle_lease.cancel()
        self.assertEqual(status, CancellationStatus(False, False, True, "CANCEL_UNSUPPORTED"))
        oracle_lease.close()

        mysql_connection = _FakeConnection()
        mysql_factory, _, _, _ = self._factory(mysql_connection, provider="mysql")
        mysql_lease = mysql_factory()
        self.assertFalse(mysql_lease.supports_cancel)
        status = mysql_lease.cancel()
        self.assertFalse(status.supported)
        self.assertTrue(status.result_unknown)
        mysql_lease.close()

    def test_pre_cancelled_query_is_rejected_without_driver_query(self) -> None:
        connection = _FakeConnection()
        factory, _, _, _ = self._factory(connection)
        lease = factory()
        class Token:
            def is_cancelled(self):
                return True

        with self.assertRaises(ReadOnlyCancelled) as raised:
            lease.execute_readonly("SELECT 1", cancellation=Token())
        self.assertFalse(raised.exception.result_unknown)
        self.assertEqual([event[0] for event in connection.events], [
            "autocommit", "cursor", "execute", "cursor_close",
        ])
        lease.close()

    def test_direct_lease_attacks_are_rejected_before_driver_query_io(self) -> None:
        connection = _FakeConnection()
        factory, _, _, _ = self._factory(connection, provider="mysql")
        lease = factory()
        before = list(connection.events)
        attacks = (
            "SELECT 1; SELECT 2",
            "SELECT LOAD_FILE('/tmp/secret')",
            "SELECT UNKNOWN_UDF(id) FROM orders",
            "SELECT 1 /*!40101 UNION SELECT 2 */",
        )
        for sql in attacks:
            with self.subTest(sql=sql):
                with self.assertRaises(ReadOnlyQueryError) as raised:
                    lease.execute_readonly(sql)
                self.assertEqual(raised.exception.code, "READ_ONLY_SQL_REQUIRED")
                self.assertEqual(connection.events, before)
        lease.close()

    def test_cross_thread_cancel_is_unsupported_and_never_calls_driver(self) -> None:
        connection = _FakeConnection(with_cancel=True)
        factory, _, _, _ = self._factory(connection)
        lease = factory()
        statuses: list[CancellationStatus] = []

        def request_cancel() -> None:
            statuses.append(lease.cancel())

        worker = threading.Thread(target=request_cancel)
        worker.start()
        worker.join()
        self.assertEqual(statuses, [CancellationStatus(False, False, True, "CANCEL_UNSUPPORTED")])
        self.assertNotIn("cancel", [event[0] for event in connection.events])
        lease.close()

    def test_wrong_thread_cannot_use_or_close_lease(self) -> None:
        connection = _FakeConnection()
        factory, _, _, _ = self._factory(connection)
        lease = factory()
        errors: list[BaseException] = []

        def wrong_thread() -> None:
            try:
                lease.execute_readonly("SELECT 1")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        worker = threading.Thread(target=wrong_thread)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ReadOnlyThreadOwnershipError)
        lease.close()


if __name__ == "__main__":
    unittest.main()
