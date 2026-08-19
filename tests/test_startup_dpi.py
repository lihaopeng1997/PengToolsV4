# -*- coding: utf-8 -*-
"""Qt6 启动配置与大字号窄布局回归。"""

from __future__ import annotations

import ast
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
RUN_PATH = os.path.join(ROOT, "run.py")


class StartupDpiTests(unittest.TestCase):
    def test_run_does_not_set_legacy_high_dpi_environment_variables(self):
        with open(RUN_PATH, "r", encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read(), filename=RUN_PATH)

        legacy_names = {"QT_ENABLE_HIGHDPI_SCALING", "QT_AUTO_SCREEN_SCALE_FACTOR"}
        assigned = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Subscript):
                    continue
                if not (
                    isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "os"
                    and target.value.attr == "environ"
                ):
                    continue
                if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                    assigned.add(target.slice.value)

        self.assertFalse(legacy_names & assigned)


class LargeFontNarrowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_panel_remains_usable_at_18px_in_narrow_layout(self):
        from config import DEFAULT_SETTINGS
        from panels.settings_panel import SettingsPanel
        from ui.theme_manager import ThemeManager

        manager = ThemeManager.instance()
        manager.load_template(ROOT)
        manager.apply(self.app, "calm", font_size=18)
        panel = SettingsPanel({**DEFAULT_SETTINGS, "font_size": 18}, "zh")
        panel.resize(960, 720)
        panel.show()
        self.app.processEvents()
        panel.apply_layout_mode("narrow")
        self.app.processEvents()

        self.assertEqual(panel.font_size.value(), 18)
        self.assertGreaterEqual(panel.font_size.height(), panel.fontMetrics().height())
        self.assertGreater(panel.theme_grid.minimumSize().height(), 0)
        self.assertTrue(panel.theme_grid.itemAtPosition(0, 0) is not None)
        panel.close()


if __name__ == "__main__":
    unittest.main()
