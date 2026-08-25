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

        settings = dict(DEFAULT_SETTINGS)
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
            window._show_panel(14)
            self.assertIsNotNone(window.ai_workbench_panel)
            self.assertIs(window.stack.currentWidget(), window.ai_workbench_panel)
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


if __name__ == '__main__':
    unittest.main()
