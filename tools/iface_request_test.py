# -*- coding: utf-8 -*-
"""接口排查：会话导出/导入、密钥提取、本机请求测试（Postman 风格）。

- 导出/导入格式：pengtools_iface_session_v1
- URL 替换：base(host:port) + 原 path/query
- 请求发送：仅允许 127.0.0.1 / localhost（Private 边界）
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

EXPORT_KIND = 'pengtools_iface_session_v1'

_KEY_HEADER_HINTS = (
    'key', 'sm4', 'sm4key', 'encryptkey', 'secretkey', 'x-key', 'x-sm4',
    'x-encrypt-key', 'gateway-key', 'encrypt-key', 'appkey',
)
_KEY_JSON_HINTS = (
    'key', 'sm4Key', 'sm4_key', 'encryptKey', 'encrypt_key', 'secretKey',
    'keyCipher', 'key_cipher', 'encryptedKey',
)


class RequestTestError(ValueError):
    pass


def normalize_base_host(text: str) -> str:
    """localhost:18031 / http://localhost:18031 → http://localhost:18031"""
    raw = (text or '').strip()
    if not raw:
        return 'http://localhost:18031'
    if '://' not in raw:
        raw = 'http://' + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ('http', 'https'):
        raise RequestTestError('本地地址仅支持 http/https')
    host = (parsed.hostname or '').lower()
    if host not in ('localhost', '127.0.0.1', '::1'):
        raise RequestTestError('请求测试仅允许访问本机 localhost / 127.0.0.1')
    netloc = parsed.netloc or host
    return f'{parsed.scheme}://{netloc}'.rstrip('/')


def rewrite_url_with_base(original_url: str, base_host: str) -> str:
    """http://xxx:10110/a/b?x=1 + localhost:18031 → http://localhost:18031/a/b?x=1"""
    base = normalize_base_host(base_host)
    src = urlparse(original_url or '')
    base_p = urlparse(base)
    return urlunparse((
        base_p.scheme,
        base_p.netloc,
        src.path or '/',
        '',
        src.query or '',
        '',
    ))


def _looks_hex_key(value: str) -> bool:
    s = re.sub(r'\s+', '', value or '')
    if len(s) < 64 or len(s) % 2:
        return False
    return bool(re.fullmatch(r'[0-9a-fA-F]+', s))


def extract_sm4_key_cipher(rec: dict, side: str = 'request') -> str:
    """从请求/响应头或 JSON 体中尽量找出 SM2 加密后的 SM4 Key 密文。"""
    headers_list = []
    if side == 'request':
        headers_list.append(rec.get('request_headers') or {})
        headers_list.append(rec.get('response_headers') or {})
    else:
        headers_list.append(rec.get('response_headers') or {})
        headers_list.append(rec.get('request_headers') or {})
    for headers in headers_list:
        for k, v in (headers or {}).items():
            kl = str(k).lower().replace('_', '').replace('-', '')
            if any(h.replace('-', '').replace('_', '') in kl for h in _KEY_HEADER_HINTS):
                text = str(v or '').strip()
                if _looks_hex_key(text):
                    return re.sub(r'\s+', '', text)
    bodies = []
    if side == 'request':
        bodies = [rec.get('request_body'), rec.get('response_body')]
    else:
        bodies = [rec.get('response_body'), rec.get('request_body')]
    for body in bodies:
        text = (body or '').strip()
        if not text:
            continue
        # 整段就是 hex key
        if _looks_hex_key(text) and len(re.sub(r'\s+', '', text)) >= 128:
            return re.sub(r'\s+', '', text)
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            for name in _KEY_JSON_HINTS:
                val = obj.get(name)
                if isinstance(val, str) and _looks_hex_key(val):
                    return re.sub(r'\s+', '', val)
            # 浅层扫描
            for k, v in obj.items():
                kl = str(k).lower()
                if 'key' in kl and isinstance(v, str) and _looks_hex_key(v):
                    return re.sub(r'\s+', '', v)
    return ''


def try_decrypt_body(body: str, key_cipher: str, preferred_side: str = 'request') -> tuple[str, bool]:
    """尝试网关 SM2+SM4 解密；失败返回原文。"""
    body = body or ''
    key_cipher = (key_cipher or '').strip()
    if not body.strip() or not key_cipher:
        return body, False
    try:
        from tools.gateway_crypto import decrypt_gateway_payload
    except Exception:
        return body, False
    sides = [preferred_side]
    if preferred_side == 'request':
        sides.append('response')
    else:
        sides.append('request')
    for side in sides:
        for env in (1, 2, 3):
            try:
                plain = decrypt_gateway_payload(side, env, key_cipher, body)
                if plain is not None and str(plain).strip() != '':
                    return str(plain), True
            except Exception:
                continue
    return body, False


def plaintext_bodies(rec: dict) -> dict:
    """导出会话时优先使用解密后的请求/响应体。"""
    req_raw = rec.get('request_body') or ''
    resp_raw = rec.get('response_body') or ''
    key_req = extract_sm4_key_cipher(rec, 'request')
    key_resp = extract_sm4_key_cipher(rec, 'response') or key_req
    req_plain, req_ok = try_decrypt_body(req_raw, key_req, 'request')
    resp_plain, resp_ok = try_decrypt_body(resp_raw, key_resp, 'response')
    return {
        'url': rec.get('url') or '',
        'method': (rec.get('method') or 'GET').upper(),
        'request_headers': dict(rec.get('request_headers') or {}),
        'response_headers': dict(rec.get('response_headers') or {}),
        'request_body': req_plain,
        'response_body': resp_plain,
        'request_body_raw': req_raw,
        'response_body_raw': resp_raw,
        'request_decrypted': req_ok,
        'response_decrypted': resp_ok,
        'sm4_key_cipher': key_req or key_resp or '',
        'status': rec.get('status'),
        'host': rec.get('host') or '',
        'path': rec.get('path') or '',
        'query': rec.get('query') or '',
        'scheme': rec.get('scheme') or '',
    }


def build_export_document(records: list[dict]) -> dict:
    items = [plaintext_bodies(r) for r in records if isinstance(r, dict)]
    return {
        'pengtools_export': EXPORT_KIND,
        'version': 1,
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(items),
        'items': items,
    }


def export_document_to_text(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2)


def parse_import_document(text: str) -> list[dict]:
    try:
        data = json.loads(text or '')
    except Exception as exc:
        raise RequestTestError(f'导入文件不是合法 JSON：{exc}') from exc
    if not isinstance(data, dict) or data.get('pengtools_export') != EXPORT_KIND:
        raise RequestTestError('导入格式不正确：必须是 PengTools 接口会话导出文件')
    items = data.get('items')
    if not isinstance(items, list) or not items:
        raise RequestTestError('导入文件没有会话条目')
    out = []
    for it in items:
        if isinstance(it, dict) and (it.get('url') or it.get('path')):
            out.append(it)
    if not out:
        raise RequestTestError('导入文件没有有效 URL 条目')
    return out


def headers_text_from_dict(headers: dict) -> str:
    lines = []
    for k, v in (headers or {}).items():
        lines.append(f'{k}: {v}')
    return '\n'.join(lines)


def headers_dict_from_text(text: str) -> dict:
    result = {}
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        result[k.strip()] = v.strip()
    return result


def params_text_from_query(query: str) -> str:
    pairs = parse_qsl(query or '', keep_blank_values=True)
    return '\n'.join(f'{k}={v}' for k, v in pairs)


def query_from_params_text(text: str) -> str:
    pairs = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            pairs.append((k.strip(), v.strip()))
        else:
            pairs.append((line, ''))
    return urlencode(pairs, doseq=True)


def merge_url_with_params(url: str, params_text: str) -> str:
    parsed = urlparse(url or '')
    q = query_from_params_text(params_text)
    # 若 params 为空则保留原 query
    if not (params_text or '').strip():
        q = parsed.query or ''
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or '/', '', q, ''))


def fill_request_form_from_item(item: dict, base_host: str = 'http://localhost:18031') -> dict:
    """把导出条目/抓包记录填到请求测试表单字段。"""
    raw_url = item.get('url') or ''
    try:
        target = rewrite_url_with_base(raw_url, base_host)
    except RequestTestError:
        target = raw_url or base_host
    headers = item.get('request_headers') or {}
    # 体优先用解密后的
    body = item.get('request_body') or item.get('request_body_raw') or ''
    parsed = urlparse(raw_url or target)
    return {
        'base_host': base_host if base_host.startswith('http') else f'http://{base_host}',
        'method': (item.get('method') or 'GET').upper(),
        'url': target,
        'headers_text': headers_text_from_dict(headers),
        'params_text': params_text_from_query(item.get('query') or parsed.query or ''),
        'body': body,
        'sm4_key_cipher': item.get('sm4_key_cipher') or extract_sm4_key_cipher(item, 'request'),
        'response_body_sample': item.get('response_body') or '',
    }


def send_http_request(
    method: str,
    url: str,
    headers: Optional[dict] = None,
    body: str = '',
    timeout: float = 30.0,
) -> dict:
    """发送 HTTP 请求（仅本机 host）。"""
    parsed = urlparse(url or '')
    host = (parsed.hostname or '').lower()
    if host not in ('localhost', '127.0.0.1', '::1'):
        raise RequestTestError('安全限制：请求测试只能发往 localhost / 127.0.0.1')
    if parsed.scheme not in ('http', 'https'):
        raise RequestTestError('仅支持 http/https')
    method = (method or 'GET').upper()
    data = None
    if body and method not in ('GET', 'HEAD'):
        data = body.encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        if str(k).lower() == 'content-length':
            continue
        req.add_header(str(k), str(v))
    if data is not None and not any(str(k).lower() == 'content-type' for k in (headers or {})):
        req.add_header('Content-Type', 'application/json;charset=UTF-8')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('utf-8', errors='replace')
            return {
                'ok': True,
                'status': getattr(resp, 'status', None) or resp.getcode(),
                'headers': {k: v for k, v in resp.headers.items()},
                'body': text,
                'error': '',
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, 'read') else b''
        try:
            text = raw.decode('utf-8')
        except Exception:
            text = raw.decode('utf-8', errors='replace') if raw else ''
        return {
            'ok': False,
            'status': exc.code,
            'headers': dict(exc.headers.items()) if exc.headers else {},
            'body': text,
            'error': str(exc),
        }
    except Exception as exc:
        raise RequestTestError(f'请求失败：{exc}') from exc
