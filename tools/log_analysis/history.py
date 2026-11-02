"""Finite local/SFTP scans with frozen boundaries and a bounded redacted index."""
from collections import deque
from contextlib import ExitStack, contextmanager
import copy
import gzip
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import time
import re
from datetime import datetime, timedelta, timezone

from .collector import Monitor
from .events import EventBuffer


class FrozenReader(io.RawIOBase):
    def __init__(self, stream, end):
        self.stream, self.end = stream, end

    def readable(self):
        return True

    def readinto(self, target):
        raw = self.stream.read(min(len(target), max(0, self.end - self.stream.tell())))
        target[:len(raw)] = raw
        return len(raw)


class HistoryScan(Monitor):
    mode = 'history'

    def __init__(self, server, service, paths, *, local=False, encoding='utf-8', warn=False,
                 connector=None, max_bytes=200 * 1024 * 1024, tail_lines=0,
                 time_start='', time_end='', timezone_minutes=480):
        paths = list(dict.fromkeys(str(p) for p in paths if str(p).strip()))
        if not 1 <= len(paths) <= 20:
            raise ValueError('请选择1～20个日志文件')
        if not local and any(not p.startswith('/') or '\x00' in p for p in paths):
            raise ValueError('远端日志必须使用绝对路径')
        super().__init__(server or {'id': 'local', 'name': '本地文件'}, service,
                         paths[0] if not local else '/local-history', encoding=encoding,
                         warn=warn, connector=connector)
        self.path = '\n'.join(paths)
        self.paths, self.local = paths, local
        self.max_bytes = max(1024, min(int(max_bytes), 200 * 1024 * 1024))
        self.tail_lines = max(0, min(int(tail_lines), 100000))
        self.timezone_minutes = int(timezone_minutes)
        if not -720 <= self.timezone_minutes <= 840:
            raise ValueError('日志时区超出范围')
        self.log_timezone = timezone(timedelta(minutes=self.timezone_minutes))
        self.time_start = self._parse_bound(time_start)
        self.time_end = self._parse_bound(time_end)
        if bool(self.time_start) != bool(self.time_end) or (self.time_start and self.time_start > self.time_end):
            raise ValueError('请填写有效的起止时间，结束不能早于开始')
        if self.tail_lines and self.time_start:
            raise ValueError('时间范围与尾部行数不能同时启用')
        self.last_log_time = None
        self.time_source = None
        self.unknown_time_lines = 0
        self.filtered_lines = 0
        self.buffer = EventBuffer(warn=warn, limit=2000)
        self.sources = []
        self.lines_read = 0
        self.index_bytes = 0
        self.complete = False
        self.temp = tempfile.TemporaryDirectory(prefix='pengtools-evidence-')
        self.db_path = str(Path(self.temp.name) / 'redacted.sqlite')
        with self._index() as db:
            db.execute('CREATE TABLE lines (id INTEGER PRIMARY KEY, source INTEGER, offset INTEGER, text TEXT)')

    @contextmanager
    def _index(self):
        db = sqlite3.connect(self.db_path, timeout=3)
        try:
            yield db
        finally:
            try:
                db.commit()
            finally:
                db.close()

    def _parse_bound(self, text):
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text.strip())
        except (ValueError, AttributeError):
            raise ValueError('时间格式应为 YYYY-MM-DD HH:MM:SS') from None
        return value.replace(tzinfo=self.log_timezone) if value.tzinfo is None else value

    def _log_time(self, text):
        match = re.match(r'^\s*\[?(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)', text)
        if match:
            try:
                return self._parse_bound(match[1].replace('/', '-').replace(',', '.'))
            except ValueError:
                pass
        return None

    def _identity(self, path):
        info = os.stat(path) if self.local else self.sftp.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('请选择具体日志文件，目录不能直接分析')
        return info

    def _open(self, path):
        return open(path, 'rb') if self.local else self.sftp.open(path, 'rb')

    def _run(self):
        try:
            self.state = '校验文件'
            if not self.local:
                self._connect()
            with ExitStack() as stack:
                files = []
                for index, path in enumerate(self.paths):
                    if self.stop_event.is_set():
                        raise InterruptedError()
                    info = self._identity(path)
                    stream = stack.enter_context(self._open(path))
                    stream.seek(max(0, info.st_size - 64))
                    anchor = hashlib.sha256(stream.read(min(64, info.st_size))).hexdigest()
                    stream.seek(0)
                    files.append((stream, info, anchor))
                    with self.lock:
                        self.sources.append({'id': index, 'path': path, 'size': info.st_size,
                                             'read_bytes': 0, 'state': '待读取',
                                             'offset_kind': '解压流字节' if path.lower().endswith('.gz') else '文件字节'})
                with self._index() as db:
                    for index, (stream, info, anchor) in enumerate(files):
                        with self.lock:
                            self.state = f'读取文件 {index + 1}/{len(files)}'
                            self.sources[index]['state'] = '读取中'
                        limited = io.BufferedReader(FrozenReader(stream, info.st_size))
                        decoded = gzip.GzipFile(fileobj=limited) if self.paths[index].lower().endswith('.gz') else limited
                        tail = deque(maxlen=self.tail_lines or 1)
                        offset = 0
                        while True:
                            if self.stop_event.is_set():
                                raise InterruptedError()
                            raw = decoded.readline(65537)
                            if not raw:
                                break
                            self.bytes_read += len(raw)
                            if self.bytes_read > self.max_bytes:
                                raise ValueError('达到读取额度，结果仅覆盖已处理部分；请缩小文件范围后继续')
                            long_line = len(raw) > 65536
                            if long_line:
                                self.cursor.truncated_lines += 1
                            text = raw[:65536].decode(self.cursor.encoding, 'replace')
                            start = offset
                            offset += len(raw)
                            while long_line and not raw.endswith(b'\n'):
                                if self.stop_event.is_set():
                                    raise InterruptedError()
                                raw = decoded.readline(65537)
                                self.bytes_read += len(raw)
                                offset += len(raw)
                                if self.bytes_read > self.max_bytes:
                                    raise ValueError('达到读取额度，长行未完整读取')
                                if not raw:
                                    break
                            if self.tail_lines:
                                tail.append((text, start))
                            else:
                                self._accept(db, index, text, start)
                            with self.lock:
                                self.sources[index]['read_bytes'] = offset
                                self.last_received = time.time()
                        for text, start in tail if self.tail_lines else ():
                            self._accept(db, index, text, start)
                        with self.lock:
                            self.buffer.flush()
                        current = self._identity(self.paths[index])
                        with self._open(self.paths[index]) as check:
                            check.seek(max(0, info.st_size - 64))
                            now_anchor = hashlib.sha256(check.read(min(64, info.st_size))).hexdigest()
                        if (current.st_size < info.st_size or now_anchor != anchor or
                            (self.local and current.st_ino != info.st_ino) or
                            (current.st_size == info.st_size and current.st_mtime != info.st_mtime)):
                            raise ValueError('源文件读取期间发生变化；证据不保证一致，请重新选择不可变文件')
                        with self.lock:
                            self.sources[index]['state'] = '已读取启动时范围'
                        db.commit()
                self.complete = True
                self.state = '读取完成 · 可继续提问'
        except InterruptedError:
            self.state = '已取消读取 · 保留部分证据'
        except Exception as error:
            self.state = '已取消读取 · 保留部分证据' if self.stop_event.is_set() else '部分读取 / 失败'
            self.notices.append(str(error) if isinstance(error, ValueError) else '读取失败：' + type(error).__name__)
        finally:
            with self.lock:
                self.buffer.flush()
            self._disconnect()

    def _accept(self, db, source, text, offset):
        with self.lock:
            if self.time_start:
                if self.time_source != source:
                    self.last_log_time, self.time_source = None, source
                parsed = self._log_time(text)
                if parsed:
                    self.last_log_time = parsed
                elif re.match(r'^\s*\[?\d{4}[-/]', text):
                    self.last_log_time = None
                if self.last_log_time is None:
                    self.unknown_time_lines += 1
                    return
                if not self.time_start <= self.last_log_time <= self.time_end:
                    self.buffer.flush()
                    self.buffer.before.clear()
                    self.filtered_lines += 1
                    return
            # Stop before eviction: a finite scan must not claim complete statistics after dropping events.
            if (len(self.buffer.events) >= 1999 or self.index_bytes >= 32 * 1024 * 1024 or
                (self.lines_read % 20 == 0 and sum(len(e.evidence.encode('utf-8')) for e in self.buffer.events) >= 8 * 1024 * 1024)):
                raise ValueError('证据容量已达上限，结果为部分读取；请分批分析')
            self.buffer.line(text, offset, source)
            clean = self.buffer.recent[-1][2]
            self.lines_read += 1
            self.index_bytes += len(clean.encode('utf-8'))
        db.execute('INSERT INTO lines(source,offset,text) VALUES(?,?,?)', (source, offset, clean))
        # Commit each bounded batch, including cancellation/failed scans.
        if self.lines_read % 100 == 0:
            db.commit()

    def search(self, keyword, cancellation):
        if cancellation.is_cancelled():
            raise ValueError('CANCELLED')
        with self._index() as db:
            db.set_progress_handler(lambda: int(cancellation.is_cancelled()), 1000)
            hits = db.execute('SELECT id,source FROM lines WHERE instr(lower(text),lower(?))>0 LIMIT 51', (keyword,)).fetchall()
            rows = {}
            for ident, source in hits[:50]:
                for row in db.execute('SELECT id,source,offset,text FROM lines WHERE source=? AND id BETWEEN ? AND ?',
                                      (source, ident - 3, ident + 3)):
                    rows[row[0]] = row[1:]
            return {'rows': [rows[k] for k in sorted(rows)], 'truncated': len(hits) > 50,
                    'coverage': '已提交的脱敏索引；每行最多4096字符', 'scan_complete': self.complete}

    def read_range(self, *args):
        raise ValueError('历史任务请使用 search_log 或 read_event 读取已保留证据')

    def snapshot(self, blocking=True):
        value = super().snapshot(blocking)
        if value is not None:
            with self.lock:
                value.update(mode='history', local=self.local, sources=copy.deepcopy(self.sources),
                             coverage='完整启动范围' if self.complete and not self.tail_lines else '有界范围 / 可能部分读取',
                             complete=self.complete, lines_read=self.lines_read, tail_lines=self.tail_lines)
                value['time_filter'] = {'start': self.time_start.isoformat() if self.time_start else None,
                                        'end': self.time_end.isoformat() if self.time_end else None,
                                        'timezone_minutes': self.timezone_minutes,
                                        'unknown_lines': self.unknown_time_lines, 'excluded_lines': self.filtered_lines}
                if self.time_start:
                    value['coverage'] += f' · 时间筛选；{self.unknown_time_lines}行无法判定时间，不能保证时段完整'
                for event in value['events']:
                    source = event['generation']
                    event['source'] = self.paths[source] if source < len(self.paths) else ''
                    timestamp = self._log_time(event['title'])
                    event['log_time'] = timestamp.isoformat() if timestamp else None
        return value

    def dispose(self):
        self.temp.cleanup()
