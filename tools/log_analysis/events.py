"""Incremental multiline parsing. No remote or UI dependencies."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import re
import time
import uuid
from itertools import islice

ERROR = re.compile(r'\b(?:ERROR|FATAL)\b|\b[\w.$]*(?:Exception|Error):')
START = re.compile(r'^\s*(?:\d{4}[-/]\d\d[-/]\d\d|\[?\d\d:\d\d:\d\d)|\b(?:INFO|DEBUG|TRACE|WARN|ERROR|FATAL)\b')
TRACE = re.compile(r'(?:traceId|trace_id|requestId)\s*[=:]\s*([\w-]{1,128})', re.I)


class Redactor:
    def __init__(self):
        self.salt = uuid.uuid4().hex
        self.private_block = False
        self.payload_block = False

    def line(self, text):
        if '-----BEGIN' in text and 'PRIVATE KEY-----' in text:
            self.private_block = True
        if self.private_block:
            if '-----END' in text and 'PRIVATE KEY-----' in text:
                self.private_block = False
            return '[私钥片段已隐藏]'
        if re.search(r'(?i)(?:requestBody|responseBody|请求体|响应体)\s*[=:]', text):
            self.payload_block = True
            return '[请求/响应正文已隐藏]'
        if self.payload_block:
            if START.search(text):
                self.payload_block = False
            else:
                return '[正文续行已隐藏]'
        return self.clean(text)

    def clean(self, text):
        text = str(text)
        text = re.sub(r'-----BEGIN[^\n]*PRIVATE KEY-----.*?(?:-----END[^\n]*PRIVATE KEY-----|$)', '[私钥已隐藏]', text, flags=re.S)
        text = re.sub(r'(?im)^.*(?:authorization|cookie|password|passwd|secret|(?:access[_-]?)?token|api[_-]?key|密码)["\x27]?\s*[=:].*$', '[敏感字段已隐藏]', text)
        text = re.sub(r'(?im)^.*(?:requestBody|responseBody|请求体|响应体)\s*[=:].*$', '[请求/响应正文已隐藏]', text)
        text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [隐藏]', text)
        text = re.sub(r'(?i)(://)[^\s/@]+:[^\s/@]+@', r'\1[隐藏]@', text)
        def alias(m):
            digest = hashlib.sha256((self.salt + m.group()).encode()).hexdigest()[:10]
            return '[标识-' + digest + ']'
        return re.sub(r'\b(?:\d{11}|\d{17}[0-9Xx])\b', alias, text)


@dataclass
class Event:
    id: str
    generation: int
    offset: int
    title: str
    evidence: str
    first_seen: float
    last_seen: float
    count: int = 1
    trace_id: str = ''
    truncated: bool = False
    analyses: list = field(default_factory=list)
    handled: bool = False

    def public(self):
        return asdict(self)


class EventBuffer:
    """Caller serializes access. Offsets refer to bytes, times to observation."""
    def __init__(self, redactor=None, warn=False, limit=200, clock=time.time):
        self.redactor = redactor or Redactor()
        self.warn, self.limit, self.clock = warn, limit, clock
        self.events = deque(maxlen=limit)
        self.before = deque(maxlen=20)
        self.pending = []
        self.pending_context = []
        self.pending_offset = 0
        self.generation = 0
        self.last_input = 0.0
        self.truncated = False
        self.dropped = 0
        self.recent = deque(maxlen=200)

    def line(self, text, offset, generation):
        now = self.clock()
        if generation != self.generation:
            self.flush()
            self.before.clear()
            self.generation = generation
        clean = self.redactor.line(text.rstrip('\r\n'))
        for event in islice(reversed(self.events), 20):
            if event.generation == generation and getattr(event, '_after_remaining', 0) > 0:
                suffix = '\n' + clean[:2048]
                if len((event.evidence + suffix).encode('utf-8')) <= 65536:
                    event.evidence += suffix
                else:
                    event.truncated = True
                event._after_remaining -= 1
        self.recent.append((generation, offset, clean[:4096]))
        is_error = bool(ERROR.search(text) or (self.warn and re.search(r'\bWARN\b', text)))
        continuation = bool(re.match(r'^\s*(?:at |Caused by:|Suppressed:|\.\.\.)', text))
        if self.pending and not continuation and START.search(text):
            self.flush()
        if is_error and not continuation:
            self.flush()
            self.pending_offset = offset
            self.pending_context = list(self.before)
            self.pending = [clean]
        elif self.pending:
            if sum(len(x.encode('utf-8')) for x in self.pending) < 48 * 1024:
                self.pending.append(clean)
            else:
                self.truncated = True
        self.before.append(clean[:2048])
        self.last_input = now

    def idle(self):
        if self.pending and self.clock() - self.last_input >= 3:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        now = self.clock()
        body = '\n'.join(self.pending)
        # Ignore timestamp and trace ID only; preserve numbers/error codes.
        fingerprint = re.sub(r'^\s*\[?\d{4}[-/]\d\d[-/]\d\d[ T][\d:.,+Z-]+\]?\s*', '', body)
        fingerprint = TRACE.sub('traceId=<id>', fingerprint)
        key = hashlib.sha256(fingerprint.encode()).hexdigest()
        matched = next((e for e in reversed(self.events) if getattr(e, '_key', '') == key
                        and e.generation == self.generation and now - e.last_seen <= 60), None)
        if matched:
            matched.count += 1
            matched.last_seen = now
        else:
            trace = TRACE.search(body)
            evidence = '\n'.join(self.pending_context + self.pending)
            raw = evidence.encode('utf-8')
            event = Event(uuid.uuid4().hex[:12], self.generation, self.pending_offset,
                          self.pending[0][:180], raw[:65536].decode('utf-8', 'ignore'), now, now,
                          trace_id=trace.group(1) if trace else '', truncated=self.truncated or len(raw) > 65536)
            event._key = key
            event._after_remaining = 20
            if len(self.events) == self.limit:
                self.dropped += 1
            self.events.append(event)
        self.pending, self.pending_context = [], []
        self.truncated = False

    def snapshot(self):
        return [e.public() for e in self.events]
