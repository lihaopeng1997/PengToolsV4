# -*- coding: utf-8 -*-
"""Safe application-exit handoff for already-created Agent panels."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication


class _CloseEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class _AgentPanel:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def agent_shutdown_cleanup(self):
        self.calls += 1
        if self.outcomes:
            self.last = self.outcomes.pop(0)
        return self.last


class AgentApplicationShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _window(self, panel):
        from main_window import MainWindow

        harness_type = type('MainWindowHarness', (), {
            '_shutdown': MainWindow._shutdown,
            'closeEvent': MainWindow.closeEvent,
            '_manual_panel_refs': MainWindow._manual_panel_refs,
            '_workbench_panel_refs': MainWindow._workbench_panel_refs,
            '_request_workbench_panel_cleanup': MainWindow._request_workbench_panel_cleanup,
            '_schedule_workbench_shutdown_poll': MainWindow._schedule_workbench_shutdown_poll,
            '_poll_workbench_panel_cleanup': MainWindow._poll_workbench_panel_cleanup,
            '_manual_status_value': MainWindow._manual_status_value,
            '_collect_manual_shutdown_state': MainWindow._collect_manual_shutdown_state,
            '_aggregate_manual_status': MainWindow._aggregate_manual_status,
            '_manual_snapshot_signature': MainWindow._manual_snapshot_signature,
            '_manual_can_commit': MainWindow._manual_can_commit,
            '_manual_summary': MainWindow._manual_summary,
            '_ask_manual_shutdown_decision': MainWindow._ask_manual_shutdown_decision,
            '_set_manual_shutdown_gate': MainWindow._set_manual_shutdown_gate,
            '_release_manual_shutdown_gate': MainWindow._release_manual_shutdown_gate,
            '_reset_manual_shutdown_attempt': MainWindow._reset_manual_shutdown_attempt,
            '_advance_manual_shutdown': MainWindow._advance_manual_shutdown,
            '_seal_manual_shutdown_state': MainWindow._seal_manual_shutdown_state,
            '_start_manual_shutdown_action': MainWindow._start_manual_shutdown_action,
            '_show_manual_shutdown_wait_status': MainWindow._show_manual_shutdown_wait_status,
            '_schedule_manual_shutdown_poll': MainWindow._schedule_manual_shutdown_poll,
            '_poll_manual_shutdown_cleanup': MainWindow._poll_manual_shutdown_cleanup,
            '_request_agent_panel_cleanup': MainWindow._request_agent_panel_cleanup,
            '_schedule_agent_shutdown_poll': MainWindow._schedule_agent_shutdown_poll,
            '_poll_agent_panel_cleanup': MainWindow._poll_agent_panel_cleanup,
        })
        window = harness_type()
        window.agent_workbench_panel = panel
        window.hotkey_service = Mock()
        window.keep_awake_service = Mock()
        window.quick_panel = Mock()
        window.tray_service = Mock()
        window.interface_debug_panel = Mock()
        window.interface_debug_panel.shutdown_cleanup = Mock()
        window.status_bar = Mock()
        window.language = 'zh'
        window._force_exit = False
        window._shutting_down = False
        window._manual_shutdown_pending = False
        window._manual_shutdown_done = False
        window._manual_shutdown_timer_scheduled = False
        window._manual_shutdown_stage = ''
        window._manual_shutdown_action = ''
        window._manual_shutdown_baseline = None
        window._manual_shutdown_gate_active = False
        window._manual_gated_panels = []
        window._manual_shutdown_error = ''
        window._workbench_shutdown_pending = False
        window._workbench_shutdown_done = False
        window._workbench_shutdown_timer_scheduled = False
        window._workbench_shutdown_error = ''
        window._agent_shutdown_pending = False
        window._agent_cleanup_done = False
        window._agent_shutdown_timer_scheduled = False
        window._shutdown_finalized = False
        window._agent_shutdown_error = ''
        return window

    def test_exit_waits_for_agent_cleanup_and_runs_original_chain_once(self):
        from main_window import MainWindow

        panel = _AgentPanel([False, False, True])
        window = self._window(panel)
        scheduled = []
        fake_app = Mock()
        window.close = Mock()

        with patch('main_window.QApplication.instance', return_value=fake_app), \
                patch('main_window.QTimer.singleShot', side_effect=lambda ms, cb: scheduled.append((ms, cb))):
            first = _CloseEvent()
            window._shutdown(first)
            self.assertTrue(first.ignored)
            self.assertFalse(first.accepted)
            self.assertEqual(panel.calls, 1)
            self.assertEqual(window.hotkey_service.unregister.call_count, 0)
            self.assertEqual(window.quick_panel.shutdown.call_count, 0)
            self.assertEqual(fake_app.quit.call_count, 0)
            self.assertEqual(len(scheduled), 1)

            duplicate = _CloseEvent()
            window._shutdown(duplicate)
            self.assertTrue(duplicate.ignored)
            self.assertEqual(panel.calls, 2)
            self.assertEqual(len(scheduled), 1)

            window._poll_agent_panel_cleanup()
            self.assertEqual(panel.calls, 3)
            self.assertEqual(window.close.call_count, 0)
            self.assertEqual(scheduled[-1][0], 0)
            scheduled[-1][1]()
            self.assertEqual(window.close.call_count, 1)

            final_event = _CloseEvent()
            window._shutdown(final_event)
            self.assertTrue(final_event.accepted)
            self.assertEqual(window.hotkey_service.unregister.call_count, 1)
            self.assertEqual(window.keep_awake_service.stop.call_count, 1)
            self.assertEqual(window.interface_debug_panel.shutdown_cleanup.call_count, 1)
            self.assertEqual(window.quick_panel.shutdown.call_count, 1)
            self.assertEqual(window.tray_service.hide.call_count, 1)
            self.assertEqual(fake_app.quit.call_count, 1)

            repeated = _CloseEvent()
            window._shutdown(repeated)
            self.assertTrue(repeated.accepted)
            self.assertEqual(fake_app.quit.call_count, 1)

    def test_minimize_to_tray_does_not_request_agent_cleanup(self):
        from main_window import MainWindow

        panel = _AgentPanel([False])
        window = self._window(panel)
        window._settings = {'close_default_action': 'minimize', 'close_ask_each_time': False}
        window.hide = Mock()
        event = _CloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.ignored)
        self.assertEqual(panel.calls, 0)
        window.hide.assert_called_once_with()

    def test_cleanup_error_keeps_diagnostic_private_and_retries(self):
        from main_window import MainWindow

        class _RaisingPanel:
            def agent_shutdown_cleanup(self):
                raise RuntimeError('private test detail')

        window = self._window(_RaisingPanel())
        scheduled = []
        event = _CloseEvent()
        with patch('main_window.QTimer.singleShot', side_effect=lambda ms, cb: scheduled.append((ms, cb))):
            window._shutdown(event)

        self.assertTrue(event.ignored)
        self.assertEqual(window._agent_shutdown_error, 'agent_workbench_panel: RuntimeError')
        message = window.status_bar.showMessage.call_args[0][0]
        self.assertEqual(message, '停止 Agent 时遇到问题，正在重试…')
        self.assertNotIn('agent_workbench_panel', message)
        self.assertNotIn('RuntimeError', message)
        self.assertEqual(len(scheduled), 1)


if __name__ == '__main__':
    unittest.main()
