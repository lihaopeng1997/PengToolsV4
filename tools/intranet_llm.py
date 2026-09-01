# -*- coding: utf-8 -*-
"""内网 OpenAI 兼容客户端：多配置；仅 loopback / RFC1918 / link-local。

默认关闭。每次请求解析主机全部 IP，拒绝公网与 DNS rebinding。
请求绕过 HTTP_PROXY。Token 仅 DPAPI/enc 存储。
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from config import AI_LOCAL_FILE, ensure_config_dir

DEFAULT_AI_LOCAL = {
    'enabled': False,
    'base_url': '',
    'model': '',
    'timeout_seconds': 120,
    'ssl_verify': True,
    'token': '',
    'app_tag': '',
    'max_tokens': 8192,
    'project_id': 'prpcar',
    'supports_vision': False,
}

CATALOG_VERSION = 2

# 粘贴完整 Chat Completions URL 时剥掉的后缀
_CHAT_SUFFIXES = (
    '/chat/completions',
    '/completions',
    '/models',
)

PUBLIC_MODEL_HOSTS = frozenset({
    'api.openai.com', 'openai.com',
    'api.deepseek.com', 'deepseek.com',
    'api.anthropic.com', 'anthropic.com',
    'generativelanguage.googleapis.com',
    'api.groq.com', 'groq.com',
    'api.together.xyz',
    'openrouter.ai',
})

_PUBLIC_HINTS = ('openai', 'deepseek', 'anthropic', 'googleapis', 'openrouter', 'groq')


class IntranetLlmError(Exception):
    """配置或请求失败（给界面原文）。"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _new_id() -> str:
    return uuid.uuid4().hex


def normalize_ai_local(raw) -> dict:
    result = dict(DEFAULT_AI_LOCAL)
    if isinstance(raw, dict):
        result['enabled'] = bool(raw.get('enabled', False))
        result['base_url'] = str(raw.get('base_url') or '').strip()
        result['model'] = str(raw.get('model') or '').strip()
        result['ssl_verify'] = bool(raw.get('ssl_verify', True))
        result['token'] = str(raw.get('token') or '')
        result['app_tag'] = str(raw.get('app_tag') or '').strip()
        result['project_id'] = str(raw.get('project_id') or 'prpcar').strip() or 'prpcar'
        result['supports_vision'] = bool(raw.get('supports_vision', False))
        try:
            result['max_tokens'] = max(16, min(8192, int(raw.get('max_tokens') or 8192)))
        except (TypeError, ValueError):
            result['max_tokens'] = 8192
        try:
            result['timeout_seconds'] = max(5, min(300, int(raw.get('timeout_seconds') or 120)))
        except (TypeError, ValueError):
            result['timeout_seconds'] = 120
        if raw.get('id'):
            result['id'] = str(raw.get('id'))
        if raw.get('name'):
            result['name'] = str(raw.get('name') or '').strip()
        if raw.get('created_at'):
            result['created_at'] = str(raw.get('created_at'))
        if raw.get('updated_at'):
            result['updated_at'] = str(raw.get('updated_at'))
        models = raw.get('available_models')
        if isinstance(models, list):
            result['available_models'] = [str(item) for item in models if str(item).strip()]
    return result


def normalize_model_item(raw) -> dict:
    return _normalize_item(raw)


def _normalize_item(raw) -> dict:
    item = normalize_ai_local(raw if isinstance(raw, dict) else {})
    item['id'] = str((raw or {}).get('id') or item.get('id') or _new_id())
    item['name'] = str((raw or {}).get('name') or item.get('name') or '默认配置').strip() or '默认配置'
    item['created_at'] = str((raw or {}).get('created_at') or item.get('created_at') or _now())
    item['updated_at'] = str((raw or {}).get('updated_at') or item.get('updated_at') or item['created_at'])
    item['available_models'] = list(item.get('available_models') or [])
    return item


def _empty_catalog() -> dict:
    return {'version': CATALOG_VERSION, 'active_model_id': '', 'items': []}


def _is_catalog(raw) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get('items'), list)


def normalize_catalog(raw) -> dict:
    if _is_catalog(raw):
        items = [_normalize_item(item) for item in raw.get('items') or [] if isinstance(item, dict)]
        names = {}
        for item in items:
            base = item['name']
            if base in names:
                names[base] += 1
                item['name'] = f'{base} ({names[base]})'
            else:
                names[base] = 1
        active = str(raw.get('active_model_id') or '')
        ids = {item['id'] for item in items}
        if active not in ids:
            enabled = next((item['id'] for item in items if item.get('enabled')), '')
            active = enabled or (items[0]['id'] if items else '')
        return {'version': CATALOG_VERSION, 'active_model_id': active, 'items': items}
    if isinstance(raw, dict) and (raw.get('base_url') or raw.get('model') or 'enabled' in raw or raw.get('token')):
        item = _normalize_item(raw)
        if not str(raw.get('name') or '').strip():
            item['name'] = '默认配置'
        return {'version': CATALOG_VERSION, 'active_model_id': item['id'], 'items': [item]}
    return _empty_catalog()


def _atomic_write_json(path: str, payload: dict) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.ai-local-', suffix='.tmp', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _read_raw():
    ensure_config_dir()
    try:
        with open(AI_LOCAL_FILE, 'r', encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError, TypeError):
        return None


def load_model_catalog() -> dict:
    raw = _read_raw()
    if raw is None:
        return _empty_catalog()
    catalog = normalize_catalog(raw)
    if not _is_catalog(raw) and os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
        try:
            _atomic_write_json(AI_LOCAL_FILE, catalog)
        except OSError:
            pass
    return catalog


def save_model_catalog(catalog) -> dict:
    ensure_config_dir()
    normalized = normalize_catalog(catalog)
    _atomic_write_json(AI_LOCAL_FILE, normalized)
    return normalized


def get_item_by_id(model_id: str, catalog=None) -> dict | None:
    data = catalog if isinstance(catalog, dict) else load_model_catalog()
    wanted = str(model_id or '')
    for item in data.get('items') or []:
        if item.get('id') == wanted:
            return dict(item)
    return None


def get_active_item(catalog=None) -> dict:
    data = catalog if isinstance(catalog, dict) else load_model_catalog()
    item = get_item_by_id(data.get('active_model_id'), data)
    if item:
        return item
    items = list(data.get('items') or [])
    return dict(items[0]) if items else normalize_ai_local({})


def list_enabled_items(catalog=None) -> list[dict]:
    data = catalog if isinstance(catalog, dict) else load_model_catalog()
    result = []
    for item in data.get('items') or []:
        if item.get('enabled') and str(item.get('base_url') or '').strip():
            result.append(dict(item))
    return result


def upsert_model_item(item: dict, *, make_active: bool = False) -> dict:
    catalog = load_model_catalog()
    data = _normalize_item(item)
    data['updated_at'] = _now()
    names = {
        str(row.get('name') or '')
        for row in catalog.get('items') or []
        if row.get('id') != data['id']
    }
    if data['name'] in names:
        raise IntranetLlmError('配置名称不可重复')
    found = False
    rows = list(catalog.get('items') or [])
    for index, row in enumerate(rows):
        if row.get('id') == data['id']:
            if not data.get('token') and row.get('token'):
                data['token'] = row.get('token')
            if not data.get('created_at'):
                data['created_at'] = row.get('created_at') or _now()
            rows[index] = data
            found = True
            break
    if not found:
        data['created_at'] = data.get('created_at') or _now()
        rows.append(data)
    catalog['items'] = rows
    if make_active or not catalog.get('active_model_id'):
        catalog['active_model_id'] = data['id']
    save_model_catalog(catalog)
    return data


def delete_model_item(model_id: str) -> dict:
    catalog = load_model_catalog()
    wanted = str(model_id or '')
    rows = [row for row in catalog.get('items') or [] if row.get('id') != wanted]
    catalog['items'] = rows
    if catalog.get('active_model_id') == wanted:
        enabled = next((row['id'] for row in rows if row.get('enabled')), '')
        catalog['active_model_id'] = enabled or (rows[0]['id'] if rows else '')
    return save_model_catalog(catalog)


def set_active_model(model_id: str) -> dict:
    catalog = load_model_catalog()
    item = get_item_by_id(model_id, catalog)
    if item is None:
        raise IntranetLlmError('找不到该模型配置')
    catalog['active_model_id'] = item['id']
    return save_model_catalog(catalog)


def load_ai_local() -> dict:
    catalog = load_model_catalog()
    if not catalog.get('items'):
        return dict(DEFAULT_AI_LOCAL)
    return normalize_ai_local(get_active_item(catalog))


def save_ai_local(settings) -> dict:
    raw = settings if isinstance(settings, dict) else {}
    if _is_catalog(raw):
        catalog = save_model_catalog(raw)
        return normalize_ai_local(get_active_item(catalog))
    item = _normalize_item(raw)
    if raw.get('base_url'):
        try:
            item['base_url'] = canonical_base_url(item['base_url'])
        except IntranetLlmError:
            pass
    catalog = load_model_catalog()
    if catalog.get('items'):
        active_id = str(raw.get('id') or catalog.get('active_model_id') or '')
        current = get_item_by_id(active_id, catalog) or get_active_item(catalog)
        item['id'] = str(current.get('id') or item['id'])
        item['name'] = str(raw.get('name') or current.get('name') or item['name'])
        if not raw.get('token') and current.get('token'):
            item['token'] = current.get('token')
        item['created_at'] = current.get('created_at') or item.get('created_at')
        saved = upsert_model_item(item, make_active=True)
        return normalize_ai_local(saved)
    saved = upsert_model_item(item, make_active=True)
    return normalize_ai_local(saved)


def decrypt_token(stored: str) -> str:
    text = str(stored or '')
    if not text:
        return ''
    try:
        from tools.secure_store import decrypt_secret
        return decrypt_secret(text)
    except Exception:
        return ''


def encrypt_token(plain: str) -> str:
    text = str(plain or '')
    if not text:
        return ''
    from tools.secure_store import encrypt_secret
    return encrypt_secret(text)


def is_enabled(settings=None) -> bool:
    cfg = settings if isinstance(settings, dict) else load_ai_local()
    return bool(cfg.get('enabled')) and bool(str(cfg.get('base_url') or '').strip())


def _hostname(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or '').strip().lower().rstrip('.')
    return host


def host_allowed(host: str) -> tuple[bool, str]:
    raw = (host or '').strip().lower().rstrip('.')
    if raw.startswith('[') and raw.endswith(']'):
        raw = raw[1:-1]
    if not raw:
        return False, '缺少主机名'
    if raw in ('localhost', '127.0.0.1', '::1'):
        return True, ''
    try:
        ip = ipaddress.ip_address(raw)
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return True, ''
        return False, '只允许内网或本机地址，不能填写公网 IP'
    except ValueError:
        pass
    if raw in PUBLIC_MODEL_HOSTS:
        return False, '禁止公网模型地址'
    for blocked in PUBLIC_MODEL_HOSTS:
        if raw.endswith('.' + blocked):
            return False, '禁止公网模型地址'
    if any(hint in raw for hint in _PUBLIC_HINTS):
        return False, '禁止公网模型地址'
    return True, ''


def resolve_private_host(host: str) -> list[str]:
    """解析主机全部 IP；任一公网地址即拒绝（防 DNS rebinding）。"""
    allowed, reason = host_allowed(host)
    if not allowed:
        raise IntranetLlmError(reason)
    raw = (host or '').strip().lower().rstrip('.')
    try:
        ipaddress.ip_address(raw[1:-1] if raw.startswith('[') and raw.endswith(']') else raw)
        return [raw]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(raw, None)
    except socket.gaierror as exc:
        raise IntranetLlmError(f'无法解析主机名：{host}') from exc
    ips = []
    for info in infos:
        address = info[4][0] if info and info[4] else ''
        if address and address not in ips:
            ips.append(address)
    if not ips:
        raise IntranetLlmError(f'无法解析主机名：{host}')
    for ip in ips:
        ok, reason = host_allowed(ip)
        if not ok:
            raise IntranetLlmError(f'DNS 解析到非内网地址，已拒绝（{ip}）')
    return ips


def validate_base_url(url: str) -> str:
    text = str(url or '').strip()
    if not text:
        raise IntranetLlmError('请填写内网模型 Base URL')
    parsed = urlparse(text)
    if parsed.scheme not in ('http', 'https'):
        raise IntranetLlmError('Base URL 须以 http:// 或 https:// 开头')
    allowed, reason = host_allowed(parsed.hostname or '')
    if not allowed:
        raise IntranetLlmError(reason)
    return text.rstrip('/')


def canonical_base_url(url: str) -> str:
    """完整 Chat Completions URL 归约到 /v1，便于再拼 /chat/completions。"""
    text = validate_base_url(url)
    parsed = urlparse(text)
    path = (parsed.path or '').rstrip('/')
    lower = path.lower()
    for suffix in _CHAT_SUFFIXES:
        if lower.endswith(suffix):
            path = path[: len(path) - len(suffix)]
            lower = path.lower()
            break
    rebuilt = parsed._replace(path=path or '/', query='', fragment='').geturl().rstrip('/')
    if rebuilt.endswith(':/') or rebuilt.endswith('://'):
        raise IntranetLlmError('Base URL 缺少路径，请包含 /v1')
    return rebuilt.rstrip('/')


def _direct_opener(verify_ssl: bool):
    """绕过系统代理；内网自签可关校验。"""
    https_handler = urllib.request.HTTPSHandler(
        context=ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()
    )
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        https_handler,
    )


def _join(base_url: str, suffix: str) -> str:
    base = canonical_base_url(base_url) + '/'
    return urljoin(base, suffix.lstrip('/'))


def build_headers(cfg: dict) -> dict:
    """OpenAI 兼容：Bearer；应用标签仅在用户填写时带上。"""
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    token = decrypt_token(cfg.get('token') or '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    tag = str(cfg.get('app_tag') or '').strip()
    if tag:
        headers['X-LLM-Application-Tag'] = tag
    return headers


def redact_llm_error(message: str) -> str:
    from tools.sql_guard import redact_error
    return redact_error(message)


def _parse_sse_text(raw: str) -> str:
    parts = []
    for line in str(raw or '').splitlines():
        line = line.strip()
        if not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if payload == '[DONE]':
            break
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        choices = obj.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        delta = choice.get('delta') if isinstance(choice.get('delta'), dict) else {}
        message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
        text = delta.get('content') or message.get('content') or ''
        if text:
            parts.append(str(text))
    return ''.join(parts)


def _decode_body(raw: str, status: int) -> dict:
    text = str(raw or '').strip()
    if not text:
        return {}
    if text.startswith('data:'):
        content = _parse_sse_text(text)
        return {'choices': [{'message': {'content': content}}]}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise IntranetLlmError(
            f'响应不是 OpenAI 兼容 JSON（HTTP {status}）。可粘贴完整 /v1/chat/completions 地址。'
        ) from exc
    if not isinstance(data, dict):
        raise IntranetLlmError('响应格式无法识别')
    return data


def _request(cfg: dict, method: str, suffix: str, body=None) -> dict:
    if not cfg.get('enabled'):
        raise IntranetLlmError('未启用内网模型')
    url = _join(cfg.get('base_url') or '', suffix)
    host = _hostname(url)
    resolve_private_host(host)
    timeout = int(cfg.get('timeout_seconds') or 120)
    payload = None
    headers = build_headers(cfg)
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        opener = _direct_opener(bool(cfg.get('ssl_verify', True)))
        with opener.open(request, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            status = getattr(resp, 'status', 200)
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = redact_llm_error(exc.read().decode('utf-8', errors='replace')[:400])
        except Exception:
            detail = str(exc)
        raise IntranetLlmError(f'HTTP {exc.code}：{detail or exc.reason}') from exc
    except urllib.error.URLError as exc:
        raise IntranetLlmError(f'无法连接内网模型：{exc.reason}') from exc
    except TimeoutError as exc:
        raise IntranetLlmError(f'连接超时（{timeout}s）') from exc
    except IntranetLlmError:
        raise
    except Exception as exc:
        raise IntranetLlmError(redact_llm_error(str(exc))) from exc
    return _decode_body(raw, status)


def _cfg_for_call(cfg=None, model_config_id: str | None = None) -> dict:
    if model_config_id:
        item = get_item_by_id(model_config_id)
        if item is None:
            raise IntranetLlmError('找不到该模型配置')
        return normalize_ai_local(item)
    if isinstance(cfg, dict):
        return normalize_ai_local(cfg)
    return load_ai_local()


def list_models(cfg=None, model_config_id: str | None = None) -> list[str]:
    """先 GET /models；网关没有该接口时改走 Chat Completions 探测。"""
    cfg = _cfg_for_call(cfg, model_config_id)
    names = []
    try:
        head_cfg = dict(cfg)
        head_cfg['timeout_seconds'] = min(8, int(cfg.get('timeout_seconds') or 120))
        data = _request(head_cfg, 'GET', 'models')
        items = data.get('data')
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    name = str(item.get('id') or item.get('name') or '').strip()
                else:
                    name = str(item or '').strip()
                if name and name not in names:
                    names.append(name)
    except IntranetLlmError:
        names = []
    if names:
        return names
    model = str(cfg.get('model') or 'qwen3.6').strip() or 'qwen3.6'
    ping = dict(cfg)
    ping['model'] = model
    ping['max_tokens'] = 16
    chat_completions([{'role': 'user', 'content': 'ping'}], cfg=ping)
    return [model]


def chat_completions(messages: list[dict], cfg=None, model_config_id: str | None = None) -> str:
    cfg = _cfg_for_call(cfg, model_config_id)
    model = str(cfg.get('model') or '').strip() or 'qwen3.6'
    body = {
        'model': model,
        'messages': messages,
        'temperature': 0.1,
        'max_tokens': int(cfg.get('max_tokens') or 8192),
        'stream': True,
    }
    try:
        data = _request(cfg, 'POST', 'chat/completions', body)
    except IntranetLlmError:
        body['stream'] = False
        data = _request(cfg, 'POST', 'chat/completions', body)
    choices = data.get('choices')
    if not isinstance(choices, list) or not choices:
        err = data.get('error')
        if isinstance(err, dict) and err.get('message'):
            raise IntranetLlmError(str(err['message']))
        raise IntranetLlmError('模型没有返回内容')
    message = choices[0].get('message') if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise IntranetLlmError('模型返回无法解析')
    content = message.get('content')
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                parts.append(str(item.get('text') or ''))
            elif isinstance(item, str):
                parts.append(item)
        content = ''.join(parts)
    text = str(content or '').strip()
    if not text:
        raise IntranetLlmError('模型返回为空')
    return text


def ping_model(cfg=None, model_config_id: str | None = None) -> dict:
    started = time.perf_counter()
    target = _cfg_for_call(cfg, model_config_id)
    probe = dict(target)
    probe['enabled'] = True
    probe['max_tokens'] = 16
    probe['timeout_seconds'] = min(8, int(target.get('timeout_seconds') or 120))
    try:
        canonical_base_url(probe.get('base_url') or '')
        chat_completions([{'role': 'user', 'content': 'ping'}], cfg=probe)
        return {
            'ok': True,
            'elapsed_ms': int((time.perf_counter() - started) * 1000),
            'model': str(target.get('model') or ''),
            'name': str(target.get('name') or ''),
            'error': '',
        }
    except Exception as exc:
        return {
            'ok': False,
            'elapsed_ms': int((time.perf_counter() - started) * 1000),
            'model': str(target.get('model') or ''),
            'name': str(target.get('name') or ''),
            'error': redact_llm_error(str(exc)),
        }
