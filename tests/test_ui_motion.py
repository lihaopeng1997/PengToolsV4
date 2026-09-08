# -*- coding: utf-8 -*-
"""微动效与交互基础单元测试：
- Motion Tokens 常量与适配时长 duration()
- motion_enabled() 开关与测试模式隔离
- 弹窗轻量入场动画 play_dialog_enter（复用 child、无累积、中断 target 正确）
- ThinkingIndicator 资源与定时器生命周期清理（hideEvent / closeEvent）
- AuroraProgress 资源与定时器生命周期清理（busy/finish/pending/close/hideEvent 全面收敛）
- QSS 核心控件按压/焦点/禁用态完备性
- Web Dashboard role="button" 键盘 Enter + Space 契约与 demo 行交互语义
"""

from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class MotionTokensTests(unittest.TestCase):
    def tearDown(self):
        from ui.motion import set_motion_enabled_for_test
        set_motion_enabled_for_test(None)

    def test_motion_tokens_constants(self):
        from ui.motion import (
            DURATION_ENTER,
            DURATION_FAST,
            DURATION_INSTANT,
            DURATION_MAX,
            DURATION_STANDARD,
        )
        self.assertEqual(DURATION_INSTANT, 70)
        self.assertEqual(DURATION_FAST, 100)
        self.assertEqual(DURATION_STANDARD, 150)
        self.assertEqual(DURATION_ENTER, 180)
        self.assertEqual(DURATION_MAX, 220)
        self.assertLessEqual(DURATION_STANDARD, 200)
        self.assertLessEqual(DURATION_FAST, 200)

    def test_motion_enabled_in_offscreen_mode(self):
        from ui.motion import duration, motion_enabled, set_motion_enabled_for_test
        set_motion_enabled_for_test(None)
        # 默认 offscreen 模式下禁用动效，确保测试无等待与确定性
        if os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
            self.assertFalse(motion_enabled())
            self.assertEqual(duration(150), 0)

    def test_motion_override_for_test(self):
        from ui.motion import duration, motion_enabled, set_motion_enabled_for_test
        set_motion_enabled_for_test(True)
        self.assertTrue(motion_enabled())
        self.assertEqual(duration(150), 150)
        self.assertEqual(duration(0), 0)

        set_motion_enabled_for_test(False)
        self.assertFalse(motion_enabled())
        self.assertEqual(duration(150), 0)


class DialogMotionTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        from ui.motion import set_motion_enabled_for_test
        set_motion_enabled_for_test(None)

    def test_play_dialog_enter_disabled_in_offscreen(self):
        from PyQt6.QtWidgets import QDialog
        from ui.motion import play_dialog_enter, set_motion_enabled_for_test
        set_motion_enabled_for_test(False)
        dlg = QDialog()
        anim = play_dialog_enter(dlg)
        self.assertIsNone(anim)
        dlg.deleteLater()

    def test_play_dialog_enter_does_not_accumulate_children_and_preserves_target(self):
        from PyQt6.QtCore import QPoint, QPropertyAnimation
        from PyQt6.QtWidgets import QDialog
        from ui.motion import play_dialog_enter, set_motion_enabled_for_test
        set_motion_enabled_for_test(True)
        dlg = QDialog()
        dlg.move(120, 180)
        target = dlg.pos()

        # 首次触发
        anim1 = play_dialog_enter(dlg, offset_y=4)
        self.assertIsNotNone(anim1)
        self.assertIsInstance(anim1, QPropertyAnimation)
        self.assertEqual(anim1.propertyName(), b'pos')
        self.assertEqual(anim1.duration(), 150)
        self.assertEqual(anim1.parent(), dlg)
        self.assertEqual(anim1.endValue(), target)
        self.assertEqual(anim1.startValue(), target + QPoint(0, 4))

        anims1 = dlg.findChildren(QPropertyAnimation)
        self.assertEqual(len(anims1), 1)

        # 连续触发：复用同一动画，子对象数量严格保持为 1，且 target 正确
        anim2 = play_dialog_enter(dlg, offset_y=4)
        self.assertIs(anim1, anim2)
        anims2 = dlg.findChildren(QPropertyAnimation)
        self.assertEqual(len(anims2), 1)
        self.assertEqual(anim2.endValue(), target)
        dlg.deleteLater()

    def test_dialogs_show_event_integration(self):
        from PyQt6.QtGui import QShowEvent
        from ui.confirm_dialog import (
            AppNoticeDialog,
            CloseActionDialog,
            ConfirmActionDialog,
            HttpsCertConsentDialog,
            NextStepDialog,
        )

        dlgs = [
            ConfirmActionDialog('确认标题', '确认内容'),
            CloseActionDialog('zh', 'minimize'),
            AppNoticeDialog('提示', '内容', kind='info'),
            NextStepDialog('推荐下一步', '提示', [('action1', '执行', True)]),
            HttpsCertConsentDialog(language='zh', for_listen=True),
        ]
        event = QShowEvent()
        for dlg in dlgs:
            dlg.showEvent(event)
            dlg.deleteLater()


class IndicatorLifecycleTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_thinking_indicator_lifecycle(self):
        from PyQt6.QtGui import QCloseEvent, QHideEvent, QShowEvent
        from ui.thinking_indicator import ThinkingIndicator

        w = ThinkingIndicator(None, text='正在推理...')
        self.assertFalse(w.is_running())
        w.start()
        self.assertTrue(w.is_running())
        self.assertTrue(w._timer.isActive())

        # hideEvent 仅暂停绘制定时器，业务 is_running 严格保持 True
        w.hideEvent(QHideEvent())
        self.assertTrue(w.is_running())
        self.assertFalse(w._timer.isActive())

        # showEvent 恢复绘制定时器
        w.showEvent(QShowEvent())
        self.assertTrue(w.is_running())
        self.assertTrue(w._timer.isActive())

        # 显式业务 stop() 才真正置 is_running = False
        w.stop()
        self.assertFalse(w.is_running())
        self.assertFalse(w._timer.isActive())

        # 重启并 closeEvent 自动 stop
        w.start()
        self.assertTrue(w.is_running())
        w.closeEvent(QCloseEvent())
        self.assertFalse(w.is_running())
        self.assertFalse(w._timer.isActive())
        w.deleteLater()

    def test_aurora_progress_busy_hide_anim_timer_stopped(self):
        from PyQt6.QtGui import QHideEvent
        from ui.aurora_progress import AuroraProgress

        p = AuroraProgress(None, delay_show_ms=0)
        p.start_busy('处理中', immediate=True)
        self.assertTrue(p._anim_timer.isActive())

        # busy 显示后 hideEvent：_anim_timer 停止，保持后台状态机
        p.hideEvent(QHideEvent())
        self.assertFalse(p._anim_timer.isActive())
        p.deleteLater()

    def test_aurora_progress_finish_linger_hide_keeps_linger_timer(self):
        from PyQt6.QtGui import QHideEvent
        from ui.aurora_progress import AuroraProgress

        p = AuroraProgress(None, delay_show_ms=0)
        p.start_busy('处理中', immediate=True)
        p.finish('完成')
        # finish 触发后存在 linger_timer
        self.assertIsNotNone(p._linger_timer)
        self.assertTrue(p._linger_timer.isActive())

        # hideEvent 仅停止 anim_timer，linger_timer 必须继续存活以保障状态机最终收敛
        p.hideEvent(QHideEvent())
        self.assertFalse(p._anim_timer.isActive())
        self.assertIsNotNone(p._linger_timer)
        self.assertTrue(p._linger_timer.isActive())
        p.deleteLater()

    def test_aurora_progress_pending_delay_hide_keeps_delay_timer(self):
        from PyQt6.QtGui import QHideEvent
        from ui.aurora_progress import AuroraProgress

        p = AuroraProgress(None, delay_show_ms=300)
        p.start_busy('处理中', immediate=False)
        self.assertEqual(p._state, 'pending_busy')
        self.assertIsNotNone(p._delay_timer)
        self.assertTrue(p._delay_timer.isActive())

        # 在 pending 阶段被隐藏：delay_timer 仍然存活，仅停绘制定时器，保持 pending 状态
        p.hideEvent(QHideEvent())
        self.assertFalse(p._anim_timer.isActive())
        self.assertIsNotNone(p._delay_timer)
        self.assertTrue(p._delay_timer.isActive())
        self.assertEqual(p._state, 'pending_busy')
        p.deleteLater()

    def test_aurora_progress_fail_static_no_anim(self):
        from ui.aurora_progress import AuroraProgress

        p = AuroraProgress(None, delay_show_ms=0)
        p.fail('加载失败')
        self.assertEqual(p._state, 'fail')
        self.assertTrue(p._is_shown)
        # fail 状态严格静态错误展示，禁止存在活跃的 anim_timer
        self.assertFalse(p._anim_timer.isActive())
        self.assertIsNotNone(p._linger_timer)
        self.assertTrue(p._linger_timer.isActive())
        p.deleteLater()

    def test_aurora_progress_close_all_timers_stopped(self):
        from PyQt6.QtGui import QCloseEvent
        from ui.aurora_progress import AuroraProgress

        p = AuroraProgress(None, delay_show_ms=0)
        p.start_busy('任务进行中', immediate=True)
        p.finish('成功')
        self.assertIsNotNone(p._linger_timer)

        p.closeEvent(QCloseEvent())
        self.assertFalse(p._anim_timer.isActive())
        self.assertIsNone(p._linger_timer)
        self.assertIsNone(p._delay_timer)
        p.deleteLater()


class DashboardKeyboardContractTests(unittest.TestCase):
    def test_dashboard_keyboard_and_demo_row_contract(self):
        vue_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue')
        tasks_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'MonthlyTasks.vue')
        stats_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'StatsGrid.vue')
        self.assertTrue(os.path.exists(vue_path))
        content = ''
        with open(vue_path, 'r', encoding='utf-8') as f:
            content += f.read()
        if os.path.exists(tasks_path):
            with open(tasks_path, 'r', encoding='utf-8') as f:
                content += '\n' + f.read()
        if os.path.exists(stats_path):
            with open(stats_path, 'r', encoding='utf-8') as f:
                content += '\n' + f.read()

        # 1. 验证所有 role="button" 的可交互非原生元素均具备 space.prevent 契约
        # 匹配所有包含 role="button" 的标签块
        button_tags = re.findall(r'<[a-zA-Z0-9_-]+[^>]*?role="button"[^>]*>', content)
        self.assertGreater(len(button_tags), 0)
        for tag in button_tags:
            self.assertIn('@keydown.space.prevent', tag, f'Tag missing space handler: {tag}')
            self.assertIn('@keydown.enter', tag, f'Tag missing enter handler: {tag}')

        # 2. 验证 demo 需求行与 demo 任务行的语义绑定（仅在非 demo 且可交互时为 button/tabindex=0，demo 模式或 demo 需求行下均为 undefined）
        self.assertIn(':tabindex="isRowInteractive(r) ? 0 : undefined"', content)
        self.assertIn(':role="isRowInteractive(r) ? \'button\' : undefined"', content)
        self.assertIn(':tabindex="isRowInteractive(task) ? 0 : undefined"', content)
        self.assertIn(':role="isRowInteractive(task) ? \'button\' : undefined"', content)
        # 验证嵌套交互行采用 .self 防止键盘冒泡重复触发
        self.assertIn('@keydown.enter.self="isRowInteractive(r) && onOpenRequirement', content)
        self.assertIn('@keydown.space.self.prevent="isRowInteractive(r) && onOpenRequirement', content)
        self.assertIn('@keydown.enter.self="isRowInteractive(task) && onOpenRequirement', content)
        self.assertIn('@keydown.space.self.prevent="isRowInteractive(task) && onOpenRequirement', content)

        # 3. 验证 demo 样式不响应 hover / active
        self.assertIn('.ck:not(.is-demo):hover', content)
        self.assertIn('.ck.is-demo:hover', content)


class StyleQssButtonStatesTests(unittest.TestCase):
    def test_button_states_in_qss(self):
        qss_path = os.path.join(ROOT, 'resources', 'style.qss')
        self.assertTrue(os.path.exists(qss_path))
        with open(qss_path, 'r', encoding='utf-8') as f:
            content = f.read()

        required_snippets = [
            'QPushButton:pressed',
            'QPushButton:focus',
            'QPushButton:disabled',
            'QPushButton#btn-secondary:pressed',
            'QPushButton#btn-secondary:focus',
            'QPushButton#btn-secondary:disabled',
            'QPushButton#primary-btn:pressed',
            'QPushButton#primary-btn:focus',
            'QPushButton#primary-btn:disabled',
            'QPushButton#btn-danger:pressed',
            'QPushButton#btn-danger:focus',
            'QPushButton#btn-danger:disabled',
            'QPushButton#btn-ghost:pressed',
            'QPushButton#btn-ghost:focus',
            'QPushButton#btn-ghost:disabled',
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, content, f'Missing required QSS rule: {snippet}')


if __name__ == '__main__':
    unittest.main()
