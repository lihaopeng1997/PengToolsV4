# -*- coding: utf-8 -*-
"""Production workbench/worker tests with a temporary store and fake shared model adapter."""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from tools.agent_core import AgentModelCapability, AgentTurn, ModelDelta, ToolCall
from tools.agent_core.request_budget import RequestInputLimitExceeded
from tools.agent_store import empty_workspace, load_workspace, save_workspace


class _ScriptedAdapter:
    effective_capability = AgentModelCapability.NATIVE_TOOLS

    def __init__(self, turns, *, gate=None, started=None):
        self.turns = list(turns)
        self.gate = gate
        self.started = started or threading.Event()
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        index = len(self.requests) - 1
        step = self.turns[index]
        if step.get('summary'):
            on_delta(ModelDelta(kind='reasoning', text=step['summary']))
        for chunk in step.get('deltas') or ():
            on_delta(ModelDelta(kind='text', text=chunk))
        if step.get('wait'):
            self.started.set()
            self.gate.wait(3.0)
        return step['turn']


class _FinalLimitAfterReadAdapter:
    effective_capability = AgentModelCapability.NATIVE_TOOLS

    def __init__(self):
        self.requests = []
        self.released = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        if len(self.requests) == 1:
            return AgentTurn(
                tool_calls=(ToolCall('temporary-read-call', 'read_file', {'path': 'src/sample.py'}),),
                finish_reason='tool_calls',
            )
        raise RequestInputLimitExceeded(
            input_bytes=request.max_request_bytes + 123,
            limit_bytes=request.max_request_bytes,
        )

    def release_run(self, context):
        self.released.append(context.run_id)


class AgentWorkbenchRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='agent_workbench_runtime_')
        agent_dir = os.path.join(self.tmp_dir, 'agent-data')
        os.makedirs(agent_dir, exist_ok=True)
        self._network_guard_hits = 0
        self._catalog_guard_hits = 0

        def deny_network(*_args, **_kwargs):
            self._network_guard_hits += 1
            raise AssertionError('Network access is disabled in this targeted test.')

        def deny_catalog(*_args, **_kwargs):
            self._catalog_guard_hits += 1
            raise AssertionError('The real model catalog reader is disabled in this targeted test.')

        self._patchers = [
            patch('tools.agent_store.AGENT_DIR', agent_dir),
            patch('tools.agent_store.AGENT_WORKSPACES_DIR', os.path.join(agent_dir, 'workspaces')),
            patch('tools.agent_store.AGENT_INDEX_FILE', os.path.join(agent_dir, 'index.json')),
            patch('tools.agent_store.ensure_config_dir', lambda: None),
            patch('config.load_settings', return_value={}),
            patch('config.save_settings', return_value={}),
            patch('config.load_layout_splitter', return_value=None),
            patch('config.save_layout_splitter', return_value=None),
            patch('panels.agent_workbench_panel.load_settings', return_value={}),
            patch('panels.agent_workbench_panel.list_enabled_items', return_value=[]),
            patch('tools.intranet_llm.list_enabled_items', side_effect=deny_catalog),
            patch('socket.create_connection', side_effect=deny_network),
            patch('http.client.HTTPConnection.connect', side_effect=deny_network),
            patch('http.client.HTTPSConnection.connect', side_effect=deny_network),
        ]
        for patcher in self._patchers:
            patcher.start()
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        self.panel = AgentWorkbenchPanel('zh')
        self.panel.resize(1320, 860)
        self.adapters = []

    def tearDown(self):
        for adapter in self.adapters:
            gate = getattr(adapter, 'gate', None)
            if gate is not None:
                gate.set()
        if self.panel is not None:
            if self.panel.agent_is_running():
                self.panel.agent_shutdown_cleanup()
                self._pump_until(lambda: not self.panel.agent_is_running(), timeout=4.0)
            worker = self.panel._agent_worker
            if worker is not None:
                worker.wait(4_000)
                self._pump_until(lambda: self.panel._agent_worker is None, timeout=1.0)
            viewer = self.panel._conflict_viewer
            if viewer is not None:
                viewer.close()
            self.panel.close()
            self.panel.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for patcher in reversed(self._patchers):
            patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.assertEqual(self._catalog_guard_hits, 0, 'A real catalog reader was reached.')
        self.assertEqual(self._network_guard_hits, 0, 'A network connection was attempted.')

    def _pump_until(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return bool(condition())

    def _workspace(self, title, directory, messages=None):
        os.makedirs(directory, exist_ok=True)
        workspace = empty_workspace(title=title, workspace_dir=directory)
        conversation = workspace['conversations'][0]
        if messages is not None:
            conversation['messages'] = messages
        save_workspace(workspace)
        return workspace

    def _submit(self, workspace, adapter, prompt='Inspect the referenced file'):
        self.panel._select_workspace(workspace['id'])
        self.panel.input.setPlainText(prompt)
        profile = {
            'id': 'temporary-model-profile',
            'model': 'fake-workbench-model',
            'enabled': True,
            'base_url': 'http://temporary.invalid/v1',
            'agent_capability': 'native_tools',
        }
        self.adapters.append(adapter)
        with patch.object(self.panel, '_current_model', return_value=profile), \
                patch.object(self.panel, '_build_model_adapter_for_task', return_value=adapter):
            self.panel._send()
        self.assertTrue(self.panel.agent_is_running())

    def test_worker_reads_attachment_edits_with_confirmation_and_persists_final_once(self):
        project = os.path.join(self.tmp_dir, 'project')
        src = os.path.join(project, 'src')
        os.makedirs(src, exist_ok=True)
        source_path = os.path.join(src, 'feature.py')
        with open(source_path, 'w', encoding='utf-8') as stream:
            stream.write('VALUE = 1\n# ORIGINAL_CODE_MARKER\n')
        rows = [f'line-{index:04d}: ' + ('x' * 70) for index in range(500)]
        generated = '\n'.join(rows) + '\n'
        adapter = _ScriptedAdapter([
            {'summary': '正在检查引用文件。', 'deltas': ['检查项目…'],
             'turn': AgentTurn(tool_calls=(ToolCall('read-1', 'read_file', {'path': 'src/feature.py'}),),
                               finish_reason='tool_calls')},
            {'summary': '已看到源文件内容。', 'deltas': ['准备精确编辑。'],
             'turn': AgentTurn(tool_calls=(ToolCall('edit-1', 'edit_file', {
                 'path': 'src/feature.py', 'old_str': 'VALUE = 1', 'new_str': 'VALUE = 2',
             }),), finish_reason='tool_calls')},
            {'summary': '编辑已确认。', 'deltas': ['准备生成完整文件。'],
             'turn': AgentTurn(tool_calls=(ToolCall('write-1', 'write_file', {
                 'path': 'generated.txt', 'content': generated,
             }),), finish_reason='tool_calls')},
            {'summary': '任务完成摘要不进入历史。', 'deltas': ['项目已完成。'],
             'turn': AgentTurn(text='项目已完成。')},
        ])
        workspace = self._workspace('Runtime closure', project)
        self.panel._file_attachments = ['src/feature.py']
        confirmations = []

        def accept_diff(_parent, title, message, **_kwargs):
            confirmations.append((title, message))
            return True

        with patch('ui.confirm_dialog.confirm_action', side_effect=accept_diff):
            self._submit(workspace, adapter, 'Change the value and create an output file')
            self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))

        with open(source_path, encoding='utf-8') as stream:
            self.assertIn('VALUE = 2', stream.read())
        with open(os.path.join(project, 'generated.txt'), encoding='utf-8') as stream:
            self.assertEqual(stream.read(), generated)
        self.assertEqual(len(confirmations), 2)
        edit_title, edit_diff = confirmations[0]
        write_title, write_diff = confirmations[1]
        self.assertIn('feature.py', edit_title)
        self.assertIn('-VALUE = 1', edit_diff)
        self.assertIn('+VALUE = 2', edit_diff)
        self.assertIn('generated.txt', write_title)
        self.assertGreater(len(write_diff), 30_000)
        self.assertIn('line-0499:', write_diff)

        self.assertEqual(len(adapter.requests), 4)
        first_request = adapter.requests[0]
        self.assertEqual(first_request.context.domain, 'programming')
        self.assertIn('read_file', {item['function']['name'] for item in first_request.tools})
        self.assertTrue(any('尚未读取' in str(item.get('content') or '') for item in first_request.messages))
        read_observation = [item for item in adapter.requests[1].messages if item.get('role') == 'tool']
        self.assertTrue(any('ORIGINAL_CODE_MARKER' in str(item.get('content') or '') for item in read_observation))

        saved = load_workspace(workspace['id'])
        conversation = saved['conversations'][0]
        self.assertEqual(sum(
            item.get('role') == 'assistant' and item.get('content') == '项目已完成。'
            for item in conversation['messages']
        ), 1)
        stored_text = '\n'.join(str(item.get('content') or '') for item in conversation['messages'])
        self.assertNotIn('任务完成摘要不进入历史', stored_text)
        self.assertEqual([item['tool'] for item in conversation['tool_calls']], ['read_file', 'edit_file', 'write_file'])

    def test_cancel_during_diff_confirmation_denies_late_write(self):
        project = os.path.join(self.tmp_dir, 'confirm-cancel')
        os.makedirs(project, exist_ok=True)
        target = os.path.join(project, 'target.txt')
        with open(target, 'w', encoding='utf-8') as stream:
            stream.write('original content\n')
        workspace = self._workspace('Cancel confirmation', project)
        adapter = _ScriptedAdapter([{
            'turn': AgentTurn(tool_calls=(ToolCall('write-cancel', 'write_file', {
                'path': 'target.txt', 'content': 'must never be applied\n',
            }),), finish_reason='tool_calls'),
        }])
        confirm_seen = threading.Event()

        def stop_inside_confirmation(_parent, _title, _message, **_kwargs):
            confirm_seen.set()
            nested = QEventLoop()
            QTimer.singleShot(0, self.panel._stop)
            QTimer.singleShot(30, nested.quit)
            nested.exec()
            return True

        with patch('ui.confirm_dialog.confirm_action', side_effect=stop_inside_confirmation):
            self._submit(workspace, adapter, 'Write a new file version')
            self.assertTrue(self._pump_until(lambda: confirm_seen.is_set()))
            self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))

        with open(target, encoding='utf-8') as stream:
            self.assertEqual(stream.read(), 'original content\n')
        saved = load_workspace(workspace['id'])
        conversation = saved['conversations'][0]
        self.assertEqual(sum(item.get('role') == 'assistant' for item in conversation['messages']), 0)
        self.assertFalse(self.panel._bridge._pending)

    def test_cancel_before_confirmation_watcher_tick_closes_production_dialog(self):
        """Stopping just after the real modal opens must not strand confirmation past its first timer tick."""
        from PyQt6.QtWidgets import QApplication

        project = os.path.join(self.tmp_dir, 'confirm-first-tick-cancel')
        os.makedirs(project, exist_ok=True)
        target = os.path.join(project, 'target.txt')
        with open(target, 'w', encoding='utf-8') as stream:
            stream.write('original content\n')
        workspace = self._workspace('Cancel before first watcher tick', project)
        adapter = _ScriptedAdapter([{
            'turn': AgentTurn(tool_calls=(ToolCall('write-before-first-tick', 'write_file', {
                'path': 'target.txt', 'content': 'must never be applied\n',
            }),), finish_reason='tool_calls'),
        }])
        state = {'dialog_seen': False, 'cancelled': False, 'watchdog': False}
        cancel_timer = QTimer(self.panel)
        cancel_timer.setInterval(1)

        def cancel_when_dialog_is_active():
            dialog = QApplication.activeModalWidget()
            if dialog is None or dialog.parentWidget() is not self.panel:
                return
            state['dialog_seen'] = True
            cancel_timer.stop()
            state['cancelled'] = True
            self.panel._stop()

        def close_only_if_stuck():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and dialog.parentWidget() is self.panel:
                state['watchdog'] = True
                dialog.reject()

        cancel_timer.timeout.connect(cancel_when_dialog_is_active)
        cancel_timer.start()
        QTimer.singleShot(750, close_only_if_stuck)
        self._submit(workspace, adapter, 'Write a new file version')
        try:
            self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running(), timeout=2.0))
        finally:
            cancel_timer.stop()

        self.assertTrue(state['dialog_seen'], 'The production confirmation dialog should have opened.')
        self.assertTrue(state['cancelled'], 'Cancellation should run inside the modal loop before 40ms.')
        self.assertFalse(state['watchdog'], 'The bridge watcher must reject the dialog without a test timeout.')
        self.assertIsNone(QApplication.activeModalWidget())
        self.assertFalse(self.panel._bridge._pending)
        with open(target, encoding='utf-8') as stream:
            self.assertEqual(stream.read(), 'original content\n')

    def test_context_switch_cancels_late_result_from_the_previous_conversation(self):
        old_dir = os.path.join(self.tmp_dir, 'old-project')
        new_dir = os.path.join(self.tmp_dir, 'new-project')
        old_workspace = self._workspace('Old project', old_dir)
        new_workspace = self._workspace('New project', new_dir)
        gate = threading.Event()
        started = threading.Event()
        adapter = _ScriptedAdapter([{
            'summary': '早期摘要', 'deltas': ['迟到正文'], 'wait': True,
            'turn': AgentTurn(text='迟到的最终结果'),
        }], gate=gate, started=started)
        self._submit(old_workspace, adapter, 'Old project question')
        self.assertTrue(self._pump_until(started.is_set))

        self.panel._select_workspace(new_workspace['id'])
        self.assertTrue(self.panel._active_task['invalidated'])
        gate.set()
        self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))

        new_saved = load_workspace(new_workspace['id'])
        self.assertEqual(new_saved['conversations'][0]['messages'], [])
        old_saved = load_workspace(old_workspace['id'])
        old_messages = old_saved['conversations'][0]['messages']
        self.assertEqual(sum(item.get('role') == 'user' for item in old_messages), 1)
        self.assertFalse(any(item.get('content') == '迟到的最终结果' for item in old_messages))
        self.assertNotIn('迟到正文', self.panel._stream_body_text)

    def test_stream_is_visible_before_completion_and_final_answer_is_persisted_once(self):
        project = os.path.join(self.tmp_dir, 'stream-project')
        history = [
            {'id': f'h-{index}', 'role': 'assistant' if index % 2 else 'user',
             'content': f'history-{index} ' + ('long text ' * 80)}
            for index in range(36)
        ]
        workspace = self._workspace('Streaming', project, history)
        gate = threading.Event()
        started = threading.Event()
        adapter = _ScriptedAdapter([{
            'summary': '当前轮的简短摘要。',
            'deltas': ['最终完整', '回答'],
            'wait': True,
            'turn': AgentTurn(text='最终完整回答'),
        }], gate=gate, started=started)
        self.panel._select_workspace(workspace['id'])
        self.panel.show()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.verticalScrollBar().maximum(), 0)
        self._submit(workspace, adapter, 'Give me a final answer')
        self.assertTrue(self._pump_until(started.is_set))
        self.assertTrue(self._pump_until(
            lambda: self.panel._stream_body is not None
            and self.panel._stream_body.toPlainText() == '最终完整回答'
        ))
        self.assertFalse(gate.is_set(), 'The UI showed streamed text before the fake model was released.')
        self.assertEqual(self.panel._stream_summary.toPlainText(), '当前轮的简短摘要。')
        scroll = self.panel.scroll.verticalScrollBar()
        scroll.setValue(0)
        self.app.processEvents()
        self.assertEqual(scroll.value(), 0)

        gate.set()
        self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))
        saved = load_workspace(workspace['id'])
        conversation = saved['conversations'][0]
        self.assertEqual(sum(
            item.get('role') == 'assistant' and item.get('content') == '最终完整回答'
            for item in conversation['messages']
        ), 1)
        self.assertNotIn('当前轮的简短摘要。', '\n'.join(item.get('content', '') for item in conversation['messages']))

    def test_external_conversation_update_preserves_old_worker_result_separately(self):
        from tools.agent_store import append_message
        project = os.path.join(self.tmp_dir, 'revision-project')
        workspace = self._workspace('Revision', project)
        gate = threading.Event()
        started = threading.Event()
        adapter = _ScriptedAdapter([{
            'wait': True, 'turn': AgentTurn(text='old worker result'),
        }], gate=gate, started=started)
        self._submit(workspace, adapter, 'start')
        self.assertTrue(self._pump_until(started.is_set))
        append_message(workspace['id'], {'role': 'user', 'content': 'new requirement'})
        gate.set()
        self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))
        saved = load_workspace(workspace['id'])
        conversation = saved['conversations'][0]
        self.assertEqual([item['content'] for item in conversation['messages']], ['start', 'new requirement'])
        self.assertEqual(saved['conflicted_runs'][0]['messages'][0]['content'], 'old worker result')
        self.assertEqual(self.panel._workspace_session['conversations'][0]['messages'], conversation['messages'])
        self.assertTrue(self.panel.conflicts_btn.isEnabled())
        before_view = load_workspace(workspace['id'])
        self.panel.conflicts_btn.click()
        self.app.processEvents()
        viewer = self.panel._conflict_viewer
        self.assertIsNotNone(viewer)
        self.assertFalse(viewer.isModal())
        text_widget = viewer.findChild(QPlainTextEdit, 'agent-conflict-viewer-text')
        self.assertIsNotNone(text_widget)
        self.assertTrue(text_widget.isReadOnly())
        self.assertIn('old worker result', text_widget.toPlainText())
        viewer.close()
        self.app.processEvents()
        self.assertEqual(load_workspace(workspace['id']), before_view)

    def test_readonly_conflict_viewer_does_not_release_a_gated_worker(self):
        workspace = self._workspace('Viewer lifetime', os.path.join(self.tmp_dir, 'viewer-project'))
        saved = load_workspace(workspace['id'])
        saved['conflicted_runs'] = [{
            'messages': [{'role': 'assistant', 'content': 'preexisting stale result'}],
            'tool_calls': [{'name': 'must-not-run'}],
        }]
        save_workspace(saved)
        self.panel._select_workspace(workspace['id'])
        self.panel.show()
        self.app.processEvents()

        gate = threading.Event()
        started = threading.Event()
        adapter = _ScriptedAdapter([{
            'wait': True, 'turn': AgentTurn(text='still gated'),
        }], gate=gate, started=started)
        self._submit(workspace, adapter, 'keep running')
        self.assertTrue(self._pump_until(started.is_set))
        worker = self.panel._agent_worker
        self.assertIsNotNone(worker)
        self.assertTrue(worker.isRunning())
        self.assertTrue(self.panel.conflicts_btn.isEnabled())

        self.panel.conflicts_btn.click()
        self.app.processEvents()
        viewer = self.panel._conflict_viewer
        self.assertIsNotNone(viewer)
        text_widget = viewer.findChild(QPlainTextEdit, 'agent-conflict-viewer-text')
        self.assertTrue(text_widget.isReadOnly())
        self.assertIn('preexisting stale result', text_widget.toPlainText())
        viewer.close()
        self.app.processEvents()
        self.assertIs(self.panel._agent_worker, worker)
        self.assertTrue(worker.isRunning())
        self.assertFalse(gate.is_set())

        gate.set()
        self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))
        self.assertTrue(worker.wait(4_000))

    def test_context_budget_event_is_estimated_and_never_persisted(self):
        from tools.agent_runtime import _context_budget_progress_json
        project = os.path.join(self.tmp_dir, 'budget-project')
        history = [
            {'id': f'old-{index}', 'role': 'assistant',
             'content': f'old assistant history {index} ' + ('x' * 10_000)}
            for index in range(8)
        ]
        workspace = self._workspace('Context budget', project, history)
        gate = threading.Event()
        started = threading.Event()
        adapter = _ScriptedAdapter([{
            'wait': True, 'turn': AgentTurn(text='budget-aware answer'),
        }], gate=gate, started=started)
        observed_budget_events = []

        def observe_budget(payload):
            encoded = _context_budget_progress_json(payload)
            observed_budget_events.append((
                encoded is not None, tuple(sorted(payload)) if type(payload) is dict else (),
                payload.get('context_status') if type(payload) is dict else None,
                payload.get('estimate_kind') if type(payload) is dict else None,
                payload.get('reason') if type(payload) is dict else None,
            ))
            return encoded

        with patch('tools.agent_runtime._context_budget_progress_json', side_effect=observe_budget):
            self._submit(workspace, adapter, 'Check the saved context')
            self.assertTrue(self._pump_until(started.is_set))
            shown = self._pump_until(lambda: not self.panel.context_budget_label.isHidden())
        self.assertTrue(shown, f'No safe context status reached the panel: {observed_budget_events!r}')
        self.assertEqual(self.panel._context_budget_state['status'], 'ready')
        self.assertGreater(self.panel._context_budget_state['messages_dropped'], 0)
        self.assertIn('请求已精简', self.panel.context_budget_label.text())
        self.assertIn('服务端上下文窗口未知', self.panel.context_budget_label.toolTip())
        self.assertIn('本机请求大小估值', self.panel.context_budget_label.toolTip())
        self.assertEqual(len(adapter.requests), 1)
        self.assertFalse(gate.is_set())

        saved_before_release = load_workspace(workspace['id'])
        saved_text = json.dumps(saved_before_release, ensure_ascii=False)
        self.assertNotIn('context_budget', saved_text)
        self.assertNotIn('heuristic_utf8_bytes', saved_text)
        self.assertEqual(sum(
            item.get('id', '').startswith('old-')
            for item in saved_before_release['conversations'][0]['messages']
        ), 8)

        gate.set()
        self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))
        saved_after = load_workspace(workspace['id'])
        self.assertEqual(sum(
            item.get('id', '').startswith('old-')
            for item in saved_after['conversations'][0]['messages']
        ), 8)

    def test_context_budget_producer_and_gui_reject_malformed_or_private_payload(self):
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from tools.agent_runtime import _context_budget_progress_json

        source = {
            'context_status': 'ready',
            'estimate_kind': 'heuristic_utf8_bytes',
            'input_estimate': 2048,
            'context_window_known': False,
            'output_reserve': 4096,
            'messages_kept': 3,
            'messages_dropped': 2,
            'reason': 'trimmed_to_application_budget_window_unknown',
        }
        encoded = _context_budget_progress_json(source)
        self.assertIsNotNone(encoded)
        decoded = AgentWorkbenchPanel._parse_context_budget_progress(encoded)
        self.assertEqual(decoded['messages_dropped'], 2)

        producer_cases = []
        producer_cases.append({**source, 'reasoning_content': 'private continuation'})
        producer_cases.append({**source, 'context_status': []})
        producer_cases.append({**source, 'input_estimate': True})
        producer_cases.append({**source, 'context_window_known': 1})
        producer_cases.append({**source, 'reason': 'untrusted prompt text'})
        for payload in producer_cases:
            self.assertIsNone(_context_budget_progress_json(payload))

        gui_cases = [
            'not-json',
            json.dumps({'schema': 1, **source}),
            encoded + (' ' * 600),
            json.dumps({**json.loads(encoded), 'reasoning_content': 'private continuation'}),
        ]
        for payload in producer_cases:
            if 'reasoning_content' in payload:
                continue
            safe_shape = {
                'schema': 1,
                'status': payload.get('context_status'),
                'estimate_kind': payload.get('estimate_kind'),
                'input_estimate': payload.get('input_estimate'),
                'context_window_known': payload.get('context_window_known'),
                'output_reserve': payload.get('output_reserve'),
                'messages_kept': payload.get('messages_kept'),
                'messages_dropped': payload.get('messages_dropped'),
                'reason': payload.get('reason'),
            }
            gui_cases.append(json.dumps(safe_shape, ensure_ascii=False))
        for payload in gui_cases:
            self.assertIsNone(AgentWorkbenchPanel._parse_context_budget_progress(payload))
        self.assertEqual(self._catalog_guard_hits, 0)
        self.assertEqual(self._network_guard_hits, 0)

        gate_source = {
            'request_budget_status': 'blocked',
            'measurement_kind': 'serialized_request_utf8_bytes',
            'local_byte_limit': 64_000,
        }
        gate_encoded = _context_budget_progress_json(gate_source)
        self.assertIsNotNone(gate_encoded)
        gate_shape = json.loads(gate_encoded)
        self.assertEqual(gate_shape, {
            'schema': 2,
            'status': 'blocked',
            'estimate_kind': 'serialized_request_utf8_bytes',
            'request_byte_limit': 64_000,
        })
        gate_state = AgentWorkbenchPanel._parse_context_budget_progress(gate_encoded)
        self.assertEqual(gate_state, gate_shape)
        for invalid in (
            {**gate_source, 'private_body': 'private marker'},
            {**gate_source, 'request_budget_status': True},
            {**gate_source, 'measurement_kind': 'provider_tokens'},
            {**gate_source, 'local_byte_limit': True},
            {**gate_source, 'local_byte_limit': 0},
            {**gate_source, 'local_byte_limit': 10_000_001},
        ):
            self.assertIsNone(_context_budget_progress_json(invalid))
        for invalid in (
            {**gate_shape, 'private_body': 'private marker'},
            {**gate_shape, 'status': True},
            {**gate_shape, 'estimate_kind': 'provider_tokens'},
            {**gate_shape, 'request_byte_limit': True},
            {**gate_shape, 'request_byte_limit': 0},
            {**gate_shape, 'request_byte_limit': 10_000_001},
        ):
            self.assertIsNone(AgentWorkbenchPanel._parse_context_budget_progress(
                json.dumps(invalid, ensure_ascii=False),
            ))

    def test_worker_final_request_block_is_transient_and_finishes_after_temp_read(self):
        project = os.path.join(self.tmp_dir, 'final-request-project')
        src = os.path.join(project, 'src')
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, 'sample.py'), 'w', encoding='utf-8') as stream:
            stream.write('TEMP_READ_RECEIPT = "safe"\n')
        workspace = self._workspace('Final request gate', project)
        adapter = _FinalLimitAfterReadAdapter()

        self._submit(workspace, adapter, 'Read the temporary file')
        worker = self.panel._agent_worker
        self.assertIsNotNone(worker)
        self.assertTrue(self._pump_until(lambda: not self.panel.agent_is_running()))
        self.assertTrue(worker.wait(4_000))

        self.assertEqual(len(adapter.requests), 2)
        self.assertEqual([request.max_request_bytes for request in adapter.requests], [64_000, 64_000])
        self.assertEqual(adapter.released, [adapter.requests[0].context.run_id])
        self.assertIsNone(self.panel._agent_worker)
        self.assertFalse(worker.isRunning())
        self.assertEqual(self.panel._context_budget_state, {
            'schema': 2,
            'status': 'blocked',
            'estimate_kind': 'serialized_request_utf8_bytes',
            'request_byte_limit': 64_000,
        })
        self.assertIn('上下文受限', self.panel.context_budget_label.text())
        tooltip = self.panel.context_budget_label.toolTip()
        self.assertIn('后续模型请求未发送', tooltip)
        self.assertIn('此前已完成操作', tooltip)
        self.assertIn('本机字节限额不能保证请求符合模型上下文窗口限制', tooltip)
        self.assertNotIn('上下文窗口未知', tooltip)
        self.assertNotIn('64,123', tooltip)

        saved = load_workspace(workspace['id'])
        saved_text = json.dumps(saved, ensure_ascii=False)
        self.assertIn('TEMP_READ_RECEIPT', saved_text)
        self.assertIn('read_file', saved_text)
        self.assertNotIn('context_budget', saved_text)
        self.assertNotIn('serialized_request_utf8_bytes', saved_text)
        self.assertNotIn('64,123', saved_text)
        self.assertNotIn('64123', saved_text)

    def test_schema2_tooltip_is_neutral_after_known_v1_or_when_received_alone(self):
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from tools.agent_runtime import _context_budget_progress_json

        known_v1 = _context_budget_progress_json({
            'context_status': 'ready',
            'estimate_kind': 'heuristic_utf8_bytes',
            'input_estimate': 1024,
            'context_window_known': True,
            'output_reserve': 4096,
            'messages_kept': 3,
            'messages_dropped': 0,
            'reason': 'within_known_input_budget',
        })
        v2 = _context_budget_progress_json({
            'request_budget_status': 'blocked',
            'measurement_kind': 'serialized_request_utf8_bytes',
            'local_byte_limit': 64_000,
        })
        v1_state = AgentWorkbenchPanel._parse_context_budget_progress(known_v1)
        v2_state = AgentWorkbenchPanel._parse_context_budget_progress(v2)
        self.assertTrue(v1_state['context_window_known'])
        self.assertEqual(v2_state['schema'], 2)

        for language in ('zh', 'en'):
            self.panel.set_language(language)
            self.panel._show_context_budget_state(v1_state)
            known_tooltip = self.panel.context_budget_label.toolTip()
            self.assertNotIn('服务端上下文窗口未知', known_tooltip)
            self.assertNotIn('server context window is unknown', known_tooltip)

            for preceding_state in ('known-v1', 'none'):
                if preceding_state == 'none':
                    self.panel._clear_context_budget_state()
                self.panel._show_context_budget_state(v2_state)
                tooltip = self.panel.context_budget_label.toolTip()
                if language == 'zh':
                    self.assertIn('后续模型请求未发送', tooltip)
                    self.assertIn('此前已完成操作仍保留记录', tooltip)
                    self.assertIn('本机 JSON UTF-8 字节限额（64000 bytes）', tooltip)
                    self.assertIn('不能保证请求符合模型上下文窗口限制', tooltip)
                    self.assertNotIn('服务端上下文窗口未知', tooltip)
                else:
                    self.assertIn('The subsequent model request was not sent', tooltip)
                    self.assertIn('earlier completed operations remain recorded', tooltip)
                    self.assertIn('local JSON UTF-8 byte ceiling (64000 bytes)', tooltip)
                    self.assertIn('does not guarantee the request fits within the model context window', tooltip)
                    self.assertNotIn('server context window is unknown', tooltip)
                self.assertNotIn('64,123', tooltip)
                self.assertNotIn('64123', tooltip)

    def test_budget_exhausted_transient_status_uses_generic_budget_wording(self):
        bridge = self.panel._bridge

        class _ProgressStub:
            def take_progress(self):
                return [('current-task', 'status', 'budget_exhausted')]

        captured = []
        self.panel._bridge = _ProgressStub()
        try:
            with patch.object(self.panel, '_task_is_current', return_value=True), \
                    patch.object(self.panel, '_set_transient_status', side_effect=captured.append):
                for language, expected in (
                    ('zh', '已达到本轮预算上限'),
                    ('en', 'Turn budget limit reached'),
                ):
                    self.panel.set_language(language)
                    self.panel._flush_progress()
                    self.assertEqual(captured[-1], expected)
            self.assertEqual(captured, ['已达到本轮预算上限', 'Turn budget limit reached'])
        finally:
            self.panel._bridge = bridge

    def test_dispose_is_nonblocking_and_drops_worker_only_after_qthread_finishes(self):
        project = os.path.join(self.tmp_dir, 'shutdown-project')
        workspace = self._workspace('Shutdown', project)
        gate = threading.Event()
        started = threading.Event()
        adapter = _ScriptedAdapter([{
            'wait': True,
            'turn': AgentTurn(text='late shutdown result'),
        }], gate=gate, started=started)
        self._submit(workspace, adapter)
        self.assertTrue(self._pump_until(started.is_set))
        worker = self.panel._agent_worker
        start = time.monotonic()

        self.assertFalse(self.panel.agent_shutdown_cleanup())
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1)
        self.assertTrue(worker.isRunning())
        self.assertIs(self.panel._agent_worker, worker)

        gate.set()
        self.assertTrue(self._pump_until(self.panel.agent_shutdown_cleanup))
        self.assertIsNone(self.panel._agent_worker)
        self.assertIsNone(self.panel._bridge)
        self.assertFalse(self.panel._progress_timer.isActive())
        saved = load_workspace(workspace['id'])
        self.assertFalse(any(
            item.get('content') == 'late shutdown result'
            for item in saved['conversations'][0]['messages']
        ))


if __name__ == '__main__':
    unittest.main()
