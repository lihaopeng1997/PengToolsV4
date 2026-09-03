# -*- coding: utf-8 -*-
"""启动：壳先就绪 + 同步 boot（offscreen）可用。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class StartupBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_sync_boot_has_dashboard_and_requirement(self):
        from config import DEFAULT_SETTINGS
        from main_window import MainWindow

        settings = dict(DEFAULT_SETTINGS, ui_web_shell=False)
        with patch('main_window.load_settings', return_value=settings):
            window = MainWindow()
        try:
            self.assertTrue(window._startup_ready)
            self.assertIsNotNone(window.dashboard_panel)
            self.assertIsNotNone(window.requirement_panel)
            self.assertIsNotNone(window.settings_panel)
            # 接口/日志仍可延后
            self.assertIsNone(window.interface_debug_panel)
            self.assertIsNone(window.ops_log_panel)
            self.assertTrue(window._startup_loading.isHidden())
            window._show_panel(12)
            self.assertIsNotNone(window.interface_debug_panel)
            self.assertIs(window.stack.currentWidget(), window.interface_debug_panel)
            self.assertTrue(window._startup_loading.isHidden())
            # v3.0：nav 18 = Oracle 面板（六数据库面板之一）
            window._show_panel(18)
            self.assertIsNotNone(window.db_panel_0)
            self.assertIs(window.stack.currentWidget(), window.db_panel_0)
        finally:
            if window.hotkey_service:
                window.hotkey_service.unregister()
            if window.quick_panel is not None:
                window.quick_panel.close_toolbar()
            if window.tray_service is not None:
                window.tray_service.hide()
            if window.keep_awake_service is not None:
                window.keep_awake_service.stop()
            window.hide()
            window.deleteLater()

    def test_splash_can_construct(self):
        from ui.startup_splash import StartupSplash
        splash = StartupSplash(self.app)
        splash.show_status('测试')
        self.assertIn('测试', splash._message)
        splash.close()


    def test_startup_top_level_widgets_no_isolated_buttons(self):
        from config import DEFAULT_SETTINGS
        from main_window import MainWindow
        from PyQt6.QtWidgets import QPushButton

        initial_top = set(self.app.topLevelWidgets())
        settings = dict(DEFAULT_SETTINGS, ui_web_shell=False)
        with patch('main_window.load_settings', return_value=settings):
            window = MainWindow()
        try:
            window.show()
            self.app.processEvents()
            new_top = set(self.app.topLevelWidgets()) - initial_top
            for widget in new_top:
                if isinstance(widget, QPushButton):
                    self.assertNotIn(widget.text(), ('检出代码', '导入资料', '系统配置'))
                title = getattr(widget, 'windowTitle', lambda: '')()
                self.assertNotIn(title, ('检出代码', '导入资料', '系统配置'))
                if widget.isVisible():
                    self.assertIn(widget.__class__.__name__, ('MainWindow', 'QuickPanel'))
        finally:
            if window.hotkey_service:
                window.hotkey_service.unregister()
            if window.quick_panel is not None:
                window.quick_panel.close_toolbar()
            if window.tray_service is not None:
                window.tray_service.hide()
            if window.keep_awake_service is not None:
                window.keep_awake_service.stop()
            window.hide()
            window.deleteLater()


if __name__ == '__main__':
    unittest.main()
