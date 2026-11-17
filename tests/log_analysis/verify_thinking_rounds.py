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
class FakeChat:
    def __init__(self):
        self.calls = []
        self.role = ''
        self.messages = []

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
        self.role = ''

    def append_message(self, message):
        self.calls.append(('msg', message.get('role')))

    def end_stream(self):
        self.calls.append(('end',))
        self.role = ''


def workspace_like(selected='A', chats=None, service=None):
    ws = SimpleNamespace(selected_id=selected, chat=FakeChat(),
                         _thinking={}, chats=chats if chats is not None else {},
                         service=service)
    for name in ('_apply_deltas', '_replay_thinking', '_thinking_round',
                 '_start_thinking_round'):
        setattr(ws, name, (lambda n: lambda *a, **k: getattr(LogAnalysisWorkspace, n)(ws, *a, **k))(name))
    return ws


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
