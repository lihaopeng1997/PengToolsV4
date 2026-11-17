"""Per-round thinking replay stays UI-only (offscreen)."""
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from ui.log_analysis.workspace import LogAnalysisWorkspace

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))

# ------------------------------------------------------- bug 2: per-task thinking
class _Bar:
    def __init__(self):
        self._value = 0
        self._maximum = 0

    def value(self):
        return self._value

    def maximum(self):
        return self._maximum

    def setValue(self, value):
        self._value = value


class FakeChat:
    def __init__(self):
        self.calls = []
        self.role = ''
        self.messages = []
        self.rendered = []
        self._bar = _Bar()

    @property
    def stream_role(self):
        return self.role

    def begin_stream(self, label, *, role='thinking'):
        self.role = role
        self.calls.append(('begin', label))

    def append_stream(self, text):
        self.calls.append(('append', text))

    def set_messages(self, messages):
        self.messages = list(messages)
        self.calls.clear()
        self.rendered.clear()
        self.role = ''

    def append_message(self, message):
        self.calls.append(('msg', message.get('role')))
        self.rendered.append(message.get('text'))

    def end_stream(self):
        self.calls.append(('end',))
        self.role = ''

    def verticalScrollBar(self):
        return self._bar


def workspace_like(selected='A', chats=None, service=None):
    ws = SimpleNamespace(selected_id=selected, chat=FakeChat(),
                         _thinking={}, _thinking_pending={}, _thinking_active={},
                         chats=chats if chats is not None else {},
                         service=service,
                         jump=SimpleNamespace(hide=lambda: None, show=lambda: None),
                         reports={}, report_baselines={},
                         draft=SimpleNamespace(toPlainText=lambda: '', setPlainText=lambda text: None),
                         right_tabs=SimpleNamespace(setCurrentIndex=lambda index: None))
    for name in ('_apply_deltas', '_replay_thinking', '_thinking_round',
                 '_start_thinking_round', '_reserve_thinking_round',
                 '_activate_thinking_round', '_shift_thinking', '_ensure_round_maps',
                 '_append_answer_preview', '_clear_answer_preview',
                 'append_chat', '_drain_analysis_events'):
        setattr(ws, name, (lambda n: lambda *a, **k: getattr(LogAnalysisWorkspace, n)(ws, *a, **k))(name))
    return ws


def thinking_follows(chat, question):
    """True when the thinking bubble is drawn immediately after the question."""
    rendered_at = 0
    previous = None
    for call in chat.calls:
        if call[0] == 'msg':
            previous = chat.rendered[rendered_at]
            rendered_at += 1
            continue
        if call[0] == 'begin':
            return previous == question
    return False


print('--- bug 2: per-task thinking ---')
ws = workspace_like('A')
LogAnalysisWorkspace._apply_deltas(ws, [
    ('A', 'model_reasoning_delta', 'A-思考1'),
    ('B', 'model_reasoning_delta', 'B-思考1'),
    ('A', 'model_reasoning_delta', 'A-思考2'),
])
check('A 只显示自己的思考',
      [c[1] for c in ws.chat.calls if c[0] == 'append'] == ['A-思考1', 'A-思考2'],
      str(ws.chat.calls))
check('B 的思考已缓存', [c for r in ws._thinking.get('B', []) for c in r['chunks']] == ['B-思考1'])
check('A 的思考已缓存', [c for r in ws._thinking.get('A', []) for c in r['chunks']] == ['A-思考1', 'A-思考2'])

# switch to B: set_messages resets the view, replay only B's own thinking
ws.selected_id = 'B'
ws.chat.set_messages([])
ws._replay_thinking()
check('切到 B 只重放 B 的思考',
      [c[1] for c in ws.chat.calls if c[0] == 'append'] == ['B-思考1'],
      str(ws.chat.calls))

# back to A
ws.selected_id = 'A'
ws.chat.set_messages([])
ws._replay_thinking()
check('切回 A 仍能看到 A 的思考',
      [c[1] for c in ws.chat.calls if c[0] == 'append'] == ['A-思考1', 'A-思考2'],
      str(ws.chat.calls))
check('思考不进 chat 消息', ws.chat.messages == [], str(ws.chat.messages))
apply_src = inspect.getsource(LogAnalysisWorkspace._apply_deltas)
check('_apply_deltas 不写历史/消息', 'self.chats[' not in apply_src and 'self.history[' not in apply_src, apply_src[-200:])


def anchor_thinking(ws, task_id, start, chunk):
    item = {'start': start, 'chunks': [chunk]}
    ws._thinking.setdefault(task_id, []).append(item)
    ws._thinking_active[task_id] = item
    return item


print('--- 截断后思考仍跟在问题上 ---')
count_ws = workspace_like('t', chats={'t': [{'role': 'system', 'text': f'旧{i}'} for i in range(204)]})
count_ws.chats['t'].append({'role': 'user', 'text': '问题甲'})
anchor_thinking(count_ws, 't', 205, '思考甲')
LogAnalysisWorkspace.append_chat(count_ws, 't', '最新一条', role='system')
check('超过 200 条后思考仍在问题之后', thinking_follows(count_ws.chat, '问题甲'), str(count_ws.chat.calls[-8:]))
check('超过 200 条后起点已前移', count_ws._thinking['t'][0]['start'] == 199, str(count_ws._thinking['t'][0]['start']))

char_ws = workspace_like('t', chats={'t': [{'role': 'system', 'text': '字' * 1000} for _ in range(140)]})
char_ws.chats['t'].append({'role': 'user', 'text': '问题乙'})
anchor_thinking(char_ws, 't', 141, '思考乙')
LogAnalysisWorkspace.append_chat(char_ws, 't', '补' * 20000, role='system')
check('超过 150000 字后思考仍在问题之后', thinking_follows(char_ws.chat, '问题乙'), str(char_ws._thinking['t']))
gone = workspace_like('t', chats={'t': [{'role': 'system', 'text': '留'}]})
gone._thinking['t'] = [{'start': 0, 'chunks': []}, {'start': 5, 'chunks': ['还在']}]
LogAnalysisWorkspace._shift_thinking(gone, 't', 3)
check('空轮次被丢掉', gone._thinking['t'] == [{'start': 2, 'chunks': ['还在']}], str(gone._thinking['t']))


print('--- 分轮 ---')
rounds = workspace_like('t', chats={'t': [{'role': 'user', 'text': '已有'}]})
LogAnalysisWorkspace._apply_deltas(rounds, [
    ('t', 'model_round_start', 'auto'),
    ('t', 'model_reasoning_delta', '自动甲'),
    ('t', 'model_round_start', 'auto'),
    ('t', 'model_reasoning_delta', '自动乙'),
])
auto_rounds = rounds._thinking['t']
check('连续两次自动分析各自一轮',
      [item['chunks'] for item in auto_rounds] == [['自动甲'], ['自动乙']], str(auto_rounds))

sent = workspace_like('t', chats={'t': []})
sent.chats['t'].append({'role': 'user', 'text': '问题1'})
LogAnalysisWorkspace._reserve_thinking_round(sent, 't')
sent.chats['t'].append({'role': 'user', 'text': '问题2'})
LogAnalysisWorkspace._reserve_thinking_round(sent, 't')
LogAnalysisWorkspace._apply_deltas(sent, [
    ('t', 'model_round_start', 'chat'),
    ('t', 'model_reasoning_delta', '思1'),
    ('t', 'model_round_start', 'chat'),
    ('t', 'model_reasoning_delta', '思2'),
])
paired = sent._thinking['t']
check('连发两条问题各归各轮',
      [(item['start'], item['chunks']) for item in paired] == [(1, ['思1']), (2, ['思2'])],
      str(paired))
check('思考不写 self.chats', all('思' not in item['text'] for item in sent.chats['t']))


print('--- 同一次刷新的到达顺序 ---')
ordered = workspace_like('t', chats={'t': []})

class OrderedService:
    def drain_events(self):
        return [
            ('result', ('t', '答案一', 'auto')),
            ('delta', ('t', 'model_round_start', 'auto')),
            ('delta', ('t', 'model_reasoning_delta', '下一轮')),
        ]

ordered.service = OrderedService()
LogAnalysisWorkspace._drain_analysis_events(ordered)
later = ordered._thinking['t'][-1]
check('结果先到时下一轮排在答案之后', later['start'] == 1 and later['chunks'] == ['下一轮'], str(ordered._thinking['t']))
check('正式消息只有答案', [item['text'] for item in ordered.chats['t']] == ['答案一'])


print('--- service 历史不含思考 ---')
import time
from types import SimpleNamespace as _NS

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
    kwargs['progress']('model_reasoning_delta', '不应进历史')
    return _NS(final_text='只有结论', error='', status=_NS(value='ok'))

from tools.log_analysis.service import LogService
_service = LogService(store=None, analyzer=_analyzer)
_service.tasks['t'] = _Task()
_service.enqueue('t', '请问', 'model', kind='auto')
_deadline = time.time() + 5
while time.time() < _deadline:
    with _service.lock:
        if _service.active is None and not _service.queue and any(row[1] == 'result' for row in _service._ordered):
            break
    time.sleep(0.02)
_events = _service.drain_events()
_service.close()
_service.join(1)
_history = _service.history.get('t', [])
_kinds = [(bucket, payload[1]) for bucket, payload in _events]
check('到达顺序是轮次开始、思考、再结果',
      _kinds[:3] == [('delta', 'model_round_start'), ('delta', 'model_reasoning_delta'), ('result', '只有结论')],
      str(_kinds))
check('history 没有思考', _history and _history[-1]['content'].startswith('只有结论') and '不应进历史' not in str(_history), str(_history))


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
