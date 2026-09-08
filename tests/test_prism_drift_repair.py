"""Behavior regressions for the approved monthly-only home and layer boundaries."""
import builtins
import datetime
import unittest
from unittest.mock import patch


class SummaryInjectionTests(unittest.TestCase):
    def test_headless_summary_does_not_load_presentation_modules(self):
        from tools.dashboard_summary import build_dashboard_summary

        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == 'ui' or name.startswith('ui.') or name.startswith('panels.'):
                raise AssertionError('headless summary imported ' + name)
            return real_import(name, *args, **kwargs)

        fixture = dict(today=datetime.date(2026, 9, 8), requirements=[], board={}, reports={})
        tools = [{'i': 18, 'zh': '数据中心', 'icon': 'database'}]
        with patch('builtins.__import__', side_effect=guarded):
            self.assertEqual(build_dashboard_summary(**fixture)['tools'], [])
            result = build_dashboard_summary(**fixture, tools=tools)
        self.assertEqual(result['tools'], tools)
        self.assertIn('monthly_release_tasks', result)


class NativeHomePresentationTests(unittest.TestCase):
    def test_monthly_card_survives_resizing_without_old_recent_card(self):
        from PyQt6.QtWidgets import QApplication
        from panels.dashboard_panel import DashboardPanel

        app = QApplication.instance() or QApplication([])
        # Construction must not read or write the user's workspace records.
        with patch.object(DashboardPanel, 'refresh'), patch.object(DashboardPanel, '_sources_changed', return_value=False):
            panel = DashboardPanel()
            try:
                panel.show()
                for mode, size in [('wide', (1440, 900)), ('narrow', (960, 640))]:
                    panel.resize(*size)
                    panel.apply_layout_mode(mode, size[1] < 720)
                    app.processEvents()
                    self.assertTrue(panel.recent_card.isHidden())
                    self.assertTrue(panel.release_card.isVisible())
                opened = []
                panel.open_sql.connect(lambda: opened.append(True))
                panel.release_more.click()
                self.assertEqual(opened, [True])
                self.assertGreater(panel.hero_card.maximumHeight(), 176)
            finally:
                panel.close()
                panel.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
