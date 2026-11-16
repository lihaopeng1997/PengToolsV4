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
obj = SimpleNamespace(selected_id='t1', chat=FakeChat())
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

print('--- text_delta 不渲染 ---')
obj2 = SimpleNamespace(selected_id='t1', chat=FakeChat())
LogAnalysisWorkspace._apply_deltas(obj2, [('t1', 'model_text_delta', '半截JSON')])
check('text_delta 不落屏', obj2.chat.calls == [], str(obj2.chat.calls))

print('--- 不写历史 ---')
apply_src = inspect.getsource(LogAnalysisWorkspace._apply_deltas)
check('_apply_deltas 不碰历史', 'history' not in apply_src and 'self.chats' not in apply_src)
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
