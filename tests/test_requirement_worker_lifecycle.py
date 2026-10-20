# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from panels.requirement_panel import (
    RequirementDialog,
    RequirementPanel,
    _ACTIVE_SVN_WORKERS,
)


class RequirementWorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._choices = patch('panels.requirement_panel.load_last_choices', return_value={})
        self._choices.start()
        self.addCleanup(self._choices.stop)

    def _panel(self):
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                patch('panels.requirement_panel.load_systems', return_value=[]), \
                patch('panels.requirement_panel.load_requirement_ui', return_value={}):
            panel = RequirementPanel()
        self.addCleanup(self._dispose_widget, panel)
        return panel

    def _dispose_widget(self, widget):
        worker = getattr(widget, '_active_worker', None)
        if worker is not None and worker.isRunning():
            worker.wait(5000)
        widget.close()
        widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _wait_for_worker(self, worker, timeout_ms=3000):
        try:
            if worker.isFinished():
                self.app.processEvents()
                return
        except RuntimeError:
            return
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        self.app.processEvents()
        try:
            finished = worker.isFinished()
        except RuntimeError:
            finished = True
        self.assertTrue(finished, 'worker did not finish before timeout')

    def test_busy_state_uses_current_toolbar_actions(self):
        panel = self._panel()
        self.assertFalse(hasattr(panel, 'checkout_btn'))
        self.assertFalse(hasattr(panel, 'import_btn'))
        self.assertFalse(hasattr(panel, 'system_config_btn'))

        panel._set_busy('probe')
        self.assertFalse(panel._action_checkout.isEnabled())
        self.assertFalse(panel._action_import.isEnabled())
        self.assertFalse(panel._action_syscfg.isEnabled())

        panel._set_busy('')
        self.assertTrue(panel._action_checkout.isEnabled())
        self.assertTrue(panel._action_import.isEnabled())
        self.assertTrue(panel._action_syscfg.isEnabled())

    def test_classify_uses_actual_date_contract_without_legacy_widget(self):
        with patch('panels.requirement_panel.load_systems', return_value=[]):
            dialog = RequirementDialog({
                'title': '日期测试',
                'code': 'REQ-DATE',
                'sql_parts': [],
                'source_files': [],
            })
        self.addCleanup(self._dispose_widget, dialog)
        self.assertFalse(hasattr(dialog, 'online_month'))
        dialog.actual_date.edit.setText('2026-09-20')
        dialog.description_edit.setPlainText('普通需求')

        dialog._classify()

        values = dialog.values()
        self.assertEqual(values['actual_release_date'], '2026-09-20')
        self.assertEqual(values['online_month'], '2026-09')

    def test_completed_worker_releases_registry_and_callback_references(self):
        panel = self._panel()
        results = []
        panel._start_task('后台探测', lambda: 'ok', (), results.append, show_loading=False)
        worker = panel._active_worker

        self._wait_for_worker(worker)

        self.assertEqual(results, ['ok'])
        self.assertNotIn(worker, _ACTIVE_SVN_WORKERS)
        self.assertIsNone(worker.success_callback)
        self.assertIsNone(worker.function)

    def test_success_callback_exception_uses_failure_path(self):
        panel = self._panel()
        panel._task_shows_loading = False

        def fail(_result):
            raise RuntimeError('callback failed')

        with patch('panels.requirement_panel.show_warning') as warning:
            panel._task_succeeded('ok', fail)

        self.assertTrue(panel._task_failed)
        warning.assert_called_once()
        self.assertIn('callback failed', warning.call_args.args[2])

    def test_deleting_panel_while_worker_runs_survives_in_subprocess(self):
        script = textwrap.dedent(
            r'''
            import os
            import sys
            import time
            from unittest.mock import patch

            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            sys.path.insert(0, os.getcwd())

            from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
            from PyQt6.QtWidgets import QApplication
            from panels.requirement_panel import RequirementPanel, _ACTIVE_SVN_WORKERS

            app = QApplication.instance() or QApplication([])
            with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                    patch('panels.requirement_panel.load_systems', return_value=[]), \
                    patch('panels.requirement_panel.load_requirement_ui', return_value={}):
                panel = RequirementPanel()

            def work():
                time.sleep(0.15)
                return 'ok'

            panel._start_task('短任务', work, (), lambda _result: None, show_loading=False)
            panel.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

            loop = QEventLoop()
            QTimer.singleShot(700, loop.quit)
            loop.exec()
            app.processEvents()
            if _ACTIVE_SVN_WORKERS:
                raise SystemExit('worker registry did not drain')
            print('SURVIVED')
            '''
        )
        env = dict(os.environ)
        env['QT_QPA_PLATFORM'] = 'offscreen'
        completed = subprocess.run(
            [sys.executable, '-c', script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f'stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}',
        )
        self.assertIn('SURVIVED', completed.stdout)

    def test_overlay_clears_when_success_dialog_starts_silent_refresh(self):
        """更新成功后弹窗期间启动的静默刷新，不能让“正在更新…”浮层一直挂着。"""
        panel = self._panel()
        finished_labels = []
        real_finish = panel.loading.finish

        def record_finish(label='', *, token=None):
            finished_labels.append((label, token))
            return real_finish(label, token=token)

        panel.loading.finish = record_finish
        silent_started = []

        def success_with_modal_dialog(_result):
            # 模拟 show_success 的模态事件循环：上一任务在此期间结束，并排队静默刷新
            first = panel._active_worker
            self._wait_for_worker(first)
            for _ in range(20):
                self.app.processEvents()
                if panel._active_worker is None:
                    break
            panel._start_task('静默刷新文件库', lambda: {'files': []}, (), lambda _r: None, show_loading=False)
            silent_started.append(panel._active_worker)
            self.assertFalse(panel._task_shows_loading)

        panel._start_task('正在更新当前 SVN 工作副本……', lambda: [{'ok': True}], (), success_with_modal_dialog)
        update_worker = panel._active_worker
        busy_token = panel._task_token
        self._wait_for_worker(update_worker)
        for _ in range(20):
            self.app.processEvents()
        if silent_started and silent_started[0] is not None:
            self._wait_for_worker(silent_started[0])
        self.app.processEvents()

        self.assertEqual(len(silent_started), 1)
        self.assertIn(('当前副本已更新', busy_token), finished_labels)
        self.assertNotEqual(panel.loading._state, 'busy')
        self.assertTrue(panel.update_all_btn.isEnabled())


if __name__ == '__main__':
    unittest.main()
