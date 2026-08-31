# -*- coding: utf-8 -*-
"""抓包生命周期回归：停止→立即重启闭环、pending start、stale 回调隔离、真实同端口 5 轮。"""
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

from tools.capture_lifecycle import (
    IDLE, STARTING, RUNNING, STOPPING, CaptureLifecycle, resolve_resume_action,
)

PANEL_SRC = io.open(os.path.join(ROOT, 'panels', 'interface_debug_panel.py'),
                    encoding='utf-8').read()


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LifecycleDoubleBeginRegressionTest(unittest.TestCase):
    """二十三节：finish_stop 不得提前 STARTING；重启必须再次 begin_start。"""

    def test_double_begin_regression(self):
        lc = CaptureLifecycle()
        epoch1 = lc.begin_start()
        lc.mark_running(epoch1)
        self.assertTrue(lc.begin_stop(epoch1))
        # STOPPING 期间立即 START → pending
        self.assertIsNone(lc.begin_start())
        self.assertTrue(lc.pending_start)
        # finish_stop：IDLE + should_restart=True，但不得进入 STARTING
        should_restart = lc.finish_stop(epoch1)
        self.assertTrue(should_restart)
        self.assertEqual(lc.state, IDLE)
        # 新启动必须再次 begin_start
        epoch2 = lc.begin_start()
        self.assertIsNotNone(epoch2)
        self.assertNotEqual(epoch2, epoch1)
        self.assertEqual(lc.state, STARTING)

    def test_finish_stop_epoch_guard(self):
        lc = CaptureLifecycle()
        e1 = lc.begin_start()
        lc.mark_running(e1)
        lc.begin_stop(e1)
        # 旧 epoch finish_stop：拒绝且状态不变
        self.assertFalse(lc.finish_stop(e1 + 100))
        self.assertEqual(lc.state, STOPPING)
        # STOPPING 期间记录 pending start（应重启）
        self.assertIsNone(lc.begin_start())
        self.assertTrue(lc.finish_stop(e1))   # should_restart=True
        self.assertEqual(lc.state, IDLE)
        # 重复 finish_stop：False
        self.assertFalse(lc.finish_stop(e1))

    def test_fail_start_current_only(self):
        lc = CaptureLifecycle()
        e1 = lc.begin_start()
        self.assertFalse(lc.fail_start(e1 + 50))   # 旧 epoch failure 不影响当前
        self.assertEqual(lc.state, STARTING)
        self.assertTrue(lc.fail_start(e1))
        self.assertEqual(lc.state, IDLE)

    def test_fail_runtime_current_only(self):
        lc = CaptureLifecycle()
        e = lc.begin_start()
        lc.mark_running(e)
        self.assertFalse(lc.fail_runtime(e + 50))
        self.assertEqual(lc.state, RUNNING)
        self.assertTrue(lc.fail_runtime(e))
        self.assertEqual(lc.state, IDLE)


class ResumeDecisionTest(unittest.TestCase):
    def test_healthy_resumes(self):
        self.assertEqual(resolve_resume_action(True, True), 'resume')

    def test_dead_restores(self):
        for worker, port in ((False, False), (True, False), (False, True)):
            self.assertEqual(resolve_resume_action(worker, port), 'restore')


class UiNonBlockingGuardTest(unittest.TestCase):
    """UI 主线程无 join；lifecycle/resume 接线存在。"""

    def test_panel_has_no_thread_join(self):
        self.assertNotIn('thread.join(', PANEL_SRC)
        self.assertNotIn('_await_previous_capture_stop', PANEL_SRC)

    def test_panel_wiring(self):
        for snippet in (
            'self._lifecycle.begin_start()',
            'self._lifecycle.begin_stop(self._capture_epoch)',
            'self._lifecycle.finish_stop(stop_epoch)',
            'self._sig_capture_stop_finalized.emit(int(stop_epoch), bool(should_restart))',
            'self._lifecycle.mark_running(boot_epoch)',
            'self._lifecycle.fail_start(boot_epoch)',
            'self._lifecycle.fail_runtime(self._capture_epoch)',
            'resolve_resume_action',
        ):
            self.assertIn(snippet, PANEL_SRC, f'缺少接线：{snippet}')

    def test_pending_start_returns_immediately_under_slow_stop(self):
        lc = CaptureLifecycle()
        done = []
        e = lc.begin_start()
        lc.mark_running(e)
        lc.begin_stop(e)

        def slow_stop():
            time.sleep(1.5)
            done.append(True)
            lc.finish_stop(e)

        th = threading.Thread(target=slow_stop, daemon=True)
        th.start()
        time.sleep(0.05)
        t0 = time.perf_counter()
        result = lc.begin_start()   # 历史路径在此 join 2.5s
        elapsed = time.perf_counter() - t0
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.3, f'begin_start 阻塞了 {elapsed:.2f}s')
        th.join(timeout=3)
        self.assertTrue(done)
        self.assertGreaterEqual(lc.begin_start(), 0)


class PanelImmediateRestartTest(unittest.TestCase):
    """二十二节：Panel wiring——STOPPING 期间 start 立即返回记 pending；
    finalize signal 后真正创建新 boot（mock worker，不碰系统代理）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cert_patch = patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=True)
        self.cert_patch.start()
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        self.panel = panel
        self.epoch1 = panel._lifecycle.begin_start()
        panel._lifecycle.mark_running(self.epoch1)
        panel._capture_epoch = self.epoch1
        panel._listening = True
        panel._ie_worker = MagicMock()

    def tearDown(self):
        self.panel.close()
        self.cert_patch.stop()

    def test_immediate_restart_creates_new_boot(self):
        from tools.capture_lifecycle import STOPPING, STARTING, RUNNING
        panel = self.panel
        def slow_stop(*args, **kwargs):
            time.sleep(1.0)
        panel._ie_worker.stop = MagicMock(side_effect=slow_stop)
        class _FakeBootWorker:
            def __init__(self, port, **kwargs):
                self.port = port
                self.ready = True
                self._thread = None
                self._poll_thread = None
                self._loop = None
                self._master = None
            def start(self):
                pass
            def wait_ready(self, timeout=None):
                return True
            def stop(self, *a, **k):
                pass
        fake_cls = MagicMock(side_effect=lambda port, **kw: _FakeBootWorker(port))
        panel._ensure_capture_ready_silently = MagicMock()
        with patch('tools.ie_proxy.restore_proxy_from_snapshot'), \
             patch('tools.ie_proxy.mark_capture_proxy_inactive'), \
             patch('tools.ie_proxy.ensure_system_proxy_safe'), \
             patch('tools.http_capture.HttpCaptureWorker', fake_cls):
            panel._stop_listen()
            self.assertEqual(panel._lifecycle.state, STOPPING)
            t0 = time.perf_counter()
            panel._start_local_proxy()
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 0.3, f'立即重启调用阻塞了 {elapsed:.2f}s')
            self.assertTrue(panel._lifecycle.pending_start)
            self.assertEqual(panel._lifecycle.state, STOPPING)
            self.assertIsNone(panel._capture_boot_worker)
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            self.app.processEvents()
            if (panel._lifecycle.state in (STARTING, RUNNING)
                    and panel._lifecycle.epoch > self.epoch1
                    and panel._capture_boot_worker is not None):
                break
            time.sleep(0.05)
        self.assertIn(panel._lifecycle.state, (STARTING, RUNNING))
        self.assertGreater(panel._lifecycle.epoch, self.epoch1)
        self.assertIsNotNone(panel._capture_boot_worker)
        self.assertFalse(panel._lifecycle.pending_start)
        panel._stop_listen()
        th = panel._capture_stop_thread
        if th is not None and th.is_alive():
            th.join(timeout=5)
        bw = panel._capture_boot_worker
        if bw is not None and hasattr(bw, 'wait'):
            bw.wait(5000)
        panel._ie_worker = None


class EpochAwareCallbackTest(unittest.TestCase):
    """二十八节：old stopped/error 不影响 current；current stopped/error → IDLE。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _make_panel(self, epoch):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        e = panel._lifecycle.begin_start()
        panel._lifecycle.mark_running(e)
        panel._capture_epoch = epoch
        panel._listening = True
        panel._channel_ready = True
        panel._ie_worker = MagicMock()
        return panel

    def test_old_stopped_ignored_new_stopped_idles(self):
        panel = self._make_panel(epoch=1)
        old_worker = MagicMock()
        panel._ie_worker = old_worker
        with patch('tools.ie_proxy.ensure_system_proxy_safe'):
            panel._on_capture_stopped(0)      # 旧 epoch stopped：忽略
        self.assertTrue(panel._listening)
        self.assertIs(panel._ie_worker, old_worker)
        panel._on_capture_stopped(99)     # 未知更新 epoch：忽略
        self.assertTrue(panel._listening)
        # 当前 epoch stopped：RUNNING → IDLE
        with patch('tools.ie_proxy.ensure_system_proxy_safe'):
            panel._on_capture_stopped(1)
        self.assertFalse(panel._listening)
        self.assertIsNone(panel._ie_worker)
        self.assertEqual(panel._lifecycle.state, IDLE)
        panel.close()

    def test_old_error_ignored_current_error_idles(self):
        panel = self._make_panel(epoch=1)
        old_worker = MagicMock()
        panel._ie_worker = old_worker
        with patch('panels.interface_debug_panel.show_warning'), \
             patch('tools.ie_proxy.restore_proxy_from_snapshot'), \
             patch('tools.ie_proxy.mark_capture_proxy_inactive'), \
             patch('tools.ie_proxy.ensure_system_proxy_safe'):
            panel._on_capture_error(0, '旧错误')     # 旧 epoch：忽略
            self.assertTrue(panel._listening)
            self.assertIs(panel._ie_worker, old_worker)
            panel._on_capture_error(1, '当前错误')   # 当前 epoch：IDLE + 恢复安全
            self.assertFalse(panel._listening)
            self.assertIsNone(panel._ie_worker)
            self.assertEqual(panel._lifecycle.state, IDLE)
        panel.close()


class StaleBootResultTest(unittest.TestCase):
    """二十四节：boot success/failure 的过期结果不得影响当前 epoch（直接测 handler）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _make_panel(self, epoch):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        e = panel._lifecycle.begin_start()
        panel._lifecycle.mark_running(e)
        panel._capture_epoch = epoch
        panel._listening = True
        panel._ie_worker = MagicMock()
        return panel

    def test_stale_success_not_running(self):
        panel = self._make_panel(epoch=1)
        stale_worker = MagicMock()
        stale_result = {'ok': True, 'worker': stale_worker, 'epoch': 999, 'port': 8899}
        detached = []
        panel._detach_capture_worker = lambda w: detached.append(w)
        # 过期结果（result.epoch=999 ≠ 当前 boot_epoch=1）：worker 被 detach+stop，
        # 当前 RUNNING 状态不受影响
        panel._on_capture_boot_result(stale_result, boot_epoch=1, port=8899, title='t', zh=True)
        self.assertEqual(panel._lifecycle.state, RUNNING)
        self.assertIn(stale_worker, detached)
        panel.close()

    def test_stale_failure_not_affect_current(self):
        panel = self._make_panel(epoch=1)
        before_listening = panel._listening
        before_worker = panel._ie_worker
        stale_result = {'ok': False, 'error': '过期失败', 'worker': None, 'epoch': 999}
        with patch('panels.interface_debug_panel.show_warning'), \
             patch('tools.ie_proxy.restore_proxy_from_snapshot'):
            panel._on_capture_boot_result(stale_result, boot_epoch=999, port=8899, title='t', zh=True)
        self.assertEqual(panel._lifecycle.state, RUNNING)   # 当前 RUNNING 不受影响
        self.assertTrue(before_listening)
        self.assertIs(panel._ie_worker, before_worker)
        panel.close()

    def test_boot_success_marks_running(self):
        panel = self._make_panel(epoch=1)
        panel._lifecycle.fail_runtime(1)         # 先回 IDLE
        e2 = panel._lifecycle.begin_start()      # 新一轮 STARTING
        self.assertEqual(panel._lifecycle.state, STARTING)
        worker = MagicMock()
        result = {'ok': True, 'worker': worker, 'epoch': e2, 'port': 8899}
        panel._on_capture_boot_result(result, boot_epoch=e2, port=8899, title='t', zh=True)
        self.assertEqual(panel._lifecycle.state, RUNNING)
        self.assertIs(panel._ie_worker, worker)
        self.assertTrue(panel._listening)
        panel.close()

    def test_boot_failure_back_to_idle(self):
        panel = self._make_panel(epoch=1)
        panel._lifecycle.fail_runtime(1)         # 先回 IDLE
        e2 = panel._lifecycle.begin_start()      # 新一轮 STARTING
        self.assertEqual(panel._lifecycle.state, STARTING)
        result = {'ok': False, 'error': '端口被占用', 'worker': None, 'epoch': e2}
        with patch('panels.interface_debug_panel.show_warning'), \
             patch('tools.ie_proxy.restore_proxy_from_snapshot'):
            panel._on_capture_boot_result(result, boot_epoch=e2, port=8899, title='t', zh=True)
        self.assertEqual(panel._lifecycle.state, IDLE)
        panel.close()


class RealWorkerSamePortFiveRoundsTest(unittest.TestCase):
    """Step 2C-1B：同一个端口连续 start→ready→stop 5 轮（不碰系统代理）。

    根因（mitmproxy 12.2.3 实源码核实）：Master.shutdown() 只置位 should_exit 结束
    run()，从不关闭 asyncio.Server 监听 socket，且引擎被 mitmproxy.ctx.master 模块级
    全局引用钉住——旧端口长期不释放，Round 2 起绑定失败。修复后 stop() 在 mitmproxy
    自己的 loop 上执行 server=False / servers.update([]) 主动关停 listener，再 shutdown。
    """

    def test_same_port_five_rounds(self):
        from tools.http_capture import HttpCaptureWorker
        port = _free_port()
        workers = []
        errors = []
        try:
            for round_no in range(1, 6):
                worker = HttpCaptureWorker(
                    port=port, apply_system_proxy=False, on_error=errors.append)
                worker.start()
                workers.append(worker)
                self.assertTrue(worker.wait_ready(timeout=15),
                                f'Round {round_no}: 同端口 worker 未就绪')
                probe = socket.create_connection(('127.0.0.1', port), timeout=1)
                probe.close()
                worker.stop(join_timeout=2.0)
                self.assertEqual(errors, [], f'Round {round_no}: 意外错误 {errors}')
                self.assertFalse(worker._thread.is_alive(), f'Round {round_no}: 线程未退出')
                self.assertFalse(worker._poll_thread.is_alive(), f'Round {round_no}: poll 未退出')
                self.assertIsNone(worker._loop, f'Round {round_no}: loop 未清')
                self.assertIsNone(worker._master, f'Round {round_no}: master 未清')
                try:
                    probe2 = socket.create_connection(('127.0.0.1', port), timeout=1)
                    probe2.close()
                    self.fail(f'Round {round_no}: stop 后端口仍在监听')
                except OSError:
                    pass
        finally:
            for w in workers:
                w.stop(join_timeout=0.5)


class StopFinalizedEpochGuardTest(unittest.TestCase):
    """Step 2C-1B：_on_capture_stop_finalized 的 epoch 守卫。

    过期 finalized（epoch 不匹配）不得回写 _capture_epoch / _capture_stop_thread /
    _ie_worker / UI，也不得触发重启；当前有效 finalized + pending restart 必须重启出
    新 epoch 并进入 STARTING/RUNNING。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cert_patch = patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=True)
        self.cert_patch.start()

    def tearDown(self):
        self.cert_patch.stop()

    def _make_panel_running(self, epoch_target):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        e = None
        for _ in range(epoch_target):
            if e is not None:
                panel._lifecycle.fail_runtime(e)   # → IDLE，允许下一轮 begin_start
            e = panel._lifecycle.begin_start()
            panel._lifecycle.mark_running(e)
        panel._capture_epoch = e
        panel._listening = True
        panel._channel_ready = True
        panel._ie_worker = MagicMock()
        return panel, e

    def test_stale_finalized_ignored(self):
        panel, current = self._make_panel_running(2)   # lifecycle.epoch = 2
        old_worker = panel._ie_worker
        panel._start_local_proxy = MagicMock()
        # 过期 finalized（epoch=1）——即使携带重启请求也不得产生任何影响
        panel._on_capture_stop_finalized(current - 1, True)
        self.assertEqual(panel._lifecycle.epoch, current)
        self.assertEqual(panel._lifecycle.state, RUNNING)
        self.assertEqual(panel._capture_epoch, current, 'epoch 镜像不得回退')
        self.assertIs(panel._ie_worker, old_worker)
        self.assertTrue(panel._listening)
        self.assertTrue(panel._channel_ready)
        panel._start_local_proxy.assert_not_called()
        panel.close()

    def test_current_finalized_pending_restart_reboots(self):
        panel, e1 = self._make_panel_running(1)
        self.assertTrue(panel._lifecycle.begin_stop(e1))
        self.assertIsNone(panel._lifecycle.begin_start())   # STOPPING：记 pending
        self.assertTrue(panel._lifecycle.pending_start)
        self.assertTrue(panel._lifecycle.finish_stop(e1))   # stop 线程收尾 → IDLE
        self.assertEqual(panel._lifecycle.state, IDLE)

        class _FakeBootWorker:
            def __init__(self, port, **kwargs):
                self.port = port
                self.ready = True
                self._thread = None
                self._poll_thread = None
                self._loop = None
                self._master = None

            def start(self):
                pass

            def wait_ready(self, timeout=None):
                return True

            def stop(self, *a, **k):
                pass

        fake_cls = MagicMock(side_effect=lambda port, **kw: _FakeBootWorker(port))
        panel._ensure_capture_ready_silently = MagicMock()
        with patch('tools.ie_proxy.restore_proxy_from_snapshot'), \
             patch('tools.ie_proxy.mark_capture_proxy_inactive'), \
             patch('tools.ie_proxy.ensure_system_proxy_safe'), \
             patch('tools.http_capture.HttpCaptureWorker', fake_cls):
            # 当前有效 finalized：guard 放行 → 自动重启 → 新 epoch STARTING/RUNNING
            panel._on_capture_stop_finalized(e1, True)
            deadline = time.perf_counter() + 10
            while time.perf_counter() < deadline:
                self.app.processEvents()
                if (panel._lifecycle.state in (STARTING, RUNNING)
                        and panel._lifecycle.epoch > e1):
                    break
                time.sleep(0.05)
            self.assertIn(panel._lifecycle.state, (STARTING, RUNNING))
            self.assertGreater(panel._lifecycle.epoch, e1, 'pending restart 必须产生新 epoch')
            self.assertFalse(panel._lifecycle.pending_start)
            # 清理：fake boot 无真实引擎，走正常 stop 收尾（lifecycle 回 IDLE）
            panel._stop_listen()
            th = panel._capture_stop_thread
            if th is not None and th.is_alive():
                th.join(timeout=5)
        self.assertEqual(panel._lifecycle.state, IDLE)
        panel._ie_worker = None
        panel.close()


class H2MitigationGuardTest(unittest.TestCase):
    """安全守护：vulnerable-transitive dependency 的执行路径缓解。

    pip-audit（2026-08-30）确认 h2 4.3.0 受 CVE-2026-71554 / GHSA-6hr6-w5qg-qmwg
    影响（multiple Host header request smuggling，fixed 4.4.1）；而 mitmproxy
    12.2.3 的 Requires-Dist 精确 pin h2>=4.3.0,<=4.3.0，无法在不制造依赖冲突的
    前提下升级。产品侧缓解 = 抓包引擎显式禁用 HTTP/2 执行路径（http2=False）。
    本测试只守护该缓解措施不被移除，不声称 CVE 本身已修复。
    """

    def test_http2_disabled_in_capture_engine(self):
        src = io.open(os.path.join(ROOT, 'tools', 'http_capture.py'),
                      encoding='utf-8').read()
        self.assertIn(
            "('http2', False),", src,
            '抓包引擎必须保持 http2=False（h2 4.3.0 存在 CVE-2026-71554，'
            'mitmproxy 12.2.3 无法升级 h2，禁用 HTTP/2 是执行路径缓解）')


class HttpsCertConsentAndSafetyTests(unittest.TestCase):
    """HTTPS CA 证书明确授权、真实状态检测与安全移除回归测试。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_ensure_capture_ready_silently_never_installs_cert(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        with patch('tools.ie_proxy.install_user_root_cert') as mock_install, \
             patch('tools.ie_proxy.ensure_mitm_ca_exists') as mock_ensure:
            panel._ensure_capture_ready_silently()
            mock_ensure.assert_called_once()
            mock_install.assert_not_called()
        panel.close()

    def test_first_capture_prompts_consent_and_cancels(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        with patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=False), \
             patch('ui.confirm_dialog.confirm_https_cert_consent', return_value=False) as mock_consent, \
             patch('tools.ie_proxy.install_user_root_cert') as mock_install, \
             patch('tools.http_capture.HttpCaptureWorker') as mock_worker:
            panel._start_local_proxy()
            mock_consent.assert_called_once()
            mock_install.assert_not_called()
            mock_worker.assert_not_called()
            self.assertEqual(panel._lifecycle.state, IDLE)
            self.assertFalse(panel._listening)
        panel.close()

    def test_first_capture_consent_install_success_proceeds(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')

        class _FakeBootWorker:
            def __init__(self, port, **kwargs):
                self.port = port
                self.ready = True
            def start(self): pass
            def wait_ready(self, timeout=None): return True
            def stop(self, *a, **k): pass

        installed_state = [False]
        def fake_is_installed(*args, **kwargs):
            return installed_state[0]
        def fake_install(*args, **kwargs):
            installed_state[0] = True
            return 'FAKE_THUMB_123'

        with patch('tools.ie_proxy.is_recorded_root_cert_installed', side_effect=fake_is_installed), \
             patch('ui.confirm_dialog.confirm_https_cert_consent', return_value=True) as mock_consent, \
             patch('tools.ie_proxy.install_user_root_cert', side_effect=fake_install) as mock_install, \
             patch('tools.http_capture.HttpCaptureWorker', side_effect=lambda port, **kw: _FakeBootWorker(port)), \
             patch('tools.ie_proxy.apply_local_proxy', return_value={}):
            panel._start_local_proxy()
            mock_consent.assert_called_once()
            mock_install.assert_called_once()
            self.assertIn(panel._lifecycle.state, (STARTING, RUNNING))
        panel.close()

    def test_first_capture_install_failure_aborts_start(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        from tools.ie_proxy import IeProxyError
        panel = InterfaceDebugPanel('zh')
        with patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=False), \
             patch('ui.confirm_dialog.confirm_https_cert_consent', return_value=True), \
             patch('tools.ie_proxy.install_user_root_cert', side_effect=IeProxyError('certutil error')), \
             patch('panels.interface_debug_panel.show_warning') as mock_warn, \
             patch('tools.http_capture.HttpCaptureWorker') as mock_worker:
            panel._start_local_proxy()
            mock_warn.assert_called_once()
            mock_worker.assert_not_called()
            self.assertEqual(panel._lifecycle.state, IDLE)
            self.assertFalse(panel._listening)
        panel.close()

    def test_existing_installed_ca_skips_consent_dialog(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')

        class _FakeBootWorker:
            def __init__(self, port, **kwargs):
                self.port = port
                self.ready = True
            def start(self): pass
            def wait_ready(self, timeout=None): return True
            def stop(self, *a, **k): pass

        with patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=True), \
             patch('ui.confirm_dialog.confirm_https_cert_consent') as mock_consent, \
             patch('tools.http_capture.HttpCaptureWorker', side_effect=lambda port, **kw: _FakeBootWorker(port)), \
             patch('tools.ie_proxy.apply_local_proxy', return_value={}):
            panel._start_local_proxy()
            mock_consent.assert_not_called()
            self.assertIn(panel._lifecycle.state, (STARTING, RUNNING))
        panel.close()

    def test_stale_thumbprint_detected_as_not_installed(self):
        from tools.ie_proxy import is_recorded_root_cert_installed
        with patch('tools.ie_proxy.load_interface_debug_config', return_value={'ie_certificate_thumbprint': 'STALE_THUMB_999'}), \
             patch('tools.ie_proxy.is_current_user_root_cert_installed', return_value=False):
            self.assertFalse(is_recorded_root_cert_installed('STALE_THUMB_999'))
            self.assertFalse(is_recorded_root_cert_installed())

    def test_remove_cert_while_listening_is_blocked(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        panel._listening = True
        with patch('tools.ie_proxy.remove_recorded_cert') as mock_remove, \
             patch('panels.interface_debug_panel.show_warning') as mock_warn:
            panel._remove_ie_cert()
            mock_remove.assert_not_called()
            mock_warn.assert_called_once()
            self.assertIn('停止监听', mock_warn.call_args[0][2])
        panel.close()

    def test_remove_cert_failure_retains_thumbprint(self):
        from tools.ie_proxy import remove_recorded_cert, IeProxyError
        cfg_mock = {'ie_certificate_thumbprint': 'THUMB_XYZ'}
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.stderr = 'access denied'
        proc_fail.stdout = ''

        with patch('tools.ie_proxy.load_interface_debug_config', return_value=cfg_mock), \
             patch('tools.ie_proxy.save_interface_debug_config') as mock_save, \
             patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=True), \
             patch('subprocess.run', return_value=proc_fail):
            with self.assertRaises(IeProxyError):
                remove_recorded_cert('THUMB_XYZ')
            # 失败时绝不把 thumbprint 清空
            mock_save.assert_not_called()
            self.assertEqual(cfg_mock['ie_certificate_thumbprint'], 'THUMB_XYZ')

    def test_remove_cert_success_clears_thumbprint(self):
        from tools.ie_proxy import remove_recorded_cert
        cfg_mock = {'ie_certificate_thumbprint': 'THUMB_XYZ'}
        proc_ok = MagicMock()
        proc_ok.returncode = 0

        with patch('tools.ie_proxy.load_interface_debug_config', return_value=cfg_mock), \
             patch('tools.ie_proxy.save_interface_debug_config') as mock_save, \
             patch('tools.ie_proxy.is_recorded_root_cert_installed', side_effect=[True, False]), \
             patch('subprocess.run', return_value=proc_ok):
            res = remove_recorded_cert('THUMB_XYZ')
            self.assertTrue(res)
            self.assertEqual(cfg_mock['ie_certificate_thumbprint'], '')
            mock_save.assert_called_once()

    def test_install_user_root_cert_verified_success(self):
        from tools.ie_proxy import install_user_root_cert
        cfg_mock = {'ie_certificate_thumbprint': ''}
        proc_ok = MagicMock()
        proc_ok.returncode = 0
        proc_ok.stderr = ''
        proc_ok.stdout = ''

        with patch('tools.ie_proxy.ensure_mitm_ca_exists', return_value=r'C:\fake\mitm.cer'), \
             patch('tools.ie_proxy.cert_sha1_thumbprint', return_value='NEW_THUMB_123'), \
             patch('subprocess.run', return_value=proc_ok), \
             patch('tools.ie_proxy.is_current_user_root_cert_installed', return_value=True), \
             patch('tools.ie_proxy.load_interface_debug_config', return_value=cfg_mock), \
             patch('tools.ie_proxy.save_interface_debug_config') as mock_save:
            thumb = install_user_root_cert()
            self.assertEqual(thumb, 'NEW_THUMB_123')
            self.assertEqual(cfg_mock['ie_certificate_thumbprint'], 'NEW_THUMB_123')
            mock_save.assert_called_once_with(cfg_mock)

    def test_install_user_root_cert_false_success_raises_and_preserves_config(self):
        from tools.ie_proxy import install_user_root_cert, IeProxyError
        cfg_mock = {'ie_certificate_thumbprint': 'OLD_THUMB_999'}
        proc_ok = MagicMock()
        proc_ok.returncode = 0
        proc_ok.stderr = ''
        proc_ok.stdout = ''

        with patch('tools.ie_proxy.ensure_mitm_ca_exists', return_value=r'C:\fake\mitm.cer'), \
             patch('tools.ie_proxy.cert_sha1_thumbprint', return_value='NEW_THUMB_123'), \
             patch('subprocess.run', return_value=proc_ok), \
             patch('tools.ie_proxy.is_current_user_root_cert_installed', return_value=False), \
             patch('tools.ie_proxy.load_interface_debug_config', return_value=cfg_mock), \
             patch('tools.ie_proxy.save_interface_debug_config') as mock_save:
            with self.assertRaises(IeProxyError) as cm:
                install_user_root_cert()
            self.assertIn('未在当前用户受信任根证书库中检测到该证书', str(cm.exception))
            # 真实状态未通过，严禁覆盖旧配置
            mock_save.assert_not_called()
            self.assertEqual(cfg_mock['ie_certificate_thumbprint'], 'OLD_THUMB_999')

    def test_install_user_root_cert_nonzero_code_raises_and_preserves_config(self):
        from tools.ie_proxy import install_user_root_cert, IeProxyError
        cfg_mock = {'ie_certificate_thumbprint': 'OLD_THUMB_999'}
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.stderr = 'access denied'
        proc_fail.stdout = ''

        with patch('tools.ie_proxy.ensure_mitm_ca_exists', return_value=r'C:\fake\mitm.cer'), \
             patch('tools.ie_proxy.cert_sha1_thumbprint', return_value='NEW_THUMB_123'), \
             patch('subprocess.run', return_value=proc_fail), \
             patch('tools.ie_proxy.load_interface_debug_config', return_value=cfg_mock), \
             patch('tools.ie_proxy.save_interface_debug_config') as mock_save:
            with self.assertRaises(IeProxyError) as cm:
                install_user_root_cert()
            self.assertIn('安装证书失败', str(cm.exception))
            mock_save.assert_not_called()
            self.assertEqual(cfg_mock['ie_certificate_thumbprint'], 'OLD_THUMB_999')

    def test_first_capture_install_false_success_aborts_start(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        from tools.ie_proxy import IeProxyError
        panel = InterfaceDebugPanel('zh')
        with patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=False), \
             patch('ui.confirm_dialog.confirm_https_cert_consent', return_value=True), \
             patch('tools.ie_proxy.install_user_root_cert', side_effect=IeProxyError('证书安装命令已执行，但未在当前用户受信任根证书库中检测到该证书')), \
             patch('panels.interface_debug_panel.show_warning') as mock_warn, \
             patch('tools.http_capture.HttpCaptureWorker') as mock_worker:
            panel._start_local_proxy()
            mock_warn.assert_called_once()
            self.assertIn('未在当前用户受信任根证书库中检测到该证书', mock_warn.call_args[0][2])
            mock_worker.assert_not_called()
            self.assertEqual(panel._lifecycle.state, IDLE)
            self.assertFalse(panel._listening)
        panel.close()

    def test_toolbar_status_and_menu_rendering(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        with patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=False), \
             patch('tools.ie_proxy.is_capture_proxy_suspended', return_value=False):
            panel._refresh_capture_status_text()
            panel._rebuild_capture_actions_menu()
            text = panel.toolbar_hint.text()
            self.assertIn('系统代理：正常', text)
            self.assertIn('HTTPS 解密：未启用', text)

        with patch('tools.ie_proxy.is_recorded_root_cert_installed', return_value=True), \
             patch('tools.ie_proxy.is_capture_proxy_suspended', return_value=False):
            panel._listening = True
            panel._refresh_capture_status_text()
            panel._rebuild_capture_actions_menu()
            text = panel.toolbar_hint.text()
            self.assertIn('监听中', text)
            self.assertIn('HTTPS 解密：已启用', text)
        panel.close()


if __name__ == '__main__':
    unittest.main()
