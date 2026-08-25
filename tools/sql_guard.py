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
    verb = leading_verb(sql)
    if verb in QUERY_VERBS:
        return True
    return False


def reject_reason(sql: str, dialect: str = 'oracle') -> str:
    text = str(sql or '').strip()
    kind = str(dialect or 'oracle').lower()
    if not text:
        return '查询为空'
    if kind == 'redis':
        verb = leading_verb(text)
        if verb in REDIS_WRITE:
            return f'Redis 只允许读取，已拦截：{verb.upper()}'
        if verb not in REDIS_READ:
            return f'Redis 不支持该命令自动执行：{verb or "未知"}'
        return ''
    if kind == 'mongodb':
        lowered = strip_sql_comments(text).lower()
        for bad in ('drop', 'deletemany', 'deleteone', 'insert', 'update', 'remove', 'rename'):
            if bad in lowered:
                return f'MongoDB 只允许 find 查询，已拦截：{bad}'
        if '"collection"' in lowered or '.find(' in lowered or lowered.startswith('{'):
            return ''
        if is_read_query(text):
            return '当前是 MongoDB，请用 find JSON，不要写 SQL'
        return 'MongoDB 查询请使用 JSON，例如 {"collection":"user","filter":{}}'
    if is_read_query(text):
        return ''
    verb = leading_verb(text) or '未知'
    return f'仅允许查询语句，已拦截：{verb.upper()}'
