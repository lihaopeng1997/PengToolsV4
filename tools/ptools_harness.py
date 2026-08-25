# -*- coding: utf-8 -*-
"""PTools Harness：PengTools 专属内网任务层。

任务白名单：sql.draft / sql.optimize / linux.query。
无通用 shell、无全盘文件工具。
"""

from __future__ import annotations

import json
import os
import re

from tools.ai_harness import strip_markdown_fence
from tools.intranet_llm import IntranetLlmError, chat_completions, is_enabled, load_ai_local
from tools.linux_guard import inspect_commands

_SKILL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'resources', 'ai_skills',
)

TASKS = {
    'sql.draft': 'sql.md',
    'sql.optimize': 'sql_optimize.md',
    'linux.query': 'log_query.md',
}

_SQL_FALLBACK = (
    '你是 Oracle SQL 助手。只输出 SQL，不要解释，不要 Markdown 围栏。'
    '不要 DROP DATABASE。'
)
_OPTIMIZE_FALLBACK = (
    '优化用户给出的 Oracle SQL：更清晰、可维护，保持表名。只输出 SQL。'
)
_LINUX_FALLBACK = (
    '把用户的自然语言转成 Linux 只读查询命令。'
    '只允许 grep/tail/head/cat/ls/stat/ps/df/free/uptime 等查看命令。'
    '禁止 rm/mv/kill/reboot/chmod 与写文件。'
    '只输出 JSON：{"summary":"...","commands":["..."],"risk":"safe"}。'
)


def _load_skill(filename: str, fallback: str) -> str:
    path = os.path.join(_SKILL_DIR, filename)
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            text = stream.read().strip()
        return text or fallback
    except OSError:
        return fallback


def _skill_for(task: str) -> str:
    if task == 'sql.optimize':
        return _load_skill(TASKS[task], _OPTIMIZE_FALLBACK)
    if task == 'linux.query':
        return _load_skill(TASKS[task], _LINUX_FALLBACK)
    return _load_skill(TASKS.get(task) or 'sql.md', _SQL_FALLBACK)


def _extract_json_object(text: str) -> dict:
    raw = strip_markdown_fence(text)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {'summary': raw[:500], 'commands': [line.strip() for line in raw.splitlines() if line.strip()], 'risk': 'unknown'}


def run_task(task: str, user_text: str, *, context: str = '', cfg=None):
    """执行一个 PTools 任务。sql.* 返回 str，linux.query 返回 dict。"""
    name = str(task or '').strip()
    if name not in TASKS:
        raise IntranetLlmError(f'未知任务：{task}')
    prompt = str(user_text or '').strip()
    extra = str(context or '').strip()
    if extra:
        if len(extra) > 8000:
            extra = extra[-8000:]
        prompt = f'{prompt}\n\n---\n上下文：\n{extra}'.strip()
    if not prompt:
        raise IntranetLlmError('请先输入自然语言或选中内容')
    settings = cfg if isinstance(cfg, dict) else load_ai_local()
    if not is_enabled(settings):
        raise IntranetLlmError('未启用内网模型，请先在设置中填写 URL 并探测')
    content = chat_completions(
        [
            {'role': 'system', 'content': _skill_for(name)},
            {'role': 'user', 'content': prompt},
        ],
        cfg=settings,
    )
    if name.startswith('sql.'):
        return strip_markdown_fence(content)
    data = _extract_json_object(content)
    commands = data.get('commands')
    if not isinstance(commands, list):
        commands = [str(commands or '').strip()] if commands else []
    cleaned = [str(item).strip() for item in commands if str(item).strip()]
    allowed, rejected = inspect_commands(cleaned)
    return {
        'summary': str(data.get('summary') or '').strip(),
        'commands': cleaned,
        'allowed': allowed,
        'rejected': rejected,
        'risk': str(data.get('risk') or 'safe'),
    }
