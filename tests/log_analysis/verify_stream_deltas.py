"""Batch-9 streaming queue regressions: deltas stay display-only (offscreen, no model)."""
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

from tools.log_analysis.service import LogService
from ui.log_analysis.workspace import LogAnalysisWorkspace

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


class FakeChat:
    def __init__(self):
        self.calls = []
        self.role = ''

    @property
    def stream_role(self):
        return self.role

    def begin_stream(self, label, *, role='thinking'):
        self.role = role
        self.calls.append(('begin', label, role))

    def append_stream(self, text):
        self.calls.append(('append', text))

    def end_stream(self):
        self.role = ''

    def set_messages(self, messages):
        self.role = ''
        self.calls.append(('set', len(messages)))

    def append_message(self, message):
        self.calls.append(('msg', message.get('text')))

    def verticalScrollBar(self):
        class _Bar:
            def value(self):
                return 0

            def maximum(self):
                return 0

            def setValue(self, value):
                return None
        return _Bar()


print('--- deltas 队列 ---')
svc = LogService(store=None, analyzer=lambda *a, **k: None)
check('初始为空', svc.drain_deltas() == [])
svc.deltas.append(('t1', 'model_reasoning_delta', '先看现象'))
svc.deltas.append(('t1', 'model_reasoning_delta', '再看证据'))
items = svc.drain_deltas()
check('按序返回', items == [('t1', 'model_reasoning_delta', '先看现象'),
                            ('t1', 'model_reasoning_delta', '再看证据')], str(items))
check('取空后可重复调用', svc.drain_deltas() == [])

print('--- progress 分流（源码约束）---')
src = inspect.getsource(LogService)
progress_block = src.split('def progress(kind, name):', 1)[1].split('result = self.analyzer', 1)[0]
check('流式 kind 先入队', "self.deltas.append((task_id, kind, name))" in progress_block)
check('流式 kind 提前 return', 'return' in progress_block.split('self.analysis_state', 1)[0])
check('状态栏文案不含流式分支',
      "'model_reasoning_delta'" not in progress_block.split('self.analysis_state', 1)[1])

print('--- _apply_deltas 只渲染当前任务 ---')
obj = SimpleNamespace(selected_id='t1', chat=FakeChat(), _thinking={}, chats={})
obj._thinking_round = lambda tid: LogAnalysisWorkspace._thinking_round(obj, tid)
LogAnalysisWorkspace._apply_deltas(obj, [
    ('t1', 'model_reasoning_delta', '片段1'),
    ('t2', 'model_reasoning_delta', '串台片段'),
    ('t1', 'model_reasoning_delta', '片段2'),
])
kinds = [c[0] for c in obj.chat.calls]
check('首次开启思考气泡', obj.chat.calls[0][0] == 'begin' and obj.chat.calls[0][1] == '思考过程',
      str(obj.chat.calls[:1]))
check('同流不再重复 begin', kinds.count('begin') == 1, str(kinds))
check('只追加当前任务', [c[1] for c in obj.chat.calls if c[0] == 'append'] == ['片段1', '片段2'],
      str(obj.chat.calls))
check('不串台', '串台片段' not in str(obj.chat.calls))

print('--- text_delta 只进临时气泡 ---')
obj2 = SimpleNamespace(selected_id='t1', chat=FakeChat(), _thinking={}, chats={},
                       _answer_preview={}, _thinking_pending={}, _thinking_active={},
                       jump=SimpleNamespace(hide=lambda: None, show=lambda: None))
for _name in ('_thinking_round', '_append_answer_preview', '_clear_answer_preview',
              '_replay_thinking', '_shift_thinking', 'append_chat', '_ensure_round_maps'):
    setattr(obj2, _name, (lambda n: lambda *a, **k: getattr(LogAnalysisWorkspace, n)(obj2, *a, **k))(_name))
LogAnalysisWorkspace._apply_deltas(obj2, [('t1', 'model_text_delta', '半截JSON')])
check('text_delta 只进临时气泡',
      [c[1] for c in obj2.chat.calls if c[0] == 'append'] == ['半截JSON']
      and obj2.chat.calls[0][2] == 'assistant', str(obj2.chat.calls))
check('text_delta 不进 self.chats/history', obj2.chats == {} and not hasattr(obj2, 'history') or obj2.chats == {})
class _Done:
    def drain_events(self):
        return [('result', ('t1', '最终核验文本', 'chat'))]
obj2.service = _Done()
obj2.reports = {}
obj2.report_baselines = {}
obj2.draft = SimpleNamespace(toPlainText=lambda: '', setPlainText=lambda text: None)
obj2.right_tabs = SimpleNamespace(setCurrentIndex=lambda index: None)
LogAnalysisWorkspace._drain_analysis_events(obj2)
check('结果到达后被 final_text 替换',
      obj2.chats.get('t1') == [{'role': 'assistant', 'text': '最终核验文本'}]
      and '半截JSON' not in str(obj2.chats), str(obj2.chats))

print('--- 不写历史 ---')
apply_src = inspect.getsource(LogAnalysisWorkspace._apply_deltas)
check('_apply_deltas 不碰历史', 'self.history[' not in apply_src and 'self.chats[' not in apply_src)
drain_src = inspect.getsource(LogService.drain_deltas)
check('drain_deltas 只出队', 'self.deltas.clear()' in drain_src and 'history' not in drain_src)

print()
try:
    _isolation.assert_untouched()
    check('未触碰真实 data/ 与用户配置', True)
except AssertionError as exc:
    check('未触碰真实 data/ 与用户配置', False, str(exc))

print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
