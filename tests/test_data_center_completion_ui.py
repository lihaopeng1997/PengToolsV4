"""Completion on isolated native editors and production directory callbacks."""

import os
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QTextCursor
from PyQt6.QtWidgets import QApplication, QWidget

from panels.data_center_panel import DataCenterPanel
from tools.data_center.completion_cache import CompletionCatalog
from tools.data_center.manual_session import ManualColumn, ManualQueryPage, ManualSessionState
from tools.data_center.object_tree import ObjectTreeService, TreeTarget
from tools.data_center.profile_host import ConnectionChoice
from tools.data_center.table_source import TableSourceOpenResult
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.data_center.manual_query import ManualQueryHost, _FutureDelivery, _TabState
from ui.data_center.result_model import ManualResultTableModel
from ui.sql_editor import SqlEditor
from tests.test_data_center_completion import metadata, ref_for
from tests.test_data_center_table_source import _DirectoryHost


class CompletionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _editor(self, parent=None):
        editor = SqlEditor(parent)
        if parent is None:
            self.widgets.append(editor)
        return editor

    @staticmethod
    def _text(editor, text, *, position=None):
        editor.setPlainText(text)
        cursor = editor.textCursor()
        if position is None:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        else:
            cursor.setPosition(len(text[:position].encode("utf-16-le")) // 2)
        editor.setTextCursor(cursor)

    def _host(self, value):
        panel = QWidget()
        panel.language = "zh"
        panel.manual_query_host = host = ManualQueryHost(panel, profile_host=Mock())
        choice = ConnectionChoice(value.target.connection_id, value.target.profile_revision,
                                  value.signed_ref.target.provider, value.signed_ref.target.mode,
                                  value.signed_ref.target.provider, value.target.database)
        tab = SimpleNamespace(editor=self._editor(panel))
        state = _TabState(tab, value.target, choice, Mock(state=ManualSessionState.DISCONNECTED), 1)
        host._states_by_tab[id(tab)] = state
        panel.current_tab = tab
        panel._current_tab = lambda: panel.current_tab
        host.result_model = ManualResultTableModel(panel)
        host.result_view = QWidget(panel)
        host.table_editor = Mock()
        for name in ("_sync_table_focus_layout", "_set_table_focus_active", "_set_status", "_sync_paging", "_update_actions", "_refresh_workbench_actions"):
            setattr(host, name, Mock())
        return panel, host, state

    def test_emoji_and_quoted_prefix_insert_without_corrupting_neighbouring_sql(self):
        editor = self._editor()
        editor.bind_schema({'dialect': 'mysql', 'objects': [{'name': 'users', 'columns': [{'name': 'name'}]}]})
        source = "-- 😀\nSELECT u.na FROM users u"
        position = source.index("na") + 2
        self._text(editor, source, position=position)
        self.assertEqual(editor._current_token(), ("u.na", position - 4, position))
        self.assertEqual(editor._completion_candidates("u.na"), ["u.`name`"])
        editor._insert_completion("u.`name`")
        self.assertEqual(editor.toPlainText(), "-- 😀\nSELECT u.`name` FROM users u")
        editor.undo()
        self.assertEqual(editor.toPlainText(), source)
        editor.bind_schema({'dialect': 'oracle', 'objects': [{'name': 'Odd Table', 'columns': []}]})
        self._text(editor, "SELECT * FROM Od")
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier, " ")
        editor.keyPressEvent(event)
        self.assertEqual(editor.toPlainText(), "SELECT * FROM Od")
        self.assertEqual(editor._model.stringList(), ['"Odd Table"'])
        self.assertEqual(editor._completer.completionCount(), 1)
        editor._insert_completion('"Odd Table"')
        self.assertEqual(editor.toPlainText(), 'SELECT * FROM "Odd Table"')

    def test_tab_switch_overrides_legacy_snapshot_and_clears_locked_history(self):
        value = metadata(ref_for(), "name")
        panel, host, state = self._host(value)
        self.widgets.append(panel)
        catalog = CompletionCatalog()
        catalog.learn_table(value)
        host.set_completion_catalog(catalog)
        self._text(state.tab.editor, "users.na")
        self.assertEqual(state.tab.editor._completion_candidates("users.na"), ["users.`name`"])
        # This models the preceding legacy currentChanged handler's stale bind.
        state.tab.editor.bind_schema({'objects': [{'name': 'stale'}]})
        host._visible_tab_changed()
        self.assertEqual(state.tab.editor._snapshot['objects'][0]['name'], "users")
        from panels.ai_workbench_panel import AiWorkbenchPanel
        panel.sql_tabs = Mock()
        panel._snapshot = {'objects': [{'name': 'obsolete_scan'}]}
        state.tab.editor._model.setStringList(["obsolete_popup"])
        AiWorkbenchPanel._bind_editors_schema(panel)
        self.assertEqual(state.tab.editor._snapshot['objects'][0]['name'], "users")
        self.assertEqual(state.tab.editor._model.stringList(), [])
        second = SimpleNamespace(editor=self._editor(panel))
        other = replace(value.target, tab_id="other", profile_revision="new-rev")
        host._states_by_tab[id(second)] = _TabState(second, other, state.choice, state.session, 1)
        panel.current_tab = second
        host._visible_tab_changed()
        self.assertEqual(second.editor._snapshot['objects'], [])
        second._history_target_locked = True
        panel._set_history_draft_status = Mock()
        second.editor.bind_schema({'objects': [{'name': 'stale'}]})
        host._visible_tab_changed()
        self.assertEqual(second.editor._snapshot, {})
        state.session.assert_not_called()

    def test_real_signed_directory_to_editor_and_table_delivery_without_extra_io(self):
        directory = _DirectoryHost("mysql", "mysql", "sales db", "", "Long\nName", "a_view")
        service = ObjectTreeService(relational_host=directory)
        target = TreeTarget("conn-1", "rev-1", "mysql", mode="mysql", database="sales db")
        root = service.root(target)
        first = service.submit_children(target, root, generation=17).future.result(timeout=3)
        database = first.nodes[0]
        second = service.submit_children(target, database, generation=17).future.result(timeout=3)
        table = next(node for node in second.nodes if node.kind == "table")
        ref = service.resolve_table_source(target, table, generation=17)
        self.assertNotEqual(ref.raw_name, table.name)  # The display name removes the newline.
        value = metadata(ref, "id", "name")
        profile = {'id': 'conn-1', 'name': 'Synthetic', 'dialect': 'mysql', 'mode': 'mysql',
                   'host': 'example.invalid', 'database': 'sales db'}
        parts = []

        def factory(_profile, _language):
            panel, host, state = self._host(value)
            parts.extend((panel, host, state))
            return panel

        panel = DataCenterPanel(catalog=WorkspaceCatalog(loader=lambda: [profile]), host_factory=factory)
        self.widgets.append(panel)
        owner = SimpleNamespace(is_closing=False, target=target, generation=17, capture_pending=False,
                                load_children=Mock(), table_source_service=service)
        panel._tree_owners[target.connection_id] = owner
        try:
            panel._on_tree_root_ready(target.connection_id, target, root, 17)
            panel._on_tree_page_ready(target.connection_id, first, None, 17)
            panel._on_tree_page_ready(target.connection_id, second, None, 17)
            panel._open_connection(target.connection_id)
            _widget, host, state = parts
            self.assertIn("Long\nName", [obj['name'] for obj in state.tab.editor._snapshot['objects']])
            self.assertTrue(all(not obj['columns'] for obj in state.tab.editor._snapshot['objects']))
            before = len(directory.directory_calls)
            state.request_id = "request"
            page = ManualQueryPage("q", value.target.tab_id, 1, tuple(ManualColumn(col.name) for col in value.columns), (), 1, 200, False, 0)
            host._deliver(_FutureDelivery(value.target.tab_id, 1, "request", "table_open", TableSourceOpenResult(value, page)))
            sql = 'SELECT u.na FROM `Long\nName` u'
            self._text(state.tab.editor, sql, position=sql.index("na") + 2)
            self.assertEqual(state.tab.editor._completion_candidates("u.na"), ["u.`name`"])
            state.tab.editor._insert_completion("u.`name`")
            self.assertIn("u.`name` FROM", state.tab.editor.toPlainText())
            self.assertEqual(len(directory.directory_calls), before)
            state.session.execute.assert_not_called()
            panel._on_tree_root_ready(target.connection_id, target, root, 18)
            self.assertEqual(state.tab.editor._snapshot['objects'], [])
            panel._on_tree_page_ready(target.connection_id, second, None, 17)
            self.assertEqual(state.tab.editor._snapshot['objects'], [])
        finally:
            panel._tree_owners.clear()
            service.close().result(timeout=3)


if __name__ == "__main__":
    unittest.main()
