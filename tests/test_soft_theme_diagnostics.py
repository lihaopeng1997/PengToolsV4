# -*- coding: utf-8 -*-
"""整体软化：主题监听诊断与运行时图标色来源回归。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from PyQt6.QtWidgets import QApplication
    from ui.icons import qicon
    from ui.theme_manager import ThemeManager
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class SoftThemeDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = ThemeManager.instance()
        self.manager.load_template(PROJECT_DIR)
        self.manager.apply(self.app, 'calm', font_size=12)
        self.manager.clear_listener_failures()

    def tearDown(self):
        self.manager.clear_listener_failures()

    def test_listener_failure_is_recorded_without_blocking_theme_apply(self):
        def broken_listener(_theme_id):
            raise RuntimeError('refresh failed')

        self.manager.add_listener(broken_listener)
        try:
            self.manager.apply(self.app, 'night', font_size=12)
            failures = self.manager.listener_failures()
        finally:
            self.manager.remove_listener(broken_listener)

        self.assertEqual(self.manager.theme_id, 'night')
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['theme_id'], 'night')
        self.assertEqual(failures[0]['error_type'], 'RuntimeError')
        self.assertIn('refresh failed', failures[0]['message'])

    def test_runtime_icon_defaults_change_with_theme(self):
        calm_image = qicon('json', size=20).pixmap(20, 20).toImage()
        calm_colors = {
            calm_image.pixelColor(x, y).name()
            for x in range(calm_image.width()) for y in range(calm_image.height())
            if calm_image.pixelColor(x, y).alpha() > 0
        }
        self.manager.apply(self.app, 'night', font_size=12)
        night_image = qicon('json', size=20).pixmap(20, 20).toImage()
        night_colors = {
            night_image.pixelColor(x, y).name()
            for x in range(night_image.width()) for y in range(night_image.height())
            if night_image.pixelColor(x, y).alpha() > 0
        }
        self.assertNotEqual(calm_colors, night_colors)


if __name__ == '__main__':
    unittest.main()
