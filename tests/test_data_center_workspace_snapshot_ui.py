"""Workspace menu -> serial SQLite owner -> dormant production draft restore."""

from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
import time
from types import MethodType
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bson import ObjectId
from bson.decimal128 import Decimal128
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTabWidget, QWidget

from panels.ai_workbench_panel import AiWorkbenchPanel
from panels.data_center_panel import DataCenterPanel
from tools.data_center.nosql_workspace import capture_mongo_run
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.workspace_catalog import WorkspaceCatalog
from tools.data_center.workspace_store import WorkspaceSnapshot, WorkspaceLayout, WorkspaceStore
from ui.data_center.manual_query import ManualQueryHost
from ui.data_center.nosql_query_history import NoSqlDraftTabs, make_nosql_draft
from ui.data_center.query_history_service import DataCenterHistoryService
from tests.test_data_center_workspace_snapshot import snapshot


def profile(name, provider="mysql"):
    return {'id': name, 'name': name, 'dialect': provider,
            'mode': provider if provider == 'mysql' else 'standalone',
            'host': 'example.invalid', 'username': 'fixture', 'port': 3306,
            'database': 'db', 'db': 0}


class WorkspaceSnapshotUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pengtools-workspace-ui-")
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.sqlite3")
        self.service = DataCenterHistoryService(store=self.store)
        self.profiles = [profile('sql-a'), profile('sql-b'), profile('redis', 'redis'), profile('mongo', 'mongodb')]
        self.profile_host = DataCenterProfileHost(connections_loader=lambda: {'items': list(self.profiles)})
        self.connector = Mock(side_effect=AssertionError("Restoring drafts must not open a database"))
        self.panels = []
        self.sql_hosts = []
        self.release = threading.Event()
        self.release.set()

    def tearDown(self):
        self.release.set()
        sessions = []
        for host in self.sql_hosts:
            for state in host.manual_query_host._states_by_tab.values():
                state.session.close(disposition="rollback").result(timeout=3)
                sessions.append(state.session)
        self.assertTrue(self._wait(lambda: all(session.poll_cleanup().cleanup_complete for session in sessions)))
        self.assertTrue(self._wait(self.service.drain))
        for panel in self.panels:
            try:
                panel.deleteLater()
            except RuntimeError:
                pass
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def _wait(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(.002)
        return bool(predicate())

    def _host(self, value, language):
        host = QWidget()
        host.language = language
        if value['dialect'] == 'mysql':
            host.sql_tabs = QTabWidget(host)
            host._tab_seq = 1
            host._manual_shutdown_gated = False
            host._snapshot = None
            host._browse_conn = lambda: dict(value)
            host._refresh_risk_chip = Mock()
            host._set_history_draft_status = Mock()
            host._history_target_identity = lambda record: record.target.connection_id
            host._new_sql_tab = MethodType(AiWorkbenchPanel._new_sql_tab, host)
            manual = ManualQueryHost(host, profile_host=self.profile_host, connector=self.connector)
            host.manual_query_host = manual
            manual._update_actions = Mock()
            manual._set_status = Mock()
            manual.history_draft_changed = Mock()
            tab = host._new_sql_tab()
            manual._ensure_tab_state(tab)
            self.sql_hosts.append(host)
        else:
            provider = 'redis' if value['dialect'] == 'redis' else 'mongo'
            host.nosql_draft_tabs = NoSqlDraftTabs(provider, parent=host)
            host._nosql_profile_host = self.profile_host
            host._worker = None
            host.query_input = QPlainTextEdit(host)
            host.cmd_input = QLineEdit(host)

            def save(draft):
                if draft is not None:
                    draft.editor_texts['command'] = host.cmd_input.text()
                    if provider == 'mongo':
                        draft.editor_texts['documents'] = host.query_input.toPlainText()

            def load(draft):
                if draft is not None:
                    host.cmd_input.setText(draft.editor_texts.get('command', ''))
                    host.query_input.setPlainText(draft.editor_texts.get('documents', ''))

            host._save_nosql_draft_text = save
            host.nosql_draft_tabs.draft_about_to_change.connect(save)
            host.nosql_draft_tabs.draft_changed.connect(load)
            host.nosql_draft_tabs.add_draft(make_nosql_draft(provider, value, self.profile_host, collection='records'))
        return host

    def _panel(self):
        panel = DataCenterPanel(catalog=WorkspaceCatalog(loader=lambda: list(self.profiles)),
                                host_factory=self._host, workspace_history_service=self.service)
        self.panels.append(panel)
        return panel

    def test_actual_menu_roundtrip_restores_sql_order_nosql_bson_and_preserves_current_edits(self):
        panel = self._panel()
        a = panel._open_connection('sql-a').host
        a.sql_tabs.widget(0).editor.setPlainText("SELECT 'first'")
        second = a._new_sql_tab("SELECT 'second'")
        a.manual_query_host._ensure_tab_state(second)
        a.manual_query_host._states_by_tab[id(second)].busy = True
        a.sql_tabs.tabBar().moveTab(1, 0)
        b = panel._open_connection('sql-b').host
        b.sql_tabs.widget(0).editor.setPlainText("SELECT 'third'")
        redis = panel._open_connection('redis').host
        redis.cmd_input.setText('  GET fixture-key  ')
        redis.nosql_draft_tabs.add_draft(make_nosql_draft('redis', self.profiles[2], self.profile_host))
        redis.cmd_input.setText('GET second-key')
        redis.nosql_draft_tabs.tab_bar.moveTab(1, 0)
        mongo = panel._open_connection('mongo').host
        text = 'db.records.find({"state":"fixture"})'
        mongo.query_input.setPlainText(text)
        mongo.cmd_input.setText('db.stats()')
        draft = mongo.nosql_draft_tabs.current_draft
        identifier = ObjectId('650000000000000000000001')
        draft.snapshot = capture_mongo_run(draft.target, text, text, action='query', mode=draft.mode,
                                           database='db', collection='records', filter={'_id': identifier, 'amount': Decimal128('1.20')},
                                           projection={'amount': 1}, sort=[['amount', -1]], skip=0, limit=20)
        panel.workspace_save_action.trigger()
        self.assertTrue(self._wait(lambda: not panel.workspace_persistence.busy))
        stored = self.store.load_workspace()
        a.manual_query_host._states_by_tab[id(second)].busy = False
        self.assertEqual(len(stored.layout.tabs), 6)
        self.assertEqual([record.text for record in stored.drafts[:2]], ["SELECT 'second'", "SELECT 'first'"])
        self.assertEqual(stored.drafts[0].state_code, 'INTERRUPTED')
        self.assertIn('  GET fixture-key  ', [record.text for record in stored.drafts])
        fresh = self._panel()
        fresh.workspace_restore_action.trigger()
        self.assertTrue(self._wait(lambda: not fresh.workspace_persistence.busy))
        self.assertEqual([fresh.workspace_tabs.widget(index).connection_id for index in range(1, fresh.workspace_tabs.count())],
                         ['sql-a', 'sql-b', 'redis', 'mongo'])
        restored_a = fresh._workspaces['sql-a'].host
        restored = restored_a.sql_tabs.widget(0)
        self.assertEqual(restored.editor.toPlainText(), "SELECT 'second'")
        self.assertTrue(restored._history_target_locked)
        self.assertNotIn(id(restored), restored_a.manual_query_host._states_by_tab)
        self.assertIn('事务状态不会恢复', fresh.workspace_status.text())
        restored_redis = fresh._workspaces['redis'].host.nosql_draft_tabs.drafts()
        self.assertEqual([draft.editor_texts['command'] for draft in restored_redis[:2]], ['GET second-key', '  GET fixture-key  '])
        restored_mongo = fresh._workspaces['mongo'].host.nosql_draft_tabs.current_draft
        args = restored_mongo.snapshot.worker_arguments()
        self.assertEqual(args['filter']['_id'], identifier)
        self.assertEqual(args['filter']['amount'], Decimal128('1.20'))
        self.assertEqual(restored_mongo.editor_texts['command'], 'db.stats()')
        self.assertEqual(getattr(restored_mongo, '_workspace_saved_id'), stored.layout.selected_tab_id)
        restored.editor.setPlainText('SELECT unsaved_change')
        count = restored_a.sql_tabs.count()
        fresh.workspace_restore_action.trigger()
        self.assertTrue(self._wait(lambda: not fresh.workspace_persistence.busy))
        self.assertEqual(restored_a.sql_tabs.count(), count)
        self.assertEqual(restored.editor.toPlainText(), 'SELECT unsaved_change')
        self.connector.assert_not_called()

    def test_changed_profile_stays_locked_and_missing_target_text_remains_copyable(self):
        panel = self._panel()
        host = panel._open_connection('sql-a').host
        host.sql_tabs.widget(0).editor.setPlainText('SELECT original_target')
        panel.workspace_save_action.trigger()
        self.assertTrue(self._wait(lambda: not panel.workspace_persistence.busy))
        saved = self.store.load_workspace()
        missing = snapshot('removed')
        target = replace(missing.layout.tabs[0], connection_id='removed')
        missing = WorkspaceSnapshot(WorkspaceLayout((*saved.layout.tabs, target), target.tab_id),
                                    (*saved.drafts, replace(missing.drafts[0], target=target)))
        self.store.save_workspace(missing)
        self.profiles[0]['host'] = 'changed.invalid'
        fresh = self._panel()
        fresh.workspace_restore_action.trigger()
        self.assertTrue(self._wait(lambda: not fresh.workspace_persistence.busy))
        tab = fresh._workspaces['sql-a'].host.sql_tabs.widget(0)
        self.assertTrue(tab._history_target_locked)
        self.assertFalse(tab._history_target_valid)
        self.assertEqual(tab.editor.toPlainText(), 'SELECT original_target')
        preview = fresh.workspace_persistence._preview
        self.assertIsNotNone(preview)
        self.assertEqual(preview.findChild(QPlainTextEdit).toPlainText(), missing.drafts[-1].text)
        self.assertEqual(self.store.load_workspace(), missing)
        self.connector.assert_not_called()

    def test_blocked_store_owner_keeps_ui_responsive_and_survives_panel_destruction(self):
        entered = threading.Event()
        self.release.clear()
        save = self.store.save_workspace

        def blocked(value):
            entered.set()
            self.release.wait(3)
            save(value)

        self.store.save_workspace = blocked
        panel = self._panel()
        ticks = []
        timer = QTimer()
        timer.setInterval(2)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        panel.workspace_save_action.trigger()
        self.assertTrue(self._wait(lambda: entered.is_set() and len(ticks) >= 4))
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(self.service.drain())
        self.assertFalse(self.service._store_closed)
        self.release.set()
        self.assertTrue(self._wait(self.service.drain))
        timer.stop()
        self.assertTrue(self.service._store_closed)

    def test_credential_in_editor_is_rejected_before_store_thread_or_file(self):
        panel = self._panel()
        host = panel._open_connection('redis').host
        host.cmd_input.setText('AUTH synthetic-value')
        panel.workspace_save_action.trigger()
        self.assertFalse(panel.workspace_persistence.busy)
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.service._controllers)
        self.assertIn('凭据', panel.workspace_status.text())


if __name__ == '__main__':
    unittest.main()
