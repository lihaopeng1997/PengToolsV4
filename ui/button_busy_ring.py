"""LD-03 button ring: 16px arc inside the button, without moving its label.

Use this only when the task has no floating Aurora overlay. A task that already
shows AuroraProgress must not also spin the button.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QWidget

from ui.motion import motion_enabled
from ui.thinking_indicator import thinking_colors


class ButtonBusyRing(QWidget):
    def __init__(self, button: QWidget):
        super().__init__(button)
        self.setObjectName('button-busy-ring')
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._busy = False
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self.update)
        self.hide()
        button.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        ):
            self._place()
        return False

    def _place(self) -> None:
        button = self.parentWidget()
        if button is None:
            return
        rect = button.contentsRect()
        y = rect.top() + max(0, (rect.height() - 16) // 2)
        self.move(rect.left(), y)
        self.raise_()

    def set_busy(self, busy: bool) -> None:
        busy = bool(busy)
        if busy and not self._busy:
            self._started = time.monotonic()
        self._busy = busy
        self.setVisible(busy)
        if busy and self.isVisible() and motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self._place()
        self.update()

    def is_busy(self) -> bool:
        return self._busy

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._busy and motion_enabled() and not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):  # noqa: N802
        if not self._busy:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent, _muted = thinking_colors()
        pen = QPen(accent, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if motion_enabled():
            angle = ((time.monotonic() - self._started) / 0.9) * 360.0
        else:
            angle = 90.0
        painter.drawArc(QRectF(1, 1, 14, 14), int(angle * 16), 90 * 16)


def set_button_busy(button: QWidget, busy: bool, *, uses_overlay: bool = False) -> ButtonBusyRing | None:
    """Show the 16px ring, or refuse it when this task already has an overlay."""

    if uses_overlay:
        ring = getattr(button, '_button_busy_ring', None)
        if ring is not None:
            ring.set_busy(False)
        return None
    ring = getattr(button, '_button_busy_ring', None)
    if ring is None:
        ring = ButtonBusyRing(button)
        button._button_busy_ring = ring
    ring.set_busy(busy)
    return ring
