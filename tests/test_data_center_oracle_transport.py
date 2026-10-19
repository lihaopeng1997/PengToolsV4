import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.data_center.connection_transport import ConnectionTransportError, retry_default_transport_cleanup
from tools.data_center.readonly_relational_connectors import connect_relational, RelationalConnectorError
from tests.test_data_center_nosql_transport import _Manager, _profile


class OracleTransportTests(unittest.TestCase):
    def test_native_oracle_and_ob_odbc_dial_single_tunnel_and_preserve_timeouts(self):
        for provider in ("oracle", "oceanbase_oracle"):
            with self.subTest(provider=provider):
                calls, events = [], []
                manager = _Manager(events)
                class Connection:
                    call_timeout = 0
                    timeout = 0
                    def close(self):
                        events.append("database.close")
                raw = Connection()
                def build(*args, **kwargs):
                    calls.append((args, kwargs))
                    return raw
                profile = _profile(provider, port=1521 if provider == "oracle" else 2883,
                                   database="ORCL", username="fixture", mode="oracle", oceanbase_oracle_provider="odbc")
                with patch.dict("sys.modules", {"oracledb": SimpleNamespace(connect=build),
                           "pyodbc": SimpleNamespace(connect=build, drivers=lambda: ["OceanBase ODBC 2.0 Driver"])}), patch(
                    "tools.oracle_runtime.load_oracle_paths", return_value={"mode": "thin"}
                ), patch("tools.oracle_runtime.ensure_oracle_client"):
                    connection = connect_relational(provider, profile, "fixture", ssh_manager=manager,
                                                    connect_timeout=2.1, read_timeout=4.1)
                    if provider == "oracle":
                        self.assertEqual(calls[0][1]["host"], "127.0.0.1")
                        self.assertEqual(calls[0][1]["port"], 49153)
                        self.assertEqual(calls[0][1]["retry_count"], 0)
                        self.assertEqual(raw.call_timeout, 4100)
                    else:
                        self.assertIn("Server={127.0.0.1};Port=49153;", calls[0][0][0])
                        self.assertEqual(raw.timeout, 5)
                    self.assertEqual(manager.calls[0][0].database_host, "db.fixture.invalid")
                    connection.close()
                    self.assertEqual(events, ["database.close", "release"])

    def test_unsupported_tls_and_raw_dsn_are_blocked_before_ssh(self):
        for provider, changes in (("oracle", {"tls_enabled": True}),
                                  ("oceanbase_oracle", {"tls_enabled": True}),
                                  ("oracle", {"database": "host/service"}),
                                  ("oracle", {"dsn": "host/service"})):
            with self.subTest(provider=provider, changes=changes), patch(
                "tools.data_center.connection_transport.default_ssh_tunnel_manager"
            ) as manager:
                with self.assertRaises((ConnectionTransportError, RelationalConnectorError)):
                    profile = dict(_profile(provider, username="fixture", database="ORCL", mode="oracle"), **changes)
                    connect_relational(provider, profile, "", connect_timeout=8, read_timeout=8)
                manager.assert_not_called()

    def test_failed_timeout_setup_retains_database_until_real_close_succeeds(self):
        events = []
        class Connection:
            def close(self):
                events.append("database.close")
                if len(events) == 1:
                    raise RuntimeError("fixture pending close")
        with patch.dict("sys.modules", {"oracledb": SimpleNamespace(connect=lambda **kwargs: Connection())}), patch(
            "tools.oracle_runtime.load_oracle_paths", return_value={"mode": "thin"}
        ), patch("tools.oracle_runtime.ensure_oracle_client"), patch("tools.data_center.connection_transport._default_manager", None):
            with self.assertRaisesRegex(RelationalConnectorError, "SSH_CLEANUP_PENDING"):
                connect_relational("oracle", _profile("oracle", username="fixture", database="ORCL", mode="oracle"), "",
                                   ssh_manager=_Manager(events), connect_timeout=8, read_timeout=8)
            self.assertEqual(events, ["database.close"])
            self.assertTrue(retry_default_transport_cleanup())
            self.assertEqual(events, ["database.close", "database.close", "release"])


if __name__ == "__main__":
    unittest.main()
