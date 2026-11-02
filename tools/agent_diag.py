# -*- coding: utf-8 -*-
"""内网 Agent / 数据助手诊断日志。

只记事件名、耗时、工具名、状态码和计数。不记 Token、密码、SQL、文件正文、模型回复。
写失败时静默，不影响查询。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import threading

_LOG_NAME = 'agent_diag.log'
_LOG_DIR = None
_LOCK = threading.Lock()
MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 2
DIAGNOSTIC_VERSION = 'agent-20261023-v3'
_NUMERIC_FIELDS = frozenset('elapsed_ms turn_index sequence offset char_count total_chars text_chars reasoning_chars answer_chars tools native_calls recovered parsed returned schema_count schema_index keyword_len deadline_seconds request_bytes messages chunks received_bytes idle_ms retry input_chars output_tokens queries max_input_chars max_tokens'.split())
_BOOL_FIELDS = frozenset(('ok', 'has_more', 'done', 'eof', 'cancelled', 'verified'))
_NUMERIC_FIELDS = _NUMERIC_FIELDS | {'input_estimate', 'limit_bytes', 'messages_kept', 'messages_dropped', 'idle_timeout_seconds'}
_TOOLS = frozenset('list_dir read_file search_code write_file edit_file delete_file run_test run_svn search_objects describe_object query_readonly redis_read mongo_read request_clarification'.split())
_ENUM_FIELDS = {
    'capability': {'native_tools', 'strict_json', 'text_only', 'unknown'},
    'provider': {'intranet', 'deepseek', 'oracle', 'mysql', 'oceanbase_mysql', 'oceanbase_oracle', 'mongodb', 'redis', 'dameng'},
    'domain': {'programming', 'data_center'},
    'stage': {'open', 'stream', 'completed', 'failed', 'cancelled'},
    'status': {'created', 'running', 'model_pending', 'tool_running', 'completed', 'failed', 'timeout', 'cancelled', 'budget_exhausted', 'needs_clarification', 'policy_blocked', 'ready', 'blocked', 'deferred'},
    'finish_reason': {'stop', 'tool_calls', 'function_call', 'length', 'max_tokens', 'content_filter'},
    'reason': {'time', 'bytes', 'input_chars', 'output_tokens', 'model_turns', 'tool_calls', 'queries', 'final_request_input_bytes', 'context_anchors_too_large', 'incomplete_history', 'REPEATED_TOOL_CALL'},
}
_ENUM_FIELDS['reason'].update({'auto_compacted', 'memory_compacted', 'memory_omitted',
                             'within_application_budget_window_unknown', 'within_known_input_budget',
                             'trimmed_to_application_budget_window_unknown', 'trimmed_to_known_input_budget'})
_CODES = frozenset('OK CANCELLED TIMEOUT QUERY_TIMEOUT QUERY_FAILED CONNECTOR_UNAVAILABLE CONNECTION_FAILED CAPABILITY_UNAVAILABLE POLICY_DENIED ARGUMENT_INVALID INVALID_TOOL_ARGUMENTS TOOL_FAILED TOOL_EXECUTION_FAILED MODEL_FAILED MODEL_TIMEOUT MODEL_PROTOCOL_ERROR MODEL_TRANSPORT_ERROR RUNTIME_FAILED SCOPE_DENIED SQL_INVALID MULTI_STATEMENT DIALECT_UNSUPPORTED PROFILE_CHANGED QUERY_OWNER_PENDING QUERY_OWNER_FAILED READONLY_UNVERIFIED SQL_POLICY_DENIED RESULT_REDACTION_FAILED REPEATED_TOOL_CALL REQUEST_INPUT_LIMIT_EXCEEDED'.split())


def _safe_fields(fields: dict) -> dict:
    """Allow only counters and known labels. Never serialize arbitrary payloads."""
    safe = {}
    for key, value in fields.items():
        if key in _NUMERIC_FIELDS and type(value) in (int, float) and math.isfinite(value):
            safe[key] = value
        elif key in _BOOL_FIELDS and type(value) is bool:
            safe[key] = value
        elif key in ('run_id', 'task') and isinstance(value, str) and value:
            safe[key] = hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]
        elif key in ('tool', 'name') and isinstance(value, str):
            safe[key] = value if value in _TOOLS else 'other'
        elif key in _ENUM_FIELDS and isinstance(value, str):
            safe[key] = value if value in _ENUM_FIELDS[key] else 'other'
        elif key == 'code' and isinstance(value, str):
            safe[key] = value if value in _CODES else 'OTHER'
        elif key == 'error_type' and isinstance(value, str):
            # Callers pass type(error).__name__, never exception text.
            safe[key] = value if re.fullmatch(r'[A-Za-z_]{1,64}(?:Error|Exceeded|Cancelled|Exception|Timeout)', value) else 'OtherError'
    return safe


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
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', event):
            return
        log_dir = _resolve_log_dir()
        record = {'time': datetime.datetime.now().astimezone().isoformat(timespec='milliseconds'),
                  'version': DIAGNOSTIC_VERSION, 'pid': os.getpid(), 'thread': threading.get_ident(),
                  'event': event, **_safe_fields(fields)}
        line = json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n'
        path = os.path.join(log_dir, _LOG_NAME)
        with _LOCK:
            os.makedirs(log_dir, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) + len(line.encode('utf-8')) > MAX_LOG_BYTES:
                for index in range(LOG_BACKUPS, 0, -1):
                    source = path if index == 1 else f'{path}.{index - 1}'
                    if os.path.exists(source):
                        os.replace(source, f'{path}.{index}')
            with open(path, 'a', encoding='utf-8') as handle:
                handle.write(line)
    except Exception:
        pass
