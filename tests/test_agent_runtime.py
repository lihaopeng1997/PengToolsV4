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
