# -*- coding: utf-8 -*-
"""内嵌 Thinking 指示器组件：用于模型响应等待与 Agent 执行时的即时视觉反馈。

晴空棱镜 V2.0 规范 (LD-05):
- 尺寸：高 28px，最小宽 140px；
- 三菱点：3 个 6×6px 菱形微晶点 (Diamond Points)，间距 5px（中心间距 11px）；
- 动效：纯透明度在 0.35 ~ 1.0 之间平滑脉动，1.2s 周期，相位 0 / 0.15 / 0.3s，严格不上下跳字；
- 文字：起始 x=48px，字号 9pt (TEXT_MUTED)，默认文案“正在等待回复…”；
- 契约：start(), stop(), set_text(), is_running()，隐藏与销毁时自动清理定时器，支持 motion 减弱。
"""

import math
import time
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


def _palette():
    try:
        from ui.theme_manager import ThemeManager
        return ThemeManager.instance().palette()
    except Exception:
        return {}


def _qc(pal: dict, key: str, fallback: str = '#29332E') -> QColor:
    raw = pal.get(key) or fallback
    try:
        from ui.theme_manager import parse_color
        parsed = parse_color(raw)
        if parsed:
            r, g, b, a = parsed
            return QColor(r, g, b, a)
    except Exception:
        pass
    c = QColor(raw)
    return c if c.isValid() else QColor(fallback)


def thinking_colors(palette: dict | None = None) -> tuple[QColor, QColor]:
    """返回 ThinkingIndicator 在给定（或当前全局）调色板下的 (accent_color, text_color)。"""
    pal = palette if palette is not None else _palette()
    accent = _qc(pal, 'PRIMARY', '#6C58D9')
    text_color = _qc(pal, 'TEXT_MUTED', '#615D73')
    return accent, text_color


class ThinkingIndicator(QWidget):
    """用于气泡内嵌的轻量 Thinking 状态指示器 (LD-05 晴空棱镜规范)。"""

    def __init__(self, parent=None, text: str = '正在等待回复…'):
        super().__init__(parent)
        self._text = text or '正在等待回复…'
        self._start_time = 0.0
        self._is_running = False

        self._timer = QTimer(self)
        self._timer.setInterval(40)  # ~25 FPS
        self._timer.timeout.connect(self._on_tick)

        self.setFixedHeight(28)
        self.setMinimumWidth(140)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str):
        self._text = text or ''
        self.update()

    def is_running(self) -> bool:
        return self._is_running

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._start_time = time.monotonic()
            self._timer.start()
            self.show()
            self.update()

    def stop(self):
        if self._is_running:
            self._is_running = False
            self._timer.stop()
            self.update()

    def _on_tick(self):
        self.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._is_running:
            if not self._timer.isActive():
                self._timer.start()

    def closeEvent(self, event):
        super().closeEvent(event)
        self.stop()

    def _is_motion_enabled(self) -> bool:
        try:
            from ui.motion import motion_enabled
            return motion_enabled()
        except Exception:
            return True

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        accent, text_color = thinking_colors()
        motion_on = self._is_running and self._is_motion_enabled()

        # 1. 绘制 3 个菱形微晶点 (LD-05: 6x6px 菱形，间距 5px，中心间距 11px)
        # 中心点 X 坐标: cx0 = 14, cx1 = 25, cx2 = 36 (点跨度 11~39，文字起于 48)
        start_cx = 14.0
        center_dist = 11.0
        center_y = self.height() / 2.0  # 14.0，严格保持固定，绝不上下跳动
        diamond_half = 3.0  # 6x6 菱形半宽/半高

        now = time.monotonic()
        elapsed = now - self._start_time if self._start_time > 0 else 0.0
        # 1.2s 周期
        period = 1.2
        angular_speed = (2.0 * math.pi) / period
        phase_delays = (0.0, 0.15, 0.30)  # 相位延迟 0 / 0.15 / 0.3s

        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(3):
            cx = start_cx + i * center_dist
            if motion_on:
                t = elapsed - phase_delays[i]
                # 正弦波平滑过渡: 0.35 ~ 1.0
                wave = (math.sin(t * angular_speed) + 1.0) / 2.0  # 0.0 ~ 1.0
                alpha_norm = 0.35 + 0.65 * wave
            else:
                alpha_norm = 0.7 if self._is_running else 0.35

            alpha = max(10, min(255, int(alpha_norm * 255)))
            dot_color = QColor(accent.red(), accent.green(), accent.blue(), alpha)
            painter.setBrush(dot_color)

            # 绘制 6x6 菱形
            path = QPainterPath()
            path.moveTo(cx, center_y - diamond_half)  # 顶部
            path.lineTo(cx + diamond_half, center_y)  # 右侧
            path.lineTo(cx, center_y + diamond_half)  # 底部
            path.lineTo(cx - diamond_half, center_y)  # 左侧
            path.closeSubpath()
            painter.drawPath(path)

        # 2. 绘制文字描述 (LD-05 规定文字起于 x=48px)
        text_x = 48.0
        painter.setPen(text_color)
        f = painter.font()
        f.setPointSize(9)
        painter.setFont(f)

        text_rect = QRectF(text_x, 0, max(0.0, self.width() - text_x - 4), self.height())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._text)
