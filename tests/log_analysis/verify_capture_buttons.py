"""Capture buttons and the analysis-stop button follow real state (offscreen)."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QPushButton

app = QApplication([])

from ui.log_analysis.workspace import LogAnalysisWorkspace

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))

# ----------------------------------------------------------- bug 3: button state
def workspace_buttons():
    buttons = {name: QPushButton(name) for name in (
        '开始监控 / AI取证', '停止任务', '暂停 / 恢复采集', '暂停 AI 分析', '恢复所选历史任务')}
    ws = SimpleNamespace(
        control_buttons=buttons,
        selected_id='',
        restored={},
        servers=SimpleNamespace(currentData=lambda: None),
        source=SimpleNamespace(currentData=lambda: 'server'),
        files=QPlainTextEdit(),
        selected_mode=lambda: 'history',
    )
    ws._sync_control_buttons = lambda task=None: LogAnalysisWorkspace._sync_control_buttons(ws, task)
    return ws, buttons


print('--- bug 3: capture button state ---')
ws3, btns = workspace_buttons()
ws3._sync_control_buttons(None)
start = btns['开始监控 / AI取证']
check('没选文件时置灰', not start.isEnabled())

ws3.source = SimpleNamespace(currentData=lambda: 'local')
ws3.files.setPlainText('/tmp/a.log')
ws3._sync_control_buttons(None)
check('选了文件后可点', start.isEnabled())
check('没有任务时停止按钮置灰', not btns['停止任务'].isEnabled())


class FakeThread:
    def __init__(self, alive=True):
        self._alive = alive

    def is_alive(self):
        return self._alive


class FakeEvent:
    def __init__(self, set_=False):
        self._set = set_

    def is_set(self):
        return self._set


running_task = SimpleNamespace(thread=FakeThread(True), stop_event=FakeEvent(False))
ws3._sync_control_buttons(running_task)
check('执行中采集按钮置灰', not start.isEnabled())
check('执行中停止按钮可用', btns['停止任务'].isEnabled())

finished_task = SimpleNamespace(thread=FakeThread(True), stop_event=FakeEvent(True))
ws3._sync_control_buttons(finished_task)
check('结束后采集按钮恢复', start.isEnabled())
check('结束后停止按钮置灰', not btns['停止任务'].isEnabled())
check('没有分析时暂停 AI 置灰', not btns['暂停 AI 分析'].isEnabled())

ws3.service = SimpleNamespace(analysis_activity=lambda tid: {'busy': True, 'state': '模型分析中'})
ws3.selected_id = 'A'
ws3._sync_control_buttons(finished_task)
check('分析进行中暂停 AI 可点', btns['暂停 AI 分析'].isEnabled())

ws3.service = SimpleNamespace(analysis_activity=lambda tid: {'busy': False, 'state': '就绪'})
ws3._sync_control_buttons(finished_task)
check('分析结束暂停 AI 又置灰', not btns['暂停 AI 分析'].isEnabled())

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
