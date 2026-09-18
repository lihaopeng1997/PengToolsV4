# -*- coding: utf-8 -*-
"""Focused contract tests for global Prism module tabs."""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from ui.module_tabs import HOME_NAV, ModuleTabs, module_tab_for_nav
from ui.navigation_model import display_name


_APP = QApplication.instance() or QApplication([])


class PrismModuleTabsTests(unittest.TestCase):
    def setUp(self):
        self.tabs = ModuleTabs()
        self.tabs.show()
        _APP.processEvents()

    def tearDown(self):
        self.tabs.close()
        self.tabs.deleteLater()
        _APP.processEvents()

    def test_model_derives_real_navigation_ids_and_excludes_parents(self):
        home = module_tab_for_nav(HOME_NAV)
        self.assertIsNotNone(home)
        self.assertFalse(home.closable)
        self.assertEqual(home.title, display_name(HOME_NAV, "zh"))
        self.assertEqual(module_tab_for_nav(14), None)
        self.assertEqual(module_tab_for_nav(15), None)
        self.assertEqual(module_tab_for_nav(999), None)
        self.assertEqual(module_tab_for_nav("bad"), None)

    def test_home_is_first_and_open_is_deduplicated(self):
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV,))
        self.assertTrue(self.tabs.open_nav(10))
        self.assertTrue(self.tabs.open_nav(18))
        self.assertTrue(self.tabs.open_nav(10))
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 18))
        self.assertEqual(self.tabs.tab_bar.count(), 3)
        self.assertEqual(self.tabs.tab_bar.tabData(1), 10)
        self.assertEqual(self.tabs.tab_bar.tabData(2), 18)
        self.assertFalse(self.tabs.open_nav(14))
        self.assertFalse(self.tabs.open_nav(999))
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 18))

    def test_tab_activation_emits_nav_request_but_programmatic_sync_is_silent(self):
        activated = []
        self.tabs.activate_requested.connect(activated.append)
        self.tabs.open_nav(10)
        self.assertEqual(activated, [])
        self.tabs._on_current_changed(self.tabs.tab_index(10))
        self.assertEqual(activated, [10])

    def test_close_request_does_not_destroy_or_remove_tab(self):
        closed = []
        self.tabs.close_requested.connect(closed.append)
        self.tabs.open_nav(10)
        self.tabs._on_close_requested(self.tabs.tab_index(10))
        self.assertEqual(closed, [10])
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10))
        self.assertFalse(self.tabs.remove_nav(HOME_NAV))
        self.assertTrue(self.tabs.remove_nav(10))
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV,))

        self.tabs.replace_nav([10, 18])
        self.tabs.set_current_nav(18)
        self.assertTrue(self.tabs.remove_nav(18))
        self.assertEqual(self.tabs.current_nav_index, 10)

    def test_neighbor_is_read_only_and_keeps_non_active_tab_current(self):
        self.tabs.replace_nav([10, 18])
        self.tabs.set_current_nav(18)
        self.assertEqual(self.tabs.neighbor_nav(18), 10)
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 18))
        self.assertEqual(self.tabs.neighbor_nav(10), 18)
        self.tabs.set_current_nav(HOME_NAV)
        self.assertEqual(self.tabs.neighbor_nav(10), HOME_NAV)

    def test_language_and_replace_preserve_navigation_identity(self):
        self.tabs.replace_nav([18, 14, 18, 23])
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 18, 23))
        self.tabs.set_current_nav(23)
        self.tabs.set_language("en")
        self.assertEqual(self.tabs.current_nav_index, 23)
        self.assertEqual(self.tabs.tab_bar.tabText(self.tabs.tab_index(18)), display_name(18, "en"))
        self.assertEqual(self.tabs.tab_bar.tabText(self.tabs.tab_index(23)), display_name(23, "en"))

    def test_context_request_contains_real_nav_id_and_global_position(self):
        requested = []
        self.tabs.context_requested.connect(lambda index, pos: requested.append((index, pos)))
        self.tabs.open_nav(10)
        self.tabs._on_context_requested(QPoint(self.tabs.tab_bar.tabRect(self.tabs.tab_index(10)).center()))
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0][0], 10)
        self.assertIsInstance(requested[0][1], QPoint)


if __name__ == "__main__":
    unittest.main()
