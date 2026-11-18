# -*- coding: utf-8 -*-
"""统一 AI 事件。思考只留在内存里，不写会话、日志或诊断包。"""

from __future__ import annotations

from dataclasses import dataclass


REASONING_BYTE_LIMIT = 64 * 1024

_THINKING_STATES = frozenset({
    'thinking',
    'think',
    'reasoning',
    'reasoning_delta',
    'model_reasoning',
    'model_reasoning_delta',
})
_TOOL_STATES = frozenset({
    'tool',
    'tool_call',
    'tool_step',
    'tool_started',
    'calling_tool',
    'function_call',
})
_TOOL_LABELS = {
    'search_objects': '搜索对象',
    'describe_object': '查看表结构',
    'query_readonly': '只读查询',
    'read_file': '读取文件',
    'search_code': '搜索代码',
    'list_dir': '查看目录',
}


@dataclass(frozen=True)
class AiEvent:
    """One UI-facing AI event. ``text`` is already safe to show or ignore."""

    kind: str
    text: str = ''
    state: str = ''
    tool: str = ''
    label: str = ''


def phase_label(state: str) -> str:
    """Map an internal state to Chinese. Unknown states stay generic."""

    key = str(state or '').strip().lower().replace('-', '_')
    if key in _THINKING_STATES:
        return '正在思考'
    if key in _TOOL_STATES:
        return '正在执行操作'
    return '正在处理'


def tool_label(tool_name: str) -> str:
    """Map a tool name to a short Chinese action."""

    return _TOOL_LABELS.get(str(tool_name or '').strip(), '执行工具')


def make_phase(state: str) -> AiEvent:
    label = phase_label(state)
    return AiEvent(kind='phase', state=str(state or ''), text=label, label=label)


def make_tool_step(tool_name: str) -> AiEvent:
    label = tool_label(tool_name)
    return AiEvent(kind='tool_step', tool=str(tool_name or ''), text=label, label=label)


def _utf8_tail(text: str, limit: int) -> str:
    raw = str(text or '').encode('utf-8')
    if len(raw) <= limit:
        return str(text or '')
    return raw[-limit:].decode('utf-8', errors='ignore')


class ReasoningBuffer:
    """In-memory thinking text. Over the cap, only the tail is kept."""

    def __init__(self, limit: int = REASONING_BYTE_LIMIT) -> None:
        self.limit = int(limit)
        self._text = ''

    def add(self, text: str) -> str:
        if not text:
            return self._text
        self._text = _utf8_tail(self._text + str(text), self.limit)
        return self._text

    def text(self) -> str:
        return self._text
