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


def reject_reason(sql: str) -> str:
    if not strip_sql_comments(sql):
        return 'SQL 为空'
    if is_read_query(sql):
        return ''
    verb = leading_verb(sql) or '未知'
    return f'仅允许查询语句，已拦截：{verb.upper()}'
