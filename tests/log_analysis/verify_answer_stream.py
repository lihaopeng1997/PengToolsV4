"""Final answers stream as display-only bubbles, then yield to final_text."""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from tools.agent_core.model_adapter import ModelAdapter
from tools.log_analysis.runtime import TextCalls
from tools.log_analysis.service import LogService
from ui.log_analysis.workspace import LogAnalysisWorkspace

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


class Delta:
    def __init__(self, kind, text=''):
        self.kind = kind
        self.text = text


class ScriptAdapter:
    def __init__(self, deltas, text='最终答案'):
        self.deltas = deltas
        self.text = text
        self.tool_calls = ()

    def complete_turn(self, request, *, cancellation, on_delta):
        for delta in self.deltas:
            on_delta(delta)
        return SimpleNamespace(tool_calls=(), text=self.text, finish_reason='stop')


def streamed(deltas):
    seen = []
    TextCalls(ScriptAdapter(deltas), delta_sink=lambda kind, text: seen.append((kind, text))).complete_turn(
        None, cancellation=None, on_delta=lambda delta: None)
    return seen


def texts(events):
    return [text for kind, text in events if kind == 'model_text_delta']


print('--- 正文防护 ---')
normal = streamed([Delta('text_delta', '你好'), Delta('text_delta', '，世界')])
check('每轮开始会发 reset', normal[0] == ('model_turn_reset', ''))
check('普通回答逐段推送', texts(normal) == ['你好', '，世界'], str(texts(normal)))

check('以 <tool_call> 开头不推', texts(streamed([Delta('text_delta', '<tool_call>{"name":"x"}')])) == [])
check('以 { 开头不推', texts(streamed([Delta('text_delta', '{')])) == [])
check('以 ```json 开头不推', texts(streamed([Delta('text_delta', '```json\n{}')])) == [])

mid = texts(streamed([
    Delta('text_delta', '看到现象 '),
    Delta('text_delta', '<tool'),
    Delta('text_delta', '_call><x>'),
]))
check('中途 tool_call 停推且无残片', mid == ['看到现象 '], str(mid))
check('思考转发保持不变',
      ('model_reasoning_delta', '想') in streamed([Delta('reasoning_delta', '想'), Delta('text_delta', '答')]))

print('--- 结果只留 final_text ---')


class _Bar:
    def value(self):
        return 0

    def maximum(self):
        return 0

    def setValue(self, value):
        return None


class _Chat:
    def __init__(self):
        self.role = ''
        self.chunks = []

    @property
    def stream_role(self):
        return self.role

    def begin_stream(self, label, *, role='thinking'):
        self.role = role
        self.chunks.append(('begin', role, label))

    def append_stream(self, text):
        self.chunks.append(('append', text))

    def end_stream(self):
        self.role = ''

    def set_messages(self, messages):
        self.role = ''

    def append_message(self, message):
        self.chunks.append(('msg', message.get('text')))

    def verticalScrollBar(self):
        return _Bar()


ws = SimpleNamespace(selected_id='t', chat=_Chat(), chats={}, _thinking={},
                     _thinking_pending={}, _thinking_active={}, _answer_preview={},
                     jump=SimpleNamespace(hide=lambda: None, show=lambda: None),
                     reports={}, report_baselines={},
                     draft=SimpleNamespace(toPlainText=lambda: '', setPlainText=lambda text: None),
                     right_tabs=SimpleNamespace(setCurrentIndex=lambda index: None))
for name in ('_apply_deltas', '_append_answer_preview', '_clear_answer_preview',
             '_replay_thinking', '_thinking_round', '_activate_thinking_round',
             '_shift_thinking', 'append_chat', '_drain_analysis_events'):
    setattr(ws, name, (lambda n: lambda *a, **k: getattr(LogAnalysisWorkspace, n)(ws, *a, **k))(name))
LogAnalysisWorkspace._apply_deltas(ws, [
    ('t', 'model_text_delta', '临时'),
    ('t', 'model_text_delta', '半截'),
])
check('临时气泡不进 chats', ws.chats == {} and ''.join(ws._answer_preview['t']) == '临时半截')


class _Events:
    def drain_events(self):
        return [('result', ('t', '质检后的最终回答', 'chat'))]


ws.service = _Events()
LogAnalysisWorkspace._drain_analysis_events(ws)
check('结果到达后只剩 final_text',
      ws.chats.get('t') == [{'role': 'assistant', 'text': '质检后的最终回答'}], str(ws.chats))
check('临时缓冲已清空', 't' not in ws._answer_preview)

print('--- history 与 DeepSeek ---')


class _Task:
    id = 't'
    thread = None

    def snapshot(self, blocking=True):
        return {'id': 't', 'events': []}

    def stop(self):
        return None

    def read_range(self, *args, **kwargs):
        return None


def _analyzer(snapshot, question, model_id, token, **kwargs):
    kwargs['progress']('model_reasoning_delta', '思考不该进历史')
    return SimpleNamespace(final_text='质检后的最终回答', error='', status=SimpleNamespace(value='ok'))


service = LogService(store=None, analyzer=_analyzer)
service.tasks['t'] = _Task()
service.enqueue('t', '问题', 'model', kind='chat')
deadline = time.time() + 5
while time.time() < deadline:
    with service.lock:
        ready = service.active is None and not service.queue and any(row[1] == 'result' for row in service._ordered)
    if ready:
        break
    time.sleep(0.02)
service.close()
service.join(1)
history = service.history.get('t', [])
check('history 只有 final_text、没有思考',
      history and history[-1]['role'] == 'assistant'
      and history[-1]['content'].startswith('质检后的最终回答')
      and '思考不该进历史' not in str(history), str(history))


class _DeepSeek:
    strict_tool_envelope = False

    def _provider(self):
        return 'deepseek'


hidden = []
returned = ModelAdapter._emit(_DeepSeek(), hidden.append, {'kind': 'reasoning_delta', 'text': '隐藏的思考'})
check('DeepSeek 关闭 expose_reasoning 时没有思考文本',
      hidden == [] and returned.text == '' and returned.kind == 'reasoning_delta',
      str((hidden, returned)))

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
