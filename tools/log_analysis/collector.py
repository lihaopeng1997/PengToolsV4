"""A monitor owns its SSH/SFTP connection; never touches a terminal channel."""
from __future__ import annotations

import codecs
from collections import deque
import copy
import hashlib
import stat
import threading
import time
import uuid

from .events import EventBuffer
from tools.agent_diag import log_agent_event

CHUNK = 256 * 1024


class Cursor:
    def __init__(self, encoding='utf-8', checkpoint=None):
        codecs.lookup(encoding)
        self.encoding = encoding
        self.offset = None
        self.generation = 0
        self.anchor = ''
        self.partial = b''
        self.line_offset = 0
        self.skipping = False
        self.truncated_lines = 0
        self.committed_anchor = ''
        if checkpoint:
            self.offset = int(checkpoint['offset'])
            self.generation = int(checkpoint['generation'])
            self.anchor = str(checkpoint.get('anchor', ''))
            self.committed_anchor = self.anchor
            self.line_offset = self.offset

    def checkpoint(self):
        # Re-read an unfinished line on resume; no decoder state is persisted.
        return {'offset': self.line_offset if self.partial else self.offset,
                'generation': self.generation, 'anchor': self.committed_anchor}

    def read(self, sftp, path, emit, *, backfill=0):
        info = sftp.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('NOT_A_REGULAR_FILE')
        rotated = False
        with sftp.open(path, 'rb') as stream:
            if self.offset is None:
                self.offset = max(0, info.st_size - backfill)
                self.line_offset = self.offset
                if self.offset:
                    stream.seek(self.offset - 1)
                    self.skipping = stream.read(1) != b'\n'
            if self.offset > 0 and self.anchor:
                stream.seek(max(0, self.offset - 64))
                current = hashlib.sha256(stream.read(min(64, self.offset))).hexdigest()
                rotated = current != self.anchor
            if info.st_size < self.offset or rotated:
                self.generation += 1
                self.offset = self.line_offset = 0
                self.partial = b''
                self.skipping = False
                rotated = True
            stream.seek(self.offset)
            raw = stream.read(min(CHUNK, max(0, info.st_size - self.offset)))
            self.offset += len(raw)
            self.partial += raw
            while b'\n' in self.partial:
                line, self.partial = self.partial.split(b'\n', 1)
                if not self.skipping:
                    if len(line) > 65536:
                        self.truncated_lines += 1
                    emit(line[:65536].decode(self.encoding, 'replace'), self.line_offset, self.generation)
                self.line_offset += len(line) + 1
                self.skipping = False
            if len(self.partial) > 65536:
                self.line_offset += len(self.partial)
                self.partial = b''
                self.skipping = True
                self.truncated_lines += 1
            stream.seek(max(0, self.offset - 64))
            self.anchor = hashlib.sha256(stream.read(min(64, self.offset))).hexdigest()
            committed = self.line_offset if self.partial else self.offset
            stream.seek(max(0, committed - 64))
            self.committed_anchor = hashlib.sha256(stream.read(min(64, committed))).hexdigest()
        return {'bytes': len(raw), 'backlog': max(0, info.st_size - self.offset), 'rotated': rotated}


class Monitor:
    def __init__(self, server, service, path, *, encoding='utf-8', warn=False,
                 interval=2, backfill=0, connector=None, restored=None):
        if not path.startswith('/') or '\x00' in path:
            raise ValueError('请选择远端绝对文件路径')
        self.id = (restored or {}).get('id') or uuid.uuid4().hex
        self.server = copy.deepcopy(server)  # Never exposed to model or cache.
        self.server_id = str(server.get('id') or '')
        self.label = str(server.get('name') or self.server_id)
        self.service, self.path = str(service), path
        self.interval = max(1, min(30, interval))
        self.backfill = max(0, min(CHUNK, backfill))
        self.cursor = Cursor(encoding, (restored or {}).get('checkpoint'))
        self.buffer = EventBuffer(warn=warn)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.thread = None
        self.client = self.sftp = None
        self.state = '待启动'
        self.notices = deque(maxlen=30)
        self.bytes_read = int((restored or {}).get('bytes', 0))
        self.backlog = 0
        self.buffer.dropped = int((restored or {}).get('dropped_events', 0))
        self.last_received = None
        self.last_poll = None
        self.connector = connector
        self.started = (restored or {}).get('started') or time.time()
        self.notices.extend((restored or {}).get('notices', [])[-20:])

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='log-monitor-' + self.id[:8], daemon=True)
        self.thread.start()
        log_agent_event('log_monitor_started', task=self.id, stage='open')

    def _connect(self):
        from tools.ops_ssh import open_ssh_client
        self.state = '连接中'
        client = (self.connector or open_ssh_client)(self.server, timeout_sec=10)
        if self.stop_event.is_set():
            client.close()
            return
        self.client = client
        self.sftp = client.open_sftp()
        self.sftp.get_channel().settimeout(10)

    def _disconnect(self):
        for resource in (self.sftp, self.client):
            if resource:
                try:
                    resource.close()
                except Exception:
                    pass
        self.sftp = self.client = None

    def _run(self):
        failures = 0
        try:
            while not self.stop_event.is_set():
                if self.paused.is_set():
                    self.state = '已暂停'
                    with self.lock:
                        self.buffer.flush()
                    self.stop_event.wait(.2)
                    continue
                try:
                    if self.sftp is None:
                        self._connect()
                    if self.stop_event.is_set():
                        break
                    with self.lock:
                        result = self.cursor.read(self.sftp, self.path, self.buffer.line, backfill=self.backfill)
                        self.buffer.idle()
                        self.bytes_read += result['bytes']
                        self.backlog = result['backlog']
                        self.last_poll = time.time()
                        if result['bytes']:
                            self.last_received = time.time()
                        if result['rotated']:
                            self.notices.append('检测到文件变化/截短；已开始新代次，轮转间隙可能缺失。')
                            log_agent_event('log_file_generation_changed', task=self.id, offset=self.cursor.offset)
                        self.state = '监控中' if result['bytes'] else '监控中 · 暂无新增'
                    failures = 0
                    self.stop_event.wait(self.interval)
                except Exception as error:
                    self._disconnect()
                    failures += 1
                    log_agent_event('log_monitor_read_failed', task=self.id, retry=failures, error_type=type(error).__name__)
                    self.state = '重连中 · ' + type(error).__name__
                    with self.lock:
                        self.notices.append('读取中断，覆盖可能存在缺口：' + type(error).__name__)
                    if failures >= 5 or type(error).__name__ == 'HostKeyChangedError':
                        self.state = '连接/读取失败；请核对文件、权限、SFTP、主机指纹后重试'
                        return
                    self.stop_event.wait(min(30, 2 ** failures))
        finally:
            self._disconnect()
            with self.lock:
                self.buffer.flush()
            if self.stop_event.is_set():
                self.state = '已停止'
            log_agent_event('log_monitor_ended', task=self.id, received_bytes=self.bytes_read,
                            returned=len(self.buffer.events), stage='completed' if self.stop_event.is_set() else 'failed')

    def stop(self):
        self.stop_event.set()
        # Closing the owned channel interrupts outstanding I/O, never a terminal.
        self._disconnect()

    def read_range(self, offset, limit, checkpoint, cancellation):
        """A separate short-lived connection for bounded Agent evidence reads."""
        from tools.ops_ssh import open_ssh_client
        from .events import Redactor
        if cancellation.is_cancelled():
            raise ValueError('CANCELLED')
        client = (self.connector or open_ssh_client)(self.server, timeout_sec=10)
        try:
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(10)
            with sftp.open(self.path, 'rb') as stream:
                boundary = checkpoint.get('offset')
                if boundary is None or offset > boundary:
                    raise ValueError('OUTSIDE_CAPTURE_BOUNDARY')
                if boundary:
                    stream.seek(max(0, boundary - 64))
                    anchor = hashlib.sha256(stream.read(min(64, boundary))).hexdigest()
                    if checkpoint.get('anchor') and anchor != checkpoint['anchor']:
                        raise ValueError('SOURCE_CHANGED_RECAPTURE_REQUIRED')
                stream.seek(offset)
                raw = stream.read(min(limit, boundary - offset))
            redactor = Redactor()
            lines = raw.decode(self.cursor.encoding, 'replace').splitlines()
            # Mid-file partial lines are explicitly excluded from evidence.
            if offset and lines:
                lines = lines[1:]
            if raw and not raw.endswith(b'\n') and lines:
                lines = lines[:-1]
            return {'start_offset': offset, 'next_offset': offset + len(raw),
                    'generation': checkpoint['generation'], 'path': self.path,
                    'excerpt': '\n'.join(redactor.line(line) for line in lines),
                    'notice': '有界字节区间，首尾半行已省略；不是完整时间段。'}
        finally:
            client.close()

    def snapshot(self, blocking=True):
        if not self.lock.acquire(blocking=blocking):
            return None
        try:
            return {'id': self.id, 'server_id': self.server_id, 'label': self.label, 'mode': 'live',
                    'service': self.service, 'path': self.path, 'encoding': self.cursor.encoding,
                    'warn': self.buffer.warn, 'interval': self.interval, 'started': self.started,
                    'state': self.state, 'bytes': self.bytes_read, 'backlog': self.backlog,
                    'last_received': self.last_received, 'last_poll': self.last_poll, 'checkpoint': self.cursor.checkpoint(),
                    'events': self.buffer.snapshot(), 'notices': list(self.notices),
                    'dropped_events': self.buffer.dropped, 'truncated_lines': self.cursor.truncated_lines,
                    'recent': list(self.buffer.recent)}
        finally:
            self.lock.release()
