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


class ModelCallTests(unittest.TestCase):
    """核心：模型调用参数必须匹配 intranet_llm.chat_completions(messages, cfg, model_config_id)。"""

    def test_run_agent_loop_calls_chat_with_cfg_not_kwargs(self):
        """确保用 cfg=model_cfg 调用，而非原先的 model/base_url/api_key/stream 关键字。
        旧代码会抛 TypeError；此处断言调用参数名不含被移除的关键字。
        """
        captured = {}

        def fake_chat(messages, cfg=None, model_config_id=None):
            captured['cfg'] = cfg
            return '任务完成'  # 直接给最终答案，不触发工具

        with path_workspace() as tmp:
            with patch.object(ar, 'chat_completions', new=fake_chat):
                model_cfg = {'model': 'qwen3.6', 'base_url': 'http://10.0.0.1/v1', 'enabled': True}
                final, _, _ = ar.run_agent_loop('请你读下 a.py', tmp, model_cfg, [], [])

        self.assertEqual(final, '任务完成')
        self.assertIs(captured.get('cfg'), model_cfg)

    def test_model_failure_returns_real_error_not_round_limit(self):
        """模型抛异常时，应返回真实错误信息，而不被「最大工具调用轮次」覆盖。"""
        def fake_chat(messages, cfg=None, model_config_id=None):
            raise RuntimeError('boom connection refused')

        with path_workspace() as tmp:
            with patch.object(ar, 'chat_completions', new=fake_chat):
                final, msgs, _ = ar.run_agent_loop('任务', tmp, {}, [], [])

        self.assertIn('模型调用失败', final)
        self.assertIn('boom', final)
        self.assertNotIn('已达到最大工具调用轮次', final)

    def test_tool_execution_roundtrip(self):
        """模型返回 read_file 工具调用，应执行成功并写回 tool 消息。"""
        responses = iter([
            '{"tool": "read_file", "args": {"path": "a.py"}}',
            '搞定，看完了',
        ])

        def fake_chat(messages, cfg=None, model_config_id=None):
            return next(responses)

        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'a.py'), 'w', encoding='utf-8') as stream:
                stream.write('print(1)')
            with patch.object(ar, 'chat_completions', new=fake_chat):
                final, msgs, tcs = ar.run_agent_loop('读 a.py', tmp, {}, [], [])

        self.assertEqual(final, '搞定，看完了')
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0]['tool'], 'read_file')
        # result 是脱敏后的 JSON 字符串
        self.assertEqual(json.loads(tcs[0]['result'])['ok'], True)


class ToolDispatchTests(unittest.TestCase):
    def test_svn_commit_requires_confirm(self):
        """SVN commit 必须经 confirm_cb 二次确认；取消则 ok=False，不执行。"""
        confirmed = []

        def confirm_cb(title, content):
            confirmed.append(title)
            return False  # 拒绝

        with path_workspace() as tmp:
            r = ar.execute_tool('run_svn',
                                {'operation': 'commit', 'message': 'm', 'paths': ''},
                                tmp, confirm_cb=confirm_cb)
        self.assertTrue(confirmed, 'SVN commit 未触发确认回调')
        self.assertFalse(r['ok'])
        self.assertIn('取消', r.get('error', ''))

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

        def fake_chat(messages, cfg=None, model_config_id=None):
            return '{"tool": "write_file", "args": {"path": "x.txt", "content": "hi"}}'

        def confirm_cb(title, content):
            return False  # 拒绝计划

        with path_workspace() as tmp:
            with patch.object(ar, 'chat_completions', new=fake_chat):
                final, _, tcs = ar.run_agent_loop('创建 x.txt', tmp, {},
                                                  [], [], plan_confirm=True, confirm_cb=confirm_cb)
            self.assertIn('取消执行计划', final)
            self.assertEqual(len(tcs), 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, 'x.txt')))

    def test_plan_confirm_accept_proceeds(self):
        """plan_confirm=True 且用户接受计划时，继续执行工具。"""

        def fake_chat(messages, cfg=None, model_config_id=None):
            return '{"tool": "write_file", "args": {"path": "y.txt", "content": "hi"}}'

        def confirm_cb(title, content):
            return True  # 接受计划

        with path_workspace() as tmp:
            with patch.object(ar, 'chat_completions', new=fake_chat):
                # 第二轮模型仍返回同一调用 → 解析出工具并执行；此处只看首轮计划确认通过后工具已写盘
                ar.MAX_TOOL_ROUNDS = 1
                final, _, tcs = ar.run_agent_loop('创建 y.txt', tmp, {},
                                                  [], [], plan_confirm=True, confirm_cb=confirm_cb)
            self.assertEqual(len(tcs), 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, 'y.txt')))


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
        responses = iter([
            '{"name": "list_dir", "parameters": {"path": ""}}',
            '目录已看完',
        ])

        def fake_chat(messages, cfg=None, model_config_id=None):
            return next(responses)

        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'main.py'), 'w', encoding='utf-8') as stream:
                stream.write('print("hi")\n')
            os.makedirs(os.path.join(tmp, 'src'), exist_ok=True)
            with open(os.path.join(tmp, 'src', 'demo.py'), 'w', encoding='utf-8') as stream:
                stream.write('demo = 1\n')
            with patch.object(ar, 'chat_completions', new=fake_chat):
                final, msgs, tcs = ar.run_agent_loop('看看有哪些 Python 文件', tmp, {}, [], [])
        self.assertEqual(final, '目录已看完')
        self.assertEqual(tcs[0]['tool'], 'list_dir')
        payload = json.loads(tcs[0]['result'])
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['success'])
        names = [e['name'] for e in payload['data']]
        self.assertIn('main.py', names)
        self.assertIn('src', names)
        assistant_json = [m for m in msgs if m.get('role') == 'assistant' and '"name": "list_dir"' in (m.get('content') or '')]
        self.assertEqual(assistant_json, [])

    def test_multi_round_list_dir_then_read_file(self):
        executed = []
        orig = ar.execute_tool

        def wrapped(tool, args, workspace_dir, confirm_cb=None):
            executed.append(tool)
            return orig(tool, args, workspace_dir, confirm_cb=confirm_cb)

        responses = iter([
            '{"name": "list_dir", "parameters": {"path": ""}}',
            '{"name": "read_file", "parameters": {"path": "main.py"}}',
            '我已经读取 main.py，项目入口……',
        ])

        def fake_chat(messages, cfg=None, model_config_id=None):
            return next(responses)

        with path_workspace() as tmp:
            with open(os.path.join(tmp, 'main.py'), 'w', encoding='utf-8') as stream:
                stream.write('print("entry")\n')
            os.makedirs(os.path.join(tmp, 'src'), exist_ok=True)
            with open(os.path.join(tmp, 'src', 'demo.py'), 'w', encoding='utf-8') as stream:
                stream.write('demo = True\n')
            with patch.object(ar, 'execute_tool', new=wrapped):
                with patch.object(ar, 'chat_completions', new=fake_chat):
                    final, _, tcs = ar.run_agent_loop('分析项目', tmp, {}, [], [])
        self.assertEqual(executed, ['list_dir', 'read_file'])
        self.assertEqual([c['tool'] for c in tcs], ['list_dir', 'read_file'])
        self.assertIn('main.py', final)
        read_payload = json.loads(tcs[1]['result'])
        self.assertIn('print("entry")', read_payload['data'])

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
    """测试 Round 4-B Agent 工具解析 incident 格式、ReAct 观察回路、熔断与取消。"""

    def test_a1_a2_flat_incident_tool_format_normalized(self):
        """A1, A2. 支持扁平 incident 格式 {"name":"list_dir","path":""} 及 read_file。"""
        # A1. list_dir
        raw1 = '{"name":"list_dir","path":""}'
        calls1 = ar.parse_tool_calls(raw1)
        self.assertEqual(len(calls1), 1)
        self.assertEqual(calls1[0]['tool'], 'list_dir')
        self.assertEqual(calls1[0]['args'], {'path': ''})

        # A2. read_file with extra parameters filtered by schema properties
        raw2 = '{"name":"read_file","path":"src/main/java/App.java","comment":"ignore this"}'
        calls2 = ar.parse_tool_calls(raw2)
        self.assertEqual(len(calls2), 1)
        self.assertEqual(calls2[0]['tool'], 'read_file')
        self.assertEqual(calls2[0]['args'], {'path': 'src/main/java/App.java'})

    def test_a3_arbitrary_json_not_misidentified_as_tool_call(self):
        """A3. 普通 JSON 结构如 {"name":"Alice","path":""} 绝不当成工具调用。"""
        raw = '{"name":"Alice","path":""}'
        calls = ar.parse_tool_calls(raw)
        self.assertEqual(calls, [])

    def test_a4_a5_a6_a7_java_project_multi_round_react_loop(self):
        """A4, A5, A6, A7. 模拟 Java 项目完整 ReAct 回路：
        MODEL -> list_dir -> OBSERVATION -> MODEL -> read_file -> OBSERVATION -> MODEL -> final answer。
        断言：
        - 工具执行结果真实进入下一轮 messages (role=tool)
        - 原始工具 JSON 绝不作为最终 assistant 回复
        - UI/finished 最终回答恰好 1 次
        """
        captured_messages = []
        progress_events = []

        fake_responses = iter([
            '{"name":"list_dir","path":""}',
            '{"name":"list_dir","path":"src/main/java"}',
            '{"name":"read_file","path":"src/main/java/com/acme/App.java"}',
            '我已经读取项目，入口类为 App.java，主方法已就绪。',
        ])

        def fake_chat(messages, cfg=None, model_config_id=None):
            captured_messages.append(list(messages))
            return next(fake_responses)

        def progress_cb(role, content):
            progress_events.append((role, content))

        with path_workspace() as tmp:
            # 建立 Java 工程目录树
            os.makedirs(os.path.join(tmp, 'src', 'main', 'java', 'com', 'acme'), exist_ok=True)
            with open(os.path.join(tmp, 'pom.xml'), 'w', encoding='utf-8') as f:
                f.write('<project></project>')
            with open(os.path.join(tmp, 'src', 'main', 'java', 'com', 'acme', 'App.java'), 'w', encoding='utf-8') as f:
                f.write('package com.acme;\npublic class App { public static void main(String[] args) {} }\n')

            with patch.object(ar, 'chat_completions', new=fake_chat):
                final_answer, msgs, tcs = ar.run_agent_loop(
                    user_message='帮我读取下项目代码',
                    workspace_dir=tmp,
                    model_cfg={'enabled': True},
                    messages=[],
                    tool_calls=[],
                    progress_cb=progress_cb,
                )

        # 验证最终回答内容与唯一性
        self.assertIn('入口类为 App.java', final_answer)
        self.assertEqual(len(tcs), 3)

        # A5. 验证工具结果真实进入后续模型的上下文 (role='tool')
        self.assertEqual(len(captured_messages), 4)
        # Round 2 调用的 messages 应包含 Round 1 的 tool observation
        round2_msgs = captured_messages[1]
        tool_in_r2 = [m for m in round2_msgs if m.get('role') == 'tool']
        self.assertEqual(len(tool_in_r2), 1)
        self.assertIn('pom.xml', tool_in_r2[0]['content'])

        # Round 4 调用的 messages 应包含全部 3 次 tool observations
        round4_msgs = captured_messages[3]
        tool_in_r4 = [m for m in round4_msgs if m.get('role') == 'tool']
        self.assertEqual(len(tool_in_r4), 3)
        self.assertIn('public class App', tool_in_r4[2]['content'])

        # A6. 原始工具 JSON 绝不作为 assistant final 回复
        self.assertNotIn('{"name":"list_dir"', final_answer)

        # A7. 进度回调中不包含 assistant 最终回答（杜绝与 _on_agent_done 双写）
        assistant_progress = [content for role, content in progress_events if role == 'assistant']
        self.assertEqual(assistant_progress, [])

    def test_a12_repeated_identical_tool_loop_broken(self):
        """A12. 模型连续重复调用相同只读工具（如连续 3 次 list_dir path=""）必须被熔断停止。"""
        fake_responses = iter([
            '{"name":"list_dir","path":""}',
            '{"name":"list_dir","path":""}',
            '{"name":"list_dir","path":""}',
            '{"name":"list_dir","path":""}',
        ])

        def fake_chat(messages, cfg=None, model_config_id=None):
            return next(fake_responses)

        with path_workspace() as tmp:
            with patch.object(ar, 'chat_completions', new=fake_chat):
                final_answer, msgs, tcs = ar.run_agent_loop(
                    user_message='测试重复工具',
                    workspace_dir=tmp,
                    model_cfg={'enabled': True},
                    messages=[],
                    tool_calls=[],
                )

        self.assertIn('模型重复请求相同工具调用', final_answer)
        self.assertIn('已停止以避免无效循环', final_answer)
        self.assertLessEqual(len(tcs), 3)

    def test_a13_cancel_stops_subsequent_tool_execution(self):
        """A13. cancel_cb 触发后，立即中断循环，后续工具绝不被执行。"""
        execution_count = 0

        def fake_chat(messages, cfg=None, model_config_id=None):
            return '{"name":"list_dir","path":""}'

        is_cancelled = False

        def cancel_gate():
            return is_cancelled

        original_exec = ar.execute_tool

        def tracking_exec(*args, **kwargs):
            nonlocal execution_count, is_cancelled
            execution_count += 1
            # 首次执行后取消
            is_cancelled = True
            return original_exec(*args, **kwargs)

        with path_workspace() as tmp:
            with patch.object(ar, 'chat_completions', new=fake_chat):
                with patch.object(ar, 'execute_tool', new=tracking_exec):
                    final_answer, msgs, tcs = ar.run_agent_loop(
                        user_message='测试取消',
                        workspace_dir=tmp,
                        model_cfg={'enabled': True},
                        messages=[],
                        tool_calls=[],
                        cancel_cb=cancel_gate,
                    )

        self.assertIn('已由用户手动停止', final_answer)
        self.assertEqual(execution_count, 1)


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
