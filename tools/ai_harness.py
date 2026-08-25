# -*- coding: utf-8 -*-
"""兼容入口：SQL 草稿转发到 PTools Harness。"""

from __future__ import annotations

import re


def strip_markdown_fence(text: str) -> str:
    raw = str(text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    return raw.strip()


def draft_sql(user_text: str, cfg=None) -> str:
    from tools.ptools_harness import run_task
    return run_task('sql.draft', user_text, cfg=cfg)
