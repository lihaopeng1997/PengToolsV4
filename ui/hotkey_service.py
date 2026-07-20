# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, pyqtSignal, QObject


WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_P = 0x50


class _NativeHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback, hotkey_id):
        super().__init__()
        self._callback = callback
        self._hotkey_id = hotkey_id

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
            self._callback()
            return True, 0
        return False, 0


class HotkeyService(QObject):
    registration_failed = pyqtSignal()

    def __init__(self, app, callback, hotkey_id=0x5047):
        super().__init__()
        self._app = app
        self._callback = callback
        self._hotkey_id = hotkey_id
        self._filter = _NativeHotkeyFilter(callback, hotkey_id)
        self._registered = False

    def register(self):
        if self._registered:
            return True
        self._app.installNativeEventFilter(self._filter)
        self._registered = bool(
            ctypes.windll.user32.RegisterHotKey(
                None, self._hotkey_id, MOD_CONTROL | MOD_SHIFT, VK_P
            )
        )
        if not self._registered:
            self._app.removeNativeEventFilter(self._filter)
            self.registration_failed.emit()
        return self._registered

    def unregister(self):
        if not self._registered:
            return
        ctypes.windll.user32.UnregisterHotKey(None, self._hotkey_id)
        self._app.removeNativeEventFilter(self._filter)
        self._registered = False
