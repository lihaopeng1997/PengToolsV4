# -*- coding: utf-8 -*-
"""停止监听后再开始：旧 worker 晚到 stop 信号不得清掉新一轮状态。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class CaptureRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_stale_stopped_signal_does_not_clear_new_worker(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        old = MagicMock()
        new = MagicMock()
        panel._ie_worker = old
        panel._listening = True
        panel._channel_ready = True
        panel._detach_capture_worker(old)
        panel._ie_worker = None
        panel._listening = False
        # 模拟新一轮已启动
        panel._ie_worker = new
        panel._listening = True
        panel._channel_ready = True
        panel._on_proxy_stopped()
        self.assertTrue(panel._listening)
        self.assertIs(panel._ie_worker, new)
        panel.close()

    def test_stop_clears_callbacks_before_async_shutdown(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        worker = MagicMock()
        worker.on_record = panel._on_ie_record_thread
        worker.on_error = panel._on_ie_error_thread
        worker.on_stopped = panel._on_ie_stopped_thread
        panel._ie_worker = worker
        panel._listening = True
        panel._channel_ready = True
        with patch('panels.interface_debug_panel.restore_proxy_from_snapshot'):
            with patch('tools.ie_proxy.mark_capture_proxy_inactive'):
                with patch('tools.ie_proxy.ensure_system_proxy_safe'):
                    panel._stop_listen()
        self.assertIsNone(worker.on_record)
        self.assertIsNone(worker.on_error)
        self.assertIsNone(worker.on_stopped)
        self.assertFalse(panel._listening)
        self.assertIsNone(panel._ie_worker)
        # 等后台 stop 线程跑完，避免干扰其它用例
        thread = panel._capture_stop_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        panel.close()

    def test_start_waits_for_previous_stop_thread(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        called = {'joined': False}

        class _FakeThread:
            def is_alive(self):
                return True

            def join(self, timeout=None):
                called['joined'] = True

        panel._capture_stop_thread = _FakeThread()
        panel._await_previous_capture_stop(0.1)
        self.assertTrue(called['joined'])
        panel.close()


if __name__ == '__main__':
    unittest.main()
