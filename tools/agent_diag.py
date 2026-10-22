# -*- coding: utf-8 -*-
"""内网 Agent / 数据助手诊断日志。

只记事件名、耗时、工具名、状态码和计数。不记 Token、密码、SQL、文件正文、模型回复。
写失败时静默，不影响查询。
"""
from __future__ import annotations

import datetime
import os

_LOG_NAME = 'agent_diag.log'
_LOG_DIR = None
_SECRET_KEYS = ('token', 'password', 'secret', 'authorization', 'cookie', 'prompt', 'sql', 'content')


def set_log_dir(path: str | None) -> None:
    global _LOG_DIR
    _LOG_DIR = path


def _resolve_log_dir() -> str:
    if _LOG_DIR:
        return _LOG_DIR
    try:
        from config import local_data_dir
        base = local_data_dir()
    except Exception:
        base = os.path.join(os.getcwd(), 'data')
    return os.path.join(base, 'logs')


def log_file_path() -> str:
    return os.path.join(_resolve_log_dir(), _LOG_NAME)


def log_agent_event(event: str, **fields) -> None:
    try:
        log_dir = _resolve_log_dir()
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        parts = [f'event={_one_line(event)}']
        for key in sorted(fields):
            lowered = str(key).lower()
            if any(part in lowered for part in _SECRET_KEYS):
                continue
            parts.append(f'{_one_line(key)}={_one_line(fields[key])}')
        line = f'{stamp} ' + ' '.join(parts) + '\n'
        with open(log_file_path(), 'a', encoding='utf-8') as handle:
            handle.write(line)
    except Exception:
        pass


def _one_line(value) -> str:
    text = str(value if value is not None else '').replace('\n', ' ').replace('\r', ' ')
    if len(text) > 180:
        text = text[:177] + '...'
    return text
