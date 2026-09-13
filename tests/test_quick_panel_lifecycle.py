# -*- coding: utf-8 -*-
"""QuickPanel 窗口生命周期 contract（offscreen，不测 Windows 任务栏截图）。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
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

    def test_expanded_modes_fit_simulated_screen_corners(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        try:
            for area in (QRect(0, 0, 960, 640), QRect(-1280, -200, 1280, 800)):
                screen = Mock()
                screen.availableGeometry.return_value = area
                with patch.object(QApplication, 'screenAt', return_value=screen):
                    for anchor in (area.topLeft(), QPoint(area.right() - 51, area.top()),
                                   QPoint(area.left(), area.bottom() - 51),
                                   QPoint(area.right() - 51, area.bottom() - 51)):
                        for mode in ('tools', 'chat'):
                            panel._set_mode(mode)
                            panel.setGeometry(anchor.x(), anchor.y(), 52, 52)
                            panel.toggle_expanded()
                            self.assertTrue(area.contains(panel.geometry()), (area, mode, panel.geometry()))
                            panel.toggle_expanded()
                            self.assertEqual(panel.geometry(), QRect(anchor.x(), anchor.y(), 52, 52))
        finally:
            panel.shutdown()
            owner.deleteLater()

    def test_one_hundred_expansions_reuse_window_and_timers(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        try:
            panel.show_panel()
            self.app.processEvents()
            panel.toggle_expanded()
            window_id = int(panel.winId())
            timers = panel.findChildren(QTimer)
            timer_ids = {id(timer) for timer in timers}
            anchor = QPoint(panel.pos())
            panel.chat_input.setText('DEMO unsent draft')
            for _ in range(100):
                panel.toggle_expanded()
                self.app.processEvents()
                panel.toggle_expanded()
                self.app.processEvents()
                self.assertEqual(int(panel.winId()), window_id)
                self.assertEqual(panel.pos(), anchor)
                self.assertEqual({id(timer) for timer in panel.findChildren(QTimer)}, timer_ids)
            self.assertEqual(panel.chat_input.text(), 'DEMO unsent draft')
            self.assertFalse(panel._learn_search_debounce.isActive())
        finally:
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

    def test_compact_geometry_and_single_icon_spec(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        self.assertEqual(panel.COMPACT_SIZE, (52, 52))
        self.assertEqual(panel.BUTTON_SIZE, 44)
        self.assertEqual(panel.BUTTON_MARGIN, 4)
        geom = panel.toggle_btn.geometry()
        self.assertEqual((geom.x(), geom.y(), geom.width(), geom.height()), (4, 4, 44, 44))
        self.assertEqual(panel.toggle_btn.iconSize().width(), 30)
        self.assertEqual(panel.toggle_btn.iconSize().height(), 30)
        self.assertEqual(panel.toggle_btn.text(), '')
        self.assertFalse(panel.toggle_btn.icon().isNull())
        panel.shutdown()
        owner.deleteLater()

    def test_expanded_geometry_and_modes(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        self.assertEqual(panel.PANEL_WIDTH, 300)
        self.assertEqual(panel.LEARN_PANEL_WIDTH, 360)
        self.assertEqual(panel.CHAT_PANEL_WIDTH, 340)
        self.assertEqual(panel.CHAT_PANEL_HEIGHT, 440)
        self.assertEqual(panel.CARD_HEIGHT, 58)
        self.assertEqual(panel.GRID_GAP, 8)
        self.assertEqual(panel.PANEL_PAD, 12)

        # Tools mode nominal expanded size
        w, h = panel._expanded_size()
        self.assertEqual(w, 316)  # 300 + 16

        # Actual Qt layout geometry assertions (PRISM-UI-P08-FIX-1)
        panel.show_panel()
        self.app.processEvents()

        # 1. Shell and content container widths
        self.assertEqual(panel.shell.width(), 300)
        self.assertEqual(panel.tools.width(), 300)
        self.assertEqual(panel.header_bar.height(), 48)
        self.assertEqual(panel.footer_bar.height(), 40)

        # 2. Content padding
        layout_margins = panel.tools.layout().contentsMargins()
        self.assertEqual(layout_margins.left(), 12)
        self.assertEqual(layout_margins.right(), 12)
        self.assertEqual(layout_margins.top(), 12)
        self.assertEqual(layout_margins.bottom(), 12)
        self.assertEqual(panel.grid_host.width(), 300 - 12 - 12)  # 276

        # 3. Card dimensions: 134px width, 58px height, 8px gap
        self.assertGreaterEqual(len(panel.tool_buttons), 2)
        btn0 = panel.tool_buttons[0]
        btn1 = panel.tool_buttons[1]
        self.assertEqual(btn0.width(), 134)
        self.assertEqual(btn1.width(), 134)
        self.assertEqual(btn0.height(), 58)
        self.assertEqual(btn1.height(), 58)
        gap = btn1.geometry().x() - (btn0.geometry().x() + btn0.width())
        self.assertEqual(gap, 8)

        # Chat mode expanded size
        panel._set_mode('chat')
        w_chat, h_chat = panel._expanded_size()
        self.assertEqual(w_chat, 356)  # 340 + 16
        self.assertEqual(h_chat, 456)  # 440 + 16
        self.app.processEvents()
        self.assertEqual(panel.shell.width(), 340)
        self.assertEqual(panel.tools.width(), 340)
        self.assertFalse(panel.footer_bar.isVisible())

        # Learn mode expanded size
        panel._set_mode('tools')
        panel._open_learning_search()
        w_learn, h_learn = panel._expanded_size()
        self.assertEqual(w_learn, 376)  # 360 + 16
        self.assertEqual(h_learn, 536)  # 520 + 16
        self.app.processEvents()
        self.assertEqual(panel.shell.width(), 360)
        self.assertEqual(panel.tools.width(), 360)

        panel.shutdown()
        owner.deleteLater()

    def test_floating_shortcuts_editor_dialog_contract(self):
        from ui.floating_shortcuts_editor import FloatingShortcutsEditor
        editor = FloatingShortcutsEditor({'floating_shortcuts': [0, 1, 2, 3]}, language='zh')
        self.assertTrue(editor.isModal())
        self.assertGreaterEqual(editor.minimumWidth(), 440)
        self.assertGreaterEqual(editor.minimumHeight(), 360)
        self.assertLessEqual(editor.width(), 520)
        self.assertLessEqual(editor.height(), 480)
        editor.show()
        editor.close()
        editor.deleteLater()

    def test_result_preview_title_and_actions_fit(self):
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=16)
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        try:
            panel.show_panel()
            for index in (1, 4):
                panel._open_result_preview(index)
                self.app.processEvents()
                self.assertTrue(panel.tools.rect().contains(panel.preview.geometry()))
                self.assertGreaterEqual(panel.preview_title.width(), panel.preview_title.sizeHint().width())
                for button in (panel.preview_back, panel.preview_gen_personal,
                               panel.preview_gen_unit, panel.preview_gen_vin):
                    if button.isVisible():
                        self.assertGreaterEqual(button.width(), button.sizeHint().width())
                        rect = QRect(button.mapTo(panel.preview, QPoint()), button.size())
                        self.assertTrue(panel.preview.rect().contains(rect), button.text())
                with patch.object(panel, '_generate_from_preview') as generate:
                    if index == 1:
                        panel.preview_gen_personal.click()
                        generate.assert_called_with('personal')
                        panel.preview_gen_unit.click()
                        generate.assert_called_with('unit')
                    else:
                        panel.preview_gen_vin.click()
                        generate.assert_called_once_with('vin')
                panel.preview_back.click()
                self.app.processEvents()
                self.assertFalse(panel.preview.isVisible())
                self.assertTrue(panel.grid_host.isVisible())
        finally:
            panel.shutdown()
            owner.deleteLater()

    def test_chat_waiting_animation_follows_original_completion_and_visibility(self):
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        try:
            panel.show_panel()
            panel._set_mode('chat')
            panel._sync_chat_running_state(True)
            self.app.processEvents()
            self.assertTrue(panel.chat_thinking.isVisible())
            self.assertTrue(panel.chat_thinking._timer.isActive())
            panel.toggle_expanded()
            self.app.processEvents()
            self.assertFalse(panel.chat_thinking._timer.isActive())
            self.assertTrue(panel.compact_wait_ring.isVisible())
            self.assertTrue(panel.compact_wait_ring._timer.isActive())
            self.assertEqual(panel.compact_wait_ring.geometry(), QRect(6, 6, 32, 32))
            panel.toggle_expanded()
            self.app.processEvents()
            self.assertTrue(panel.chat_thinking._timer.isActive())
            self.assertFalse(panel.compact_wait_ring._timer.isActive())
            panel._on_chat_completed('DEMO reply')
            self.assertFalse(panel.chat_thinking.isVisible())
            self.assertFalse(panel.chat_thinking._timer.isActive())
            self.assertIn('DEMO reply', panel.chat_history.toPlainText())
            self.assertFalse(panel.compact_wait_ring._running)
            panel._sync_chat_running_state(True)
            panel._on_chat_failed('DEMO error')
            self.assertFalse(panel.chat_thinking._timer.isActive())
            panel._sync_chat_running_state(True)
            worker = Mock()
            worker.isRunning.return_value = True
            panel._chat_worker = worker
            panel.chat_send_btn.click()
            self.assertTrue(worker.cancelled)
            self.assertFalse(panel.chat_thinking._timer.isActive())
            panel._sync_chat_running_state(True)
            panel.close_toolbar()
            self.assertFalse(panel.chat_thinking._timer.isActive())
            panel._sync_chat_running_state(True)
            self.assertTrue(panel.chat_thinking.is_running())
            self.assertFalse(panel.chat_thinking._timer.isActive())
        finally:
            panel.shutdown()
            owner.deleteLater()

    def test_compact_wait_ring_frames_and_reduced_motion(self):
        from ui.motion import set_motion_enabled_for_test
        owner = _Stub()
        panel = QuickPanel(owner, 'zh')
        try:
            set_motion_enabled_for_test(True)
            panel.show_panel()
            panel.toggle_expanded()
            panel._sync_chat_running_state(True)
            self.app.processEvents()
            ring = panel.compact_wait_ring
            self.assertTrue(ring.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            with patch('ui.brand_wait_ring.time.monotonic', return_value=ring._started + .1):
                first = ring.grab().toImage()
            with patch('ui.brand_wait_ring.time.monotonic', return_value=ring._started + .4):
                second = ring.grab().toImage()
            self.assertNotEqual(first, second)
            set_motion_enabled_for_test(False)
            ring._tick()
            self.assertFalse(ring._timer.isActive())
            with patch('ui.brand_wait_ring.time.monotonic', return_value=ring._started + .1):
                first = ring.grab().toImage()
            with patch('ui.brand_wait_ring.time.monotonic', return_value=ring._started + .4):
                second = ring.grab().toImage()
            self.assertEqual(first, second)
            self.assertFalse(panel.expanded)
            self.assertEqual(panel.size().width(), 52)
        finally:
            set_motion_enabled_for_test(None)
            panel.shutdown()
            owner.deleteLater()
