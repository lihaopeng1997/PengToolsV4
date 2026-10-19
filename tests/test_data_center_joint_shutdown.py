"""TEMP-only integration of MainWindow exit and DataCenterPanel worker cleanup."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QWidget

from panels.data_center_panel import DataCenterPanel
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.data_center.manual_query import (
    ManualShutdownSessionSnapshot,
    ManualShutdownSnapshot,
    ManualShutdownStatus,
)


APP = QApplication.instance() or QApplication([])


class _CloseEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class _ControlledQThread(QThread):
    """A TEMP worker whose finish point is controlled by the test."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.entered.set()
        self.release.wait(10)


class _TempWorkspaceHost(QWidget):
    def __init__(self, connection_id, *, pending_worker=False, unknown=False):
        super().__init__()
        self.connection_id = connection_id
        self.unknown = bool(unknown)
        self.manual_calls = []
        self.manual_gates = []
        self.workbench_calls = 0
        self.agent_calls = 0
        self._worker = _ControlledQThread() if pending_worker else None

    def manual_shutdown_snapshot(self):
        sessions = ()
        if self.unknown:
            sessions = (ManualShutdownSessionSnapshot(
                tab_id=f"TEMP-{self.connection_id}",
                generation=1,
                state="UNKNOWN",
                busy=False,
                cleanup_complete=False,
            ),)
        return ManualShutdownSnapshot(sessions=sessions, can_commit=not self.unknown)

    def manual_shutdown_cleanup(self, disposition=None):
        self.manual_calls.append(disposition)
        if disposition is None:
            return (
                ManualShutdownStatus.DECISION_REQUIRED
                if self.unknown else ManualShutdownStatus.READY
            )
        if disposition == "cancel":
            return ManualShutdownStatus.CANCELLED
        return ManualShutdownStatus.READY

    def manual_shutdown_gate(self, enabled):
        self.manual_gates.append(bool(enabled))
        return True

    def workbench_shutdown_cleanup(self):
        self.workbench_calls += 1
        return self._worker is None or not self._worker.isRunning()

    def agent_shutdown_cleanup(self):
        self.agent_calls += 1
        return True


class DataCenterJointShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _make_panel(self, *, opened, pending=(), unknown=()):
        rows = [
            {
                "id": connection_id,
                "name": f"TEMP {connection_id}",
                "dialect": "mysql",
                "host": "db.invalid",
                "port": 3306,
                "database": "fixture",
                "username": "fixture-user",
                "group_path": "TEMP/Shutdown",
                "environment": "test",
            }
            for connection_id in ("ready-a", "pending-b", "unknown-c", "unopened-d")
        ]
        catalog = WorkspaceCatalog(
            loader=lambda: [dict(row) for row in rows],
            saver=lambda item, secret=None: dict(item),
            deleter=lambda _connection_id: None,
        )
        hosts = {}
        factory_calls = []

        def host_factory(profile, _language):
            connection_id = str(profile["id"])
            factory_calls.append(connection_id)
            host = _TempWorkspaceHost(
                connection_id,
                pending_worker=connection_id in set(pending),
                unknown=connection_id in set(unknown),
            )
            hosts[connection_id] = host
            return host

        panel = DataCenterPanel("en", catalog=catalog, host_factory=host_factory)
        self.addCleanup(self._cleanup_panel, panel, hosts)
        for connection_id in opened:
            self.assertIsNotNone(panel._open_connection(connection_id))
        return panel, hosts, factory_calls

    def _cleanup_panel(self, panel, hosts):
        for host in hosts.values():
            worker = host._worker
            if worker is not None:
                worker.release.set()
        deadline = time.monotonic() + 2
        while any(
            host._worker is not None and host._worker.isRunning()
            for host in hosts.values()
        ) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        panel.deleteLater()
        self.app.processEvents()

    def _window(self, panel):
        from main_window import MainWindow

        method_names = (
            "closeEvent", "_shutdown", "_manual_panel_refs", "_workbench_panel_refs",
            "_has_live_workbench_worker", "_request_workbench_panel_cleanup",
            "_schedule_workbench_shutdown_poll", "_poll_workbench_panel_cleanup",
            "_manual_status_value", "_collect_manual_shutdown_state",
            "_aggregate_manual_status", "_manual_snapshot_signature", "_manual_can_commit",
            "_manual_summary", "_ask_manual_shutdown_decision", "_set_manual_shutdown_gate",
            "_release_manual_shutdown_gate", "_reset_manual_shutdown_attempt",
            "_advance_manual_shutdown", "_seal_manual_shutdown_state",
            "_start_manual_shutdown_action", "_show_manual_shutdown_wait_status",
            "_schedule_manual_shutdown_poll", "_poll_manual_shutdown_cleanup",
            "_request_agent_panel_cleanup", "_schedule_agent_shutdown_poll",
            "_poll_agent_panel_cleanup",
        )
        harness_type = type(
            "MainWindowExitHarness",
            (),
            {name: getattr(MainWindow, name) for name in method_names},
        )
        window = harness_type()
        window._manual_status_value = MainWindow._manual_status_value
        window.data_center_panel = panel
        window.hotkey_service = Mock()
        window.keep_awake_service = Mock()
        window.quick_panel = Mock()
        window.tray_service = Mock()
        window.interface_debug_panel = Mock()
        window.interface_debug_panel.shutdown_cleanup = Mock()
        window.status_bar = Mock()
        window.language = "en"
        window._settings = {"close_default_action": "exit", "close_ask_each_time": False}
        window._force_exit = False
        window._shutting_down = False
        window._manual_shutdown_pending = False
        window._manual_shutdown_done = False
        window._manual_shutdown_timer_scheduled = False
        window._manual_shutdown_stage = ""
        window._manual_shutdown_action = ""
        window._manual_shutdown_baseline = None
        window._manual_shutdown_gate_active = False
        window._manual_gated_panels = []
        window._manual_shutdown_error = ""
        window._workbench_shutdown_pending = False
        window._workbench_shutdown_done = False
        window._workbench_shutdown_timer_scheduled = False
        window._workbench_shutdown_error = ""
        window._agent_shutdown_pending = False
        window._agent_cleanup_done = False
        window._agent_shutdown_timer_scheduled = False
        window._agent_shutdown_error = ""
        window._shutdown_finalized = False
        return window

    def test_main_exit_retains_workspace_until_every_host_finishes_then_quits_once(self):
        panel, hosts, factory_calls = self._make_panel(
            opened=("pending-b", "ready-a"),
            pending=("pending-b",),
        )
        for host in hosts.values():
            if host._worker is not None:
                host._worker.start()
                self.assertTrue(host._worker.entered.wait(1))
        window = self._window(panel)
        scheduled = []
        fake_app = Mock()
        resumed_events = []

        def close_again():
            event = _CloseEvent()
            window._shutdown(event)
            resumed_events.append(event)

        window.close = close_again
        initial_workspaces = dict(panel._workspaces)
        event = _CloseEvent()
        with patch("main_window.QApplication.instance", return_value=fake_app), patch(
            "main_window.QTimer.singleShot",
            side_effect=lambda delay, callback: scheduled.append((delay, callback)),
        ):
            window._shutdown(event)

            self.assertTrue(event.ignored)
            self.assertTrue(window._workbench_shutdown_pending)
            self.assertIs(window.data_center_panel, panel)
            self.assertEqual(panel._workspaces, initial_workspaces)
            self.assertEqual(factory_calls, ["pending-b", "ready-a"])
            self.assertEqual(hosts["pending-b"].workbench_calls, 1)
            self.assertEqual(hosts["ready-a"].workbench_calls, 1)
            self.assertEqual([host.agent_calls for host in hosts.values()], [0, 0])
            self.assertEqual(fake_app.quit.call_count, 0)

            hosts["pending-b"]._worker.release.set()
            self._wait_for_threads_to_stop((hosts["pending-b"],))
            window._poll_workbench_panel_cleanup()
            self.assertTrue(window._workbench_shutdown_done)
            self.assertEqual(fake_app.quit.call_count, 0)
            self.assertEqual([host.agent_calls for host in hosts.values()], [0, 0])
            self.assertEqual(scheduled[-1][0], 0)
            scheduled[-1][1]()

            self.assertEqual(len(resumed_events), 1)
            self.assertTrue(resumed_events[0].accepted)
            self.assertEqual([host.agent_calls for host in hosts.values()], [1, 1])
            self.assertEqual(fake_app.quit.call_count, 1)
            self.assertIs(window.data_center_panel, panel)
            self.assertEqual(panel._workspaces, initial_workspaces)

            repeated = _CloseEvent()
            window._shutdown(repeated)
            self.assertTrue(repeated.accepted)
            self.assertEqual(fake_app.quit.call_count, 1)

    def _wait_for_threads_to_stop(self, hosts, timeout=2):
        deadline = time.monotonic() + timeout
        while any(
            host._worker is not None and host._worker.isRunning()
            for host in hosts
        ) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertFalse(any(
            host._worker is not None and host._worker.isRunning()
            for host in hosts
        ), "TEMP QThread did not finish after release")

    def test_cancelled_unknown_decision_preserves_host_and_never_starts_workbench_or_agent_cleanup(self):
        panel, hosts, _factory_calls = self._make_panel(
            opened=("unknown-c",),
            pending=("unknown-c",),
            unknown=("unknown-c",),
        )
        host = hosts["unknown-c"]
        host._worker.start()
        self.assertTrue(host._worker.entered.wait(1))
        window = self._window(panel)
        window._ask_manual_shutdown_decision = Mock(return_value=None)
        fake_app = Mock()
        event = _CloseEvent()

        with patch("main_window.QApplication.instance", return_value=fake_app):
            window._shutdown(event)

        self.assertTrue(event.ignored)
        self.assertIs(window.data_center_panel, panel)
        self.assertIn("unknown-c", panel._workspaces)
        self.assertTrue(host._worker.isRunning())
        self.assertGreaterEqual(host.manual_calls.count(None), 1)
        self.assertEqual(host.workbench_calls, 0)
        self.assertEqual(host.agent_calls, 0)
        self.assertEqual(fake_app.quit.call_count, 0)
        self.assertFalse(window._manual_shutdown_gate_active)

    def test_tray_hide_skips_manual_workbench_and_agent_cleanup(self):
        panel, hosts, _factory_calls = self._make_panel(opened=("ready-a",))
        host = hosts["ready-a"]
        window = self._window(panel)
        window._settings["close_default_action"] = "minimize"
        window.hide = Mock()
        event = _CloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.ignored)
        window.hide.assert_called_once_with()
        self.assertEqual(host.manual_calls, [])
        self.assertEqual(host.workbench_calls, 0)
        self.assertEqual(host.agent_calls, 0)


if __name__ == "__main__":
    unittest.main()
