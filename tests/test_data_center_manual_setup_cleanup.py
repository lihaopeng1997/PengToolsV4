from __future__ import annotations

import threading
import time
import unittest

from tools.data_center.manual_session import ManualSessionError, ManualSessionState, ManualSessionTarget
from tools.data_center.manual_relational_providers import BoundManualTableScope, create_manual_session
from tests.test_data_center_manual_table_scope import _Connection, _profile, _tree


class _CloseFailsOnceConnection(_Connection):
    def __init__(self, schema="", *, autocommit=True):
        super().__init__(schema, autocommit=autocommit)
        self.close_attempts = []

    def close(self):
        self.close_attempts.append(threading.get_ident())
        if len(self.close_attempts) == 1:
            self.events.append(("connection_close_failed", threading.get_ident()))
            raise OSError("TEMP vendor close diagnostic")
        super().close()


class _BlockingCloseConnection(_Connection):
    def __init__(self):
        super().__init__(autocommit=True)
        self.close_entered = threading.Event()
        self.allow_close = threading.Event()

    def close(self):
        self.close_entered.set()
        if not self.allow_close.wait(3):
            raise OSError("TEMP close wait timed out")
        super().close()


class ManualSetupCleanupTests(unittest.TestCase):
    @staticmethod
    def _new_session(provider, tab_id, connection, connector=None):
        database = "service.prod" if provider.endswith("oracle") else "signed-db"
        schema = 'Sales "East' if provider.endswith("oracle") else ""
        profile = _profile(provider, database, "LOGIN")
        service, _tree_host, tree_target, node, profile_host, choice = _tree(
            provider, profile, database, schema,
        )
        target = ManualSessionTarget(
            tab_id, choice.connection_id, choice.revision,
            database, schema or database, 4,
        )
        scope = BoundManualTableScope.capture(service, tree_target, node, 17, target, choice)
        selected_connector = connector or (lambda _profile, _secret: connection)
        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=selected_connector, secret_resolver=lambda _token: "TEMP-SECRET",
        )
        return session, service, tree_target, node, connection

    def test_stale_scope_setup_retains_handle_until_owner_close_retry_succeeds(self):
        connection = _CloseFailsOnceConnection("LOGIN")
        connector_calls = []

        def invalidate_in_connector(_loaded, _secret):
            connector_calls.append(threading.get_ident())
            service.invalidate(tree_target, node)
            return connection

        # Build the real signed tree first so connector code can invalidate its
        # actual registry entry while returning the new DBAPI connection.
        profile = _profile("oracle", "service.prod", "LOGIN")
        service, _host, tree_target, node, profile_host, choice = _tree(
            "oracle", profile, "service.prod", 'Sales "East',
        )
        target = ManualSessionTarget(
            "setup-retry", choice.connection_id, choice.revision,
            "service.prod", 'Sales "East', 8,
        )
        scope = BoundManualTableScope.capture(service, tree_target, node, 17, target, choice)
        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=invalidate_in_connector,
            secret_resolver=lambda _token: "TEMP-SECRET",
        )
        try:
            with self.assertRaises(ManualSessionError) as setup_failure:
                session.query("SELECT 1").result(timeout=3)
            self.assertEqual(setup_failure.exception.code, "MANUAL_COMMIT_CONFIGURATION_FAILED")
            self.assertIsNone(setup_failure.exception.__cause__)
            self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
            self.assertEqual(len(connector_calls), 1)
            self.assertTrue(session.poll_cleanup().connection_retained)
            self.assertFalse(connection.closed)
            self.assertEqual(
                [event[0] for event in connection.events],
                [],
                "stale setup must not configure autocommit or issue schema/user SQL",
            )

            with self.assertRaises(ManualSessionError) as blocked_query:
                session.query("SELECT 1").result(timeout=3)
            self.assertEqual(blocked_query.exception.code, "CONNECTION_SETUP_FAILED")
            self.assertEqual(len(connector_calls), 1, "retained failed setup must not reconnect")

            with self.assertRaises(ManualSessionError) as first_close:
                session.close().result(timeout=3)
            self.assertEqual(first_close.exception.code, "CONNECTION_CLOSE_FAILED")
            self.assertNotIn("TEMP vendor", str(first_close.exception))
            status = session.poll_cleanup()
            self.assertEqual(status.state, ManualSessionState.DISCONNECTED)
            self.assertTrue(status.connection_retained)
            self.assertTrue(status.owner_alive)
            self.assertFalse(status.cleanup_complete)
            self.assertFalse(connection.closed)

            result = session.close().result(timeout=3)
            self.assertTrue(result.closed)
            deadline = time.monotonic() + 3
            while not session.poll_cleanup().cleanup_complete and time.monotonic() < deadline:
                time.sleep(0.01)
            final_status = session.poll_cleanup()
            self.assertTrue(final_status.cleanup_complete)
            self.assertFalse(final_status.connection_retained)
            self.assertEqual(len(connection.close_attempts), 2)
            self.assertEqual(len(set(connector_calls + connection.close_attempts)), 1)
            self.assertFalse(any(event[0] in {"commit", "rollback"} for event in connection.events))
        finally:
            if not session.poll_cleanup().cleanup_complete:
                try:
                    session.close().result(timeout=3)
                except ManualSessionError:
                    session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_autocommit_configuration_failure_retains_same_handle_without_user_sql(self):
        def configuration_error():
            raise OSError("TEMP autocommit vendor diagnostic")

        connection = _Connection(autocommit=True, on_autocommit=configuration_error)
        session, service, _tree_target, _node, _connection = self._new_session(
            "mysql", "setup-config-failure", connection,
        )
        try:
            with self.assertRaises(ManualSessionError) as setup_failure:
                session.query("SELECT 1").result(timeout=3)
            self.assertEqual(setup_failure.exception.code, "MANUAL_COMMIT_CONFIGURATION_FAILED")
            self.assertIsNone(setup_failure.exception.__cause__)
            self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
            self.assertTrue(session.poll_cleanup().connection_retained)
            self.assertFalse(connection.closed)
            self.assertFalse(any(event[0] in {"cursor", "execute", "commit", "rollback"} for event in connection.events))
            with self.assertRaises(ManualSessionError) as blocked:
                session.query("SELECT 1").result(timeout=3)
            self.assertEqual(blocked.exception.code, "CONNECTION_SETUP_FAILED")
            self.assertTrue(session.close().result(timeout=3).closed)
            self.assertTrue(connection.closed)
            self.assertFalse(any(event[0] in {"commit", "rollback"} for event in connection.events))
        finally:
            if not session.poll_cleanup().cleanup_complete:
                session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_running_blocked_close_keeps_future_and_owner_reference_until_release(self):
        connection = _BlockingCloseConnection()
        session, service, _tree_target, _node, _connection = self._new_session(
            "mysql", "blocked-close", connection,
        )
        try:
            result = session.query("SELECT 1").result(timeout=3)
            self.assertEqual(result.rows, ((1,),))
            close_future = session.close()
            self.assertTrue(connection.close_entered.wait(3))
            self.assertTrue(close_future.running())
            self.assertFalse(close_future.done())
            self.assertFalse(close_future.cancel())
            status = session.poll_cleanup()
            self.assertTrue(status.busy)
            self.assertTrue(status.owner_alive)
            self.assertTrue(status.connection_retained)
            self.assertFalse(status.cleanup_complete)
            self.assertFalse(connection.closed)
            connection.allow_close.set()
            self.assertTrue(close_future.result(timeout=3).closed)
            deadline = time.monotonic() + 3
            while not session.poll_cleanup().cleanup_complete and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(session.poll_cleanup().cleanup_complete)
        finally:
            connection.allow_close.set()
            if not session.poll_cleanup().cleanup_complete:
                session.close().result(timeout=3)
            service.close().result(timeout=3)


if __name__ == "__main__":
    unittest.main()
