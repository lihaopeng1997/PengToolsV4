# -*- coding: utf-8 -*-
import ctypes
import sys

from PyQt6.QtCore import QObject, QTimer


class KeepAwakeService(QObject):
    """Periodically reports activity so managed Windows sessions stay awake."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    MOUSEEVENTF_MOVE = 0x0001

    def __init__(self, parent=None, pulse=None):
        super().__init__(parent)
        self._pulse = pulse or self._windows_pulse
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.pulse)

    def apply_preferences(self, enabled, interval_minutes):
        if not enabled:
            self.stop()
            return
        interval_ms = max(1, min(60, int(interval_minutes))) * 60 * 1000
        self._timer.start(interval_ms)
        self.pulse()

    def pulse(self):
        self._pulse()

    def stop(self):
        self._timer.stop()
        if sys.platform == 'win32':
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)

    def is_active(self):
        return self._timer.isActive()

    def interval_minutes(self):
        return self._timer.interval() // 60000

    @classmethod
    def _windows_pulse(cls):
        if sys.platform != 'win32':
            return
        ctypes.windll.kernel32.SetThreadExecutionState(
            cls.ES_CONTINUOUS | cls.ES_SYSTEM_REQUIRED | cls.ES_DISPLAY_REQUIRED
        )
        ctypes.windll.user32.mouse_event(cls.MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        ctypes.windll.user32.mouse_event(cls.MOUSEEVENTF_MOVE, -1, 0, 0, 0)
