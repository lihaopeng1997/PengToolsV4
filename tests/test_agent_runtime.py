# -*- coding: utf-8 -*-
"""棒4：内网 Agent 工作台运行时定向测试。"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import agent_runtime as ar
from tools.agent_core import AgentTurn, CancellationToken, ToolCall


class ScriptedModelAdapter:
    """Offline native-tool adapter for exercising the production runner seam."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        value = self.turns.pop(0)
        if isinstance(value, tuple):
            turn, deltas = value
            for delta in deltas:
                on_delta(delta)
            return turn
        return value


def _tool_turn(call_id, name, **arguments):
    return AgentTurn(
        tool_calls=(ToolCall(call_id, name, arguments),),
        finish_reason='tool_calls',
    )


class ModelCallTests(unittest.TestCase):
    """The production loop consumes the shared ModelAdapter protocol."""

    def test_run_agent_loop_uses_injected_shared_adapter(self):
        adapter = ScriptedModelAdapter([AgentTurn(text='任务完成')])
        with path_workspace() as tmp:
            model_cfg = {'id': 'selected-test-model', 'model': 'synthetic'}
            final, messages, calls = ar.run_agent_loop(
                '请你读下 a.py', tmp, model_cfg, [], [], model_adapter=adapter,
            )

        self.assertEqual(final, '任务完成')
        self.assertEqual(adapter.requests[0].context.model_config_id, 'selected-test-model')
        self.assertEqual(len(adapter.requests), 1)
        self.assertEqual(calls, [])
        self.assertEqual([item['content'] for item in messages if item.get('role') == 'assistant'], ['任务完成'])

    def test_model_failure_returns_real_error_not_round_limit(self):
        """Adapter details are not exposed in the user-visible safe failure."""
        class FailingAdapter:
            def complete_turn(self, request, *, cancellation, on_delta):
                raise RuntimeError('boom connection refused')

        with path_workspace() as tmp:
            final, msgs, _ = ar.run_agent_loop(
                '任务', tmp, {}, [], [], model_adapter=FailingAdapter(),
            )

        self.assertIn('执行失败', final)
        self.assertNotIn('boom', final)
        self.assertNotIn('connection refused', final)
        self.assertNotIn('已达到最大工具调用轮次', final)

    def test_tool_execution_roundtrip(self):
        """Native tool calls execute once and receive a matching tool result."""
        adapter = ScriptedModelAdapter([
            _tool_turn('read-1', 'read_file', path='a.py'),
            AgentTurn(text='搞定，看完了'),
        ])
        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'a.py'), 'w', encoding='utf-8') as stream:
                stream.write('print(1)')
            final, msgs, tcs = ar.run_agent_loop(
                '读 a.py', tmp, {}, [], [], model_adapter=adapter,
            )

        self.assertEqual(final, '搞定，看完了')
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0]['tool'], 'read_file')
        self.assertEqual(json.loads(tcs[0]['result'])['ok'], True)
        tool_message = next(item for item in msgs if item.get('role') == 'tool')
        self.assertEqual(tool_message['tool_call_id'], 'read-1')
        assistant_call = next(item for item in msgs if item.get('tool_calls'))
        self.assertEqual(assistant_call['tool_calls'][0]['id'], tool_message['tool_call_id'])


class ToolDispatchTests(unittest.TestCase):
    def test_svn_commit_requires_confirm(self):
        """SVN commit 必须经 confirm_cb 二次确认；取消则 ok=False，不执行。"""
        confirmed = []

        def confirm_cb(title, content):
            confirmed.append(title)
            return False  # 拒绝

        commands = []

        def fake_svn(args, **_kwargs):
            commands.append(args)
            return type('Completed', (), {
                'returncode': 0,
                'stdout': 'synthetic full diff\n',
                'stderr': '',
            })()

        with path_workspace() as tmp:
            with patch.object(ar.subprocess, 'run', side_effect=fake_svn):
                r = ar.execute_tool('run_svn',
                                    {'operation': 'commit', 'message': 'm', 'paths': ''},
                                    tmp, confirm_cb=confirm_cb)
        self.assertTrue(confirmed, 'SVN commit 未触发确认回调')
        self.assertFalse(r['ok'])
        self.assertIn('取消', r.get('error', ''))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:2], ['svn', 'diff'])

    def test_svn_readonly_does_not_require_confirm(self):
        """SVN status/diff 为只读，不应触发确认回调。"""
        confirmed = []

        def confirm_cb(title, content):
            confirmed.append(title)
            return True

        with path_workspace() as tmp:
            # 本机无 svn 时 status 会失败（ok=False），但保证不触发确认
            r = ar.execute_tool('run_svn', {'operation': 'status'}, tmp, confirm_cb=confirm_cb)
        self.assertEqual(confirmed, [])
        self.assertIn('ok', r)

    def test_svn_failure_returns_ok_false(self):
        """svn 命令返回非 0（如本机无 svn）/stderr 时，不得伪装成功。"""
        with path_workspace() as tmp:
            r = ar._run_svn_impl('status', tmp)
        self.assertFalse(r.get('ok'))

    def test_run_test_blocks_dangerous_args(self):
        with path_workspace() as tmp:
            for bad in ('-p pytest_asyncio', '--pyargs pkg', 'tests/a.py; rm -rf /',
                        '--collect-only', '--pdb'):
                r = ar._run_test_impl(bad, tmp)
                self.assertIn('禁止', r.get('error', ''), msg=f'未拦截: {bad}')

    def test_validate_path_blocks_escape(self):
        with path_workspace() as tmp:
            ok, resolved, err = ar.validate_path('../secret.txt', tmp)
            self.assertFalse(ok)
            self.assertIn('越界', err)
            ok2, _, _ = ar.validate_path('a.py', tmp)
            self.assertTrue(ok2)


class PlanConfirmTests(unittest.TestCase):
    def test_plan_confirm_reject_aborts_tool_execution(self):
        """plan_confirm=True 且用户拒绝计划时，应终止且不执行任何工具。"""
        adapter = ScriptedModelAdapter([
            _tool_turn('write-x', 'write_file', path='x.txt', content='hi'),
        ])

        def confirm_cb(title, content):
            return False  # 拒绝计划

        with path_workspace() as tmp:
            final, _, tcs = ar.run_agent_loop(
                '创建 x.txt', tmp, {}, [], [], plan_confirm=True,
                confirm_cb=confirm_cb, model_adapter=adapter,
            )
            self.assertIn('取消执行计划', final)
            self.assertEqual(len(tcs), 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, 'x.txt')))

    def test_plan_confirm_accept_proceeds(self):
        """plan_confirm=True 且用户接受计划时，继续执行工具。"""
        adapter = ScriptedModelAdapter([
            _tool_turn('write-y', 'write_file', path='y.txt', content='hi'),
            AgentTurn(text='文件已创建。'),
        ])
        confirmations = []

        def confirm_cb(title, content):
            confirmations.append((title, content))
            return True  # 接受计划

        with path_workspace() as tmp:
            final, _, tcs = ar.run_agent_loop(
                '创建 y.txt', tmp, {}, [], [], plan_confirm=True,
                confirm_cb=confirm_cb, model_adapter=adapter,
            )
            self.assertEqual(len(tcs), 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, 'y.txt')))
        self.assertEqual(final, '文件已创建。')
        self.assertEqual([title for title, _ in confirmations], ['确认执行计划', '确认写入文件: y.txt'])


class ToolCallNormalizeTests(unittest.TestCase):
    def test_tool_plus_args(self):
        calls = ar.parse_tool_calls('{"tool": "list_dir", "args": {"path": ""}}')
        self.assertEqual(calls, [{'tool': 'list_dir', 'args': {'path': ''}}])

    def test_name_plus_parameters_incident_format(self):
        raw = '{"name": "list_dir", "parameters": {"path": ""}}'
        calls = ar.parse_tool_calls(raw)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['tool'], 'list_dir')
        self.assertEqual(calls[0]['args'], {'path': ''})

    def test_parameters_json_string(self):
        raw = '{"name": "read_file", "parameters": "{\\"path\\":\\"main.py\\"}"}'
        calls = ar.parse_tool_calls(raw)
        self.assertEqual(calls[0]['tool'], 'read_file')
        self.assertEqual(calls[0]['args']['path'], 'main.py')

    def test_invalid_parameters_json(self):
        raw = '{"name": "read_file", "parameters": "{not json"}'
        calls = ar.parse_tool_calls(raw)
        self.assertEqual(calls[0]['tool'], 'read_file')
        self.assertEqual(calls[0]['error'], ar.INVALID_TOOL_ARGUMENTS)

    def test_tool_calls_envelope(self):
        raw = json.dumps({
            'tool_calls': [
                {'name': 'read_file', 'parameters': {'path': 'main.py'}},
            ]
        })
        calls = ar.parse_tool_calls(raw)
        self.assertEqual(calls[0]['tool'], 'read_file')
        self.assertEqual(calls[0]['args']['path'], 'main.py')

    def test_unknown_tool_execute(self):
        with path_workspace() as tmp:
            r = ar.execute_tool('delete_everything', {}, tmp)
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], ar.UNKNOWN_TOOL)


class BoundProjectAndLoopTests(unittest.TestCase):
    def test_incident_name_parameters_executes_list_dir(self):
        adapter = ScriptedModelAdapter([
            _tool_turn('list-1', 'list_dir', path=''),
            AgentTurn(text='目录已看完'),
        ])

        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'main.py'), 'w', encoding='utf-8') as stream:
                stream.write('print("hi")\n')
            os.makedirs(os.path.join(tmp, 'src'), exist_ok=True)
            with open(os.path.join(tmp, 'src', 'demo.py'), 'w', encoding='utf-8') as stream:
                stream.write('demo = 1\n')
            final, msgs, tcs = ar.run_agent_loop(
                '看看有哪些 Python 文件', tmp, {}, [], [], model_adapter=adapter,
            )
        self.assertEqual(final, '目录已看完')
        self.assertEqual(tcs[0]['tool'], 'list_dir')
        payload = json.loads(tcs[0]['result'])
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['success'])
        names = [e['name'] for e in payload['data']]
        self.assertIn('main.py', names)
        self.assertIn('src', names)
        assistant_calls = [m for m in msgs if m.get('role') == 'assistant' and m.get('tool_calls')]
        self.assertEqual(assistant_calls[0]['tool_calls'][0]['id'], 'list-1')

    def test_multi_round_list_dir_then_read_file(self):
        executed = []
        orig = ar.execute_tool

        def wrapped(tool, args, workspace_dir, confirm_cb=None, cancel_cb=None):
            executed.append(tool)
            return orig(tool, args, workspace_dir, confirm_cb=confirm_cb, cancel_cb=cancel_cb)

        adapter = ScriptedModelAdapter([
            _tool_turn('list-1', 'list_dir', path=''),
            _tool_turn('read-2', 'read_file', path='main.py'),
            AgentTurn(text='我已经读取 main.py，项目入口……'),
        ])

        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'main.py'), 'w', encoding='utf-8') as stream:
                stream.write('print("entry")\n')
            os.makedirs(os.path.join(tmp, 'src'), exist_ok=True)
            with open(os.path.join(tmp, 'src', 'demo.py'), 'w', encoding='utf-8') as stream:
                stream.write('demo = True\n')
            with patch.object(ar, 'execute_tool', new=wrapped):
                final, _, tcs = ar.run_agent_loop(
                    '分析项目', tmp, {}, [], [], model_adapter=adapter,
                )
        self.assertEqual(executed, ['list_dir', 'read_file'])
        self.assertEqual([c['tool'] for c in tcs], ['list_dir', 'read_file'])
        self.assertIn('main.py', final)
        read_payload = json.loads(tcs[1]['result'])
        self.assertIn('print("entry")', read_payload['data'])
        self.assertEqual(adapter.requests[1].messages[-2]['role'], 'assistant')
        self.assertEqual(adapter.requests[1].messages[-1]['tool_call_id'], 'list-1')

    def test_bound_project_list_read_search(self):
        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'main.py'), 'w', encoding='utf-8') as stream:
                stream.write('from src.demo import x\n')
            os.makedirs(os.path.join(tmp, 'src'), exist_ok=True)
            with open(os.path.join(tmp, 'src', 'demo.py'), 'w', encoding='utf-8') as stream:
                stream.write('demo = 1\n')
            listed = ar.execute_tool('list_dir', {'path': ''}, tmp)
            read = ar.execute_tool('read_file', {'path': 'main.py'}, tmp)
            found = ar.execute_tool('search_code', {'pattern': 'demo'}, tmp)
        self.assertTrue(listed['ok'])
        self.assertTrue(read['ok'])
        self.assertIn('from src.demo', read['content'])
        self.assertTrue(found['ok'])
        files = {m['file'].replace('\\', '/') for m in found['matches']}
        self.assertTrue(files)
        self.assertTrue(all(not p.startswith('..') for p in files))
        self.assertTrue(any('demo' in p for p in files))

    def test_path_escape_rejected(self):
        with path_workspace() as tmp:
            outside = os.path.join(os.path.dirname(tmp), 'outside.txt')
            with open(outside, 'w', encoding='utf-8') as stream:
                stream.write('secret')
            rel = ar.execute_tool('read_file', {'path': '../outside.txt'}, tmp)
            abs_path = ar.execute_tool('read_file', {'path': outside}, tmp)
            self.assertFalse(rel['ok'])
            self.assertFalse(abs_path['ok'])
            try:
                link = os.path.join(tmp, 'escape_link')
                os.symlink(os.path.dirname(tmp), link)
                escaped = ar.execute_tool('read_file', {'path': 'escape_link/outside.txt'}, tmp)
                self.assertFalse(escaped['ok'])
            except OSError:
                pass
            finally:
                if os.path.exists(outside):
                    os.remove(outside)


class AgentIncidentAndReActLoopTests(unittest.TestCase):
    """Legacy parser helpers plus shared native-tool loop behavior."""

    def test_a1_a2_flat_incident_tool_format_normalized(self):
        raw1 = '{"name":"list_dir","path":""}'
        calls1 = ar.parse_tool_calls(raw1)
        self.assertEqual(len(calls1), 1)
        self.assertEqual(calls1[0]['tool'], 'list_dir')
        self.assertEqual(calls1[0]['args'], {'path': ''})

        raw2 = '{"name":"read_file","path":"src/main/java/App.java","comment":"ignore this"}'
        calls2 = ar.parse_tool_calls(raw2)
        self.assertEqual(len(calls2), 1)
        self.assertEqual(calls2[0]['tool'], 'read_file')
        self.assertEqual(calls2[0]['args'], {'path': 'src/main/java/App.java'})

    def test_a3_arbitrary_json_not_misidentified_as_tool_call(self):
        self.assertEqual(ar.parse_tool_calls('{"name":"Alice","path":""}'), [])

    def test_a4_a5_a6_a7_java_project_multi_round_react_loop(self):
        progress_events = []
        adapter = ScriptedModelAdapter([
            _tool_turn('list-1', 'list_dir', path=''),
            _tool_turn('list-2', 'list_dir', path='src/main/java'),
            _tool_turn('read-3', 'read_file', path='src/main/java/com/acme/App.java'),
            AgentTurn(text='我已经读取项目，入口类为 App.java，主方法已就绪。'),
        ])

        with path_workspace() as tmp:
            os.makedirs(os.path.join(tmp, 'src', 'main', 'java', 'com', 'acme'), exist_ok=True)
            with open(os.path.join(tmp, 'pom.xml'), 'w', encoding='utf-8') as stream:
                stream.write('<project></project>')
            with open(os.path.join(tmp, 'src', 'main', 'java', 'com', 'acme', 'App.java'), 'w', encoding='utf-8') as stream:
                stream.write('package com.acme;\npublic class App { public static void main(String[] args) {} }\n')

            final, messages, calls = ar.run_agent_loop(
                '帮我读取下项目代码', tmp, {'id': 'synthetic-model'}, [], [],
                progress_cb=lambda kind, body: progress_events.append((kind, body)),
                model_adapter=adapter,
            )

        self.assertIn('入口类为 App.java', final)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(adapter.requests), 4)
        round2_tools = [item for item in adapter.requests[1].messages if item.get('role') == 'tool']
        self.assertEqual(len(round2_tools), 1)
        self.assertIn('pom.xml', round2_tools[0]['content'])
        round4_tools = [item for item in adapter.requests[3].messages if item.get('role') == 'tool']
        self.assertEqual(len(round4_tools), 3)
        self.assertIn('public class App', round4_tools[2]['content'])
        self.assertEqual([item['tool_call_id'] for item in round4_tools], ['list-1', 'list-2', 'read-3'])
        self.assertEqual(
            [item.get('content') for item in messages if item.get('role') == 'assistant' and item.get('content')],
            ['我已经读取项目，入口类为 App.java，主方法已就绪。'],
        )
        self.assertTrue(any(kind == 'assistant_delta' and '入口类为 App.java' in body
                            for kind, body in progress_events))
        self.assertEqual(
            [body for kind, body in progress_events if kind == 'status' and body.startswith('turn_started:')],
            ['turn_started:0', 'turn_started:1', 'turn_started:2', 'turn_started:3'],
        )

    def test_a12_repeated_identical_tool_loop_broken(self):
        adapter = ScriptedModelAdapter([
            _tool_turn(f'list-{index}', 'list_dir', path='') for index in range(1, 6)
        ])
        with path_workspace() as tmp:
            final, _messages, calls = ar.run_agent_loop(
                '测试重复工具', tmp, {'id': 'synthetic-model'}, [], [],
                model_adapter=adapter,
            )
        self.assertIn('模型重复请求相同工具调用', final)
        self.assertIn('已停止以避免无效循环', final)
        # 前两次真实执行，第三、四次只回提醒，第五次停止
        self.assertEqual(len(adapter.requests), 5)
        reminders = [item for item in adapter.requests[3].messages if item.get('role') == 'tool']
        self.assertIn('REPEATED_CALL', reminders[-1]['content'])
        self.assertIn('上一条工具消息', reminders[-1]['content'])

    def test_a12c_native_model_text_json_tool_call_is_executed(self):
        adapter = ScriptedModelAdapter([
            AgentTurn(text='```json\n[\n  {"name": "list_dir", "path": ""}\n]\n```'),
            AgentTurn(text='已列出目录。'),
        ])
        adapter.capability = 'native_tools'
        with path_workspace() as tmp:
            final, _messages, calls = ar.run_agent_loop(
                '读取项目', tmp, {'id': 'synthetic-model'}, [], [],
                model_adapter=adapter,
            )
        self.assertEqual(final, '已列出目录。')
        self.assertEqual(len(adapter.requests), 2)
        tool_messages = [item for item in adapter.requests[1].messages if item.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        self.assertIn('"tool":"list_dir"', tool_messages[0]['content'])

    def test_a12d_text_with_prose_or_unknown_tool_is_not_executed(self):
        self.assertEqual(ar.recover_text_tool_calls('先看看目录：[{"name": "list_dir", "path": ""}]'), [])
        self.assertEqual(ar.recover_text_tool_calls('[{"name": "rm_rf", "path": "/"}]'), [])
        self.assertEqual(ar.recover_text_tool_calls('[{"name": "list_dir", "path": ""}, {"name": "rm_rf"}]'), [])
        self.assertEqual(
            ar.recover_text_tool_calls('{"name": "read_file", "arguments": {"path": "a.py"}}'),
            [{'tool': 'read_file', 'args': {'path': 'a.py'}}],
        )

    def test_a12f_budget_stop_message_is_plain_language(self):
        from types import SimpleNamespace

        result = SimpleNamespace(status='budget_exhausted', error='input_chars', final_text='')
        text = ar._safe_status_text(result)
        self.assertNotIn('input_chars', text)
        self.assertIn('继续', text)
        self.assertGreaterEqual(ar.MAX_RUN_INPUT_CHARS, 1_000_000)

    def test_a12e_marked_text_tool_calls_are_recovered(self):
        self.assertEqual(
            ar.recover_text_tool_calls('我先列出工作区根目录结构：\n[调用工具 list_dir: {"path": ""}]'),
            [{'tool': 'list_dir', 'args': {'path': ''}}],
        )
        self.assertEqual(
            ar.recover_text_tool_calls(
                '先读说明。<tool_call>\n{"name": "read_file", "arguments": {"path": "README.md"}}\n</tool_call>'
            ),
            [{'tool': 'read_file', 'args': {'path': 'README.md'}}],
        )
        # 未知工具或参数损坏：整体不执行
        self.assertEqual(ar.recover_text_tool_calls('[调用工具 rm_rf: {"path": "/"}]'), [])
        self.assertEqual(ar.recover_text_tool_calls('[调用工具 list_dir: {"path": ]'), [])

    def test_a12b_repeat_reminder_lets_model_recover(self):
        adapter = ScriptedModelAdapter([
            _tool_turn('list-1', 'list_dir', path=''),
            _tool_turn('list-2', 'list_dir', path=''),
            _tool_turn('list-3', 'list_dir', path=''),
            AgentTurn(text='项目只有一个说明文件。'),
        ])
        with path_workspace() as tmp:
            final, _messages, _calls = ar.run_agent_loop(
                '读取项目', tmp, {'id': 'synthetic-model'}, [], [],
                model_adapter=adapter,
            )
        self.assertEqual(final, '项目只有一个说明文件。')
        tool_messages = [item for item in adapter.requests[1].messages if item.get('role') == 'tool']
        # 工具结果只带有值字段，模型能直接看到 data
        self.assertNotIn('null', tool_messages[0]['content'])
        self.assertIn('"tool":"list_dir"', tool_messages[0]['content'])

    def test_a13_cancel_stops_subsequent_tool_execution(self):
        execution_count = 0
        token = CancellationToken()
        adapter = ScriptedModelAdapter([
            _tool_turn('list-1', 'list_dir', path=''),
            _tool_turn('list-2', 'list_dir', path=''),
        ])
        original_exec = ar.execute_tool

        def tracking_exec(*args, **kwargs):
            nonlocal execution_count
            execution_count += 1
            result = original_exec(*args, **kwargs)
            token.cancel('ui stop')
            return result

        with path_workspace() as tmp:
            with patch.object(ar, 'execute_tool', new=tracking_exec):
                final, _messages, _calls = ar.run_agent_loop(
                    '测试取消', tmp, {'id': 'synthetic-model'}, [], [],
                    model_adapter=adapter, cancellation=token,
                )
        self.assertIn('已由用户手动停止', final)
        self.assertEqual(execution_count, 1)
        self.assertEqual(len(adapter.requests), 1)

def path_workspace():

    """返回一个可用的临时工作目录上下文管理器。"""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        tmp = tempfile.mkdtemp()
        try:
            yield tmp
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    return _ctx()


if __name__ == '__main__':
    unittest.main()
