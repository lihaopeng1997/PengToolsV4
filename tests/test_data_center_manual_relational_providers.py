from __future__ import annotations

from decimal import Decimal
import threading
import unittest

from tools.data_center.manual_relational_providers import (
    ManualRelationalProviderFactory,
    classify_manual_sql,
    create_manual_session,
    inspect_manual_provider,
)
from tools.data_center.manual_session import (
    ManualSessionError,
    ManualSessionState,
    ManualSessionTarget,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.profile_host import ConnectionChoice, DataCenterProfileHost


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection
        self._description = None
        self.rowcount = -1
        self._rows = []
        self._cursor_closed = False

    @property
    def description(self):
        return self._description

    def execute(self, sql, parameters=None):
        self.connection.events.append(("execute", threading.get_ident(), sql, parameters))
        upper = str(sql).lstrip().upper()
        if upper.startswith("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')"):
            self._description = (("current_schema", str, None, None, None, None, False),)
            self._rows = [(self.connection.current_schema,)]
            self.rowcount = 1
        elif upper.startswith("ALTER SESSION SET CURRENT_SCHEMA ="):
            requested = str(sql).split("=", 1)[1].strip()
            if requested.startswith('"') and requested.endswith('"'):
                requested = requested[1:-1].replace('""', '"')
            else:
                requested = requested.upper()
            self.connection.current_schema = requested
            self._description = None
            self._rows = []
            self.rowcount = -1
        elif upper.startswith("SELECT"):
            self._description = (("amount", Decimal, None, None, 12, 2, True),)
            self._rows = [(Decimal("12.50"),), (None,), (b"",)]
            self.rowcount = -1
        elif upper.startswith(("UPDATE", "INSERT", "DELETE")):
            self._description = None
            self._rows = []
            self.rowcount = self.connection.next_rowcount
        else:
            self._description = None
            self._rows = []
            self.rowcount = -1
        return self

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", threading.get_ident(), size))
        result = self._rows[:size]
        self._rows = self._rows[size:]
        return result

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))
        self._cursor_closed = True


class _FakeConnection:
    def __init__(self, *, autocommit=True) -> None:
        self.events = []
        self._autocommit = bool(autocommit)
        self.current_schema = "TEMP-USER"
        self.next_rowcount = 1
        self.closed = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", threading.get_ident(), bool(value)))
        self._autocommit = bool(value)

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _FakeCursor(self)

    def commit(self):
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.events.append(("connection_close", threading.get_ident()))
        self.closed = True


class _StuckAutocommitConnection(_FakeConnection):
    @property
    def autocommit(self):
        return True

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", threading.get_ident(), bool(value)))


def _profile(provider: str, mode: str, *, connection_id="tab-db", schema="OWNER"):
    dialect_by_mode = {"mysql": "mysql", "oracle": "oracle", "dameng": "dameng"}
    raw_provider = "oceanbase" if provider.startswith("oceanbase_") else provider
    profile = {
        "id": connection_id,
        "provider": raw_provider,
        "engine": raw_provider,
        "mode": mode,
        "dialect": dialect_by_mode[mode],
        "host": "127.0.0.1",
        "port": 1521 if mode == "oracle" else 3306,
        "username": "temp-user",
        "database": "sample-db",
        "schema": schema,
    }
    if provider == "oceanbase_oracle":
        profile["oceanbase_oracle_provider"] = "odbc"
    return profile


def _host_and_choice(profile):
    host = DataCenterProfileHost(connections_loader=lambda: [profile])
    choice = host.capture_connection(profile, database=profile["database"])
    return host, choice


def _target(choice, *, tab_id="tab-1", schema=None):
    if schema is None:
        schema = "OWNER" if choice.mode == "oracle" else choice.database
    return ManualSessionTarget(
        tab_id=tab_id,
        connection_id=choice.connection_id,
        profile_revision=choice.revision,
        database=choice.database,
        schema=schema,
        generation=1,
    )


def _result(future):
    return future.result(timeout=5)


class ManualRelationalProviderTests(unittest.TestCase):
    def test_oracle_owner_thread_select_dml_explicit_commit_and_close(self):
        profile = _profile("oracle", "oracle")
        owner_events = []
        host = DataCenterProfileHost(
            connections_loader=lambda: (owner_events.append(("profile", threading.get_ident())) or [profile])
        )
        choice = host.capture_connection(profile, database=profile["database"])
        connection = _FakeConnection(autocommit=True)
        calls = []

        def resolve_secret(token):
            calls.append(("secret", threading.get_ident()))
            self.assertEqual(token, "")
            return "synthetic-secret"

        def connect(loaded_profile, secret):
            calls.append(("connector", threading.get_ident()))
            self.assertEqual(loaded_profile["id"], choice.connection_id)
            self.assertEqual(secret, "synthetic-secret")
            return connection

        target = _target(choice)
        session = create_manual_session(
            target,
            choice,
            profile_host=host,
            connector=connect,
            secret_resolver=resolve_secret,
        )
        agent_lease_connection = object()
        self.assertIsNot(agent_lease_connection, connection)
        self.assertEqual(calls, [])
        self.assertEqual(owner_events, [])
        self.assertEqual(session.state, ManualSessionState.DISCONNECTED)

        page = _result(session.query("SELECT amount FROM sample WHERE id = :1", (1,)))
        self.assertEqual(page.rows, ((Decimal("12.50"),), (None,), (b"",)))
        self.assertEqual(session.state, ManualSessionState.CLEAN)
        update = _result(session.submit(
            "UPDATE sample SET amount = :1 WHERE id = :2",
            (Decimal("99.25"), 7),
        ))
        self.assertEqual(update.state, ManualSessionState.DIRTY)
        self.assertEqual(session.state, ManualSessionState.DIRTY)
        self.assertEqual([event[0] for event in connection.events].count("commit"), 0)

        committed = _result(session.commit())
        self.assertTrue(committed.applied)
        self.assertEqual(committed.state, ManualSessionState.CLEAN)
        closed = _result(session.close())
        self.assertTrue(closed.closed)
        self.assertTrue(connection.closed)
        self.assertEqual([event[0] for event in connection.events].count("commit"), 1)
        self.assertFalse(connection.autocommit)

        caller_thread = threading.get_ident()
        operation_threads = {
            event[1]
            for event in connection.events
            if len(event) > 1 and isinstance(event[1], int)
        }
        operation_threads.update(thread_id for _, thread_id in calls)
        operation_threads.update(thread_id for _, thread_id in owner_events)
        self.assertEqual(len(operation_threads), 1)
        self.assertNotIn(caller_thread, operation_threads)
        self.assertEqual(
            [
                event[3]
                for event in connection.events
                if event[0] == "execute" and event[3] is not None
            ],
            [(1,), (Decimal("99.25"), 7)],
        )

    def test_provider_mode_matrix_uses_only_injected_connectors(self):
        cases = (
            ("mysql", "mysql", "sql"),
            ("oracle", "oracle", "implicit-on-commit"),
            ("oceanbase_mysql", "mysql", "sql"),
            ("oceanbase_oracle", "oracle", "implicit-on-commit"),
        )
        for provider, mode, release in cases:
            with self.subTest(provider=provider, mode=mode):
                profile = _profile(provider, mode, connection_id=f"{provider}-id")
                host, choice = _host_and_choice(profile)
                capability = inspect_manual_provider(choice)
                self.assertTrue(capability.supported)
                self.assertEqual(capability.savepoint_release, release)
                self.assertTrue(capability.supports_autocommit_control)
                self.assertTrue(capability.supports_savepoints)
                self.assertFalse(capability.supports_native_cancel)
                self.assertEqual(capability.timeout_scope, "connect-and-per-call")

                connection = _FakeConnection()
                session = create_manual_session(
                    _target(choice, tab_id=f"tab-{provider}"),
                    choice,
                    profile_host=host,
                    connector=lambda loaded, secret, result=connection: result,
                    secret_resolver=lambda token: "synthetic-secret",
                )
                result = _result(session.query("SELECT amount FROM sample"))
                self.assertEqual(result.rows[0], (Decimal("12.50"),))
                if provider in {"oracle", "oceanbase_oracle"}:
                    executed_sql = [event[2] for event in connection.events if event[0] == "execute"]
                    self.assertEqual(sum(sql.startswith("SELECT SYS_CONTEXT") for sql in executed_sql), 2)
                    self.assertEqual(sum(sql.startswith("ALTER SESSION SET CURRENT_SCHEMA") for sql in executed_sql), 1)
                _result(session.close())
                self.assertTrue(connection.closed)

    def test_vendor_classification_covers_transaction_effect_edges(self):
        cases = (
            ("oracle", "oracle", "CREATE TABLE x (id NUMBER)", StatementKind.DDL, TransactionEffect.IMPLICIT_COMMIT),
            ("oceanbase_oracle", "oracle", "GRANT SELECT ON x TO y", StatementKind.DCL, TransactionEffect.IMPLICIT_COMMIT),
            ("mysql", "mysql", "CREATE TEMPORARY TABLE x (id INT)", StatementKind.DDL, TransactionEffect.UNKNOWN),
            ("oceanbase_mysql", "mysql", "WITH q AS (SELECT 1) DELETE FROM x", StatementKind.DML, TransactionEffect.DIRTY),
            ("oracle", "oracle", "SELECT id FROM x FOR UPDATE", StatementKind.QUERY, TransactionEffect.DIRTY),
            ("mysql", "mysql", "SELECT id INTO @x FROM t", StatementKind.QUERY, TransactionEffect.DIRTY),
            ("oracle", "oracle", "CALL maybe_commits()", StatementKind.OTHER, TransactionEffect.UNKNOWN),
        )
        for provider, mode, sql, kind, effect in cases:
            with self.subTest(provider=provider, sql=sql):
                _, choice = _host_and_choice(_profile(provider, mode))
                decision = classify_manual_sql(choice, sql)
                self.assertEqual((decision.kind, decision.effect), (kind, effect))

    def test_oracle_savepoint_batch_uses_no_release_statement_and_keeps_dirty(self):
        profile = _profile("oracle", "oracle")
        host, choice = _host_and_choice(profile)
        connection = _FakeConnection()
        session = create_manual_session(
            _target(choice), choice, profile_host=host,
            connector=lambda loaded, secret: connection,
            secret_resolver=lambda token: "synthetic-secret",
        )
        batch = _result(session.run_savepoint_batch(
            [("UPDATE sample SET amount = :1 WHERE id = :2", (Decimal("2.00"), 1))],
            expected_rowcounts=(1,),
        ))
        self.assertTrue(batch.succeeded)
        self.assertEqual(batch.state, ManualSessionState.DIRTY)
        executed = [event[2] for event in connection.events if event[0] == "execute"]
        self.assertTrue(any(sql.startswith("SAVEPOINT ") for sql in executed))
        self.assertFalse(any(sql.startswith("RELEASE SAVEPOINT ") for sql in executed))
        self.assertEqual([event[0] for event in connection.events].count("commit"), 0)
        _result(session.commit())
        _result(session.close())

    def test_mysql_savepoint_batch_uses_vendor_release_sql(self):
        profile = _profile("mysql", "mysql")
        host, choice = _host_and_choice(profile)
        connection = _FakeConnection()
        session = create_manual_session(
            _target(choice), choice, profile_host=host,
            connector=lambda loaded, secret: connection,
            secret_resolver=lambda token: "synthetic-secret",
        )
        batch = _result(session.run_savepoint_batch(
            [("UPDATE sample SET amount = %s WHERE id = %s", (Decimal("3.25"), 2))],
            expected_rowcounts=(1,),
        ))
        self.assertTrue(batch.succeeded)
        self.assertEqual(batch.state, ManualSessionState.DIRTY)
        executed = [event[2] for event in connection.events if event[0] == "execute"]
        self.assertTrue(any(sql.startswith("SAVEPOINT ") for sql in executed))
        self.assertTrue(any(sql.startswith("RELEASE SAVEPOINT `") for sql in executed))
        self.assertEqual(
            [event[3] for event in connection.events if event[0] == "execute" and event[2].startswith("UPDATE")],
            [(Decimal("3.25"), 2)],
        )
        self.assertEqual([event[0] for event in connection.events].count("commit"), 0)
        _result(session.commit())
        _result(session.close())

    def test_dameng_and_invalid_mode_fail_closed_without_connector(self):
        dameng_profile = _profile("dameng", "dameng")
        dameng_host, dameng_choice = _host_and_choice(dameng_profile)
        capability = inspect_manual_provider(dameng_choice)
        self.assertFalse(capability.supported)
        self.assertEqual(capability.reason_code, "MANUAL_PROVIDER_UNSUPPORTED")
        connector_calls = []
        session = create_manual_session(
            _target(dameng_choice), dameng_choice,
            profile_host=dameng_host,
            connector=lambda profile, secret: connector_calls.append("called"),
            secret_resolver=lambda token: "synthetic-secret",
        )
        with self.assertRaises(ManualSessionError) as error:
            _result(session.submit("UPDATE sample SET amount = 1"))
        self.assertEqual(error.exception.code, "MANUAL_PROVIDER_UNSUPPORTED")
        self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
        self.assertEqual(connector_calls, [])
        self.assertTrue(_result(session.close()).closed)

        invalid = ConnectionChoice("invalid-id", "r1", "oceanbase_oracle", "mysql", "mysql", "db")
        invalid_capability = inspect_manual_provider(invalid)
        self.assertFalse(invalid_capability.supported)
        self.assertEqual(invalid_capability.reason_code, "MANUAL_PROVIDER_UNSUPPORTED")
        invalid_dialect = ConnectionChoice("invalid-dialect", "r1", "oracle", "oracle", "mysql", "db")
        self.assertFalse(inspect_manual_provider(invalid_dialect).supported)

    def test_profile_drift_and_schema_mismatch_stop_before_connector(self):
        profile = _profile("oracle", "oracle")
        profiles = [profile]
        host = DataCenterProfileHost(connections_loader=lambda: profiles)
        choice = host.capture_connection(profile, database=profile["database"])
        connector_calls = []
        profile["host"] = "changed-after-selection"
        session = create_manual_session(
            _target(choice), choice, profile_host=host,
            connector=lambda loaded, secret: connector_calls.append("called"),
            secret_resolver=lambda token: "synthetic-secret",
        )
        with self.assertRaises(ManualSessionError) as error:
            _result(session.query("SELECT 1 FROM dual"))
        self.assertEqual(error.exception.code, "PROFILE_MISMATCH")
        self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
        self.assertEqual(connector_calls, [])
        self.assertTrue(_result(session.close()).closed)

        unchanged = _profile("oracle", "oracle", connection_id="schema-id")
        schema_host, schema_choice = _host_and_choice(unchanged)
        schema_connector_calls = []
        bad_schema_session = create_manual_session(
            _target(schema_choice, tab_id="schema-tab", schema="OTHER"),
            schema_choice,
            profile_host=schema_host,
            connector=lambda loaded, secret: schema_connector_calls.append("called"),
            secret_resolver=lambda token: "synthetic-secret",
        )
        with self.assertRaises(ManualSessionError):
            _result(bad_schema_session.query("SELECT 1 FROM dual"))
        self.assertEqual(bad_schema_session.state, ManualSessionState.DISCONNECTED)
        self.assertEqual(schema_connector_calls, [])
        self.assertTrue(_result(bad_schema_session.close()).closed)

    def test_autocommit_disable_must_be_verified_before_any_sql(self):
        profile = _profile("mysql", "mysql")
        host, choice = _host_and_choice(profile)
        connection = _StuckAutocommitConnection(autocommit=True)
        session = create_manual_session(
            _target(choice), choice, profile_host=host,
            connector=lambda loaded, secret: connection,
            secret_resolver=lambda token: "synthetic-secret",
        )
        with self.assertRaises(ManualSessionError) as error:
            _result(session.query("SELECT 1"))
        self.assertEqual(error.exception.code, "MANUAL_COMMIT_CONFIGURATION_FAILED")
        self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
        self.assertFalse(connection.closed)
        self.assertTrue(session.poll_cleanup().connection_retained)
        self.assertFalse(any(event[0] == "execute" for event in connection.events))
        self.assertTrue(all(event[1] != threading.get_ident() for event in connection.events))
        self.assertTrue(_result(session.close()).closed)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
