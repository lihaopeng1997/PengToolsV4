"""Explicit text tool-call formats used when a gateway does not return native tool_calls.

Qwen and several intranet gateways write calls into the assistant text:

    <tool_call><function=read_file><parameter=path>README.md</parameter></function></tool_call>

Only this markup, or JSON nested inside a tool_call tag, is a call. Prose that
happens to contain a JSON example is not.
"""

from __future__ import annotations

import json
import re
from typing import Any, Collection


_TOOL_CALL_BLOCK = re.compile(r"<(?:tool_call|function_call)>\s*(.*?)\s*</(?:tool_call|function_call)>", re.I | re.S)
_FUNCTION_BLOCK = re.compile(
    r"<(?:function|invoke)(?:=|\s+name=)\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*>\s*(.*?)\s*</(?:function|invoke)>",
    re.I | re.S,
)
_PARAM_BLOCK = re.compile(
    r"<parameter(?:=|\s+name=)\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*>\s*(.*?)\s*</parameter>",
    re.I | re.S,
)
_FUNCTION_OPEN = re.compile(
    r"<(?:function|invoke)(?:=|\s+name=)\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*>",
    re.I,
)
_PARAM_OPEN = re.compile(
    r"<parameter(?:=|\s+name=)\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*>",
    re.I,
)
_TRAILING_CLOSE = re.compile(r"\s*</(?:parameter|function|invoke|tool_call|function_call)>\s*$", re.I)

TEXT_PROTOCOL_INSTRUCTION = (
    "需要查数据或调用工具时，输出完整标记，应用会执行并把结果发回。"
    "没有工具结果之前，不要声称已经查完，也不要说自己无法访问数据库或文件系统。格式：\n"
    "<tool_call><function=工具名><parameter=参数名>参数值</parameter></function></tool_call>"
)

# 工具结果不是结束语。数据中心和代码 Agent 的系统提示都带这一句。
EVIDENCE_CONTINUATION_INSTRUCTION = (
    "工具结果只是中间证据，拿到结果后必须继续调用工具或给出基于证据的完整回答，不要只描述计划。"
)

# 只映射到本次允许列表里已经有的工具。目标不在列表中时调用方必须拒绝，不能放宽白名单。
TOOL_NAME_ALIASES = {
    "describe_table": "describe_object",
    "get_columns": "describe_object",
    "list_columns": "describe_object",
    "get_table_schema": "describe_object",
    "get_table_info": "describe_object",
    "search_tables": "search_objects",
    "list_tables": "search_objects",
    "find_tables": "search_objects",
    "execute_sql": "query_readonly",
    "run_sql": "query_readonly",
    "query_sql": "query_readonly",
    "select_query": "query_readonly",
    "execute_query": "query_readonly",
    "sql_query": "query_readonly",
    "find_file": "find_files",
    "glob": "find_files",
    "glob_files": "find_files",
    "locate_files": "find_files",
}


def build_text_tool_instruction(tools: Any) -> str:
    """Build a compact text-protocol tool declaration when native `tools` is rejected at runtime."""
    items: list[str] = []
    if isinstance(tools, (list, tuple)):
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            fn = entry.get("function") if isinstance(entry.get("function"), dict) else entry
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            desc = str(fn.get("description") or "").strip()
            params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
            props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
            req = set(params.get("required") or ())
            sig = ", ".join(f"{k}{'*' if k in req else ''}" for k in props)
            items.append(f"- {name}({sig}): {desc[:120]}")
    if not items:
        return TEXT_PROTOCOL_INSTRUCTION
    return TEXT_PROTOCOL_INSTRUCTION + "\n可用工具：\n" + "\n".join(items)

_INVALID = object()


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


def _strip_embedded_fence(text: str) -> str:
    """Drop one surrounding ``` / ```json fence. Leave other prose unchanged."""

    raw = str(text or "").strip()
    match = re.fullmatch(r"```(?:json|JSON)?\s*([\s\S]*?)\s*```", raw)
    if match:
        return match.group(1).strip()
    return raw


def _coerce_arguments(raw: Any) -> Any:
    """Return a dict, or ``_INVALID`` when a JSON argument payload cannot be used."""

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = _strip_embedded_fence(raw)
        if not text:
            return {}
        if text[:1] not in "{[":
            return _INVALID
        try:
            value = json.loads(text, strict=False)
        except json.JSONDecodeError:
            return _INVALID
        if isinstance(value, dict):
            return value
        return _INVALID
    return _INVALID


def _call_from_mapping(obj: Any) -> Any:
    """One ``{name, arguments}`` object, or ``_INVALID`` when the shape is unusable."""

    if not isinstance(obj, dict):
        return _INVALID
    name = obj.get("name")
    arguments = None
    if not isinstance(name, str) or not name.strip():
        function = obj.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str) and function.get("name").strip():
            name = function.get("name")
            arguments = function.get("arguments", function.get("parameters", function.get("args")))
        else:
            tool = obj.get("tool")
            if isinstance(tool, str) and tool.strip():
                name = tool
                arguments = obj.get("arguments", obj.get("args", obj.get("parameters")))
            else:
                return _INVALID
    if arguments is None:
        if "arguments" in obj:
            arguments = obj.get("arguments")
        elif "args" in obj:
            arguments = obj.get("args")
        elif "parameters" in obj:
            arguments = obj.get("parameters")
        else:
            arguments = {}
    coerced = _coerce_arguments(arguments)
    if coerced is _INVALID:
        return _INVALID
    return {"name": str(name).strip(), "arguments": coerced}


def _calls_from_json_payload(body: str) -> Any:
    """Calls from a JSON object/array, ``None`` when it is not JSON, ``_INVALID`` when it is broken."""

    stripped = _strip_embedded_fence(body)
    if not stripped:
        return None
    candidate = stripped
    if candidate[0] not in "{[":
        return None
    if candidate[0] == "{":
        blob = _balanced_json_object(candidate, 0)
        if not blob:
            return _INVALID
        trailing = candidate[len(blob):].strip()
        if trailing and trailing != "```":
            return _INVALID
        try:
            value = json.loads(blob, strict=False)
        except json.JSONDecodeError:
            return _INVALID
    else:
        try:
            value = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            return _INVALID
    if isinstance(value, dict) and isinstance(value.get("tool_calls"), list) and "name" not in value and "tool" not in value:
        items = value.get("tool_calls")
        if not items:
            return _INVALID
    elif isinstance(value, list):
        items = value
        if not items:
            return _INVALID
    else:
        items = [value]
    calls: list[dict[str, Any]] = []
    for item in items:
        call = _call_from_mapping(item)
        if call is _INVALID:
            return _INVALID
        calls.append(call)
    return calls


def _arguments_from_function_body(body: str) -> dict[str, Any] | None:
    """Parameter tags, a JSON object, or None when a JSON body is unusable."""

    if "<parameter=" in str(body or "").lower() or "<parameter " in str(body or "").lower():
        return _arguments_from_body(body)
    stripped = _strip_embedded_fence(body)
    if not stripped:
        return {}
    if stripped[:1] in "{[":
        coerced = _coerce_arguments(stripped)
        if coerced is _INVALID:
            return None
        return coerced
    return _arguments_from_body(body)


def _calls_from_fragment(fragment: str) -> list[dict[str, Any]] | None:
    """Parse function tags. None means this fragment has no usable function tag."""

    lowered = fragment.lower()
    if "<function=" not in lowered and "<function " not in lowered and "<invoke " not in lowered:
        return None
    calls: list[dict[str, Any]] = []
    closed = list(_FUNCTION_BLOCK.finditer(fragment))
    if closed:
        for match in closed:
            arguments = _arguments_from_function_body(match.group(2))
            if arguments is None:
                return None
            calls.append({
                "name": match.group(1),
                "arguments": arguments,
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
    arguments = _arguments_from_function_body(body)
    if arguments is None:
        return None
    calls.append({
        "name": opened.group(1),
        "arguments": arguments,
    })
    return calls


def _calls_from_tool_block(body: str) -> list[dict[str, Any]] | None:
    """Calls inside one ``<tool_call>`` body. None means the body is unusable."""

    lowered_body = str(body or "").lower()
    if "<function=" in lowered_body or "<function " in lowered_body or "<invoke " in lowered_body:
        return _calls_from_fragment(body)
    payload = _calls_from_json_payload(body)
    if payload is None or payload is _INVALID or not payload:
        return None
    return payload


def parse_qwen_tool_calls(text: str) -> list[dict[str, Any]] | None:
    """Return parsed calls, [] when markup is present but unusable, or None when absent.

    Accepts the parameter-tag form, ``<tool_call>{name, arguments}</tool_call>``
    (several tags, surrounding prose, a ```json fence, arguments as a JSON
    string) and ``<function=name>{json}</function>``.
    """

    raw = str(text or "")
    lowered = raw.lower()
    if (
        "<tool_call" not in lowered
        and "<function_call" not in lowered
        and "<function=" not in lowered
        and "<function " not in lowered
        and "<invoke " not in lowered
    ):
        return None
    calls: list[dict[str, Any]] = []
    blocks = list(_TOOL_CALL_BLOCK.finditer(raw))
    if blocks:
        for block in blocks:
            parsed = _calls_from_tool_block(block.group(1))
            if not parsed:
                return []
            calls.extend(parsed)
        tail = raw[blocks[-1].end():]
        if "<tool_call" in tail.lower() or "<function=" in tail.lower():
            extra = _calls_from_tool_block(re.sub(r"^.*?<tool_call>\s*", "", tail, count=1, flags=re.I | re.S))
            if not extra:
                return []
            calls.extend(extra)
        return calls
    fragment = raw
    start = lowered.find("<tool_call")
    if start >= 0:
        fragment = raw[start:]
        fragment = re.sub(r"^<tool_call>\s*", "", fragment, count=1, flags=re.I)
    parsed = _calls_from_tool_block(fragment)
    if not parsed:
        return []
    return parsed


def resolve_tool_alias(name: str, allowed: Collection[str]) -> str:
    """Map a tool name onto this turn's allow-list. Empty string means reject."""

    cleaned = str(name or "").strip()
    allowed_set = {str(item) for item in allowed if str(item)}
    if cleaned in allowed_set:
        return cleaned
    target = TOOL_NAME_ALIASES.get(cleaned)
    if target and target in allowed_set:
        return target
    return ""


def prepare_text_tool_calls(
    text: str,
    allowed: Collection[str],
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``(calls, None)`` when markup maps onto allowed tools.

    ``(None, None)`` when the text has no tool markup.
    ``(None, "parse_failed"|"tool_rejected")`` when markup cannot be executed.
    A rejected alias is never rewritten onto a tool outside ``allowed``.
    """

    parsed = parse_qwen_tool_calls(text)
    if parsed is None:
        return None, None
    if not parsed:
        return None, "parse_failed"
    prepared: list[dict[str, Any]] = []
    for item in parsed:
        name = resolve_tool_alias(str(item.get("name") or ""), allowed)
        if not name:
            return None, "tool_rejected"
        arguments = item.get("arguments") or {}
        if not isinstance(arguments, dict):
            return None, "parse_failed"
        prepared.append({"name": name, "arguments": dict(arguments)})
    return prepared, None


def strip_tool_markup(text: str) -> str:
    """Remove tool-call markup and leave the surrounding prose."""

    raw = str(text or "")
    without = _TOOL_CALL_BLOCK.sub("", raw)
    without = _FUNCTION_BLOCK.sub("", without)
    without = re.sub(r"```(?:json|JSON)?\s*```", "", without)
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


def visible_model_text(text: str, *, streaming: bool = False) -> str:
    """去掉工具标记和只含 tool_calls/final_answer 的 JSON，留给界面的正文。"""

    raw = str(text or "")
    if streaming:
        # A transport chunk may end in the middle of a tag or protocol key.
        # This function expects the accumulated turn, never an isolated delta.
        tag_start = raw.rfind("<")
        if tag_start >= 0:
            tail = raw[tag_start:].lower()
            markers = ("<tool_call", "</tool_call", "<function", "</function", "<parameter", "</parameter")
            if ">" not in tail and any(marker.startswith(tail) or tail.startswith(marker) for marker in markers):
                raw = raw[:tag_start]
        json_start = raw.rfind("{")
        if json_start >= 0:
            tail = re.sub(r"\s+", "", raw[json_start:])
            if any(marker.startswith(tail) for marker in ('{"tool_calls":', '{"final_answer":')):
                raw = raw[:json_start]
    cleaned = strip_tool_markup(raw)
    cleaned = re.sub(r'</?(?:parameter|function|tool_call)\b[^>]*>', '', cleaned, flags=re.I)
    pieces: list[str] = []
    index = 0
    while True:
        match = re.search(r'\{\s*"(?:tool_calls|final_answer)"\s*:', cleaned[index:], flags=re.I)
        if match is None:
            pieces.append(cleaned[index:])
            break
        start = index + match.start()
        pieces.append(cleaned[index:start])
        blob = _balanced_json_object(cleaned, start)
        if not blob:
            if streaming:
                pieces.append(_streaming_final_answer(cleaned, start))
            else:
                pieces.append(cleaned[start:])
            break
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            pieces.append(blob)
        else:
            if not isinstance(data, dict) or set(data) - {"tool_calls", "final_answer"}:
                pieces.append(blob)
            elif isinstance(data.get("final_answer"), str):
                pieces.append(data["final_answer"])
        index = start + len(blob)
    return strip_tool_markup("".join(pieces))


def _streaming_final_answer(text: str, start: int) -> str:
    """Show the opened ``final_answer`` string before its closing brace arrives."""

    tail = text[start:]
    match = re.match(r'\{\s*"final_answer"\s*:\s*', tail, flags=re.I)
    if match is None:
        return ""
    value_at = start + match.end()
    if value_at >= len(text) or text[value_at] != '"':
        return ""
    return _partial_json_string(text, value_at)


def _partial_json_string(text: str, start: int) -> str:
    """Decode a JSON string that may still be missing its closing quote."""

    if start >= len(text) or text[start] != '"':
        return ""
    chars: list[str] = []
    index = start + 1
    simple = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/"}
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text):
                break
            nxt = text[index + 1]
            if nxt in simple:
                chars.append(simple[nxt])
                index += 2
                continue
            if nxt == "u":
                hexpart = text[index + 2:index + 6]
                if len(hexpart) < 4 or re.fullmatch(r"[0-9a-fA-F]{4}", hexpart) is None:
                    break
                chars.append(chr(int(hexpart, 16)))
                index += 6
                continue
            break
        if char == '"':
            break
        chars.append(char)
        index += 1
    return "".join(chars)


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
