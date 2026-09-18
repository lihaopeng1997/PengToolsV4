# -*- coding: utf-8 -*-
"""Focused contract tests for the standalone Prism title bar."""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from ui.prism_title_bar import (
    TITLEBAR_H,
    PrismTitleBar,
    start_system_move,
    start_system_resize,
)


_APP = QApplication.instance() or QApplication([])


class PrismTitleBarTests(unittest.TestCase):
    def setUp(self):
        self.bar = PrismTitleBar()
        self.bar.resize(860, TITLEBAR_H)
        self.bar.show()
        _APP.processEvents()

    def tearDown(self):
        self.bar.close()
        self.bar.deleteLater()
        _APP.processEvents()

    def test_calm_brand_and_window_controls_are_bounded(self):
        self.assertEqual(self.bar.objectName(), "prism-titlebar")
        self.assertEqual(self.bar.height(), TITLEBAR_H)
        self.assertEqual(self.bar.brand_name.text(), "PengToolsHub")
        self.assertEqual(self.bar.pin_button.objectName(), "prism-window-pin-btn")
        self.assertFalse(self.bar.pin_button.icon().isNull())
        self.assertEqual(self.bar.minimize_button.objectName(), "prism-window-min-btn")
        self.assertEqual(self.bar.maximize_button.objectName(), "prism-window-max-btn")
        self.assertEqual(self.bar.close_button.objectName(), "prism-window-close-btn")
        for button in (
            self.bar.pin_button,
            self.bar.minimize_button,
            self.bar.maximize_button,
            self.bar.close_button,
        ):
            self.assertEqual(button.accessibleName(), button.toolTip())
            self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.TabFocus)

    def test_window_requests_are_signals_and_close_is_not_direct_exit(self):
        signals = {"pin": [], "min": [], "max": [], "close": []}
        self.bar.pin_toggled.connect(signals["pin"].append)
        self.bar.minimize_requested.connect(lambda: signals["min"].append(True))
        self.bar.maximize_requested.connect(lambda: signals["max"].append(True))
        self.bar.close_requested.connect(lambda: signals["close"].append(True))

        self.bar.pin_button.click()
        self.bar.minimize_button.click()
        self.bar.maximize_button.click()
        self.bar.close_button.click()

        self.assertEqual(signals["pin"], [True])
        self.assertEqual(signals["min"], [True])
        self.assertEqual(signals["max"], [True])
        self.assertEqual(signals["close"], [True])

    def test_state_reflection_does_not_reemit_pin_and_updates_restore_name(self):
        emitted = []
        self.bar.pin_toggled.connect(emitted.append)
        self.bar.set_pinned(True)
        self.assertTrue(self.bar.is_pinned())
        self.assertEqual(emitted, [])

        self.bar.set_maximized(True)
        self.assertTrue(self.bar.is_maximized())
        self.assertIn("还原", self.bar.maximize_button.toolTip())
        self.bar.set_language("en")
        self.assertEqual(self.bar.maximize_button.accessibleName(), "Restore")
        self.assertEqual(self.bar.close_button.accessibleName(), "Close")

    def test_menu_api_only_attaches_host_owned_qmenus(self):
        menu = QMenu("文件")
        action = QAction("关闭当前窗口", menu)
        menu.addAction(action)
        self.bar.set_menus({"文件": menu})
        button = self.bar.menu_button("文件")
        self.assertIsNotNone(button)
        self.assertEqual(button.menu(), menu)
        self.assertIs(button.menu().actions()[0], action)
        self.bar.clear_menus()
        self.assertFalse(self.bar.menu_bar.isVisible())

    def test_empty_titlebar_double_click_is_distinct_from_buttons(self):
        doubled = []
        self.bar.drag_double_clicked.connect(lambda: doubled.append(True))
        QTest.mouseDClick(self.bar, Qt.MouseButton.LeftButton, pos=QPoint(430, 20))
        self.assertEqual(doubled, [True])

    def test_native_helpers_fail_closed_without_a_native_window(self):
        self.assertFalse(start_system_move(QWidget()))
        self.assertFalse(start_system_resize(QWidget(), Qt.Edge.LeftEdge))
        self.assertFalse(start_system_resize(QWidget(), Qt.Edge(0)))


if __name__ == "__main__":
    unittest.main()
