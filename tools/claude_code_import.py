"""从本机 Claude Code 配置导入模型连接。

支持两种来源（都只读本机文件，不联网）：

- VS Code 插件：``%APPDATA%/Code/User/settings.json`` 中的
  ``claudeCode.environmentVariables``（``[{name, value}, ...]``，文件允许注释）
- Claude Code CLI：``~/.claude/settings.json`` 中的 ``env``（``{NAME: value}``）

读到的 token 只返回给调用方用于加密保存；本模块不打印、不记录 token，
``ClaudeCodeProfile`` 的 repr 也不包含它。
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tools.anthropic_compat import PROTOCOL_ANTHROPIC

_TOKEN_KEYS = ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY')
_MODEL_KEYS = (
    'ANTHROPIC_MODEL',
    'ANTHROPIC_DEFAULT_SONNET_MODEL',
    'ANTHROPIC_DEFAULT_OPUS_MODEL',
    'ANTHROPIC_SMALL_FAST_MODEL',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL',
)
_VSCODE_FLAVORS = ('Code', 'Code - Insiders', 'VSCodium', 'Cursor')


class ClaudeCodeImportError(Exception):
    pass


@dataclass(frozen=True)
class ClaudeCodeProfile:
    source_path: str
    base_url: str
    model: str
    token: str = field(repr=False)
    max_output_tokens: int | None = None

    @property
    def source_label(self) -> str:
        return os.path.basename(os.path.dirname(self.source_path)) + os.sep + os.path.basename(self.source_path)


def strip_jsonc(text: str) -> str:
    """去掉 // 与 /* */ 注释和对象/数组末尾逗号（字符串内容保持原样）。"""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = n if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    cleaned = ''.join(out)
    # 末尾逗号：, 后面只有空白再接 } 或 ]
    result: list[str] = []
    in_string = False
    i, n = 0, len(cleaned)
    while i < n:
        ch = cleaned[i]
        if in_string:
            result.append(ch)
            if ch == '\\' and i + 1 < n:
                result.append(cleaned[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            result.append(ch)
        elif ch == ',':
            j = i + 1
            while j < n and cleaned[j] in ' \t\r\n':
                j += 1
            if j < n and cleaned[j] in '}]':
                i += 1
                continue
            result.append(ch)
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _load_json_file(path: str) -> Any:
    with open(path, 'r', encoding='utf-8-sig') as stream:
        text = stream.read()
    try:
        return json.loads(strip_jsonc(text))
    except ValueError as exc:
        raise ClaudeCodeImportError(f'无法解析配置文件：{os.path.basename(path)}') from exc


def _env_from_settings(data: Any) -> dict[str, str]:
    if not isinstance(data, Mapping):
        return {}
    env: dict[str, str] = {}
    rows = data.get('claudeCode.environmentVariables')
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping) and row.get('name'):
                env[str(row['name']).strip()] = str(row.get('value') or '').strip()
    block = data.get('env')
    if isinstance(block, Mapping):
        for key, value in block.items():
            env.setdefault(str(key).strip(), str(value or '').strip())
    return env


def profile_from_env(env: Mapping[str, str], source_path: str = '') -> ClaudeCodeProfile | None:
    base_url = str(env.get('ANTHROPIC_BASE_URL') or '').strip()
    if not base_url:
        return None
    token = next((str(env[k]).strip() for k in _TOKEN_KEYS if str(env.get(k) or '').strip()), '')
    model = next((str(env[k]).strip() for k in _MODEL_KEYS if str(env.get(k) or '').strip()), '')
    try:
        max_out = int(str(env.get('CLAUDE_CODE_MAX_OUTPUT_TOKENS') or '').strip())
    except ValueError:
        max_out = None
    return ClaudeCodeProfile(
        source_path=source_path,
        base_url=base_url.rstrip('/'),
        model=model,
        token=token,
        max_output_tokens=max_out if max_out and max_out > 0 else None,
    )


def profile_from_file(path: str) -> ClaudeCodeProfile | None:
    return profile_from_env(_env_from_settings(_load_json_file(path)), source_path=path)


def candidate_paths(environ: Mapping[str, str] | None = None) -> list[str]:
    environ = os.environ if environ is None else environ
    paths: list[str] = []
    appdata = environ.get('APPDATA')
    if appdata:
        for flavor in _VSCODE_FLAVORS:
            paths.append(os.path.join(appdata, flavor, 'User', 'settings.json'))
    home = environ.get('USERPROFILE') or environ.get('HOME') or os.path.expanduser('~')
    if home:
        paths.append(os.path.join(home, '.claude', 'settings.json'))
    return paths


def find_profiles(paths: list[str] | None = None) -> list[ClaudeCodeProfile]:
    """按顺序扫描已知位置，返回含 ANTHROPIC_BASE_URL 的配置（解析失败的文件跳过）。"""
    found = []
    for path in paths if paths is not None else candidate_paths():
        if not os.path.isfile(path):
            continue
        try:
            profile = profile_from_file(path)
        except (OSError, ClaudeCodeImportError):
            continue
        if profile is not None:
            found.append(profile)
    return found


def to_model_item(profile: ClaudeCodeProfile, *, encrypted_token: str, existing_id: str = '',
                  name: str = '') -> dict[str, Any]:
    """转成软件模型配置；token 由调用方先加密再传入。"""
    item: dict[str, Any] = {
        'name': name or f'{profile.model or "内网模型"}（Claude Code 导入）',
        'provider': 'intranet',
        'api_protocol': PROTOCOL_ANTHROPIC,
        'enabled': True,
        'base_url': profile.base_url,
        'model': profile.model,
        'available_models': [profile.model] if profile.model else [],
        'token': encrypted_token,
        # Claude Code 网关本身靠工具调用工作，声明原生工具调用
        'agent_capability': 'native_tools',
    }
    if profile.max_output_tokens:
        item['max_tokens'] = profile.max_output_tokens
    if existing_id:
        item['id'] = existing_id
    return item


__all__ = [
    'ClaudeCodeImportError',
    'ClaudeCodeProfile',
    'candidate_paths',
    'find_profiles',
    'profile_from_env',
    'profile_from_file',
    'strip_jsonc',
    'to_model_item',
]
