"""Session-identity regressions for the SSH terminal callbacks (offscreen, fake SSH)."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

app = QApplication([])

from panels.ops_log_panel import OpsLogPanel
from ui.ssh_terminal import SshTerminalWidget

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


def sess(sid, *, connected=False, gen=0, closed=False, widget=None):
    return {'session_id': sid, 'gen': gen, 'closed': closed,
            'client': object() if connected else None, 'connected': connected,
            'remote_entries': [], 'title': sid, 'widget': widget}


def make_panel(sessions, current=0):
    panel = SimpleNamespace(
        language='zh', header_status=QLabel(), toolbar_hint=QLabel(),
        connect_btn=QPushButton(), disconnect_btn=QPushButton(),
        _term_sessions=sessions, term_tabs=FakeTabs(len(sessions), current),
        _session_index_label=lambda i: str(i + 1),
        _console_append=lambda *a, **k: None,
        _apply_tab_chrome=lambda *a, **k: None,
    )
    panel._current_session = lambda: (
        panel._term_sessions[panel.term_tabs.currentIndex()]
        if 0 <= panel.term_tabs.currentIndex() < len(panel._term_sessions) else None)
    for name in ('_session_hint', '_find_session', '_resolve_target_session',
                 '_on_terminal_dropped', '_on_terminal_dropped_widget',
                 '_refresh_header_session_status', '_close_session_resources'):
        setattr(panel, name, (lambda n: lambda *a, **k: getattr(OpsLogPanel, n)(panel, *a, **k))(name))
    return panel


print('--- A/B/C：关闭 A 后 B 掉线不影响 C ---')
a, b, c = sess('A', connected=True), sess('B', connected=True), sess('C', connected=True)
panel = make_panel([a, b, c], current=2)
a['gen'] += 1
a['closed'] = True
panel._term_sessions.remove(a)
panel.term_tabs._count = 2
panel.term_tabs._current = 1
panel._on_terminal_dropped('B')
check('B 掉线后 B 未连接', not b['connected'] and b['client'] is None)
check('C 仍连接', c['connected'] and c['client'] is not None)
check('状态指向当前(C) 已连接', '已连接' in panel.header_status.text(), panel.header_status.text())

print('--- A 连接中关闭，成功后返回 ---')
a2 = sess('A2', connected=False, gen=2)
a2['connecting'] = True
panel2 = make_panel([a2, sess('B2', connected=True)], current=1)
a2['gen'] += 1
a2['closed'] = True
panel2._term_sessions.remove(a2)
panel2.term_tabs._count = 1
panel2.term_tabs._current = 0
check('关闭后旧代次失效', panel2._resolve_target_session('A2', 2) is None)
check('已连接代次仍有效', panel2._resolve_target_session('B2', 0) is not None)

print('--- 最后标签关闭后重建 ---')
w = SshTerminalWidget()
old = sess('OLD', connected=True, gen=3, widget=w)
p3 = make_panel([old])
old['gen'] += 1
old['closed'] = True
p3._term_sessions[0] = sess('NEW', connected=False, gen=0, widget=w)
check('旧 id 失效', p3._resolve_target_session('OLD', 3) is None)
check('新 id 有效', p3._resolve_target_session('NEW', 0) is not None)
check('新标签不继承连接', not p3._term_sessions[0]['connected'])

print('--- 旧连接掉线不覆盖新连接 ---')
fresh = sess('NEW2', connected=True)
p4 = make_panel([fresh])
p4._on_terminal_dropped('OLD2')
check('未知 id 掉线不影响新终端', fresh['connected'] and fresh['client'] is not None)

stale = sess('S', connected=True, gen=2)
p5 = make_panel([stale])
p5._on_terminal_dropped('S')
check('掉线后置为未连接', stale['connected'] is False)
p5._on_terminal_dropped('S')
check('重复掉线保持未连接', stale['connected'] is False and stale['client'] is None)

print('--- 正常 attach 不误报掉线 ---')
term = SshTerminalWidget()
fired = []
term.connection_closed.connect(lambda: fired.append(1))
term.detach()
check('普通 detach 不发信号', fired == [], str(fired))
term.detach(notify=True)
check('显式 notify 才发信号', len(fired) == 1, str(fired))

term2 = SshTerminalWidget()
fired2 = []
term2.connection_closed.connect(lambda: fired2.append(1))
term2.view._session_generation = 7
term2.view._on_shell_closed(6)
check('旧 shell 关闭不发信号', fired2 == [], str(fired2))
term2.view._on_shell_closed(7)
check('当前 shell 关闭才发信号', len(fired2) == 1, str(fired2))

print('--- 主动断开只影响目标终端 ---')
b1, c1 = sess('B1', connected=True), sess('C1', connected=True)
p6 = make_panel([b1, c1], current=0)
p6._close_session_resources(b1)
check('目标终端已清理', b1['client'] is None and not b1['connected'])
check('其他终端未受影响', c1['connected'] and c1['client'] is not None)

print('--- 延迟信号 ---')
b2, c2 = sess('B2x', connected=True), sess('C2x', connected=True)
p7 = make_panel([b2, c2], current=1)
QTimer.singleShot(0, lambda: p7._on_terminal_dropped('B2x'))
app.processEvents()
check('延迟掉线定位正确', not b2['connected'] and c2['connected'])

print()
print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
