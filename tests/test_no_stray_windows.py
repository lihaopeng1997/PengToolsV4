# -*- coding: utf-8 -*-
"""刷新页面时不得把尚未挂父对象的控件显示成独立小窗口。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication, QWidget


class StrayWindowSpy:
    """记录 setVisible(True) 时仍是顶层窗口的控件（主窗口/页面本身除外）。"""

    def __init__(self, *allowed_types):
        self.allowed = set(allowed_types)
        self.shown = []
        self._original = QWidget.setVisible

    def __enter__(self):
        spy = self

        def set_visible(widget, visible):
            if visible and widget.isWindow() and type(widget).__name__ not in spy.allowed:
                spy.shown.append(f'{type(widget).__name__}#{widget.objectName()}')
            return spy._original(widget, visible)

        QWidget.setVisible = set_visible
        return self

    def __exit__(self, *_exc):
        QWidget.setVisible = self._original
        return False


class DashboardRefreshStrayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_after_requirement_change_shows_no_floating_widgets(self):
        rows = [{
            'id': 'r1', 'code': 'REQ-1', 'title': '示例', 'status': '已上线', 'online_month': '2025-07',
            'actual_release_date': '2025-07-31', 'needs_interface_update': True,
            'sql_parts': [], 'source_files': [], 'test_points': [],
        }]
        with patch('tools.dashboard_summary.load_requirements', return_value=rows), \
                patch('tools.dashboard_summary.load_release_board', return_value={}), \
                patch('tools.daily_reports.load_reports', return_value={}), \
                patch('panels.dashboard_panel.load_release_board', return_value={}):
            from panels.dashboard_panel import DashboardPanel
            panel = DashboardPanel('zh')
            self.addCleanup(panel.deleteLater)
            self.addCleanup(panel.close)
            panel.resize(1200, 800)
            panel.show()
            self.app.processEvents()
            with StrayWindowSpy('DashboardPanel') as spy:
                panel.refresh(preferred_release_month=None)
                for _ in range(10):
                    self.app.processEvents()
            self.assertEqual(spy.shown, [])


class SharedWidgetStrayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_compact_stepper_suffix_never_floats(self):
        from ui.field_metrics import CompactStepper
        host = QWidget()
        self.addCleanup(host.deleteLater)
        with StrayWindowSpy('QWidget') as spy:
            stepper = CompactStepper(1, 60, 30, suffix=' s')
            stepper.setParent(host)
            self.app.processEvents()
        self.assertEqual(spy.shown, [])


if __name__ == '__main__':
    unittest.main()
