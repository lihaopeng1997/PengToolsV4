"""Paging callbacks with isolated widgets and real ManualSession owners."""

from __future__ import annotations

import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from tools.data_center.manual_session import ManualSessionState, ManualSessionTarget
from tools.data_center.profile_host import ConnectionChoice
from ui.data_center.manual_query import ManualQueryHost
from ui.data_center.result_model import ManualResultTableModel
from tests.test_data_center_manual_session import _FakeDriver


class QueryPageSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = QWidget()
        self.panel.language = "zh"
        self.panel.next_btn = QPushButton("Next", self.panel)
        self.panel.all_btn = QPushButton("All", self.panel)
        self.panel.run_btn = QPushButton("Run", self.panel)
        self.panel.result_status = QLabel(self.panel)
        layout = QVBoxLayout(self.panel)
        self.pager = QHBoxLayout()
        self.pager.addWidget(self.panel.next_btn)
        self.pager.addWidget(self.panel.all_btn)
        self.pager.addWidget(self.panel.result_status)
        layout.addLayout(self.pager)
        self.tab = None
        self.panel._current_tab = lambda: self.tab
        self.editor = SimpleNamespace(
            selected_or_all=lambda: ("SELECT value FROM t", 0),
            toPlainText=lambda: "SELECT value FROM t",
        )
        self.panel._current_editor = lambda: self.editor
        self.host = ManualQueryHost(self.panel, profile_host=Mock())
        self.host.commit_btn = QPushButton(self.panel)
        self.host.rollback_btn = QPushButton(self.panel)
        self.host.stop_btn = QPushButton(self.panel)
        self.host.result_view = QWidget(self.panel)
        self.host.result_model = ManualResultTableModel(self.panel)
        self.host.table_editor = Mock()
        self.host._sync_table_focus_layout = Mock()
        self.host._set_table_focus_active = Mock()
        self.host._install_page_size_control()
        self.watched = []
        self.host._watch_future = lambda state, operation, future, *args, **kwargs: self.watched.append((state, operation, future))
        self.choice = ConnectionChoice("temp", "rev", "mysql", "mysql", "mysql", "db")
        self.drivers = {}
        self.factories = []

        def provider_factory(*args, **kwargs):
            def connect(target):
                self.factories.append((target, threading.get_ident()))
                return self.drivers[target.tab_id]
            return connect

        self.factory_patch = patch("ui.data_center.manual_query.ManualRelationalProviderFactory", side_effect=provider_factory)
        self.factory_patch.start()
        self.host._capture_target = lambda tab: (self.choice, tab.target)

    def tearDown(self):
        sessions = [state.session for state in self.host._states_by_tab.values()]
        for session in sessions:
            session.close(disposition="rollback").result(timeout=3)
        deadline = time.monotonic() + 3
        while any(not session.poll_cleanup().cleanup_complete for session in sessions) and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertTrue(all(session.poll_cleanup().cleanup_complete for session in sessions))
        self.factory_patch.stop()
        self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _new_tab(self, name):
        self.tab = SimpleNamespace(target=ManualSessionTarget(name, "temp", "rev", database="db"))
        self.drivers[name] = _FakeDriver(statements={"SELECT value FROM t": {"rows": [(i,) for i in range(1000)]}})
        state = self.host._ensure_tab_state(self.tab)
        self.assertIsNotNone(state)
        self.host._visible_tab_changed()
        return state

    def _select(self, size):
        combo = self.host.query_page_size
        combo.setCurrentIndex(combo.findData(size))

    def _finish_page(self):
        state, operation, future = self.watched[-1]
        page = future.result(timeout=3)
        state.last_page = page
        state.pages = (*state.pages, page) if operation == "page" else (page,)
        state.busy = False
        self.host._sync_paging(state)
        return page

    def test_per_tab_selection_busy_shutdown_and_table_mode(self):
        combo = self.host.query_page_size
        self.assertFalse(combo.isEnabled())
        self.assertEqual(self.pager.indexOf(combo), 0)
        first = self._new_tab("first")
        self.assertEqual(combo.currentData(), 200)
        self._select(50)
        second = self._new_tab("second")
        self._select(500)
        self.tab = first.tab
        self.host._visible_tab_changed()
        self.assertEqual(combo.currentData(), 50)
        self.assertEqual(second.query_page_size, 500)
        self.host._update_actions(second)  # A background completion must not reset the visible tab.
        self.assertTrue(combo.isEnabled())
        self.assertEqual(combo.currentData(), 50)
        first.busy = True
        self.host._update_actions(first)
        self.assertFalse(combo.isEnabled())
        self._select(200)  # Programmatic changes cannot bypass the disabled UI.
        self.assertEqual(first.query_page_size, 50)
        self.assertEqual(combo.currentData(), 50)
        first.busy = False
        self.host.manual_shutdown_gate(True)
        self.assertFalse(combo.isEnabled())
        self._select(500)
        self.assertEqual(first.query_page_size, 50)
        self.host.manual_shutdown_gate(False)
        self.assertTrue(combo.isEnabled())
        first.table_mode_active = True
        self.host._sync_paging(first)
        self.assertTrue(combo.isHidden())
        first.table_mode_active = False
        self.host._sync_paging(first)
        self.assertFalse(combo.isHidden())
        self.tab = None
        self.host._visible_tab_changed()
        self.host.manual_shutdown_gate(True)
        self.assertFalse(combo.isEnabled())
        self.assertEqual(self.factories, [])  # UI choices never open a connection.

    def test_query_and_next_page_sizes_preserve_cursor_owner_and_dirty_transaction(self):
        state = self._new_tab("paged")
        driver = self.drivers["paged"]
        state.session.submit("UPDATE t SET value=1 WHERE id=1").result(timeout=3)
        self.assertIs(state.session.state, ManualSessionState.DIRTY)
        self._select(50)
        self.host.run_current()
        self.assertFalse(self.host.query_page_size.isEnabled())
        pages = [self._finish_page()]
        for size in (200, 500):
            before = list(driver.events)
            self._select(size)
            self.assertEqual(driver.events, before)
            self.host.fetch_next_page()
            self.assertFalse(self.host.query_page_size.isEnabled())
            pages.append(self._finish_page())
        self.assertEqual([len(page.rows) for page in pages], [50, 200, 500])
        self.assertEqual([row[0] for page in pages for row in page.rows], list(range(750)))
        self.assertEqual(len({page.query_id for page in pages}), 1)
        self.assertEqual(len(self.factories), 1)
        owner = self.factories[0][1]
        self.assertNotEqual(owner, threading.get_ident())
        self.assertEqual({event[1] for event in driver.events}, {owner})
        self.assertEqual([event[2] for event in driver.events if event[0] == "execute"], ["UPDATE t SET value=1 WHERE id=1", "SELECT value FROM t"])
        self.assertEqual((driver.commit_count, driver.rollback_count), (0, 0))
        self.assertIs(state.session.state, ManualSessionState.DIRTY)

    def test_custom_default_is_kept_for_new_and_restored_history_tabs(self):
        self.host.page_size = 50
        first = self._new_tab("custom")
        self.assertEqual(first.query_page_size, 50)
        self.assertEqual(self.host.query_page_size.currentData(), 50)
        self.tab = SimpleNamespace(_history_target_record=SimpleNamespace(target=SimpleNamespace(connection_id="temp", profile_revision="rev", database="db", schema="")))
        self.host._history_profile = Mock(return_value=("", {"database": "db"}, self.choice))
        restored = self.host._activate_history_draft(self.tab)
        self.assertEqual(restored.query_page_size, 50)
        self.assertEqual(self.factories, [])


if __name__ == "__main__":
    unittest.main()
