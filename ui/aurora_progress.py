# -*- coding: utf-8 -*-
"""企业级浮层 Loading：视觉刷新，API 保持 start_busy / set_progress / finish / fail。

触发策略由调用方控制（长任务才 show），本组件不占布局、不改业务。
"""

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class AuroraProgress(QWidget):
    """Floating enterprise progress chip — visual only; trigger logic stays in callers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._value = -1
        self._label = ''
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(62)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hide()

    def start_busy(self, label):
        self._label = label
        self._value = -1
        self._phase = 0
        self.show()
        self.raise_()
        self._timer.start(28)
        self.update()

    def set_progress(self, value, label=None):
        self._value = max(0, min(100, int(value)))
        if label is not None:
            self._label = label
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start(28)
        self.update()

    def finish(self, label):
        self._label = label
        self._value = 100
        self._timer.start(28)
        self.update()
        # 企业软件：成功反馈短促，减少等待
        QTimer.singleShot(1100, self._fade_out)

    def fail(self, label):
        self._label = label
        self._value = 0
        self._timer.stop()
        self.show()
        self.raise_()
        self.update()

    def _fade_out(self):
        if self._value == 100:
            self._timer.stop()
            self.hide()

    def _tick(self):
        self._phase = (self._phase + 4) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(5.0, 4.0, self.width() - 10, self.height() - 9)

        # soft layered shadow
        for i, alpha in enumerate((12, 20, 30)):
            shadow = bounds.adjusted(-1 + i * 0.4, 1 + i * 0.5, 1 - i * 0.4, 2 + i * 0.55)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, alpha))
            painter.drawRoundedRect(shadow, 14, 14)

        # enterprise surface: clean white with cool edge
        body = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        body.setColorAt(0.0, QColor('#FFFFFF'))
        body.setColorAt(0.65, QColor('#F8FAFD'))
        body.setColorAt(1.0, QColor('#F1F5FB'))
        painter.setPen(QPen(QColor('#C7D2E5'), 1))
        painter.setBrush(body)
        painter.drawRoundedRect(bounds, 13, 13)

        # top highlight
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1))
        painter.drawLine(
            QPointF(bounds.left() + 16, bounds.top() + 1.2),
            QPointF(bounds.right() - 16, bounds.top() + 1.2),
        )

        # left brand bar
        accent = QRectF(bounds.left() + 2, bounds.top() + 12, 3.5, bounds.height() - 24)
        painter.setPen(Qt.PenStyle.NoPen)
        accent_grad = QLinearGradient(accent.topLeft(), accent.bottomLeft())
        accent_grad.setColorAt(0.0, QColor('#7C93FF'))
        accent_grad.setColorAt(1.0, QColor('#4A61F0'))
        painter.setBrush(accent_grad)
        painter.drawRoundedRect(accent, 2, 2)

        # status chip on the right (busy / % / fail)
        is_fail = self._value == 0 and not self._timer.isActive()
        chip_w = 54 if self._value >= 0 else 62
        chip = QRectF(bounds.right() - chip_w - 12, bounds.top() + 10, chip_w, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        if is_fail:
            painter.setBrush(QColor('#FEE2E2'))
            painter.setPen(QPen(QColor('#FECACA'), 1))
        elif self._value >= 100:
            painter.setBrush(QColor('#DCFCE7'))
            painter.setPen(QPen(QColor('#BBF7D0'), 1))
        else:
            painter.setBrush(QColor('#EEF2FF'))
            painter.setPen(QPen(QColor('#C7D2FE'), 1))
        painter.drawRoundedRect(chip, 11, 11)
        painter.setPen(QColor('#B91C1C') if is_fail else (QColor('#15803D') if self._value >= 100 else QColor('#3730A3')))
        painter.setFont(QFont('Microsoft YaHei UI', 8, QFont.Weight.Bold))
        if self._value < 0:
            chip_text = '处理中'
        elif is_fail:
            chip_text = '失败'
        else:
            chip_text = f'{self._value}%'
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)

        # progress track
        track = QRectF(bounds.left() + 18, bounds.bottom() - 15, bounds.width() - 36, 5.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#E2E8F0'))
        painter.drawRoundedRect(track, 3, 3)

        if self._value < 0:
            width = max(72.0, track.width() * 0.22)
            x = track.left() + ((self._phase / 360.0) * (track.width() + width)) - width
            fill = QRectF(x, track.top(), width, track.height())
        else:
            fill = QRectF(track.left(), track.top(), track.width() * self._value / 100.0, track.height())

        gradient = QLinearGradient(fill.left(), fill.top(), fill.right(), fill.top())
        if is_fail:
            gradient.setColorAt(0.0, QColor('#FB923C'))
            gradient.setColorAt(0.55, QColor('#EF4444'))
            gradient.setColorAt(1.0, QColor('#DC2626'))
        elif self._value >= 100:
            gradient.setColorAt(0.0, QColor('#34D399'))
            gradient.setColorAt(1.0, QColor('#10B981'))
        else:
            gradient.setColorAt(0.0, QColor('#38BDF8'))
            gradient.setColorAt(0.5, QColor('#6366F1'))
            gradient.setColorAt(1.0, QColor('#818CF8'))
        path = QPainterPath()
        path.addRoundedRect(track, 3, 3)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(fill, gradient)
        painter.restore()

        # label
        painter.setPen(QColor('#1E293B'))
        painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
        label_rect = QRectF(bounds.left() + 18, bounds.top() + 9, bounds.width() - chip_w - 40, 22)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label)
