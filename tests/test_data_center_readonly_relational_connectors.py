from __future__ import annotations

from types import SimpleNamespace
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools.data_center.readonly_relational_connectors import (
    RelationalConnectorError,
    connect_relational,
)
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.readonly_driver_adapters import ReadOnlyInitializationError


def _profile(provider: str = "mysql", **extra):
    data = {
        "id": "c1",
        "provider": provider,
        "mode": "mysql" if provider.endswith("mysql") else "oracle",
        "dialect": "mysql" if provider.endswith("mysql") else "oracle",
        "host": "db.internal",
        "port": 3306 if provider.endswith("mysql") else 1521,
        "database": "APP",
        "username": "agent",
    }
    data.update(extra)
    return data


class _Cursor:
    description = ()

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        self.connection.events.append(("execute", sql, params))
        return self

    def close(self):
        self.connection.events.append(("cursor_close",))


class _Connection:
    def __init__(self):
        self.events = []
        self.closed = False
        self.autocommit = True
        self.timeout = 0
        self.call_timeout = 0

    def cursor(self):
        self.events.append(("cursor",))
        return _Cursor(self)

    def rollback(self):
        self.events.append(("rollback",))

    def close(self):
        self.closed = True
        self.events.append(("close",))


class ReadonlyRelationalConnectorTests(unittest.TestCase):
    def test_mysql_passes_bounded_timeouts_before_connect(self):
        calls = []
        connection = _Connection()
        fake = SimpleNamespace(
            cursors=SimpleNamespace(Cursor=object),
            connect=lambda **kwargs: (calls.append(kwargs) or connection),
        )
        with patch.dict(sys.modules, {"pymysql": fake}):
            result = connect_relational(
                "mysql",
                _profile(),
                "pw-hidden",
                connect_timeout=8,
                read_timeout=15,
            )
        self.assertIs(result, connection)
        self.assertEqual(calls, [{
            "host": "db.internal",
            "port": 3306,
            "user": "agent",
            "password": "pw-hidden",
            "database": "APP",
            "charset": "utf8mb4",
            "cursorclass": object,
            "connect_timeout": 8.0,
            "read_timeout": 15.0,
            "write_timeout": 15.0,
            "autocommit": False,
            "local_infile": False,
        }])

    def test_default_host_path_uses_dedicated_connector_and_readonly_lease(self):
        calls = []
        connection = _Connection()
        fake = SimpleNamespace(
            cursors=SimpleNamespace(Cursor=object),
            connect=lambda **kwargs: (calls.append(kwargs) or connection),
        )
        profile = _profile(password_ciphertext="encrypted-only")
        host = RelationalReadOnlyHost(
            connections={"c1": profile},
            decryptor=lambda token: "pw-hidden",
        )
        with patch.dict(sys.modules, {"pymysql": fake}):
            lease = host.connect("c1", session_id="tab", generation=1)
        self.assertEqual(calls[0]["connect_timeout"], 8.0)
        self.assertEqual(calls[0]["read_timeout"], 15.0)
        self.assertFalse(lease.capabilities.supports_timeout)
        lease.close()
        self.assertTrue(connection.closed)

    def test_default_oracle_sets_call_timeout_before_readonly_init_and_cleans_failure(self):
        secret = "oracle-vendor-secret"

        class OracleCursor:
            description = ()

            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, params=None):
                self.connection.events.append(("execute", sql, params))
                if self.connection.fail:
                    raise RuntimeError(f"driver password={secret}")
                return self

            def close(self):
                self.connection.events.append(("cursor_close",))

        class OracleConnection:
            def __init__(self, fail=False):
                self.events = []
                self.fail = fail
                self.closed = False
                self._autocommit = True
                self._call_timeout = 0

            @property
            def autocommit(self):
                return self._autocommit

            @autocommit.setter
            def autocommit(self, value):
                self.events.append(("autocommit", value))
                self._autocommit = value

            @property
            def call_timeout(self):
                return self._call_timeout

            @call_timeout.setter
            def call_timeout(self, value):
                self.events.append(("call_timeout", value))
                self._call_timeout = value

            def cursor(self):
                self.events.append(("cursor",))
                return OracleCursor(self)

            def rollback(self):
                self.events.append(("rollback",))

            def close(self):
                self.events.append(("close",))
                self.closed = True

        def make_host(connection):
            fake_oracledb = SimpleNamespace(connect=lambda **_: connection)
            return RelationalReadOnlyHost(
                connections={
                    "c1": _profile(
                        "oracle",
                        mode="oracle",
                        dialect="oracle",
                        database="ORCL",
                        password_ciphertext="encrypted-only",
                    )
                },
                decryptor=lambda _token: secret,
                connector=None,
            ), fake_oracledb

        success_connection = OracleConnection()
        success_host, success_driver = make_host(success_connection)
        with patch.dict(sys.modules, {"oracledb": success_driver}), patch(
            "tools.oracle_runtime.load_oracle_paths",
            return_value={"mode": "thin", "lib_dir": "", "home": "", "oci_lib": ""},
        ), patch("tools.oracle_runtime.ensure_oracle_client"), patch(
            "socket.create_connection",
            side_effect=AssertionError("Oracle fake must not use a real socket"),
        ):
            lease = success_host.connect("c1", session_id="tab", generation=1)
        self.assertGreater(success_connection.call_timeout, 0)
        names = [event[0] for event in success_connection.events]
        self.assertLess(names.index("call_timeout"), names.index("cursor"))
        self.assertLess(names.index("cursor"), names.index("execute"))
        lease.close()

        failure_connection = OracleConnection(fail=True)
        failure_host, failure_driver = make_host(failure_connection)
        with patch.dict(sys.modules, {"oracledb": failure_driver}), patch(
            "tools.oracle_runtime.load_oracle_paths",
            return_value={"mode": "thin", "lib_dir": "", "home": "", "oci_lib": ""},
        ), patch("tools.oracle_runtime.ensure_oracle_client"):
            with self.assertRaises(ReadOnlyInitializationError) as raised:
                failure_host.connect("c1", session_id="tab", generation=1)
        self.assertEqual(raised.exception.code, "READONLY_INIT_FAILED")
        self.assertTrue(failure_connection.closed)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, repr(raised.exception))

    def test_oceanbase_mysql_uses_explicit_mysql_mode(self):
        calls = []
        fake = SimpleNamespace(
            cursors=SimpleNamespace(Cursor=object),
            connect=lambda **kwargs: (calls.append(kwargs) or _Connection()),
        )
        with patch.dict(sys.modules, {"pymysql": fake}):
            connect_relational(
                "oceanbase",
                _profile(
                    provider="oceanbase",
                    mode="mysql",
                    dialect="oceanbase",
                    port=2883,
                ),
                "pw",
                connect_timeout=2,
                read_timeout=3,
            )
        self.assertEqual(calls[0]["port"], 2883)
        self.assertEqual(calls[0]["connect_timeout"], 2.0)
        self.assertEqual(calls[0]["read_timeout"], 3.0)

    def test_oracle_uses_structured_target_and_retry_zero(self):
        calls = []
        connection = _Connection()
        fake = SimpleNamespace(connect=lambda **kwargs: (calls.append(kwargs) or connection))
        with patch.dict(sys.modules, {"oracledb": fake}), patch(
            "tools.oracle_runtime.load_oracle_paths",
            return_value={"mode": "thin", "lib_dir": "", "home": "", "oci_lib": ""},
        ), patch("tools.oracle_runtime.ensure_oracle_client") as ensure:
            result = connect_relational(
                "oracle",
                _profile("oracle", mode="oracle", dialect="oracle", database="ORCL"),
                "pw",
                connect_timeout=4.5,
                read_timeout=9,
            )
        self.assertIs(result, connection)
        ensure.assert_called_once_with("thin", lib_dir="", home="", oci_lib="")
        self.assertEqual(calls[0], {
            "user": "agent",
            "password": "pw",
            "host": "db.internal",
            "port": 1521,
            "service_name": "ORCL",
            "tcp_connect_timeout": 4.5,
            "retry_count": 0,
        })
        self.assertEqual(connection.call_timeout, 9000)

    def test_oracle_raw_dsn_is_rejected_before_driver_import(self):
        fake = SimpleNamespace(connect=lambda **_: self.fail("driver must not be called"))
        with patch.dict(sys.modules, {"oracledb": fake}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational(
                    "oracle",
                    _profile("oracle", database="host:1521/service"),
                    "pw",
                    connect_timeout=4,
                    read_timeout=5,
                )
        self.assertEqual(raised.exception.code, "UNSUPPORTED_ORACLE_DSN")

    def test_oracle_call_timeout_failure_closes_connection_before_lease_init(self):
        class NoCallTimeout:
            __slots__ = ("closed",)

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        connection = NoCallTimeout()
        fake = SimpleNamespace(connect=lambda **_: connection)
        with patch.dict(sys.modules, {"oracledb": fake}), patch(
            "tools.oracle_runtime.load_oracle_paths",
            return_value={"mode": "thin", "lib_dir": "", "home": "", "oci_lib": ""},
        ), patch("tools.oracle_runtime.ensure_oracle_client"):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational(
                    "oracle",
                    _profile("oracle", mode="oracle", dialect="oracle", database="ORCL"),
                    "pw",
                    connect_timeout=2,
                    read_timeout=4,
                )
        self.assertEqual(raised.exception.code, "ORACLE_CALL_TIMEOUT_UNAVAILABLE")
        self.assertTrue(connection.closed)

    def test_oceanbase_oracle_sets_integer_statement_timeout_and_translates_binds(self):
        calls = []
        raw = _Connection()

        class FakePyodbc:
            @staticmethod
            def drivers():
                return ["OceanBase ODBC 2.0 Driver"]

            @staticmethod
            def connect(connection_string, **kwargs):
                calls.append((connection_string, kwargs))
                return raw

        with patch.dict(sys.modules, {"pyodbc": FakePyodbc}):
            connection = connect_relational(
                "oceanbase_oracle",
                _profile(
                    "oceanbase_oracle",
                    mode="oracle",
                    dialect="oracle",
                    port=2883,
                    oceanbase_oracle_provider="odbc",
                ),
                " pw;secret} ",
                connect_timeout=2.1,
                read_timeout=4.1,
            )
        self.assertEqual(calls[0][1], {"timeout": 3, "autocommit": False})
        self.assertEqual(raw.timeout, 5)
        self.assertIn("Password={ pw;secret}} };", calls[0][0])
        connection.cursor().execute("SELECT :1, ':2'", [7])
        self.assertEqual(raw.events[-1], ("execute", "SELECT ?, ':2'", (7,)))

    def test_oceanbase_oracle_timeout_failure_closes_raw_connection(self):
        class NoTimeout:
            __slots__ = ("events", "closed", "autocommit")

            def __init__(self):
                self.events = []
                self.closed = False
                self.autocommit = False

            def close(self):
                self.closed = True

        raw = NoTimeout()

        class FakePyodbc:
            @staticmethod
            def drivers():
                return ["OceanBase ODBC 2.0 Driver"]

            @staticmethod
            def connect(*_args, **_kwargs):
                return raw

        with patch.dict(sys.modules, {"pyodbc": FakePyodbc}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational(
                    "oceanbase_oracle",
                    _profile(
                        "oceanbase_oracle",
                        mode="oracle",
                        dialect="oracle",
                        oceanbase_oracle_provider="odbc",
                    ),
                    "pw",
                    connect_timeout=2,
                    read_timeout=4,
                )
        self.assertEqual(raised.exception.code, "ODBC_STATEMENT_TIMEOUT_UNAVAILABLE")
        self.assertTrue(raw.closed)

    def test_ambiguous_fuzzy_odbc_drivers_fail_before_connect(self):
        calls = []

        class FakePyodbc:
            @staticmethod
            def drivers():
                return ["OceanBase ODBC Legacy", "OceanBase ODBC Experimental"]

            @staticmethod
            def connect(*_args, **_kwargs):
                calls.append(True)
                return _Connection()

        with patch.dict(sys.modules, {"pyodbc": FakePyodbc}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational(
                    "oceanbase_oracle",
                    _profile(
                        "oceanbase_oracle",
                        mode="oracle",
                        dialect="oracle",
                        oceanbase_oracle_provider="odbc",
                    ),
                    "pw",
                    connect_timeout=2,
                    read_timeout=4,
                )
        self.assertEqual(raised.exception.code, "ODBC_DRIVER_AMBIGUOUS")
        self.assertEqual(calls, [])

    def test_invalid_timeout_and_unsupported_transport_fail_before_driver_import(self):
        fake = SimpleNamespace(
            cursors=SimpleNamespace(Cursor=object),
            connect=lambda **_: self.fail("driver must not be called"),
        )
        with patch.dict(sys.modules, {"pymysql": fake}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational("mysql", _profile(ssl_ca="C:/ca.pem"), "pw", connect_timeout=0, read_timeout=4)
        self.assertEqual(raised.exception.code, "CONNECT_TIMEOUT_INVALID")
        with patch.dict(sys.modules, {"pymysql": fake}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational("mysql", _profile(ssl_ca="C:/ca.pem"), "pw", connect_timeout=2, read_timeout=4)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_TRANSPORT")

    def test_huge_finite_timeout_is_rejected_before_driver_connect(self):
        calls = []
        fake = SimpleNamespace(
            cursors=SimpleNamespace(Cursor=object),
            connect=lambda **kwargs: (calls.append(kwargs) or _Connection()),
        )
        with patch.dict(sys.modules, {"pymysql": fake}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational("mysql", _profile(), "pw", connect_timeout=86_400.1, read_timeout=4)
        self.assertEqual(raised.exception.code, "CONNECT_TIMEOUT_INVALID")
        self.assertEqual(calls, [])
        with patch.dict(sys.modules, {"pymysql": fake}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational("mysql", _profile(), "pw", connect_timeout=2, read_timeout=86_400.1)
        self.assertEqual(raised.exception.code, "READ_TIMEOUT_INVALID")
        self.assertEqual(calls, [])

    def test_connector_vendor_error_is_sanitized(self):
        secret = "vendor-password-secret"
        fake = SimpleNamespace(
            cursors=SimpleNamespace(Cursor=object),
            connect=lambda **_: (_ for _ in ()).throw(RuntimeError(f"password={secret}")),
        )
        with patch.dict(sys.modules, {"pymysql": fake}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational("mysql", _profile(), secret, connect_timeout=2, read_timeout=4)
        self.assertEqual(raised.exception.code, "CONNECT_FAILED")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, repr(raised.exception))

    def test_unsupported_target_and_dameng_are_closed_before_driver_io(self):
        with self.assertRaises(RelationalConnectorError) as raised:
            connect_relational(
                "postgres",
                _profile("postgres", mode="postgres", dialect="postgres"),
                "pw",
                connect_timeout=2,
                read_timeout=4,
            )
        self.assertEqual(raised.exception.code, "UNSUPPORTED_COMBINATION")
        fake_dm = SimpleNamespace(connect=lambda **_: self.fail("dmPython must not be imported/used"))
        with patch.dict(sys.modules, {"dmPython": fake_dm}):
            with self.assertRaises(RelationalConnectorError) as raised:
                connect_relational(
                    "dameng",
                    _profile("dameng", mode="dameng", dialect="dameng", port=5236),
                    "pw",
                    connect_timeout=2,
                    read_timeout=4,
                )
        self.assertEqual(raised.exception.code, "DAMENG_SERVICE_TIMEOUT_UNVERIFIED")

    def test_module_import_does_not_load_drivers_or_legacy_config(self):
        project_root = Path(__file__).resolve().parent.parent
        probe = r'''
import builtins
import sys
forbidden = {"oracledb", "pymysql", "pyodbc", "dmPython", "tools.db_connect", "config"}
original = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.partition(".")[0]
    if level == 0 and (root in forbidden or name in forbidden):
        raise AssertionError(name)
    return original(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
import tools.data_center.readonly_relational_connectors as module
assert "connect_relational" in module.__all__
assert not hasattr(module, "readonly_relational_connect")
assert not hasattr(module, "create_relational_connection")
assert not any(item.partition(".")[0] in forbidden for item in sys.modules)
print("connector import smoke passed")
'''
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=project_root,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(result.stdout.strip(), "connector import smoke passed")


if __name__ == "__main__":
    unittest.main()
