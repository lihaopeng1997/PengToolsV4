"""Own bounded monitoring and serialized model work independently of widgets."""
from __future__ import annotations

from collections import deque
import threading
import time

from tools.agent_core.contracts import CancellationToken
from .collector import Monitor
from .events import Event
from .runtime import analyze
from tools.agent_diag import log_agent_event


class LogService:
    def __init__(self, store=None, analyzer=analyze):
        self.store, self.analyzer = store, analyzer
        self.tasks = {}
        self.queue = deque(maxlen=50)
        self.results = deque(maxlen=100)
        self.lock = threading.RLock()
        self.token = None
        self.active = None
        self.active_since = None
        self.outcomes = {}
        self.analysis_state = '待分析'
        self.cache_state = ''
        self.auto = {}  # task -> explicitly chosen model ID
        self.last_auto = {}
        self.history = {}
        self.closed = threading.Event()
        self.wake = threading.Event()
        self.worker = threading.Thread(target=self._work, name='log-analysis', daemon=True)
        self.saver = threading.Thread(target=self._save, name='log-checkpoints', daemon=True)
        self.worker.start()
        self.saver.start()

    def open_snapshot(self, snapshot):
        from .cached import CachedTask
        with self.lock:
            if snapshot['id'] not in self.tasks:
                if len(self.tasks) >= 20:
                    raise ValueError('最多打开20个任务记录')
                self.tasks[snapshot['id']] = CachedTask(snapshot)
            return self.tasks[snapshot['id']]

    def add(self, server, service, path, *, mode='live', **options):
        with self.lock:
            def collecting(task):
                return any(owner and owner.thread and owner.thread.is_alive()
                           for owner in (task, getattr(task, 'log_task', None)))
            if sum(bool(collecting(t)) for t in self.tasks.values()) >= 4:
                raise ValueError('最多同时监控4个任务，请先停止一个任务')
            if len(self.tasks) >= 20:
                raise ValueError('本次打开已保留20个任务，请保存报告后重新打开软件')
            for task in self.tasks.values():
                if (mode == 'system' and hasattr(task, 'sampler') and
                        task.server_id == str(server.get('id', '')) and collecting(task)):
                    raise ValueError('此服务器已有系统监控任务，请选择已有任务或先停止它')
                if task.server_id == str(server.get('id', '')) and task.path == path and task.thread and task.thread.is_alive():
                    raise ValueError('此服务器的日志文件已有任务，请选择已有任务')
            if mode == 'history':
                from .history import HistoryScan
                task = HistoryScan(server, service, path, **options)
            elif mode == 'system':
                from .system import SystemMonitor
                task = SystemMonitor(server, service, path, **options)
            elif mode == 'live':
                task = Monitor(server, service, path, **options)
            else:
                raise ValueError('未知排查方式')
            restored = options.get('restored') or {}
            for data in restored.get('events', [])[-200:]:
                task.buffer.events.append(Event(**{k: v for k, v in data.items() if k in Event.__dataclass_fields__}))
            if restored:
                task.notices.append('从检查点手动恢复；软件未运行期间没有监控，轮转间隙可能缺失。')
            self.tasks[task.id] = task
            task.start()
            return task

    def enqueue(self, task_id, question, model_id, event_id=None, kind='chat'):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or self.closed.is_set():
                return False
            if len(self.queue) >= 50:
                self.analysis_state = '分析队列已满（50），请稍后重试'
                return False
            if event_id and any(x[0] == task_id and x[3] == event_id for x in self.queue):
                return True
            self.queue.append((task_id, question, model_id, event_id, kind))
            self.outcomes.pop(task_id, None)
            self.wake.set()
            return True

    def cancel_analysis(self, task_id, disable_auto=False):
        with self.lock:
            if disable_auto:
                self.auto.pop(task_id, None)
            self.queue = deque((x for x in self.queue if x[0] != task_id), maxlen=50)
            if self.active == task_id and self.token:
                self.token.cancel()
            self.outcomes[task_id] = '已取消分析'

    def analysis_activity(self, task_id):
        """Presentation reads one task's state, never another task's progress."""
        with self.lock:
            queued = sum(row[0] == task_id for row in self.queue)
            active = self.active == task_id and self.active is not None
            cancelled = active and self.token and self.token.is_cancelled()
            state = ('正在取消分析' if cancelled else self.analysis_state) if active else (
                '等待分析队列' if queued else self.outcomes.get(task_id, '尚未开始分析'))
            return {'busy': bool(active or queued), 'state': state, 'queued': queued,
                    'elapsed': int(time.monotonic() - self.active_since) if active and self.active_since is not None else 0}

    def stop(self, task_id):
        self.cancel_analysis(task_id, disable_auto=True)
        task = self.tasks[task_id]
        task.stop()

    def _work(self):
        while not self.closed.is_set():
            self.wake.wait(.5)
            with self.lock:
                self.wake.clear()
                if not self.queue:
                    continue
                task_id, question, model_id, event_id, kind = self.queue.popleft()
                task = self.tasks.get(task_id)
                if not task:
                    continue
                self.active = task_id
                self.active_since = time.monotonic()
                token = self.token = CancellationToken()
                self.analysis_state = '分析中'
            log_agent_event('log_analysis_started', task=task_id, stage='open')
            try:
                snapshot = task.snapshot()
                clean_question = task.buffer.redactor.clean(question)
                if event_id:
                    # Pin selected evidence in the prompt; full tools remain scoped.
                    event = next((e for e in snapshot['events'] if e['id'] == event_id), None)
                    if event:
                        clean_question += '\n所选异常证据：\n' + event['evidence']
                def progress(kind, name):
                    self.analysis_state = {'turn_started': '模型分析中', 'tool_started': '正在补查',
                                           'tool_result': '补查完成', 'budget': '整理上下文'}.get(kind, kind) + (' · ' + name if name else '')
                result = self.analyzer(snapshot, clean_question, model_id, token,
                                       history=self.history.get(task_id, []), progress=progress, reader=task.read_range,
                                       searcher=getattr(task, 'search', None), probe=getattr(task, 'probe', None))
                if token.is_cancelled() or self.closed.is_set():
                    continue
                text = task.buffer.redactor.clean(result.final_text or '分析未完成：' + str(result.error or result.status.value))
                if event_id:
                    for owner in (task, getattr(task, 'log_task', None)):
                        if owner is None:
                            continue
                        with owner.lock:
                            e = next((e for e in owner.buffer.events if e.id == event_id), None)
                            if e:
                                e.analyses.append({'time': time.time(), 'model': model_id, 'text': text})
                                e.analyses = e.analyses[-5:]
                                break
                history = self.history.setdefault(task_id, [])
                history.extend([{'role': 'user', 'content': task.buffer.redactor.clean(question)[:8192]},
                                {'role': 'assistant', 'content': text[:16384]}])
                self.history[task_id] = history[-12:]
                if len(history) > 12:
                    self.history[task_id][0] = dict(self.history[task_id][0])
                    self.history[task_id][0]['content'] = '[较早对话已省略；证据以当前工具查询为准]\n' + self.history[task_id][0]['content']
                with self.lock:
                    self.results.append((task_id, text, kind))
                    self.outcomes[task_id] = '分析失败，可重试' if result.error else '本次分析完成'
            except Exception as error:
                log_agent_event('log_analysis_failed', task=task_id, stage='failed', error_type=type(error).__name__)
                if not token.is_cancelled():
                    with self.lock:
                        self.results.append((task_id, '分析失败：' + type(error).__name__ + '；已保留证据，可手动重试。', 'chat'))
                        self.outcomes[task_id] = '分析失败，可重试'
            finally:
                log_agent_event('log_analysis_ended', task=task_id, cancelled=token.is_cancelled())
                with self.lock:
                    self.active = None
                    self.active_since = None
                    if token.is_cancelled():
                        self.outcomes[task_id] = '已取消分析'
                    self.analysis_state = '待分析' if self.queue else '分析空闲'

    def _save(self):
        while not self.closed.wait(5):
            with self.lock:
                tasks = list(self.tasks.values())
            for task in tasks:
                snapshot = task.snapshot(blocking=False)
                if snapshot is None:
                    continue
                if self.store:
                    try:
                        self.store.save(snapshot)
                        self.cache_state = ''
                    except Exception as error:
                        self.cache_state = str(error) if isinstance(error, ValueError) else '缓存保存失败：' + type(error).__name__
                model_id = self.auto.get(task.id)
                if model_id and not task.stop_event.is_set() and not getattr(task, 'saved', None):
                    if snapshot.get('mode') == 'history':
                        key = (task.id, 'history-summary')
                        if not task.thread.is_alive() and key not in self.last_auto:
                            if self.enqueue(task.id, '根据实际覆盖范围汇总历史日志异常，引用证据并说明未读范围。', model_id, kind='auto'):
                                self.last_auto[key] = (time.time(), 1)
                        continue
                    for event in snapshot['events']:
                        key = (task.id, event['id'])
                        last, count = self.last_auto.get(key, (0, 0))
                        if time.time() - last >= 60 and event['count'] != count:
                            if self.enqueue(task.id, '分析此异常，引用证据，区分事实和推断。', model_id, event['id'], kind='auto'):
                                self.last_auto[key] = (time.time(), event['count'])

    def drain(self):
        with self.lock:
            rows = list(self.results)
            self.results.clear()
            return rows

    def close(self):
        self.closed.set()
        self.wake.set()
        with self.lock:
            self.queue.clear()
            if self.token:
                self.token.cancel()
            tasks = list(self.tasks.values())
        for task in tasks:
            task.stop()

    def join(self, timeout=1):
        deadline = time.monotonic() + timeout
        threads = [self.worker, self.saver] + [t.thread for t in self.tasks.values() if t.thread]
        threads += [t.log_task.thread for t in self.tasks.values() if getattr(t, 'log_task', None) and t.log_task.thread]
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        done = not any(t.is_alive() for t in threads)
        if done and self.store:
            for task in self.tasks.values():
                try:
                    self.store.save(task.snapshot())
                except Exception:
                    self.cache_state = '停止时缓存未保存，请保留当前报告'
        if done:
            for task in self.tasks.values():
                if hasattr(task, 'dispose'):
                    task.dispose()
        return done
