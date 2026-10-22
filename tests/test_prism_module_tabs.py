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

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTabBar, QToolButton

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
        self.assertEqual(module_tab_for_nav(14).nav_index, 14)
        self.assertEqual(module_tab_for_nav(15), None)
        for legacy_index in range(18, 24):
            self.assertEqual(module_tab_for_nav(legacy_index).nav_index, 14)
        self.assertEqual(module_tab_for_nav(999), None)
        self.assertEqual(module_tab_for_nav("bad"), None)

    def test_home_is_first_and_open_is_deduplicated(self):
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV,))
        self.assertTrue(self.tabs.open_nav(10))
        self.assertTrue(self.tabs.open_nav(18))
        self.assertTrue(self.tabs.open_nav(23))
        self.assertTrue(self.tabs.open_nav(14))
        self.assertTrue(self.tabs.open_nav(10))
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 14))
        self.assertEqual(self.tabs.tab_bar.count(), 3)
        self.assertEqual(self.tabs.tab_bar.tabData(1), 10)
        self.assertEqual(self.tabs.tab_bar.tabData(2), 14)
        self.assertFalse(self.tabs.open_nav(999))
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 14))

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

    def test_close_button_click_requests_one_nav_and_waits_for_host(self):
        self.tabs.open_nav(10)
        self.tabs.open_nav(18)
        self.assertIsNone(self.tabs.tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide))
        button = self.tabs.tab_bar.tabButton(self.tabs.tab_index(10), QTabBar.ButtonPosition.RightSide)
        self.assertIsInstance(button, QToolButton)
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.TabFocus)
        button.setFocus(Qt.FocusReason.TabFocusReason)
        _APP.processEvents()
        self.assertTrue(button.hasFocus())

        closed = []
        self.tabs.close_requested.connect(closed.append)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertEqual(closed, [10])
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 14))
        self.assertEqual(self.tabs.current_nav_index, 14)
        self.assertEqual(self.tabs.tab_bar.count(), 3)

    def test_large_fonts_and_scroll_keep_last_close_button_visible(self):
        original_font = QFont(_APP.font())
        try:
            for point_size in (13, 16):
                with self.subTest(point_size=point_size):
                    font = QFont(original_font)
                    font.setPointSize(point_size)
                    _APP.setFont(font)
                    tabs = ModuleTabs()
                    try:
                        tabs.resize(320, 38)
                        tabs.show()
                        tabs.replace_nav([10, 1, 2, 3, 5, 16, 17, 14])
                        tabs.set_current_nav(14)
                        _APP.processEvents()

                        bar = tabs.tab_bar
                        self.assertEqual(bar.font().pointSize(), point_size)
                        self.assertEqual(tabs.height(), 38)
                        self.assertLessEqual(bar.sizeHint().height(), bar.height())
                        self.assertLessEqual(bar.geometry().bottom(), tabs.height() - 1)
                        for tab_index in range(bar.count()):
                            self.assertLessEqual(bar.tabRect(tab_index).height(), bar.height())

                        last = tabs.tab_index(14)
                        last_rect = bar.tabRect(last)
                        button = bar.tabButton(last, QTabBar.ButtonPosition.RightSide)
                        left_arrow = bar.findChild(QToolButton, "ScrollLeftButton")
                        right_arrow = bar.findChild(QToolButton, "ScrollRightButton")
                        self.assertTrue(left_arrow.isVisible())
                        self.assertTrue(right_arrow.isVisible())
                        self.assertTrue(bar.rect().contains(last_rect))
                        self.assertTrue(bar.rect().contains(button.geometry()))
                        self.assertGreaterEqual(button.geometry().left(), last_rect.left())
                        self.assertLessEqual(button.geometry().right(), last_rect.right())
                        self.assertLess(button.geometry().bottom(), bar.height())
                        self.assertFalse(button.geometry().intersects(left_arrow.geometry()))
                        self.assertFalse(button.geometry().intersects(right_arrow.geometry()))
                    finally:
                        tabs.close()
                        tabs.deleteLater()
                        _APP.processEvents()
        finally:
            _APP.setFont(original_font)

    def test_neighbor_is_read_only_and_keeps_non_active_tab_current(self):
        self.tabs.replace_nav([10, 18])
        self.tabs.set_current_nav(18)
        self.assertEqual(self.tabs.neighbor_nav(18), 10)
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 14))
        self.assertEqual(self.tabs.neighbor_nav(10), 14)
        self.tabs.set_current_nav(HOME_NAV)
        self.assertEqual(self.tabs.neighbor_nav(10), HOME_NAV)

    def test_language_and_replace_preserve_navigation_identity(self):
        self.tabs.replace_nav([18, 14, 18, 23])
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 14))
        self.tabs.set_current_nav(18)
        self.tabs.set_language("en")
        self.assertEqual(self.tabs.current_nav_index, 14)
        self.assertEqual(self.tabs.tab_bar.tabText(self.tabs.tab_index(14)), display_name(14, "en"))

    def test_close_targets_cover_self_others_and_all_without_home(self):
        self.tabs.replace_nav([10, 12, 14])
        self.tabs.set_current_nav(12)
        self.assertEqual(self.tabs.close_targets(12, "self"), (12,))
        self.assertEqual(self.tabs.keep_nav_after_close(12, "self"), 10)
        self.assertEqual(self.tabs.close_targets(14, "others"), (10, 12))
        self.assertEqual(self.tabs.keep_nav_after_close(14, "others"), 14)
        self.assertEqual(self.tabs.close_targets(HOME_NAV, "all"), (10, 14, 12))
        self.assertEqual(self.tabs.keep_nav_after_close(12, "all"), HOME_NAV)
        self.assertNotIn(HOME_NAV, self.tabs.close_targets(HOME_NAV, "self"))
        self.assertEqual(self.tabs.close_targets(HOME_NAV, "self"), ())
        self.assertEqual(self.tabs.open_nav_indices, (HOME_NAV, 10, 12, 14))

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
