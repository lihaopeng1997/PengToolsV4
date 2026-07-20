# -*- coding: utf-8 -*-
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class AuroraProgress(QWidget):
    """Floating animated progress chip — visual only; trigger logic stays in callers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._value = -1
        self._label = ''
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(58)
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
        # inset leaves room for soft layered shadow
        bounds = QRectF(4.0, 3.0, self.width() - 8, self.height() - 7)

        # layered shadow for floating card feel (no extra assets)
        for i, alpha in enumerate((18, 28, 38)):
            shadow = bounds.adjusted(-1 + i * 0.3, 1 + i * 0.5, 1 - i * 0.3, 2 + i * 0.6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(22, 32, 58, alpha))
            painter.drawRoundedRect(shadow, 15, 15)

        # card body: cool white → soft indigo mist
        body = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        body.setColorAt(0.0, QColor('#FFFFFF'))
        body.setColorAt(0.55, QColor('#F8FAFF'))
        body.setColorAt(1.0, QColor('#EEF2FF'))
        painter.setPen(QPen(QColor('#C5D0F0'), 1))
        painter.setBrush(body)
        painter.drawRoundedRect(bounds, 14, 14)

        # top hairline highlight
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.drawLine(
            QPointF(bounds.left() + 14, bounds.top() + 1),
            QPointF(bounds.right() - 14, bounds.top() + 1),
        )

        # left accent bar (module accent)
        accent = QRectF(bounds.left() + 2, bounds.top() + 11, 3.5, bounds.height() - 22)
        painter.setPen(Qt.PenStyle.NoPen)
        accent_grad = QLinearGradient(accent.topLeft(), accent.bottomLeft())
        accent_grad.setColorAt(0.0, QColor('#7C93FF'))
        accent_grad.setColorAt(1.0, QColor('#5B73FF'))
        painter.setBrush(accent_grad)
        painter.drawRoundedRect(accent, 2, 2)

        track = QRectF(bounds.left() + 18, bounds.bottom() - 16, bounds.width() - 36, 6)
        painter.setBrush(QColor('#E2E8F5'))
        painter.drawRoundedRect(track, 3, 3)

        if self._value < 0:
            width = max(76.0, track.width() * 0.26)
            x = track.left() + ((self._phase / 360.0) * (track.width() + width)) - width
            fill = QRectF(x, track.top(), width, track.height())
        else:
            fill = QRectF(track.left(), track.top(), track.width() * self._value / 100.0, track.height())

        gradient = QLinearGradient(fill.left(), fill.top(), fill.right(), fill.top())
        # fail() 会停表并把 value 置 0；普通 0% 进度仍用主色
        is_fail = self._value == 0 and not self._timer.isActive()
        if is_fail:
            gradient.setColorAt(0.0, QColor('#F97316'))
            gradient.setColorAt(0.55, QColor('#EF4444'))
            gradient.setColorAt(1.0, QColor('#DC2626'))
        else:
            gradient.setColorAt(0.0, QColor('#38BDF8'))
            gradient.setColorAt(0.45, QColor('#6366F1'))
            gradient.setColorAt(1.0, QColor('#A78BFA'))
        path = QPainterPath()
        path.addRoundedRect(track, 3, 3)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(fill, gradient)
        painter.restore()

        # orbit dot along the top edge of the chip
        orbit_span = max(1.0, bounds.width() - 48)
        orbit_x = bounds.left() + 24 + (self._phase % 360) / 360.0 * orbit_span
        painter.setPen(QPen(QColor(99, 102, 241, 80), 1))
        painter.setBrush(QColor(99, 102, 241, 200))
        painter.drawEllipse(QPointF(orbit_x, bounds.top() + 14), 2.6, 2.6)

        painter.setPen(QColor('#1E2B4A'))
        painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
        label_rect = QRectF(bounds.left() + 18, bounds.top() + 8, bounds.width() - 96, 20)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, self._label)
        if self._value >= 0:
            painter.setPen(QColor('#DC2626') if is_fail else QColor('#4A61F0'))
            painter.drawText(
                QRectF(bounds.right() - 72, bounds.top() + 8, 54, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f'{self._value}%',
            )
