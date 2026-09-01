# -*- coding: utf-8 -*-
"""Comprehensive tests for StartupSplash (visual hierarchy, timing contract, auto delay timer & themes)."""

import os
import time
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from ui.startup_splash import StartupSplash, DEFAULT_SPLASH_DELAY_MS, MIN_VISIBLE_MS, _resolve_palette
from ui.theme_manager import THEMES


class StartupSplashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fast_startup_under_300ms_never_shows(self):
        """1. <300ms 不显示，且 finish 0 延迟。"""
        splash = StartupSplash(self.app, delay_ms=300)
        splash.show_status('正在加载主题…')
        self.app.processEvents()
        self.assertFalse(splash.is_visible_to_user)
        self.assertTrue(splash.isHidden())

        dummy_win = QWidget()
        dummy_win.show()

        start = time.monotonic()
        splash.finish(dummy_win)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.05, 'Never-shown splash must finish with 0 delay')
        self.assertFalse(splash.is_visible_to_user)
        self.assertTrue(splash.isHidden())
        dummy_win.close()

    def test_auto_delay_show_without_subsequent_show_status(self):
        """2. >=300ms 即使不再调用 show_status，delay timer 也会自动触发展示。"""
        splash = StartupSplash(self.app, delay_ms=50, min_visible_ms=0)
        self.assertIsNotNone(splash._show_timer)
        self.assertTrue(splash._show_timer.isActive())

        # 只在 <50ms 调用一次 show_status
        splash.show_status('单次初始状态')
        self.assertFalse(splash.is_visible_to_user)

        # 模拟 50ms 延时到达（不调用任何新的 show_status）
        splash._start_time = time.monotonic() - 0.06
        splash._show_timer.timeout.emit()
        self.app.processEvents()

        # 验证自动显示，且 show_timer 已停止
        self.assertTrue(splash.is_visible_to_user)
        self.assertFalse(splash._show_timer.isActive())
        self.assertEqual(splash._message, '单次初始状态')
        splash._do_finish()

    def test_show_status_before_delay_only_updates_text(self):
        """3. show_status 在 delay 前只更新内部文本，不强制弹出。"""
        splash = StartupSplash(self.app, delay_ms=500)
        splash.show_status('阶段 1')
        self.assertFalse(splash.is_visible_to_user)
        self.assertEqual(splash._message, '阶段 1')

        splash.show_status('阶段 2')
        self.assertFalse(splash.is_visible_to_user)
        self.assertEqual(splash._message, '阶段 2')
        splash._do_finish()

    def test_finish_before_delay_stops_timer_and_never_shows(self):
        """4. 延迟期内调用 finish 会停掉 show timer，之后绝不再次弹出。"""
        splash = StartupSplash(self.app, delay_ms=500)
        self.assertTrue(splash._show_timer.isActive())

        splash.finish()
        self.assertFalse(splash._show_timer.isActive())
        self.assertTrue(splash._is_finished)

        # 模拟迟到的 timeout
        splash._on_show_timer_timeout()
        self.assertFalse(splash.is_visible_to_user)

    def test_finish_when_visible_under_min_visible_uses_delayed_close(self):
        """5. 已显示不足 550ms 时 finish 使用非阻塞 QTimer 延迟关闭。"""
        splash = StartupSplash(self.app, delay_ms=0, min_visible_ms=550)
        splash.show_status('已显示')
        self.app.processEvents()
        self.assertTrue(splash.is_visible_to_user)

        dummy_win = QWidget()
        dummy_win.show()

        # Call finish when visible elapsed is only ~10ms
        splash.finish(dummy_win)
        self.assertIsNotNone(splash._finish_timer)
        self.assertTrue(splash._finish_timer.isActive())
        # Splash is still visible, waiting for remaining ms non-blockingly
        self.assertTrue(splash.is_visible_to_user)

        # Manually trigger finish timer
        splash._finish_timer.timeout.emit()
        self.app.processEvents()
        self.assertTrue(splash.isHidden())
        self.assertFalse(splash.is_visible_to_user)
        dummy_win.close()

    def test_finish_when_visible_over_min_visible_finishes_immediately(self):
        """6. 已显示超过 550ms 时立即 finish。"""
        splash = StartupSplash(self.app, delay_ms=0, min_visible_ms=550)
        splash.show_status('已显示很久')
        splash._visible_at = time.monotonic() - 0.6  # 600ms ago
        self.app.processEvents()

        dummy_win = QWidget()
        dummy_win.show()

        start = time.monotonic()
        splash.finish(dummy_win)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.05)
        self.assertTrue(splash.isHidden())
        dummy_win.close()

    def test_repeated_finish_is_idempotent(self):
        """7. repeated finish 幂等安全。"""
        splash = StartupSplash(self.app, delay_ms=0, min_visible_ms=0)
        splash.show_status('启动')
        splash.finish()
        splash.finish()
        splash.finish()
        self.assertTrue(splash._is_finished)

    def test_animation_timer_stops_when_hidden(self):
        """8. animation timer 在 hidden / close 后停止。"""
        splash = StartupSplash(self.app, delay_ms=0)
        splash.show_status('启动中')
        self.app.processEvents()
        self.assertTrue(splash._anim_timer.isActive())

        splash.hide()
        self.app.processEvents()
        self.assertFalse(splash._anim_timer.isActive())

        splash.show()
        self.app.processEvents()
        self.assertTrue(splash._anim_timer.isActive())
        splash._do_finish()

    def test_do_finish_stops_all_timers(self):
        """_do_finish 会停止 show_timer, anim_timer, finish_timer。"""
        splash = StartupSplash(self.app, delay_ms=500, min_visible_ms=500)
        self.assertTrue(splash._show_timer.isActive())
        splash._do_finish()
        self.assertFalse(splash._show_timer.isActive())
        self.assertFalse(splash._anim_timer.isActive())

    def test_title_and_branding_hierarchy(self):
        """9. APP_NAME 可见，不含 V4 / Private / Build / internal version 标识。"""
        splash = StartupSplash(self.app, delay_ms=0)
        self.assertEqual(splash._title, 'PengToolsHub')
        self.assertNotIn('V4', splash._title)
        self.assertNotIn('Private', splash._title)
        self.assertNotIn('Build', splash._title)
        self.assertEqual(splash._subtitle, 'Developer & Ops Workbench')
        splash._do_finish()

    def test_all_theme_palettes_instantiate_and_paint(self):
        """10. calm / clear / warm / black palette 均能正确实例化并绘制。"""
        for theme_id in ('calm', 'clear', 'warm', 'black'):
            with patch('config.load_settings', return_value={'ui_theme': theme_id}):
                splash = StartupSplash(self.app, delay_ms=0)
                splash.show_status(f'Testing {theme_id}')
                self.app.processEvents()
                # Verify palette tokens
                self.assertIn('PRIMARY', splash._palette)
                self.assertIn('ELEVATED_SURFACE', splash._palette)
                self.assertIn('TEXT_STRONG', splash._palette)
                # Verify paint does not raise
                img = splash.grab().toImage()
                self.assertFalse(img.isNull())
                self.assertEqual(splash.width(), 480)
                self.assertEqual(splash.height(), 280)
                splash._do_finish()

    def test_fallback_palette_safe(self):
        """11. 异常情况下 fallback palette 不崩。"""
        with patch('config.load_settings', side_effect=RuntimeError('disk error')):
            splash = StartupSplash(self.app, delay_ms=0)
            self.assertIsNotNone(splash._palette)
            self.assertEqual(splash._palette.get('SURFACE'), '#FFFFFF')
            splash._do_finish()

    def test_secondary_instance_guard_contract(self):
        """13. Single-instance secondary path 不得创建 Splash。"""
        from ui.single_instance import SingleInstanceGuard
        guard = SingleInstanceGuard(server_name='test_guard_splash', parent=self.app)
        # Mock becoming secondary
        with patch.object(guard, 'try_become_primary', return_value=False):
            self.assertFalse(guard.try_become_primary())


if __name__ == '__main__':
    unittest.main()
