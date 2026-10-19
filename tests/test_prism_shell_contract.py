# -*- coding: utf-8 -*-
"""Real MainWindow shell contract for the approved Prism title bar, tabs, and sidebar."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['PENGTOOLS_SYNC_BOOT'] = '1'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from config import DEFAULT_SETTINGS


_app = QApplication.instance() or QApplication([])
_WINDOW = None


def _window():
    global _WINDOW
    if _WINDOW is None:
        from main_window import MainWindow

        settings = dict(DEFAULT_SETTINGS)
        settings['ui_web_shell'] = False
        settings['close_ask_each_time'] = True
        with patch('main_window.load_settings', return_value=settings):
            _WINDOW = MainWindow()
        _WINDOW.show()
        _app.processEvents()
    return _WINDOW


class PrismShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _window()

    def test_title_bar_controls_and_close_uses_the_window_path(self):
        win = self.win
        bar = win.title_bar
        self.assertEqual(bar.brand_name.text(), 'PengToolsHub')
        for name in ('pin_button', 'minimize_button', 'maximize_button', 'close_button'):
            button = getattr(bar, name)
            self.assertTrue(button.isEnabled())
            self.assertTrue(bar.isAncestorOf(button))
        self.assertIn('文件', win._shell_menus)
        self.assertIn('视图', win._shell_menus)
        self.assertIn('帮助', win._shell_menus)
        labels = []
        for menu in win._shell_menus.values():
            labels.extend(action.text() for action in menu.actions())
        joined = ' '.join(labels)
        for forbidden in ('通知', '导出', '设计展板', 'Notification', 'Export'):
            self.assertNotIn(forbidden, joined)

        quit_calls = []
        with patch.object(QApplication, 'quit', side_effect=lambda *args: quit_calls.append(True)):
            with patch.object(win, '_ask_close_action', return_value=None) as ask:
                bar.close_button.click()
                _app.processEvents()
                ask.assert_called()
        self.assertEqual(quit_calls, [])
        self.assertFalse(win.isHidden())

    def test_maximize_restore_and_resize_respect_minimum(self):
        win = self.win
        win.showNormal()
        _app.processEvents()
        win.resize(1200, 760)
        _app.processEvents()
        smaller = QSize(win.width(), win.height())
        win.resize(1440, 900)
        _app.processEvents()
        self.assertGreaterEqual(win.width(), smaller.width())
        self.assertGreaterEqual(win.height(), smaller.height())

        win.title_bar.maximize_button.click()
        _app.processEvents()
        self.assertTrue(win.isMaximized() or win.title_bar.is_maximized())
        win.title_bar.maximize_button.click()
        _app.processEvents()
        self.assertFalse(win.isMaximized())
        self.assertFalse(win.title_bar.is_maximized())

        self.assertGreaterEqual(win.minimumWidth(), 960)
        self.assertGreaterEqual(win.minimumHeight(), 640)
        win.resize(200, 200)
        _app.processEvents()
        self.assertGreaterEqual(win.width(), 960)
        self.assertGreaterEqual(win.height(), 640)

    def test_module_tabs_live_in_the_workspace_and_close_keeps_the_panel(self):
        win = self.win
        content = win._content_frame
        sidebar = win._sidebar
        tabs = win.module_tabs
        self.assertTrue(content.isAncestorOf(tabs))
        self.assertFalse(sidebar.isAncestorOf(tabs))
        root = win.centralWidget().layout()
        shell_body = win.centralWidget().findChild(QWidget, 'prism-shell-body')
        self.assertIsNotNone(shell_body)
        self.assertLess(root.indexOf(win.title_bar), root.indexOf(shell_body))
        self.assertTrue(shell_body.isAncestorOf(sidebar))
        self.assertTrue(shell_body.isAncestorOf(content))
        order = win._content_layout
        self.assertIsNone(win._context_header)
        self.assertEqual(order.indexOf(tabs), 0)
        self.assertLess(order.indexOf(tabs), order.indexOf(win._page_body))
        self.assertEqual(tabs.y(), 0)

        quick_button = win._quick_panel_btn
        self.assertTrue(win.title_bar.isAncestorOf(quick_button))
        self.assertFalse(content.isAncestorOf(quick_button))
        self.assertTrue(quick_button.isVisible())
        with patch.object(win, 'quick_panel', Mock()) as quick_panel:
            quick_button.click()
            quick_panel.show_panel.assert_called_once()

        self.assertTrue(win._show_panel(1))
        self.assertTrue(win._show_panel(2))
        credit = win.credit_panel
        self.assertIsNotNone(credit)
        self.assertIn(1, tabs.open_nav_indices)
        win._on_module_tab_close(1)
        _app.processEvents()
        self.assertNotIn(1, tabs.open_nav_indices)
        self.assertIs(win.credit_panel, credit)
        self.assertFalse(sip_is_deleted(credit))
        self.assertEqual(tabs.current_nav_index, 2)


class PrismSidebarContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _window()

    def test_default_collapsed_type_icons_and_expanded_leaves_and_workspace_label(self):
        win = self.win
        sidebar = win._prism_sidebar
        self.assertTrue(win._nav_collapsed)
        self.assertTrue(sidebar._icon_only)
        self.assertFalse(win.sidebar_title.isVisible())
        self.assertIn('datacenter', sidebar._group_buttons)
        self.assertIn('ops', sidebar._group_buttons)
        self.assertNotIn(18, sidebar._buttons)
        self.assertNotIn(19, sidebar._buttons)

        win._set_nav_collapsed(False, persist=False)
        self.assertEqual(win.sidebar_title.text(), '个人工作空间')
        self.assertNotEqual(win.sidebar_title.text(), 'PengToolsHub')
        sidebar.set_language('en')
        self.assertEqual(win.sidebar_title.text(), 'Personal workspace')
        self.assertIn('Floating toolbar', sidebar._palette_button.toolTip())
        sidebar.set_language('zh')
        self.assertIn('悬浮工具栏', sidebar._palette_button.toolTip())
        self.assertNotIn('主题', sidebar._palette_button.text())

        model = win._build_web_nav_model()
        self.assertEqual(model['language'], 'zh')
        indexes = list(sidebar._buttons)
        for leaf in (16, 17, 18, 19, 20, 21, 22, 23):
            self.assertIn(leaf, indexes)
        self.assertNotIn(14, indexes)
        self.assertNotIn(15, indexes)
        self.assertFalse(sidebar._group_buttons)
        sidebar.set_current(19)
        self.assertTrue(sidebar._buttons[19].isChecked())
        self.assertFalse(sidebar._buttons[18].isChecked())
        win._set_language(1)
        self.assertEqual(win._build_web_nav_model()['language'], 'en')
        self.assertEqual(win.sidebar_title.text(), 'Personal workspace')
        win._set_language(0)


def sip_is_deleted(widget) -> bool:
    try:
        widget.objectName()
    except RuntimeError:
        return True
    return False


if __name__ == '__main__':
    unittest.main()
