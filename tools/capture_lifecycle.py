# -*- coding: utf-8 -*-
"""抓包生命周期纯状态机（UI-free：不 import PyQt6/panels/ui）。

状态：IDLE → STARTING → RUNNING → STOPPING → IDLE
RUNNING→STOPPING 期间的 START 请求记为 pending_start，STOP 真正收尾后
由调用方再次 begin_start()（finish_stop 不代替 begin_start）。
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
        """仅 STARTING 且 epoch 匹配时进入 RUNNING（过期 boot 结果返回 False）。"""
        with self._lock:
            if self._state == STARTING and epoch == self._epoch:
                self._state = RUNNING
                return True
            return False

    def begin_stop(self, epoch: int) -> bool:
        """仅 STARTING/RUNNING 且 epoch 匹配时进入 STOPPING（重复 stop 返回 False）。"""
        with self._lock:
            if self._state in (STARTING, RUNNING) and epoch == self._epoch:
                self._state = STOPPING
                return True
            return False

    def finish_stop(self, epoch: int) -> bool:
        """stop 线程收尾：原子回 IDLE 并取出 pending_start。

        仅 state==STOPPING 且 epoch 匹配时生效；不递增 epoch、不进入 STARTING——
        重启必须由调用方再次 begin_start()。返回 should_restart。
        """
        with self._lock:
            if self._state != STOPPING or epoch != self._epoch:
                return False
            should_restart = self._pending_start
            self._pending_start = False
            self._state = IDLE
            return should_restart

    def fail_start(self, epoch: int) -> bool:
        """当前 epoch 的 boot 失败：回 IDLE（清 pending）。旧 epoch 返回 False。"""
        with self._lock:
            if self._state == STARTING and epoch == self._epoch:
                self._state = IDLE
                self._pending_start = False
                return True
            return False

    def fail_runtime(self, epoch: int) -> bool:
        """当前 worker 异常退出/死亡：回 IDLE（清 pending）。旧 epoch 返回 False。"""
        with self._lock:
            if epoch == self._epoch and self._state in (STARTING, RUNNING):
                self._state = IDLE
                self._pending_start = False
                return True
            return False
