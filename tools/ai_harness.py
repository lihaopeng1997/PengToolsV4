# -*- coding: utf-8 -*-
"""内网模型薄层：单轮草稿，不提供 shell / 文件系统工具。"""

from __future__ import annotations

import os
import re

from tools.intranet_llm import IntranetLlmError, chat_completions, is_enabled, load_ai_local

_SKILL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'resources', 'ai_skills', 'sql.md',
)

_DEFAULT_SQL_SKILL = (
    '你是保险核心内网场景的 Oracle SQL 助手。根据用户说明生成或改写 SQL。'
    '只输出 SQL，不要解释，不要 Markdown 围栏。'
    '不要编造表名以外的生产账密；不要写 DROP DATABASE。'
)


def _load_sql_skill() -> str:
    try:
        with open(_SKILL_PATH, 'r', encoding='utf-8') as stream:
            text = stream.read().strip()
        return text or _DEFAULT_SQL_SKILL
    except OSError:
        return _DEFAULT_SQL_SKILL


def strip_markdown_fence(text: str) -> str:
    raw = str(text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    return raw.strip()


def draft_sql(user_text: str, cfg=None) -> str:
    """把需求/SQL 说明发给内网模型，返回可粘贴的 SQL 草稿。"""
    prompt = str(user_text or '').strip()
    if not prompt:
        raise IntranetLlmError('请先输入需求说明或 SQL')
    settings = cfg if isinstance(cfg, dict) else load_ai_local()
    if not is_enabled(settings):
        raise IntranetLlmError('未启用内网模型，请先在设置中填写 URL 并探测')
    content = chat_completions(
        [
            {'role': 'system', 'content': _load_sql_skill()},
            {'role': 'user', 'content': prompt},
        ],
        cfg=settings,
    )
    return strip_markdown_fence(content)
