"""Isolated log privacy checks that never import/boot PengTools."""
import ast
import hashlib
import json
import re
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests' / 'ssh_session')]
import _isolation
PASS, FAIL = [], []


def check(name, condition):
    (PASS if condition else FAIL).append(name)
    print(f'{("PASS" if condition else "FAIL")}  {name}')


def tree(path):
    source = (ROOT / path).read_text(encoding='utf-8')
    compile(source, path, 'exec')
    return ast.parse(source)


def method(module, owner, name):
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == owner)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def load_nodes(*nodes, **namespace):
    exec(compile(ast.Module(body=list(nodes), type_ignores=[]), '<isolated log privacy>', 'exec'), namespace)
    return namespace


def definition(module, name):
    return next(node for node in module.body if getattr(node, 'name', None) == name)


logging_tree = tree('tools/app_logging.py')
events_tree = tree('tools/log_analysis/events.py')
runtime_tree = tree('tools/log_analysis/runtime.py')
service_tree = tree('tools/log_analysis/service.py')
workspace_tree = tree('ui/log_analysis/workspace.py')
panel_tree = tree('panels/interface_debug_panel.py')
mask = load_nodes(definition(logging_tree, 'mask_private_ipv4'))['mask_private_ipv4']

raw = '连接10.1.2.3:443 172.16.2.4 172.31.9.8 192.168.7.6 127.0.0.1 172.32.1.2 203.0.113.20'
expected = '连接10.x.x.x:443 172.x.x.x 172.x.x.x 192.x.x.x 127.0.0.1 172.32.1.2 203.0.113.20'
check('同一 IPv4 策略覆盖三个私网段并保留公网与回环', mask(raw) == expected)

start = next(node for node in events_tree.body if isinstance(node, ast.Assign) and
             ast.unparse(node.targets[0]) == 'START')
events = load_nodes(start, definition(events_tree, 'Redactor'), definition(events_tree, 'EventBuffer'),
                    deque=deque, re=re, time=time, uuid=uuid, hashlib=hashlib, mask_private_ipv4=mask)
redactor = events['Redactor']
check('默认采集脱敏和显式原文分离',
      events['EventBuffer']().redactor.line(raw) == expected
      and events['EventBuffer'](redact=False).redactor.line(raw) == raw)

setup_src = ast.unparse(method(workspace_tree, 'LogAnalysisWorkspace', '_setup'))
start_src = ast.unparse(method(workspace_tree, 'LogAnalysisWorkspace', 'start'))
check('日志页原文默认关闭，单次采集需确认',
      'self.show_raw.setChecked(False)' in setup_src
      and 'is_intranet_open' not in setup_src
      and 'self.show_raw.setChecked(False)' in start_src
      and 'if not confirm_action(' in start_src
      and start_src.index('if not confirm_action(') < start_src.index('self.service.add(')
      and 'self._raw_task_ids.add(task.id)' in start_src)
check('模式开关无法解除强制清洗',
      'self.scrub_model.setEnabled(False)' in setup_src
      and 'self.scrubber = Redactor()' in ast.unparse(method(service_tree, 'LogService', '__init__'))
      and 'self.scrubber.enabled = True' in ast.unparse(method(service_tree, 'LogService', 'set_scrub_for_model')))

display = load_nodes(method(workspace_tree, 'LogAnalysisWorkspace', '_display_evidence'),
                     mask_private_ipv4=mask, Redactor=redactor)['_display_evidence']
ws = SimpleNamespace(selected_id='current', _raw_task_ids=set(), snapshots={'current': {'redacted': False}})
check('旧原文缓存默认遮蔽，授权任务仍可看原文',
      display(ws, raw + '\npassword=synthetic') == expected + '\n[敏感字段已隐藏]')
ws._raw_task_ids.add('current')
check('任务授权允许原文显示', display(ws, raw) == raw)
consent_src = ast.unparse(method(workspace_tree, 'LogAnalysisWorkspace', 'send'))
check('日志模型确认仍在排队之前', consent_src.index('model_consent.isChecked()') < consent_src.index('self.service.enqueue('))

runtime_names = {'_IPV4_STREAM_TAIL', '_TOOL_MARK'}
runtime_constants = [node for node in runtime_tree.body if isinstance(node, ast.Assign)
                     and ast.unparse(node.targets[0]) in runtime_names]
runtime = load_nodes(*runtime_constants,
                     *(definition(runtime_tree, name) for name in (
                         '_clean_model_value', '_split_tool_hold', '_is_tool_envelope', 'TextCalls')),
                     json=json, re=re, replace=replace, Redactor=redactor, mask_private_ipv4=mask)


@dataclass(frozen=True)
class Request:
    system_prompt: str
    messages: tuple


messages = (
    {'role': 'user', 'content': 'password=synthetic\n' + raw},
    {'role': 'assistant', 'content': '历史 ' + raw, 'tool_calls': [
        {'id': 'demo-id', 'function': {'name': 'search_log', 'arguments': '{"keyword":"10.8.7.6"}'}}]},
    {'role': 'tool', 'tool_call_id': 'demo-id',
     'content': json.dumps({'evidence': raw, 'token': 'synthetic', 'query': 'requestBody=x\nsecret continuation'})},
)
request = Request('目标 172.20.1.9', messages)
transmitted = []
displayed = []
chunks = (
    ('reasoning_delta', '连接19'), ('reasoning_delta', '2.168.1.2'),
    ('text_delta', '证据1'), ('text_delta', '0.9.'), ('text_delta', '8.7。'),
)


def complete(outbound, *, cancellation, on_delta):
    transmitted.append(outbound)
    for kind, text in chunks:
        on_delta(SimpleNamespace(kind=kind, text=text))
    return SimpleNamespace(tool_calls=(), text='完成')


runtime['TextCalls'](SimpleNamespace(complete_turn=complete), delta_sink=lambda *value: displayed.append(value)).complete_turn(
    request, cancellation=None, on_delta=lambda delta: None)
sent = transmitted[0]
tool = json.loads(sent.messages[2]['content'])
check('每轮请求清洗系统范围、提问、历史、工具参数和工具结果副本',
      sent.system_prompt == '目标 172.x.x.x'
      and expected in sent.messages[0]['content'] and 'synthetic' not in sent.messages[0]['content']
      and expected in sent.messages[1]['content']
      and json.loads(sent.messages[1]['tool_calls'][0]['function']['arguments']) == {'keyword': '10.x.x.x'}
      and tool['evidence'] == expected and tool['token'] == '[敏感字段已隐藏]'
      and 'secret continuation' not in tool['query']
      and messages[2]['content'].find('synthetic') >= 0
      and sent.messages[2]['tool_call_id'] == 'demo-id')
check('拆分到多个增量的 IPv4 在推理和回答均遮蔽',
      ''.join(text for kind, text in displayed if kind == 'model_reasoning_delta') == '连接192.x.x.x'
      and ''.join(text for kind, text in displayed if kind == 'model_text_delta') == '证据10.x.x.x。')

send_src = ast.unparse(method(panel_tree, 'InterfaceDebugPanel', '_rt_send'))
check('接口发送继续读取真实 URL / Base 并调用原有远程确认',
      'mask_private_ipv4' not in send_src and 'self.rt_url.text()' in send_src
      and 'self.rt_base_edit.text()' in send_src and 'self._rt_confirm_remote_if_needed(url, method)' in send_src)
for name in ('_fill_local_targets', '_rt_refresh_send_label', '_rt_set_response_view', '_rt_lib_refresh_list'):
    check('请求验证展示使用同一遮蔽策略 ' + name,
          'mask_private_ipv4(' in ast.unparse(method(panel_tree, 'InterfaceDebugPanel', name)))
probe = ast.unparse(method(panel_tree, 'InterfaceDebugPanel', '_test_listen_loopback'))
check('接口监听探测仍只连接回环', "socket.create_connection(('127.0.0.1', int(port))" in probe)

_isolation.assert_untouched()
print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
raise SystemExit(bool(FAIL))
