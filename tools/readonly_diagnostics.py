"""Read-only diagnostics toolkit bound to one code root.

This reuses ``tools.agent_runtime.execute_tool`` so path validation
(``realpath`` + workspace containment, which also defeats symlink escapes),
extension whitelisting and size limits are the ones already in production.
It adds the guards a diagnostics scenario needs and the generic workspace tool
does not provide:

* a hard tool allowlist - no write/edit/delete/command/SQL tools exist here;
* sensitive-file screening - ``.env``, private keys and credential files are
  refused even though ``read_file`` would otherwise accept them;
* bounded results so one call cannot flood the model context.

Nothing in this module executes a process, writes a file, or touches a
database.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from tools.agent_core.contracts import ToolCall, ToolResult
from tools.agent_runtime import TOOL_SCHEMAS, execute_tool

READ_ONLY_TOOLS = ('list_dir', 'read_file', 'search_code')

SENSITIVE_NAMES = frozenset({
    '.env', '.env.local', '.env.production', '.env.development',
    '.npmrc', '.pypirc', '.netrc', '.git-credentials', 'credentials',
    'credentials.json', 'secrets.json', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
    '.htpasswd', 'shadow',
})
SENSITIVE_SUFFIXES = ('.pem', '.key', '.pfx', '.p12', '.jks', '.keystore', '.ppk', '.kdbx')


@dataclass(frozen=True, slots=True)
class DiagnosticsLimits:
    """Hard caps for one tool call."""

    max_matches: int = 60
    max_line_chars: int = 300
    max_entries: int = 200
    max_file_chars: int = 12_000


def is_sensitive_path(relative_path: str) -> bool:
    """True when the path names a credential or private-key file."""
    name = os.path.basename(str(relative_path or '').replace('\\', '/')).strip().casefold()
    if not name:
        return False
    if name in SENSITIVE_NAMES:
        return True
    if name.startswith('id_') and '.' not in name:
        return True
    return name.endswith(SENSITIVE_SUFFIXES)


class ReadOnlyDiagnostics:
    """A closed, read-only tool registry over one authorized directory."""

    def __init__(self, code_root: str, *, allowed_tools=READ_ONLY_TOOLS,
                 limits: DiagnosticsLimits | None = None):
        root = os.path.realpath(os.path.abspath(str(code_root or '')))
        if not root or not os.path.isdir(root):
            raise ValueError('代码根目录不存在或不可读')
        unknown = [name for name in allowed_tools if name not in READ_ONLY_TOOLS]
        if unknown:
            raise ValueError(f'只读排障不允许这些工具: {", ".join(unknown)}')
        self.code_root = root
        self.allowed_tools = tuple(dict.fromkeys(allowed_tools))
        self.limits = limits or DiagnosticsLimits()
        self._schemas = {schema['name']: schema for schema in TOOL_SCHEMAS}

    # -- ToolRegistry -----------------------------------------------------
    def definitions(self):
        return tuple({
            'type': 'function',
            'function': {
                'name': schema['name'],
                'description': schema['description'],
                'parameters': schema['parameters'],
            },
        } for schema in (self._schemas[name] for name in self.allowed_tools))

    def execute(self, call: ToolCall, *, context=None, cancellation=None) -> ToolResult:
        name = str(getattr(call, 'name', '') or '')
        arguments = dict(getattr(call, 'arguments', None) or {})

        if name not in self.allowed_tools:
            return ToolResult(ok=False, code='TOOL_NOT_ALLOWED', data={
                'error': '该场景只允许只读工具',
                'tool': name,
                'allowed': list(self.allowed_tools),
            })
        if cancellation is not None and cancellation.is_cancelled():
            return ToolResult(ok=False, code='CANCELLED', data={'error': '任务已取消'})

        path = str(arguments.get('path') or '')
        if path and is_sensitive_path(path):
            return ToolResult(ok=False, code='SENSITIVE_PATH', data={
                'error': '该文件属于凭据/私钥/配置密钥，只读排障不会读取',
                'path': path,
            })

        # read_file must stay inside the per-call cap even if the model asks more.
        if name == 'read_file':
            requested = arguments.get('char_count')
            try:
                requested = int(requested) if requested is not None else self.limits.max_file_chars
            except (TypeError, ValueError):
                requested = self.limits.max_file_chars
            arguments['char_count'] = max(1, min(requested, self.limits.max_file_chars))

        cancel_cb = (lambda: cancellation.is_cancelled()) if cancellation is not None else None
        try:
            raw = execute_tool(name, arguments, self.code_root, confirm_cb=None, cancel_cb=cancel_cb)
        except Exception as exc:  # tool failures are evidence, not crashes
            return ToolResult(ok=False, code='TOOL_FAILED', data={
                'error': f'{type(exc).__name__}: {exc}',
                'tool': name,
            })

        if not isinstance(raw, dict) or not raw.get('ok'):
            error = str((raw or {}).get('error') or '工具执行失败')
            code = 'CANCELLED' if '取消' in error else 'TOOL_FAILED'
            return ToolResult(ok=False, code=code, data={'error': error, 'tool': name})

        data, truncated = self._trim(name, raw)
        return ToolResult(ok=True, code='OK', data=data, truncated=truncated,
                          scope={'root': os.path.basename(self.code_root)})

    # -- result shaping ---------------------------------------------------
    def _trim(self, name: str, raw: dict):
        limits = self.limits
        if name == 'search_code':
            matches = list(raw.get('matches') or [])
            cut = matches[:limits.max_matches]
            shaped = [{
                'file': item.get('file'),
                'line': item.get('line'),
                'text': str(item.get('text') or '')[:limits.max_line_chars],
            } for item in cut if not is_sensitive_path(str(item.get('file') or ''))]
            dropped = len(matches) - len(cut)
            return ({'pattern': raw.get('pattern'), 'matches': shaped,
                     'returned': len(shaped), 'dropped': max(0, dropped)},
                    bool(dropped) or len(shaped) < len(cut))
        if name == 'list_dir':
            entries = list(raw.get('entries') or [])
            kept = [entry for entry in entries[:limits.max_entries]
                    if not is_sensitive_path(str((entry or {}).get('name') or ''))]
            return ({**raw, 'entries': kept, 'returned': len(kept),
                     'dropped': max(0, len(entries) - len(kept))},
                    len(entries) > limits.max_entries)
        content = str(raw.get('content') or '')
        return ({**raw, 'content': content[:limits.max_file_chars],
                 'chars': len(content)},
                bool(raw.get('truncated')) or len(content) >= limits.max_file_chars)
