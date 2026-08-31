# -*- coding: utf-8 -*-
"""Deterministic tests for unified loading, busy, success, failure and splash feedback."""

import os
import sys
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget
from ui.aurora_progress import AuroraProgress, DEFAULT_DELAY_SHOW_MS, DEFAULT_MIN_VISIBLE_MS
from ui.startup_splash import StartupSplash
from ui.theme_manager import ThemeManager


class LoadingFeedbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.host = QWidget()
        self.host.resize(800, 600)
        self.host.show()

    def tearDown(self):
        self.host.close()

    def test_rapid_success_never_shows_busy(self):
        """<300ms rapid success: busy overlay is never shown."""
        p = AuroraProgress(self.host, delay_show_ms=100)
        p.start_busy('快速查询中…')
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'pending_busy')

        # 50ms 之后任务完成（早于 100ms 延迟）
        p.finish('查询完成')
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'idle')

    def test_delayed_show_activates_after_threshold(self):
        """中长任务：超过 delay_show_ms 后统一激活 busy 浮层。"""
        p = AuroraProgress(self.host, delay_show_ms=50)
        p.start_busy('正在扫描庞大数据库…')
        self.assertFalse(p.is_visible_to_user)

        # 触发延迟定时器
        p._delay_timer.timeout.emit()
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertFalse(p.isHidden())
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '正在扫描庞大数据库…')

    def test_minimum_visible_duration_enforced(self):
        """浮层一旦展示，finish 时保障最小可视驻留时长。"""
        p = AuroraProgress(self.host, delay_show_ms=50, min_visible_ms=500, success_linger_ms=100)
        p.start_busy('数据同步…')
        p._delay_timer.timeout.emit()
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)

        # 模拟展示仅 50ms 后调用 finish
        p._shown_timestamp = time.monotonic() - 0.05
        p.finish('同步完成')
        self.app.processEvents()
        # 仍应处于可视状态（等待 linger 定时器）
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._state, 'finish')
        self.assertEqual(p._value, 100)
        self.assertTrue(p._linger_timer.isActive())

        # 触发 linger 定时器后正常收起
        p._linger_timer.timeout.emit()
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'idle')

    def test_fail_immediately_visible(self):
        """fail() 立即展示，取消 pending 延迟。"""
        p = AuroraProgress(self.host, delay_show_ms=300, fail_linger_ms=500)
        p.start_busy('正在连接远程服务器…')
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p._delay_timer.isActive())

        # 50ms 时发生网络错误
        p.fail('连接超时，请检查网络')
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertFalse(p.isHidden())
        self.assertEqual(p._state, 'fail')
        self.assertEqual(p._value, 0)
        self.assertFalse(p._delay_timer.isActive())
        self.assertTrue(p._linger_timer.isActive())

    def test_repeated_start_busy_updates_state_safely(self):
        """多次连续调用 start_busy 不会重叠崩溃，更新最新语义。"""
        p = AuroraProgress(self.host, delay_show_ms=100)
        p.start_busy('任务 A')
        gen1 = p._generation
        p.start_busy('任务 B')
        gen2 = p._generation
        self.assertGreater(gen2, gen1)
        self.assertEqual(p._label, '任务 B')
        self.assertEqual(p._state, 'pending_busy')

    def test_stale_delay_timer_ignored(self):
        """过期的 delay 定时器不会唤醒已被 finish 或 reset 的任务。"""
        p = AuroraProgress(self.host, delay_show_ms=100)
        p.start_busy('已取消的任务')
        p.hide_now()
        self.assertEqual(p._state, 'idle')

        # 假定旧 timer 触发
        p._on_delay_show_timeout()
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())

    def test_stale_linger_timer_does_not_hide_new_task(self):
        """任务 1 的 linger 定时器不会意外关闭任务 2。"""
        p = AuroraProgress(self.host, delay_show_ms=0)
        p.start_busy('任务 1', immediate=True)
        p.finish('任务 1 完成')
        self.assertTrue(p._linger_timer.isActive())

        # 立即启动任务 2
        p.start_busy('任务 2', immediate=True)
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '任务 2')

        # 任务 1 的 linger timer 尝试 hide
        p._on_linger_hide()
        self.assertEqual(p._state, 'idle')
        self.assertFalse(p.is_visible_to_user)

    def test_hide_now_cancels_all_timers_and_hides(self):
        """hide_now() 立刻收起并重置所有定时器与状态。"""
        p = AuroraProgress(self.host, delay_show_ms=100)
        p.start_busy('处理中…')
        p.hide_now()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'idle')
        self.assertFalse(p._delay_timer.isActive())
        self.assertFalse(p._linger_timer.isActive())

    def test_set_progress_shows_explicitly(self):
        """set_progress() 显式百分比进度直接展示。"""
        p = AuroraProgress(self.host, delay_show_ms=300)
        p.set_progress(45, '正在导入数据 45%')
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._value, 45)
        self.assertEqual(p._state, 'progress')

    def test_theme_paint_event_light_and_dark(self):
        """验证 paintEvent 在 Light 和 Dark 两种主题下均能正确渲染不报错。"""
        manager = ThemeManager.instance()
        manager.load_template(PROJECT_DIR)

        # Light 主题
        manager.apply(self.app, 'calm', font_size=12)
        p = AuroraProgress(self.host, delay_show_ms=0)
        p.set_progress(60, '正在处理')
        p.resize(400, 62)
        self.app.processEvents()
        img_light = p.grab().toImage()
        self.assertFalse(img_light.isNull())

        # Dark 主题
        manager.apply(self.app, 'night', font_size=12)
        self.app.processEvents()
        img_dark = p.grab().toImage()
        self.assertFalse(img_dark.isNull())

    def test_startup_splash_renders_theme_palette(self):
        """验证 StartupSplash 自适应当前主题调色板。"""
        splash = StartupSplash(self.app)
        splash.show_status('测试状态…')
        self.app.processEvents()
        img = splash.grab().toImage()
        self.assertFalse(img.isNull())
        splash.close()


if __name__ == '__main__':
    unittest.main()
