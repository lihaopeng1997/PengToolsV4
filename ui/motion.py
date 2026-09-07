# -*- coding: utf-8 -*-
"""PengTools 统一微动效与交互时间常量（Motion Tokens）。

规范：
- INSTANT: 0–70 ms
- FAST: 90–110 ms
- STANDARD: 130–160 ms
- ENTER: 170–200 ms
- MAX: 220 ms
- 任何普通 UI transition <= 200 ms
- 优先采用 OutCubic (进场/悬停) 与 InCubic (离场)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QDialog, QWidget

DURATION_INSTANT = 70
DURATION_FAST = 100
DURATION_STANDARD = 150
DURATION_ENTER = 180
DURATION_MAX = 220

_MOTION_ENABLED_OVERRIDE: bool | None = None


def motion_enabled() -> bool:
    """是否启用动效。测试环境（offscreen）或显式环境变量下默认禁用以保障速度与确定性。"""
    if _MOTION_ENABLED_OVERRIDE is not None:
        return _MOTION_ENABLED_OVERRIDE
    if os.environ.get('PENGTOOLS_DISABLE_MOTION') == '1':
        return False
    if os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
        return False
    return True


def set_motion_enabled_for_test(enabled: bool | None) -> None:
    """供测试临时重载 motion 开关。None 恢复默认。"""
    global _MOTION_ENABLED_OVERRIDE
    _MOTION_ENABLED_OVERRIDE = enabled


def duration(ms: int) -> int:
    """返回当前环境下的适配时长（禁用时返回 0）。"""
    if not motion_enabled():
        return 0
    return max(0, int(ms))


def play_dialog_enter(dialog: QDialog, offset_y: int = 4) -> QPropertyAnimation | None:
    """为弹窗挂接轻量且安全的垂直入场微动效（<=4px），parent 绑定 dialog，不阻塞操作与模态。"""
    if not motion_enabled():
        return None

    try:
        anim = getattr(dialog, '_enter_anim', None)
        if anim is not None and isinstance(anim, QPropertyAnimation):
            if anim.state() == QPropertyAnimation.State.Running and anim.endValue() is not None:
                target_pos = anim.endValue()
            else:
                target_pos = dialog.pos()
            anim.stop()
        else:
            target_pos = dialog.pos()
            anim = QPropertyAnimation(dialog, b'pos', dialog)
            setattr(dialog, '_enter_anim', anim)

        start_pos = target_pos + QPoint(0, max(0, min(8, int(offset_y))))

        anim.setDuration(duration(DURATION_STANDARD))
        anim.setStartValue(start_pos)
        anim.setEndValue(target_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim.start()
        return anim
    except Exception:
        return None
