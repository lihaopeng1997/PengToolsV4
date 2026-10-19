"""Closed metadata-template tests at the real read-only driver boundary.

The lease and adapter are exercised with a tiny in-memory DB-API connection.
No configuration, credentials, network, or real driver is involved.
"""

from __future__ import annotations

import unittest

from tools.data_center.readonly_driver_adapters import (
    DBAPIReadOnlyAdapter,
    ReadOnlyCancelled,
    ReadOnlyQueryError,
    ReadOnlyTimeout,
)
from tools.data_center.readonly_lease import ReadOnlyLeaseFactory, ReadOnlyTarget


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.description = (("value",),)

    def execute(self, sql, parameters=None):
        self.connection.events.append(("execute", str(sql), parameters))
        if str(sql).strip() in {
            "SET TRANSACTION READ ONLY",
            "START TRANSACTION READ ONLY",
        }:
            self.rows = []
        else:
            self.rows = list(self.connection.rows)
        return self

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", size))
        return self.rows[:size]

    def close(self) -> None:
        self.connection.events.append(("cursor_close",))


class _Connection:
    def __init__(self, rows: tuple[tuple[object, ...], ...] = ((1,), (2,), (3,))) -> None:
        self.events: list[tuple] = []
        self.rows = rows
        self._autocommit = True
        self.call_timeout = 0
        self.closed = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", value))
        self._autocommit = value

    def cursor(self):
        self.events.append(("cursor",))
        return _Cursor(self)

    def rollback(self):
        self.events.append(("rollback",))

    def close(self):
        self.events.append(("close",))
        self.closed = True

    def commit(self):  # pragma: no cover - reaching this proves a bug
        raise AssertionError("read-only metadata must never commit")


def _open_lease(provider: str, connection: _Connection, *, mode: str, dialect: str):
    target = ReadOnlyTarget("metadata-connection", "profile-1", provider, dialect=dialect, mode=mode)
    profile = {
        "connection_id": target.connection_id,
        "profile_revision": target.profile_revision,
        "provider": target.provider,
        "mode": target.mode,
        "dialect": target.dialect,
    }
    factory = ReadOnlyLeaseFactory(
        target,
        profile_loader=lambda *_args, **_kwargs: profile,
        secret_loader=lambda *_args, **_kwargs: "in-memory-secret",
        connector=lambda *_args, **_kwargs: connection,
    )
    return factory(target.connection_id, session_id="metadata-session", generation=1)


class MetadataDriverBoundaryTests(unittest.TestCase):
    def test_user_tree_uses_only_closed_directory_template_ids(self) -> None:
        connection = _Connection(rows=(("APP",), ("TEST",), ("MORE",)))
        lease = _open_lease("mysql", connection, mode="mysql", dialect="mysql")
        try:
            result = lease.execute_metadata("mysql:directory_databases", (51, 0), row_limit=2)
            self.assertEqual(result["rows"], (("APP",), ("TEST",)))
            self.assertTrue(result["truncated"])
            query = [event for event in connection.events if event[0] == "execute"][1]
            self.assertIn("information_schema.SCHEMATA", query[1])
            self.assertEqual(query[2], (51, 0))
            count = len([event for event in connection.events if event[0] == "execute"])
            for template_id in ("mysql:directory_search", "oracle:directory_databases", "SELECT * FROM users"):
                with self.subTest(template_id=template_id):
                    with self.assertRaises(ReadOnlyQueryError) as raised:
                        lease.execute_metadata(template_id, ())
                    self.assertEqual(raised.exception.code, "METADATA_TEMPLATE_UNAVAILABLE")
                    self.assertEqual(len([event for event in connection.events if event[0] == "execute"]), count)
        finally:
            lease.close()

    def test_closed_templates_bind_per_provider_after_readonly_initialization(self) -> None:
        cases = (
            ("oracle", "oracle", "oracle:search", "SET TRANSACTION READ ONLY", ("APP", "%ORDERS%", "TABLE", "VIEW", 21, 0), ":1", False),
            ("oceanbase_oracle", "oracle", "oceanbase_oracle:search", "SET TRANSACTION READ ONLY", ("APP", "%ORDERS%", "TABLE", "VIEW", 21, 0), ":1", False),
            ("mysql", "mysql", "mysql:search", "START TRANSACTION READ ONLY", ("APP", "%ORDERS%", "BASE TABLE", "VIEW", 21, 0), "%s", True),
            ("oceanbase_mysql", "mysql", "oceanbase_mysql:search", "START TRANSACTION READ ONLY", ("APP", "%ORDERS%", "BASE TABLE", "VIEW", 21, 0), "%s", True),
        )
        for provider, mode, template_id, init_sql, parameters, marker, format_style in cases:
            with self.subTest(provider=provider):
                connection = _Connection()
                lease = _open_lease(provider, connection, mode=mode, dialect=mode)
                try:
                    result = lease.execute_metadata(template_id, parameters, row_limit=2)
                finally:
                    lease.close()

                self.assertEqual(result["rows"], ((1,), (2,)))
                self.assertTrue(result["truncated"])
                execute_events = [event for event in connection.events if event[0] == "execute"]
                self.assertEqual(execute_events[0][1], init_sql)
                self.assertEqual(execute_events[1][2], parameters)
                self.assertIn(marker, execute_events[1][1])
                self.assertEqual(format_style, marker == "%s")
                cursor_indices = [index for index, event in enumerate(connection.events) if event[0] == "cursor"]
                close_indices = [index for index, event in enumerate(connection.events) if event[0] == "cursor_close"]
                self.assertEqual(len(cursor_indices), 2)
                self.assertEqual(len(close_indices), 2)
                self.assertLess(close_indices[0], cursor_indices[1])
                self.assertEqual([event[0] for event in connection.events[-2:]], ["rollback", "close"])
                fetch_events = [event for event in connection.events if event[0] == "fetchmany"]
                self.assertEqual(fetch_events, [("fetchmany", 3)])
                self.assertFalse(any(event[0] == "commit" for event in connection.events))

    def test_unknown_mismatched_and_raw_template_ids_fail_before_query_execute(self) -> None:
        connection = _Connection()
        lease = _open_lease("oracle", connection, mode="oracle", dialect="oracle")
        try:
            init_execute_count = len([event for event in connection.events if event[0] == "execute"])
            for template_id in ("oracle:unknown", "mysql:search", "SELECT owner FROM all_objects"):
                with self.subTest(template_id=template_id):
                    with self.assertRaises(ReadOnlyQueryError) as raised:
                        lease.execute_metadata(template_id, ())
                    self.assertEqual(raised.exception.code, "METADATA_TEMPLATE_UNAVAILABLE")
                    self.assertEqual(
                        len([event for event in connection.events if event[0] == "execute"]),
                        init_execute_count,
                    )
        finally:
            lease.close()

    def test_cancelled_or_expired_metadata_fails_before_query_execute(self) -> None:
        connection = _Connection()
        lease = _open_lease("oracle", connection, mode="oracle", dialect="oracle")

        class Cancelled:
            def is_cancelled(self):
                return True

        try:
            with self.assertRaises(ReadOnlyCancelled) as cancelled:
                lease.execute_metadata("oracle:search", (), cancellation=Cancelled())
            self.assertFalse(cancelled.exception.result_unknown)
            with self.assertRaises(ReadOnlyTimeout) as expired:
                lease.execute_metadata("oracle:search", (), deadline=0.0)
            self.assertFalse(expired.exception.result_unknown)
            execute_events = [event for event in connection.events if event[0] == "execute"]
            self.assertEqual(len(execute_events), 1)
            self.assertEqual(execute_events[0][1], "SET TRANSACTION READ ONLY")
        finally:
            lease.close()

    def test_metadata_does_not_weaken_sql_policy_for_ordinary_reads(self) -> None:
        connection = _Connection()
        adapter = DBAPIReadOnlyAdapter("oracle", connection).initialize()
        try:
            adapter.execute_metadata("oracle:search", ("APP", "%ORDERS%", "TABLE", "VIEW", 2, 0), row_limit=1)
            before = len([event for event in connection.events if event[0] == "execute"])
            with self.assertRaises(ReadOnlyQueryError) as raised:
                adapter.execute_readonly("DELETE FROM orders")
            self.assertEqual(raised.exception.code, "READ_ONLY_SQL_REQUIRED")
            self.assertEqual(
                len([event for event in connection.events if event[0] == "execute"]),
                before,
            )
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
