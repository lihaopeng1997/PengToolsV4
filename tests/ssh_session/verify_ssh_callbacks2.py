"""Remaining SSH callback fixes: widget rebind, worker refresh, query cancel."""
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


class FakeTimer:
    def __init__(self):
        self.running = False

    def start(self, _ms):
        self.running = True

    def stop(self):
        self.running = False


class FakeWorker:
    def __init__(self, running=True):
        self._running = running

    def isRunning(self):
        return self._running


def make_panel(sessions, current=0):
    panel = SimpleNamespace(
        language='zh',
        header_status=QLabel(),
        toolbar_hint=QLabel(),
        status_label=QLabel(),
        connect_btn=QPushButton(),
        disconnect_btn=QPushButton(),
        exec_query_btn=QPushButton(),
        _term_sessions=sessions,
        term_tabs=FakeTabs(len(sessions), current),
        _session_index_label=lambda i: str(i + 1),
        _console_append=lambda *a, **k: None,
        _apply_tab_chrome=lambda *a, **k: None,
        _query_wait_term=None,
        _query_wait_timer=FakeTimer(),
        _worker=None,
        _worker_purpose='',
        _refresh_exec_query_button=lambda: None,
    )
    panel._current_session = lambda: (
        panel._term_sessions[panel.term_tabs.currentIndex()]
        if 0 <= panel.term_tabs.currentIndex() < len(panel._term_sessions) else None)
    for name in ('_session_hint', '_find_session', '_resolve_target_session',
                 '_on_terminal_dropped', '_on_terminal_dropped_widget',
                 '_on_worker_finished', '_refresh_header_session_status',
                 '_finish_query_wait', '_close_session_resources',
                 '_query_response_received', '_query_connection_closed'):
        setattr(panel, name, (lambda n: lambda *a, **k: getattr(OpsLogPanel, n)(panel, *a, **k))(name))
    return panel


def sess(sid, widget, *, connected=False, gen=0):
    return {'session_id': sid, 'gen': gen, 'closed': False, 'widget': widget,
            'client': object() if connected else None, 'connected': connected,
            'remote_entries': [], 'title': sid}


print('--- 1. 最后标签重建后掉线仍生效 ---')
w = SshTerminalWidget()
old = sess('OLD', w, connected=True, gen=2)
panel = make_panel([old])
# rebuild the last tab the way _close_term_tab does: same widget, new session
panel._term_sessions[0] = sess('NEW', w, connected=True, gen=0)
panel._term_sessions[0]['connecting'] = False
panel._on_terminal_dropped_widget(w)
check('重建后掉线命中新会话', panel._term_sessions[0]['connected'] is False)
check('状态变为未连接', '未连接' in panel.header_status.text(), panel.header_status.text())
check('提示远端已断开', '远端连接已断开' in panel.toolbar_hint.text(), panel.toolbar_hint.text())

panel._term_sessions[0]['connected'] = True
panel._term_sessions[0]['client'] = object()
panel._on_terminal_dropped('OLD')
check('旧 id 回调无效', panel._term_sessions[0]['connected'] is True)

print('--- 2. worker 结束后自动刷新按钮 ---')
w2 = SshTerminalWidget()
a = sess('A', w2, connected=False)
b = sess('B', SshTerminalWidget(), connected=False)
p2 = make_panel([a, b], current=1)
p2._worker = FakeWorker(running=True)
p2._worker_purpose = 'connect'
p2._refresh_header_session_status()
check('连接中被禁用', not p2.connect_btn.isEnabled())
check('文案为正在连接', '正在连接' in p2.header_status.text(), p2.header_status.text())

# A finishes while B is on screen -> the shared slot clears and B's button returns
p2._worker._running = False
p2._on_worker_finished()
check('结束后 B 连接按钮恢复', p2.connect_btn.isEnabled())
check('文案回到未连接', '未连接' in p2.header_status.text(), p2.header_status.text())

p2._worker = FakeWorker(running=True)
p2._worker_purpose = 'file'
p2._refresh_header_session_status()
check('文件任务文案区分', '后台任务进行中' in p2.header_status.text(), p2.header_status.text())
check('文件任务时连接按钮禁用', not p2.connect_btn.isEnabled())
p2._worker._running = False
p2._on_worker_finished()
check('文件任务结束后恢复', p2.connect_btn.isEnabled())

print('--- 3. 断开/关闭立即结束查询等待 ---')
w3 = SshTerminalWidget()
c = sess('C', w3, connected=True)
p3 = make_panel([c])
p3._query_wait_term = w3
p3._query_wait_timer.start(30000)
p3.exec_query_btn.setText('   等待回显…')
p3.exec_query_btn.setEnabled(False)
p3._close_session_resources(c)
check('等待已结束', p3._query_wait_term is None)
check('定时器已停', p3._query_wait_timer.running is False)
check('查询按钮恢复', p3.exec_query_btn.text() == '执行查询', p3.exec_query_btn.text())
check('提示为会话已断开', '会话已断开' in p3.status_label.text(), p3.status_label.text())
check('未误报远端断开', '远端' not in p3.status_label.text(), p3.status_label.text())

print('--- 3b. 其他终端断开不影响本终端查询 ---')
w4, w5 = SshTerminalWidget(), SshTerminalWidget()
d = sess('D', w4, connected=True)
e = sess('E', w5, connected=True)
p4 = make_panel([d, e])
p4._query_wait_term = w5           # query waits on E
p4._close_session_resources(d)     # disconnect D
check('无关断开不终止查询', p4._query_wait_term is w5)

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
