# -*- coding: utf-8 -*-
"""QuickPanel 窗口生命周期 contract（offscreen，不测 Windows 任务栏截图）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QWidget
from ui.quick_panel import QuickPanel


class _Stub(QWidget):
    def navigate_to(self, _index):
        return None

    def showNormal(self):
        return None


class QuickPanelLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_constructor_does_not_show(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        self.assertTrue(panel.isHidden())
        self.assertFalse(panel.isVisible())
        panel.shutdown()
        owner.deleteLater()

    def test_apply_preferences_keeps_hidden(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        self.assertTrue(panel.isHidden())
        panel.apply_preferences(80, False)
        self.assertTrue(panel.isHidden())
        panel.apply_preferences(96, True)
        self.assertTrue(panel.isHidden())
        panel.shutdown()
        owner.deleteLater()

    def test_reset_position_hidden_stays_hidden(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        panel.reset_position()
        self.assertTrue(panel.isHidden())
        panel.shutdown()
        owner.deleteLater()

    def test_show_panel_is_the_display_entry(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        panel.show_panel()
        self.assertFalse(panel.isHidden())
        panel.close_toolbar()
        self.assertTrue(panel.isHidden())
        panel.shutdown()
        owner.deleteLater()

    def test_toggle_expanded_same_object(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        identity = id(panel)
        panel.show_panel()
        win_id = int(panel.winId())
        for _ in range(6):
            panel.toggle_expanded()
        self.assertEqual(id(panel), identity)
        self.assertEqual(int(panel.winId()), win_id)
        panel.shutdown()
        owner.deleteLater()

    def test_tool_flags_not_plain_window(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        flags = panel.windowFlags()
        self.assertTrue(bool(flags & Qt.WindowType.Tool))
        self.assertTrue(bool(flags & Qt.WindowType.FramelessWindowHint))
        self.assertFalse(bool(flags & Qt.WindowType.WindowMaximizeButtonHint))
        panel.shutdown()
        owner.deleteLater()

    def test_position_clamp_uses_available_geometry(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        clamped = panel._clamp_to_available(QPoint(-8000, -8000), 52, 52)
        self.assertGreaterEqual(clamped.x(), -100)
        self.assertGreaterEqual(clamped.y(), -100)
        panel.shutdown()
        owner.deleteLater()
