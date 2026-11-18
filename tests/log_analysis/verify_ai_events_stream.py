"""Fake SSE streaming and the shared AI event contract. No network."""
import http.client
import json
import os
import random
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication

QApplication([])

from tools.ai_events import ReasoningBuffer, phase_label, tool_label
from tools.app_logging import current_log_dir, get_logger, setup_logging
from tools.intranet_llm import (
    IntranetLlmError,
    StreamIdleTimeout,
    _completion_text,
    _decode_anthropic_body,
    chat_completions,
    consume_openai_sse,
    iter_idle_chunks,
)
import tools.model_chat_store as chat_store
import tools.intranet_llm as llm
from panels.model_chat_panel import ModelChatPanel, _ChatWorker

import config
chat_store.MODEL_CHAT_DIR = os.path.join(_isolation.temp_dir(), 'model_chat')
config.MODEL_CHAT_DIR = chat_store.MODEL_CHAT_DIR

PASS, FAIL = [], []
THINK = 'THINK_zz_9f3c_不应落盘'
ANSWER_TAIL = '可见答案'


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


def _has_cjk(text: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in text)


def _sse(payload) -> str:
    if payload == '[DONE]':
        return 'data: [DONE]\n\n'
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


def _cfg(**extra):
    item = {
        'enabled': True,
        'provider': 'intranet',
        'api_protocol': 'openai',
        'base_url': 'http://127.0.0.1:9/v1',
        'model': 'fake',
        'timeout_seconds': 30,
        'max_tokens': 64,
        'token': '',
    }
    item.update(extra)
    return item


class _Resp:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.status = 200
        self._pos = 0

    def read(self, n=-1):
        if self._pos >= len(self.payload):
            return b''
        if n is None or n < 0 or n >= len(self.payload) - self._pos:
            chunk = self.payload[self._pos:]
            self._pos = len(self.payload)
            return chunk
        chunk = self.payload[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


SSE = ''.join([
    _sse({'choices': [{'delta': {'reasoning_content': '先想一步'}}]}),
    _sse({'choices': [{'delta': {'content': '可见<think>' + THINK + '</think>答案'}}]}),
    _sse('[DONE]'),
]).encode('utf-8')


print('--- SSE 事件 ---')
pieces = [SSE[index:index + 5] for index in range(0, len(SSE), 5)]
seen = []
text = consume_openai_sse(pieces, on_delta=lambda kind, part: seen.append((kind, part)))
check('正文去掉 think 标签', text == '可见答案' and '<think>' not in text and '</think>' not in text and THINK not in text, text)
check('事件顺序', seen == [
    ('reasoning', '先想一步'),
    ('text', '可见'),
    ('reasoning', THINK),
    ('text', '答案'),
], str(seen))
check('正文事件不含 think 标签', all('<think>' not in part and '</think>' not in part for kind, part in seen if kind == 'text'))


def _install_open(factory):
    original = llm._open_http

    def _open(cfg, method, suffix, body, timeout):
        return factory(body)

    llm._open_http = _open
    return original


print('--- 经 chat_completions 的真流式 ---')
opens = []


def _stream_open(body):
    opens.append(bool(body.get('stream')))
    return _Resp(SSE)


original_open = _install_open(_stream_open)
try:
    streamed = []
    full = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg(), on_delta=lambda kind, part: streamed.append((kind, part)))
    plain = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg())
finally:
    llm._open_http = original_open
check('流式回调与直接解析一致', streamed == seen and full == '可见答案', str(streamed))
check('不传回调仍返回完整正文', plain == '可见答案' and opens == [True, True], str(opens))

print('--- 空闲超时 ---')
clock = {'t': 0.0}
step = {'n': 0}


def _slow_read():
    step['n'] += 1
    if step['n'] == 1:
        clock['t'] = 0.2
        return b'a'
    clock['t'] = 8.0
    return b'b'


try:
    list(iter_idle_chunks(_slow_read, idle_seconds=2, clock=lambda: clock['t']))
    check('分片间空闲超时触发', False, 'no timeout')
except StreamIdleTimeout:
    check('分片间空闲超时触发', True)

clock = {'t': 0.0}
count = {'n': 0}


def _steady_read():
    clock['t'] += 0.5
    count['n'] += 1
    if count['n'] > 40:
        return b''
    return b'x'


steady = list(iter_idle_chunks(_steady_read, idle_seconds=1, clock=lambda: clock['t']))
check('持续有分片时总时长不触发超时', len(steady) == 40 and clock['t'] >= 20, str((len(steady), clock['t'])))

print('--- 回退 ---')
fallback_body = json.dumps({'choices': [{'message': {'content': [
    {'type': 'text', 'text': '同一<th'},
    {'type': 'text', 'text': 'ink>' + THINK + '</think>结果'},
]}}]}, ensure_ascii=False).encode('utf-8')
anth_body = json.dumps({
    'id': 'm',
    'type': 'message',
    'role': 'assistant',
    'model': 'fake',
    'stop_reason': 'end_turn',
    'content': [{'type': 'text', 'text': '<THINK>' + THINK + '</THINK>同一结果'}],
}, ensure_ascii=False).encode('utf-8')
calls = []


def _nonsse_open(body):
    calls.append(bool(body.get('stream')))
    if body.get('stream'):
        return _Resp(b'not-sse')
    return _Resp(fallback_body)


def _anth_open(body):
    calls.append(bool(body.get('stream')))
    return _Resp(anth_body)


class _JsonGateway(_Resp):
    def read1(self, size):
        return super().read(min(size, 7))

    def read(self, size=-1):
        raise AssertionError('应优先使用 read1')


json_requests = []
original_open = _install_open(lambda body: (json_requests.append(body['stream']), _JsonGateway(fallback_body))[1])
try:
    json_answer = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg())
finally:
    llm._open_http = original_open
check('网关直接返回分片 JSON 时不重发且只返回正文', json_requests == [True] and json_answer == '同一结果')


original_open = _install_open(_nonsse_open)
try:
    non_sse = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg())
    non_sse_calls = list(calls)
finally:
    llm._open_http = original_open
calls.clear()
original_open = _install_open(_anth_open)
try:
    anth = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg(api_protocol='anthropic'))
    anth_calls = list(calls)
finally:
    llm._open_http = original_open
expected = _completion_text(json.loads(fallback_body.decode('utf-8')))
anth_expected = _completion_text(_decode_anthropic_body(anth_body.decode('utf-8'), 200))
check('非 SSE 走非流式回退', non_sse == expected == '同一结果' and non_sse_calls == [True, False], str(non_sse_calls))
check('未闭合思考只保留前面的正文', _completion_text({
    'choices': [{'message': {'content': '可见<think>' + THINK}}],
}) == '可见')
try:
    _completion_text({'choices': [{'message': {'content': '<think>' + THINK + '</think>'}}]})
except IntranetLlmError as exc:
    check('纯思考使用无正文错误', str(exc) == '模型返回为空')
else:
    check('纯思考使用无正文错误', False)
fallback_session = chat_store.create_session(model='fake')
fallback_session = chat_store.append_message(fallback_session['id'], 'assistant', '', status='pending')
fallback_host = SimpleNamespace(_stop_requested=False, _session=fallback_session,
                                _pending_id=fallback_session['messages'][-1]['id'])
ModelChatPanel._on_chat_ok(fallback_host, non_sse)
saved_message = chat_store.load_session(fallback_session['id'])['messages'][-1]
check('非流式回退落盘只含正文', saved_message['content'] == '同一结果' and saved_message['status'] == 'complete')
calls.clear()


def _broken_open(body):
    calls.append(bool(body.get('stream')))
    if body.get('stream'):
        return _Resp(b'data: {bad\n\n')
    return _Resp(fallback_body)


original_open = _install_open(_broken_open)
try:
    broken = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg())
    broken_calls = list(calls)
finally:
    llm._open_http = original_open
check('解析异常回退后与非流式一致', broken == expected and broken_calls == [True, False], str((broken, broken_calls)))
check('anthropic 直接走非流式且结果一致', anth == anth_expected == '同一结果' and anth_calls == [False], str((anth, anth_calls)))

print('--- 流中断 ---')
partial_sse = (
    _sse({'choices': [{'delta': {'reasoning_content': THINK}}]})
    + _sse({'choices': [{'delta': {'content': '可见一半'}}]})
).encode('utf-8')


class _Then:
    def __init__(self, parts, exc=None):
        self.parts = list(parts)
        self.exc = exc
        self.status = 200

    def read(self, n=-1):
        if self.parts:
            return self.parts.pop(0)
        if self.exc is not None:
            raise self.exc
        return b''

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _interrupt(parts, exc=None):
    seen = []

    def _open(body):
        seen.append(bool(body.get('stream')))
        if body.get('stream'):
            return _Then(parts, exc)
        return _Resp(fallback_body)

    original = _install_open(_open)
    try:
        try:
            result = chat_completions([{'role': 'user', 'content': 'hi'}], cfg=_cfg())
        except IntranetLlmError as err:
            return 'error', seen, err
        return 'ok', seen, result
    finally:
        llm._open_http = original


kind, seen, value = _interrupt([], ConnectionResetError())
check('无正文连接重置走回退', kind == 'ok' and value == expected and seen == [True, False], str((kind, seen)))
kind, seen, value = _interrupt([], http.client.IncompleteRead(b''))
check('无正文 IncompleteRead 走回退', kind == 'ok' and value == expected and seen == [True, False], str((kind, seen)))


def _incomplete(parts, exc=None):
    kind, seen, err = _interrupt(parts, exc)
    message = '' if kind == 'ok' else str(err)
    return (
        kind == 'error'
        and seen == [True]
        and type(err) is IntranetLlmError
        and '回答不完整' in message
        and THINK not in message
        and 'http://' not in message
        and 'https://' not in message
    )


check('有正文后连接重置不回退', _incomplete([partial_sse], ConnectionResetError()))
check('有正文但无结束信号的 EOF 不重发', _incomplete([partial_sse]))
finish_chunk = _sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}).encode('utf-8')
kind, seen, value = _interrupt([partial_sse, finish_chunk])
check('只有 finish_reason 的正常 EOF 保留正文', (kind, seen, value) == ('ok', [True], '可见一半'))
reasoning_chunk = _sse({'choices': [{'delta': {'reasoning_content': THINK}}]}).encode('utf-8')
kind, seen, value = _interrupt([reasoning_chunk])
check('只有思考且无结束信号的 EOF 可以回退', (kind, seen, value) == ('ok', [True, False], expected))
check('有正文后空闲超时不回退', _incomplete([partial_sse], TimeoutError()))
check('有正文后解析异常不回退', _incomplete([partial_sse, b'data: {bad\n\n']))

print('--- 标签与上限 ---')
rng = random.Random(7)
unknown_states = [
    'zz_' + '_'.join(''.join(rng.choice('abcxyz') for _ in range(4)) for _ in range(3))
    for _ in range(12)
]
unknown_labels = [phase_label(state) for state in unknown_states]
check(
    '随机英文状态都不回显原文且是中文',
    all(state not in label and _has_cjk(label) and label == '正在处理' for state, label in zip(unknown_states, unknown_labels)),
    str(list(zip(unknown_states, unknown_labels))[:2]),
)
check('重置阶段是正在处理', phase_label('model_turn_reset') == '正在处理')
check('思考阶段是正在思考', phase_label('thinking') == '正在思考')
check('工具阶段是正在执行操作', phase_label('tool_call') == '正在执行操作')
check('已知工具中文名', [
    tool_label('search_objects'), tool_label('describe_object'), tool_label('query_readonly'),
    tool_label('read_file'), tool_label('search_code'), tool_label('list_dir'),
] == ['搜索对象', '查看表结构', '只读查询', '读取文件', '搜索代码', '查看目录'])
check('未知工具是执行工具', tool_label('drop_database') == '执行工具' and 'drop_database' not in tool_label('drop_database'))
buffer = ReasoningBuffer()
blob = '思' * 40000
buffer.add(blob)
stored = buffer.text()
check('思考内存不超过 64KB', len(stored.encode('utf-8')) <= 64 * 1024 and blob.endswith(stored) and stored)

print('--- 会话与日志不含思考 ---')
from ui.quick_panel import _QuickChatWorker
from unittest.mock import patch

raw_chat = [{'role': 'assistant', 'content': 'password=TEST_ONLY_MARKER'},
            {'role': 'user', 'content': [{'type': 'text', 'text': '普通文字\ntoken=TEST_ONLY_MARKER'},
                                         {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]}]
for worker_type in (_QuickChatWorker, _ChatWorker):
    with patch.object(llm, 'chat_completions', return_value='OK') as fake_send:
        worker_type(raw_chat, _cfg()).run()
        sent = fake_send.call_args.args[0]
        check(worker_type.__name__ + ' 清洗请求副本而非原历史', 'TEST_ONLY_MARKER' not in str(sent)
              and 'TEST_ONLY_MARKER' in raw_chat[0]['content'] and '普通文字' in str(sent)
              and sent[1]['content'][1] == raw_chat[1]['content'][1])
import tools.intranet_llm as llm_mod


def _fake_chat(messages, cfg=None, model_config_id=None, on_delta=None):
    if on_delta:
        on_delta('reasoning', THINK)
        on_delta('text', ANSWER_TAIL)
    return ANSWER_TAIL


llm_mod.chat_completions = _fake_chat
session = chat_store.create_session(model='fake')
session = chat_store.append_message(session['id'], 'assistant', '', status='pending')
host = SimpleNamespace(
    _stop_requested=False,
    _session=session,
    _pending_id=session['messages'][-1]['id'],
    _live_answer='',
    _pending_body=None,
    _render_messages=lambda: None,
)
worker = _ChatWorker([{'role': 'user', 'content': 'hi'}], _cfg())
worker.ai_event.connect(lambda event: ModelChatPanel._on_ai_event(host, event))
worker.completed.connect(lambda text: ModelChatPanel._on_chat_ok(host, text))
worker.run()
saved = json.dumps(chat_store.load_session(session['id']), ensure_ascii=False)
check('保存的会话没有思考文本', THINK not in saved and ANSWER_TAIL in saved, saved)
log_path = setup_logging()
get_logger('pengtools.ai_events_test').info('marker-only')
log_file = Path(current_log_dir() or os.path.dirname(log_path)) / 'app.log'
log_text = log_file.read_text(encoding='utf-8') if log_file.is_file() else ''
check('日志文件没有思考文本', THINK not in log_text and 'marker-only' in log_text, log_file.as_posix())

print()
try:
    _isolation.assert_untouched()
    check('未触碰真实数据目录与用户配置', True)
except AssertionError as exc:
    check('未触碰真实数据目录与用户配置', False, str(exc))

print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
