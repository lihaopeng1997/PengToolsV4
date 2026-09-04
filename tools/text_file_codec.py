# -*- coding: utf-8 -*-
"""统一文本文件编解码器：按确定性优先级安全识别编码，杜绝 errors='replace' 产生 \\ufffd 乱码。

编码识别优先级：
1. UTF-8 BOM (\\xef\\xbb\\xbf) -> utf-8-sig
2. UTF-16 LE/BE BOM (\\xff\\xfe / \\xfe\\xff) -> utf-16
3. Strict UTF-8 + is_probably_text 门禁
4. Strict GB18030 + is_probably_text 门禁
5. 非文本 / 二进制安全阻断，不强行作为文本返回
"""

from __future__ import annotations

import unicodedata

MAX_TEXT_FILE_SIZE = 256 * 1024  # 256 KB 默认文本上限
MAX_SEARCH_TEXT_FILE_SIZE = 1024 * 1024  # 1 MB 搜索文本上限


def is_probably_text(text: str, original_bytes: bytes, encoding: str) -> bool:
    """严格文本可读性门禁：检查控制字符比率与 GB18030 稀疏未分配字符，防止随机二进制误识别为文本。"""
    if not text:
        return True
    # 严格禁止 NUL 字节
    if '\x00' in text:
        return False

    control_count = 0
    printable_count = 0
    for ch in text:
        if ch in ('\n', '\r', '\t'):
            printable_count += 1
            continue
        cat = unicodedata.category(ch)
        if cat.startswith('C'):  # Cc, Cf, Cs, Co, Cn
            control_count += 1
        else:
            printable_count += 1

    total = control_count + printable_count
    if total == 0:
        return False
    if (control_count / total) > 0.015:
        return False

    # 对于 GB18030: 检查未分配/私用字符比例（随机二进制常落入此类稀疏区域）
    if encoding.lower() in ('gb18030', 'gbk', 'gb2312'):
        private_or_unassigned = sum(1 for ch in text if unicodedata.category(ch) in ('Co', 'Cn'))
        if (private_or_unassigned / total) > 0.03:
            return False

    return True


def decode_text_bytes(
    raw: bytes | None,
    *,
    filename: str = '',
    max_size: int = MAX_TEXT_FILE_SIZE,
) -> dict:
    """按确定性顺序识别并解码文本字节流。
    
    返回结构：
    {
        "ok": bool,
        "text": str,
        "encoding": str,
        "binary": bool,
        "too_large": bool,
        "reason": str,  # "too_large" | "binary" | ""
        "error": str
    }
    """
    if raw is None:
        return {'ok': True, 'text': '', 'encoding': 'empty', 'binary': False, 'too_large': False, 'reason': '', 'error': ''}

    if not isinstance(raw, bytes):
        raw = str(raw).encode('utf-8', errors='ignore')

    file_label = f"「{filename}」" if filename else "文件"

    # 大小门禁检查 (max_size)
    if max_size > 0 and len(raw) > max_size:
        size_kb = max(1, max_size // 1024) if max_size >= 1024 else max_size
        unit = 'KB' if max_size >= 1024 else '字节'
        return {
            'ok': False,
            'text': '',
            'encoding': 'too_large',
            'binary': False,
            'too_large': True,
            'reason': 'too_large',
            'error': f"{file_label}大小超过限制（{size_kb} {unit}），无法作为文本处理。",
        }

    if len(raw) == 0:
        return {'ok': True, 'text': '', 'encoding': 'utf-8', 'binary': False, 'too_large': False, 'reason': '', 'error': ''}

    # 1. 检查 UTF-8 BOM
    if raw.startswith(b'\xef\xbb\xbf'):
        try:
            t = raw.decode('utf-8-sig', errors='strict')
            if is_probably_text(t, raw, 'utf-8-sig'):
                return {'ok': True, 'text': t, 'encoding': 'utf-8-sig', 'binary': False, 'too_large': False, 'reason': '', 'error': ''}
        except UnicodeDecodeError:
            pass

    # 2. 检查 UTF-16 BOM (LE / BE)
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        try:
            t = raw.decode('utf-16', errors='strict')
            if is_probably_text(t, raw, 'utf-16'):
                return {'ok': True, 'text': t, 'encoding': 'utf-16', 'binary': False, 'too_large': False, 'reason': '', 'error': ''}
        except (UnicodeDecodeError, ValueError):
            pass

    # 3. Strict UTF-8 + roundtrip check
    try:
        t = raw.decode('utf-8', errors='strict')
        if t.encode('utf-8', errors='strict') == raw and is_probably_text(t, raw, 'utf-8'):
            return {'ok': True, 'text': t, 'encoding': 'utf-8', 'binary': False, 'too_large': False, 'reason': '', 'error': ''}
    except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError):
        pass

    # 4. Strict GB18030 + roundtrip check + is_probably_text 门禁
    try:
        t = raw.decode('gb18030', errors='strict')
        if t.encode('gb18030', errors='strict') == raw and is_probably_text(t, raw, 'gb18030'):
            return {'ok': True, 'text': t, 'encoding': 'gb18030', 'binary': False, 'too_large': False, 'reason': '', 'error': ''}
    except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError):
        pass

    # 5. 二进制/不可安全解码回退
    return {
        'ok': False,
        'text': '',
        'encoding': 'binary',
        'binary': True,
        'too_large': False,
        'reason': 'binary',
        'error': f"{file_label}包含二进制内容或未知字符编码，无法安全作为文本处理。",
    }
