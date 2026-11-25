"""LD-04 result-area status band: 32px row, 16px icon, 12px left inset.

The band only reflects text the caller already sets. It does not clear a
table model, reset scroll, or start a query.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ui.motion import motion_enabled
from ui.thinking_indicator import thinking_colors


_QUERY_PREFIXES = (
    '正在',
    'running',
    'fetching',
    'testing',
    'scanning',
    'cancelling',
    'canceling',
    'connecting',
    'working',
)

_ERROR_TOKENS = (
    '失败', '不可用', '错误', '异常', '超时', '拒绝', '中断',
    'failed', 'error', 'unavailable', 'timeout', 'denied', 'exception',
)
_OK_TOKENS = (
    '已返回', '已获取', '已扫描', '已完成', '已提交', '已回滚', '成功', '就绪', '行',
    'ready', 'done', 'ok', 'rows', 'committed', 'scanned',
)


class _StatusIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._querying = False
        self._tone = 'idle'
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self.update)

    def set_tone(self, tone: str) -> None:
        self._tone = str(tone or 'idle')
        self.update()

    def set_querying(self, querying: bool) -> None:
        querying = bool(querying)
        if querying and not self._querying:
            self._started = time.monotonic()
        self._querying = querying
        if self._querying and self.isVisible() and motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def is_querying(self) -> bool:
        return self._querying

    def showEvent(self, event):
        super().showEvent(event)
        self.set_querying(self._querying)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._querying:
            accent, _muted = thinking_colors()
            pen = QPen(accent, 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            angle = ((time.monotonic() - self._started) / 0.9) * 360.0 if motion_enabled() else 90.0
            painter.drawArc(QRectF(2, 2, 12, 12), int(angle * 16), 110 * 16)
        else:
            color = (
                QColor('#DC2626') if self._tone == 'error'
                else QColor('#10B981') if self._tone == 'ok'
                else QColor('#A59FC0')
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(4.5, 4.5, 7.0, 7.0))


class QueryStatusBand(QWidget):
    """Top-of-results status row used by the existing query status text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('query-status-band')
        self.setFixedHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(6)
        self._icon = _StatusIcon(self)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._label = QLabel(self)
        self._label.setObjectName('field-hint')
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.installEventFilter(self)
        layout.addWidget(self._label, 1)
        self._full_text = ''

    def setText(self, text: str, *, querying: bool | None = None) -> None:  # noqa: N802
        value = str(text or '')
        self._full_text = value
        self._refresh_label_display()
        folded = value.strip().casefold()
        if querying is None:
            querying = folded.startswith(_QUERY_PREFIXES)
        if querying:
            tone = 'busy'
        elif any(tok in folded for tok in _ERROR_TOKENS):
            tone = 'error'
        elif any(tok in folded for tok in _OK_TOKENS):
            tone = 'ok'
        else:
            tone = 'idle'
        self._icon.set_tone(tone)
        self._icon.set_querying(bool(querying))

    def text(self) -> str:
        return self._full_text

    def is_querying(self) -> bool:
        return self._icon.is_querying()

    def _refresh_label_display(self) -> None:
        full_text = self._full_text
        available_width = max(0, self._label.contentsRect().width())
        displayed = self._label.fontMetrics().elidedText(
            full_text, Qt.TextElideMode.ElideRight, available_width,
        )
        if self._label.text() != displayed:
            self._label.setText(displayed)
        self._label.setToolTip(full_text)
        self._label.setAccessibleName(full_text)
        self._label.setAccessibleDescription(full_text)

    def eventFilter(self, watched, event):
        if watched is self._label and event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.Resize,
        ):
            self._refresh_label_display()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_label_display()
