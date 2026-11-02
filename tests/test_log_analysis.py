"""Synthetic logs only: bounded reads, evidence, scope and UI lifecycle."""
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tools.log_analysis.collector import Cursor, Monitor
from tools.log_analysis.events import EventBuffer, Redactor
from tools.log_analysis.report import Store, report
from tools.log_analysis.runtime import LogTools, analyze
from tools.log_analysis.service import LogService
from tools.agent_core.contracts import AgentTurn, CancellationToken, RunContext, ToolCall


class Sftp:
    def __init__(self, data=b''):
        self.data = data
        self.closed = False

    def stat(self, _path):
        return SimpleNamespace(st_size=len(self.data), st_mode=stat.S_IFREG)

    def open(self, _path, mode):
        assert mode == 'rb'
        return io.BytesIO(self.data)

    def get_channel(self):
        return self

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


def snapshot():
    buffer = EventBuffer()
    buffer.line('2026-10-28 12:00:00 ERROR E104 traceId=demo timeout', 0, 0)
    buffer.line('    at app.Service.run(Service.java:20)', 60, 0)
    buffer.flush()
    return {'id': 'fixture', 'label': '测试服务器', 'server_id': 's1', 'service': 'app', 'path': '/app.log',
            'encoding': 'utf-8', 'started': 1, 'state': '监控中', 'bytes': 100, 'backlog': 0,
            'events': buffer.snapshot(), 'notices': [], 'dropped_events': 0, 'truncated_lines': 0,
            'recent': list(buffer.recent), 'checkpoint': {'offset': 100, 'generation': 0, 'anchor': ''}}


class LogAnalysisTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        logger = patch('tools.agent_diag._LOG_DIR', directory.name)
        logger.start()
        self.addCleanup(logger.stop)

    def test_incremental_utf8_partial_line_and_resume(self):
        ftp, cursor, rows = Sftp(), Cursor(), []
        emit = lambda *row: rows.append(row)
        cursor.read(ftp, '/app.log', emit)
        data = 'ERROR 中文\n'.encode()
        ftp.data = data[:-2]
        cursor.read(ftp, '/app.log', emit)
        self.assertEqual(rows, [])
        resumed = Cursor(checkpoint=cursor.checkpoint())
        ftp.data = data
        resumed.read(ftp, '/app.log', emit)
        self.assertEqual(rows[0][0], 'ERROR 中文')

    def test_rotation_and_copytruncate(self):
        ftp, cursor, rows = Sftp(b'old file\n'), Cursor(), []
        cursor.read(ftp, '/x', lambda *x: rows.append(x))
        ftp.data = b'ERROR new longer file\n'
        result = cursor.read(ftp, '/x', lambda *x: rows.append(x))
        self.assertTrue(result['rotated'])
        self.assertEqual(rows[-1][2], 1)
        ftp.data = b'ERROR\n'
        cursor.read(ftp, '/x', lambda *x: rows.append(x))
        self.assertEqual(rows[-1][2], 2)

    def test_multiline_repeat_count_and_error_code(self):
        clock = [100.0]
        buffer = EventBuffer(clock=lambda: clock[0])
        for _ in range(100):
            buffer.line('2026-10-28 12:00:00 ERROR E104 failure', 0, 0)
            buffer.line('    at app.Service.run(Service.java:20)', 50, 0)
            buffer.line('Caused by: java.io.IOException: failed', 100, 0)
            buffer.flush()
        buffer.line('2026-10-28 12:00:00 ERROR E105 failure', 150, 0)
        clock[0] += 4
        buffer.idle()
        self.assertEqual([e.count for e in buffer.events], [100, 1])

    def test_buffer_limits_and_warn_opt_in(self):
        buffer = EventBuffer(limit=2)
        buffer.line('WARN ignored', 0, 0)
        buffer.flush()
        self.assertFalse(buffer.events)
        for i in range(4):
            buffer.line('ERROR E' + str(i), i, 0)
            buffer.flush()
        self.assertEqual(len(buffer.events), 2)
        self.assertEqual(buffer.dropped, 2)

    def test_secret_redaction(self):
        clean = Redactor().clean('password=synthetic-secret\nAuthorization: Bearer fake\nERROR phone 13800138000')
        self.assertNotIn('synthetic-secret', clean)
        self.assertNotIn('13800138000', clean)
        self.assertIn('ERROR', clean)

    def test_multiline_private_key_and_payload_hidden(self):
        buffer = EventBuffer()
        for i, line in enumerate(['ERROR failed', '-----BEGIN PRIVATE KEY-----', 'synthetic-base64',
                                  '-----END PRIVATE KEY-----', 'requestBody={', 'synthetic-payload', '}',
                                  '2026-10-28 12:00:00 INFO resumed']):
            buffer.line(line, i * 100, 0)
        buffer.flush()
        text = json.dumps(buffer.snapshot())
        self.assertNotIn('synthetic-base64', text)
        self.assertNotIn('synthetic-payload', text)

    def test_read_range_checks_source_and_does_not_write(self):
        ftp = Sftp(b'INFO fixture\nERROR E104\n')
        client = SimpleNamespace(open_sftp=lambda: ftp, close=lambda: None)
        task = Monitor({'id': 's1'}, 'app', '/app.log', connector=lambda *a, **k: client)
        task.cursor.read(ftp, '/app.log', task.buffer.line)
        checkpoint = task.cursor.checkpoint()
        result = task.read_range(0, 65536, checkpoint, CancellationToken())
        self.assertIn('E104', result['excerpt'])
        ftp.data = b'INFO changed\nERROR E105\n'
        with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'):
            task.read_range(0, 65536, checkpoint, CancellationToken())

    def test_nonblocking_snapshot_while_network_read_waits(self):
        task = Monitor({'id': 's1'}, 'app', '/app.log')
        entered, release = threading.Event(), threading.Event()
        def hold():
            with task.lock:
                entered.set()
                release.wait(2)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertIsNone(task.snapshot(blocking=False))
        release.set()
        thread.join(1)

    def test_closed_tools_reject_paths_shell_and_wrong_scope(self):
        registry = LogTools(snapshot())
        context = RunContext('r', 'fixture', 'm')
        token = CancellationToken()
        for call in (ToolCall('1', 'exec', {'command': 'anything'}),
                     ToolCall('2', 'read_recent', {'path': '/outside'})):
            self.assertFalse(registry.execute(call, context=context, cancellation=token).ok)
        self.assertFalse(registry.execute(ToolCall('3', 'list_events'),
            context=RunContext('r', 'wrong', 'm'), cancellation=token).ok)
        found = registry.execute(ToolCall('4', 'search_log', {'keyword': 'demo'}), context=context, cancellation=token)
        self.assertTrue(found.ok)
        self.assertIn('E104', str(found.data))

    def test_real_runner_text_tool_roundtrip(self):
        class Model:
            def __init__(self):
                self.requests = []

            def complete_turn(self, request, **kwargs):
                self.requests.append(request)
                if len(self.requests) == 1:
                    return AgentTurn(text='<tool_call><function=search_log><parameter=keyword>demo</parameter></function></tool_call>')
                return AgentTurn(text='已读取证据，日志显示 E104；根因尚未验证。')
        model = Model()
        with tempfile.TemporaryDirectory() as directory, patch('tools.agent_diag._LOG_DIR', directory):
            result = analyze(snapshot(), '请查错误', 'fixture-model', CancellationToken(), adapter=model)
        self.assertEqual(len(model.requests), 2)
        self.assertIn('E104', result.final_text)
        self.assertTrue(any(m['role'] == 'tool' for m in model.requests[1].messages))

    def test_cache_report_quota_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save(snapshot())
            saved = store.load()[0]
            self.assertNotIn('recent', saved)
            self.assertIn('fixture', json.dumps(saved))
            self.assertIn('E104', report(saved))
            with self.assertRaises(ValueError):
                Store(directory, quota=1).save(snapshot())
            store.clear()
            self.assertEqual(store.load(), [])

    def test_monitor_stop_closes_only_owned_connection(self):
        ftp = Sftp(b'ERROR fixture\n')
        client = SimpleNamespace(open_sftp=lambda: ftp, close=lambda: None)
        task = Monitor({'id': 's1'}, 'app', '/app.log', backfill=100, connector=lambda *a, **k: client)
        task.start()
        deadline = time.time() + 2
        while not task.bytes_read and time.time() < deadline:
            time.sleep(.01)
        task.stop()
        task.thread.join(2)
        self.assertFalse(task.thread.is_alive())
        self.assertTrue(ftp.closed)
        self.assertEqual(len(task.snapshot()['events']), 1)

    def test_late_model_result_suppressed(self):
        entered, release = threading.Event(), threading.Event()
        def fake(*args, **kwargs):
            entered.set()
            release.wait(2)
            return SimpleNamespace(final_text='late', error=None)
        service = LogService(analyzer=fake)
        self.addCleanup(service.close)
        task = Monitor({'id': 's1'}, 'app', '/app.log')
        service.tasks[task.id] = task
        service.enqueue(task.id, 'why', 'm')
        self.assertTrue(entered.wait(2))
        service.stop(task.id)
        release.set()
        service.close()
        self.assertTrue(service.join(2))
        self.assertEqual(service.drain(), [])


class LogAnalysisUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_lazy_workspace_and_report_edit_preserved(self):
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        with tempfile.TemporaryDirectory() as directory, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            widget = LogAnalysisWorkspace(lambda: [], store=Store(directory))
            widget.snapshots['fixture'] = snapshot()
            widget.tasks.addItem('fixture', 'fixture')
            widget.tasks.setCurrentIndex(1)
            widget.make_report()
            self.assertIn('E104', widget.draft.toPlainText())
            widget.draft.setPlainText('人工编辑')
            widget.tasks.setCurrentIndex(0)
            widget.tasks.setCurrentIndex(1)
            self.assertEqual(widget.draft.toPlainText(), '人工编辑')
            self.assertIsNone(widget.service)
            widget.shutdown()
            widget.deleteLater()

    def test_enter_shift_enter_and_ime(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QInputMethodEvent
        from PyQt6.QtTest import QTest
        from ui.log_analysis.workspace import Composer
        editor = Composer()
        sent = []
        editor.submitted.connect(lambda: sent.append(True))
        QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertFalse(sent)
        editor.inputMethodEvent(QInputMethodEvent('拼音', []))
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertFalse(sent)
        editor.inputMethodEvent(QInputMethodEvent('', []))
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(sent, [True])
        editor.deleteLater()

    def test_chat_append_preserves_scroll_and_send_reaches_bottom(self):
        from ui.log_analysis.workspace import LogAnalysisWorkspace
        with tempfile.TemporaryDirectory() as directory, patch('tools.intranet_llm.list_enabled_items', return_value=[]):
            widget = LogAnalysisWorkspace(lambda: [], store=Store(directory))
            widget.selected_id = 'fixture'
            widget.resize(1100, 740)
            widget.show()
            widget.append_chat('fixture', '\n'.join('synthetic line ' + str(i) for i in range(200)), True)
            self.app.processEvents()
            bar = widget.chat.verticalScrollBar()
            bar.setValue(20)
            widget.append_chat('fixture', 'background message')
            self.assertEqual(bar.value(), 20)
            widget.append_chat('fixture', 'user question', True)
            self.assertEqual(bar.value(), bar.maximum())
            widget.shutdown()
            widget.close()


if __name__ == '__main__':
    unittest.main()
