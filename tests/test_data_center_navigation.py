"""Navigation and MainWindow convergence for the unified Data Center page."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication

from config import DEFAULT_SETTINGS
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.module_tabs import module_tab_for_nav
from ui.navigation_model import (
    AI_WORKBENCH_NAV,
    DASHBOARD_QUICK_TOOL_INDICES,
    NAV_MODEL,
    build_web_nav_model_data,
    canonical_nav_index,
    get_dashboard_quick_tools,
)


APP = QApplication.instance() or QApplication([])


class DataCenterNavigationTests(unittest.TestCase):
    def test_authoritative_python_model_projects_one_page_and_home_shortcut(self):
        indices = [index for _group, entries in NAV_MODEL for index, *_rest in entries]
        self.assertEqual(indices.count(14), 1)
        self.assertFalse(any(18 <= index <= 23 for index in indices))
        self.assertEqual(DASHBOARD_QUICK_TOOL_INDICES[0], 14)
        self.assertEqual(get_dashboard_quick_tools()[0]["i"], 14)
        self.assertEqual(build_web_nav_model_data()["quick_tool_indices"], list(DASHBOARD_QUICK_TOOL_INDICES))
        web_indices = [
            item["i"] for group in build_web_nav_model_data(private_unlocked=True)["groups"]
            for item in group["items"]
        ]
        self.assertIn(14, web_indices)
        self.assertFalse(any(18 <= index <= 23 for index in web_indices))
        for legacy in range(18, 24):
            self.assertEqual(canonical_nav_index(legacy), 14)
            self.assertEqual(module_tab_for_nav(legacy).nav_index, 14)

    def test_production_show_panel_routes_14_and_legacy_aliases_to_one_panel(self):
        from main_window import MainWindow, STACK_DATA_CENTER, _STACK_PANEL_ATTRS
        import panels.data_center_panel as panel_module

        rows = [
            {"id": "oracle-1", "name": "Oracle", "dialect": "oracle"},
            {"id": "mysql-1", "name": "MySQL", "dialect": "mysql"},
            {"id": "ocean-1", "name": "OceanBase", "dialect": "oceanbase"},
            {"id": "dm-1", "name": "Dameng", "dialect": "dameng"},
            {"id": "redis-1", "name": "Redis", "dialect": "redis"},
            {"id": "mongo-1", "name": "MongoDB", "dialect": "mongodb"},
        ]
        catalog = WorkspaceCatalog(loader=lambda: [dict(item) for item in rows])
        settings = dict(DEFAULT_SETTINGS)
        settings["ui_web_shell"] = False
        settings["close_ask_each_time"] = True
        patches = [
            patch("main_window.load_settings", return_value=settings),
            patch("main_window.save_settings", side_effect=lambda value: value),
            patch("config.save_settings", side_effect=lambda value: value),
            patch("config.save_layout_splitter", side_effect=lambda *_args: None),
            patch.object(MainWindow, "_complete_startup_sync", lambda self: None),
            patch.object(MainWindow, "_ensure_dashboard_panel", lambda self: None),
            patch.object(panel_module, "WorkspaceCatalog", lambda: catalog),
        ]
        for active in patches:
            active.start()
        window = None
        try:
            window = MainWindow()
            self.assertTrue(window._show_panel(14))
            panel = window.data_center_panel
            self.assertIsNotNone(panel)
            self.assertEqual(window.stack.currentIndex(), STACK_DATA_CENTER)
            self.assertEqual(window.module_tabs.open_nav_indices, (0, 14))

            for legacy in range(18, 24):
                self.assertTrue(window._show_panel(legacy))
                self.assertIs(window.data_center_panel, panel)
                self.assertEqual(window._current_nav_index, 14)
                self.assertEqual(window.stack.currentIndex(), STACK_DATA_CENTER)
                self.assertEqual(window.module_tabs.open_nav_indices, (0, 14))
                self.assertEqual(len(panel._workspaces), 0)

            self.assertEqual(window._stack_index_for_nav(AI_WORKBENCH_NAV), 14)
            self.assertEqual(_STACK_PANEL_ATTRS[14], "agent_workbench_panel")
            self.assertEqual(_STACK_PANEL_ATTRS[STACK_DATA_CENTER], "data_center_panel")
            self.assertIsNone(window.agent_workbench_panel)
        finally:
            if window is not None:
                window.hide()
                window.deleteLater()
                APP.processEvents()
            for active in reversed(patches):
                active.stop()


if __name__ == "__main__":
    unittest.main()
