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
    'timeout_seconds': 60,
    'ssl_verify': True,
    'token': '',
}

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
        try:
            result['timeout_seconds'] = max(5, min(300, int(raw.get('timeout_seconds') or 60)))
        except (TypeError, ValueError):
            result['timeout_seconds'] = 60
        result['ssl_verify'] = bool(raw.get('ssl_verify', True))
        result['token'] = str(raw.get('token') or '')
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
    base = validate_base_url(base_url) + '/'
    return urljoin(base, suffix.lstrip('/'))


def _request(cfg: dict, method: str, suffix: str, body=None) -> dict:
    if not cfg.get('enabled'):
        raise IntranetLlmError('未启用内网模型')
    url = _join(cfg.get('base_url') or '', suffix)
    timeout = int(cfg.get('timeout_seconds') or 60)
    payload = None
    headers = {'Accept': 'application/json'}
    token = decrypt_token(cfg.get('token') or '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
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
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise IntranetLlmError(
            f'响应不是 OpenAI 兼容 JSON（HTTP {status}）。请确认 Base URL 指向 /v1。'
        ) from exc
    if not isinstance(data, dict):
        raise IntranetLlmError('响应格式无法识别')
    return data


def list_models(cfg=None) -> list[str]:
    cfg = normalize_ai_local(cfg if cfg is not None else load_ai_local())
    data = _request(cfg, 'GET', 'models')
    items = data.get('data')
    names = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                name = str(item.get('id') or item.get('name') or '').strip()
            else:
                name = str(item or '').strip()
            if name and name not in names:
                names.append(name)
    return names


def chat_completions(messages: list[dict], cfg=None) -> str:
    cfg = normalize_ai_local(cfg if cfg is not None else load_ai_local())
    model = str(cfg.get('model') or '').strip()
    if not model:
        raise IntranetLlmError('请先选择模型')
    data = _request(cfg, 'POST', 'chat/completions', {
        'model': model,
        'messages': messages,
        'temperature': 0.2,
    })
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
