"""Focused P6 widget checks using synthetic events and temporary settings."""

import ast
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace


repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / 'tests' / 'ssh_session'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'


def deny_network(*args, **kwargs):
    raise AssertionError('network access in isolated verify')


socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.getaddrinfo = deny_network


import _isolation
import config

for name, value in vars(config).copy().items():
    if isinstance(value, str) and Path(value).is_absolute() and Path(value).is_relative_to(repo / 'data'):
        setattr(config, name, str(Path(config.local_data_dir()) / Path(value).relative_to(repo / 'data')))

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QLabel
from panels import agent_workbench_panel as workbench
from ui.chat_kit import ChatView, ErrorCard, MessageBubble, ToolStepLine
from ui.data_center.agent_panel import DataCenterAgentPanel, empty_answer_text


def event(kind, **payload):
    return SimpleNamespace(event_type=kind, payload=payload)


app = QApplication([])
passed = 0


def check(condition, label):
    global passed
    assert condition, label
    passed += 1
    print('PASS', label)


try:
    for path in (repo / 'panels/agent_workbench_panel.py', repo / 'ui/chat_kit/bubble.py', repo / 'ui/chat_kit/markdown.py', repo / 'ui/chat_kit/view.py', repo / 'ui/data_center/agent_panel.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'))
    check(True, 'edited Python parses')

    panel = DataCenterAgentPanel()
    panel.set_compact_rail(True)
    panel.start_run('synthetic-run', 'synthetic-tab')
    panel.prompt_input.setPlainText('<b>查一条</b>')
    panel.acknowledge_prompt()
    panel._handle_event(event('tool_call', name='query_readonly', call_id='private-call-key'))
    panel._handle_event(event('tool_started', name='query_readonly', call_id='private-call-key'))
    panel._handle_event(event('tool_result', name='query_readonly', call_id='private-call-key',
                              ok=False, result_preview='synthetic preview'))
    rows = panel.chat.findChildren(ToolStepLine)
    check(isinstance(panel.chat, ChatView) and len(rows) == 1, 'DC ChatView coalesces tools by event identity')
    check(rows[0].mark.text() == '!' and '只读查询' in rows[0].text(), 'DC failed tool remains a readable step')
    check(all('private-call-key' not in label.text() for label in panel.findChildren(QLabel))
          and 'private-call-key' not in panel.evidence_view.toPlainText(), 'DC call_id stays off the UI')
    check('synthetic preview' in panel.result_preview_view.toPlainText(), 'DC result evidence preserved')
    panel._handle_event(event('final', text='', failure_reason='parse_failed'))
    failure = panel._chat_answer.error_card
    check(isinstance(failure, ErrorCard) and not failure.isHidden()
          and failure.reason.text() == empty_answer_text('parse_failed')
          and failure.retry_btn.isHidden(), 'DC uses the translated failure card without replay')
    check(panel.result_status_label.text() == '已完成 · 未验证', 'DC unverified badge preserved')
    panel.reset_run()
    check(not panel.chat.has_items(), 'DC reset clears chat')
    panel.set_compact_rail(False)
    panel.start_run('verified-run', 'synthetic-tab')
    panel._handle_event(event('model_text_delta', text='**半成品'))
    check(panel._chat_answer.visible_text() == '**半成品', 'DC partial answer remains literal')
    panel._handle_event(event('final', text='**完整正文**', verified=True))
    check(panel._chat_answer.visible_text() == '**完整正文**'
          and panel.result_status_label.text() == '已完成 · 已验证', 'DC final answer preserves verification')
    panel.reset_run()
    panel.start_run('cancelled-run', 'synthetic-tab')
    panel._on_stop_clicked()
    panel._handle_event(event('final', text='late result', verified=True))
    check(panel.result_status_label.text() == '取消待确认', 'DC late final cannot bypass pending cancellation')
    panel._handle_event(event('cancelled'))
    check(panel.result_status_label.text() == '已取消', 'DC cancellation still gets a terminal status')

    workbench.list_workspaces = lambda: []
    workbench.list_enabled_items = lambda: []
    wb = workbench.AgentWorkbenchPanel()
    wb._append_step('read_file', {'path': 'synthetic.py', 'call_id': 'private-live-key'})
    live = wb.scroll.findChildren(ToolStepLine)[0]
    check('synthetic.py' in live.text() and 'private-live-key' not in live.text(),
          'workbench uses ToolStepLine for live steps')
    wb._append_stream_delta('reasoning_delta', '<b>temporary reasoning</b>')
    wb._append_stream_delta('assistant_delta', 'partial body')
    check(isinstance(wb.scroll, ChatView) and isinstance(wb._stream_holder, MessageBubble)
          and wb._stream_holder.thinking.body.isHidden()
          and wb._stream_holder.visible_text() == 'partial body', 'workbench streams a ChatKit bubble')

    worker = SimpleNamespace(failure_reason='', context_memory=None)
    workbench._WorkbenchWorker._capture_terminal_event(worker, event('final', failure_reason='empty_output'))
    wb._agent_worker = worker
    wb._task_is_current = lambda task_id: True
    wb._refresh_space_tree = lambda **kwargs: None
    wb._active_task = dict(workspace_id='s', conversation_id='c', workspace_revision=1,
                           conversation_revision=1, base_message_count=1, base_tool_call_count=0,
                           user_message='test question')
    answer = empty_answer_text('empty_output')
    messages = [
        dict(id='user', role='user', content='test question'),
        dict(id='call', role='assistant', content='', tool_calls=[{
            'id': 'private-history-key', 'function': {'name': 'read_file', 'arguments': '{"path":"synthetic.py"}'},
        }]),
        dict(id='answer', role='assistant', content=answer),
    ]
    calls = [dict(id='private-history-key', error='synthetic failure')]
    saved = {}

    def commit(workspace_id, conversation_id, **kwargs):
        saved.update(kwargs)
        return dict(status='saved', workspace=dict(id=workspace_id, conversations=[
            dict(id=conversation_id, messages=kwargs['messages'], tool_calls=kwargs['tool_calls']),
        ]))

    workbench.commit_conversation_result = commit
    wb._on_agent_completed('task', answer, messages, calls)
    card = wb.scroll.bubble('answer').error_card
    check(not card.isHidden() and card.reason.text() == answer, 'workbench final failure renders ErrorCard')
    check(saved['messages'] == messages and 'temporary reasoning' not in str(saved)
          and 'failure_reason' not in str(saved), 'workbench persistence payload keeps its original contract')
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    history = wb.scroll.findChildren(ToolStepLine)
    check(len(history) == 1 and history[0].mark.text() == '!'
          and 'private-history-key' not in history[0].text(), 'workbench history keeps tool state and hides identifiers')
    wb._agent_worker = None
    wb._active_task = None

    qss = (repo / 'resources/style.qss').read_text(encoding='utf-8-sig')
    check(all('#' + selector in qss for selector in (
        'chat-user-bubble', 'chat-assistant-bubble', 'chat-error-card', 'chat-tool-caption',
        'agent-steps-toggle', 'agent-step-note', 'agent-composer-input', 'agent-empty-hero',
        'ai-explain', 'zone-title',
    )) and all('#' + selector not in qss for selector in (
        'chat-bubble-user', 'chat-bubble-assistant', 'agent-stream-card', 'agent-step-row',
    )), 'old QSS removed and neighbouring/live QSS retained')
    runner_ast = ast.parse((repo / 'tools/agent_core/runner.py').read_text(encoding='utf-8-sig'))
    limits = {node.targets[0].id: node.value.value for node in runner_ast.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and isinstance(node.value, ast.Constant)}
    check(limits.get('_MAX_TOOL_FORMAT_RETRIES') == limits.get('_MAX_ANNOUNCEMENT_CONTINUATIONS') == 2,
          'format repair and announcement continuation limits remain two')
finally:
    _isolation.assert_untouched()

print(f'RESULT: {passed} passed, 0 failed')
