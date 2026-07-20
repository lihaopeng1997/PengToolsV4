# -*- coding: utf-8 -*-
"""XML 美化纯逻辑：去外层引号、反转义、保留声明、清晰缩进。与 Qt 解耦便于单测。"""
from __future__ import annotations

import json
import re
from xml.dom import minidom
from xml.parsers.expat import ExpatError

_XML_DECL_RE = re.compile(r'^\s*(<\?xml\b[^?]*\?>)', re.IGNORECASE | re.DOTALL)
_ESCAPE_HINT_RE = re.compile(r'\\[nrt"\\/]|\\u[0-9a-fA-F]{4}')


def _strip_outer_double_quotes(text: str) -> str:
    """去掉首尾成对的英文双引号（可多层），中间内容保留。"""
    s = text
    while len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()
    return s


def _try_json_string_unescape(text: str) -> str | None:
    """把 JSON/日志里的转义字符串反解成真实文本。失败返回 None。"""
    candidates = [text]
    # 已去掉外层引号时，补回引号再走 json.loads
    if not (text.startswith('"') and text.endswith('"')):
        candidates.append('"' + text + '"')
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(value, str) and value:
            return value
    return None


def _looks_like_escaped(text: str) -> bool:
    return bool(_ESCAPE_HINT_RE.search(text))


def normalize_xml_input(text: str) -> str:
    """清洗粘贴文本：去空白、外层引号，并尽量反转义成真实 XML 源。"""
    if text is None or not str(text).strip():
        raise ValueError('XML 内容为空')
    s = str(text).strip()
    s = _strip_outer_double_quotes(s)
    if not s:
        raise ValueError('XML 内容为空')

    # 优先尝试 JSON 字符串反转义（覆盖 \" \\ \n \t \uXXXX 等）
    if _looks_like_escaped(s) or ('\\' in s and '<' in s):
        unescaped = _try_json_string_unescape(s)
        if unescaped is not None:
            s = unescaped.strip()
            s = _strip_outer_double_quotes(s)

    if not s:
        raise ValueError('XML 内容为空')
    return s


def _extract_declaration(raw: str) -> str | None:
    match = _XML_DECL_RE.match(raw)
    return match.group(1).strip() if match else None


def _format_parse_error(exc: ExpatError) -> str:
    line = getattr(exc, 'lineno', None) or 1
    # ExpatError.offset 为 0-based 列偏移；展示为 1-based 列号
    col = (getattr(exc, 'offset', None) or 0) + 1
    message = exc.args[0] if exc.args else str(exc)
    return f'XML 格式错误：第 {line} 行，第 {col} 列，{message}'


def _collapse_pretty_lines(pretty: str) -> list[str]:
    """去掉 minidom 多余空行，保留有内容的行。"""
    return [line.rstrip() for line in pretty.splitlines() if line.strip()]


def format_xml_text(text: str, indent: str = '  ') -> str:
    """
    规范化输入并美化 XML。
    - 自动去外层双引号、反转义
    - 保留原有 XML 声明（若有）
    - 缩进清晰，不转义中文为实体以外的破坏性处理
    """
    raw = normalize_xml_input(text)
    original_decl = _extract_declaration(raw)

    try:
        # 以 UTF-8 字节解析，避免声明 encoding 与字符串解码不一致
        document = minidom.parseString(raw.encode('utf-8'))
    except ExpatError as exc:
        raise ValueError(_format_parse_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 统一为可展示错误
        raise ValueError(f'XML 解析失败：{exc}') from exc

    # encoding=None（默认）返回 str，避免中文被弄成字节
    pretty = document.toprettyxml(indent=indent)
    lines = _collapse_pretty_lines(pretty)

    # minidom 总会输出默认声明；按是否有原声明替换/删除
    if lines and lines[0].lstrip().lower().startswith('<?xml'):
        if original_decl:
            lines[0] = original_decl
        else:
            lines = lines[1:]

    if not lines:
        raise ValueError('XML 内容为空')
    return '\n'.join(lines) + '\n'
