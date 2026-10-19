"""Fake-host coverage for the application's manual-session shutdown coordinator."""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton
from PyQt6.QtWidgets import QWidget


@dataclass(frozen=True)
class _Session:
    tab_id: str
    generation: int
    state: str
    busy: bool = False
    cleanup_complete: bool = False
    pending_local_changes: int = 0
    owner_kind: str = "manual"


@dataclass(frozen=True)
class _Snapshot:
    sessions: tuple[_Session, ...]
    can_commit: bool


class _ManualOwner:
    def __init__(self, snapshot, preflight=(), action_result="ready"):
        self.snapshot = snapshot
        self.preflight = list(preflight)
        self.action_result = action_result
        self.calls = []
        self.actions = []
        self.gates = []
        self.workbench_results = []

    def manual_shutdown_snapshot(self):
        self.calls.append("snapshot")
        return self.snapshot

    def manual_shutdown_cleanup(self, disposition=None):
        self.calls.append(disposition)
        if disposition is None:
            if self.preflight:
                value = self.preflight.pop(0)
                return value
            return "ready"
        self.actions.append(disposition)
        return self.action_result

    def manual_shutdown_gate(self, enabled):
        self.gates.append(bool(enabled))
        return True

    def workbench_shutdown_cleanup(self):
        if self.workbench_results:
            return self.workbench_results.pop(0)
        return True


class _CloseEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class DataCenterApplicationShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _window(self, **panels):
        from main_window import MainWindow

        methods = (
            "_shutdown", "closeEvent", "_manual_panel_refs", "_workbench_panel_refs",
            "_has_live_workbench_worker", "_request_workbench_panel_cleanup", "_schedule_workbench_shutdown_poll",
            "_poll_workbench_panel_cleanup", "_manual_status_value",
            "_collect_manual_shutdown_state", "_aggregate_manual_status",
            "_manual_snapshot_signature", "_manual_can_commit", "_manual_summary",
            "_ask_manual_shutdown_decision", "_set_manual_shutdown_gate",
            "_release_manual_shutdown_gate", "_reset_manual_shutdown_attempt",
            "_advance_manual_shutdown", "_seal_manual_shutdown_state",
            "_start_manual_shutdown_action", "_show_manual_shutdown_wait_status",
            "_schedule_manual_shutdown_poll", "_poll_manual_shutdown_cleanup",
            "_request_agent_panel_cleanup", "_schedule_agent_shutdown_poll",
            "_poll_agent_panel_cleanup",
        )
        harness_type = type("MainWindowHarness", (), {name: getattr(MainWindow, name) for name in methods})
        window = harness_type()
        # Preserve the production staticmethod descriptor when building a
        # lightweight instance harness; assigning the raw function as a class
        # attribute would bind an extra `self` argument.
        window._manual_status_value = MainWindow._manual_status_value
        for name, value in panels.items():
            setattr(window, name, value)
        window.hotkey_service = Mock()
        window.keep_awake_service = Mock()
        window.quick_panel = Mock()
        window.tray_service = Mock()
        window.interface_debug_panel = Mock()
        window.interface_debug_panel.shutdown_cleanup = Mock()
        window.status_bar = Mock()
        window.language = "zh"
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
        window._shutdown_finalized = False
        window._agent_shutdown_error = ""
        return window

    def test_mixed_clean_and_unknown_cancel_performs_no_action_or_agent_cleanup(self):
        class Worker:
            def __init__(self):
                self.running = True

            def isRunning(self):
                return self.running

        clean = _ManualOwner(
            _Snapshot((_Session("clean-tab", 1, "CLEAN"),), can_commit=True),
            preflight=("ready_to_close",),
        )
        clean._worker = Worker()
        unknown = _ManualOwner(
            _Snapshot((_Session("unknown-tab", 2, "UNKNOWN"),), can_commit=False),
            preflight=("decision_required",),
        )
        agent = Mock()
        agent.agent_shutdown_cleanup = Mock(return_value=True)
        window = self._window(db_panel_0=clean, db_panel_1=unknown, agent_workbench_panel=agent)
        window._ask_manual_shutdown_decision = Mock(return_value=None)
        event = _CloseEvent()

        window._shutdown(event)

        self.assertTrue(event.ignored)
        self.assertEqual(clean.actions, [])
        self.assertEqual(unknown.actions, [])
        self.assertEqual(clean.gates, [])
        self.assertEqual(unknown.gates, [])
        self.assertEqual(agent.agent_shutdown_cleanup.call_count, 0)
        self.assertTrue(clean._worker.isRunning())
        self.assertFalse(window._manual_shutdown_pending)
        self.assertFalse(window._shutting_down)
        self.assertFalse(window._force_exit)

    def test_unknown_disables_commit_and_even_forced_commit_is_rejected_before_io(self):
        owner = _ManualOwner(
            _Snapshot((_Session("unknown-tab", 1, "UNKNOWN"),), can_commit=False),
            preflight=("decision_required", "decision_required"),
        )
        window = self._window(db_panel_0=owner)
        window._ask_manual_shutdown_decision = Mock(return_value="commit")

        window._shutdown(_CloseEvent())

        self.assertEqual(owner.actions, [])
        self.assertEqual(owner.gates, [True, False])
        self.assertFalse(window._manual_shutdown_pending)
        self.assertFalse(window._manual_shutdown_gate_active)
        self.assertIn("未知", window.status_bar.showMessage.call_args[0][0])

    def test_unknown_decision_dialog_is_compact_and_commit_button_is_disabled(self):
        from main_window import MainWindow

        owner = _ManualOwner(_Snapshot((
            _Session("unknown-import", 1, "UNKNOWN", owner_kind="import"),
            _Session("staged-tab", 2, "DIRTY", pending_local_changes=3),
        ), can_commit=False))
        dialog_harness_type = type("DecisionDialogHarness", (QWidget,), {
            "_manual_can_commit": MainWindow._manual_can_commit,
            "_manual_summary": MainWindow._manual_summary,
            "_ask_manual_shutdown_decision": MainWindow._ask_manual_shutdown_decision,
        })
        window = dialog_harness_type()
        window.language = "en"
        captured = {}

        def fake_exec(dialog):
            captured["width"] = dialog.width()
            captured["buttons"] = {
                button.text(): button.isEnabled()
                for button in dialog.findChildren(QPushButton)
            }
            captured["text"] = dialog.findChild(QLabel, "manual-shutdown-decision-summary").text()
            label = dialog.findChild(QLabel, "manual-shutdown-decision-summary")
            captured["wraps"] = label.wordWrap()
            captured["modal"] = dialog.isModal()
            captured["default_cancel"] = dialog.findChild(QPushButton, "manual-shutdown-cancel").isDefault()
            captured["minimum_width"] = dialog.minimumWidth()
            captured["maximum_width"] = dialog.maximumWidth()
            rollback = dialog.findChild(QPushButton, "manual-shutdown-rollback")
            captured["rollback_text"] = rollback.text()
            captured["rollback_tooltip"] = rollback.toolTip()

        with patch.object(QDialog, "exec", new=fake_exec):
            selected = window._ask_manual_shutdown_decision([
                ("db_panel_0", owner, owner.snapshot, "decision_required")
            ])

        self.assertIsNone(selected)
        self.assertLessEqual(captured["width"], 460)
        self.assertFalse(captured["buttons"]["Commit and exit"])
        self.assertTrue(captured["buttons"]["Roll back / acknowledge"])
        self.assertTrue(captured["buttons"]["Cancel exit"])
        self.assertTrue(captured["wraps"])
        self.assertTrue(captured["modal"])
        self.assertTrue(captured["default_cancel"])
        self.assertLessEqual(captured["minimum_width"], 420)
        self.assertLessEqual(captured["maximum_width"], 520)
        self.assertIn("does not prove", captured["text"])
        self.assertIn("An UNKNOWN import is acknowledged and released", captured["text"])
        self.assertIn("no additional rollback", captured["text"])
        self.assertIn("additional rollback is sent", captured["rollback_tooltip"])
        self.assertIn("3 table edit(s) are local and unsaved", captured["text"])
        self.assertIn("will not be applied automatically and block commit", captured["text"])

    def test_pending_local_count_changes_shutdown_signature_and_blocks_commit(self):
        from main_window import MainWindow

        owner = _ManualOwner(_Snapshot((
            _Session("local-tab", 4, "CLEAN", pending_local_changes=1),
        ), can_commit=False))
        window = self._window()
        entry = ("db_panel_0", owner, owner.snapshot, "decision_required")
        before = window._manual_snapshot_signature([entry])
        changed = _Snapshot((
            _Session("local-tab", 4, "CLEAN", pending_local_changes=2),
        ), can_commit=False)
        after = window._manual_snapshot_signature([
            ("db_panel_0", owner, changed, "decision_required")
        ])
        self.assertNotEqual(before, after)
        self.assertFalse(window._manual_can_commit([entry]))

    def test_pending_clean_close_keeps_owner_reference_and_runs_quit_chain_once(self):
        owner = _ManualOwner(
            _Snapshot((_Session("clean-tab", 1, "CLEAN"),), can_commit=True),
            preflight=("ready_to_close", "ready_to_close"),
            action_result="pending",
        )
        window = self._window(db_panel_0=owner)
        window.close = Mock()
        scheduled = []
        fake_app = Mock()
        event = _CloseEvent()

        with patch("main_window.QApplication.instance", return_value=fake_app), patch(
            "main_window.QTimer.singleShot", side_effect=lambda ms, callback: scheduled.append((ms, callback))
        ):
            window._shutdown(event)

            self.assertTrue(event.ignored)
            self.assertEqual(owner.actions, ["close"])
            self.assertIs(window.db_panel_0, owner)
            self.assertTrue(window._manual_shutdown_gate_active)
            self.assertEqual(len(scheduled), 1)

            window._poll_manual_shutdown_cleanup()
            self.assertEqual(owner.actions, ["close"])
            self.assertEqual(window.close.call_count, 0)
            self.assertEqual(scheduled[-1][0], 0)
            scheduled[-1][1]()
            self.assertEqual(window.close.call_count, 1)

            final = _CloseEvent()
            window._shutdown(final)
            self.assertTrue(final.accepted)
            self.assertEqual(fake_app.quit.call_count, 1)
            repeated = _CloseEvent()
            window._shutdown(repeated)
            self.assertTrue(repeated.accepted)
            self.assertEqual(fake_app.quit.call_count, 1)

    def test_close_action_requires_ready_and_rejects_nonterminal_statuses(self):
        for action_result in ("ready_to_close", "unexpected-status"):
            with self.subTest(action_result=action_result):
                owner = _ManualOwner(
                    _Snapshot((_Session("clean-tab", 1, "CLEAN"),), can_commit=True),
                    preflight=("ready_to_close", "ready_to_close"),
                    action_result=action_result,
                )
                agent = Mock()
                agent.agent_shutdown_cleanup = Mock(return_value=True)
                window = self._window(db_panel_0=owner, agent_workbench_panel=agent)
                window.close = Mock()
                scheduled = []
                event = _CloseEvent()
                with patch("main_window.QApplication.instance", return_value=Mock()), patch(
                    "main_window.QTimer.singleShot", side_effect=lambda ms, callback: scheduled.append((ms, callback))
                ):
                    window._shutdown(event)

                self.assertTrue(event.ignored)
                self.assertEqual(owner.actions, ["close"])
                self.assertIs(window.db_panel_0, owner)
                self.assertEqual(window.close.call_count, 0)
                self.assertEqual(agent.agent_shutdown_cleanup.call_count, 0)
                if action_result == "ready_to_close":
                    self.assertTrue(window._manual_shutdown_pending)
                    self.assertTrue(window._manual_shutdown_gate_active)
                    self.assertEqual(len(scheduled), 1)
                else:
                    self.assertFalse(window._manual_shutdown_pending)
                    self.assertFalse(window._manual_shutdown_gate_active)
                    self.assertTrue(window._manual_shutdown_error)

    def test_legacy_workbench_worker_reference_blocks_exit_until_finished(self):
        class Worker:
            running = True

            def isRunning(self):
                return self.running

        class LegacyPanel:
            def __init__(self):
                self._worker = Worker()

        panel = LegacyPanel()
        window = self._window(sql_panel=panel)
        window.close = Mock()
        scheduled = []
        fake_app = Mock()
        event = _CloseEvent()

        with patch("main_window.QApplication.instance", return_value=fake_app), patch(
            "main_window.QTimer.singleShot", side_effect=lambda ms, callback: scheduled.append((ms, callback))
        ):
            window._shutdown(event)
            self.assertTrue(event.ignored)
            self.assertIs(window.sql_panel, panel)
            self.assertTrue(window._workbench_shutdown_pending)
            self.assertEqual(len(scheduled), 1)

            panel._worker.running = False
            window._poll_workbench_panel_cleanup()
            self.assertTrue(window._workbench_shutdown_done)
            self.assertEqual(window.close.call_count, 0)
            self.assertEqual(scheduled[-1][0], 0)
            scheduled[-1][1]()
            self.assertEqual(window.close.call_count, 1)

            final = _CloseEvent()
            window._shutdown(final)
            self.assertTrue(final.accepted)
            self.assertEqual(fake_app.quit.call_count, 1)
            window._shutdown(_CloseEvent())
            self.assertEqual(fake_app.quit.call_count, 1)

    def test_tray_hide_does_not_preflight_or_gate_manual_owners(self):
        owner = _ManualOwner(
            _Snapshot((_Session("dirty-tab", 1, "DIRTY"),), can_commit=True)
        )
        window = self._window(db_panel_0=owner)
        window._settings["close_default_action"] = "minimize"
        window.hide = Mock()
        event = _CloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.ignored)
        window.hide.assert_called_once_with()
        self.assertEqual(owner.calls, [])
        self.assertEqual(owner.gates, [])

    def test_shutdown_wait_guard_does_not_construct_or_switch_pages(self):
        from main_window import MainWindow

        harness_type = type("NavigationShutdownHarness", (), {
            "_show_panel": MainWindow._show_panel,
        })
        window = harness_type()
        window._shutting_down = True
        window._shutdown_finalized = False
        window._ensure_panel_for_nav = Mock()

        self.assertFalse(window._show_panel(14))
        window._ensure_panel_for_nav.assert_not_called()


if __name__ == "__main__":
    unittest.main()
