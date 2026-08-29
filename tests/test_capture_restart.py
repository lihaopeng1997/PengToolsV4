# -*- coding: utf-8 -*-
"""抓包生命周期回归：停止→立即重启、pending start、旧信号隔离、恢复决策、真实 worker。"""
import io
import os
import re
import socket
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PANEL_SRC = io.open(os.path.join(ROOT, 'panels', 'interface_debug_panel.py'),
                    encoding='utf-8').read()


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CaptureLifecycleStateMachineTest(unittest.TestCase):
    """Case 1/2/3/8：状态机覆盖停止后重启、pending start、重复点击。"""

    def test_case1_start_stop_start(self):
        from tools.capture_lifecycle import IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle
        lc = CaptureLifecycle()
        e1 = lc.begin_start()
        self.assertIsNotNone(e1)
        lc.mark_running(e1)
        self.assertEqual(lc.state, RUNNING)
        self.assertTrue(lc.begin_stop(e1))
        lc.mark_stopped()
        self.assertEqual(lc.state, IDLE)
        e2 = lc.begin_start()
        self.assertIsNotNone(e2)
        lc.mark_running(e2)
        self.assertEqual(lc.state, RUNNING)

    def test_case2_stop_then_immediate_start_is_pending(self):
        from tools.capture_lifecycle import IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle
        lc = CaptureLifecycle()
        e1 = lc.begin_start()
        lc.mark_running(e1)
        self.assertTrue(lc.begin_stop(e1))
        # stop 线程尚未收尾：立即 START 只记 pending，不产生新 epoch
        self.assertIsNone(lc.begin_start())
        self.assertTrue(lc.pending_start)
        self.assertEqual(lc.state, STOPPING)
        lc.mark_stopped()
        e2 = lc.confirm_pending_start()
        self.assertIsNotNone(e2)
        self.assertEqual(lc.state, STARTING)
        lc.mark_running(e2)
        self.assertEqual(lc.state, RUNNING)

    def test_case3_ten_rounds_single_active_epoch(self):
        from tools.capture_lifecycle import IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle
        lc = CaptureLifecycle()
        last = None
        for _ in range(10):
            e = lc.begin_start()
            self.assertIsNotNone(e)
            self.assertNotEqual(e, last)
            last = e
            lc.mark_running(e)
            self.assertTrue(lc.begin_stop(e))
            lc.mark_stopped()
        self.assertEqual(lc.state, IDLE)

    def test_case8_double_start_single_boot(self):
        from tools.capture_lifecycle import IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle
        lc = CaptureLifecycle()
        e1 = lc.begin_start()
        self.assertIsNone(lc.begin_start())   # STARTING 重复点击
        lc.mark_running(e1)
        self.assertIsNone(lc.begin_start())   # RUNNING 重复点击
        self.assertEqual(lc.epoch, e1)

    def test_stale_boot_epoch_rejected(self):
        """旧 epoch 的 boot 晚到：不得成为当前 worker（Case 5 前置）。"""
        from tools.capture_lifecycle import IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle
        lc = CaptureLifecycle()
        e1 = lc.begin_start()
        lc.begin_stop(e1)
        lc.mark_stopped()
        e2 = lc.begin_start()
        self.assertFalse(lc.mark_running(e1))
        self.assertTrue(lc.mark_running(e2))

    def test_pending_start_returns_immediately_under_slow_stop(self):
        """UI 无阻塞（<300ms）：STOPPING 期间 begin_start 立即返回，不等待 stop 完成。"""
        from tools.capture_lifecycle import IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle
        lc = CaptureLifecycle()
        done = []
        e = lc.begin_start()
        lc.mark_running(e)
        lc.begin_stop(e)

        def slow_stop():
            time.sleep(1.5)
            done.append(True)
            lc.mark_stopped()

        th = threading.Thread(target=slow_stop, daemon=True)
        th.start()
        time.sleep(0.05)
        t0 = time.perf_counter()
        result = lc.begin_start()   # 历史路径在这里 join 2.5s
        elapsed = time.perf_counter() - t0
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.3, f'begin_start 阻塞了 {elapsed:.2f}s')
        th.join(timeout=3)
        self.assertTrue(done)
        self.assertIsNotNone(lc.confirm_pending_start())


class PanelWiringGuardTest(unittest.TestCase):
    """源码级守护：UI 主线程无 join；lifecycle/resume guard 接线存在。"""

    def test_panel_has_no_thread_join(self):
        self.assertNotIn('thread.join(', PANEL_SRC)
        self.assertNotIn('_await_previous_capture_stop', PANEL_SRC)

    def test_panel_uses_lifecycle_and_resume_guard(self):
        self.assertIn('self._lifecycle.begin_start()', PANEL_SRC)
        self.assertIn('self._lifecycle.begin_stop(self._capture_epoch)', PANEL_SRC)
        self.assertIn('confirm_pending_start', PANEL_SRC)
        self.assertIn('resolve_resume_action', PANEL_SRC)
        self.assertIn('_lifecycle.mark_stopped()', PANEL_SRC)


class ResumeDecisionTest(unittest.TestCase):
    """Case 6/7 决策函数：worker 健康且端口在听才 resume；否则恢复用户代理。"""

    def test_healthy_resumes(self):
        from tools.capture_lifecycle import resolve_resume_action
        self.assertEqual(resolve_resume_action(True, True), 'resume')

    def test_dead_restores(self):
        from tools.capture_lifecycle import resolve_resume_action
        self.assertEqual(resolve_resume_action(False, False), 'restore')
        self.assertEqual(resolve_resume_action(True, False), 'restore')
        self.assertEqual(resolve_resume_action(False, True), 'restore')


class PanelActivateResumeTest(unittest.TestCase):
    """Case 6/7 panel 级：回到页面时的 resume/restore 行为。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _make_panel(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        return panel

    def test_case6_healthy_worker_resumes_proxy(self):
        panel = self._make_panel()
        panel._lifecycle.begin_start()
        panel._listening = True
        panel._ie_worker = MagicMock()
        port = panel._current_port()
        blocker = socket.socket()
        blocker.bind(('127.0.0.1', port))
        blocker.listen(1)   # 端口在听（用真实 listener 模拟健康 worker）
        try:
            with patch('tools.ie_proxy.resume_capture_system_proxy', return_value='resumed') as resume:
                with patch('tools.ie_proxy.is_capture_proxy_suspended', return_value=True):
                    panel.on_panel_activated()
            resume.assert_called_once()
            self.assertTrue(panel._listening)
        finally:
            blocker.close()
            panel.close()

    def test_case7_dead_worker_never_resumes_dead_port(self):
        panel = self._make_panel()
        panel._lifecycle.begin_start()
        panel._listening = True
        panel._ie_worker = None   # worker 已死亡
        port = panel._current_port()   # 没有真实 listener
        with patch('tools.ie_proxy.resume_capture_system_proxy') as resume:
            with patch('tools.ie_proxy.restore_proxy_from_snapshot') as restore:
                with patch('tools.ie_proxy.mark_capture_proxy_inactive') as mark:
                    with patch('tools.ie_proxy.is_capture_proxy_suspended', return_value=True):
                        panel.on_panel_activated()
            resume.assert_not_called()      # 绝不 resume 到死端口
            restore.assert_called_once()
            mark.assert_called_once()
            self.assertFalse(panel._listening)   # 回到非监听状态
        panel.close()


class LegacySignalIsolationTest(unittest.TestCase):
    """Case 5：旧 worker stopped/error 晚到，不得清掉新一轮状态。"""

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
        panel._ie_worker = new       # 模拟新一轮已启动
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
        panel._capture_epoch = panel._lifecycle.begin_start()
        with patch('tools.ie_proxy.restore_proxy_from_snapshot'):
            with patch('tools.ie_proxy.mark_capture_proxy_inactive'):
                with patch('tools.ie_proxy.ensure_system_proxy_safe'):
                    panel._stop_listen()
        self.assertIsNone(worker.on_record)
        self.assertIsNone(worker.on_error)
        self.assertIsNone(worker.on_stopped)
        self.assertFalse(panel._listening)
        thread = panel._capture_stop_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        panel.close()


class RealWorkerRestartTest(unittest.TestCase):
    """Case 4：真实 HttpCaptureWorker 127.0.0.1 动态端口 start→stop→restart ×5（不碰系统代理）。"""

    def test_real_worker_five_rounds(self):
        from tools.http_capture import HttpCaptureWorker
        workers = []
        for round_no in range(5):
            port = _free_port()
            worker = HttpCaptureWorker(port=port, apply_system_proxy=False)
            worker.start()
            workers.append(worker)
            try:
                self.assertTrue(worker.wait_ready(timeout=15), f'round{round_no}: 未就绪')
                probe = socket.create_connection(('127.0.0.1', port), timeout=1)
                probe.close()
                worker.stop(join_timeout=2.0)
                self.assertFalse(worker._thread.is_alive(), f'round{round_no}: 线程未退出')
                self.assertFalse(worker._poll_thread.is_alive(), f'round{round_no}: poll 线程未退出')
                # Windows TIME_WAIT 下端口可能短暂不可重绑，
                # 服务停止的权威证据是线程退出（stop 内部已等待端口释放，另有专项测试）。
            except Exception:
                worker.stop(join_timeout=0.5)
                raise
        # 线程泄漏检查：全部已停止
        for w in workers:
            self.assertFalse(w._thread.is_alive())
            self.assertFalse(w._poll_thread.is_alive())


if __name__ == '__main__':
    unittest.main()
