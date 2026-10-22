# -*- coding: utf-8 -*-
"""Regression for immediate hiding of retired sidebar widgets."""

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from ui.navigation_model import build_web_nav_model_data
from ui.prism_sidebar import PrismSidebar


APP = QApplication.instance() or QApplication(['prism-sidebar-retirement-test'])


class PrismSidebarRetirementTests(unittest.TestCase):
    def setUp(self):
        self.model = build_web_nav_model_data(private_unlocked=True, current=0)
        self.sidebar = PrismSidebar(self.model)
        self.sidebar.resize(248, 700)
        self.sidebar.show()
        APP.processEvents()
        self.retired = []

    def tearDown(self):
        self.sidebar.close()
        self.sidebar.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    @staticmethod
    def _layout_widgets(layout):
        widgets = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None:
                widgets.append(widget)
        return widgets

    def _attached_widgets(self):
        return (
            self._layout_widgets(self.sidebar._nav_layout)
            + self._layout_widgets(self.sidebar._footer_layout)
        )

    def _assert_retired_immediately(self, retired, rebuild_name):
        self.assertTrue(retired, rebuild_name)
        self.assertTrue(any(isinstance(widget, QPushButton) for widget in retired), rebuild_name)
        for widget in retired:
            self.assertFalse(sip.isdeleted(widget), (rebuild_name, widget.objectName(), 'deleted synchronously'))
            self.assertFalse(widget.isVisible(), (rebuild_name, widget.objectName(), 'still visible'))
        self.retired.extend(retired)

    def _assert_current_navigation_works(self, button, expected_index, context):
        self.assertIsNotNone(button, context)
        self.assertTrue(button.isVisible(), (context, button.objectName(), 'hidden current control'))
        requests = []

        def capture(index):
            requests.append(index)

        self.sidebar.navigate_requested.connect(capture)
        try:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        finally:
            self.sidebar.navigate_requested.disconnect(capture)
        self.assertEqual(requests, [expected_index], context)

    def test_mode_language_and_model_rebuilds_hide_then_defer_disposal(self):
        # Collapsing replaces the expanded labels, page buttons, and footer
        # buttons. The newly attached controls remain visible and clickable.
        retired = self._attached_widgets()
        self.sidebar.set_icon_only(True)
        self.assertTrue(any(isinstance(widget, QLabel) for widget in retired), 'mode rebuild should retire section labels')
        self._assert_retired_immediately(retired, 'mode rebuild')
        APP.processEvents()
        self.assertTrue(self.sidebar._group_buttons)
        self.assertTrue(self.sidebar._settings_button.isVisible())
        self.assertTrue(self.sidebar._palette_button.isVisible())
        self._assert_current_navigation_works(self.sidebar._buttons[0], 0, 'mode rebuild home click')

        # Language rebuilds replace the currently collapsed labels/buttons too.
        retired = self._attached_widgets()
        self.sidebar.set_language('en')
        self._assert_retired_immediately(retired, 'language rebuild')
        APP.processEvents()
        self.assertEqual(self.sidebar._language, 'en')
        self.assertTrue(self.sidebar._group_buttons['datacenter'].isVisible())
        self.assertTrue(self.sidebar._settings_button.isVisible())
        self.assertTrue(self.sidebar._palette_button.isVisible())
        self._assert_current_navigation_works(self.sidebar._buttons[0], 0, 'language rebuild home click')

        # A fresh permission-filtered model retires the previous footer controls
        # while preserving the accepted gear -> settings icon adapter.
        locked_model = build_web_nav_model_data(private_unlocked=False, current=7)
        retired = self._attached_widgets()
        self.sidebar.set_nav_model(locked_model)
        self._assert_retired_immediately(retired, 'navigation-model rebuild')
        APP.processEvents()
        self.assertIn('personal', self.sidebar._group_buttons)
        settings = self.sidebar._settings_button
        self.assertIs(settings, self.sidebar._buttons[7])
        self.assertFalse(settings.icon().isNull())
        self.assertEqual(self.sidebar.nav_model['settings']['icon'], 'gear')
        self.assertTrue(settings.isVisible())
        self.assertTrue(self.sidebar._palette_button.isVisible())
        self._assert_current_navigation_works(settings, 7, 'navigation-model rebuild settings click')

        # Rebuilds hide immediately but retain Qt's normal deferred ownership.
        self.assertTrue(self.retired)
        self.assertTrue(all(not sip.isdeleted(widget) for widget in self.retired))
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.assertTrue(all(sip.isdeleted(widget) for widget in self.retired))


if __name__ == '__main__':
    unittest.main(verbosity=2)
