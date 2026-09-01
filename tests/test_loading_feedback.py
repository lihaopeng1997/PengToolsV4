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
from ui.startup_splash import StartupSplash, DEFAULT_SPLASH_DELAY_MS
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
        """<300ms rapid success: busy overlay is never shown to the user."""
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
        self.assertIsNotNone(p._linger_timer)
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
        self.assertIsNotNone(p._delay_timer)
        self.assertTrue(p._delay_timer.isActive())

        # 50ms 时发生网络错误
        p.fail('连接超时，请检查网络')
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertFalse(p.isHidden())
        self.assertEqual(p._state, 'fail')
        self.assertEqual(p._value, 0)
        self.assertIsNone(p._delay_timer)
        self.assertIsNotNone(p._linger_timer)
        self.assertTrue(p._linger_timer.isActive())

    def test_fail_cancels_pending_delayed_show(self):
        """fail() 取消 pending 延迟，且后续触发旧 delayed timer 不会覆盖 fail 状态。"""
        p = AuroraProgress(self.host, delay_show_ms=300, fail_linger_ms=500)
        p.start_busy('任务 A')
        gen_a = p._generation
        p.fail('任务 A 报错')
        self.assertEqual(p._state, 'fail')

        # 尝试使用旧 generation 调用 delayed show callback
        p._on_delay_show_timeout(gen_a)
        self.assertEqual(p._state, 'fail')
        self.assertEqual(p._value, 0)
        self.assertEqual(p._label, '任务 A 报错')

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

    def test_stale_delay_callback_does_not_show_subsequent_pending_task(self):
        """Task A pending -> Task B start -> Task A stale delay callback 绝不提前展示 Task B。"""
        p = AuroraProgress(self.host, delay_show_ms=300)
        p.start_busy('任务 A')
        gen_a = p._generation
        self.assertEqual(gen_a, 1)

        # 紧接着启动任务 B（仍处于 pending 状态）
        p.start_busy('任务 B')
        gen_b = p._generation
        self.assertEqual(gen_b, 2)
        self.assertEqual(p._state, 'pending_busy')
        self.assertFalse(p.is_visible_to_user)

        # 模拟任务 A 的 stale delay callback 触发
        p._on_delay_show_timeout(gen_a)
        self.app.processEvents()

        # 断言：任务 B 依然保持 pending_busy 状态，未被提前展示
        self.assertEqual(p._state, 'pending_busy')
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._label, '任务 B')

    def test_stale_linger_timer_does_not_hide_new_task(self):
        """Task 1 linger callback 绝不能关闭新启动的 Task 2。"""
        p = AuroraProgress(self.host, delay_show_ms=0)
        p.start_busy('任务 1', immediate=True)
        gen1 = p._generation
        p.finish('任务 1 完成')
        gen_finish = p._generation
        self.assertIsNotNone(p._linger_timer)
        self.assertTrue(p._linger_timer.isActive())

        # 立即启动任务 2 并展示
        p.start_busy('任务 2', immediate=True)
        gen2 = p._generation
        self.assertGreater(gen2, gen_finish)
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '任务 2')
        self.assertTrue(p.is_visible_to_user)

        # 模拟任务 1 的旧 linger callback 触发
        p._on_linger_hide(gen_finish)
        self.app.processEvents()

        # 断言：任务 2 必须依然处于 busy 状态且对用户可见，label 保持为任务 2
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '任务 2')
        self.assertTrue(p.is_visible_to_user)
        self.assertFalse(p.isHidden())

    def test_hide_now_cancels_all_timers_and_stale_callbacks_ignored(self):
        """hide_now() 立刻收起并重置所有定时器，旧 callbacks 被完全忽略。"""
        p = AuroraProgress(self.host, delay_show_ms=100)
        p.start_busy('处理中…')
        gen_busy = p._generation
        p.hide_now()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'idle')
        self.assertIsNone(p._delay_timer)
        self.assertIsNone(p._linger_timer)

        # 尝试触发旧 callback
        p._on_delay_show_timeout(gen_busy)
        self.assertEqual(p._state, 'idle')
        self.assertFalse(p.is_visible_to_user)

        p._on_linger_hide(gen_busy)
        self.assertEqual(p._state, 'idle')
        self.assertFalse(p.is_visible_to_user)

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

    def test_fast_startup_splash_never_shown(self):
        """快启动路径（耗时 < 300ms）：StartupSplash 从未对用户可见。"""
        splash = StartupSplash(self.app, delay_ms=300)
        splash.show_status('加载主题…')
        self.app.processEvents()
        self.assertFalse(splash.is_visible_to_user)
        self.assertTrue(splash.isHidden())

        splash.show_status('检查系统代理…')
        self.app.processEvents()
        self.assertFalse(splash.is_visible_to_user)
        self.assertTrue(splash.isHidden())

        dummy_win = QWidget()
        splash.finish(dummy_win)
        self.assertFalse(splash.is_visible_to_user)
        self.assertTrue(splash.isHidden())
        dummy_win.close()

    def test_slow_startup_splash_shown_after_threshold(self):
        """慢启动路径（耗时 >= 300ms）：达到阈值后展示统一品牌闪屏。"""
        splash = StartupSplash(self.app, delay_ms=50)
        # 模拟启动耗时 100ms
        splash._start_time = time.monotonic() - 0.1
        splash.show_status('加载重型模块…')
        self.app.processEvents()
        self.assertTrue(splash.is_visible_to_user)
        self.assertFalse(splash.isHidden())
        self.assertEqual(splash._message, '加载重型模块…')

        dummy_win = QWidget()
        dummy_win.show()
        splash.finish(dummy_win)
        self.app.processEvents()
        self.assertTrue(splash.isHidden())
        dummy_win.close()

    def test_startup_splash_show_status_before_visible_does_not_force_visible(self):
        """在未达到延迟阈值前调用 show_status 不会强制弹出闪屏。"""
        splash = StartupSplash(self.app, delay_ms=500)
        splash.show_status('阶段 2')
        self.assertFalse(splash.is_visible_to_user)
        self.assertEqual(splash._message, '阶段 2')
        splash.close()

    def test_startup_splash_renders_theme_palette(self):
        """验证 StartupSplash 自适应当前主题调色板。"""
        splash = StartupSplash(self.app, delay_ms=0)
        splash.show_status('测试状态…')
        self.app.processEvents()
        img = splash.grab().toImage()
        self.assertFalse(img.isNull())
        splash.close()

    def test_token_based_stale_finish_ignored(self):
        """Task A finish callback with old token is ignored and does not affect Task B."""
        p = AuroraProgress(self.host, delay_show_ms=0)
        token_a = p.start_busy('任务 A', immediate=True)
        self.assertEqual(p._label, '任务 A')

        # 启动任务 B
        token_b = p.start_busy('任务 B', immediate=True)
        self.assertGreater(token_b, token_a)
        self.assertEqual(p._label, '任务 B')
        self.assertEqual(p._state, 'busy')

        # 任务 A 的 late finish 回调到达，携带旧 token
        p.finish('任务 A 完成', token=token_a)
        self.app.processEvents()

        # 断言：任务 B 保持 busy 且 label 仍为任务 B，未被收起或改写为 finish
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '任务 B')
        self.assertTrue(p.is_visible_to_user)

    def test_token_based_stale_fail_ignored(self):
        """Task A fail callback with old token is ignored and does not override Task B."""
        p = AuroraProgress(self.host, delay_show_ms=0)
        token_a = p.start_busy('任务 A', immediate=True)
        token_b = p.start_busy('任务 B', immediate=True)

        # 任务 A 的 late fail 回调到达
        p.fail('任务 A 失败', token=token_a)
        self.app.processEvents()

        # 断言：任务 B 状态保持 busy，不受任务 A 失败影响
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '任务 B')

    def test_hide_event_cancels_pending_delay_timer(self):
        """当宿主被隐藏时，pending 延迟定时器立即取消，切回时不闪现。"""
        p = AuroraProgress(self.host, delay_show_ms=200)
        p.start_busy('等待操作…')
        self.assertEqual(p._state, 'pending_busy')
        self.assertIsNotNone(p._delay_timer)

        # 模拟宿主隐藏（如切页切换 QStackedWidget）
        self.host.hide()
        self.app.processEvents()
        self.assertEqual(p._state, 'idle')
        self.assertIsNone(p._delay_timer)

    def test_same_token_supports_multiple_progress_updates_and_finish(self):
        """同一任务 token 在多次进度刷新和最终 finish 期间保持有效。"""
        p = AuroraProgress(self.host, delay_show_ms=0)
        token = p.start_busy('任务 1', immediate=True)
        self.assertEqual(p.current_token, token)
        self.assertEqual(p._state, 'busy')

        # 连续更新进度
        p.set_progress(10, '解析 10%', token=token)
        self.assertEqual(p._value, 10)
        self.assertEqual(p._state, 'progress')
        self.assertEqual(p.current_token, token)

        p.set_progress(50, '处理 50%', token=token)
        self.assertEqual(p._value, 50)
        self.assertEqual(p.current_token, token)

        p.set_progress(90, '生成 90%', token=token)
        self.assertEqual(p._value, 90)
        self.assertEqual(p.current_token, token)

        # 最终完成
        p.finish('任务 1 完成', token=token)
        self.assertEqual(p._state, 'finish')
        self.assertEqual(p._value, 100)
        self.assertEqual(p._label, '任务 1 完成')

    def test_async_task_overlap_token_isolation(self):
        """Task A starts -> Task B starts -> Task A finishes late -> Task B remains active."""
        p = AuroraProgress(self.host, delay_show_ms=0)
        token_a = p.start_busy('DB 扫描 A', immediate=True)
        token_b = p.start_busy('DB 扫描 B', immediate=True)
        self.assertGreater(token_b, token_a)
        self.assertEqual(p._label, 'DB 扫描 B')
        self.assertEqual(p._state, 'busy')

        # Task A 的晚到成功回调
        p.finish('DB 扫描 A 完成', token=token_a)
        self.app.processEvents()
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, 'DB 扫描 B')
        self.assertTrue(p.is_visible_to_user)

        # Task A 的晚到失败回调
        p.fail('DB 扫描 A 失败', token=token_a)
        self.app.processEvents()
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, 'DB 扫描 B')
        self.assertTrue(p.is_visible_to_user)

        # Task B 正常完成
        p.finish('DB 扫描 B 完成', token=token_b)
        self.app.processEvents()
        self.assertEqual(p._state, 'finish')
        self.assertEqual(p._label, 'DB 扫描 B 完成')

    def test_visible_busy_deactivation_reset(self):
        """已展示的 Busy 浮层在页面切换 reset/hide_now 后彻底清除，旧定时器和回调无法复活。"""
        p = AuroraProgress(self.host, delay_show_ms=0)
        token = p.start_busy('正在执行长任务…', immediate=True)
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._state, 'busy')

        # 页面离开：执行 reset / hide_now
        p.reset()
        self.assertFalse(p.is_visible_to_user)
        self.assertEqual(p._state, 'idle')

        # 旧回调尝试完成
        p.finish('长任务完成', token=token)
        self.assertFalse(p.is_visible_to_user)
        self.assertEqual(p._state, 'idle')

        p.fail('长任务失败', token=token)
        self.assertFalse(p.is_visible_to_user)
        self.assertEqual(p._state, 'idle')


if __name__ == '__main__':
    unittest.main()
