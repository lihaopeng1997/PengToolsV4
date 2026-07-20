# -*- coding: utf-8 -*-
"""接口排查会话视图：筛选、排序、类型识别、体积格式化。

仅处理内存中的记录摘要，不涉及网络发送，不落盘报文。
"""

from __future__ import annotations

import json
import re
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlparse

from tools.browser_debug import is_static_url, should_keep_record

# 列定义：(key, 默认可见, 默认宽)
COLUMN_DEFS = [
    ('status', True, 72),
    ('method', True, 66),
    ('path', True, 280),
    ('duration', True, 74),
    ('type', True, 80),
    ('time', True, 100),
    ('size', False, 80),
    ('source', False, 80),
]

COLUMN_KEYS = [c[0] for c in COLUMN_DEFS]

FILTER_ALL = 'all'
FILTER_XHR = 'xhr'
FILTER_FAILED = 'failed'
FILTER_SLOW = 'slow'
FILTER_JSON_XML = 'json_xml'
FILTER_STATIC = 'static'


def content_kind(rec: dict) -> str:
    """返回 JSON/XML/HTML/图片/脚本/其他。"""
    mime = (rec.get('mime_type') or '').lower()
    body = (rec.get('response_body') or rec.get('request_body') or '').lstrip()
    path = (rec.get('path') or urlparse(rec.get('url') or '').path or '').lower()
    if 'json' in mime or body.startswith('{') or body.startswith('['):
        return 'JSON'
    if 'xml' in mime or body.startswith('<?xml') or (body.startswith('<') and not body.lower().startswith('<!doctype html')):
        if 'html' not in mime and not path.endswith(('.html', '.htm')):
            if body.startswith('<') or 'xml' in mime:
                return 'XML'
    if 'html' in mime or path.endswith(('.html', '.htm')) or body.lower().startswith('<!doctype html'):
        return 'HTML'
    if any(x in mime for x in ('image/', 'img')) or path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico')):
        return '图片'
    if 'javascript' in mime or path.endswith(('.js', '.mjs')) or 'css' in mime or path.endswith('.css'):
        return '脚本'
    rtype = (rec.get('resource_type') or '').lower()
    if rtype in ('stylesheet', 'script', 'font', 'image', 'media'):
        return '脚本' if rtype in ('stylesheet', 'script', 'font') else '图片'
    return '其他'


def response_size_bytes(rec: dict) -> Optional[int]:
    body = rec.get('response_body')
    if body is None or body == '':
        # 尝试 Content-Length
        headers = rec.get('response_headers') or {}
        for k, v in headers.items():
            if str(k).lower() == 'content-length':
                try:
                    return max(0, int(v))
                except (TypeError, ValueError):
                    pass
        return None
    if isinstance(body, bytes):
        return len(body)
    return len(str(body).encode('utf-8', errors='replace'))


def format_size(n: Optional[int]) -> str:
    if n is None:
        return '—'
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / (1024 * 1024):.2f} MB'


def host_path_display(rec: dict) -> str:
    url = rec.get('url') or ''
    parsed = urlparse(url)
    host = parsed.netloc or ''
    path = parsed.path or rec.get('path') or '/'
    if host:
        return f'{host}{path}'
    return path or '/'


def is_failed(rec: dict) -> bool:
    if rec.get('failure'):
        return True
    status = rec.get('status')
    try:
        code = int(status)
        return code >= 400
    except (TypeError, ValueError):
        return False


def is_slow(rec: dict, threshold_ms: int = 1000) -> bool:
    dur = rec.get('duration_ms')
    try:
        return int(dur) > threshold_ms
    except (TypeError, ValueError):
        return False


def is_xhr_like(rec: dict) -> bool:
    rtype = (rec.get('resource_type') or '').lower()
    if rtype in ('xhr', 'fetch', 'xmlhttprequest'):
        return True
    # 无类型时，非静态且非 document 的也算业务
    if not rtype:
        return not is_static_url(rec.get('url') or '')
    return False


def is_json_or_xml(rec: dict) -> bool:
    return content_kind(rec) in ('JSON', 'XML')


def record_search_blob(rec: dict) -> str:
    parts = [
        rec.get('url') or '',
        rec.get('path') or '',
        rec.get('method') or '',
        str(rec.get('status') or ''),
        rec.get('mime_type') or '',
        rec.get('resource_type') or '',
        content_kind(rec),
    ]
    parsed = urlparse(rec.get('url') or '')
    parts.append(parsed.netloc or '')
    parts.append(parsed.query or '')
    return '\n'.join(parts).casefold()


def match_search(rec: dict, query: str) -> bool:
    q = (query or '').strip().casefold()
    if not q:
        return True
    return all(term in record_search_blob(rec) for term in q.split())


def match_filters(rec: dict, active_filters: Iterable[str], *, show_static_default: bool = False) -> bool:
    """active_filters 含 FILTER_*；`all` 表示无额外业务过滤。"""
    filters = set(active_filters or [])
    if not filters or filters == {FILTER_ALL}:
        filters = {FILTER_ALL}
    # 静态资源：默认隐藏，除非用户选了 static 或 show_static
    static = is_static_url(rec.get('url') or '') or (
        (rec.get('resource_type') or '').lower() in ('stylesheet', 'script', 'image', 'font', 'media')
    )
    want_static = FILTER_STATIC in filters or show_static_default
    if static and not want_static:
        return False
    if FILTER_STATIC in filters and not static:
        # 仅看静态时
        if not any(f in filters for f in (FILTER_XHR, FILTER_FAILED, FILTER_SLOW, FILTER_JSON_XML, FILTER_ALL)):
            return static
    checks = []
    if FILTER_XHR in filters:
        checks.append(is_xhr_like(rec))
    if FILTER_FAILED in filters:
        checks.append(is_failed(rec))
    if FILTER_SLOW in filters:
        checks.append(is_slow(rec))
    if FILTER_JSON_XML in filters:
        checks.append(is_json_or_xml(rec))
    if not checks:
        return True
    # 组合：AND（可组合 FilterChip）
    return all(checks)


def sort_records(
    records: list[dict],
    sort_key: str = 'time',
    reverse: bool = True,
) -> list[dict]:
    def key_fn(rec):
        if sort_key == 'status':
            try:
                return int(rec.get('status') or -1)
            except (TypeError, ValueError):
                return -1
        if sort_key == 'method':
            return (rec.get('method') or '').upper()
        if sort_key == 'path':
            return host_path_display(rec).casefold()
        if sort_key == 'duration':
            try:
                return int(rec.get('duration_ms') or -1)
            except (TypeError, ValueError):
                return -1
        if sort_key == 'type':
            return content_kind(rec)
        if sort_key == 'size':
            return response_size_bytes(rec) or -1
        if sort_key == 'source':
            return rec.get('source') or ''
        # time / default
        return float(rec.get('started_at') or 0)

    return sorted(records, key=key_fn, reverse=reverse)


def filter_and_sort(
    records: list[dict],
    *,
    query: str = '',
    filters: Optional[Iterable[str]] = None,
    sort_key: str = 'time',
    sort_desc: bool = True,
    show_static: bool = False,
) -> list[dict]:
    out = []
    for rec in records:
        if not match_search(rec, query):
            continue
        if not match_filters(rec, filters or [FILTER_ALL], show_static_default=show_static):
            continue
        # 与 CDP 静态策略一致（show_static 时放行）
        if not show_static and FILTER_STATIC not in set(filters or []):
            if not should_keep_record(rec, show_static=False) and not is_xhr_like(rec):
                # should_keep 可能过严；若已有 path 且非静态仍保留
                if is_static_url(rec.get('url') or ''):
                    continue
        out.append(rec)
    return sort_records(out, sort_key=sort_key, reverse=sort_desc)


def pretty_body(text: str) -> tuple[str, str, Optional[str]]:
    """返回 (kind, display_text, error)。kind=json|xml|text。"""
    raw = text or ''
    s = raw.strip()
    if not s:
        return 'text', '', None
    if s[0] in '{[':
        try:
            obj = json.loads(s)
            return 'json', json.dumps(obj, ensure_ascii=False, indent=2), None
        except Exception as exc:
            return 'json', raw, f'JSON 解析失败：{exc}'
    if s.startswith('<') or s.startswith('<?xml'):
        # 轻量缩进：按标签换行
        try:
            pretty = _light_xml_indent(s)
            return 'xml', pretty, None
        except Exception as exc:
            return 'xml', raw, f'XML 整理失败：{exc}'
    return 'text', raw, None


def _light_xml_indent(text: str) -> str:
    # 避免引入外部库；简单按 >< 断行
    compact = re.sub(r'>\s*<', '>\n<', text.strip())
    lines = compact.splitlines()
    indent = 0
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('</'):
            indent = max(0, indent - 1)
        out.append(('  ' * indent) + stripped)
        if (
            stripped.startswith('<')
            and not stripped.startswith('</')
            and not stripped.startswith('<?')
            and not stripped.startswith('<!')
            and not stripped.endswith('/>')
            and '</' not in stripped[1:]
        ):
            indent += 1
    return '\n'.join(out)


def split_cookies(header_value: str) -> list[tuple[str, str]]:
    result = []
    for part in (header_value or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if '=' in part:
            k, v = part.split('=', 1)
            result.append((k.strip(), v.strip()))
        else:
            result.append((part, ''))
    return result


def query_pairs(url_or_query: str) -> list[tuple[str, str]]:
    if not url_or_query:
        return []
    if '://' in url_or_query or url_or_query.startswith('/'):
        q = urlparse(url_or_query).query
    else:
        q = url_or_query
    return parse_qsl(q, keep_blank_values=True)


def duration_severity(ms) -> str:
    """normal | warn | danger。"""
    try:
        v = int(ms)
    except (TypeError, ValueError):
        return 'normal'
    if v > 3000:
        return 'danger'
    if v > 1000:
        return 'warn'
    return 'normal'
