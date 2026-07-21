# -*- coding: utf-8 -*-
"""Java 开发常用文本转换（离线，不落盘）。"""

from __future__ import annotations

import base64
import datetime
import re
from urllib.parse import quote, unquote


class TextHelperError(ValueError):
    pass


def encode_base64(text: str) -> str:
    data = (text or '').encode('utf-8')
    return base64.b64encode(data).decode('ascii')


def decode_base64(text: str) -> str:
    raw = re.sub(r'\s+', '', text or '')
    if not raw:
        raise TextHelperError('Base64 输入为空')
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise TextHelperError(f'Base64 解码失败：{exc}') from exc
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise TextHelperError('解码结果不是合法 UTF-8 文本（不猜测二进制文件）') from exc


def encode_url(text: str) -> str:
    # 保留可读的 query 分隔符：对整串使用 quote，safe 保留 =&?/#:
    return quote(text or '', safe='=&?/#:/%')


def decode_url(text: str) -> str:
    try:
        return unquote(text or '')
    except Exception as exc:
        raise TextHelperError(f'URL 解码失败：{exc}') from exc


_UNICODE_ESCAPE_RE = re.compile(r'\\u([0-9a-fA-F]{4})')


def encode_unicode_escapes(text: str) -> str:
    out = []
    for ch in text or '':
        code = ord(ch)
        if code > 127:
            out.append(f'\\u{code:04x}')
        else:
            out.append(ch)
    return ''.join(out)


def decode_unicode_escapes(text: str) -> str:
    def repl(match):
        return chr(int(match.group(1), 16))

    # 仅替换 \uXXXX；普通反斜杠保留
    return _UNICODE_ESCAPE_RE.sub(repl, text or '')


def parse_timestamp_input(text: str):
    """返回 (datetime_utc_naive_or_local, kind)。"""
    raw = (text or '').strip()
    if not raw:
        raise TextHelperError('时间输入为空')
    if re.fullmatch(r'\d{10}', raw):
        ts = int(raw)
        return datetime.datetime.fromtimestamp(ts), 'unix10'
    if re.fullmatch(r'\d{13}', raw):
        ts = int(raw) / 1000.0
        return datetime.datetime.fromtimestamp(ts), 'unix13'
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d'):
        try:
            return datetime.datetime.strptime(raw, fmt), 'datetime'
        except ValueError:
            continue
    raise TextHelperError('无法识别时间：支持 10/13 位时间戳或 yyyy-MM-dd[ HH:mm:ss]')


def format_timestamp_bundle(text: str) -> str:
    dt, kind = parse_timestamp_input(text)
    # 本地时间视为系统时区；北京时间按 UTC+8 显示
    local = dt
    beijing = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8))) if dt.tzinfo else (
        datetime.datetime.fromtimestamp(dt.timestamp(), tz=datetime.timezone(datetime.timedelta(hours=8)))
    )
    unix10 = int(local.timestamp())
    unix13 = int(local.timestamp() * 1000)
    return (
        f'输入类型：{kind}\n'
        f'Unix 秒（10 位）：{unix10}\n'
        f'Unix 毫秒（13 位）：{unix13}\n'
        f'本地时间：{local.strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'北京时间（UTC+8）：{beijing.strftime("%Y-%m-%d %H:%M:%S %z")}'
    )


_EXCEPTION_LINE = re.compile(r'^([\w.$]+(?:Error|Exception|Throwable)(?:\$\w+)?):\s*(.*)$')
_CAUSED_BY = re.compile(r'^Caused by:\s*([\w.$]+)(?::\s*(.*))?$')
_AT_LINE = re.compile(r'^\s*at\s+([\w.$]+)\(([^)]*)\)$')


def extract_java_stack(text: str) -> dict:
    lines = (text or '').splitlines()
    first_exception = ''
    first_message = ''
    caused = []
    first_business = ''
    business_prefixes = ('com.', 'cn.', 'org.springframework', 'org.apache')
    skip_prefixes = ('java.', 'javax.', 'jdk.', 'sun.', 'com.sun.', 'kotlin.', 'scala.')

    for line in lines:
        m = _EXCEPTION_LINE.match(line.strip())
        if m and not first_exception:
            first_exception = m.group(1)
            first_message = m.group(2) or ''
            break
    for line in lines:
        m = _CAUSED_BY.match(line.strip())
        if m:
            caused.append((m.group(1), m.group(2) or ''))
    for line in lines:
        m = _AT_LINE.match(line)
        if not m:
            continue
        method = m.group(1)
        loc = m.group(2)
        if any(method.startswith(p) for p in skip_prefixes):
            continue
        if any(method.startswith(p) for p in business_prefixes) or not method.startswith('java'):
            first_business = f'at {method}({loc})'
            break

    chain = []
    if first_exception:
        chain.append(f'{first_exception}: {first_message}'.rstrip(': '))
    for name, msg in caused:
        chain.append(f'Caused by: {name}' + (f': {msg}' if msg else ''))
    summary = '异常链：\n' + ('\n'.join(chain) if chain else '（未识别到异常类）')
    summary += '\n\n首个业务位置：\n' + (first_business or '（未找到业务包 at 行）')
    compact = summary + '\n\n—— 原始堆栈 ——\n' + (text or '').strip()
    return {
        'first_exception': first_exception,
        'first_message': first_message,
        'caused_by': caused,
        'first_business_at': first_business,
        'compact_text': compact,
        'summary': summary,
    }
