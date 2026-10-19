from __future__ import annotations

import socket
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.data_center.connection_transport import (
    ConnectionTransportError,
    DataCenterConnectionTransport,
    LeasedConnection,
)
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.manual_driver import RelationalManualDriverFactory
from tools.data_center.manual_session import (
    ManualSession,
    ManualSessionTarget,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.readonly_relational_connectors import (
    RelationalConnectorError,
    connect_relational,
)


class _Lease:
    def __init__(self, events):
        self.dial_host = "127.0.0.1"
        self.dial_port = 49152
        self.events = events
        self.release_calls = 0
        self.release_results = []

    def release(self):
        self.release_calls += 1
        self.events.append("lease.release")
        return self.release_results.pop(0) if self.release_results else True


class _Manager:
    def __init__(self, lease):
        self.lease = lease
        self.calls = []

    def acquire(self, spec, *, connection_id, profile_revision):
        self.calls.append((spec, connection_id, profile_revision))
        return self.lease


class _LocalSocket:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def close(self):
        self.closed = True
        self.events.append("socket.close")


class _Cursor:
    description = (("value", "INTEGER", None, None, None, None, True),)
    rowcount = -1

    def __init__(self, events):
        self.events = events

    def execute(self, sql, parameters=None):
        self.events.append(("execute", sql))

    def fetchmany(self, size):
        return []

    def close(self):
        self.events.append("cursor.close")


class _MysqlConnection:
    def __init__(self, events):
        self.events = events
        self.autocommit = False
        self.connected_socket = None

    def connect(self, sock=None):
        self.connected_socket = sock
        self.events.append(("mysql.connect.sock", sock))

    def cursor(self):
        return _Cursor(self.events)

    def close(self):
        self.events.append("mysql.close")

    def rollback(self):
        self.events.append("mysql.rollback")


class _FakePyMySQL:
    def __init__(self, events):
        self.cursors = SimpleNamespace(Cursor=object)
        self.events = events
        self.calls = []
        self.connection = _MysqlConnection(events)

    def connect(self, **kwargs):
        self.calls.append(dict(kwargs))
        self.events.append(("mysql.construct", dict(kwargs)))
        return self.connection


def _mysql_profile(**extra):
    profile = {
        "id": "mysql-connection",
        "provider": "mysql",
        "dialect": "mysql",
        "mode": "standalone",
        "host": "db.fixture.invalid",
        "port": 3306,
        "username": "fixture-user",
        "database": "fixture_db",
    }
    profile.update(extra)
    return profile


class DataCenterConnectionTransportTests(unittest.TestCase):
    def test_system_trust_roots_enforce_original_host_with_native_driver(self):
        import pymysql

        native_connection = pymysql.connections.Connection

        def construct_without_network(**kwargs):
            return native_connection(**dict(kwargs, defer_connect=True))

        with (
            patch("pymysql.connect", side_effect=construct_without_network),
            patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        ):
            connection = connect_relational(
                "mysql", _mysql_profile(tls_enabled=True), "fixture-db-password",
                connect_timeout=2, read_timeout=4,
            )
        self.assertTrue(connection.ctx.check_hostname)
        self.assertEqual(connection.ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection._ssl_required)
        self.assertEqual(connection.host, "db.fixture.invalid")
        self.assertIsNone(connection._sock)

    def test_typed_transport_separates_credentials_and_rejects_unsupported_targets(self):
        profile = _mysql_profile(mode="mysql",
            ssh_enabled=True,
            ssh_host="ssh.fixture.invalid",
            ssh_port=2222,
            ssh_username="ssh-user",
            ssh_auth_method="password",
            ssh_password_token="enc:fixture-ssh-token",
            tls_enabled=True,
        )
        spec = DataCenterConnectionTransport.from_profile(profile, "mysql", "standalone")
        self.assertEqual(spec.ssh_password_token, "enc:fixture-ssh-token")
        self.assertNotIn("fixture-ssh-token", repr(spec.binding_key()))
        for target in (
            ("redis", "standalone"),
            ("mysql", "cluster"),
        ):
            with self.subTest(target=target):
                provider, mode = target
                changed_profile = dict(profile, mode=mode) if mode == "cluster" else profile
                with self.assertRaises(ConnectionTransportError):
                    DataCenterConnectionTransport.from_profile(changed_profile, provider, mode)
        with self.assertRaises(ConnectionTransportError):
            DataCenterConnectionTransport.from_profile(
                _mysql_profile(ssh=True, ssh_password="plain"), "mysql", "standalone"
            )

    def test_default_manual_owner_uses_tunnel_with_original_tls_host_and_close_order(self):
        events = []
        lease = _Lease(events)
        manager = _Manager(lease)
        profile = _mysql_profile(
            mode="mysql",
            ssh_enabled=True,
            ssh_host="ssh.fixture.invalid",
            ssh_port=22,
            ssh_username="ssh-user",
            ssh_auth_method="password",
            ssh_password_token="enc:fixture-ssh-token",
            tls_enabled=True,
            tls_ca_path="C:/fixture/ca.pem",
        )
        profile_host = DataCenterProfileHost(connections_loader=lambda: [profile])
        choice = profile_host.capture_connection(profile, database=profile["database"])
        target = ManualSessionTarget(
            "manual-tab",
            choice.connection_id,
            choice.revision,
            database=choice.database,
            schema=choice.database,
        )
        fake_mysql = _FakePyMySQL(events)
        local_socket = _LocalSocket(events)
        factory = RelationalManualDriverFactory(
            choice,
            profile_host=profile_host,
            secret_resolver=lambda token: "fixture-db-password" if token == "" else "fixture-db-password",
        )
        session = ManualSession(
            target,
            factory,
            lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE),
        )
        native_context_factory = ssl.create_default_context
        with (
            patch.dict("sys.modules", {"pymysql": fake_mysql}),
            patch("socket.create_connection", return_value=local_socket),
            patch("tools.data_center.connection_transport.default_ssh_tunnel_manager", return_value=manager),
            patch(
                "tools.data_center.readonly_relational_connectors.ssl.create_default_context",
                side_effect=lambda **_kwargs: native_context_factory(),
            ) as context_factory,
        ):
            result = session.submit("SELECT 1").result(timeout=5)
            self.assertEqual(result.rowcount, -1)
            self.assertIs(session.close().result(timeout=5).closed, True)

        self.assertEqual(manager.calls[0][1:], (choice.connection_id, choice.revision))
        self.assertEqual(fake_mysql.calls[0]["host"], "db.fixture.invalid")
        self.assertEqual(fake_mysql.calls[0]["port"], 3306)
        self.assertTrue(fake_mysql.calls[0]["defer_connect"])
        self.assertTrue(fake_mysql.calls[0]["ssl"].check_hostname)
        self.assertEqual(fake_mysql.calls[0]["ssl"].verify_mode, ssl.CERT_REQUIRED)
        context_factory.assert_called_once_with(cafile="C:/fixture/ca.pem")
        self.assertIs(fake_mysql.connection.connected_socket, local_socket)
        self.assertLess(events.index("mysql.close"), events.index("lease.release"))
        self.assertEqual(lease.release_calls, 1)

    def test_tunnel_without_tls_explicitly_disables_driver_tls(self):
        events = []
        lease = _Lease(events)
        manager = _Manager(lease)
        fake_mysql = _FakePyMySQL(events)
        local_socket = _LocalSocket(events)
        profile = _mysql_profile(
            ssh_enabled=True,
            ssh_host="ssh.fixture.invalid",
            ssh_username="ssh-user",
            ssh_auth_method="password",
            ssh_password_token="enc:fixture-ssh-token",
            tls_enabled=False,
        )
        with (
            patch.dict("sys.modules", {"pymysql": fake_mysql}),
            patch("socket.create_connection", return_value=local_socket),
        ):
            connection = connect_relational(
                "mysql", profile, "fixture-db-password", connect_timeout=2, read_timeout=4,
                connection_id=profile["id"], profile_revision="rev-1", ssh_manager=manager,
            )
            connection.close()
        self.assertTrue(fake_mysql.calls[0]["ssl_disabled"])
        self.assertNotIn("ssl_verify_cert", fake_mysql.calls[0])
        self.assertLess(events.index("mysql.close"), events.index("lease.release"))

    def test_cleanup_pending_retry_never_replays_database_close(self):
        events = []
        lease = _Lease(events)
        lease.release_results = [False, True]
        connection = _MysqlConnection(events)
        wrapped = LeasedConnection(connection, lease)
        with self.assertRaises(ConnectionTransportError) as raised:
            wrapped.close()
        self.assertEqual(raised.exception.code, "SSH_CLEANUP_PENDING")
        wrapped.close()
        self.assertEqual(events.count("mysql.close"), 1)
        self.assertEqual(lease.release_calls, 2)

    def test_direct_tls_keeps_existing_route_but_enforces_identity(self):
        events = []
        fake_mysql = _FakePyMySQL(events)
        profile = _mysql_profile(tls_enabled=True, tls_ca_path="C:/fixture/ca.pem")
        native_context_factory = ssl.create_default_context
        with (
            patch.dict("sys.modules", {"pymysql": fake_mysql}),
            patch(
                "tools.data_center.readonly_relational_connectors.ssl.create_default_context",
                side_effect=lambda **_kwargs: native_context_factory(),
            ) as context_factory,
        ):
            connection = connect_relational(
                "mysql", profile, "fixture-db-password", connect_timeout=2, read_timeout=4,
            )
        self.assertIs(connection, fake_mysql.connection)
        self.assertEqual(fake_mysql.calls[0]["host"], "db.fixture.invalid")
        self.assertTrue(fake_mysql.calls[0]["ssl"].check_hostname)
        self.assertEqual(fake_mysql.calls[0]["ssl"].verify_mode, ssl.CERT_REQUIRED)
        context_factory.assert_called_once_with(cafile="C:/fixture/ca.pem")


if __name__ == "__main__":
    unittest.main()
