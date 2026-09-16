"""Provider-mode matrix tests using only in-memory DB-API fakes."""

from __future__ import annotations

import threading
import unittest

from tools.data_center.readonly_driver_adapters import (
    READONLY_DRIVER_MATRIX,
    DBAPIReadOnlyAdapter,
    ReadOnlyDriverError,
    ReadOnlyInitializationError,
)
from tools.data_center.readonly_lease import ReadOnlyLeaseFactory, ReadOnlyTarget
from tools.data_center.readonly_lease import ReadOnlyScopeError


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = (("value",),)

    def execute(self, sql, params=None):
        self.connection.events.append(("execute", sql, params, threading.get_ident()))
        if sql == self.connection.fail_sql:
            raise RuntimeError("simulated driver failure with password=hidden")
        return self

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", size, threading.get_ident()))
        return [(1,), (2,)][:size]

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _Connection:
    def __init__(self, *, fail_sql=None, with_cancel=False):
        self.events: list[tuple] = []
        self.fail_sql = fail_sql
        self._autocommit = True
        self._autoCommit = True
        self.with_cancel = with_cancel
        self.closed = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", value, threading.get_ident()))
        self._autocommit = value

    @property
    def autoCommit(self):
        return self._autoCommit

    @autoCommit.setter
    def autoCommit(self, value):
        self.events.append(("autoCommit", value, threading.get_ident()))
        self._autoCommit = value

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _Cursor(self)

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.events.append(("close", threading.get_ident()))
        self.closed = True

    def commit(self):  # pragma: no cover - reaching this is a safety failure
        raise AssertionError("commit is outside the read-only adapter")

    def cancel(self):
        if not self.with_cancel:  # pragma: no cover - not a declared mode capability
            raise AssertionError("cancel must not be guessed")
        self.events.append(("cancel", threading.get_ident()))


class ReadOnlyLeaseMatrixTests(unittest.TestCase):
    def test_all_relational_modes_have_explicit_readonly_strategy(self) -> None:
        expected = {
            "oracle": "SET TRANSACTION READ ONLY",
            "mysql": "START TRANSACTION READ ONLY",
            "oceanbase_oracle": "SET TRANSACTION READ ONLY",
            "oceanbase_mysql": "START TRANSACTION READ ONLY",
            "dameng": "SET TRANSACTION READ ONLY",
        }
        self.assertEqual(set(READONLY_DRIVER_MATRIX), set(expected))
        for provider, statement in expected.items():
            with self.subTest(provider=provider):
                connection = _Connection(with_cancel=provider == "oracle")
                adapter = DBAPIReadOnlyAdapter(provider, connection).initialize()
                result = adapter.execute_readonly("SELECT 1")
                self.assertEqual(result["rows"], ((1,), (2,)))
                execute_sql = [item[1] for item in connection.events if item[0] == "execute"]
                self.assertEqual(execute_sql[0], statement)
                self.assertEqual(execute_sql[1], "SELECT 1")
                self.assertFalse(any(item[0] == "commit" for item in connection.events))
                self.assertEqual(adapter.capabilities.provider, provider)
                self.assertEqual(
                    adapter.capabilities.dialect,
                    "mysql" if provider.endswith("mysql") else "oracle",
                )
                self.assertFalse(adapter.supports_cancel)
                adapter.close()
                self.assertEqual([item[0] for item in connection.events][-2:], ["rollback", "close"])

    def test_dameng_connector_options_include_readonly_access_and_no_autocommit(self) -> None:
        strategy = READONLY_DRIVER_MATRIX["dameng"]
        self.assertTrue(strategy.connector_options["read_only"])
        self.assertEqual(strategy.connector_options["access_mode"], "read_only")
        self.assertFalse(strategy.connector_options["autoCommit"])
        self.assertFalse(strategy.connector_options.get("autocommit", False))

    def test_init_failure_is_fail_closed_and_closes_before_factory_returns(self) -> None:
        connection = _Connection(fail_sql="START TRANSACTION READ ONLY")
        adapter = DBAPIReadOnlyAdapter("mysql", connection)
        with self.assertRaises(ReadOnlyInitializationError):
            adapter.initialize()
        self.assertTrue(connection.closed)
        self.assertEqual([item[0] for item in connection.events], [
            "autocommit", "cursor", "execute", "cursor_close", "rollback", "close",
        ])
        with self.assertRaises(ReadOnlyDriverError):
            adapter.execute_readonly("SELECT 1")

    def test_lease_profile_provider_and_revision_are_fixed(self) -> None:
        connection = _Connection()
        calls: list[tuple] = []

        def profile_loader(connection_id, profile_revision):
            calls.append(("profile", connection_id, profile_revision))
            return {
                "connection_id": connection_id,
                "profile_revision": profile_revision,
                "provider": "mysql",
                "mode": "mysql",
                "dialect": "mysql",
                "host": "private-host",
            }

        def secret_loader(profile):
            calls.append(("secret", profile["profile_revision"]))
            return "private-secret"

        def connector(provider, profile, secret, **kwargs):  # noqa: ARG001
            calls.append(("connector", provider, profile["profile_revision"], secret, kwargs["access_mode"] if "access_mode" in kwargs else None))
            return connection

        factory = ReadOnlyLeaseFactory(
            ReadOnlyTarget("conn", "rev-3", "mysql"),
            profile_loader=profile_loader,
            secret_loader=secret_loader,
            connector=connector,
        )
        lease = factory("conn", session_id="s", generation=2)
        self.assertEqual(lease.target.connection_id, "conn")
        self.assertEqual(lease.target.profile_revision, "rev-3")
        self.assertEqual(lease.target.provider, "mysql")
        self.assertEqual(lease.target.mode, "mysql")
        self.assertEqual(lease.target.dialect, "mysql")
        self.assertEqual(calls[0], ("profile", "conn", "rev-3"))
        self.assertEqual(calls[1], ("secret", "rev-3"))
        self.assertEqual(calls[2][0:4], ("connector", "mysql", "rev-3", "private-secret"))
        lease.close()

        with self.assertRaises(ReadOnlyScopeError) as raised:
            factory("another-connection")
        self.assertEqual(getattr(raised.exception, "code", ""), "SCOPE_DENIED")

    def test_loaded_profile_requires_complete_exact_target_snapshot(self) -> None:
        target = ReadOnlyTarget("conn", "rev-3", "mysql")
        complete = {
            "connection_id": "conn",
            "profile_revision": "rev-3",
            "provider": "mysql",
            "mode": "mysql",
            "dialect": "mysql",
        }
        cases = {
            "missing_connection_id": {**complete, "connection_id": ""},
            "wrong_connection_id": {**complete, "connection_id": "other"},
            "missing_profile_revision": {**complete, "profile_revision": ""},
            "wrong_profile_revision": {**complete, "profile_revision": "rev-4"},
            "missing_provider": {**complete, "provider": ""},
            "wrong_provider": {**complete, "provider": "oracle", "mode": "oracle", "dialect": "oracle"},
            "missing_mode": {**complete, "mode": ""},
            "wrong_mode": {**complete, "mode": "oracle"},
            "missing_dialect": {**complete, "dialect": ""},
            "wrong_dialect": {**complete, "dialect": "oracle"},
        }
        for label, profile in cases.items():
            with self.subTest(label=label):
                secret_calls: list[object] = []
                connector_calls: list[object] = []

                def load_profile(connection_id, profile_revision):  # noqa: ARG001
                    return dict(profile)

                def load_secret(value):  # noqa: ARG001
                    secret_calls.append(value)
                    return "secret"

                def connector(*args, **kwargs):  # noqa: ARG001
                    connector_calls.append((args, kwargs))
                    return _Connection()

                factory = ReadOnlyLeaseFactory(
                    target,
                    profile_loader=load_profile,
                    secret_loader=load_secret,
                    connector=connector,
                )
                with self.assertRaises(ReadOnlyScopeError) as raised:
                    factory()
                self.assertEqual(raised.exception.code, "PROFILE_MISMATCH")
                self.assertEqual(secret_calls, [])
                self.assertEqual(connector_calls, [])


if __name__ == "__main__":
    unittest.main()
