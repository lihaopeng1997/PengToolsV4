# -*- coding: utf-8 -*-
"""Bounded end-to-end scan tests using only TEMP folders and a controlled Qt dialog."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QFileDialog

from panels import requirement_panel as requirement_panel_module
from tools import dialog_paths
from tools import requirements as requirement_store


class RequirementScanLiveRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='pengtools-requirement-scan-')
        self.root = Path(self.temp_dir.name)
        self.ledger_path = self.root / 'requirements.json'
        self.panels = []
        self.release_events = []
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []
        self.success_messages = []
        self.saved_paths = []
        self.filter_updates = []
        self._patchers = [
            patch.object(requirement_panel_module, 'load_systems', return_value=[{'name': 'System B'}]),
            patch.object(requirement_panel_module, 'load_requirements', side_effect=self._load_temp_ledger),
            patch.object(requirement_panel_module, 'load_requirement_ui', return_value={}),
            patch.object(requirement_panel_module, 'load_last_choices', return_value={}),
            patch.object(requirement_panel_module, 'save_requirement_ui', return_value=None),
            patch.object(requirement_panel_module, 'update_last_choices', side_effect=self._record_filter_update),
            patch.object(requirement_panel_module, 'save_requirements', side_effect=self._save_temp_ledger),
            patch.object(requirement_panel_module, 'REQUIREMENTS_FILE', str(self.ledger_path)),
            patch.object(requirement_panel_module, 'SYSTEMS_FILE', str(self.root / 'systems.json')),
            patch.object(dialog_paths, 'get_dialog_start_dir', return_value=str(self.root)),
            patch.object(dialog_paths, 'remember_dialog_path', side_effect=self._remember_path),
            patch.object(requirement_panel_module, 'show_info', side_effect=lambda *args, **kwargs: self.info_messages.append(args)),
            patch.object(requirement_panel_module, 'show_warning', side_effect=lambda *args, **kwargs: self.warning_messages.append(args)),
            patch.object(requirement_panel_module, 'show_error', side_effect=lambda *args, **kwargs: self.error_messages.append(args)),
            patch.object(requirement_panel_module, 'show_success', side_effect=lambda *args, **kwargs: self.success_messages.append(args)),
            patch('tools.svn_workspace.run_svn', side_effect=AssertionError('SVN is forbidden in this TEMP-only test')),
        ]
        for item in self._patchers:
            item.start()
        self.addCleanup(self._cleanup)

    def _load_temp_ledger(self):
        return requirement_store.load_requirements(path=str(self.ledger_path))

    def _save_temp_ledger(self, items):
        self.saved_paths.append(str(self.ledger_path))
        requirement_store.save_requirements(items, path=str(self.ledger_path))

    def _remember_path(self, key, path, **kwargs):
        self.saved_paths.append((key, str(path)))

    def _record_filter_update(self, **kwargs):
        self.filter_updates.append(kwargs)

    def _write_folder(self, root, files):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        return root

    def _seed_record(self, record_id, path, *, title, status='待分析', kind='需求',
                     system='', actual_release_date='', actual_online_date='', online_month=''):
        return {
            'id': record_id,
            'code': f'REQ-{record_id.upper()}',
            'title': title,
            'record_kind': kind,
            'status': status,
            'system': system,
            'actual_release_date': actual_release_date,
            'actual_online_date': actual_online_date,
            'online_month': online_month,
            'local_path': str(path),
            'workspace_kind': 'folder',
            'file_count': 1,
            'description': 'Synthetic TEMP row',
        }

    def _make_panel(self, initial=()):
        initial = list(initial)
        if initial:
            requirement_store.save_requirements(initial, path=str(self.ledger_path))
        panel = requirement_panel_module.RequirementPanel()
        panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        panel.resize(960, 640)
        panel._offer_next_steps = lambda *args, **kwargs: None
        panel.show()
        self.app.processEvents()
        self.panels.append(panel)
        return panel

    def _visible_records(self, panel):
        visible = []

        def visit(item):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('id'):
                visible.append(data)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(panel.requirement_list.topLevelItemCount()):
            visit(panel.requirement_list.topLevelItem(index))
        return visible

    def _wait_until(self, predicate, timeout=6.0):
        loop = QEventLoop()
        deadline = time.monotonic() + timeout
        outcome = {'done': False}

        def poll():
            if predicate():
                outcome['done'] = True
                loop.quit()
            elif time.monotonic() >= deadline:
                loop.quit()
            else:
                QTimer.singleShot(20, poll)

        QTimer.singleShot(0, poll)
        loop.exec()
        self.app.processEvents()
        return outcome['done']

    def _wait_for_idle(self, panel):
        return self._wait_until(
            lambda: panel._active_worker is None
            and not requirement_panel_module._ACTIVE_SVN_WORKERS
            and not panel._pending_file_refresh
            and not panel._pending_scan_folder
            and not panel._pending_scan_dispatch_scheduled
        )

    def _run_scan_with_real_qfiledialog(self, panel, selected_folder):
        selected_folder = str(Path(selected_folder).resolve())
        real_get_directory = QFileDialog.getExistingDirectory
        state = {'attempts': 0, 'dialog_found': False}
        returned = []

        def choose_folder():
            dialogs = [
                widget for widget in QApplication.topLevelWidgets()
                if isinstance(widget, QFileDialog)
                and widget.isVisible()
            ]
            if not dialogs:
                state['attempts'] += 1
                if state['attempts'] < 80:
                    QTimer.singleShot(25, choose_folder)
                return
            dialog = dialogs[-1]
            state['dialog_found'] = True
            dialog.setDirectory(selected_folder)
            QApplication.processEvents()
            state['directory_after_set'] = dialog.directory().absolutePath()
            dialog.accept()

        def get_existing_directory(parent=None, caption='', directory='', options=QFileDialog.Option.ShowDirsOnly):
            QTimer.singleShot(50, choose_folder)
            result = real_get_directory(
                parent,
                caption,
                directory,
                options | QFileDialog.Option.DontUseNativeDialog,
            )
            returned.append(result)
            return result

        with patch.object(QFileDialog, 'getExistingDirectory', new=staticmethod(get_existing_directory)):
            panel._action_scan.trigger()
        self.assertTrue(state['dialog_found'], 'the real Qt QFileDialog did not appear before timeout')
        self.assertEqual(len(returned), 1)
        self.assertEqual(os.path.normcase(os.path.abspath(returned[0])), os.path.normcase(selected_folder))
        return returned[0]

    def _set_filters(self, panel, *, search, kind, status, system):
        controls = (panel.search_edit, panel.kind_filter, panel.status_filter, panel.system_filter)
        previous = [control.signalsBlocked() for control in controls]
        for control in controls:
            control.blockSignals(True)
        try:
            panel.search_edit.setText(search)
            panel.kind_filter.setCurrentIndex(panel.kind_filter.findText(kind))
            panel.status_filter.setCurrentIndex(panel.status_filter.findText(status))
            panel.system_filter.setCurrentIndex(panel.system_filter.findData(system))
        finally:
            for control, blocked in zip(controls, previous):
                control.blockSignals(blocked)
        panel._req_search_timer.stop()
        panel._refresh()

    def _cleanup(self):
        for event in self.release_events:
            event.set()
        for panel in self.panels:
            worker = getattr(panel, '_active_worker', None)
            if worker is not None and worker.isRunning():
                worker.wait(3000)
            panel.close()
            panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._wait_until(lambda: not requirement_panel_module._ACTIVE_SVN_WORKERS, timeout=4.0)
        self.app.processEvents()
        for item in reversed(self._patchers):
            item.stop()
        self.temp_dir.cleanup()

    def test_button_scan_imports_into_empty_ledger_and_reveals_through_stale_filters(self):
        selected_root = self._write_folder(
            self.root / 'single-existing-folder',
            {'需求说明.txt': 'Synthetic requirement', '附件/截图.txt': 'Synthetic attachment'},
        )
        panel = self._make_panel()
        self._set_filters(
            panel,
            search='no-such-requirement-7731',
            kind='BUG',
            status='已上线',
            system='System B',
        )
        self.assertEqual(self._visible_records(panel), [])

        selected = self._run_scan_with_real_qfiledialog(panel, selected_root)
        self.assertTrue(self._wait_until(
            lambda: len(self._load_temp_ledger()) == 1
            and panel._active_worker is None
            and not requirement_panel_module._ACTIVE_SVN_WORKERS
            and not panel._pending_file_refresh
            and bool(panel._file_entries_cache)
        ))

        saved = self._load_temp_ledger()
        self.assertEqual(len(saved), 1)
        imported = saved[0]
        self.assertEqual(os.path.normcase(os.path.abspath(imported['local_path'])), os.path.normcase(selected))
        self.assertEqual(imported['status'], '待分析')
        self.assertEqual(imported['actual_release_date'], '')
        self.assertEqual(imported['actual_online_date'], '')
        self.assertEqual(imported['online_month'], '')
        self.assertEqual([item.get('id') for item in self._visible_records(panel)], [imported['id']])
        self.assertEqual(panel._current.get('id'), imported['id'])
        self.assertEqual(panel.search_edit.text(), '')
        self.assertEqual(panel.kind_filter.currentIndex(), 0)
        self.assertEqual(panel.status_filter.currentIndex(), 0)
        self.assertEqual(panel.system_filter.currentIndex(), 0)
        self.assertTrue(any(message[1] == '需求文件夹扫描完成' for message in self.success_messages))
        self.assertFalse(panel._task_failed)
        self.assertFalse(panel._pending_file_refresh)
        self.assertFalse(requirement_panel_module._ACTIVE_SVN_WORKERS)

    def test_scan_update_keeps_existing_status_dates_and_month_then_selects_record(self):
        selected_root = self._write_folder(
            self.root / 'existing-row-folder',
            {'需求说明.txt': 'Synthetic update candidate'},
        )
        record = self._seed_record(
            'preserve-fields', selected_root, title='Hand-authored title', status='开发中',
            actual_release_date='2026-09-18', actual_online_date='2026-09-18', online_month='2026-09',
        )
        panel = self._make_panel([record])
        self.assertTrue(self._wait_until(lambda: panel._active_worker is None))
        self._set_filters(
            panel,
            search='does-not-match-the-import',
            kind='BUG',
            status='已上线',
            system='System B',
        )
        self.assertEqual(self._visible_records(panel), [])

        self._run_scan_with_real_qfiledialog(panel, selected_root)
        self.assertTrue(self._wait_until(
            lambda: panel._active_worker is None
            and not requirement_panel_module._ACTIVE_SVN_WORKERS
            and not panel._pending_file_refresh
            and bool(panel._file_entries_cache)
        ))

        saved = self._load_temp_ledger()
        self.assertEqual(len(saved), 1)
        updated = saved[0]
        self.assertEqual(updated['id'], record['id'])
        self.assertEqual(updated['title'], record['title'])
        self.assertEqual(updated['status'], record['status'])
        self.assertEqual(updated['actual_release_date'], record['actual_release_date'])
        self.assertEqual(updated['actual_online_date'], record['actual_online_date'])
        self.assertEqual(updated['online_month'], record['online_month'])
        self.assertEqual([item.get('id') for item in self._visible_records(panel)], [record['id']])
        self.assertEqual(panel._current.get('id'), record['id'])

    def test_quiet_worker_queues_coalesced_scan_before_pending_refresh_and_drops_stale_callback(self):
        first_root = self._write_folder(self.root / 'first-pending-folder', {'one.txt': 'first'})
        latest_root = self._write_folder(self.root / 'latest-pending-folder', {'two.txt': 'latest'})
        first_existing = self._write_folder(self.root / 'existing-a', {'a.txt': 'A'})
        second_existing = self._write_folder(self.root / 'existing-b', {'b.txt': 'B'})
        seeds = [
            self._seed_record('seed-a', first_existing, title='ZZZ initial selected path'),
            self._seed_record(
                'seed-b', second_existing, title='AAA Existing B', status='开发中', kind='BUG',
                system='System B', actual_release_date='2026-09-18', online_month='2026-09',
            ),
        ]
        requirement_store.save_requirements(seeds, path=str(self.ledger_path))
        entered = threading.Event()
        release = threading.Event()
        self.release_events.append(release)
        real_workspace_files = requirement_panel_module.workspace_files
        real_scan = requirement_panel_module.scan_working_copies
        worker_events = []
        scan_paths = []
        stale_callbacks = []

        def controlled_workspace_files(path, *args, **kwargs):
            normalized = os.path.normcase(os.path.abspath(path))
            worker_events.append(('file-start', normalized))
            if normalized == os.path.normcase(os.path.abspath(first_existing)) and not entered.is_set():
                entered.set()
                if not release.wait(5.0):
                    raise TimeoutError('bounded file-tree worker release timed out')
            result = real_workspace_files(path, *args, **kwargs)
            worker_events.append(('file-end', normalized))
            return result

        def traced_scan(path):
            normalized = os.path.normcase(os.path.abspath(path))
            scan_paths.append(normalized)
            worker_events.append(('scan', normalized))
            return real_scan(path)

        original_file_loaded = requirement_panel_module.RequirementPanel._file_tree_loaded

        def traced_file_loaded(panel, payload):
            stored_path = os.path.normcase(os.path.abspath(panel._file_tree_path)) if panel._file_tree_path else ''
            current_path = os.path.normcase(os.path.abspath(panel._current_path())) if panel._current_path() else ''
            stale_callbacks.append((stored_path, current_path))
            return original_file_loaded(panel, payload)

        with patch.object(requirement_panel_module, 'workspace_files', side_effect=controlled_workspace_files), \
                patch.object(requirement_panel_module, 'scan_working_copies', side_effect=traced_scan), \
                patch.object(requirement_panel_module.RequirementPanel, '_file_tree_loaded', traced_file_loaded):
            panel = self._make_panel(seeds)
            self.assertTrue(self._wait_until(lambda: panel._active_worker is None))
            self.assertTrue(panel.focus_requirement('seed-a'))
            self.assertTrue(entered.wait(2.0), (
                f'initial quiet file-tree worker did not start; events={worker_events!r}; '
                f'current={panel._current!r}; active={panel._active_worker!r}; '
                f'pending_refresh={panel._pending_file_refresh!r}; '
                f'tree_path={getattr(panel, "_file_tree_path", None)!r}'
            ))
            original_worker = panel._active_worker
            self.assertIsNotNone(original_worker)
            self.assertFalse(panel._task_shows_loading)
            self.assertTrue(panel._action_scan.isEnabled())
            stale_callbacks.clear()
            self.assertTrue(panel.focus_requirement('seed-b'))
            self.assertEqual(panel._file_tree_path, str(first_existing))
            self.assertEqual(panel._current_path(), str(second_existing))
            self.assertTrue(panel._pending_file_refresh)

            self._set_filters(
                panel,
                search='Existing B',
                kind='BUG',
                status='开发中',
                system='System B',
            )
            visible_before = {item.get('id') for item in self._visible_records(panel)}
            self.assertEqual(visible_before, {'seed-b'})
            self.assertNotIn(os.path.normcase(os.path.abspath(latest_root)),
                             {os.path.normcase(os.path.abspath(item.get('local_path') or ''))
                              for item in self._visible_records(panel)})

            self._run_scan_with_real_qfiledialog(panel, first_root)
            self.assertEqual(
                os.path.normcase(os.path.abspath(panel._pending_scan_folder)),
                os.path.normcase(os.path.abspath(first_root)),
            )
            self.assertIs(panel._active_worker, original_worker)
            self.assertEqual(scan_paths, [])

            with patch.object(QFileDialog, 'getExistingDirectory',
                              new=staticmethod(lambda *args, **kwargs: str(latest_root))):
                panel._action_scan.trigger()
            self.assertEqual(
                os.path.normcase(os.path.abspath(panel._pending_scan_folder)),
                os.path.normcase(os.path.abspath(latest_root)),
            )
            self.assertTrue(any('替换等待扫描的目录' in str(message) for message in self.info_messages))
            panel._refresh_file_tree()
            self.assertTrue(panel._pending_file_refresh)

            release.set()
            self.assertTrue(self._wait_until(
                lambda: any(os.path.normcase(os.path.abspath(item.get('local_path') or ''))
                            == os.path.normcase(os.path.abspath(latest_root))
                            for item in self._load_temp_ledger())
                and panel._active_worker is None
                and not requirement_panel_module._ACTIVE_SVN_WORKERS
                and not panel._pending_file_refresh
                and not panel._pending_scan_folder
                and bool(panel._file_entries_cache)
            ))

        latest_key = os.path.normcase(os.path.abspath(latest_root))
        self.assertEqual(scan_paths, [latest_key])
        self.assertTrue(stale_callbacks)
        self.assertEqual(stale_callbacks[0], (
            os.path.normcase(os.path.abspath(first_existing)),
            os.path.normcase(os.path.abspath(second_existing)),
        ))
        scan_index = next(index for index, event in enumerate(worker_events) if event[0] == 'scan')
        file_start_indexes = [index for index, event in enumerate(worker_events) if event[0] == 'file-start']
        self.assertTrue(file_start_indexes[0] < scan_index < file_start_indexes[-1])
        self.assertEqual(worker_events[file_start_indexes[-1]][1], latest_key)
        self.assertEqual(panel._current_path() and os.path.normcase(os.path.abspath(panel._current_path())), latest_key)
        self.assertEqual(panel._current.get('local_path'), str(latest_root))
        self.assertEqual(panel.search_edit.text(), '')
        self.assertEqual(panel.kind_filter.currentIndex(), 0)
        self.assertEqual(panel.status_filter.currentIndex(), 0)
        self.assertEqual(panel.system_filter.currentIndex(), 0)
        saved = self._load_temp_ledger()
        self.assertEqual(len(saved), 3)
        preserved = next(item for item in saved if item.get('id') == 'seed-b')
        self.assertEqual(preserved['status'], '开发中')
        self.assertEqual(preserved['actual_release_date'], '2026-09-18')
        self.assertEqual(preserved['online_month'], '2026-09')
        imported = next(item for item in saved if os.path.normcase(os.path.abspath(item.get('local_path') or '')) == latest_key)
        self.assertIn(imported['id'], {item.get('id') for item in self._visible_records(panel)})
        self.assertFalse(requirement_panel_module._ACTIVE_SVN_WORKERS)

    def test_cancelled_chooser_does_not_queue_or_save(self):
        selected_root = self._write_folder(self.root / 'cancelled-folder', {'one.txt': 'one'})
        panel = self._make_panel()
        with patch.object(QFileDialog, 'getExistingDirectory', new=staticmethod(lambda *args, **kwargs: '')):
            panel._action_scan.trigger()
        self.assertIsNone(panel._pending_scan_folder)
        self.assertEqual(self._load_temp_ledger(), [])
        self.assertEqual(self.success_messages, [])
        self.assertFalse(panel._active_worker)
        self.assertNotIn(('requirement_month_root', str(selected_root)), self.saved_paths)

    def test_partial_svn_scan_preserves_local_path_and_reports_warning_not_success(self):
        selected_root = self._write_folder(self.root / 'svn-info-failed', {'one.txt': 'one'})
        panel = self._make_panel()
        result = panel._scan_finished([{
            'local_path': str(selected_root),
            'relative_path': 'svn-info-failed',
            'svn_url': '',
            'svn_revision': '',
            'svn_status': 'synthetic SVN info failure',
            'error': 'synthetic SVN info failure',
            'workspace_kind': 'svn',
            'file_count': 1,
        }])

        self.assertTrue(result)
        self.assertTrue(self._wait_until(lambda: panel._active_worker is None
                                         and not requirement_panel_module._ACTIVE_SVN_WORKERS))
        saved = self._load_temp_ledger()
        self.assertEqual(len(saved), 1)
        self.assertEqual(os.path.normcase(os.path.abspath(saved[0]['local_path'])),
                         os.path.normcase(os.path.abspath(selected_root)))
        self.assertEqual(saved[0]['svn_status'], 'synthetic SVN info failure')
        self.assertEqual(self.success_messages, [])
        self.assertTrue(self.warning_messages)
        self.assertIn('1 条 SVN 信息读取失败', self.warning_messages[-1][2])

    def test_long_task_guard_and_close_cancel_pending_scan(self):
        selected_root = self._write_folder(self.root / 'must-not-run', {'one.txt': 'one'})
        panel = self._make_panel()
        long_started = threading.Event()
        long_release = threading.Event()
        self.release_events.append(long_release)

        def long_work():
            long_started.set()
            if not long_release.wait(5.0):
                raise TimeoutError('bounded long worker release timed out')
            return 'finished'

        panel._start_task('模拟长操作', long_work, (), lambda _result: None, show_loading=True)
        self.assertTrue(long_started.wait(2.0))
        with patch.object(QFileDialog, 'getExistingDirectory', new=staticmethod(lambda *args, **kwargs: str(selected_root))):
            panel._scan_folder()
        self.assertIsNone(panel._pending_scan_folder)
        self.assertTrue(any('未加入扫描队列' in str(message) for message in self.info_messages))
        long_release.set()
        self.assertTrue(self._wait_for_idle(panel))

        existing = self._write_folder(self.root / 'existing-for-close', {'old.txt': 'old'})
        close_scan_root = self._write_folder(self.root / 'close-queued-folder', {'new.txt': 'new'})
        seed = self._seed_record('close-seed', existing, title='Close while refreshing')
        requirement_store.save_requirements([seed], path=str(self.ledger_path))
        entered = threading.Event()
        release = threading.Event()
        self.release_events.append(release)
        real_workspace_files = requirement_panel_module.workspace_files

        def blocked_workspace_files(path, *args, **kwargs):
            entered.set()
            if not release.wait(5.0):
                raise TimeoutError('bounded close-test worker release timed out')
            return real_workspace_files(path, *args, **kwargs)

        with patch.object(requirement_panel_module, 'workspace_files', side_effect=blocked_workspace_files), \
                patch.object(QFileDialog, 'getExistingDirectory', new=staticmethod(lambda *args, **kwargs: str(close_scan_root))):
            closing_panel = self._make_panel([seed])
            self.assertTrue(entered.wait(2.0))
            closing_panel._action_scan.trigger()
            self.assertEqual(closing_panel._pending_scan_folder, str(close_scan_root))
            closing_panel.close()
            self.assertIsNone(closing_panel._pending_scan_folder)
            release.set()
            self.assertTrue(self._wait_until(
                lambda: closing_panel._active_worker is None
                and not requirement_panel_module._ACTIVE_SVN_WORKERS
            ))
        saved = self._load_temp_ledger()
        self.assertEqual([item.get('id') for item in saved], ['close-seed'])
        self.assertFalse(requirement_panel_module._ACTIVE_SVN_WORKERS)


if __name__ == '__main__':
    unittest.main()
