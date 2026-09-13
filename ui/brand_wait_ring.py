"""Presentation-only LD-07 ring for the existing compact brand button."""
import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QWidget

from ui.motion import motion_enabled
from ui.thinking_indicator import thinking_colors


class BrandWaitRing(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._running = False
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_running(self, running):
        if running and not self._running:
            self._started = time.monotonic()
        self._running = bool(running)
        self.setVisible(self._running)
        self._sync_timer()
        self.update()

    def _sync_timer(self):
        if self._running and self.isVisible() and motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        self._sync_timer()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        if not self._running:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent, _ = thinking_colors()
        pen = QPen(accent, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        angle = ((time.monotonic() - self._started) / .9 * 360) if motion_enabled() else 90
        painter.drawArc(QRectF(1, 1, 30, 30), int(angle * 16), 90 * 16)
