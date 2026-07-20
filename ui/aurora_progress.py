# -*- coding: utf-8 -*-
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class AuroraProgress(QWidget):
    """Compact animated data-stream progress indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._value = -1
        self._label = ''
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(54)
        self.hide()

    def start_busy(self, label):
        self._label = label
        self._value = -1
        self._phase = 0
        self.show()
        self._timer.start(32)
        self.update()

    def set_progress(self, value, label=None):
        self._value = max(0, min(100, int(value)))
        if label is not None:
            self._label = label
        self.show()
        if not self._timer.isActive():
            self._timer.start(32)
        self.update()

    def finish(self, label):
        self._label = label
        self._value = 100
        self._timer.start(32)
        self.update()
        QTimer.singleShot(1800, self._fade_out)

    def fail(self, label):
        self._label = label
        self._value = 0
        self._timer.stop()
        self.show()
        self.update()

    def _fade_out(self):
        if self._value == 100:
            self._timer.stop()
            self.hide()

    def _tick(self):
        self._phase = (self._phase + 3) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(1.0, 1.0, self.width() - 2, self.height() - 2)

        # soft outer rim for floating feel
        painter.setPen(QPen(QColor(30, 42, 72, 28), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bounds.adjusted(-0.5, -0.5, 0.5, 0.5), 16, 16)

        body = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        body.setColorAt(0.0, QColor('#FFFFFF'))
        body.setColorAt(1.0, QColor('#F4F7FF'))
        painter.setPen(QPen(QColor('#C9D4F0'), 1))
        painter.setBrush(body)
        painter.drawRoundedRect(bounds, 14, 14)

        # left accent bar
        accent = QRectF(bounds.left() + 1, bounds.top() + 10, 3.5, bounds.height() - 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#5B73FF'))
        painter.drawRoundedRect(accent, 2, 2)

        track = QRectF(20, 34, self.width() - 40, 6)
        painter.setBrush(QColor('#E4E9F5'))
        painter.drawRoundedRect(track, 3, 3)

        if self._value < 0:
            width = max(72.0, track.width() * 0.24)
            x = track.left() + ((self._phase / 360.0) * (track.width() + width)) - width
            fill = QRectF(x, track.top(), width, track.height())
        else:
            fill = QRectF(track.left(), track.top(), track.width() * self._value / 100.0, track.height())

        gradient = QLinearGradient(fill.left(), fill.top(), fill.right(), fill.top())
        gradient.setColorAt(0.0, QColor('#4CC9F0'))
        gradient.setColorAt(0.45, QColor('#5B73FF'))
        gradient.setColorAt(1.0, QColor('#8B5CF6'))
        path = QPainterPath()
        path.addRoundedRect(track, 3, 3)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(fill, gradient)
        painter.restore()

        orbit_x = 22 + (self._phase % max(1, self.width() - 44))
        painter.setPen(QPen(QColor(91, 115, 255, 90), 1))
        painter.setBrush(QColor(91, 115, 255, 190))
        painter.drawEllipse(QPointF(orbit_x, 15), 2.8, 2.8)

        painter.setPen(QColor('#24355A'))
        painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
        painter.drawText(QRectF(20, 8, self.width() - 100, 20), Qt.AlignmentFlag.AlignVCenter, self._label)
        if self._value >= 0:
            painter.setPen(QColor('#4A61F0'))
            painter.drawText(
                QRectF(self.width() - 78, 8, 58, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f'{self._value}%',
            )
