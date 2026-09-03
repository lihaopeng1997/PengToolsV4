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
        "error": str
    }
    """
    if raw is None:
        return {'ok': True, 'text': '', 'encoding': 'empty', 'binary': False, 'error': ''}

    if not isinstance(raw, bytes):
        raw = str(raw).encode('utf-8', errors='ignore')

    if len(raw) == 0:
        return {'ok': True, 'text': '', 'encoding': 'utf-8', 'binary': False, 'error': ''}

    # 1. 检查 UTF-8 BOM
    if raw.startswith(b'\xef\xbb\xbf'):
        try:
            t = raw.decode('utf-8-sig')
            if is_probably_text(t, raw, 'utf-8-sig'):
                return {'ok': True, 'text': t, 'encoding': 'utf-8-sig', 'binary': False, 'error': ''}
        except UnicodeDecodeError:
            pass

    # 2. 检查 UTF-16 BOM (LE / BE)
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        try:
            t = raw.decode('utf-16')
            if is_probably_text(t, raw, 'utf-16'):
                return {'ok': True, 'text': t, 'encoding': 'utf-16', 'binary': False, 'error': ''}
        except (UnicodeDecodeError, ValueError):
            pass

    # 3. Strict UTF-8
    try:
        t = raw.decode('utf-8')
        if is_probably_text(t, raw, 'utf-8'):
            return {'ok': True, 'text': t, 'encoding': 'utf-8', 'binary': False, 'error': ''}
    except UnicodeDecodeError:
        pass

    # 4. Strict GB18030 (需通过文本可读性门禁)
    try:
        t = raw.decode('gb18030')
        if is_probably_text(t, raw, 'gb18030'):
            return {'ok': True, 'text': t, 'encoding': 'gb18030', 'binary': False, 'error': ''}
    except UnicodeDecodeError:
        pass

    # 5. 二进制/不可安全解码回退
    file_label = f"「{filename}」" if filename else "文件"
    return {
        'ok': False,
        'text': '',
        'encoding': 'binary',
        'binary': True,
        'error': f"{file_label}包含二进制内容或未知字符编码，无法安全作为文本处理。",
    }
