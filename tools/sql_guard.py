# -*- coding: utf-8 -*-
"""只允许自动执行查询类 SQL。"""

from __future__ import annotations

import re

_COMMENT_LINE = re.compile(r'--[^\n]*')
_COMMENT_BLOCK = re.compile(r'/\*.*?\*/', re.DOTALL)
_FIRST_WORD = re.compile(r'([A-Za-z]+)')

QUERY_VERBS = frozenset({
    'select', 'with', 'show', 'desc', 'describe', 'explain', 'pragma',
})
MUTATION_VERBS = frozenset({
    'insert', 'update', 'delete', 'merge', 'drop', 'truncate', 'alter',
    'create', 'grant', 'revoke', 'replace', 'call', 'begin', 'declare',
})

REDIS_READ = frozenset({
    'get', 'mget', 'exists', 'type', 'ttl', 'pttl', 'strlen', 'getrange',
    'hget', 'hgetall', 'hkeys', 'hvals', 'hexists', 'hlen',
    'lrange', 'llen', 'lindex',
    'smembers', 'scard', 'sismember',
    'zrange', 'zrevrange', 'zcard', 'zscore', 'zrank',
    'scan', 'hscan', 'sscan', 'zscan', 'dbsize', 'info', 'ping',
})
REDIS_WRITE = frozenset({
    'set', 'del', 'unlink', 'flushdb', 'flushall', 'expire', 'persist',
    'rename', 'lpush', 'rpush', 'lpop', 'rpop', 'sadd', 'srem',
    'zadd', 'zrem', 'hset', 'hdel', 'config', 'shutdown', 'move', 'restore',
    'keys',
})


def strip_sql_comments(sql: str) -> str:
    text = _COMMENT_BLOCK.sub(' ', str(sql or ''))
    text = _COMMENT_LINE.sub(' ', text)
    return text.strip()


def leading_verb(sql: str) -> str:
    text = strip_sql_comments(sql)
    if text.endswith(';'):
        text = text[:-1].strip()
    match = _FIRST_WORD.search(text)
    return (match.group(1) if match else '').lower()


def is_read_query(sql: str) -> bool:
    info = classify_statement(sql)
    return bool(info.get('is_read'))


_REDACT_KV = re.compile(
    r'(password|pwd|passwd|token|secret|authorization|api[_-]?key)\s*[=:]\s*([^\s,;]+)',
    re.IGNORECASE,
)
_REDACT_BEARER = re.compile(r'Bearer\s+\S+', re.IGNORECASE)
_REDACT_URL_AUTH = re.compile(r'([a-z][a-z0-9+.-]*://)([^/@\s]+):([^@\s]+)@', re.IGNORECASE)

MONGO_WRITE_MARKS = (
    'drop', 'deletemany', 'deleteone', 'insert', 'insertone', 'insertmany',
    'update', 'updateone', 'updatemany', 'remove', 'rename', 'replaceone',
)

DDL_VERBS = frozenset({
    'create', 'alter', 'drop', 'truncate', 'grant', 'revoke', 'comment', 'rename',
})
DML_VERBS = frozenset({
    'insert', 'update', 'delete', 'merge', 'replace', 'call', 'begin', 'declare',
})


def redact_error(message: str) -> str:
    text = str(message or '')
    text = _REDACT_BEARER.sub('Bearer ***', text)
    text = _REDACT_URL_AUTH.sub(r'\1***:***@', text)
    text = _REDACT_KV.sub(lambda m: f'{m.group(1)}=***', text)
    return text


def split_sql_statements(sql: str) -> list[str]:
    """按分号切分，忽略引号、行/块注释中的分号。"""
    raw = str(sql or '')
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(raw)
    state = 'normal'
    while i < n:
        ch = raw[i]
        nxt = raw[i + 1] if i + 1 < n else ''
        if state == 'line_comment':
            buf.append(ch)
            if ch == '\n':
                state = 'normal'
            i += 1
            continue
        if state == 'block_comment':
            buf.append(ch)
            if ch == '*' and nxt == '/':
                buf.append(nxt)
                i += 2
                state = 'normal'
                continue
            i += 1
            continue
        if state == 'squote':
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                state = 'normal'
            i += 1
            continue
        if state == 'dquote':
            buf.append(ch)
            if ch == '"' and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            if ch == '"':
                state = 'normal'
            i += 1
            continue
        if ch == '-' and nxt == '-':
            buf.append(ch)
            buf.append(nxt)
            i += 2
            state = 'line_comment'
            continue
        if ch == '/' and nxt == '*':
            buf.append(ch)
            buf.append(nxt)
            i += 2
            state = 'block_comment'
            continue
        if ch == "'":
            buf.append(ch)
            state = 'squote'
            i += 1
            continue
        if ch == '"':
            buf.append(ch)
            state = 'dquote'
            i += 1
            continue
        if ch == ';':
            piece = ''.join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def statement_at_cursor(sql: str, cursor: int) -> str:
    text = str(sql or '')
    if not text.strip():
        return ''
    pos = max(0, min(int(cursor or 0), len(text)))
    parts = split_sql_statements(text)
    if len(parts) <= 1:
        return (parts[0] if parts else text).strip()
    search_from = 0
    for piece in parts:
        start = text.find(piece, search_from)
        if start < 0:
            continue
        end = start + len(piece)
        if start <= pos <= end + 1:
            return piece
        search_from = end
    return parts[-1]


def _mongo_is_write(text: str) -> str:
    lowered = strip_sql_comments(text).lower()
    for mark in MONGO_WRITE_MARKS:
        if mark in lowered:
            return mark
    return ''


def classify_statement(sql: str, dialect: str = 'oracle') -> dict:
    text = str(sql or '').strip()
    kind = str(dialect or 'oracle').lower()
    result = {
        'empty': not bool(text),
        'dialect': kind,
        'verb': '',
        'category': 'unknown',
        'is_read': False,
        'needs_confirm': False,
        'label': '未知',
    }
    if not text:
        result['label'] = '空语句'
        return result
    if kind == 'redis':
        verb = leading_verb(text)
        result['verb'] = verb
        if verb in REDIS_WRITE:
            result.update(category='redis_write', needs_confirm=True, label=f'Redis 写命令 {verb.upper()}')
        elif verb in REDIS_READ:
            result.update(category='redis_read', is_read=True, label=f'Redis 读命令 {verb.upper()}')
        else:
            result.update(category='unknown', needs_confirm=True, label=f'Redis 命令 {verb.upper() or "未知"}')
        return result
    if kind == 'mongodb':
        mark = _mongo_is_write(text)
        if mark:
            result.update(verb=mark, category='mongo_write', needs_confirm=True, label=f'Mongo 写操作 {mark}')
            return result
        lowered = strip_sql_comments(text).lower()
        if '"collection"' in lowered or '.find(' in lowered or lowered.startswith('{'):
            result.update(verb='find', category='mongo_read', is_read=True, label='Mongo 查询')
            return result
        result.update(category='unknown', needs_confirm=True, label='Mongo 语句')
        return result
    verb = leading_verb(text)
    result['verb'] = verb
    lowered = strip_sql_comments(text).lower()
    if verb == 'with' and re.search(r'\b(insert|update|delete|merge)\b', lowered):
        result.update(category='dml', needs_confirm=True, label='WITH DML')
        return result
    if verb in ('begin', 'declare'):
        result.update(category='plsql', needs_confirm=True, label='PL/SQL')
        return result
    if verb in ('grant', 'revoke'):
        result.update(category='dcl', needs_confirm=True, label=f'DCL {verb.upper()}')
        return result
    if verb in QUERY_VERBS:
        result.update(category='select', is_read=True, label='查询')
        return result
    if verb in DML_VERBS:
        result.update(category='dml', needs_confirm=True, label=f'DML {verb.upper()}')
        return result
    if verb in DDL_VERBS:
        result.update(category='ddl', needs_confirm=True, label=f'DDL {verb.upper()}')
        return result
    result.update(category='unknown', needs_confirm=True, label=verb.upper() or '未知')
    return result


def ai_draft_safety(sql: str, dialect: str = 'oracle') -> dict:
    """AI 草案永不自动执行；无法静态证明只读时 fail closed。"""
    parts = split_sql_statements(sql)
    if len(parts) != 1:
        return {
            'safe_to_execute': False,
            'fail_closed': True,
            'reason': '多语句或无法可靠切分，仅草案 / 不可安全执行',
        }
    info = classify_statement(parts[0], dialect)
    if info.get('is_read') and info.get('category') == 'select':
        return {
            'safe_to_execute': False,
            'fail_closed': False,
            'reason': '只读草案，仍须在控制台手工执行',
        }
    return {
        'safe_to_execute': False,
        'fail_closed': True,
        'reason': '仅草案 / 不可安全执行',
        'category': info.get('category'),
    }


def reject_reason(sql: str, dialect: str = 'oracle') -> str:
    text = str(sql or '').strip()
    kind = str(dialect or 'oracle').lower()
    if not text:
        return '查询为空'
    info = classify_statement(text, kind)
    if info.get('is_read'):
        return ''
    if kind == 'redis':
        verb = info.get('verb') or '未知'
        if verb in REDIS_WRITE:
            return f'Redis 只允许读取，已拦截：{verb.upper()}'
        return f'Redis 不支持该命令自动执行：{verb.upper() or "未知"}'
    if kind == 'mongodb':
        if info.get('category') == 'mongo_write':
            return f'MongoDB 只允许 find 查询，已拦截：{info.get("verb")}'
        if is_read_query(text):
            return '当前是 MongoDB，请用 find JSON，不要写 SQL'
        return 'MongoDB 查询请使用 JSON，例如 {"collection":"user","filter":{}}'
    verb = info.get('verb') or '未知'
    return f'仅允许查询语句，已拦截：{verb.upper()}'
