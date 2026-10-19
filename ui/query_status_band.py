"""LD-04 result-area status band: 32px row, 16px icon, 12px left inset.

The band only reflects text the caller already sets. It does not clear a
table model, reset scroll, or start a query.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QPainter, QPen
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


class _StatusIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._querying = False
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self.update)

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
        accent, _muted = thinking_colors()
        pen = QPen(accent, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._querying and motion_enabled():
            angle = ((time.monotonic() - self._started) / 0.9) * 360.0
        else:
            angle = 90.0
        painter.drawArc(QRectF(1, 1, 14, 14), int(angle * 16), 90 * 16)


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
        if querying is None:
            folded = value.strip().casefold()
            querying = folded.startswith(_QUERY_PREFIXES)
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
