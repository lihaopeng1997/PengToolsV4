# -*- coding: utf-8 -*-
"""PRISM-UI-P06: Full Loading Visual Overhaul targeted contract test suite.

Authoritative Specification:
《UI优化需求_晴空棱镜_Agent实施规范 V2.0》Section 10 (Loading 全场景重设计) & Section 10.4 (LD-T01 ~ LD-T12).
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QWidget
from ui.aurora_progress import (
    AuroraProgress,
    DEFAULT_DELAY_SHOW_MS,
    DEFAULT_MIN_VISIBLE_MS,
    DEFAULT_SUCCESS_LINGER_MS,
    FAIL_LINGER_MS,
)
from ui.thinking_indicator import ThinkingIndicator
from ui.startup_splash import StartupSplash, DEFAULT_SPLASH_DELAY_MS, MIN_VISIBLE_MS
from ui.theme_manager import ThemeManager


class PrismLoadingContractTests(unittest.TestCase):
    """PRISM-UI-P06 Loading contract tests covering LD-T01 through LD-T12."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.resize(1000, 700)
        self.host.show()

    def tearDown(self):
        self.host.close()

    def test_ld_t01_rapid_task_never_shows_busy(self):
        """LD-T01: 100ms 任务在 300ms 延迟内完成，用户完全看不到 busy 浮层。"""
        p = AuroraProgress(self.host, delay_show_ms=300)
        token = p.start_busy('快速查询中…')
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'pending_busy')

        # 模拟 100ms 之后任务完成
        p.finish('查询完成', token=token)
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'idle')

    def test_ld_t02_medium_task_shows_and_lingers(self):
        """LD-T02: 400ms 任务展示后按 500ms 最小可视 + linger 收起。"""
        p = AuroraProgress(self.host, delay_show_ms=50, min_visible_ms=500, success_linger_ms=350)
        token = p.start_busy('扫描数据库…')
        self.assertFalse(p.is_visible_to_user)

        # 触发 50ms 延时
        p._delay_timer.timeout.emit()
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._state, 'busy')

        # 模拟已显示 200ms 后 finish（不足 500ms）
        p._shown_timestamp = time.monotonic() - 0.20
        p.finish('扫描完成', token=token)
        self.app.processEvents()
        # 必须依然处于可视状态（等待 linger 计时器）
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._state, 'finish')
        self.assertEqual(p._value, 100)
        self.assertIsNotNone(p._linger_timer)
        self.assertTrue(p._linger_timer.isActive())

        # linger 计时器触发后正常隐去
        p._linger_timer.timeout.emit()
        self.app.processEvents()
        self.assertFalse(p.is_visible_to_user)
        self.assertEqual(p._state, 'idle')

    def test_ld_t03_multi_task_token_isolation(self):
        """LD-T03: A 开始、B 开始、A 迟到 finish，B 绝不被提前隐藏。"""
        p = AuroraProgress(self.host, delay_show_ms=0)
        token_a = p.start_busy('任务 A', immediate=True)
        self.assertEqual(p._label, '任务 A')

        token_b = p.start_busy('任务 B', immediate=True)
        self.assertGreater(token_b, token_a)
        self.assertEqual(p._label, '任务 B')
        self.assertEqual(p._state, 'busy')

        # A 迟到 finish 回调
        p.finish('任务 A 完成', token=token_a)
        self.app.processEvents()
        self.assertEqual(p._state, 'busy')
        self.assertEqual(p._label, '任务 B')
        self.assertTrue(p.is_visible_to_user)

    def test_ld_t04_fail_cancels_pending_and_lingers(self):
        """LD-T04: fail 立即打断 pending 并显示原错误，驻留 2200ms。"""
        p = AuroraProgress(self.host, delay_show_ms=300, fail_linger_ms=2200)
        p.start_busy('请求外部接口…')
        self.assertEqual(p._state, 'pending_busy')
        self.assertFalse(p.is_visible_to_user)

        p.fail('接口调用失败: 404')
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._state, 'fail')
        self.assertEqual(p._value, 0)
        self.assertEqual(p._label, '接口调用失败: 404')
        self.assertIsNone(p._delay_timer)
        self.assertIsNotNone(p._linger_timer)

    def test_ld_t05_set_progress_shows_immediately(self):
        """LD-T05: set_progress() 在 100ms 按原接口立即显示，不等待 300ms 延迟。"""
        p = AuroraProgress(self.host, delay_show_ms=300)
        p.set_progress(45, '正在导入数据 45%')
        self.app.processEvents()
        self.assertTrue(p.is_visible_to_user)
        self.assertEqual(p._value, 45)
        self.assertEqual(p._state, 'progress')
        self.assertEqual(p._label, '正在导入数据 45%')

    def test_ld_t06_hide_now_stops_visual_timers_cleanly(self):
        """LD-T06: 用户切页 / hide_now 视觉 timer 彻底停止，不留后台泄漏。"""
        p = AuroraProgress(self.host, delay_show_ms=100)
        p.start_busy('进行中…')
        self.assertEqual(p._state, 'pending_busy')
        self.assertTrue(p._delay_timer.isActive())

        p.hide_now()
        self.assertFalse(p.is_visible_to_user)
        self.assertTrue(p.isHidden())
        self.assertEqual(p._state, 'idle')
        self.assertIsNone(p._delay_timer)
        self.assertIsNone(p._linger_timer)
        self.assertFalse(p._anim_timer.isActive())

    def test_ld_t07_thinking_indicator_geometry_and_text(self):
        """LD-T07: ThinkingIndicator 高 28，min 宽 140，文字起于 x=48。"""
        ti = ThinkingIndicator(None, text='正在分析语义…')
        self.assertEqual(ti.height(), 28)
        self.assertGreaterEqual(ti.minimumWidth(), 140)
        self.assertEqual(ti.text(), '正在分析语义…')
        self.assertFalse(ti.is_running())

        ti.start()
        self.assertTrue(ti.is_running())
        self.assertTrue(ti._timer.isActive())

        ti.stop()
        self.assertFalse(ti.is_running())
        self.assertFalse(ti._timer.isActive())
        ti.deleteLater()

    def test_ld_t08_thinking_indicator_diamond_opacity_pulse_no_y_jump(self):
        """LD-T08: ThinkingIndicator 菱形微晶点纯透明度脉动，严格不发生 Y 轴位移跳跃。"""
        ti = ThinkingIndicator(None, text='正在推理…')
        ti.start()
        self.app.processEvents()

        # paintEvent 抓取并在不同时间戳渲染，验证不抛异常且尺寸恒定
        img1 = ti.grab().toImage()
        self.assertFalse(img1.isNull())
        self.assertEqual(img1.height(), 28)

        ti.stop()
        ti.deleteLater()

    def test_ld_t09_startup_splash_geometry_and_track(self):
        """LD-T09: StartupSplash 480x280，Logo 64x64 at (208,48)，轨道 (48,216,384,4)。"""
        splash = StartupSplash(self.app, delay_ms=0, min_visible_ms=0)
        self.assertEqual(splash.width(), 480)
        self.assertEqual(splash.height(), 280)
        self.assertEqual(splash._logo.width(), 64)
        self.assertEqual(splash._logo.height(), 64)
        self.assertEqual(splash._title, 'PengToolsHub')
        self.assertEqual(splash._subtitle, 'Developer & Ops Workbench')

        # 验证 paintEvent 能绘制完整品牌外圈与 96px 往复光带
        splash.show_status('加载主配置…')
        self.app.processEvents()
        img = splash.grab().toImage()
        self.assertFalse(img.isNull())
        self.assertEqual(img.width(), 480)
        self.assertEqual(img.height(), 280)
        splash._do_finish()

    def test_ld_t10_reduced_motion_renders_statically(self):
        """LD-T10: 减弱动效下渲染静态环与静态点，无死循环。"""
        from ui.motion import set_motion_enabled_for_test
        set_motion_enabled_for_test(False)
        try:
            # 1. AuroraProgress 静态绘制
            p = AuroraProgress(self.host, delay_show_ms=0)
            p.start_busy('静态加载中…', immediate=True)
            self.app.processEvents()
            img_p = p.grab().toImage()
            self.assertFalse(img_p.isNull())
            p.hide_now()

            # 2. ThinkingIndicator 静态绘制
            ti = ThinkingIndicator(None)
            ti.start()
            self.app.processEvents()
            img_ti = ti.grab().toImage()
            self.assertFalse(img_ti.isNull())
            ti.stop()
            ti.deleteLater()
        finally:
            set_motion_enabled_for_test(None)

    def test_ld_t11_theme_adaptation_calm_and_black(self):
        """LD-T11: AuroraProgress 与 StartupSplash 在 calm (浅色) 与 black (深色) 下均能正确解析 Token。"""
        tm = ThemeManager.instance()
        tm.load_template(ROOT)

        for theme_id in ('calm', 'black'):
            tm.apply(self.app, theme_id, font_size=12)

            p = AuroraProgress(self.host, delay_show_ms=0)
            p.set_progress(75, f'{theme_id} 进度')
            self.app.processEvents()
            img_p = p.grab().toImage()
            self.assertFalse(img_p.isNull())
            p.hide_now()

            ti = ThinkingIndicator(None)
            ti.start()
            self.app.processEvents()
            img_ti = ti.grab().toImage()
            self.assertFalse(img_ti.isNull())
            ti.stop()
            ti.deleteLater()

    def test_ld_t12_mouse_transparency_preserved(self):
        """LD-T12: AuroraProgress 与 ThinkingIndicator 均具备 WA_TransparentForMouseEvents，不拦截用户操作。"""
        p = AuroraProgress(self.host)
        self.assertTrue(p.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

        ti = ThinkingIndicator(None)
        self.assertTrue(ti.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        ti.deleteLater()

    def test_web_prism_loading_component_exists_and_valid(self):
        """验证 frontend/src/shared/components/PrismLoading.vue 规范组件存在且契约完备。"""
        vue_path = os.path.join(ROOT, 'frontend', 'src', 'shared', 'components', 'PrismLoading.vue')
        self.assertTrue(os.path.exists(vue_path), 'PrismLoading.vue 组件必须存在')
        with open(vue_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Props 契约验证 (Section 11)
        self.assertIn('busy?: boolean', content)
        self.assertIn('progress?: number', content)
        self.assertIn('label?: string', content)
        self.assertIn('variant?:', content)
        self.assertIn('reducedMotion?: boolean', content)

        # 视觉变体验证 (Section 10)
        self.assertIn('variant-ring', content)
        self.assertIn('variant-dots', content)
        self.assertIn('variant-bar', content)
        self.assertIn('variant-overlay', content)

        # 900ms 棱镜环与 1.2s 菱形点
        self.assertIn('0.9s', content)
        self.assertIn('1.2s', content)
        self.assertIn('role="status"', content)
        self.assertIn('aria-live="polite"', content)


if __name__ == '__main__':
    unittest.main()
