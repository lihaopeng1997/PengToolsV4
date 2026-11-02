import gzip
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tools.agent_core.contracts import CancellationToken, RunContext, ToolCall
from tools.log_analysis.history import HistoryScan
from tools.log_analysis.system import LinuxSampler, SystemMonitor, AlertRules, run_readonly
from tools.log_analysis.service import LogService
from tools.log_analysis.runtime import LogTools
from tools.log_analysis.report import Store, report


def setUpModule():
    global diagnostic_temp, diagnostic_patch
    diagnostic_temp = tempfile.TemporaryDirectory()
    diagnostic_patch = patch('tools.agent_diag._LOG_DIR', diagnostic_temp.name)
    diagnostic_patch.start()


def tearDownModule():
    diagnostic_patch.stop()
    diagnostic_temp.cleanup()


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def scan(self, names, **kwargs):
        task = HistoryScan({}, 'fixture', [str(self.root / name) for name in names], local=True, **kwargs)
        self.addCleanup(task.dispose)
        task.start()
        task.thread.join(10)
        self.assertFalse(task.thread.is_alive())
        return task

    def test_full_history_finds_start_error_beyond_old_tail_limit_and_index(self):
        (self.root / 'app.log').write_text('ERROR E901 start\nINFO secret=abc\n' + ('INFO regular\n' * 25000), encoding='utf-8')
        task = self.scan(['app.log'])
        snap = task.snapshot()
        self.assertTrue(snap['complete'], snap['notices'])
        self.assertEqual(len(snap['events']), 1)
        self.assertIn('E901', snap['events'][0]['title'])
        found = task.search('E901', CancellationToken())
        self.assertTrue(found['rows'])
        self.assertNotIn('secret=abc', json.dumps(found))
        self.assertIn('app.log', report(snap))

    def test_gzip_multiple_files_final_line_and_tail(self):
        with gzip.open(self.root / 'old.log.gz', 'wb') as file:
            file.write('ERROR 旧日志\n  at Example.run\n'.encode())
        (self.root / 'new.log').write_text('ERROR 新日志没有末尾换行', encoding='utf-8')
        task = self.scan(['old.log.gz', 'new.log'])
        self.assertTrue(task.complete)
        self.assertEqual(len(task.snapshot()['events']), 2)
        self.assertNotEqual(task.snapshot()['events'][0]['source'], task.snapshot()['events'][1]['source'])
        self.assertNotIn('新日志', task.snapshot()['events'][0]['evidence'])
        (self.root / 'tail.log').write_text('ERROR old\nINFO x\nERROR new\n', encoding='utf-8')
        tail = self.scan(['tail.log'], tail_lines=1)
        self.assertEqual(len(tail.snapshot()['events']), 1)
        self.assertIn('new', tail.snapshot()['events'][0]['title'])

    def test_frozen_boundary_ignores_append_and_rejects_changed_source(self):
        path = self.root / 'grow.log'
        path.write_text('ERROR original\nINFO end\n', encoding='utf-8')
        original = HistoryScan._accept
        appended = []
        def accept(task, *args):
            original(task, *args)
            if not appended:
                appended.append(True)
                with path.open('a', encoding='utf-8') as stream:
                    stream.write('ERROR appended after start\n')
        with patch.object(HistoryScan, '_accept', accept):
            task = self.scan(['grow.log'])
        self.assertTrue(task.complete)
        self.assertFalse(task.search('appended', CancellationToken())['rows'])

    def test_capacity_stops_without_claiming_complete_or_silently_evicting(self):
        (self.root / 'large.log').write_text(''.join(f'ERROR unique-{i}\n' for i in range(2200)), encoding='utf-8')
        task = self.scan(['large.log'])
        self.assertFalse(task.complete)
        self.assertEqual(task.buffer.dropped, 0)
        self.assertTrue(task.notices)
        self.assertGreater(len(task.snapshot()['events']), 1000)

    def test_byte_limit_for_compressed_input_and_directory_rejection(self):
        with gzip.open(self.root / 'large.gz', 'wb') as file:
            file.write(b'INFO line\n' * 10000)
        task = self.scan(['large.gz'], max_bytes=1024)
        self.assertFalse(task.complete)
        self.assertIn('达到读取额度', task.notices[-1])
        self.assertIn('达到读取额度', report(task.snapshot()))
        bad = self.scan(['.'])
        self.assertFalse(bad.complete)
        self.assertIn('目录', bad.notices[-1])

    def test_time_filter_keeps_stack_and_reports_unknown_lines(self):
        (self.root / 'dated.log').write_text(
            'ERROR unrecognized time\n2026-10-29 08:00:00 INFO before\n'
            '2026-10-29 09:01:00 ERROR selected\n    at Example.run\n'
            '2026-10-29 10:00:00 ERROR outside\n', encoding='utf-8')
        task = self.scan(['dated.log'], time_start='2026-10-29 09:00:00', time_end='2026-10-29 09:30:00')
        snap = task.snapshot()
        self.assertTrue(task.complete)
        self.assertEqual(len(snap['events']), 1)
        self.assertIn('Example.run', snap['events'][0]['evidence'])
        self.assertNotIn('outside', snap['events'][0]['evidence'])
        self.assertEqual(snap['time_filter']['unknown_lines'], 1)
        self.assertIn('不能保证时段完整', report(snap))
        self.assertFalse(task.search('outside', CancellationToken())['rows'])

    def test_remote_finite_scan_and_source_replacement_detection(self):
        import io
        import stat
        closed = []
        class Sftp:
            def stat(self, path):
                return SimpleNamespace(st_size=13, st_mode=stat.S_IFREG, st_mtime=1)
            def open(self, path, mode):
                self_test.assertEqual(mode, 'rb')
                return io.BytesIO(b'ERROR remote\n')
            def get_channel(self):
                return SimpleNamespace(settimeout=lambda *a: None)
            def close(self):
                closed.append('sftp')
        self_test = self
        client = SimpleNamespace(open_sftp=Sftp, close=lambda: closed.append('ssh'))
        task = HistoryScan({'id': 'fixture'}, 'app', ['/app.log'], connector=lambda *a, **k: client)
        self.addCleanup(task.dispose)
        task.start(); task.thread.join(2)
        self.assertTrue(task.complete, task.notices)
        self.assertEqual(task.snapshot()['events'][0]['source'], '/app.log')
        self.assertEqual(set(closed), {'sftp', 'ssh'})
        path = self.root / 'changed.log'
        path.write_text('ERROR original\n', encoding='utf-8')
        original = HistoryScan._accept
        def replace(task, *args):
            original(task, *args)
            path.write_text('ERROR replaced\n', encoding='utf-8')
        with patch.object(HistoryScan, '_accept', replace):
            changed = self.scan(['changed.log'])
        self.assertFalse(changed.complete)
        self.assertIn('发生变化', changed.notices[-1])

    def test_cancel_retains_partial_evidence_and_does_not_claim_complete(self):
        (self.root / 'cancel.log').write_text('ERROR first\nINFO second\nERROR last\n', encoding='utf-8')
        original = HistoryScan._accept
        def cancel(task, *args):
            original(task, *args)
            task.stop()
        with patch.object(HistoryScan, '_accept', cancel):
            task = self.scan(['cancel.log'])
        self.assertFalse(task.complete)
        self.assertIn('取消', task.state)
        self.assertIn('first', task.snapshot()['events'][0]['evidence'])
        self.assertTrue(task.search('first', CancellationToken())['rows'])


class LinuxTests(unittest.TestCase):
    def test_cached_disk_reading_does_not_count_as_two_samples(self):
        rules = AlertRules(duration=0)
        sample = dict(cpu_percent=None, memory_available_percent=None, units={}, processes={}, ports={},
                      disks=[dict(path='/', used_percent=95, inode_percent=20, observed_at=1)])
        self.assertEqual(rules.evaluate(sample, 0), [])
        self.assertEqual(rules.evaluate(sample, 5), [])
        sample['disks'][0]['observed_at'] = 2
        self.assertEqual(rules.evaluate(sample, 30), [('disk:/:used_percent', '异常持续', 95)])

    def test_same_host_system_monitor_rejected_even_with_different_log(self):
        service = LogService()
        task = SystemMonitor({'id': 'fixture'}, path='/first.log')
        task.thread = SimpleNamespace(is_alive=lambda: True)
        service.tasks[task.id] = task
        try:
            with self.assertRaisesRegex(ValueError, '已有系统监控'):
                service.add({'id': 'fixture'}, 'app', '/second.log', mode='system')
        finally:
            task.thread = None
            service.close(); self.assertTrue(service.join(2))
    def fixture_executor(self):
        counts = iter(('cpu 100 0 100 800 0 0 0 0 0 0\ncpu0 1\n', 'cpu 150 0 150 900 0 0 0 0 0 0\ncpu0 1\n'))
        commands = []
        def execute(client, command, cancellation):
            commands.append(command)
            if command == 'cat /proc/stat':
                return next(counts), 0
            if command == 'cat /proc/meminfo':
                return 'MemTotal: 1000 kB\nMemAvailable: 200 kB\nSwapTotal: 100 kB\nSwapFree: 90 kB\n', 0
            if command == 'cat /proc/loadavg':
                return '0.10 0.20 0.30 1/20 1', 0
            if command.startswith('df '):
                return 'Filesystem Blocks Used Available Capacity Mounted on\n/dev/a 100 40 60 40% /\n', 0
            if command.startswith('ps '):
                return '100 java 20.5 1024\n101 nginx 0.1 200\n', 0
            if command.startswith('systemctl '):
                return 'LoadState=loaded\nActiveState=active\nMainPID=100\n', 0
            if command.startswith('ss '):
                return 'tcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*\n', 0
            raise AssertionError(command)
        return execute, commands

    def test_samples_delta_resources_services_ports_and_no_write_commands(self):
        execute, commands = self.fixture_executor()
        sampler = LinuxSampler(['app.service'], ['java'], [8080, 9090], executor=execute)
        first = sampler.sample(None, threading.Event())
        second = sampler.sample(None, threading.Event())
        self.assertIsNone(first['cpu_percent'])
        self.assertEqual(second['cpu_percent'], 50)
        self.assertEqual(second['memory_available_percent'], 20)
        self.assertEqual(second['disks'][0]['inode_percent'], 40)
        self.assertEqual(second['processes']['java'][0]['pid'], 100)
        self.assertEqual(second['ports'], {'8080': True, '9090': False})
        self.assertEqual(second['errors'], {})
        self.assertTrue(all('restart' not in c and 'sudo' not in c and 'environ' not in c for c in commands))

    def test_invalid_scope_and_unavailable_metrics(self):
        with self.assertRaises(ValueError):
            LinuxSampler(['app; touch /tmp/test'])
        with self.assertRaises(ValueError):
            LinuxSampler(ports=[65536])
        sampler = LinuxSampler(['app.service'], ['java'], [80], executor=lambda *a: ('', 127))
        sample = sampler.sample(None, threading.Event())
        self.assertIsNone(sample['cpu_percent'])
        self.assertEqual(sample['ports'], {'80': None})
        self.assertEqual(sample['units'], {'app.service': None})
        self.assertEqual(sample['processes'], {'java': None})
        self.assertTrue(sample['errors'])

    def test_malformed_responses_are_unknown_and_never_down_alerts(self):
        sampler = LinuxSampler(['app.service'], ['java'], [80], executor=lambda *a: ('unrecognized', 0))
        rules = AlertRules(duration=0)
        for i in range(3):
            sample = sampler.sample(None, threading.Event())
            self.assertIsNone(sample['units']['app.service'])
            self.assertIsNone(sample['processes']['java'])
            self.assertIsNone(sample['ports']['80'])
            self.assertEqual(rules.evaluate(sample, i), [])
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        text = LogAnalysisWorkspace._format_metrics(sample, {})
        self.assertIn('app.service：未知', text)
        self.assertIn('java：未知', text)
        self.assertIn('80：未知', text)
        self.assertNotIn('未监听', text)

    def test_empty_required_outputs_are_unknown_but_empty_listener_list_is_valid(self):
        sampler = LinuxSampler(['app.service'], ['java'], [80], executor=lambda *a: ('', 0))
        sample = sampler.sample(None, threading.Event())
        self.assertEqual(sample['ports'], {'80': False})
        self.assertIsNone(sample['units']['app.service'])
        self.assertIsNone(sample['processes']['java'])
        self.assertIn('memory', sample['errors'])
        self.assertIn('cpu', sample['errors'])
        self.assertIn('load', sample['errors'])

    def test_disconnect_marks_unknown_and_reconnects_owned_connection(self):
        execute, _ = self.fixture_executor()
        clients = [SimpleNamespace(get_transport=lambda: SimpleNamespace(is_active=lambda: False), close=unittest.mock.Mock()),
                   SimpleNamespace(get_transport=lambda: SimpleNamespace(is_active=lambda: True), close=unittest.mock.Mock())]
        connect = unittest.mock.Mock(side_effect=clients)
        task = SystemMonitor({'id': 's'}, units=['app.service'], processes=['java'], ports=[80], executor=execute, connector=connect)
        waits = []
        def wait(_seconds):
            waits.append(True)
            if len(waits) == 2:
                task.stop_event.set()
        with patch.object(task.stop_event, 'wait', side_effect=wait):
            task._run()
        self.assertEqual(connect.call_count, 2)
        self.assertIsNone(task.metrics[0]['units']['app.service'])
        self.assertIn('capture', task.metrics[0]['errors'])
        self.assertEqual(task.metrics[1]['units']['app.service']['ActiveState'], 'active')
        self.assertEqual(task.snapshot()['events'], [])
        for client in clients:
            client.close.assert_called()

    def test_bounded_executor_closes_channel_for_output_limit_and_cancel(self):
        class Channel:
            def __init__(self):
                self.closed = False
            def settimeout(self, value):
                pass
            def exec_command(self, command):
                self.command = command
            def recv_ready(self):
                return True
            def recv(self, size):
                return b'x' * size
            def recv_stderr_ready(self):
                return False
            def recv_stderr(self, size):
                return b''
            def close(self):
                self.closed = True
        for cancel in (False, True):
            channel = Channel()
            client = SimpleNamespace(get_transport=lambda: SimpleNamespace(is_active=lambda: True, open_session=lambda **k: channel))
            cancellation = threading.Event()
            if cancel:
                cancellation.set()
            with self.assertRaises(InterruptedError if cancel else ValueError):
                run_readonly(client, 'cat /proc/stat', cancellation, limit=1024)
            self.assertTrue(channel.closed)

    def test_bounded_executor_deadline_closes_channel(self):
        channel = SimpleNamespace(settimeout=lambda *a: None, exec_command=lambda *a: None, close=unittest.mock.Mock())
        client = SimpleNamespace(get_transport=lambda: SimpleNamespace(is_active=lambda: True, open_session=lambda **k: channel))
        with patch('tools.log_analysis.system.time.monotonic', side_effect=[0, 9]):
            with self.assertRaises(TimeoutError):
                run_readonly(client, 'cat /proc/stat', threading.Event(), timeout=8)
        channel.close.assert_called_once()

    def test_system_and_log_threads_both_stop_without_losing_evidence(self):
        from tests.test_log_analysis import Sftp
        execute, _ = self.fixture_executor()
        clients = []
        def connect(*a, **k):
            ftp = Sftp(b'ERROR companion log\n')
            client = SimpleNamespace(open_sftp=lambda: ftp, close=unittest.mock.Mock())
            clients.append((client, ftp))
            return client
        task = SystemMonitor({'id': 's'}, path='/app.log', backfill=100, executor=execute, connector=connect)
        service = LogService()
        self.addCleanup(service.close)
        service.tasks[task.id] = task
        task.start()
        deadline = time.time() + 2
        while (not task.metrics or not task.log_task.bytes_read) and time.time() < deadline:
            time.sleep(.01)
        service.close()
        self.assertTrue(service.join(2))
        self.assertFalse(task.log_task.thread.is_alive())
        self.assertFalse(task.thread.is_alive())
        self.assertIn('companion log', report(task.snapshot()))
        self.assertEqual(len(clients), 2)
        for client, ftp in clients:
            client.close.assert_called()

    def test_event_pages_are_bounded_and_continuable(self):
        from tests.test_log_analysis import snapshot
        snap = snapshot()
        snap['events'] = [dict(snap['events'][0], id=str(i)) for i in range(250)]
        registry = LogTools(snap)
        context, token = RunContext('r', snap['id'], 'm'), CancellationToken()
        first = registry.execute(ToolCall('1', 'list_events', {}), context=context, cancellation=token)
        self.assertEqual(len(first.data['events']), 100)
        self.assertEqual(first.data['total'], 250)
        self.assertFalse(first.truncated)
        last = registry.execute(ToolCall('2', 'list_events', {'offset': 200}), context=context, cancellation=token)
        self.assertEqual(len(last.data['events']), 50)
        self.assertIsNone(last.data['next_offset'])

    def test_rules_sustain_recover_and_missing_samples_reset(self):
        def sample(cpu, port=True):
            return dict(cpu_percent=cpu, memory_available_percent=50, disks=[], units={}, processes={}, ports={'80': port})
        rules = AlertRules(duration=10)
        self.assertEqual(rules.evaluate(sample(90), 0), [])
        self.assertEqual(rules.evaluate(sample(None), 9), [])
        self.assertEqual(rules.evaluate(sample(90), 10), [])
        self.assertEqual(rules.evaluate(sample(90), 20), [('cpu', '异常持续', 90)])
        self.assertEqual(rules.evaluate(sample(30), 21), [])
        self.assertEqual(rules.evaluate(sample(30), 31), [('cpu', '已恢复', 30)])
        self.assertEqual(rules.evaluate(sample(30, False), 32), [])
        self.assertEqual(rules.evaluate(sample(30, False), 33), [('port:80', '异常持续', 1)])

    def test_model_tools_cannot_execute_shell_or_probe_other_services(self):
        snap = {'id': 'fixture', 'path': '', 'mode': 'system', 'metrics': [{'cpu_percent': 50}],
                'events': [], 'targets': {'units': ['app.service']}}
        probe = unittest.mock.Mock(return_value={'text': 'fixture'})
        tools = LogTools(snap, probe=probe)
        context = RunContext('r', 'fixture', 'model', domain='log-analysis')
        token = CancellationToken()
        def call(name, args):
            return tools.execute(ToolCall('c', name, args), context=context, cancellation=token)
        self.assertFalse(call('shell', {'command': 'restart'}).ok)
        self.assertFalse(call('read_service_journal', {'unit': 'other.service'}).ok)
        probe.assert_not_called()
        self.assertTrue(call('get_host_snapshot', {}).ok)
        self.assertTrue(call('read_service_journal', {'unit': 'app.service'}).ok)

    def test_stopped_task_can_analyze_and_cached_evidence_never_connects(self):
        execute, _ = self.fixture_executor()
        closed = []
        client = SimpleNamespace(close=lambda: closed.append(True))
        task = SystemMonitor({'id': 's'}, units=['app.service'], executor=execute, connector=lambda *a, **kw: client)
        task.start()
        deadline = time.time() + 2
        while not task.metrics and time.time() < deadline:
            time.sleep(.01)
        task.stop(); task.thread.join(2)
        self.assertTrue(closed)
        entered = threading.Event()
        def analyze(*args, **kwargs):
            entered.set()
            return SimpleNamespace(final_text='已分析停止前证据', error=None)
        service = LogService(analyzer=analyze)
        self.addCleanup(service.close)
        service.tasks[task.id] = task
        self.assertTrue(service.enqueue(task.id, 'why', 'm'))
        self.assertTrue(entered.wait(2))
        snap = task.snapshot()
        snap['id'] = 'cachefixture'
        with patch('tools.ops_ssh.open_ssh_client') as connect:
            cached = service.open_snapshot(snap)
            self.assertTrue(cached.snapshot()['cached'])
            self.assertTrue(service.enqueue(cached.id, 'why', 'm'))
            connect.assert_not_called()
        service.close()
        self.assertTrue(service.join(2))


class NewWorkspaceUiTests(unittest.TestCase):
    def test_connecting_feedback_does_not_pretend_model_is_running(self):
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        with tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            widget = LogAnalysisWorkspace(lambda: [], store=Store(root))
            widget.service = LogService()
            stop = threading.Event()
            task = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True), state='连接中',
                                   stop_event=stop, snapshot=lambda **k: None, stop=stop.set)
            widget.service.tasks['connecting'] = task
            widget.tasks.addItem('connecting', 'connecting'); widget.tasks.setCurrentIndex(1)
            try:
                widget.refresh()
                self.assertTrue(widget.capture_waiting.is_running())
                self.assertFalse(widget.ai_waiting.is_running())
                self.assertIn('等待首次采集', widget.scope.text())
                stop.set(); widget.refresh()
                self.assertFalse(widget.capture_waiting.is_running())
            finally:
                widget.service.tasks.clear()
                widget.shutdown(); widget.close(); widget.deleteLater()
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_mode_fields_and_history_ui_start(self):
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        with tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            path = Path(root) / 'app.log'
            path.write_text('ERROR local history\n', encoding='utf-8')
            widget = LogAnalysisWorkspace(lambda: [], store=Store(Path(root) / 'cache'))
            try:
                widget.resize(1500, 900)
                widget.show()
                self.app.processEvents()
                self.assertTrue(widget.files.isVisible())
                self.assertFalse(widget.servers.isVisible())
                widget.files.setPlainText(str(path))
                widget.start()
                task = widget.current_task()
                self.assertIsNotNone(task)
                task.thread.join(2)
                widget.refresh()
                self.assertEqual(widget.events.rowCount(), 1)
                self.assertIn('历史日志分析', widget.scope.text())
                widget.mode.setCurrentIndex(1)
                self.app.processEvents()
                self.assertFalse(widget.files.isVisible())
                widget.system_advanced.setChecked(True)
                self.assertTrue(widget.system_options.isVisible())
                self.assertTrue(widget.servers.isVisible())
                self.assertFalse(widget.path.isVisible())
                widget.with_logs.setChecked(True)
                self.assertTrue(widget.path.isVisible())
            finally:
                widget.shutdown()
                widget.close()
                widget.deleteLater()

    def test_analysis_animation_tracks_selected_task_and_cancel_completion(self):
        from tests.test_log_analysis import snapshot
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        entered, release = threading.Event(), threading.Event()
        def analyze(*args, **kwargs):
            kwargs['progress']('tool_started', 'read_service_journal')
            entered.set()
            release.wait(3)
            return SimpleNamespace(final_text='late answer', error=None)
        with tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            widget = LogAnalysisWorkspace(lambda: [], store=Store(root))
            widget.service = LogService(analyzer=analyze)
            first = widget.service.open_snapshot(snapshot())
            second = widget.service.open_snapshot(dict(snapshot(), id='another'))
            for task in (first, second):
                widget.tasks.addItem(task.id, task.id)
            widget.tasks.setCurrentIndex(1)
            widget.models.addItem('test', 'm'); widget.models.setCurrentIndex(1)
            widget.model_consent.setChecked(True)
            widget.resize(1100, 740); widget.show()
            try:
                widget.input.setPlainText('inspect')
                widget.send()
                self.assertTrue(widget.ai_waiting.is_running())
                self.assertTrue(entered.wait(2))
                widget.refresh()
                self.assertIn('正在补查', widget.ai_activity.text())
                widget.tasks.setCurrentIndex(2)
                self.assertFalse(widget.ai_waiting.is_running())
                self.assertNotIn('正在补查', widget.ai_activity.text())
                widget.tasks.setCurrentIndex(1)
                self.assertTrue(widget.ai_waiting.is_running())
                widget.cancel_analysis()
                self.assertIn('正在取消', widget.ai_activity.text())
                release.set()
                deadline = time.monotonic() + 2
                while widget.service.analysis_activity(first.id)['busy'] and time.monotonic() < deadline:
                    time.sleep(.01)
                widget.refresh()
                self.assertFalse(widget.ai_waiting.is_running())
                self.assertIn('已取消', widget.ai_activity.text())
                self.assertNotIn('late answer', widget.chat.toPlainText())
            finally:
                release.set(); widget.shutdown(); widget.close(); widget.deleteLater()

    def test_analysis_failure_and_completion_end_waiting(self):
        from tests.test_log_analysis import snapshot
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
                def analyze(*a, **k):
                    if failure:
                        raise TimeoutError()
                    return SimpleNamespace(final_text='done', error=None)
                widget = LogAnalysisWorkspace(lambda: [], store=Store(root))
                widget.service = LogService(analyzer=analyze)
                task = widget.service.open_snapshot(snapshot())
                widget.tasks.addItem('test', task.id); widget.tasks.setCurrentIndex(1)
                try:
                    widget.service.enqueue(task.id, 'inspect', 'm')
                    deadline = time.monotonic() + 2
                    while widget.service.analysis_activity(task.id)['busy'] and time.monotonic() < deadline:
                        time.sleep(.01)
                    widget.refresh()
                    self.assertFalse(widget.ai_waiting.is_running())
                    self.assertIn('失败' if failure else '完成', widget.ai_activity.text())
                finally:
                    widget.shutdown(); widget.close(); widget.deleteLater()

    def test_stopped_task_report_result_preserves_user_edits_and_task_selection(self):
        from tests.test_log_analysis import snapshot
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        entered, release = threading.Event(), threading.Event()
        def analyze(*args, **kwargs):
            entered.set()
            release.wait(2)
            return SimpleNamespace(final_text='AI generated report', error=None)
        with tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            widget = LogAnalysisWorkspace(lambda: [], store=Store(root))
            widget.service = LogService(analyzer=analyze)
            task = widget.service.open_snapshot(snapshot())
            task.stop()
            widget.tasks.addItem('stopped', task.id)
            widget.tasks.setCurrentIndex(1)
            widget.models.addItem('fixture', 'fixture-model')
            widget.models.setCurrentIndex(1)
            widget.model_consent.setChecked(True)
            try:
                widget.make_ai_report()
                self.assertTrue(entered.wait(2))
                widget.draft.setPlainText('User edit during generation')
                release.set()
                deadline = time.time() + 2
                while not widget.service.results and time.time() < deadline:
                    time.sleep(.01)
                widget.refresh()
                self.assertEqual(widget.selected_id, task.id)
                self.assertEqual(widget.draft.toPlainText(), 'User edit during generation')
                self.assertIn('AI generated report', widget.chat.toPlainText())
                widget.draft.clear()
                widget.make_report()
                self.assertIn('E104', widget.draft.toPlainText())
            finally:
                release.set()
                widget.shutdown()
                widget.close()
                widget.deleteLater()

    def test_remote_picker_async_result_selects_files_and_closes_connection(self):
        import stat
        from ui.log_analysis.file_picker import RemoteFilePicker
        ftp = SimpleNamespace(stat=lambda path: SimpleNamespace(st_mode=stat.S_IFDIR),
              get_channel=lambda: SimpleNamespace(settimeout=lambda value: None),
              listdir_iter=lambda path: iter([SimpleNamespace(filename='app.log', st_size=123, st_mode=stat.S_IFREG)]))
        client = SimpleNamespace(open_sftp=lambda: ftp, close=unittest.mock.Mock())
        with patch('tools.ops_ssh.open_ssh_client', return_value=client):
            picker = RemoteFilePicker({'id': 'fixture'}, '/logs')
            try:
                deadline = time.time() + 2
                while not picker.table.rowCount() and time.time() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertEqual(picker.table.rowCount(), 1)
                picker.table.selectRow(0)
                picker.choose()
                self.assertEqual(picker.selected_paths, ['/logs/app.log'])
                self.assertTrue(picker.cancel.is_set())
                client.close.assert_called()
            finally:
                picker.reject()
                picker.deleteLater()

    def test_modes_fit_and_metric_updates_keep_reading_position(self):
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        with tempfile.TemporaryDirectory() as root, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            widget = LogAnalysisWorkspace(lambda: [], store=Store(root))
            try:
                self.assertEqual(widget.mode.tabText(0), '历史日志分析')
                self.assertEqual(widget.mode.tabText(1), 'Linux 实时监控')
                for width in (900, 1100, 1500):
                    widget.resize(width, 740)
                    widget.show()
                    self.app.processEvents()
                    self.assertEqual(widget.width(), width)
                    for key in ('开始监控 / AI取证', '停止任务'):
                        button = widget.control_buttons[key]
                        self.assertTrue(button.isVisible())
                        self.assertLessEqual(button.geometry().right(), width)
                widget.result_tabs.setCurrentIndex(1)
                widget._set_preview(widget.metric_view, '\n'.join(f'line {i}' for i in range(200)))
                self.app.processEvents()
                bar = widget.metric_view.verticalScrollBar()
                self.assertEqual(bar.value(), 0)
                bar.setValue(20)
                widget._set_preview(widget.metric_view, widget.metric_view.toPlainText() + '\nupdated')
                self.assertEqual(bar.value(), 20)
            finally:
                widget.shutdown()
                widget.close()
                widget.deleteLater()


if __name__ == '__main__':
    unittest.main()
