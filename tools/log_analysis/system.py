"""Read-only Linux sampling. Only host-owned commands, never model shell strings."""
from collections import deque
import copy
import json
import re
import shlex
import threading
import time
import uuid

from .collector import Monitor
from .events import Event


def run_readonly(client, command, cancellation, timeout=8, limit=65536):
    """Drain both channel streams with an absolute deadline and a shared output limit."""
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        raise ConnectionError('SSH_DISCONNECTED')
    channel = transport.open_session(timeout=timeout)
    out, err = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        channel.settimeout(timeout)
        channel.exec_command('LC_ALL=C ' + command)
        while True:
            if cancellation.is_set():
                raise InterruptedError('CANCELLED')
            if time.monotonic() >= deadline:
                raise TimeoutError('COMMAND_DEADLINE')
            for ready, recv, target in ((channel.recv_ready, channel.recv, out),
                                        (channel.recv_stderr_ready, channel.recv_stderr, err)):
                if ready():
                    data = recv(4096)
                    target.extend(data)
                    if len(out) + len(err) > limit:
                        raise ValueError('OUTPUT_LIMIT')
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            cancellation.wait(.02)
        return out.decode('utf-8', 'replace'), channel.recv_exit_status()
    finally:
        channel.close()


class LinuxSampler:
    def __init__(self, units=(), processes=(), ports=(), mounts=('/',), executor=run_readonly):
        self.units = self._names(units, r'[A-Za-z0-9_.@:-]{1,128}', '服务')
        self.processes = self._names(processes, r'[A-Za-z0-9_.-]{1,64}', '进程')
        self.ports = tuple(dict.fromkeys(int(p) for p in ports))
        if len(self.ports) > 16 or any(not 1 <= p <= 65535 for p in self.ports):
            raise ValueError('最多16个端口，范围1～65535')
        self.mounts = tuple(dict.fromkeys(mounts))
        if len(self.mounts) > 8 or any(not p.startswith('/') or any(c in p for c in '\x00\n\r') for p in self.mounts):
            raise ValueError('最多8个绝对磁盘路径')
        self.executor, self.previous = executor, None
        self.disk_cache, self.disk_errors, self.disk_at = [], {}, 0

    @staticmethod
    def _names(values, pattern, label):
        values = tuple(dict.fromkeys(values))
        if len(values) > 16 or any(not re.fullmatch(pattern, value) for value in values):
            raise ValueError(label + '名称格式无效，最多16项')
        return values

    def unknown_sample(self, error):
        return {'time': time.time(), 'cpu_percent': None, 'memory_available_percent': None,
                'load': None, 'disks': [{'path': p, 'used_percent': None, 'inode_percent': None} for p in self.mounts],
                'units': {u: None for u in self.units}, 'processes': {p: None for p in self.processes},
                'ports': {str(p): None for p in self.ports}, 'top_processes': [], 'errors': {'capture': error}}

    def sample(self, client, cancellation):
        deadline = time.monotonic() + 30
        result = {'time': time.time(), 'cpu_percent': None, 'memory_available_percent': None,
                  'load': None, 'disks': [], 'units': {u: None for u in self.units},
                  'processes': {p: None for p in self.processes}, 'ports': {str(p): None for p in self.ports},
                  'top_processes': [], 'errors': {}}
        def query(key, command, *, allow_empty=False):
            if cancellation.is_set():
                raise InterruptedError()
            if time.monotonic() >= deadline:
                result['errors'][key] = '本轮采样时间预算已用完'
                return None
            try:
                text, code = self.executor(client, command, cancellation)
                if code != 0:
                    result['errors'][key] = f'查询失败，退出码{code}（可能不支持或无权限）'
                    return None
                if not allow_empty and not text.strip():
                    result['errors'][key] = '命令未返回有效内容，指标未知'
                    return None
                return text
            except InterruptedError:
                raise
            except Exception as error:
                result['errors'][key] = type(error).__name__
                return None

        raw = query('cpu', 'cat /proc/stat')
        if raw:
            try:
                values = [int(x) for x in raw.splitlines()[0].split()[1:9]]
                total, idle = sum(values), values[3] + values[4]
                if self.previous:
                    dt, di = total - self.previous[0], idle - self.previous[1]
                    if dt > 0 and 0 <= di <= dt:
                        result['cpu_percent'] = round(100 * (dt - di) / dt, 2)
                self.previous = total, idle
                result['cpu_count'] = sum(bool(re.match(r'^cpu\d+ ', line)) for line in raw.splitlines())
            except (ValueError, IndexError):
                self.previous = None
                result['errors']['cpu'] = '无法解析CPU计数'
        else:
            self.previous = None
        raw = query('memory', 'cat /proc/meminfo')
        if raw:
            mem = {m.group(1): int(m.group(2)) for m in re.finditer(r'^(\w+):\s+(\d+)', raw, re.M)}
            if mem.get('MemTotal') and 'MemAvailable' in mem:
                result['memory_available_percent'] = round(100 * mem['MemAvailable'] / mem['MemTotal'], 2)
                result['memory_available_kib'] = mem['MemAvailable']
                result['memory_total_kib'] = mem['MemTotal']
            else:
                result['errors']['memory'] = '缺少MemAvailable或MemTotal'
            result['swap_used_kib'] = max(0, mem.get('SwapTotal', 0) - mem.get('SwapFree', 0))
        raw = query('load', 'cat /proc/loadavg')
        if raw:
            try:
                result['load'] = [float(x) for x in raw.split()[:3]]
            except ValueError:
                result['errors']['load'] = '无法解析负载'
        refresh_disks = time.monotonic() - self.disk_at >= 30 or not self.disk_cache
        for path in self.mounts if refresh_disks else ():
            disk = {'path': path, 'used_percent': None, 'inode_percent': None}
            disk['observed_at'] = time.time()
            for flag, key in (('-Pk', 'used_percent'), ('-Pi', 'inode_percent')):
                raw = query('disk:' + path + ':' + key, f'df {flag} -- {shlex.quote(path)}')
                if raw:
                    match = re.search(r'\s(\d+)%\s', raw.splitlines()[-1])
                    if match:
                        disk[key] = int(match.group(1))
                    else:
                        result['errors']['disk:' + path + ':' + key] = '无法解析磁盘使用率'
            result['disks'].append(disk)
        if refresh_disks:
            self.disk_cache = copy.deepcopy(result['disks'])
            self.disk_errors = {k: v for k, v in result['errors'].items() if k.startswith('disk:')}
            self.disk_at = time.monotonic()
        else:
            result['disks'] = copy.deepcopy(self.disk_cache)
            result['errors'].update(self.disk_errors)
        raw = query('processes', 'ps -eo pid=,comm=,pcpu=,rss= --sort=-pcpu')
        if raw is not None:
            rows = []
            invalid = False
            for line in raw.splitlines():
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) == 4:
                    try:
                        rows.append({'pid': int(parts[0]), 'name': parts[1], 'cpu_percent': float(parts[2]), 'rss_kib': int(parts[3])})
                    except ValueError:
                        invalid = True
                else:
                    invalid = True
            if not rows or invalid:
                result['errors']['processes'] = '未取得可解析的进程列表，进程状态未知'
            else:
                result['top_processes'] = rows[:20]
                for name in self.processes:
                    result['processes'][name] = [row for row in rows if row['name'] == name][:32]
        for unit in self.units:
            raw = query('unit:' + unit, 'systemctl show --no-pager --property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus -- ' + shlex.quote(unit))
            if raw is not None:
                values = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
                if values.get('ActiveState'):
                    result['units'][unit] = values
                else:
                    result['errors']['unit:' + unit] = '缺少ActiveState，服务状态未知'
        if self.ports:
            raw = query('ports', 'ss -H -lntu', allow_empty=True)
            if raw is not None:
                found = set()
                invalid = False
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) >= 5:
                        value = parts[4].rsplit(':', 1)[-1]
                        if value.isdigit():
                            found.add(int(value))
                        else:
                            invalid = True
                    else:
                        invalid = True
                if invalid:
                    result['errors']['ports'] = '无法解析监听列表，端口状态未知'
                else:
                    result['ports'] = {str(p): p in found for p in self.ports}
        return result


class AlertRules:
    def __init__(self, cpu=85, memory=10, disk=85, duration=60):
        self.cpu, self.memory, self.disk, self.duration = cpu, memory, disk, duration
        self.pending, self.active, self.recovering = {}, {}, {}
        self.disk_observations = {}

    def evaluate(self, sample, now):
        values = {'cpu': (sample.get('cpu_percent'), self.cpu, self.cpu - 15, False),
                  'memory': (sample.get('memory_available_percent'), self.memory, self.memory + 5, True)}
        for disk in sample['disks']:
            for field in ('used_percent', 'inode_percent'):
                values['disk:' + disk['path'] + ':' + field] = (disk[field], self.disk, self.disk - 5, False)
        for name, value in sample['units'].items():
            state = value.get('ActiveState') if value else None
            values['unit:' + name] = (int(state != 'active') if state else None, 1, 0, False)
        for name, rows in sample['processes'].items():
            values['process:' + name] = (int(not rows) if rows is not None else None, 1, 0, False)
        for port, listening in sample['ports'].items():
            values['port:' + port] = (int(not listening) if listening is not None else None, 1, 0, False)
        transitions = []
        disk_times = {'disk:' + d['path'] + ':' + field: d.get('observed_at')
                      for d in sample['disks'] for field in ('used_percent', 'inode_percent')}
        for key in set(values) | set(self.pending) | set(self.active):
            value, high, low, inverted = values.get(key, (None, 0, 0, False))
            if value is None:
                self.pending.pop(key, None)
                self.recovering.pop(key, None)
                continue
            observed = disk_times.get(key)
            if observed is not None:
                if self.disk_observations.get(key) == observed:
                    continue
                self.disk_observations[key] = observed
            bad = value <= high if inverted else value >= high
            good = value >= low if inverted else value <= low
            if key not in self.active:
                self.recovering.pop(key, None)
                if bad:
                    since, count = self.pending.get(key, (now, 0))
                    self.pending[key] = since, count + 1
                    instant = key.startswith(('unit:', 'process:', 'port:'))
                    if count >= 1 and (instant or now - since >= self.duration):
                        self.active[key] = True
                        transitions.append((key, '异常持续', value))
                        self.pending.pop(key, None)
                else:
                    self.pending.pop(key, None)
            elif good:
                since, count = self.recovering.get(key, (now, 0))
                self.recovering[key] = since, count + 1
                if count >= 1 and (key.startswith(('unit:', 'process:', 'port:')) or now - since >= self.duration):
                    self.active.pop(key, None)
                    self.recovering.pop(key, None)
                    transitions.append((key, '已恢复', value))
            else:
                self.recovering.pop(key, None)
        return transitions


class SystemMonitor(Monitor):
    def __init__(self, server, service='', path='', *, units=(), processes=(), ports=(), mounts=('/',),
                 cpu=85, memory=10, disk=85, duration=60, executor=run_readonly, **kwargs):
        super().__init__(server, service or 'Linux系统', path or '/system', **kwargs)
        self.path = path
        self.interval = max(5, self.interval)
        self.sampler = LinuxSampler(units, processes, ports, mounts, executor)
        self.rules = AlertRules(cpu, memory, disk, duration)
        self.metrics = deque(maxlen=360)
        self.alerts = {}
        self.last_poll = None
        self.log_task = Monitor(server, service, path, **kwargs) if path else None

    def start(self):
        if self.log_task:
            self.log_task.start()
        super().start()

    def _run(self):
        from tools.ops_ssh import open_ssh_client
        failures = 0
        try:
            while not self.stop_event.is_set():
                if self.paused.is_set():
                    self.state = '已暂停采集'
                    self.sampler.previous = None
                    self.rules.pending.clear()
                    self.rules.recovering.clear()
                    self.stop_event.wait(.2)
                    continue
                try:
                    self.state = '连接中' if self.client is None else '采样中'
                    if self.client is None:
                        self.client = (self.connector or open_ssh_client)(self.server, timeout_sec=8)
                    sample = self.sampler.sample(self.client, self.stop_event)
                    if self.stop_event.is_set():
                        break
                    transport = self.client.get_transport() if hasattr(self.client, 'get_transport') else None
                    if hasattr(self.client, 'get_transport') and (transport is None or not transport.is_active()):
                        raise ConnectionError('SSH_DISCONNECTED')
                    failures = 0
                    now = time.time()
                    with self.lock:
                        self.metrics.append(sample)
                        self.last_poll = now
                        self.last_received = now
                        for key, state, value in self.rules.evaluate(sample, time.monotonic()):
                            evidence = json.dumps({'metric': key, 'state': state, 'value': value, 'sample': sample}, ensure_ascii=False)
                            event = Event(uuid.uuid4().hex[:12], 0, 0, f'{state} · {key}',
                                          self.buffer.redactor.clean(evidence)[:32000], now, now)
                            event._metric_key = key
                            if len(self.buffer.events) == self.buffer.limit:
                                self.buffer.dropped += 1
                            self.buffer.events.append(event)
                            self.alerts[key] = state
                        self.state = '监控中' if not sample['errors'] else '监控中 · 部分指标不可用'
                except InterruptedError:
                    break
                except Exception as error:
                    if self.stop_event.is_set():
                        break
                    self._disconnect()
                    self.sampler.previous = None
                    self.sampler.disk_cache.clear()
                    self.rules.pending.clear()
                    self.rules.recovering.clear()
                    with self.lock:
                        self.state = '连接失败 · 指标未知 · ' + type(error).__name__
                        self.metrics.append(self.sampler.unknown_sample(type(error).__name__))
                        self.notices.append('采样中断，不能据此判断服务已停止：' + type(error).__name__)
                    failures += 1
                    if failures >= 5 or type(error).__name__ == 'HostKeyChangedError':
                        break
                self.stop_event.wait(min(30, max(self.interval, 2 ** failures)))
        finally:
            self._disconnect()
            if self.stop_event.is_set():
                self.state = '已停止监控 · 可继续分析证据'

    def stop(self):
        if self.log_task:
            self.log_task.stop()
        super().stop()

    def set_paused(self, paused):
        for task in (self, self.log_task):
            if task:
                task.paused.set() if paused else task.paused.clear()

    def read_range(self, *args):
        if not self.log_task:
            raise ValueError('此任务没有绑定日志文件')
        return self.log_task.read_range(*args)

    def probe(self, unit, cancellation):
        """A model may inspect only the journals of the user's selected units."""
        from tools.ops_ssh import open_ssh_client
        from .events import Redactor
        if unit not in self.sampler.units:
            raise ValueError('服务不在当前监控范围内')
        if cancellation.is_cancelled():
            raise InterruptedError()
        class CancelAdapter:
            def is_set(self):
                return cancellation.is_cancelled()
            def wait(self, seconds):
                deadline = time.monotonic() + seconds
                while not self.is_set() and time.monotonic() < deadline:
                    time.sleep(.01)
        client = (self.connector or open_ssh_client)(self.server, timeout_sec=8)
        try:
            text, code = self.sampler.executor(client, 'journalctl --no-pager -n 80 -o short-iso -u ' + shlex.quote(unit), CancelAdapter())
            if code:
                raise ValueError('journal不可用或无权限，未取得日志')
            redactor = Redactor()
            return {'unit': unit, 'observed_at': time.time(), 'coverage': '该服务当前journal末尾最多80行，非故障时段全量',
                    'text': '\n'.join(redactor.line(line) for line in text.splitlines())}
        finally:
            client.close()

    def snapshot(self, blocking=True):
        value = super().snapshot(blocking)
        if value is None:
            return None
        with self.lock:
            value.update(mode='system', metrics=copy.deepcopy(list(self.metrics)), alerts=dict(self.alerts),
                         last_poll=self.last_poll, targets={'units': self.sampler.units, 'processes': self.sampler.processes,
                            'ports': self.sampler.ports, 'mounts': self.sampler.mounts},
                         rules={'cpu': self.rules.cpu, 'memory': self.rules.memory, 'disk': self.rules.disk, 'duration': self.rules.duration})
            for event in value['events']:
                event['source'] = 'Linux系统采样'
        if self.log_task:
            log = self.log_task.snapshot(blocking)
            if log:
                value['events'] += log['events']
                value['recent'], value['checkpoint'] = log['recent'], log['checkpoint']
                value['notices'] += log['notices'][-5:]
                value['log_state'] = log['state']
                value['dropped_events'] += log['dropped_events']
                value['backlog'], value['bytes'] = log['backlog'], log['bytes']
        return value
