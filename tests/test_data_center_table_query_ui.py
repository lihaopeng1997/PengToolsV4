from __future__ import annotations

import os
from concurrent.futures import Future
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from tools.data_center.manual_session import ManualColumn, ManualQueryPage, ManualSessionState, ManualSessionTarget
from tools.data_center.object_tree import TreeTarget, VerifiedTableRef
from tools.data_center.profile_host import ConnectionChoice
from tools.data_center.table_source import TableColumnMetadata, TableFilter, TableOrder, TableQuerySpec, TableSourceMetadata, TableSourceOpenResult
from ui.data_center.manual_query import ManualQueryHost, _FutureDelivery, _TabState
from ui.data_center.result_model import ManualResultTableModel
from ui.data_center.table_editor import TableEditorModel, TableEditorWidget
from ui.data_center.table_query_dialog import TableQueryDialog


def _metadata():
    target = ManualSessionTarget("ui-tab", "temp", "rev", database="db", schema="db")
    choice = ConnectionChoice("temp", "rev", "mysql", "mysql", "mysql", "db")
    tree_target = TreeTarget("temp", "rev", "mysql", mode="mysql", database="db")
    ref = VerifiedTableRef(tree_target, "synthetic", 1, 0, "view", "items", "db", "")
    columns = tuple(TableColumnMetadata(name, kind, True, False, False, False, comparable, False) for name, kind, comparable in (("id", "integer", True), ("name", "varchar", True), ("amount", "decimal", True), ("payload", "blob", False)))
    return TableSourceMetadata(target, ref, columns, (), False, None, "Read-only view"), choice


class TableQueryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.metadata, self.choice = _metadata()
        self.parent = QWidget()

    def tearDown(self):
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_dialog_restores_typed_conditions_accepts_and_clear_means_default_query(self):
        query = TableQuerySpec((TableFilter("name", "contains", "x%_!"), TableFilter("amount", ">=", Decimal("4.20")), TableFilter("payload", "IS NULL")), (TableOrder("amount", True), TableOrder("id")))
        dialog = TableQueryDialog(self.metadata, query, parent=self.parent)
        self.assertEqual(dialog.query(), query)
        self.assertFalse(dialog.filters.cellWidget(2, 2).isEnabled())
        self.assertEqual(dialog.orders.cellWidget(0, 0).findData("payload"), -1)
        dialog.apply_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog = TableQueryDialog(self.metadata, query, parent=self.parent)
        dialog._clear()
        self.assertEqual(dialog.query(), TableQuerySpec())
        dialog.add_filter()
        dialog.filters.cellWidget(0, 0).setCurrentIndex(2)  # numeric field
        dialog.filters.cellWidget(0, 2).setText("not a number")
        dialog.apply_button.click()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.error.text())

    def test_button_reaches_host_query_and_local_changes_busy_shutdown_block_reload(self):
        panel = self.parent
        panel.language = "zh"
        tab = SimpleNamespace()
        panel._current_tab = lambda: tab
        panel.result_status = Mock()
        host = ManualQueryHost(panel, profile_host=Mock())
        session = Mock()
        session.open_table_source.return_value = Future()
        model = TableEditorModel(self.metadata, self.choice)
        state = _TabState(tab, self.metadata.target, self.choice, session, 1, table_model=model, table_mode_active=True, table_scope=SimpleNamespace(service=object(), tree_target=self.metadata.signed_ref.target, node=object(), tree_generation=1))
        host._states_by_tab[id(tab)] = state
        host.table_editor = TableEditorWidget(parent=panel)
        host.table_editor.bind_model(model)
        host.table_editor.server_query_requested.connect(host.open_table_query)
        host._watch_future = Mock()
        host._update_actions = Mock()
        query = TableQuerySpec((TableFilter("name", "=", "target"),), (TableOrder("id", True),))
        from unittest.mock import patch
        with patch("ui.data_center.table_query_dialog.TableQueryDialog") as dialog_type:
            dialog_type.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog_type.return_value.query.return_value = query
            host.table_editor.server_query_button.click()
            dialog_type.assert_called_once()
        session.open_table_source.assert_called_once()
        call = session.open_table_source.call_args
        self.assertEqual(call.kwargs["query"], query)
        self.assertEqual(call.kwargs["page_size"], 200)
        self.assertIs(call.args[0], state.table_scope.service)
        self.assertEqual(state.table_query, TableQuerySpec())  # Applied spec changes only after successful delivery.
        session.open_table_source.reset_mock()
        state.busy = False
        pending_model = Mock(metadata=self.metadata, has_staged_changes=True)
        state.table_model = pending_model
        host.apply_table_query(query)
        session.open_table_source.assert_not_called()
        pending_model.discard.assert_not_called()
        state.table_model = model
        state.busy = True
        host.apply_table_query(query)
        session.open_table_source.assert_not_called()
        state.busy = False
        host._shutdown_gated = True
        host.apply_table_query(query)
        session.open_table_source.assert_not_called()

    def test_widget_labels_and_control_state_follow_busy_and_shutdown(self):
        editor = TableEditorWidget(parent=self.parent)
        model = TableEditorModel(self.metadata, self.choice)
        editor.bind_model(model)
        self.assertTrue(editor.server_query_button.isEnabled())
        editor.set_server_query_counts(2, 1)
        self.assertIn("2/1", editor.server_query_button.text())
        editor.set_language("en")
        self.assertIn("Filter", editor.server_query_button.text())
        editor.set_operation_state(has_more=True, busy=True)
        self.assertFalse(editor.server_query_button.isEnabled())
        editor.set_operation_state(has_more=True, busy=False)
        editor.set_shutdown_gated(True)
        self.assertFalse(editor.server_query_button.isEnabled())
        editor.set_shutdown_gated(False)
        self.assertTrue(editor.server_query_button.isEnabled())

    def test_success_delivery_tab_switch_and_refresh_keep_applied_query(self):
        panel = self.parent
        panel.language = "zh"
        panel.result_status = Mock()
        first_tab, second_tab = SimpleNamespace(), SimpleNamespace()
        current = [first_tab]
        panel._current_tab = lambda: current[0]
        host = ManualQueryHost(panel, profile_host=Mock())
        host.table_editor = TableEditorWidget(parent=panel)
        host.result_model = ManualResultTableModel(panel)
        host.result_view = Mock()
        host._sync_table_focus_layout = Mock()
        host._set_table_focus_active = Mock()
        host._update_actions = Mock()
        host._refresh_workbench_actions = Mock()
        host._watch_future = Mock()
        session = Mock(state=ManualSessionState.CLEAN)
        scope = SimpleNamespace(service=object(), tree_target=self.metadata.signed_ref.target, node=object(), tree_generation=1)
        first = _TabState(first_tab, self.metadata.target, self.choice, session, 1, request_id="request", busy=True, table_scope=scope)
        host._states_by_tab[id(first_tab)] = first
        columns = tuple(ManualColumn(column.name) for column in self.metadata.columns)
        page = ManualQueryPage("query", first.target.tab_id, 1, columns, ((1, "kept", Decimal("4.2"), None),), 1, 200, False, 100)
        query = TableQuerySpec((TableFilter("name", "=", "kept"),), (TableOrder("id", True),))
        host._deliver(_FutureDelivery(first.target.tab_id, 1, "request", "table_open", TableSourceOpenResult(self.metadata, page, query)))
        self.assertEqual(first.table_query, query)
        self.assertIn("1/1", host.table_editor.server_query_button.text())
        second_target = replace(first.target, tab_id="second")
        second = _TabState(second_tab, second_target, self.choice, Mock(state=ManualSessionState.CLEAN), 1, table_model=TableEditorModel(self.metadata, self.choice), table_mode_active=True)
        host._states_by_tab[id(second_tab)] = second
        current[0] = second_tab
        host._visible_tab_changed()
        self.assertEqual(host.table_editor._server_query_counts, (0, 0))
        current[0] = first_tab
        host._visible_tab_changed()
        self.assertEqual(host.table_editor._server_query_counts, (1, 1))
        host.refresh_table_source()
        self.assertEqual(session.open_table_source.call_args.kwargs["query"], query)


if __name__ == "__main__":
    unittest.main()
