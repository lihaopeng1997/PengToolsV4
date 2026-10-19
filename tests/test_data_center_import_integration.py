"""TEMP-only grouped import lifetime and decision integration coverage."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from tests.test_data_center_import_session import _MySqlProviderConnection
import tests.test_data_center_import_ui as _import_ui
from tools.data_center.import_preview import CsvSource
from tools.data_center.import_session import ImportSessionState
from ui.data_center.import_host import ImportHost


class ImportHostIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = _import_ui.ImportUiTests()
        self.fixture.app = self.app
        self.fixture.setUp()
        self.host = ImportHost()

    def tearDown(self):
        self.host.set_shutdown_gate(False)
        if self.host._entries:
            self.host.cleanup("rollback")
            self._pump_until(lambda: not self.host._entries, timeout=3)
            for import_id, entry in tuple(self.host._entries.items()):
                if entry.controller.session is not None and entry.controller.session.status.connection_retained:
                    self.host.retry_cleanup(import_id)
            self._pump_until(lambda: not self.host._entries, timeout=5)
        for controller in tuple(self.host.registry._sessions.values()):
            if controller.session.status.connection_retained:
                self.assertTrue(self.host.retry_cleanup(controller.controller.import_id))
        self.fixture.tearDown()

    def _pump_until(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return bool(predicate())

    def _new_import(self, name: str, connection: _MySqlProviderConnection):
        source = self.fixture.root / f"{name}.csv"
        source.write_text("name,amount\nAlpha,1.25\n", encoding="utf-8", newline="")
        self.fixture.connections.append(connection)
        controller = self.host.open_import(self.fixture.context, "en")
        self.assertIsNotNone(controller)
        self.fixture.controllers.append(controller)
        entry = self.host._entries[controller.import_id]
        self.fixture.dialogs.append(entry.dialog)
        self.fixture._start_import(controller, source)
        self.assertEqual(controller.progress().state, "READY_TO_COMMIT", controller.progress().safe_code)
        return controller, connection, entry.dialog

    def test_mixed_unknown_blocks_group_commit_and_rollback_only_acknowledges_unknown(self):
        unknown_connection = _MySqlProviderConnection()
        unknown_connection.commit_error = True
        unknown, unknown_connection, _unknown_dialog = self._new_import("ambiguous-commit", unknown_connection)
        self.assertTrue(unknown.commit())
        self.assertTrue(self._pump_until(
            lambda: unknown.progress().state == "UNKNOWN",
        ), f"commit failure should leave an unknown outcome: {unknown.session.status!r}")
        self.assertEqual(unknown_connection.commit_count, 1)
        self.assertEqual(unknown_connection.rollback_count, 0)
        ready, ready_connection, _ready_dialog = self._new_import(
            "still-ready", _MySqlProviderConnection(),
        )
        before = self.host.snapshot()
        self.assertFalse(before.can_commit)
        self.assertEqual(self.host.cleanup("commit"), "decision_required")
        self.assertEqual(ready_connection.commit_count, 0)
        self.assertEqual(ready_connection.rollback_count, 0)
        self.assertEqual(unknown_connection.commit_count, 1)
        self.assertEqual(unknown_connection.rollback_count, 0)

        result = self.host.cleanup("rollback")
        self.assertIn(result, {"pending", "ready"})
        self.assertTrue(self._pump_until(
            lambda: self.host.cleanup(None) == "ready",
            timeout=8,
        ),
                        f"group rollback did not retire owners: action={self.host._shutdown_action!r}; snapshot={self.host.snapshot()!r}")
        self.assertFalse(self.host._entries)
        self.assertEqual(ready.session.status.state, ImportSessionState.ROLLED_BACK)
        self.assertEqual((ready_connection.commit_count, ready_connection.rollback_count), (0, 1))
        self.assertEqual(unknown.session.status.state, ImportSessionState.UNKNOWN)
        self.assertTrue(unknown.unknown_acknowledged)
        self.assertEqual((unknown_connection.commit_count, unknown_connection.rollback_count), (1, 0))

    def test_terminal_owner_with_open_dialog_requires_explicit_host_close(self):
        from tools.data_center.import_session import ImportSessionState

        controller, connection, dialog = self._new_import(
            "terminal-dialog-close", _MySqlProviderConnection(),
        )
        self.assertTrue(dialog.isVisible())
        self.app.processEvents()
        self.assertTrue(dialog.commit_button.isEnabled())
        dialog.commit_button.click()
        self.assertTrue(self._pump_until(
            lambda: controller.session.status.state is ImportSessionState.COMMITTED
            and controller.session.cleanup_future.done()
            and not controller.session.status.owner_alive,
        ), f"explicit commit did not finish its actual owner: {controller.session.status!r}")
        self.assertEqual((connection.commit_count, connection.rollback_count), (1, 0))
        self.assertTrue(controller.session.status.cleanup_complete)
        self.assertTrue(dialog.isVisible(), "a terminal dialog remains an explicit close decision")
        owner = next(item for item in self.host.snapshot().owners if item.import_id == controller.import_id)
        self.assertTrue(owner.cleanup_complete)
        self.assertTrue(owner.dialog_open)
        self.assertEqual(self.host.status(), "ready_to_close")
        self.assertEqual(self.host.cleanup(None), "ready_to_close")
        self.assertEqual(self.host.cleanup("close"), "ready")
        self.assertFalse(dialog.isVisible())
        self.assertNotIn(controller.import_id, self.host._entries)

    def test_explicit_group_commit_retires_a_completed_import_dialog_without_second_commit(self):
        controller, connection, dialog = self._new_import(
            "terminal-dialog-group-commit", _MySqlProviderConnection(),
        )
        self.app.processEvents()
        self.assertTrue(dialog.commit_button.isEnabled())
        dialog.commit_button.click()
        self.assertTrue(self._pump_until(
            lambda: controller.session.status.state is ImportSessionState.COMMITTED
            and controller.session.cleanup_future.done()
            and not controller.session.status.owner_alive,
        ))
        self.assertEqual((connection.commit_count, connection.rollback_count), (1, 0))
        self.assertTrue(dialog.isVisible())

        self.assertEqual(self.host.cleanup("commit"), "ready")
        self.assertFalse(dialog.isVisible())
        self.assertNotIn(controller.import_id, self.host._entries)
        self.assertEqual((connection.commit_count, connection.rollback_count), (1, 0))

    def test_explicit_group_rollback_retires_a_completed_import_dialog_without_second_decision(self):
        controller, connection, dialog = self._new_import(
            "terminal-dialog-group-rollback", _MySqlProviderConnection(),
        )
        self.app.processEvents()
        self.assertTrue(dialog.rollback_button.isEnabled())
        dialog.rollback_button.click()
        self.assertTrue(self._pump_until(
            lambda: controller.session.status.state is ImportSessionState.ROLLED_BACK
            and controller.session.cleanup_future.done()
            and not controller.session.status.owner_alive,
        ))
        self.assertEqual((connection.commit_count, connection.rollback_count), (0, 1))
        self.assertTrue(dialog.isVisible())

        self.assertEqual(self.host.cleanup("rollback"), "ready")
        self.assertFalse(dialog.isVisible())
        self.assertNotIn(controller.import_id, self.host._entries)
        self.assertEqual((connection.commit_count, connection.rollback_count), (0, 1))

    def test_dead_unknown_owner_acknowledgement_reaches_parent_preflight_without_rollback(self):
        connection = _MySqlProviderConnection()
        connection.commit_error = True
        controller, connection, dialog = self._new_import("standalone-unknown-ack", connection)
        entry = self.host._entries[controller.import_id]
        self.assertTrue(controller.commit())
        self.assertTrue(self._pump_until(
            lambda: controller.progress().state == "UNKNOWN"
            and controller.session.cleanup_future.done()
            and not controller.session.status.owner_alive,
            timeout=8,
        ), f"failed commit did not finish with a dead UNKNOWN owner: {controller.session.status!r}")
        self.assertEqual((connection.commit_count, connection.rollback_count), (1, 0))
        self.assertTrue(controller.request_close(action="acknowledge"))
        self.assertTrue(controller.unknown_acknowledged)
        dialog.hide()  # the accepting standalone close event retires only the window

        self.assertFalse(any(
            item.import_id == controller.import_id for item in self.host.snapshot().owners
        ), "a dead, acknowledged, hidden owner is collected before the public snapshot")
        self.assertFalse(self.host.has_parent_decision(entry.parent_sql_tab_id, entry.parent_sql_generation))
        self.assertEqual(self.host.cleanup(None), "ready")
        self.assertEqual((connection.commit_count, connection.rollback_count), (1, 0))
        self.assertNotIn(controller.import_id, self.host._entries)

    def test_failed_close_is_retryable_not_busy_and_deferred_parent_delete_keeps_qobjects(self):
        parent = QWidget()
        connection = _import_ui._CloseFailingMySqlProviderConnection()
        controller, connection, dialog = self._new_import("retry-close", connection)
        self.assertTrue(controller.rollback())
        self.assertTrue(self._pump_until(
            lambda: controller.session.status.state is ImportSessionState.ROLLED_BACK
            and controller.session.status.connection_retained,
        ), f"first close must expose retained failure: progress={controller.progress()!r}; status={controller.session.status!r}; events={connection.events!r}")

        owner = next(item for item in self.host.snapshot().owners if item.import_id == controller.import_id)
        self.assertEqual(owner.state, "ROLLED_BACK")
        self.assertFalse(owner.busy)
        self.assertTrue(owner.failed_cleanup)
        self.assertFalse(owner.cleanup_complete)
        self.assertTrue(controller.session.status.owner_alive)
        self.assertFalse(controller.session.cleanup_future.done())
        self.assertTrue(self.host.retry_cleanup(controller.import_id))

        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertTrue(sip.isdeleted(parent))
        self.assertIsNone(controller.parent())
        self.assertIsNone(dialog.parentWidget())
        self.assertFalse(sip.isdeleted(controller))
        self.assertFalse(sip.isdeleted(dialog))
        self.assertTrue(self._pump_until(
            lambda: controller.session.cleanup_future.done()
            and not controller.session.status.owner_alive
            and connection.closed,
        ))
        dialog.hide()
        self.host._collect_finished()
        self.assertNotIn(controller.import_id, self.host._entries)

    def test_done_decision_future_retains_live_owner_controller_and_dialog(self):
        connection = _MySqlProviderConnection()
        commit_entered = threading.Event()
        release_commit = threading.Event()
        release_future_callback = threading.Event()
        callback_started = threading.Event()
        callback_observations = {}
        original_commit = connection.commit

        def blocked_commit():
            commit_entered.set()
            if not release_commit.wait(5):
                raise TimeoutError("temporary owner-retention commit wait timed out")
            original_commit()

        connection.commit = blocked_commit
        controller, connection, dialog = self._new_import("done-future-live-owner", connection)
        session = controller.session
        import_id = controller.import_id
        self.assertIsNotNone(session)
        try:
            self.assertTrue(controller.commit())
            self.assertTrue(commit_entered.wait(3), "owner did not enter the bounded fake commit")
            future = session.decision_future
            self.assertIsNotNone(future)

            def hold_owner_after_future_done(_future):
                callback_observations["status"] = session.status
                callback_observations["cleanup_future_done"] = session.cleanup_future.done()
                callback_observations["entry_retained"] = self.host._entries.get(import_id)
                callback_observations["registry_snapshot"] = self.host.registry.snapshot()
                callback_started.set()
                if not release_future_callback.wait(5):
                    raise TimeoutError("temporary future callback release timed out")

            future.add_done_callback(hold_owner_after_future_done)
            release_commit.set()
            self.assertTrue(callback_started.wait(3), "decision future callback did not run")
            self.assertTrue(future.done())
            self.assertTrue(session.cleanup_future.done())
            status = callback_observations["status"]
            self.assertTrue(status.owner_alive, "the callback runs before the actual owner thread exits")
            self.assertTrue(callback_observations["cleanup_future_done"])
            self.assertFalse(status.connection_retained)
            self.assertEqual(status.transaction_outcome, "committed")
            registry_snapshot = callback_observations["registry_snapshot"]
            retained = next((item for item in registry_snapshot.entries if item.import_id == import_id), None)
            self.assertIsNotNone(retained)
            self.assertTrue(retained.cleanup_future_done)
            self.assertTrue(retained.owner_alive)
            self.assertIn(import_id, registry_snapshot.active_ids)
            self.assertFalse(registry_snapshot.can_close)

            # The GUI thread has not yet processed the queued finish signal;
            # the host's process-lifetime keepalive and entry still retain the
            # parentless QObjects at the exact Future-done/owner-live point.
            self.assertTrue(session._owner.is_alive())
            self.assertIs(callback_observations["entry_retained"].controller, controller)
            self.assertIs(callback_observations["entry_retained"].dialog, dialog)
            self.assertIn(import_id, self.host._entries)
            self.assertIs(self.host._entries[import_id].controller, controller)
            self.assertIs(self.host._entries[import_id].dialog, dialog)
            self.assertFalse(sip.isdeleted(controller))
            self.assertFalse(sip.isdeleted(dialog))
        finally:
            release_commit.set()
            release_future_callback.set()

        self.assertTrue(self._pump_until(lambda: not session.status.owner_alive, timeout=5))
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        dialog.hide()
        self.host._collect_finished()
        self.assertNotIn(import_id, self.host._entries)


if __name__ == "__main__":
    unittest.main()
