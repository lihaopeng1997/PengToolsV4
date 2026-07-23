# -*- coding: utf-8 -*-
"""XML 美化纯逻辑：去外层引号、反转义、保留声明、清晰缩进。与 Qt 解耦便于单测。"""
from __future__ import annotations

import json
import re
from xml.dom import minidom
from xml.parsers.expat import ExpatError

_XML_DECL_RE = re.compile(r'^\s*(<\?xml\b[^?]*\?>)', re.IGNORECASE | re.DOTALL)
_ESCAPE_HINT_RE = re.compile(r'\\[nrt"\\/]|\\u[0-9a-fA-F]{4}')
# 声明中的 encoding="..." / encoding='...'
_XML_ENCODING_ATTR_RE = re.compile(
    r'(<\?xml\b[^?]*?\bencoding\s*=\s*)([\'"])([^\'"]+)\2',
    re.IGNORECASE | re.DOTALL,
)
# Expat/minidom 仅可靠支持的“单字节/UTF”族；其余声明会导致 multi-byte encodings are not supported
_EXPAT_SAFE_ENCODINGS = frozenset({
    'utf-8', 'utf8', 'utf-16', 'utf16', 'utf-16le', 'utf-16be',
    'ascii', 'us-ascii', 'iso-8859-1', 'latin-1', 'latin1',
    'iso8859-1', 'iso_8859_1',
})


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


def _declared_encoding(raw: str) -> str | None:
    match = _XML_ENCODING_ATTR_RE.search(raw or '')
    if not match:
        return None
    return (match.group(3) or '').strip() or None


def _is_expat_safe_encoding(name: str | None) -> bool:
    if not name:
        return True
    key = name.strip().lower().replace('_', '-')
    if key in _EXPAT_SAFE_ENCODINGS:
        return True
    # utf-8-sig / utf8 变体
    if key.replace('-', '') in ('utf8', 'utf16', 'usascii', 'latin1', 'iso88591'):
        return True
    return False


def _prepare_xml_for_expat(raw: str) -> str:
    """把声明 encoding 改写为 UTF-8，避免 Expat「multi-byte encodings are not supported」。

    粘贴到 UI 的文本已是 Unicode；声明里的 GBK/GB2312 等只表示原文件编码，
    不能按字节再解码，解析前统一按 UTF-8 喂给 Expat 即可。输出阶段再恢复原声明。
    """
    if not raw:
        return raw
    enc = _declared_encoding(raw)
    if _is_expat_safe_encoding(enc):
        return raw

    def _repl(match: re.Match) -> str:
        # 保留原引号风格，编码名改为 utf-8
        return f'{match.group(1)}{match.group(2)}utf-8{match.group(2)}'

    rewritten, count = _XML_ENCODING_ATTR_RE.subn(_repl, raw, count=1)
    return rewritten if count else raw


def _format_parse_error(exc: ExpatError) -> str:
    line = getattr(exc, 'lineno', None) or 1
    # ExpatError.offset 为 0-based 列偏移；展示为 1-based 列号
    col = (getattr(exc, 'offset', None) or 0) + 1
    message = exc.args[0] if exc.args else str(exc)
    # 用户友好补充（仍可能从其它路径抛出）
    low = str(message).lower()
    if 'multi-byte' in low or 'encoding' in low:
        return (
            f'XML 格式错误：第 {line} 行，第 {col} 列，{message}。'
            '若声明为 GBK/GB2312 等，请升级本工具后重试（已自动按 UTF-8 解析并保留原声明）。'
        )
    return f'XML 格式错误：第 {line} 行，第 {col} 列，{message}'


def _collapse_pretty_lines(pretty: str) -> list[str]:
    """去掉 minidom 多余空行，保留有内容的行。"""
    return [line.rstrip() for line in pretty.splitlines() if line.strip()]


def format_xml_text(text: str, indent: str = '  ') -> str:
    """
    规范化输入并美化 XML。
    - 自动去外层双引号、反转义
    - 保留原有 XML 声明（若有；含 GBK 等声明名）
    - 缩进清晰，不转义中文为实体以外的破坏性处理
    """
    raw = normalize_xml_input(text)
    original_decl = _extract_declaration(raw)
    parse_source = _prepare_xml_for_expat(raw)

    try:
        # 以 UTF-8 字节解析；声明 encoding 已必要时改写为 utf-8
        document = minidom.parseString(parse_source.encode('utf-8'))
    except ExpatError as exc:
        raise ValueError(_format_parse_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 统一为可展示错误
        msg = str(exc)
        if 'multi-byte' in msg.lower():
            raise ValueError(
                'XML 解析失败：声明编码不被解析器支持。'
                '内容已是文本时，请将 encoding 改为 UTF-8 后重试，或升级后的版本会自动处理。'
            ) from exc
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
