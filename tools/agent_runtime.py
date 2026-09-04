# -*- coding: utf-8 -*-
"""Agent 工作台受控工具运行时：ReAct + Plan & Execute 组合。

结构化输出 + 本地解析 + 受控工具协议。
每个会话绑定一个 workspace_dir，所有文件操作校验后在其内执行。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone

from config import ensure_config_dir
from tools.ai_harness import strip_markdown_fence
from tools.intranet_llm import chat_completions
from tools.linux_guard import inspect_commands
from tools.sql_guard import redact_error

MAX_TOOL_ROUNDS = 10  # 单次请求最多工具调用轮次（含 Plan 阶段）
MAX_FILE_SIZE = 200 * 1024  # 单文件最大读取 200KB
TOOL_ID_PREFIX = 'call_'
INVALID_TOOL_ARGUMENTS = 'INVALID_TOOL_ARGUMENTS'
UNKNOWN_TOOL = 'UNKNOWN_TOOL'

# 白名单扩展名（用于目录树索引）
WHITELIST_EXTENSIONS = frozenset(
    '.py .js .ts .vue .html .css .scss .md .json .txt .yml .yaml .xml '
    '.sql .sh .bat .ps1 .rs .go .java .c .cpp .h .hpp .less .properties .gradle'.split()
)

# 默认跳过的大型/生成目录
IGNORED_DIR_NAMES = frozenset({
    '.git', '.svn', '.idea', '.gradle', 'target', 'build', 'out',
    'node_modules', '__pycache__', 'dist', '.pytest_cache', '.mypy_cache',
})


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _new_id() -> str:
    return uuid.uuid4().hex


def space_or_root(relative: str, workspace_dir: str) -> str:
    """SVN 操作路径：空值回落工作文件夹根；否则原样返回（交由 svn 处理相对路径）。"""
    rel = str(relative or '').strip()
    return workspace_dir if not rel else rel


# ─── 工具 Schema（供模型理解工具能力）────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        'name': 'list_dir',
        'description': '列目录内容（只读，仅返回文件名/目录名，不读文件内容）',
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '目录相对路径或空字符串（工作文件夹根）'},
            },
            'required': ['path'],
        },
    },
    {
        'name': 'read_file',
        'description': '读文件内容（限工作文件夹内 + 单文件 ≤200KB）',
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '文件相对路径'},
            },
            'required': ['path'],
        },
    },
    {
        'name': 'search_code',
        'description': '在工作文件夹内按关键字或正则搜索文件内容',
        'parameters': {
            'type': 'object',
            'properties': {
                'pattern': {'type': 'string', 'description': '搜索关键字或正则'},
                'file_pattern': {'type': 'string', 'description': '文件扩展名过滤，如 *.py（可选）'},
            },
            'required': ['pattern'],
        },
    },
    {
        'name': 'write_file',
        'description': '创建或覆盖文件，写入前展示完整内容预览',
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '文件相对路径'},
                'content': {'type': 'string', 'description': '文件完整内容'},
            },
            'required': ['path', 'content'],
        },
    },
    {
        'name': 'edit_file',
        'description': '精准替换文件中的指定字符串（old_str → new_str）',
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '文件相对路径'},
                'old_str': {'type': 'string', 'description': '待替换的原始字符串（必须精确匹配）'},
                'new_str': {'type': 'string', 'description': '替换后的新字符串'},
            },
            'required': ['path', 'old_str', 'new_str'],
        },
    },
    {
        'name': 'delete_file',
        'description': '删除文件（二次确认 + 危险标红）',
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '文件相对路径'},
            },
            'required': ['path'],
        },
    },
    {
        'name': 'run_test',
        'description': '在工作文件夹内运行 pytest 定向测试',
        'parameters': {
            'type': 'object',
            'properties': {
                'args': {'type': 'string', 'description': 'pytest 命令行参数，如 tests/test_foo.py::test_bar'},
            },
            'required': ['args'],
        },
    },
    {
        'name': 'run_svn',
        'description': 'SVN 只读操作（status/diff）或提交（需二次确认）',
        'parameters': {
            'type': 'object',
            'properties': {
                'operation': {'type': 'string', 'enum': ['status', 'diff', 'commit']},
                'message': {'type': 'string', 'description': 'commit 时填写，status/diff 忽略'},
                'paths': {'type': 'string', 'description': '操作路径，默认为工作文件夹根'},
            },
            'required': ['operation'],
        },
    },
]


# ─── 路径安全校验 ────────────────────────────────────────────────────────────

def _is_within_workspace(resolved: str, workspace_dir: str) -> bool:
    """realpath 后再比较，拦截 .. / 绝对路径 / junction 逃逸。"""
    try:
        real_path = os.path.normcase(os.path.realpath(resolved))
        real_root = os.path.normcase(os.path.realpath(workspace_dir))
        common = os.path.commonpath([real_path, real_root])
    except (OSError, ValueError):
        return False
    return common == real_root


def validate_path(relative_path: str, workspace_dir: str) -> tuple[bool, str, str]:
    """校验相对路径不越界。返回 (ok, resolved_path, error_msg)。"""
    if not relative_path:
        return False, '', '路径不能为空'

    # 禁止绝对路径
    if os.path.isabs(relative_path):
        return False, '', '禁止绝对路径'

    try:
        resolved = os.path.realpath(os.path.join(workspace_dir, relative_path))
    except Exception as e:
        return False, '', f'路径解析失败: {e}'

    if not _is_within_workspace(resolved, workspace_dir):
        return False, '', f'禁止越界访问: {resolved} 不在 {workspace_dir} 内'

    return True, resolved, ''


# ─── 工具实现 ────────────────────────────────────────────────────────────────

def _list_dir_impl(relative_path: str, workspace_dir: str) -> dict:
    # 空路径 → 工作文件夹根
    path_arg = relative_path if relative_path else '.'
    ok, resolved, err = validate_path(path_arg, workspace_dir)
    if not ok:
        return {'ok': False, 'error': err}

    if not os.path.isdir(resolved):
        return {'ok': False, 'error': f'不是目录: {relative_path}'}

    try:
        entries = os.listdir(resolved)
    except OSError as e:
        return {'ok': False, 'error': f'无法列目录: {e}'}

    result = []
    for name in sorted(entries):
        if name in IGNORED_DIR_NAMES or name.startswith('.'):
            continue
        full = os.path.join(resolved, name)
        is_dir = os.path.isdir(full)
        ext = os.path.splitext(name)[1].lower()
        # 非白名单扩展名且非目录则跳过
        if not is_dir and ext not in WHITELIST_EXTENSIONS:
            continue
        result.append({'name': name, 'type': 'dir' if is_dir else 'file'})
    return {'ok': True, 'entries': result}


def _read_file_impl(relative_path: str, workspace_dir: str) -> dict:
    ok, resolved, err = validate_path(relative_path, workspace_dir)
    if not ok:
        return {'ok': False, 'error': err}

    if not os.path.isfile(resolved):
        return {'ok': False, 'error': f'不是文件: {relative_path}'}

    try:
        size = os.path.getsize(resolved)
    except OSError:
        return {'ok': False, 'error': '无法获取文件大小'}

    if size > MAX_FILE_SIZE:
        return {'ok': False, 'error': f'文件超过 {MAX_FILE_SIZE // 1024}KB 限制，拒绝读取'}

    try:
        with open(resolved, 'rb') as f:
            raw = f.read()
    except OSError as e:
        return {'ok': False, 'success': False, 'tool': 'read_file', 'error': f'读取失败: {e}', 'data': None}

    from tools.text_file_codec import decode_text_bytes
    decoded = decode_text_bytes(raw, filename=relative_path)
    if not decoded.get('ok') or decoded.get('binary'):
        return {
            'ok': False,
            'success': False,
            'tool': 'read_file',
            'error': decoded.get('error') or '文件包含二进制内容或未知编码，无法作为文本读取',
            'data': None,
        }

    content = decoded.get('text', '')
    return {
        'ok': True,
        'success': True,
        'tool': 'read_file',
        'path': relative_path,
        'size': size,
        'content': content,
        'data': content,
        'error': '',
    }


def _search_code_impl(pattern: str, workspace_dir: str, file_pattern: str = '') -> dict:
    """在工作文件夹内递归搜索含 pattern 的文件行。"""
    if not pattern:
        return {'ok': False, 'error': '搜索关键字不能为空'}

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return {'ok': False, 'error': f'正则表达式错误: {e}'}

    from tools.text_file_codec import decode_text_bytes, MAX_SEARCH_TEXT_FILE_SIZE

    results = []
    try:
        for root, dirs, files in os.walk(workspace_dir):
            # 跳过隐藏目录和常见生成/非源码目录
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in IGNORED_DIR_NAMES]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in WHITELIST_EXTENSIONS and not fname.startswith('.'):
                    continue
                # file_pattern 过滤（如 *.py）
                if file_pattern:
                    if not re.match(file_pattern.replace('*', '.*'), fname):
                        continue
                fpath = os.path.join(root, fname)
                try:
                    sz = os.path.getsize(fpath)
                    if sz > MAX_SEARCH_TEXT_FILE_SIZE:
                        continue
                    with open(fpath, 'rb') as bf:
                        raw = bf.read()
                    decoded = decode_text_bytes(raw, filename=fname, max_size=MAX_SEARCH_TEXT_FILE_SIZE)
                    if not decoded.get('ok') or decoded.get('binary'):
                        continue
                    text = decoded.get('text', '')
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            rel = os.path.relpath(fpath, workspace_dir)
                            results.append({
                                'file': rel,
                                'line': lineno,
                                'text': line,
                            })
                            if len(results) >= 200:  # 限制结果数
                                break
                except OSError:
                    continue
            if len(results) >= 200:
                break
    except OSError as e:
        return {'ok': False, 'error': f'搜索失败: {e}'}

    return {'ok': True, 'pattern': pattern, 'matches': results[:200]}


def _write_file_impl(relative_path: str, content: str, workspace_dir: str) -> dict:
    ok, resolved, err = validate_path(relative_path, workspace_dir)
    if not ok:
        return {'ok': False, 'error': err}

    # 检查是否覆盖已有文件
    existed = os.path.exists(resolved)
    try:
        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        return {'ok': False, 'error': f'写入失败: {e}'}

    return {
        'ok': True,
        'path': relative_path,
        'existed': existed,
        'size': len(content),
        'action': '覆盖' if existed else '创建',
    }


def _edit_file_impl(relative_path: str, old_str: str, new_str: str, workspace_dir: str) -> dict:
    ok, resolved, err = validate_path(relative_path, workspace_dir)
    if not ok:
        return {'ok': False, 'error': err}

    if not os.path.isfile(resolved):
        return {'ok': False, 'error': f'文件不存在: {relative_path}'}

    try:
        with open(resolved, 'r', encoding='utf-8') as f:
            original = f.read()
    except OSError as e:
        return {'ok': False, 'error': f'读取失败: {e}'}

    if old_str not in original:
        return {'ok': False, 'error': '未找到匹配 old_str 的内容（必须精确匹配）'}

    new_content = original.replace(old_str, new_str, 1)
    try:
        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except OSError as e:
        return {'ok': False, 'error': f'写入失败: {e}'}

    return {
        'ok': True,
        'path': relative_path,
        'replaced': True,
    }


def _delete_file_impl(relative_path: str, workspace_dir: str) -> dict:
    ok, resolved, err = validate_path(relative_path, workspace_dir)
    if not ok:
        return {'ok': False, 'error': err}

    if not os.path.exists(resolved):
        return {'ok': False, 'error': f'文件不存在: {relative_path}'}

    try:
        os.unlink(resolved)
    except OSError as e:
        return {'ok': False, 'error': f'删除失败: {e}'}

    return {'ok': True, 'path': relative_path}


def _run_test_impl(args: str, workspace_dir: str) -> dict:
    """在工作文件夹内运行 pytest 命令。

    参数经白名单过滤；禁止一切可能执行任意代码或污染环境的参数。
    因为参数由 split() 拆词传给 subprocess（无 shell），shell 元字符本身不构成注入，
    但为保险仍拦截常见元字符，并仅放行安全的只读 pytest 参数。
    """
    if not args:
        return {'ok': False, 'error': 'pytest 参数不能为空'}

    tokens = args.strip().split()
    # 明确禁止的 pytest 参数（按 token 前缀精确匹配）
    FORBIDDEN_PREFIX = (
        '--collect-only', '--cache-clear', '--pdb', '--pdbcls',
        '--trace', '--capture=', '-p', '--pyargs', '--import-mode=',
    )
    FORBIDDEN_EXACT = {'-p', '-x', '--x', '--exitfirst', '-k', '--lf', '--ff', '-W'}
    for tok in tokens:
        if tok in FORBIDDEN_EXACT:
            return {'ok': False, 'error': f'禁止的参数: {tok}'}
        if any(tok.startswith(p) for p in FORBIDDEN_PREFIX):
            return {'ok': False, 'error': f'禁止的参数: {tok}'}
    # 拦截 shell 元字符（防拼接绕过）
    import re as _re
    if _re.search(r'[\;\|\&\`\$\(\)\{\}\<\>]', args):
        return {'ok': False, 'error': '禁止的 shell 元字符'}

    try:
        result = subprocess.run(
            ['python', '-m', 'pytest'] + tokens,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            'ok': result.returncode == 0,
            'returncode': result.returncode,
            'stdout': result.stdout[-3000:],  # 截断防溢出
            'stderr': result.stderr[-1500:],
        }
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': '测试超时（120s）'}
    except Exception as e:
        return {'ok': False, 'error': f'执行失败: {e}'}


def _run_svn_impl(operation: str, workspace_dir: str, message: str = '', paths: str = '') -> dict:
    """SVN 只读操作或提交。

    status/diff 为只读；commit 为写操作，需调用方经 confirm_cb 二次确认。
    _svn_run 失败（超时/异常/returncode!=0）时返回 ok=False，不再伪装成功。
    """
    if operation not in ('status', 'diff', 'commit'):
        return {'ok': False, 'error': f'未知 SVN 操作: {operation}'}

    target = space_or_root(paths, workspace_dir)

    def _svn_run(args: list) -> dict:
        try:
            result = subprocess.run(
                ['svn'] + args,
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {'ok': result.returncode == 0,
                    'returncode': result.returncode,
                    'stdout': result.stdout[-5000:], 'stderr': result.stderr[-2000:]}
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': 'SVN 命令超时（60s）'}
        except Exception as e:
            return {'ok': False, 'error': f'SVN 命令失败: {e}'}

    if operation == 'status':
        r = _svn_run(['status', target])
        if not r.get('ok') or r.get('stderr'):
            err = r.get('error') or r.get('stderr', '').strip()[:1000] or 'svn status 失败'
            return {'ok': False, 'operation': 'status', 'error': err}
        lines = [l for l in r.get('stdout', '').splitlines() if l.strip()]
        return {'ok': True, 'operation': 'status',
                'output': '\n'.join(lines) if lines else '工作副本干净，无本地改动'}
    elif operation == 'diff':
        r = _svn_run(['diff', target])
        if not r.get('ok') or r.get('stderr'):
            err = r.get('error') or r.get('stderr', '').strip()[:1000] or 'svn diff 失败'
            return {'ok': False, 'operation': 'diff', 'error': err}
        return {'ok': True, 'operation': 'diff', 'output': r.get('stdout', '')[:5000]}
    else:  # commit
        if not message:
            return {'ok': False, 'error': 'commit 必须提供 message'}
        r = _svn_run(['commit', '-m', message, target])
        if not r.get('ok') or r.get('stderr'):
            err = r.get('error') or r.get('stderr', '').strip()[:1000] or 'svn commit 失败'
            return {'ok': False, 'operation': 'commit', 'error': err,
                    'returncode': r.get('returncode')}
        return {'ok': True, 'operation': 'commit', 'output': r.get('stdout', '')[:3000],
                'returncode': r.get('returncode')}


# ─── 工具调度 ────────────────────────────────────────────────────────────────

def execute_tool(
    tool: str,
    args: dict,
    workspace_dir: str,
    confirm_cb=None,  # (title, diff_content) -> bool
) -> dict:
    """执行单个工具，返回结果字典（始终含 ok 字段）。"""
    if tool == 'list_dir':
        return _list_dir_impl(args.get('path', ''), workspace_dir)
    elif tool == 'read_file':
        return _read_file_impl(args.get('path', ''), workspace_dir)
    elif tool == 'search_code':
        return _search_code_impl(
            args.get('pattern', ''),
            workspace_dir,
            args.get('file_pattern', ''),
        )
    elif tool == 'write_file':
        content = args.get('content', '')
        title = f"确认写入文件: {args.get('path', '')}"
        diff_content = f"文件: {args.get('path', '')}\n内容预览（前500字符）:\n{content[:500]}"
        if confirm_cb and not confirm_cb(title, diff_content):
            return {'ok': False, 'error': '用户取消写入'}
        return _write_file_impl(args.get('path', ''), content, workspace_dir)
    elif tool == 'edit_file':
        old_str = args.get('old_str', '')
        new_str = args.get('new_str', '')
        title = f"确认编辑文件: {args.get('path', '')}"
        diff_content = (f"文件: {args.get('path', '')}\n"
                        f"--- 将删除 ---\n{old_str[:300]}\n--- 替换为 ---\n{new_str[:300]}")
        if confirm_cb and not confirm_cb(title, diff_content):
            return {'ok': False, 'error': '用户取消编辑'}
        return _edit_file_impl(args.get('path', ''), old_str, new_str, workspace_dir)
    elif tool == 'delete_file':
        path = args.get('path', '')
        title = f"⚠️ 确认删除文件（危险）: {path}"
        diff_content = f"即将删除: {path}\n此操作不可恢复！"
        if confirm_cb and not confirm_cb(title, diff_content):
            return {'ok': False, 'error': '用户取消删除'}
        return _delete_file_impl(path, workspace_dir)
    elif tool == 'run_test':
        return _run_test_impl(args.get('args', ''), workspace_dir)
    elif tool == 'run_svn':
        operation = str(args.get('operation', ''))
        # commit 属写操作，必须二次确认（与 schema 声明一致）
        if operation == 'commit':
            diff_content = (f"SVN 提交\nmessage: {args.get('message', '')}\n"
                            f"路径: {space_or_root(args.get('paths', ''), workspace_dir)}")
            if confirm_cb and not confirm_cb('确认 SVN 提交', diff_content):
                return {'ok': False, 'error': '用户取消 SVN 提交'}
        return _run_svn_impl(
            operation,
            workspace_dir,
            args.get('message', ''),
            args.get('paths', ''),
        )
    else:
        return {
            'ok': False,
            'success': False,
            'tool': tool,
            'error': UNKNOWN_TOOL,
            'data': None,
        }


# 有效受控工具白名单及参数属性
KNOWN_TOOL_NAMES = frozenset(s['name'] for s in TOOL_SCHEMAS)
TOOL_ALLOWED_ARGS = {
    s['name']: frozenset((s.get('parameters') or {}).get('properties', {}).keys())
    for s in TOOL_SCHEMAS
}


def _looks_like_tool_call(obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get('tool'):
        return True
    name = str(obj.get('name') or '').strip()
    if name in KNOWN_TOOL_NAMES:
        return True
    if name and ('parameters' in obj or 'args' in obj or 'arguments' in obj):
        return True
    fn = obj.get('function')
    return isinstance(fn, dict) and bool(fn.get('name'))


def _coerce_tool_args(raw) -> tuple[dict, str]:
    """返回 (args, error)。error 非空表示 INVALID_TOOL_ARGUMENTS。"""
    if raw is None:
        return {}, ''
    if isinstance(raw, dict):
        return raw, ''
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}, ''
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}, INVALID_TOOL_ARGUMENTS
        if isinstance(data, dict):
            return data, ''
        return {}, INVALID_TOOL_ARGUMENTS
    return {}, INVALID_TOOL_ARGUMENTS


def normalize_tool_call(obj: dict) -> dict | None:
    """把 name/parameters、tool/args、function.name 及扁平 {"name": "list_dir", "path": ""} 统一成 {tool, args}。"""
    if not _looks_like_tool_call(obj):
        return None
    tool = ''
    raw_args = {}
    fn = obj.get('function') if isinstance(obj.get('function'), dict) else None
    if obj.get('tool'):
        tool = str(obj.get('tool') or '').strip()
        raw_args = obj['args'] if 'args' in obj else obj.get('parameters', obj.get('arguments', {}))
    elif fn and fn.get('name'):
        tool = str(fn.get('name') or '').strip()
        raw_args = fn.get('arguments', fn.get('parameters', fn.get('args', {})))
    else:
        tool = str(obj.get('name') or '').strip()
        if 'parameters' in obj:
            raw_args = obj['parameters']
        elif 'args' in obj:
            raw_args = obj['args']
        elif 'arguments' in obj:
            raw_args = obj['arguments']
        elif tool in KNOWN_TOOL_NAMES:
            # 扁平 incident 格式：严格按 schema properties 白名单提取参数
            allowed_props = TOOL_ALLOWED_ARGS.get(tool, frozenset())
            raw_args = {k: v for k, v in obj.items() if k in allowed_props}
        else:
            raw_args = {}

    args, err = _coerce_tool_args(raw_args)
    if not tool:
        return None
    out = {'tool': tool, 'args': args}
    if err:
        out['error'] = INVALID_TOOL_ARGUMENTS
    return out


def _flatten_tool_payloads(data) -> list[dict]:
    items: list[dict] = []
    if isinstance(data, dict):
        if isinstance(data.get('tool_calls'), list):
            for entry in data['tool_calls']:
                if isinstance(entry, dict):
                    items.append(entry)
            return items
        items.append(data)
        return items
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get('tool_calls'), list):
                items.extend(_flatten_tool_payloads(entry))
            elif isinstance(entry, dict):
                items.append(entry)
    return items


def parse_tool_calls(text: str) -> list[dict]:
    """从模型输出解析工具调用，并 normalize 为 canonical {tool, args}。"""
    raw = strip_markdown_fence(text).strip()
    if not raw:
        return []

    payloads: list[dict] = []
    try:
        data = json.loads(raw)
        payloads = _flatten_tool_payloads(data)
    except Exception:
        payloads = []

    if not payloads:
        i = 0
        n = len(raw)
        while i < n:
            j = raw.find('{', i)
            if j < 0:
                break
            depth = 0
            k = j
            while k < n:
                c = raw[k]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            else:
                i = j + 1
                continue
            candidate = raw[j:k + 1]
            try:
                obj = json.loads(candidate)
                payloads.extend(_flatten_tool_payloads(obj))
            except Exception:
                pass
            i = k + 1

    results = []
    for item in payloads:
        normalized = normalize_tool_call(item)
        if normalized:
            results.append(normalized)
    return results


# ─── ReAct + Plan & Execute 主循环 ───────────────────────────────────────────

def build_tool_system_prompt(workspace_dir: str) -> str:
    """构建发给模型的 system prompt（含工具 schema 和安全边界）。"""
    schemas_json = json.dumps(TOOL_SCHEMAS, ensure_ascii=False, indent=2)
    return (
        f'你是内网编程 Agent，工作文件夹为 {workspace_dir}。\n'
        f'你有以下受控工具可用，输出 JSON 调用：\n{schemas_json}\n'
        '协议：用户发任务 → 你先出计划（含步骤） → 默认自动执行（每步工具调用 → 观察结果 → 下一步）\n'
        '直到任务完成或输出最终答案。\n'
        '注意：\n'
        '1. 所有文件路径相对于工作文件夹，禁止越界（../ 绝对路径均被拦截）\n'
        '2. 写文件/删除文件/覆盖文件必须展示完整内容/差异并等待确认\n'
        '3. run_test/run_svn 只执行只读或已确认安全的操作\n'
        '4. 工具调用结果会脱敏后返回（敏感内容用 *** 替换）\n'
        '5. 若无法执行，说明原因并给出建议\n'
        f'6. 单次请求最多 {MAX_TOOL_ROUNDS} 轮工具调用，超出则停止\n'
    )


def run_agent_loop(
    user_message: str,
    workspace_dir: str,
    model_cfg: dict,
    messages: list[dict],
    tool_calls: list[dict],
    plan_confirm: bool = False,
    confirm_cb=None,  # (title: str, content: str) -> bool
    progress_cb=None,  # (role, content) -> None，实时回调用于流式 UI
    cancel_cb=None,  # () -> bool，用于检测外部取消信号
) -> tuple[str, list[dict], list[dict]]:
    """
    ReAct + Plan & Execute 主循环。
    """
    import json as _json

    system_prompt = build_tool_system_prompt(workspace_dir)

    # 初始化或追加用户消息
    if messages and messages[-1].get('role') == 'user' and messages[-1].get('content') == user_message:
        pass  # 已是最新用户消息，不重复追加
    else:
        messages.append({
            'id': _new_id(),
            'role': 'user',
            'content': user_message,
            'created_at': _now(),
        })

    recent_signatures: list[str] = []
    READ_ONLY_TOOLS = frozenset({'list_dir', 'read_file', 'search_code'})

    for round_idx in range(MAX_TOOL_ROUNDS):
        if cancel_cb and cancel_cb():
            cancels = '任务已由用户手动停止。'
            messages.append({'id': _new_id(), 'role': 'assistant', 'content': cancels, 'created_at': _now()})
            return cancels, messages, tool_calls

        # 构造本次调用消息（含 system + history）
        call_messages = [{'role': 'system', 'content': system_prompt}] + messages[-30:]

        # 调用模型
        try:
            raw_response = chat_completions(call_messages, cfg=model_cfg)
        except Exception as e:
            answer = f'模型调用失败: {redact_error(str(e))}'
            messages.append({'id': _new_id(), 'role': 'assistant', 'content': answer, 'created_at': _now()})
            return answer, messages, tool_calls

        if cancel_cb and cancel_cb():
            cancels = '任务已由用户手动停止。'
            messages.append({'id': _new_id(), 'role': 'assistant', 'content': cancels, 'created_at': _now()})
            return cancels, messages, tool_calls

        safe_response = redact_error(raw_response)
        parsed = parse_tool_calls(raw_response)
        if not parsed:
            messages.append({
                'id': _new_id(),
                'role': 'assistant',
                'content': safe_response,
                'created_at': _now(),
            })
            # 注意：最终自然语言回答由调用方在完成时统一渲染，此处不再 progress_cb 双写
            return safe_response, messages, tool_calls

        status = '正在调用工具：' + '、'.join(str(c.get('tool') or '') for c in parsed)
        if progress_cb:
            progress_cb('tool', status)

        # 重复调用熔断器检查
        for c in parsed:
            t_name = str(c.get('tool') or '')
            t_args = c.get('args') or {}
            sig = f"{t_name}:{_json.dumps(t_args, sort_keys=True, ensure_ascii=False)}"
            if t_name in READ_ONLY_TOOLS:
                if recent_signatures and recent_signatures[-1] == sig:
                    repeat_count = sum(1 for s in reversed(recent_signatures) if s == sig)
                    if repeat_count >= 2:
                        stop_msg = '模型重复请求相同工具调用，已停止以避免无效循环。'
                        messages.append({'id': _new_id(), 'role': 'assistant', 'content': stop_msg, 'created_at': _now()})
                        return stop_msg, messages, tool_calls
                recent_signatures.append(sig)
            else:
                recent_signatures.clear()

        # plan_confirm=True：执行前先把本轮工具调用作为计划交给用户确认
        if plan_confirm and confirm_cb:
            plan_lines = []
            for c in parsed:
                plan_lines.append(f"- {c.get('tool')}: {_json.dumps(c.get('args') or {}, ensure_ascii=False)}")
            plan_text = '准备执行以下工具调用：\n' + '\n'.join(plan_lines) if plan_lines else '模型未产生可执行工具调用'
            if not confirm_cb('确认执行计划', plan_text):
                cancels = '用户取消执行计划，未执行任何工具'
                messages.append({'id': _new_id(), 'role': 'assistant',
                                 'content': cancels, 'created_at': _now()})
                return cancels, messages, tool_calls

        for call in parsed:
            if cancel_cb and cancel_cb():
                cancels = '任务已由用户手动停止。'
                messages.append({'id': _new_id(), 'role': 'assistant', 'content': cancels, 'created_at': _now()})
                return cancels, messages, tool_calls

            tool_name = str(call.get('tool', ''))
            args = call.get('args') or {}
            tool_id = call.get('id') or _new_id()
            if call.get('error') == INVALID_TOOL_ARGUMENTS:
                result = {
                    'ok': False,
                    'success': False,
                    'tool': tool_name,
                    'error': INVALID_TOOL_ARGUMENTS,
                    'data': None,
                }
            else:
                result = execute_tool(tool_name, args, workspace_dir, confirm_cb=confirm_cb)
            result.setdefault('success', bool(result.get('ok')))
            result.setdefault('tool', tool_name)
            result_text = _json.dumps({
                'ok': bool(result.get('ok')),
                'success': bool(result.get('success', result.get('ok'))),
                'tool': tool_name,
                'data': result.get('data', result.get('content', result.get('entries', result.get('matches')))),
                'error': result.get('error') or '',
            }, ensure_ascii=False)
            safe_result = redact_error(result_text)

            tool_calls.append({
                'id': tool_id,
                'tool': tool_name,
                'args': args,
                'result': safe_result,
                'error': result.get('error', ''),
                'timestamp': _now(),
            })
            messages.append({
                'id': _new_id(),
                'role': 'tool',
                'content': safe_result,
                'tool_call_id': tool_id,
                'created_at': _now(),
            })
            if progress_cb:
                compact = {
                    'list_dir': '已读取项目目录',
                    'read_file': f"已读取文件 {args.get('path') or ''}".strip(),
                    'search_code': '已搜索代码',
                }.get(tool_name, f"工具 {tool_name} 已完成")
                if not result.get('ok'):
                    compact = f"{tool_name} 失败：{result.get('error') or ''}"
                progress_cb('tool', compact)

        if cancel_cb and cancel_cb():
            cancels = '任务已由用户手动停止。'
            messages.append({'id': _new_id(), 'role': 'assistant', 'content': cancels, 'created_at': _now()})
            return cancels, messages, tool_calls

    # 超出轮次上限
    max_round_msg = f'工具调用次数达到本轮上限（已达到最大工具调用轮次 {MAX_TOOL_ROUNDS}）。请重新发起请求或缩小任务范围。'
    messages.append({'id': _new_id(), 'role': 'assistant', 'content': max_round_msg, 'created_at': _now()})
    return (
        max_round_msg,
        messages,
        tool_calls,
    )
