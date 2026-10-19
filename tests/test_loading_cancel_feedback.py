# -*- coding: utf-8 -*-
"""Focused LD-08 coverage for neutral cancellation feedback and token retirement."""

import os
import sys
import subprocess
import tempfile
import textwrap
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from PyQt6.QtWidgets import QApplication, QWidget

from ui.aurora_progress import AuroraProgress


class LoadingCancelFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.host = QWidget()
        self.host.resize(900, 600)
        self.host.show()
        self.progress = AuroraProgress(self.host, delay_show_ms=300, fail_linger_ms=700)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_pending_cancel_shows_neutral_terminal_and_reuses_linger(self):
        token = self.progress.start_busy('正在扫描…')
        self.assertEqual(self.progress._state, 'pending_busy')
        self.assertFalse(self.progress.is_visible_to_user)

        self.progress.cancel('扫描已取消', token=token)
        self.app.processEvents()

        self.assertEqual(self.progress._state, 'cancelled')
        self.assertEqual(self.progress._label, '扫描已取消')
        self.assertTrue(self.progress.is_visible_to_user)
        self.assertIsNone(self.progress._delay_timer)
        self.assertIsNotNone(self.progress._linger_timer)
        self.assertEqual(self.progress._linger_timer.interval(), 700)
        self.assertFalse(self.progress._anim_timer.isActive())
        image = self.progress.grab().toImage()
        self.assertFalse(image.isNull())
        self.assertIsNone(self.progress.cancel('stale cancel', token=token - 1))
        self.assertEqual(self.progress._label, '扫描已取消')

        retired_generation = self.progress.current_token
        self.progress._linger_timer.timeout.emit()
        self.app.processEvents()
        self.assertEqual(self.progress._state, 'idle')
        self.assertFalse(self.progress.is_visible_to_user)
        self.assertFalse(self.progress._anim_timer.isActive())
        self.progress._on_linger_hide(retired_generation - 1)
        self.assertEqual(self.progress._state, 'idle')

    def test_visible_cancel_retires_late_finish_and_failure_tokens(self):
        token = self.progress.start_busy('正在扫描…', immediate=True)
        self.app.processEvents()
        self.assertTrue(self.progress._anim_timer.isActive())

        self.progress.cancel('扫描已取消', token=token)
        self.assertTrue(self.progress.is_visible_to_user)
        self.assertFalse(self.progress._anim_timer.isActive())

        self.progress.finish('迟到的完成', token=token)
        self.progress.fail('迟到的错误', token=token)
        self.assertEqual(self.progress._state, 'cancelled')
        self.assertEqual(self.progress._label, '扫描已取消')
        self.assertTrue(self.progress.is_visible_to_user)
        self.assertTrue(self.progress._linger_timer.isActive())

    def test_stale_cancel_cannot_replace_a_newer_operation(self):
        token_a = self.progress.start_busy('任务 A', immediate=True)
        token_b = self.progress.start_busy('任务 B', immediate=True)

        self.progress.cancel('任务 A 已取消', token=token_a)

        self.assertEqual(self.progress.current_token, token_b)
        self.assertEqual(self.progress._state, 'busy')
        self.assertEqual(self.progress._label, '任务 B')
        self.assertTrue(self.progress._anim_timer.isActive())

    def test_english_cancel_chip_fits_existing_badge_width(self):
        self.host.language = 'en'
        token = self.progress.start_busy('Scanning…', immediate=True)
        self.progress.cancel('Scan cancelled', token=token)

        label = self.progress._cancel_chip_label()
        self.assertEqual(label, 'Cancelled')

    def test_hide_now_clears_cancel_terminal_timers(self):
        token = self.progress.start_busy('正在扫描…', immediate=True)
        self.progress.cancel('扫描已取消', token=token)
        linger_generation = self.progress.current_token

        self.progress.hide_now()
        self.assertEqual(self.progress._state, 'idle')
        self.assertIsNone(self.progress._delay_timer)
        self.assertIsNone(self.progress._linger_timer)
        self.assertFalse(self.progress._anim_timer.isActive())
        self.assertFalse(self.progress.is_visible_to_user)

        self.progress._on_linger_hide(linger_generation)
        self.assertEqual(self.progress._state, 'idle')
        self.assertFalse(self.progress.is_visible_to_user)

    def test_real_scan_cancel_callback_and_late_worker_callbacks_keep_stop_state(self):
        # Import and construct the production panel in a child process whose config
        # paths are redirected before imports; no module-global config mutation can
        # contaminate the test runner or unrelated modules.
        child = textwrap.dedent(f'''\
            import os, sys, tempfile
            from unittest.mock import patch
            sys.path.insert(0, {PROJECT_DIR!r})
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            with tempfile.TemporaryDirectory(prefix='pengtools-panel-cancel-') as root:
                import config as app_config
                app_config.CONFIG_DIR = root
                for name, value in tuple(vars(app_config).items()):
                    if name.endswith(('_FILE', '_DIR')) and isinstance(value, str):
                        setattr(app_config, name, os.path.join(root, os.path.basename(value)))
                from PyQt6.QtWidgets import QApplication
                from panels.ai_workbench_panel import AiWorkbenchPanel
                app = QApplication.instance() or QApplication([])
                class FakeWorker:
                    def __init__(self):
                        self._cancelled = False
                        self.cancelled_writes = 0
                    @property
                    def cancelled(self):
                        return self._cancelled
                    @cancelled.setter
                    def cancelled(self, value):
                        self.cancelled_writes += 1
                        self._cancelled = value
                with patch('panels.ai_workbench_panel.load_connections', return_value=[]), \\
                     patch('panels.ai_workbench_panel.load_snapshot', return_value=None), \\
                     patch('panels.ai_workbench_panel.show_info'), \\
                     patch('panels.ai_workbench_panel.show_error'):
                    panel = AiWorkbenchPanel(language='zh')
                    worker = FakeWorker()
                    token = panel.loading.start_busy('正在扫描结构…', immediate=True)
                    panel._db_token = token
                    panel._worker = worker
                    panel._cancel_scan()
                    assert worker.cancelled_writes == 1
                    assert worker.cancelled is True
                    assert panel.loading._state == 'cancelled'
                    assert panel.loading._label == '扫描已取消'
                    # Exercise the real production success and failure callbacks with
                    # the retired Aurora token; they cannot repaint the cancel state.
                    panel._on_db_ok('test', {{'ok': True}}, {{}}, token=token)
                    panel._on_db_fail('late scan error', token=token)
                    assert panel.loading._state == 'cancelled'
                    assert panel.loading._label == '扫描已取消'
                    assert panel.loading._linger_timer.isActive()
                    panel.deleteLater()
                    app.processEvents()
        ''')
        result = subprocess.run(
            [sys.executable, '-c', child],
            cwd=PROJECT_DIR,
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(
            result.returncode,
            0,
            f'isolated production callback probe exited {result.returncode}\n'
            f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}',
        )


if __name__ == '__main__':
    unittest.main()
