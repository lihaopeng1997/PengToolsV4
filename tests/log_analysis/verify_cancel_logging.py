"""Isolated regression for collector stop and programming Agent callbacks."""

import ast
import io
import os
import stat
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT
sys.path[:0] = [str(ROOT), str(ROOT / 'tests' / 'ssh_session')]
import _isolation


def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected external connection')


def install_isolation(scratch):
    for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        os.environ[name] = str(scratch)
    config = ModuleType('config')
    config.local_data_dir = lambda: str(scratch)
    config.AGENT_DIR = str(scratch / 'agent')
    config.AGENT_WORKSPACES_DIR = str(scratch / 'workspaces')
    config.AGENT_INDEX_FILE = str(scratch / 'agent-index.json')
    config.ensure_config_dir = lambda: None
    sys.modules['config'] = config
    ssh = ModuleType('tools.ops_ssh')
    ssh.open_ssh_client = forbidden
    sys.modules[ssh.__name__] = ssh


class FakeSftp:
    def __init__(self, read_stat):
        self.stat = read_stat
        self.closed = threading.Event()

    def close(self):
        self.closed.set()

    def open(self, _path, _mode):
        return io.BytesIO(b'demo\n')

    def get_channel(self):
        return self

    def settimeout(self, _seconds):
        pass


class FakeClient:
    def __init__(self, sftp):
        self.sftp = sftp
        self.closed = threading.Event()

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed.set()


def stopped_during_stat(collector, make_error, *, should_log=False):
    entered = threading.Event()

    def read_stat(_path):
        entered.set()
        assert sftp.closed.wait(3)
        make_error()

    sftp = FakeSftp(read_stat)
    client = FakeClient(sftp)
    connections = []

    def connect(server, timeout_sec):
        connections.append(client)
        return client

    with patch.object(collector, 'log_failure') as failure, patch.object(collector, 'log_agent_event') as event:
        monitor = collector.Monitor({'id': 'fake'}, 'demo', '/synthetic.log', connector=connect)
        monitor.start()
        try:
            assert entered.wait(3), 'collector never entered stat'
        finally:
            monitor.stop()
            monitor.thread.join(3)
        assert not monitor.thread.is_alive() and monitor.state == '已停止'
        assert sftp.closed.is_set() and client.closed.is_set() and connections == [client]
        assert [call.args[1] for call in failure.call_args_list] == (
            ['LOG_MONITOR_READ_FAILED'] if should_log else []
        )
        retries = [call for call in event.call_args_list if call.args[0] == 'log_monitor_read_failed']
        assert len(retries) == len(monitor.notices) == int(should_log)
        ended = [call for call in event.call_args_list if call.args[0] == 'log_monitor_ended']
        assert len(ended) == 1 and ended[0].kwargs['stage'] == 'completed'


def raise_error(error):
    raise error


def raise_wrapped_eof():
    try:
        raise EOFError()
    except EOFError:
        raise RuntimeError('Server connection dropped: ')


def active_fault_reconnects(collector):
    reconnect = threading.Event()
    clients = []

    def connect(server, timeout_sec):
        if clients:
            sftp = FakeSftp(lambda _path: SimpleNamespace(st_mode=stat.S_IFREG, st_size=5))
        else:
            sftp = FakeSftp(lambda _path: raise_error(ConnectionResetError('synthetic failure')))
        client = FakeClient(sftp)
        clients.append(client)
        if len(clients) == 2:
            reconnect.set()
        return client

    with patch.object(collector, 'log_failure') as failure, patch.object(collector, 'log_agent_event') as event:
        monitor = collector.Monitor({'id': 'fake'}, 'demo', '/synthetic.log', connector=connect)
        monitor.start()
        try:
            assert reconnect.wait(5), 'active failure did not reconnect'
        finally:
            monitor.stop()
            monitor.thread.join(3)
        assert not monitor.thread.is_alive() and len(clients) == 2
        failure.assert_called_once()
        assert failure.call_args.args[1] == 'LOG_MONITOR_READ_FAILED'
        retries = [call for call in event.call_args_list if call.args[0] == 'log_monitor_read_failed']
        assert len(retries) == 1 and retries[0].kwargs['retry'] == 1
        assert len(monitor.notices) == 1


def check_agent(runtime, token_type, turn_type, workspace, *, cancel, break_callback):
    statuses = []
    events = []

    class Model:
        capability = 'text_only'

        def complete_turn(self, request, *, cancellation, on_delta):
            if cancel:
                cancellation.cancel()
            return turn_type(text='合成回答。')

    def event_cb(event):
        events.append(event.event_type)
        if break_callback == 'event' and (not cancel or event.event_type == 'cancelled'):
            raise RuntimeError('synthetic callback failure')

    def progress_cb(kind, content):
        if kind == 'status':
            statuses.append(content)
        if break_callback == 'progress' and content == 'cancelled':
            raise RuntimeError('synthetic callback failure')

    token = token_type()
    with patch.object(runtime, 'log_failure') as failure:
        runtime.run_agent_loop('合成问题', str(workspace), {}, [], [],
                               model_adapter=Model(), cancellation=token,
                               event_cb=event_cb, progress_cb=progress_cb)
    expected_terminal = 'cancelled' if cancel else 'completed'
    assert statuses[-1] == expected_terminal
    assert events[-1] == ('cancelled' if cancel else 'final')
    assert [call.args[1] for call in failure.call_args_list] == (
        ['AGENT_EVENT_CB_FAILED'] if break_callback else []
    )


def main(scratch):
    sys.path.insert(0, str(SOURCE))
    install_isolation(scratch)
    from tools import agent_diag, agent_runtime
    from tools.agent_core import AgentTurn, CancellationToken
    from tools.log_analysis import collector

    for module in (collector, agent_runtime):
        source_path = Path(module.__file__).resolve()
        assert source_path.is_relative_to(SOURCE)
        ast.parse(source_path.read_text(encoding='utf-8-sig'))
    agent_diag.set_log_dir(str(scratch))
    cases = (
        ('stop suppresses EOF caused by owned-channel closure', lambda: stopped_during_stat(
            collector, lambda: raise_error(EOFError()))),
        ('stop suppresses a wrapped Paramiko read EOF', lambda: stopped_during_stat(collector, raise_wrapped_eof)),
        ('stop suppresses Paramiko send on a closed socket', lambda: stopped_during_stat(
            collector, lambda: raise_error(OSError('Socket is closed')))),
        ('stop preserves a genuine concurrent permission failure', lambda: stopped_during_stat(
            collector, lambda: raise_error(PermissionError('synthetic denial')), should_log=True)),
        ('stop preserves a genuine concurrent connection reset', lambda: stopped_during_stat(
            collector, lambda: raise_error(ConnectionResetError('synthetic failure')), should_log=True)),
        ('active transport fault logs and reconnects', lambda: active_fault_reconnects(collector)),
        ('normal Agent cancellation invokes callbacks without failure logs', lambda: check_agent(
            agent_runtime, CancellationToken, AgentTurn, scratch, cancel=True, break_callback=None)),
        ('Agent event callback exception on cancellation remains visible', lambda: check_agent(
            agent_runtime, CancellationToken, AgentTurn, scratch, cancel=True, break_callback='event')),
        ('Agent progress callback exception on cancellation remains visible', lambda: check_agent(
            agent_runtime, CancellationToken, AgentTurn, scratch, cancel=True, break_callback='progress')),
        ('repeated event callback faults still log once per run', lambda: check_agent(
            agent_runtime, CancellationToken, AgentTurn, scratch, cancel=False, break_callback='event')),
    )
    for label, verify in cases:
        verify()
        print('PASS', label)
    _isolation.assert_untouched()
    print(f'RESULT: {len(cases)} passed, 0 failed')


if __name__ == '__main__':
    with patch('socket.create_connection', side_effect=forbidden):
        main(Path(_isolation.temp_dir()))
