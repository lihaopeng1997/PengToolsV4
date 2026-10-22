"""Explicit text tool-call formats used when a gateway does not return native tool_calls.

Qwen and several intranet gateways write calls into the assistant text:

    <tool_call><function=read_file><parameter=path>README.md</parameter></function></tool_call>

Only this markup, or JSON nested inside a tool_call tag, is a call. Prose that
happens to contain a JSON example is not.
"""

from __future__ import annotations

import json
import re
from typing import Any


_TOOL_CALL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.I | re.S)
_FUNCTION_BLOCK = re.compile(
    r"<function=([A-Za-z_][A-Za-z0-9_]*)>\s*(.*?)\s*</function>",
    re.I | re.S,
)
_PARAM_BLOCK = re.compile(
    r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>\s*(.*?)\s*</parameter>",
    re.I | re.S,
)
_FUNCTION_OPEN = re.compile(r"<function=([A-Za-z_][A-Za-z0-9_]*)>", re.I)
_PARAM_OPEN = re.compile(r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>", re.I)
_TRAILING_CLOSE = re.compile(r"\s*</(?:parameter|function|tool_call)>\s*$", re.I)

TEXT_PROTOCOL_INSTRUCTION = (
    "需要查数据或调用工具时，输出完整标记，应用会执行并把结果发回。"
    "没有工具结果之前，不要声称已经查完，也不要说自己无法访问数据库或文件系统。格式：\n"
    "<tool_call><function=工具名><parameter=参数名>参数值</parameter></function></tool_call>"
)


def _looks_like_json_literal(text: str) -> bool:
    if text[:1] in '{["':
        return True
    if text in {"true", "false", "null"}:
        return True
    body = text[1:] if text[:1] == "-" else text
    return bool(body) and body[0].isdigit()


def coerce_tool_value(raw: str) -> Any:
    """Keep plain text as text. Parse JSON objects, arrays, numbers and booleans."""

    text = str(raw or "").strip()
    text = _TRAILING_CLOSE.sub("", text).strip()
    if not text:
        return ""
    if not _looks_like_json_literal(text):
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if value is None:
        return ""
    return value


def _arguments_from_body(body: str) -> dict[str, Any]:
    closed = list(_PARAM_BLOCK.finditer(body))
    if closed:
        arguments: dict[str, Any] = {}
        for match in closed:
            arguments[match.group(1)] = coerce_tool_value(match.group(2))
        tail = body[closed[-1].end():]
        loose = _PARAM_OPEN.search(tail)
        if loose is not None:
            arguments[loose.group(1)] = coerce_tool_value(tail[loose.end():])
        return arguments
    opens = list(_PARAM_OPEN.finditer(body))
    arguments = {}
    for index, match in enumerate(opens):
        start = match.end()
        end = opens[index + 1].start() if index + 1 < len(opens) else len(body)
        arguments[match.group(1)] = coerce_tool_value(body[start:end])
    return arguments


def _calls_from_fragment(fragment: str) -> list[dict[str, Any]] | None:
    """Parse function tags. None means this fragment has no function tag."""

    if "<function=" not in fragment.lower():
        return None
    calls: list[dict[str, Any]] = []
    closed = list(_FUNCTION_BLOCK.finditer(fragment))
    if closed:
        for match in closed:
            calls.append({
                "name": match.group(1),
                "arguments": _arguments_from_body(match.group(2)),
            })
        tail = fragment[closed[-1].end():]
        if "<function=" not in tail.lower():
            return calls
        fragment = tail
    opened = _FUNCTION_OPEN.search(fragment)
    if opened is None:
        return calls or None
    body = fragment[opened.end():]
    body = re.split(r"</function>", body, maxsplit=1, flags=re.I)[0]
    calls.append({
        "name": opened.group(1),
        "arguments": _arguments_from_body(body),
    })
    return calls


def parse_qwen_tool_calls(text: str) -> list[dict[str, Any]] | None:
    """Return parsed calls, [] when markup is present but unusable, or None when absent."""

    raw = str(text or "")
    lowered = raw.lower()
    if "<tool_call" not in lowered and "<function=" not in lowered:
        return None
    calls: list[dict[str, Any]] = []
    blocks = list(_TOOL_CALL_BLOCK.finditer(raw))
    if blocks:
        for block in blocks:
            parsed = _calls_from_fragment(block.group(1))
            if not parsed:
                return []
            calls.extend(parsed)
        tail = raw[blocks[-1].end():]
        if "<tool_call" in tail.lower() or "<function=" in tail.lower():
            extra = _calls_from_fragment(re.sub(r"^.*?<tool_call>\s*", "", tail, count=1, flags=re.I | re.S))
            if not extra:
                return []
            calls.extend(extra)
        return calls
    fragment = raw
    start = lowered.find("<tool_call")
    if start >= 0:
        fragment = raw[start:]
        fragment = re.sub(r"^<tool_call>\s*", "", fragment, count=1, flags=re.I)
    parsed = _calls_from_fragment(fragment)
    if not parsed:
        return []
    return parsed


def strip_tool_markup(text: str) -> str:
    """Remove tool-call markup and leave the surrounding prose."""

    raw = str(text or "")
    without = _TOOL_CALL_BLOCK.sub("", raw)
    without = re.sub(r"<tool_call\b[^>]*>[\s\S]*\Z", "", without, flags=re.I)
    without = re.sub(r"<function=[^>]*>[\s\S]*\Z", "", without, flags=re.I)
    lines = [line.rstrip() for line in without.splitlines()]
    compacted: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if compacted and not blank:
                compacted.append("")
            blank = True
            continue
        blank = False
        compacted.append(line)
    return "\n".join(compacted).strip()


def visible_model_text(text: str) -> str:
    """去掉工具标记和只含 tool_calls/final_answer 的 JSON，留给界面的正文。"""

    cleaned = strip_tool_markup(text)
    pieces: list[str] = []
    index = 0
    while True:
        match = re.search(r'\{\s*"tool_calls"\s*:', cleaned[index:], flags=re.I)
        if match is None:
            pieces.append(cleaned[index:])
            break
        start = index + match.start()
        pieces.append(cleaned[index:start])
        blob = _balanced_json_object(cleaned, start)
        if not blob:
            pieces.append(cleaned[start:])
            break
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            pieces.append(blob)
        else:
            if not isinstance(data, dict) or set(data) - {"tool_calls", "final_answer"}:
                pieces.append(blob)
        index = start + len(blob)
    return strip_tool_markup("".join(pieces))


def _balanced_json_object(text: str, start: int) -> str:
    if start >= len(text) or text[start] != "{":
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""
