# -*- coding: utf-8 -*-
"""内网 OpenAI 兼容客户端：仅用户启用后访问配置的那一个 Base URL。

默认关闭。允许 loopback 与 RFC1918；拒绝已知公网模型域名。
请求绕过 HTTP_PROXY，避免公司代理把内网地址转到外网。
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from config import AI_LOCAL_FILE, ensure_config_dir

DEFAULT_AI_LOCAL = {
    'enabled': False,
    'base_url': '',
    'model': '',
    'timeout_seconds': 120,
    'ssl_verify': True,
    'token': '',
    'app_tag': 'proxyai',
    'max_tokens': 8192,
}

# JetBrains ProxyAI Custom OpenAI 实际在用的路径后缀，粘贴完整 URL 时剥掉
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


def normalize_ai_local(raw) -> dict:
    result = dict(DEFAULT_AI_LOCAL)
    if isinstance(raw, dict):
        result['enabled'] = bool(raw.get('enabled', False))
        result['base_url'] = str(raw.get('base_url') or '').strip()
        result['model'] = str(raw.get('model') or '').strip()
        result['ssl_verify'] = bool(raw.get('ssl_verify', True))
        result['token'] = str(raw.get('token') or '')
        tag = str(raw.get('app_tag') if raw.get('app_tag') is not None else 'proxyai').strip()
        result['app_tag'] = tag or 'proxyai'
        try:
            result['max_tokens'] = max(16, min(8192, int(raw.get('max_tokens') or 8192)))
        except (TypeError, ValueError):
            result['max_tokens'] = 8192
        try:
            result['timeout_seconds'] = max(5, min(300, int(raw.get('timeout_seconds') or 120)))
        except (TypeError, ValueError):
            result['timeout_seconds'] = 120
    return result


def load_ai_local() -> dict:
    ensure_config_dir()
    try:
        with open(AI_LOCAL_FILE, 'r', encoding='utf-8') as stream:
            return normalize_ai_local(json.load(stream))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_AI_LOCAL)


def save_ai_local(settings) -> dict:
    ensure_config_dir()
    normalized = normalize_ai_local(settings)
    if normalized.get('base_url'):
        try:
            normalized['base_url'] = canonical_base_url(normalized['base_url'])
        except IntranetLlmError:
            pass
    with open(AI_LOCAL_FILE, 'w', encoding='utf-8') as stream:
        json.dump(normalized, stream, indent=2, ensure_ascii=False)
    return normalized


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
    """接受 JetBrains ProxyAI 的完整 Chat Completions URL，归约到 /v1。"""
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
    """对齐 JetBrains ProxyAI：Bearer + X-LLM-Application-Tag: proxyai。"""
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    token = decrypt_token(cfg.get('token') or '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    tag = str(cfg.get('app_tag') or 'proxyai').strip() or 'proxyai'
    headers['X-LLM-Application-Tag'] = tag
    return headers


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
            detail = exc.read().decode('utf-8', errors='replace')[:400]
        except Exception:
            detail = str(exc)
        raise IntranetLlmError(f'HTTP {exc.code}：{detail or exc.reason}') from exc
    except urllib.error.URLError as exc:
        raise IntranetLlmError(f'无法连接内网模型：{exc.reason}') from exc
    except TimeoutError as exc:
        raise IntranetLlmError(f'连接超时（{timeout}s）') from exc
    except Exception as exc:
        raise IntranetLlmError(str(exc)) from exc
    return _decode_body(raw, status)


def list_models(cfg=None) -> list[str]:
    """先 GET /models；网关没有该接口时，改走 Chat Completions 探测（对齐 ProxyAI TEST CONNECTION）。"""
    cfg = normalize_ai_local(cfg if cfg is not None else load_ai_local())
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


def chat_completions(messages: list[dict], cfg=None) -> str:
    cfg = normalize_ai_local(cfg if cfg is not None else load_ai_local())
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
