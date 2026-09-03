# -*- coding: utf-8 -*-
"""内嵌 Thinking 指示器组件：用于模型响应等待与 Agent 执行时的即时视觉反馈。

规范契约：
- 采用 QTimer 驱动平滑的 3 节点微动动画，符合企业级 subtle motion 规范；
- 自适应 Light / Dark 主题配色，复用 ThemeManager 调色板；
- 提供 start(), stop(), set_text() 接口，支持中英文自适应；
- 销毁与隐藏时自动清理定时器，杜绝 CPU/内存泄漏。
"""

import math
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


class ThinkingIndicator(QWidget):
    """用于气泡内嵌的轻量 Thinking 状态指示器。"""

    def __init__(self, parent=None, text: str = '正在思考...'):
        super().__init__(parent)
        self._text = text
        self._phase = 0.0
        self._is_running = False

        self._timer = QTimer(self)
        self._timer.setInterval(40)  # ~25 FPS
        self._timer.timeout.connect(self._on_tick)

        self.setFixedHeight(28)
        self.setMinimumWidth(120)
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
            self._phase = 0.0
            self._timer.start()
            self.show()
            self.update()

    def stop(self):
        if self._is_running:
            self._is_running = False
            self._timer.stop()
            self.update()

    def _on_tick(self):
        self._phase = (self._phase + 0.15) % (2 * math.pi)
        self.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop()

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        pal = _palette()
        accent = _qc(pal, 'accent', '#0D9488')
        text_color = _qc(pal, 'text_muted', '#64748B')

        # 1. 绘制 3 个动态脉动光点 (Aurora dots)
        dot_radius_base = 3.0
        spacing = 11.0
        start_x = 10.0
        center_y = self.height() / 2.0

        for i in range(3):
            dot_phase = self._phase - (i * 0.7)
            # 正弦波驱动半径与透明度脉动
            pulse = (math.sin(dot_phase) + 1.0) / 2.0  # 0.0 ~ 1.0
            r = dot_radius_base + pulse * 1.5
            alpha = int(120 + pulse * 135) if self._is_running else 60

            dot_color = QColor(accent.red(), accent.green(), accent.blue(), alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            painter.drawEllipse(QPointF(start_x + i * spacing, center_y), r, r)

        # 2. 绘制文字描述
        text_x = start_x + 3 * spacing + 6.0
        painter.setPen(text_color)
        f = painter.font()
        f.setPointSize(9)
        painter.setFont(f)

        text_rect = QRectF(text_x, 0, self.width() - text_x - 4, self.height())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._text)
