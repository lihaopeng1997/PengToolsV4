# -*- coding: utf-8 -*-
"""Isolated production MainWindow shell wiring checks.

The harness calls production shell methods on a real ``MainWindow`` object but
never runs its startup constructor, loads user settings, creates business
panels, starts services, or opens WebEngine.  Panel navigation is supplied by
an in-memory stub so the tests exercise the real tab/leave-path integration.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication, QMainWindow

from config import DEFAULT_SETTINGS
from main_window import MainWindow, _STACK_PANEL_ATTRS


_APP = QApplication.instance() or QApplication([])


def _isolated_host() -> MainWindow:
    """Construct only the production client shell with memory-only state."""

    host = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(host)
    host._settings = dict(DEFAULT_SETTINGS, ui_web_shell=False, floating_enabled=False)
    host.language = 'zh'
    host._private_unlocked = True
    host._current_nav_index = 0
    host._layout_mode = 'standard'
    host._nav_icon_only = False
    host._nav_collapsed = False
    host._window_pinned = False
    host._web_shell_enabled = False
    host._chrome_bridge = None
    host._dash_bridge = None
    host._dash_holder = None
    host._startup_ready = True
    host._user_navigated = False
    host._shutting_down = False
    host._force_exit = False
    host.quick_panel = None
    host.tray_service = None
    host.hotkey_service = None
    host.keep_awake_service = None
    host._completed_tasks = 0
    host._boot_step = 0
    for attr in _STACK_PANEL_ATTRS:
        setattr(host, attr, None)

    with patch('ui.web_shell.runtime_web_shell_available', return_value=False):
        host._setup_ui_shell()

    class _LayoutStub:
        low_height = False

        def observe(self, _width, _height):
            return None

        def force(self, _width, _height):
            return None

    host._layout_controller = _LayoutStub()
    host._panel_needs_create = lambda index: False
    host._ensure_panel_for_nav = lambda index: None
    return host


class PrismMainShellIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.host = _isolated_host()
        self.host.show()
        _APP.processEvents()

    def tearDown(self):
        # This isolated host never owns startup/shutdown services; hiding it
        # avoids entering the production close confirmation chain.
        self.host.hide()
        self.host.deleteLater()
        _APP.processEvents()

    def test_common_controls_are_in_client_shell_and_global_tabs_are_real(self):
        self.assertTrue(self.host._window_frame.is_frameless())
        self.assertIs(self.host.title_bar.parentWidget(), self.host.centralWidget())
        self.assertIs(self.host.user_chip.window(), self.host)
        self.assertIs(self.host.version_label.window(), self.host)
        self.assertEqual(self.host.module_tabs.open_nav_indices, (0,))

        self.assertTrue(self.host._show_panel(1))
        self.assertTrue(self.host._show_panel(2))
        self.assertEqual(self.host.module_tabs.open_nav_indices, (0, 1, 2))
        self.assertEqual(self.host._current_nav_index, 2)

        self.host._show_panel(1)
        self.assertEqual(self.host.module_tabs.current_nav_index, 1)
        self.assertEqual(self.host.module_tabs.tab_bar.currentIndex(), 1)

    def test_open_tabs_follow_context_header_inside_workspace(self):
        content = self.host._content_frame
        layout = self.host._content_layout

        self.assertIs(self.host.module_tabs.parentWidget(), content)
        self.assertIsNot(self.host.module_tabs.parentWidget(), self.host.centralWidget())
        self.assertLess(layout.indexOf(self.host._context_header), layout.indexOf(self.host.module_tabs))
        self.assertLess(layout.indexOf(self.host.module_tabs), layout.indexOf(self.host._page_body))
        self.assertEqual(layout.indexOf(self.host._context_header), 0)
        self.assertEqual(layout.indexOf(self.host.module_tabs), 1)
        self.assertEqual(layout.indexOf(self.host._page_body), 2)

    def test_failed_close_fallback_keeps_the_requested_tab(self):
        self.assertTrue(self.host._show_panel(1))
        self.assertTrue(self.host._show_panel(2))
        self.assertEqual(self.host.module_tabs.current_nav_index, 2)
        old_stack_index = self.host.stack.currentIndex()

        def fail(_index):
            raise RuntimeError('isolated panel failure')

        self.host._ensure_panel_for_nav = fail
        self.host._on_module_tab_close(2)
        self.host._on_module_tab_close(2)
        self.assertIn(2, self.host.module_tabs.open_nav_indices)
        self.assertEqual(self.host.module_tabs.current_nav_index, 2)
        self.assertEqual(self.host._current_nav_index, 2)
        self.assertEqual(self.host.stack.currentIndex(), old_stack_index)

    def test_close_current_tab_reuses_neighbor_without_destroying_shell(self):
        self.assertTrue(self.host._show_panel(1))
        self.assertTrue(self.host._show_panel(2))
        old_stack = self.host.stack
        self.host._on_module_tab_close(2)
        self.assertEqual(self.host._current_nav_index, 1)
        self.assertEqual(self.host.module_tabs.current_nav_index, 1)
        self.assertNotIn(2, self.host.module_tabs.open_nav_indices)
        self.assertIs(self.host.stack, old_stack)


if __name__ == '__main__':
    unittest.main()
