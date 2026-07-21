# -*- coding: utf-8 -*-
"""接口排查会话视图（对齐 Fiddler Session 列表习惯）。

Fiddler 核心：本机 HTTP/HTTPS 流量中转 → 会话列表 → 检视详情。
本模块只做列表筛选/排序/展示字段，不发送网络、不落盘报文。
Private 边界：只「看」与生成草稿，不改包、不重放、不 Mock 外发。
"""

from __future__ import annotations

import json
import re
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlparse

from tools.browser_debug import is_static_url, should_keep_record

# Fiddler 式列：# / Result / Protocol / Method / Host / URL / Body / Type / Time
# (key, 默认可见, 默认宽)
COLUMN_DEFS = [
    ('seq', True, 44),
    ('status', True, 64),
    ('protocol', True, 56),
    ('method', True, 64),
    ('host', True, 150),
    ('url', True, 320),
    ('body', True, 72),
    ('type', True, 72),
    ('duration', True, 72),
    ('time', False, 96),
]

COLUMN_KEYS = [c[0] for c in COLUMN_DEFS]
# 旧配置字段兼容
_COLUMN_ALIASES = {
    'path': 'url',
    'size': 'body',
    'source': 'type',
}

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


def protocol_of(rec: dict) -> str:
    scheme = (rec.get('scheme') or urlparse(rec.get('url') or '').scheme or '').lower()
    if scheme in ('http', 'https'):
        return scheme
    return 'https' if (rec.get('url') or '').lower().startswith('https') else (scheme or 'http')


def host_of(rec: dict) -> str:
    url = rec.get('url') or ''
    parsed = urlparse(url)
    host = rec.get('host') or parsed.hostname or parsed.netloc or ''
    if host and rec.get('port') and ':' not in str(host):
        try:
            port = int(rec.get('port'))
            default = 443 if protocol_of(rec) == 'https' else 80
            if port and port != default:
                return f'{host}:{port}'
        except (TypeError, ValueError):
            pass
    return host or (parsed.netloc or '')


def url_path_display(rec: dict) -> str:
    """Fiddler URL 列：path + query。"""
    url = rec.get('url') or ''
    parsed = urlparse(url)
    path = rec.get('path') or parsed.path or '/'
    query = rec.get('query') if rec.get('query') is not None else (parsed.query or '')
    if query:
        return f'{path}?{query}'
    return path or '/'


def host_path_display(rec: dict) -> str:
    """兼容旧名：host + path。"""
    host = host_of(rec)
    path = rec.get('path') or urlparse(rec.get('url') or '').path or '/'
    if host:
        return f'{host}{path}'
    return path or '/'


def normalize_column_key(key: str) -> str:
    k = str(key or '')
    return _COLUMN_ALIASES.get(k, k)


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
    sort_key = normalize_column_key(sort_key)
    def key_fn(rec):
        if sort_key == 'status':
            try:
                return int(rec.get('status') or -1)
            except (TypeError, ValueError):
                return -1
        if sort_key == 'method':
            return (rec.get('method') or '').upper()
        if sort_key in ('path', 'url'):
            return url_path_display(rec).casefold()
        if sort_key == 'host':
            return host_of(rec).casefold()
        if sort_key == 'protocol':
            return protocol_of(rec)
        if sort_key in ('duration',):
            try:
                return int(rec.get('duration_ms') or -1)
            except (TypeError, ValueError):
                return -1
        if sort_key in ('body', 'size'):
            return response_size_bytes(rec) or -1
        if sort_key == 'seq':
            return int(rec.get('seq') or 0)
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
    show_static: bool = True,
) -> list[dict]:
    """筛选排序。默认显示全部类型（含静态），避免用户误以为「没抓到」。"""
    out = []
    active = list(filters or [FILTER_ALL])
    for rec in records:
        if not match_search(rec, query):
            continue
        if not match_filters(rec, active, show_static_default=show_static):
            continue
        # 默认保留业务与未知；仅隐藏明确静态
        want_static = show_static or FILTER_STATIC in set(active)
        if not want_static and not should_keep_record(rec, show_static=False):
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
