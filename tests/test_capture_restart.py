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

    def test_stop_waits_port_release_before_return(self):
        """停止监听必须等端口真正释放，否则紧接着再点监听会抢不到端口。"""
        import threading
        from tools.http_capture import HttpCaptureWorker
        # 不真正 start：仅验证 stop 内部端口释放等待路径
        worker = HttpCaptureWorker.__new__(HttpCaptureWorker)
        worker.port = 8899
        worker._stop = threading.Event()
        worker._stop.set()
        worker._thread = None
        worker._master = None
        worker._loop = None
        worker._proxy_applied = False
        worker.on_stopped = None
        worker._restore_proxy = MagicMock()
        # _stop 置位后，_port_bound 前两次返回 True（占用），第三次返回 False（已释放）
        seq = {'n': 0}

        def _fake_bound():
            seq['n'] += 1
            return seq['n'] < 3

        worker._port_bound = _fake_bound
        worker._wait_port_released(timeout=2.0)
        # 端口释放后立即返回，不再空转
        self.assertEqual(seq['n'], 3)

    def test_wait_port_released_skips_when_not_stopped(self):
        """未请求 stop 时不应等待端口释放（避免误等无关连接）。"""
        from tools.http_capture import HttpCaptureWorker
        worker = HttpCaptureWorker.__new__(HttpCaptureWorker)
        worker.port = 8899
        worker._stop = MagicMock()
        worker._stop.is_set.return_value = False
        worker._port_bound = MagicMock(return_value=True)
        worker._wait_port_released(timeout=2.0)
        worker._port_bound.assert_not_called()

    def test_bind_conflict_fails_fast_then_succeeds_after_release(self):
        """端口被占用时快速失败且报错含端口提示；释放后同端口可再启动（用户回归场景）。"""
        import socket as _socket
        from tools.http_capture import HttpCaptureWorker

        blocker = _socket.socket()
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]

        errors = []
        w1 = HttpCaptureWorker(port=port, apply_system_proxy=False, on_error=errors.append)
        w1.start()
        try:
            # 占用期间：快速失败（不再傻等 10 秒），且不得误报"就绪"
            self.assertFalse(w1.wait_ready(timeout=8.0))
            self.assertFalse(w1.ready)
            self.assertTrue(any('端口' in str(e) for e in errors), f'errors={errors}')
        finally:
            blocker.close()
            w1.stop(join_timeout=1.0)

        # 释放后：同端口重新启动应成功（停止→再监听的主回归路径）
        w2 = HttpCaptureWorker(port=port, apply_system_proxy=False)
        w2.start()
        try:
            self.assertTrue(w2.wait_ready(timeout=15.0))
            self.assertTrue(w2.ready)
        finally:
            w2.stop(join_timeout=1.0, clear_records=True)


if __name__ == '__main__':
    unittest.main()
