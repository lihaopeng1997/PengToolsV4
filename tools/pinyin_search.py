# -*- coding: utf-8 -*-
"""统一拼音/首字母搜索（完全离线可用）。

- 原文、全拼、首字母、中英数混合
- 优先本机已打包的 pypinyin（不访问网络）
- 无 pypinyin 时：用 GBK 编码区间推算首字母（离线、无外网、无额外下载）
- 索引仅含非敏感元数据，禁止写入报文/密钥/Cookie
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable

_PYPINYIN = False
Style = None  # type: ignore
lazy_pinyin = None  # type: ignore
try:
    # 仅本地 import；打包进 Private 后离线可用，绝不联网
    from pypinyin import Style as _Style, lazy_pinyin as _lazy_pinyin

    Style = _Style
    lazy_pinyin = _lazy_pinyin
    _PYPINYIN = True
except Exception:
    _PYPINYIN = False


# 手工补充表（业务高频 + 多音字偏好）
_INITIAL_MAP = {
    '车': 'c', '重': 'z', '长': 'c', '行': 'x', '区': 'q', '会': 'h', '还': 'h',
    '着': 'z', '得': 'd', '地': 'd', '的': 'd', '了': 'l', '吗': 'm', '呢': 'n',
    '啊': 'a', '哦': 'o', '嗯': 'e', '么': 'm', '哪': 'n', '那': 'n',
}

# GBK 双字节汉字 → 拼音首字母区间（离线算法，覆盖常用汉字）
# 数据为公知编码区间，无网络、无第三方服务
_GBK_INITIAL_RANGES = (
    (0xB0A1, 0xB0C4, 'a'), (0xB0C5, 0xB2C0, 'b'), (0xB2C1, 0xB4ED, 'c'),
    (0xB4EE, 0xB6E9, 'd'), (0xB6EA, 0xB7A1, 'e'), (0xB7A2, 0xB8C0, 'f'),
    (0xB8C1, 0xB9FD, 'g'), (0xB9FE, 0xBBF6, 'h'), (0xBBF7, 0xBFA5, 'j'),
    (0xBFA6, 0xC0AB, 'k'), (0xC0AC, 0xC2E7, 'l'), (0xC2E8, 0xC4C2, 'm'),
    (0xC4C3, 0xC5B5, 'n'), (0xC5B6, 0xC5BD, 'o'), (0xC5BE, 0xC6D9, 'p'),
    (0xC6DA, 0xC8BA, 'q'), (0xC8BB, 0xC8F5, 'r'), (0xC8F6, 0xCBF9, 's'),
    (0xCBFA, 0xCDD9, 't'), (0xCDDA, 0xCEF3, 'w'), (0xCEF4, 0xD1B8, 'x'),
    (0xD1B9, 0xD4D0, 'y'), (0xD4D1, 0xD7F9, 'z'),
)

_VOWEL_STRIP = re.compile(r'[^a-z0-9]+')
_SPACE_RE = re.compile(r'[\s_\-·./\\:]+')


def normalize_query(text: str) -> str:
    s = unicodedata.normalize('NFKD', str(text or ''))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold().strip()
    s = _SPACE_RE.sub(' ', s)
    return s


def _gbk_initial(ch: str) -> str:
    """离线：GBK 区间推算首字母。"""
    if ch in _INITIAL_MAP:
        return _INITIAL_MAP[ch]
    try:
        raw = ch.encode('gbk')
    except Exception:
        return ''
    if len(raw) != 2:
        return ''
    code = (raw[0] << 8) | raw[1]
    for start, end, letter in _GBK_INITIAL_RANGES:
        if start <= code <= end:
            return letter
    return ''


def _char_initial(ch: str) -> str:
    if not ch:
        return ''
    if ch.isascii() and ch.isalnum():
        return ch.lower()
    if '\u4e00' <= ch <= '\u9fff':
        return _gbk_initial(ch) or _INITIAL_MAP.get(ch, '')
    return _INITIAL_MAP.get(ch, '')


@lru_cache(maxsize=16384)
def pinyin_full(text: str) -> str:
    """全拼。有本地 pypinyin 用库；否则离线场景退化为「首字母串 + 原文 ascii」。"""
    s = str(text or '')
    if not s:
        return ''
    if _PYPINYIN and lazy_pinyin is not None:
        try:
            parts = lazy_pinyin(s, style=Style.NORMAL, errors='default')
            return ' '.join(str(p) for p in parts if p).casefold()
        except Exception:
            pass
    # 离线无全拼字典：用首字母空格分隔作为弱全拼，保证至少能搜字母
    initials = []
    ascii_buf = []
    for ch in s:
        if ch.isascii() and ch.isalnum():
            ascii_buf.append(ch.lower())
        else:
            if ascii_buf:
                initials.append(''.join(ascii_buf))
                ascii_buf = []
            ini = _char_initial(ch)
            if ini:
                initials.append(ini)
    if ascii_buf:
        initials.append(''.join(ascii_buf))
    return ' '.join(initials)


@lru_cache(maxsize=16384)
def pinyin_initials(text: str) -> str:
    """拼音首字母连续串，如 车险承保 → cxcb（离线 GBK 可算）。"""
    s = str(text or '')
    if not s:
        return ''
    if _PYPINYIN and lazy_pinyin is not None:
        try:
            parts = lazy_pinyin(s, style=Style.FIRST_LETTER, errors='default')
            return ''.join(str(p)[:1] for p in parts if p).casefold()
        except Exception:
            pass
    return ''.join(_char_initial(ch) for ch in s)


def build_search_blob(*parts: object) -> str:
    raw_bits = []
    full_bits = []
    init_bits = []
    for part in parts:
        if part is None:
            continue
        text = str(part).strip()
        if not text:
            continue
        raw_bits.append(text)
        full_bits.append(pinyin_full(text))
        init_bits.append(pinyin_initials(text))
    raw = '\n'.join(raw_bits)
    full = ' '.join(x for x in full_bits if x)
    initials = ''.join(init_bits)
    compact_full = _VOWEL_STRIP.sub('', full)
    # 多形态索引，便于 cx / cxcb / chexian / 车险 命中
    pieces = [
        raw,
        normalize_query(raw),
        full,
        compact_full,
        initials,
        ' '.join(init_bits),
    ]
    blob = '\n'.join(pieces)
    return normalize_query(blob) + '\n' + compact_full + '\n' + initials


def match_query(blob_or_text: str, query: str) -> bool:
    """多词 AND；支持原文、全拼、首字母、无空格。完全离线。"""
    q = normalize_query(query)
    if not q:
        return True
    src = blob_or_text or ''
    if any('\u4e00' <= c <= '\u9fff' for c in src) and '\n' not in src:
        blob = build_search_blob(src)
    else:
        # 已是 blob 或纯英文
        if any('\u4e00' <= c <= '\u9fff' for c in src):
            blob = build_search_blob(src)
        else:
            blob = normalize_query(src) + '\n' + _VOWEL_STRIP.sub('', normalize_query(src))

    compact_blob = _VOWEL_STRIP.sub('', blob)
    for term in q.split():
        if not term:
            continue
        compact_t = _VOWEL_STRIP.sub('', term)
        if term in blob or compact_t in compact_blob:
            continue
        # 查询含中文：对查询本身再生成拼音后匹配（离线）
        if any('\u4e00' <= c <= '\u9fff' for c in term):
            q_full = _VOWEL_STRIP.sub('', pinyin_full(term))
            q_init = pinyin_initials(term)
            if (q_full and q_full in compact_blob) or (q_init and q_init in compact_blob):
                continue
            # 原文子串
            if term in src:
                continue
        return False
    return True


def filter_by_query(items: Iterable, query: str, text_getter) -> list:
    q = normalize_query(query)
    if not q:
        return list(items)
    out = []
    for item in items:
        raw = text_getter(item)
        if isinstance(raw, (list, tuple)):
            blob = build_search_blob(*raw)
        else:
            blob = build_search_blob(raw)
        if match_query(blob, q):
            out.append(item)
    return out


def highlight_terms(text: str, query: str) -> str:
    q = normalize_query(query)
    if not q or not text:
        return text or ''
    result = text
    for term in q.split():
        if not term:
            continue
        # 仅高亮原文可见片段
        if any('\u4e00' <= c <= '\u9fff' for c in term) or term.isascii():
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            result = pattern.sub(lambda m: f'【{m.group(0)}】', result)
    return result


def clear_pinyin_cache() -> None:
    pinyin_full.cache_clear()
    pinyin_initials.cache_clear()


def backend_name() -> str:
    if _PYPINYIN:
        return 'pypinyin-local'
    return 'offline-gbk-initials'
