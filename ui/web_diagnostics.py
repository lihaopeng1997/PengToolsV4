# -*- coding: utf-8 -*-
"""WebEngine 启动/运行诊断日志（轻量、安全、失败静默）。

- 单行 append，UTF-8，带时间戳；写失败静默吞掉，绝不影响程序启动。
- 只记录 WebEngine 生命周期与路径信息；禁止记录 Token/Cookie/密码/SQL/AI prompt/
  用户需求正文/HTTP body/Bridge 业务 DTO。
"""
from __future__ import annotations

import datetime
import os

_LOG_NAME = 'webengine_startup.log'
_LOG_DIR = None  # 允许测试/调用方覆盖；None 时按默认规则解析


def set_log_dir(path: str | None) -> None:
    """显式指定日志目录（测试用）。传 None 恢复默认。"""
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


def log_web_event(event: str, **fields) -> None:
    """记录一条 WebEngine 生命周期事件。任何失败静默忽略。"""
    try:
        log_dir = _resolve_log_dir()
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        parts = [f'event={event}']
        for key in sorted(fields):
            value = str(fields[key]).replace('\n', ' ').replace('\r', ' ')
            parts.append(f'{key}={value}')
        line = f'{stamp} ' + ' '.join(parts) + '\n'
        with open(os.path.join(log_dir, _LOG_NAME), 'a', encoding='utf-8') as fh:
            fh.write(line)
    except Exception:
        pass


def log_file_path() -> str:
    """当前日志文件路径（测试/诊断用）。"""
    return os.path.join(_resolve_log_dir(), _LOG_NAME)
