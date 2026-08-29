# -*- coding: utf-8 -*-
"""抓包生命周期纯状态机（UI-free：不 import PyQt6/panels/ui）。

状态：IDLE → STARTING → RUNNING → STOPPING → IDLE
RUNNING→STOPPING 期间的 START 请求记为 pending_start，STOP 真正收尾后自动转正。
线程安全（threading.Lock）；UI 文案/QTimer/信号由 panel 负责。
"""
from __future__ import annotations

import threading

IDLE = 'idle'
STARTING = 'starting'
RUNNING = 'running'
STOPPING = 'stopping'


def resolve_resume_action(worker_exists: bool, port_open: bool) -> str:
    """回到抓包页时的恢复决策：worker 健康且端口在听才 resume；否则恢复用户代理。"""
    return 'resume' if (worker_exists and port_open) else 'restore'


class CaptureLifecycle:
    """epoch + 状态 + pending_start 的唯一权威；过期 boot/stop 结果据此丢弃。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = IDLE
        self._epoch = 0
        self._pending_start = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def pending_start(self) -> bool:
        with self._lock:
            return self._pending_start

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def begin_start(self):
        """请求开始监听。返回新 epoch；STOPPING 期间记 pending 并返回 None；重复启动返回 None。"""
        with self._lock:
            if self._state == STOPPING:
                self._pending_start = True
                return None
            if self._state in (STARTING, RUNNING):
                return None
            self._state = STARTING
            self._epoch += 1
            self._pending_start = False
            return self._epoch

    def mark_running(self, epoch: int) -> bool:
        with self._lock:
            if self._state == STARTING and epoch == self._epoch:
                self._state = RUNNING
                return True
            return False

    def begin_stop(self, epoch: int) -> bool:
        """请求停止。仅 RUNNING/STARTING 且 epoch 匹配时进入 STOPPING。"""
        with self._lock:
            if self._state in (RUNNING, STARTING) and epoch == self._epoch:
                self._state = STOPPING
                return True
            return False

    def mark_stopped(self) -> None:
        """stop 线程收尾完成（可在后台线程调用；状态回 IDLE）。"""
        with self._lock:
            self._state = IDLE

    def confirm_pending_start(self):
        """stop 收尾后若有 pending start：转 STARTING 并返回新 epoch。"""
        with self._lock:
            if self._state == IDLE and self._pending_start:
                self._pending_start = False
                self._state = STARTING
                self._epoch += 1
                return self._epoch
            return None
