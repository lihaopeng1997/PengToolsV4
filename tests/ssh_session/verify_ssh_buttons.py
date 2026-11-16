"""Button-state regressions for the manual-troubleshooting SSH tabs (offscreen)."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

app = QApplication([])

from panels.ops_log_panel import OpsLogPanel

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


class FakeTabs:
    def __init__(self, count, current=0):
        self._count = count
        self._current = current

    def count(self):
        return self._count

    def currentIndex(self):
        return self._current


def make_panel(sessions, current=0, count=None):
    panel = SimpleNamespace(
        language='zh', header_status=QLabel(), toolbar_hint=QLabel(),
        connect_btn=QPushButton(), disconnect_btn=QPushButton(),
        _term_sessions=sessions,
        term_tabs=FakeTabs(count if count is not None else len(sessions), current),
        _session_index_label=lambda i: str(i + 1),
    )
    panel._current_session = lambda: (
        panel._term_sessions[panel.term_tabs.currentIndex()]
        if 0 <= panel.term_tabs.currentIndex() < len(panel._term_sessions) else None)
    return panel


def refresh(p):
    OpsLogPanel._refresh_header_session_status(p)


connected = {'client': object(), 'connected': True}
idle = {'client': None, 'connected': False}

print('--- 按钮跟随当前终端 ---')
p = make_panel([dict(connected), dict(idle)], current=0)
refresh(p)
check('A已连接: 连接置灰', not p.connect_btn.isEnabled())
check('A已连接: 断开可用', p.disconnect_btn.isEnabled())
check('A状态文案', '已连接' in p.header_status.text(), p.header_status.text())

p.term_tabs._current = 1
refresh(p)
check('B未连接: 连接可用', p.connect_btn.isEnabled())
check('B未连接: 断开置灰', not p.disconnect_btn.isEnabled())
check('B状态文案', '未连接' in p.header_status.text(), p.header_status.text())

p.term_tabs._current = 0
refresh(p)
check('切回A仍正确', not p.connect_btn.isEnabled() and p.disconnect_btn.isEnabled())

print('--- 进行中 ---')
s2 = [dict(connected, connecting=True), dict(idle)]
p2 = make_panel(s2, current=0)
refresh(p2)
check('连接中: 两钮都禁用', not p2.connect_btn.isEnabled() and not p2.disconnect_btn.isEnabled())
check('连接中文案', '正在连接' in p2.header_status.text(), p2.header_status.text())

s3 = [dict(connected, disconnecting=True), dict(idle)]
p3 = make_panel(s3, current=0)
refresh(p3)
check('断开中: 两钮都禁用', not p3.connect_btn.isEnabled() and not p3.disconnect_btn.isEnabled())
check('断开中文案', '正在断开' in p3.header_status.text(), p3.header_status.text())

print('--- 无终端 ---')
p4 = make_panel([], current=0, count=0)
refresh(p4)
check('无终端: 两钮禁用', not p4.connect_btn.isEnabled() and not p4.disconnect_btn.isEnabled())
check('无终端提示', '请选择或新建终端' in p4.header_status.text(), p4.header_status.text())

print('--- A 连接中切到 B ---')
s5 = [dict(connected, connecting=True), dict(idle)]
p5 = make_panel(s5, current=1)
refresh(p5)
check('A连接中切B: B连接可用', p5.connect_btn.isEnabled())
check('A连接中切B: B断开置灰', not p5.disconnect_btn.isEnabled())
check('A状态不污染B', '已连接' not in p5.header_status.text(), p5.header_status.text())

print('--- 结构断言 ---')
import inspect
src = inspect.getsource(OpsLogPanel)
check('断开按钮移入左工具条', 'tool_l.addWidget(self.disconnect_btn)' in src)
check('页头不再挂断开按钮', 'header.layout().addWidget(self.disconnect_btn' not in src)
check('切换标签刷新按钮', src.count('_refresh_header_session_status()') >= 5,
      str(src.count('_refresh_header_session_status()')))
check('新会话初始未连接', "'connected': False" in inspect.getsource(OpsLogPanel._new_session_dict))
check('掉线处理存在', '_on_terminal_dropped' in src and 'connection_closed.connect' in src)
check('失败不再弹模态', "show_error(self, 'PengTools', msg)" not in inspect.getsource(OpsLogPanel._connect_session))

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
