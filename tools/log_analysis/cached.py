"""Saved evidence can be analyzed without starting any remote connection."""
import copy
from dataclasses import fields
import threading
from .events import Event, EventBuffer


class CachedTask:
    def __init__(self, snapshot):
        self.saved = copy.deepcopy(snapshot)
        self.id = snapshot['id']
        self.server_id = snapshot.get('server_id', '')
        self.path = snapshot.get('path', '')
        self.thread = None
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.lock = threading.RLock()
        self.buffer = EventBuffer(limit=2000)
        keys = {f.name for f in fields(Event)}
        for event in snapshot.get('events', [])[:2000]:
            self.buffer.events.append(Event(**{k: v for k, v in event.items() if k in keys}))

    def snapshot(self, blocking=True):
        with self.lock:
            result = copy.deepcopy(self.saved)
            extra = {e['id']: {k: e[k] for k in ('source', 'log_time') if k in e} for e in result.get('events', [])}
            result['events'] = self.buffer.snapshot()
            for event in result['events']:
                event.update(extra.get(event['id'], {}))
            result.update(state='缓存证据 · 未采集', cached=True, recent=[])
            result['notices'] = result.get('notices', []) + ['缓存仅包含保留证据；原始日志索引不可用，未重新连接服务器。']
            return result

    def stop(self):
        self.stop_event.set()

    def read_range(self, *args):
        raise ValueError('缓存记录未连接原始文件，请新建读取任务')
