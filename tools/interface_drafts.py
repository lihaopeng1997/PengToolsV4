# -*- coding: utf-8 -*-
"""接口验证草稿：URL 重写、Postman Collection v2.1、cURL。不发送网络请求。"""

from __future__ import annotations

import json
import shlex
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class DraftError(ValueError):
    pass


def validate_base_url(base_url: str) -> str:
    text = (base_url or '').strip().rstrip('/')
    if not text:
        raise DraftError('本地地址不能为空')
    parsed = urlparse(text)
    if parsed.scheme not in ('http', 'https'):
        raise DraftError('本地地址必须是 http:// 或 https://')
    if not parsed.netloc:
        raise DraftError('本地地址缺少 host')
    if parsed.path and parsed.path not in ('', '/'):
        raise DraftError('base URL 不允许带 path，请只填 scheme://host:port')
    return f'{parsed.scheme}://{parsed.netloc}'


def rewrite_url(original_url: str, base_url: str) -> str:
    base = validate_base_url(base_url)
    src = urlparse(original_url or '')
    base_p = urlparse(base)
    return urlunparse((
        base_p.scheme,
        base_p.netloc,
        src.path or '',
        '',
        src.query or '',
        '',
    ))


def build_postman_collection(record: dict, base_url: str, name: str = 'PengTools Local Draft') -> dict:
    """返回 Postman Collection v2.1 + 附带 environment 片段。"""
    target = rewrite_url(record.get('url') or '', base_url)
    parsed = urlparse(target)
    path_parts = [p for p in (parsed.path or '').split('/') if p != '']
    query = [{'key': k, 'value': v} for k, v in parse_qsl(parsed.query, keep_blank_values=True)]
    headers = []
    for key, value in (record.get('request_headers') or {}).items():
        headers.append({'key': str(key), 'value': str(value), 'type': 'text'})
    body_raw = record.get('request_body') or ''
    body = None
    if body_raw:
        body = {'mode': 'raw', 'raw': body_raw}
    item = {
        'name': record.get('path') or target,
        'request': {
            'method': (record.get('method') or 'GET').upper(),
            'header': headers,
            'url': {
                'raw': '{{baseUrl}}' + (parsed.path or '') + (('?' + parsed.query) if parsed.query else ''),
                'host': ['{{baseUrl}}'],
                'path': path_parts,
                'query': query,
            },
        },
    }
    if body:
        item['request']['body'] = body
    collection = {
        'info': {
            'name': name,
            'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
        },
        'item': [item],
        'variable': [{'key': 'baseUrl', 'value': validate_base_url(base_url)}],
    }
    environment = {
        'name': f'{name} Env',
        'values': [
            {'key': 'baseUrl', 'value': validate_base_url(base_url), 'enabled': True},
        ],
    }
    return {'collection': collection, 'environment': environment}


def build_curl(record: dict, base_url: str) -> str:
    target = rewrite_url(record.get('url') or '', base_url)
    method = (record.get('method') or 'GET').upper()
    parts = ['curl', '-X', method, shlex.quote(target)]
    for key, value in (record.get('request_headers') or {}).items():
        parts.extend(['-H', shlex.quote(f'{key}: {value}')])
    body = record.get('request_body') or ''
    if body:
        parts.extend(['--data-raw', shlex.quote(body)])
    return ' '.join(parts)


def drafts_as_json_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
