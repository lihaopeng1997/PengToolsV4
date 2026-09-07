# -*- coding: utf-8 -*-
"""微动效与交互基础单元测试：
- Motion Tokens 常量与适配时长 duration()
- motion_enabled() 开关与测试模式隔离
- 弹窗轻量入场动画 play_dialog_enter
- ThinkingIndicator / AuroraProgress 资源与定时器生命周期清理
- QSS 核心控件按压/焦点/禁用态完备性
"""

from __future__ import annotations

import os
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

    def test_play_dialog_enter_enabled(self):
        from PyQt6.QtCore import QPropertyAnimation
        from PyQt6.QtWidgets import QDialog
        from ui.motion import play_dialog_enter, set_motion_enabled_for_test
        set_motion_enabled_for_test(True)
        dlg = QDialog()
        anim = play_dialog_enter(dlg, offset_y=4)
        self.assertIsNotNone(anim)
        self.assertIsInstance(anim, QPropertyAnimation)
        self.assertEqual(anim.propertyName(), b'pos')
        self.assertEqual(anim.duration(), 150)
        self.assertEqual(anim.parent(), dlg)

        # 连续调用：停止旧动画并替换，不崩溃
        anim2 = play_dialog_enter(dlg, offset_y=4)
        self.assertIsNotNone(anim2)
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
        from PyQt6.QtGui import QCloseEvent, QHideEvent
        from ui.thinking_indicator import ThinkingIndicator

        w = ThinkingIndicator(None, text='正在推理...')
        self.assertFalse(w.is_running())
        w.start()
        self.assertTrue(w.is_running())
        self.assertTrue(w._timer.isActive())

        # hideEvent 自动 stop
        w.hideEvent(QHideEvent())
        self.assertFalse(w.is_running())
        self.assertFalse(w._timer.isActive())

        # 重启并 closeEvent 自动 stop
        w.start()
        self.assertTrue(w.is_running())
        w.closeEvent(QCloseEvent())
        self.assertFalse(w.is_running())
        self.assertFalse(w._timer.isActive())
        w.deleteLater()

    def test_aurora_progress_timer_stopped_on_hide(self):
        from PyQt6.QtGui import QHideEvent
        from ui.aurora_progress import AuroraProgress

        p = AuroraProgress(None, delay_show_ms=0)
        p.start_busy('处理中', immediate=True)
        self.assertTrue(p._anim_timer.isActive())

        # 触发 hideEvent 应停止动画定时器，防止后台空转
        p.hideEvent(QHideEvent())
        self.assertFalse(p._anim_timer.isActive())

        # hide_now 状态与定时器完全收敛
        p.start_busy('新任务', immediate=True)
        self.assertTrue(p._anim_timer.isActive())
        p.hide_now()
        self.assertFalse(p._anim_timer.isActive())
        self.assertEqual(p._state, 'idle')
        p.deleteLater()


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
