"""Background worker must grey out both session buttons (offscreen, fake SSH)."""
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


class FakeWorker:
    def __init__(self, running=True):
        self._running = running

    def isRunning(self):
        return self._running


def sess(sid, *, connected=False, gen=0):
    return {'session_id': sid, 'gen': gen, 'closed': False,
            'client': object() if connected else None, 'connected': connected,
            'remote_entries': [], 'title': sid, 'widget': None}


def make_panel(sessions, current=0):
    panel = SimpleNamespace(
        language='zh',
        header_status=QLabel(),
        toolbar_hint=QLabel(),
        connect_btn=QPushButton(),
        disconnect_btn=QPushButton(),
        _term_sessions=sessions,
        term_tabs=FakeTabs(len(sessions), current),
        _session_index_label=lambda i: str(i + 1),
        _worker=None,
        _worker_purpose='',
    )
    panel._current_session = lambda: (
        panel._term_sessions[panel.term_tabs.currentIndex()]
        if 0 <= panel.term_tabs.currentIndex() < len(panel._term_sessions) else None)
    for name in ('_refresh_header_session_status', '_on_worker_finished', '_session_hint'):
        setattr(panel, name, (lambda n: lambda *a, **k: getattr(OpsLogPanel, n)(panel, *a, **k))(name))
    return panel


print('--- 后台任务运行中 ---')
connected = make_panel([sess('A', connected=True)], current=0)
connected._worker = FakeWorker(running=True)
connected._worker_purpose = 'file'
connected._refresh_header_session_status()
check('列目录/读日志中连接置灰', not connected.connect_btn.isEnabled())
check('列目录/读日志中断开置灰', not connected.disconnect_btn.isEnabled())
check('文案为后台任务进行中', '后台任务进行中' in connected.header_status.text(),
      connected.header_status.text())

print('--- 后台任务结束 ---')
connected._worker._running = False
connected._on_worker_finished()
check('结束后连接仍置灰（已连接）', not connected.connect_btn.isEnabled())
check('结束后断开恢复', connected.disconnect_btn.isEnabled())
check('结束后文案回到已连接', '已连接' in connected.header_status.text(),
      connected.header_status.text())

print('--- 未连接终端的行为一致 ---')
idle = make_panel([sess('B', connected=False)], current=0)
idle._worker = FakeWorker(running=True)
idle._worker_purpose = 'file'
idle._refresh_header_session_status()
check('未连接时也置灰两个按钮',
      not idle.connect_btn.isEnabled() and not idle.disconnect_btn.isEnabled())
idle._worker._running = False
idle._on_worker_finished()
check('结束后未连接可连接', idle.connect_btn.isEnabled())
check('结束后未连接不可断开', not idle.disconnect_btn.isEnabled())

print('--- 连接任务仍显示原文案 ---')
busy_connect = make_panel([sess('C', connected=False)], current=0)
busy_connect._worker = FakeWorker(running=True)
busy_connect._worker_purpose = 'connect'
busy_connect._refresh_header_session_status()
check('连接中显示其他会话正在连接', '其他会话正在连接' in busy_connect.header_status.text(),
      busy_connect.header_status.text())

print('--- 真实 QThread 走 _run_bg ---')
import threading
import time

from PyQt6.QtCore import QObject


class QPanel(QObject):
    """QObject so _Worker(fn, self) gets a valid parent."""

    def __init__(self, sessions):
        super().__init__()
        self.language = 'zh'
        self.header_status = QLabel()
        self.toolbar_hint = QLabel()
        self.session_status = QLabel()
        self.connect_btn = QPushButton()
        self.disconnect_btn = QPushButton()
        self._term_sessions = sessions
        self.term_tabs = FakeTabs(len(sessions), 0)
        self._worker = None
        self._worker_purpose = ''

    def _current_session(self):
        index = self.term_tabs.currentIndex()
        return self._term_sessions[index] if 0 <= index < len(self._term_sessions) else None

    def _console_append(self, *_args, **_kwargs):
        pass

    def _session_index_label(self, index=None):
        return max(1, int(index or 0) + 1)


real = QPanel([sess('A', connected=True)])
for name in ('_refresh_header_session_status', '_on_worker_finished', '_run_bg', '_session_hint'):
    setattr(real, name, (lambda n: lambda *a, **k: getattr(OpsLogPanel, n)(real, *a, **k))(name))

entered = threading.Event()
gate = threading.Event()


def blocking_job():
    entered.set()
    gate.wait(5)
    return 'listed'


real._run_bg(blocking_job, on_ok=lambda _r: None, busy_text='正在读取远端目录…')
check('_run_bg 立刻返回且 worker 在跑', real._worker is not None and real._worker.isRunning())
check('工作线程已进入 fn', entered.wait(2.0))

check('运行中连接置灰', not real.connect_btn.isEnabled())
check('运行中断开置灰', not real.disconnect_btn.isEnabled())
check('运行中文案为后台任务进行中', '后台任务进行中' in real.header_status.text(),
      real.header_status.text())

gate.set()
deadline = time.time() + 5
while time.time() < deadline and real._worker is not None:
    app.processEvents()
    time.sleep(0.02)
for _ in range(20):          # let the queued finished signal land
    app.processEvents()
    time.sleep(0.02)

check('worker 结束后槽位已清空', real._worker is None)
check('结束后断开恢复（终端已连接）', real.disconnect_btn.isEnabled())
check('结束后连接仍置灰（终端已连接）', not real.connect_btn.isEnabled())
check('结束后文案回到已连接', '已连接' in real.header_status.text(), real.header_status.text())

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
