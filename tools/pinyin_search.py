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
    # 必须再 strip：否则 "..." 会变成 " "（真值非空），导致当作「有查询」却 0 个 token → 误显示全部
    return s.strip()


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
    # 首行必须是「可见原文」专用（供单字母/中文原文匹配），不要把拼音拼进首行。
    # 旧实现 normalize_query(整段) 会把换行压成空格，导致 e 命中 che、乱输仍像有结果。
    raw_line = normalize_query(raw)
    return '\n'.join([
        raw_line,
        full,
        compact_full,
        initials,
        ' '.join(init_bits),
    ])


def _has_cjk(text: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in (text or ''))


def _latin_token(text: str) -> str:
    """仅保留 a-z0-9，用于拼音/英文紧凑匹配。绝不把中文压成空串后当命中。"""
    return _VOWEL_STRIP.sub('', (text or '').casefold())


def match_query(blob_or_text: str, query: str) -> bool:
    """多词 AND；支持原文、全拼、首字母。完全离线。

    重要约束（防「乱输也显示全部」）：
    - 空查询 → True（展示全部）
    - 纯标点/无有效 token → False（无命中）
    - 禁止用空串 ``'' in blob``（Python 恒 True）判定命中
    - 单字母只匹配「可见原文」，不在拼音索引里找（避免 e 命中 che）
    - 中文靠原文子串或拼音全拼/首字母（长度≥2）
    """
    raw_query = str(query or '')
    q = normalize_query(query)
    if not q:
        # 仅空白 → 展示全部；纯标点（归一化后变空）→ 无命中
        return not bool(raw_query.strip())

    terms = [t for t in q.split() if t]
    if not terms:
        return not bool(raw_query.strip())

    src = blob_or_text or ''
    # 已是 build_search_blob 产物：首行=原文，后续=拼音索引
    looks_like_blob = ('\n' in src) and (src.count('\n') >= 2)
    if looks_like_blob:
        blob = src
        raw_visible = normalize_query(src.split('\n', 1)[0])
    else:
        blob = build_search_blob(src)
        raw_visible = normalize_query(src)

    # blob 行：0 原文 / 1 全拼 / 2 紧凑全拼 / 3 首字母串 / 4 首字母空格串
    lines = blob.split('\n')
    raw_line = lines[0] if lines else raw_visible
    full_line = _latin_token(lines[1]) if len(lines) > 1 else ''
    compact_line = _latin_token(lines[2]) if len(lines) > 2 else _latin_token(blob)
    init_line = (lines[3] if len(lines) > 3 else '').casefold()
    meaningful = 0

    for term in terms:
        matched = False
        latin = _latin_token(term)
        has_cjk = _has_cjk(term)

        if has_cjk:
            # 中文：先原文，再拼音（全拼走 compact/full，首字母只走 init 行，避免 sq 误伤 jiekousql）
            if term in raw_visible or term in raw_line:
                matched = True
            else:
                q_full = _latin_token(pinyin_full(term))
                q_init = (pinyin_initials(term) or '').casefold()
                if q_full and len(q_full) >= 2 and (q_full in compact_line or q_full in full_line):
                    matched = True
                elif q_init and len(q_init) >= 2 and q_init in init_line:
                    matched = True
        elif latin:
            if len(latin) >= 2:
                # 英文/数字/拼音：可见原文、全拼紧凑或首字母串
                if (
                    latin in raw_visible
                    or term in raw_visible
                    or latin in compact_line
                    or latin in full_line
                    or (len(latin) >= 2 and latin in init_line)
                ):
                    matched = True
            else:
                # 单字母：仅可见原文（编号 REQ 中的 r 等），禁止落在拼音 che/xian 上
                if latin in raw_visible or latin in raw_line:
                    matched = True
        else:
            # 纯标点 token：忽略
            continue

        if not matched:
            return False
        meaningful += 1

    # 全是标点（如 "!!!" / "..."）→ 无命中，列表应为空
    if meaningful == 0:
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


def find_term_spans(text: str, query: str) -> list[tuple[int, int]]:
    """返回原文中可高亮的 (start, end) 区间（end 不含）。"""
    q = normalize_query(query)
    source = text or ''
    if not q or not source:
        return []
    spans: list[tuple[int, int]] = []
    for term in q.split():
        if not term:
            continue
        # 拼音类查询在原文无对应字符时跳过（避免误高亮）
        if not any('\u4e00' <= c <= '\u9fff' for c in term) and not any(c.isascii() and c.isalnum() for c in term):
            continue
        try:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        except re.error:
            continue
        for match in pattern.finditer(source):
            spans.append((match.start(), match.end()))
    if not spans:
        return []
    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def match_snippet(text: str, query: str, radius: int = 28, max_len: int = 96) -> str:
    """截取首个命中附近片段，并用【】标出关键词，便于列表定位展示。"""
    source = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    source = re.sub(r'\s+', ' ', source).strip()
    if not source:
        return ''
    spans = find_term_spans(source, query)
    if not spans:
        # 无字面命中（可能是拼音命中元数据）：给开头摘要
        snippet = source[:max_len]
        return (snippet + '…') if len(source) > max_len else snippet
    start, end = spans[0]
    left = max(0, start - radius)
    right = min(len(source), end + radius)
    chunk = source[left:right]
    # 在片段内重新标亮
    marked = highlight_terms(chunk, query)
    prefix = '…' if left > 0 else ''
    suffix = '…' if right < len(source) else ''
    result = f'{prefix}{marked}{suffix}'
    if len(result) > max_len + 8:
        result = result[: max_len + 8] + '…'
    return result


def first_match_line(text: str, query: str) -> tuple[int, str]:
    """返回 (0-based 行号, 行文本)；无命中时 (-1, '')。"""
    source = str(text or '')
    if not source:
        return -1, ''
    lines = source.splitlines() or [source]
    q = normalize_query(query)
    if not q:
        return 0, lines[0] if lines else ''
    for index, line in enumerate(lines):
        if find_term_spans(line, query):
            return index, line
        # 拼音命中：整行 blob
        if match_query(build_search_blob(line), query):
            return index, line
    return -1, ''


def clear_pinyin_cache() -> None:
    pinyin_full.cache_clear()
    pinyin_initials.cache_clear()


def backend_name() -> str:
    if _PYPINYIN:
        return 'pypinyin-local'
    return 'offline-gbk-initials'
