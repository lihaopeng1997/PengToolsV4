from __future__ import annotations

import os
from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from tools.data_center.manual_session import ManualScriptEntry, ManualScriptResult
from ui.data_center.manual_query import _FutureDelivery
from ui.data_center.query_history_service import DataCenterHistoryService, make_history_capture
from ui.data_center.result_view import ResultTableView
from tests import test_data_center_query_page_size as fixture_module


class ManualScriptUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_module.QueryPageSizeTests.setUpClass()

    def setUp(self):
        self.fixture = fixture_module.QueryPageSizeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture._new_tab("script-ui")
        self.host, self.panel = self.fixture.host, self.fixture.panel
        self.host.result_view = ResultTableView(self.panel)
        self.host.result_view.setModel(self.host.result_model)
        self.panel._middle_pane = QWidget(self.panel)
        layout = QVBoxLayout(self.panel._middle_pane)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.panel.run_btn)
        layout.addLayout(toolbar)
        self.host._install_transaction_actions()
        self.text = "SELECT first; UPDATE t SET value=1 WHERE id=1; SELECT value FROM t;"
        self.fixture.editor.toPlainText = lambda: self.text
        self.driver = self.fixture.drivers["script-ui"]
        self.driver.statements["SELECT first"] = {"rows": [(i,) for i in range(100)]}

    def _deliver_latest(self):
        state, operation, future = self.fixture.watched[-1]
        result = future.result(timeout=3)
        self.host._deliver(_FutureDelivery(state.target.tab_id, state.generation, state.request_id, operation, result))
        return result

    def test_actual_run_all_button_reviews_count_and_switches_query_results_without_replay(self):
        self.host.query_page_size.setCurrentIndex(self.host.query_page_size.findData(50))
        with patch("ui.confirm_dialog.confirm_action", return_value=True) as review:
            self.host.run_all_btn.click()
        self.assertIn("3", review.call_args.args[2])
        self.assertTrue(self.state.busy)
        result = self._deliver_latest()
        self.assertEqual(result.completed_count, 3)
        self.assertFalse(self.host.script_results.isHidden())
        self.assertEqual(self.host.script_results.count(), 3)
        self.assertEqual(self.host.result_model.rowCount(), 50)
        self.assertTrue(self.panel.next_btn.isEnabled())
        before = list(self.driver.events)
        self.host.script_results.setCurrentIndex(1)
        self.assertIn("影响 1 行", self.panel.result_status.text())
        self.host.script_results.setCurrentIndex(0)
        self.assertEqual(self.host.result_model.rowCount(), 50)
        self.assertFalse(self.panel.next_btn.isEnabled())
        self.assertFalse(self.host._capture_export_context(self.state).full_query_available)
        self.host.fetch_next_page()
        self.assertEqual(self.driver.events, before)
        self.host.script_results.setCurrentIndex(2)
        self.panel.next_btn.click()
        self._deliver_latest()
        self.assertEqual(self.host.result_model.rowCount(), 100)
        self.fixture._new_tab("other")
        self.fixture.tab = self.state.tab
        self.host._visible_tab_changed()
        self.assertEqual(self.host.result_model.rowCount(), 100)
        self.assertEqual(self.host.script_results.currentIndex(), 2)
        self.assertEqual(len([event for event in self.driver.events if event[0] == "execute"]), 3)
        self.assertEqual((self.driver.commit_count, self.driver.rollback_count), (0, 0))

    def test_cancelled_review_invalid_script_and_staged_changes_never_execute(self):
        with patch("ui.confirm_dialog.confirm_action", return_value=False):
            self.host.run_all_btn.click()
        self.assertEqual(self.fixture.factories, [])
        self.text = "SELECT first; COMMIT;"
        self.host.run_all_btn.click()
        self.assertEqual(self.fixture.factories, [])
        self.text = "SELECT first"
        self.state.table_model = Mock(has_staged_changes=True)
        with patch("ui.confirm_dialog.confirm_action", return_value=True) as review:
            self.host.run_all_current()
        review.assert_not_called()
        self.assertEqual(self.fixture.factories, [])
        self.state.table_model = None

    def test_valid_dormant_history_script_can_use_run_all_without_auto_connect(self):
        self.fixture.tab = SimpleNamespace(_history_target_locked=True, _history_target_valid=True)
        self.panel._set_history_draft_status = Mock()
        self.host._visible_tab_changed()
        self.assertTrue(self.host.run_all_btn.isEnabled())
        self.assertEqual(self.fixture.factories, [])
        self.fixture.tab._history_target_valid = False
        self.host._update_actions(None)
        self.assertFalse(self.host.run_all_btn.isEnabled())

    def test_background_failure_restores_completed_failed_and_skipped_results(self):
        self.driver.statements["UPDATE t SET value=1 WHERE id=1"] = {"error": RuntimeError("synthetic failure")}
        with patch("ui.confirm_dialog.confirm_action", return_value=True):
            self.host.run_all_btn.click()
        self.fixture._new_tab("background-other")
        result = self._deliver_latest()
        self.assertEqual(result.completed_count, 1)
        self.assertTrue(self.host.script_results.isHidden())
        self.fixture.tab = self.state.tab
        self.host._visible_tab_changed()
        self.assertIn("失败", self.host.script_results.itemText(1))
        self.assertIn("未执行", self.host.script_results.itemText(2))
        self.host.script_results.setCurrentIndex(0)
        self.assertEqual(self.host.result_model.rowCount(), 100)
        self.assertFalse(self.panel.next_btn.isEnabled())
        self.assertEqual(len([event for event in self.driver.events if event[0] == "execute"]), 2)

    def test_script_history_reducer_records_partial_failure_and_cancellation(self):
        service = DataCenterHistoryService(store=Mock(), parent=self.panel)
        service._get_persistence_controller = Mock()
        service.save_capture = Mock()
        for error, expected in (("", "COMPLETED"), ("STATEMENT_EXECUTION_FAILED", "FAILED"), ("CANCELLED", "CANCELLED")):
            capture = make_history_capture("SELECT first; SELECT later", connection_id="temp", profile_revision="rev", runtime_tab_id="script-ui", generation=1)
            future = Future()
            service.track_manual_future(future, capture, target_tab_id="script-ui", generation=1)
            future.set_result(ManualScriptResult("script-result", "script-ui", 1, (ManualScriptEntry("SELECT first", error_code=error),), error))
            self.fixture.app.processEvents()
            service.save_capture.assert_called_with(capture, expected, row_count=None)
        self.assertEqual(service._manual_pending, {})


if __name__ == "__main__":
    unittest.main()
