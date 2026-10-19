from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tools.data_center.manual_session import ManualOperationFuture, ManualSessionError, ManualSessionState, ManualSessionTarget
from ui.data_center.manual_query import _FutureDelivery
from ui.data_center.query_history_service import DataCenterHistoryService, make_history_capture
from tests import test_data_center_query_page_size as host_fixture


class ProceduralUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host_fixture.QueryPageSizeTests.setUpClass()

    def setUp(self):
        self.fixture = host_fixture.QueryPageSizeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture._new_tab("block-ui")
        self.host = self.fixture.host
        self.driver = self.fixture.drivers["block-ui"]
        def execute(sql, provider):
            self.driver.events.append(("block", threading.get_ident(), sql, provider))
            return -1
        self.driver.execute_block = execute
        self.driver.block_transaction_state = lambda: ManualSessionState.CLEAN
        self.block = "CREATE PROCEDURE p() BEGIN SELECT 'a;b'; SELECT 2; END"
        self.selection = "DELIMITER $$\n" + self.block + "$$\nDELIMITER ;"
        self.fixture.editor.selected_or_all = lambda: (self.selection, 0)
        self.fixture.editor.toPlainText = lambda: "SELECT 999;\n" + self.selection

    def test_explicit_selection_confirmation_submits_one_complete_block_and_delivers_state(self):
        with patch("ui.confirm_dialog.confirm_action", return_value=True) as review:
            self.host.run_current()
        self.assertIn("整个块", review.call_args.kwargs["confirm_text"])
        state, operation, future = self.fixture.watched[-1]
        result = future.result(timeout=3)
        self.assertEqual(operation, "block")
        self.assertEqual([(event[2], event[3]) for event in self.driver.events if event[0] == "block"], [(self.block, "mysql")])
        self.host._deliver(_FutureDelivery(state.target.tab_id, state.generation, state.request_id, operation, result))
        self.assertEqual(result.state, ManualSessionState.CLEAN)
        self.assertIn("完整块已返回", self.fixture.panel.result_status.text())
        self.assertFalse(self.host.commit_btn.isEnabled())

    def test_declined_confirmation_and_dirty_state_cannot_execute_block(self):
        with patch("ui.confirm_dialog.confirm_action", return_value=False):
            self.host.run_current()
        self.assertEqual(self.fixture.factories, [])
        self.state.session.submit("UPDATE t SET v=1").result(timeout=3)
        with patch("ui.confirm_dialog.confirm_action", return_value=True) as review:
            self.host.run_current()
        review.assert_not_called()
        self.assertFalse(any(event[0] == "block" for event in self.driver.events))

    def test_history_error_identity_is_verified_and_cancel_is_distinct_from_failure(self):
        service = DataCenterHistoryService(store=Mock(), parent=self.fixture.panel)
        service._get_persistence_controller = Mock()
        service.save_capture = Mock()
        capture = make_history_capture(self.block, connection_id="temp", profile_revision="rev", runtime_tab_id="block-ui", generation=1)
        for code, tab_id, connection_id, expected in (("CANCELLED", "block-ui", "temp", "CANCELLED"), ("BLOCK_EXECUTION_FAILED", "block-ui", "temp", "FAILED"), ("CANCELLED", "another-tab", "temp", "UNKNOWN"), ("CANCELLED", "block-ui", "another-connection", "UNKNOWN")):
            future = ManualOperationFuture(ManualSessionTarget(tab_id, connection_id, "rev"))
            service.track_manual_future(future, capture, target_tab_id="block-ui", generation=1)
            future.set_exception(ManualSessionError(code))
            self.fixture.app.processEvents()
            service.save_capture.assert_called_with(capture, expected, row_count=None)
        self.assertEqual(service._manual_pending, {})


if __name__ == "__main__":
    unittest.main()
