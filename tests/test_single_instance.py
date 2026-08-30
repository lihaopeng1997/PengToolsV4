# -*- coding: utf-8 -*-
"""单实例守卫回归（Step 4C）：identity、所有权、并发竞争、崩溃恢复、release。

覆盖语义：
- 同 edition 跨 APP_VERSION → 同一 server identity（升级后仍互斥）；
- 不同 edition → 不同 identity（既有产品约束不变）；
- primary/secondary 真实 IPC 通知；
- 多进程同时竞争 → 恰好 1 个 PRIMARY，其余 SECONDARY，主实例存活期内不换人；
- 崩溃（强杀持有者）后可重新成为 PRIMARY（无永久假锁）；release 后可立即被接管。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PyQt6.QtWidgets import QApplication
    QT_AVAILABLE = True
except Exception:  # pragma: no cover
    QT_AVAILABLE = False

# 进程级竞争测试：主实例至少保持存活的窗口期（秒），期间其余进程必须全部判为 SECONDARY，
# 防止 PRIMARY 快速退出后另一进程接管导致 exactly-one 假通过。
PRIMARY_HOLD_SECONDS = 3.0


def _random_name() -> str:
    return f'PengToolsHub.Test.{uuid.uuid4().hex[:12]}'


class ServerIdentityTests(unittest.TestCase):
    def test_server_identity_cross_version_and_edition_isolation(self):
        """同 edition 跨 APP_VERSION → 同 identity；不同 edition → 仍隔离。"""
        from ui.single_instance import local_server_name
        self.assertEqual(
            local_server_name(edition='Private', version='4.27'),
            local_server_name(edition='Private', version='4.28'),
        )
        self.assertEqual(local_server_name(edition='Private', version='4.27'),
                         'PengToolsHub.Private')
        standard = local_server_name(edition='Standard', version='4.28')
        self.assertNotEqual(standard, 'PengToolsHub.Private')
        self.assertEqual(standard, 'PengToolsHub.Standard')


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class OwnershipIpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_primary_secondary_real_ipc_notify(self):
        """恰一个 PRIMARY；secondary 经真实 QLocalSocket 通知触发 activate。"""
        from ui.single_instance import SingleInstanceGuard
        name = _random_name()
        g1 = SingleInstanceGuard(server_name=name, parent=self.app)
        try:
            self.assertTrue(g1.try_become_primary())
            self.assertTrue(g1.is_primary)

            hits = {'n': 0}
            g1.activate_requested.connect(lambda: hits.__setitem__('n', hits['n'] + 1))

            g2 = SingleInstanceGuard(server_name=name, parent=self.app)
            self.assertFalse(g2.try_become_primary())
            self.assertFalse(g2.is_primary)

            # primary 的 newConnection/readyRead 信号需要事件循环泵送
            deadline = time.time() + 3
            while hits['n'] == 0 and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertEqual(hits['n'], 1, 'secondary 的 activate 通知未到达 primary')
        finally:
            g1.release()


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class SimultaneousContentionTests(unittest.TestCase):
    def test_five_process_race_exactly_one_primary(self):
        """5 个子进程同时抢同一 server name：恰好 1 PRIMARY + 4 SECONDARY，
        且主实例在存活窗口内不换人（防 PRIMARY 秒退导致假通过）。"""
        name = _random_name()
        child_code = (
            "import sys, time\n"
            "from PyQt6.QtCore import QCoreApplication, QTimer\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from ui.single_instance import SingleInstanceGuard\n"
            "app = QCoreApplication([])\n"
            "g = SingleInstanceGuard(server_name=sys.argv[1], parent=app)\n"
            "if g.try_become_primary():\n"
            "    print('PRIMARY', flush=True)\n"
            f"    QTimer.singleShot(int({PRIMARY_HOLD_SECONDS} * 1000), app.quit)\n"
            "    app.exec()\n"
            "else:\n"
            "    # SECONDARY：activate 已送达，立即退出（与真实 run.py 行为一致）\n"
            "    print('SECONDARY', flush=True)\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, '-c', child_code, name],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace', cwd=ROOT,
            )
            for _ in range(5)
        ]
        results = {'PRIMARY': [], 'SECONDARY': []}
        deadline = time.time() + PRIMARY_HOLD_SECONDS + 30
        for p in procs:
            remaining = max(5.0, deadline - time.time())
            try:
                out, _ = p.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                p.kill()
                self.fail('竞争子进程超时未退出')
            line = (out or '').strip().splitlines()
            tag = line[-1].strip() if line else f'NO_OUTPUT(rc={p.returncode})'
            self.assertIn(tag, results, f'子进程输出异常：{tag}')
            results[tag].append(p.pid)

        self.assertEqual(len(results['PRIMARY']), 1,
                         f'必须恰好 1 个 PRIMARY，实际 {len(results["PRIMARY"])}')
        self.assertEqual(len(results['SECONDARY']), 4,
                         f'必须 4 个 SECONDARY，实际 {len(results["SECONDARY"])}')


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class CrashRecoveryAndReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_release_then_reacquire_primary(self):
        """§18：release 后同 server_name 的新 Guard 应可立即成为 PRIMARY。"""
        from ui.single_instance import SingleInstanceGuard
        name = _random_name()
        g1 = SingleInstanceGuard(server_name=name, parent=self.app)
        self.assertTrue(g1.try_become_primary())
        g1.release()
        self.assertFalse(g1.is_primary)
        g2 = SingleInstanceGuard(server_name=name, parent=self.app)
        try:
            self.assertTrue(g2.try_become_primary(), 'release 后新 Guard 未能接管')
            self.assertTrue(g2.is_primary)
        finally:
            g2.release()

    def test_killed_owner_no_permanent_lock(self):
        """§17：持有者被强杀（模拟异常退出）后，下一次启动必须能成为 PRIMARY，
        不得留下永久假锁。子进程持有真实 server，taskkill 模拟崩溃。"""
        name = _random_name()
        hold_code = (
            "import sys, time\n"
            "from PyQt6.QtCore import QCoreApplication\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from ui.single_instance import SingleInstanceGuard\n"
            "app = QCoreApplication([])\n"
            "g = SingleInstanceGuard(server_name=sys.argv[1], parent=app)\n"
            "assert g.try_become_primary(), 'holder failed to become primary'\n"
            "print('HELD', flush=True)\n"
            "app.exec()\n"
        )
        holder = subprocess.Popen(
            [sys.executable, '-c', hold_code, name],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', cwd=ROOT,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'HELD',
                             '持有者子进程未正常持有 server')
            subprocess.run(['taskkill', '/F', '/PID', str(holder.pid)],
                           capture_output=True, timeout=10)
            holder.wait(timeout=10)
            # Windows 命名管道随进程消亡；留一小段调度余量
            time.sleep(0.4)
            from ui.single_instance import SingleInstanceGuard
            g = SingleInstanceGuard(server_name=name, parent=self.app)
            try:
                self.assertTrue(g.try_become_primary(),
                                f'强杀持有者后无法接管：{g.last_error}')
                self.assertTrue(g.is_primary)
            finally:
                g.release()
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
