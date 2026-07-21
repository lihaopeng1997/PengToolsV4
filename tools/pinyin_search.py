# -*- coding: utf-8 -*-
"""统一拼音/首字母搜索：原文、全拼、首字母、中英数混合。

优先 pypinyin；未安装时使用内置常用汉字首字母表降级。
索引仅含非敏感元数据，禁止写入报文/密钥/Cookie。
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, Optional

_PYPINYIN = None
try:
    from pypinyin import Style, lazy_pinyin  # type: ignore

    _PYPINYIN = True
except Exception:
    _PYPINYIN = False
    Style = None  # type: ignore
    lazy_pinyin = None  # type: ignore


# 常用汉字 → 首字母（降级表，覆盖业务高频字；未知字跳过首字母）
_INITIAL_MAP = {
    '车': 'c', '险': 'x', '承': 'c', '保': 'b', '中': 'z', '心': 'x', '客': 'k', '户': 'h',
    '信': 'x', '息': 'x', '平': 'p', '台': 't', '数': 's', '据': 'j', '字': 'z', '典': 'd',
    '统': 't', '一': 'y', '监': 'j', '管': 'g', '接': 'j', '入': 'r', '共': 'g', '享': 'x',
    '升': 's', '级': 'j', '准': 'z', '备': 'b', '发': 'f', '版': 'b', '联': 'l', '动': 'd',
    '需': 'x', '求': 'q', '日': 'r', '报': 'b', '学': 'x', '习': 'x', '加': 'j', '密': 'm',
    '解': 'j', '排': 'p', '查': 'c', '格': 'g', '式': 's', '工': 'g', '具': 'j', '运': 'y',
    '维': 'w', '助': 'z', '手': 's', '设': 's', '置': 'z', '文': 'w', '件': 'j', '库': 'k',
    '系': 'x', '统': 't', '配': 'p', '置': 'z', '分': 'f', '类': 'l', '状': 'z', '态': 't',
    '待': 'd', '分': 'f', '析': 'x', '开': 'k', '发': 'f', '测': 'c', '试': 's', '完': 'w',
    '成': 'c', '上': 's', '线': 'x', '月': 'y', '份': 'f', '年': 'n', '日': 'r', '期': 'q',
    '通': 't', '知': 'z', '周': 'z', '边': 'b', '临': 'l', '时': 's', '脚': 'j', '本': 'b',
    '回': 'h', '滚': 'g', '验': 'y', '证': 'z', '提': 't', '交': 'j', '锁': 's', '定': 'd',
    '更': 'g', '新': 'x', '刷': 's', '新': 'x', '导': 'd', '出': 'c', '入': 'r', '删': 's',
    '除': 'c', '复': 'f', '制': 'z', '路': 'l', '径': 'j', '名': 'm', '称': 'c', '类': 'l',
    '型': 'x', '大': 'd', '小': 'x', '修': 'x', '改': 'g', '时': 's', '间': 'j', '折': 'z',
    '叠': 'd', '展': 'z', '开': 'k', '搜': 's', '索': 's', '过': 'g', '滤': 'l', '全': 'q',
    '部': 'b', '选': 'x', '批': 'p', '量': 'l', '管': 'g', '理': 'l', '档': 'd', '案': 'a',
    '附': 'f', '件': 'j', '版': 'b', '本': 'b', '仓': 'c', '库': 'k', '分': 'f', '支': 'z',
    '任': 'r', '务': 'w', '编': 'b', '码': 'm', '内': 'n', '容': 'r', '公': 'g', '告': 'g',
    '车': 'c', '辆': 'l', '证': 'z', '件': 'j', '个': 'g', '人': 'r', '单': 'd', '位': 'w',
    '理': 'l', '赔': 'p', '销': 'x', '售': 's', '核': 'h', '保': 'b', '批': 'p', '单': 'd',
    '见': 'j', '费': 'f', '批': 'p', '改': 'g', '续': 'x', '保': 'b', '退': 't', '保': 'b',
    '问': 'w', '题': 't', '故': 'g', '障': 'z', '异': 'y', '常': 'c', '优': 'y', '化': 'h',
    '重': 'z', '构': 'g', '迁': 'q', '移': 'y', '同': 't', '步': 'b', '对': 'd', '接': 'j',
    '接': 'j', '口': 'k', '文': 'w', '档': 'd', '说': 's', '明': 'm', '清': 'q', '单': 'd',
    '清': 'q', '单': 'd', '列': 'l', '表': 'b', '详': 'x', '情': 'q', '摘': 'z', '要': 'y',
    '标': 'b', '题': 't', '描': 'm', '述': 's', '备': 'b', '注': 'z', '说': 's', '明': 'm',
    '用': 'y', '户': 'h', '环': 'h', '境': 'j', '生': 's', '产': 'c', '集': 'j', '成': 'c',
    '模': 'm', '拟': 'n', '测': 'c', '试': 's', '联': 'l', '调': 'd', '验': 'y', '收': 's',
    '日': 'r', '志': 'z', '报': 'b', '告': 'g', '汇': 'h', '总': 'z', '周': 'z', '报': 'b',
    '月': 'y', '报': 'b', '年': 'n', '报': 'b', '待': 'd', '办': 'b', '已': 'y', '办': 'b',
    '关': 'g', '闭': 'b', '重': 'z', '开': 'k', '挂': 'g', '起': 'q', '推': 't', '进': 'j',
    '阻': 'z', '塞': 's', '风': 'f', '险': 'x', '优': 'y', '先': 'x', '高': 'g', '中': 'z',
    '低': 'd', '紧': 'j', '急': 'j', '普': 'p', '通': 't', '重': 'z', '要': 'y',
    '甲': 'j', '乙': 'y', '丙': 'b', '丁': 'd', '东': 'd', '南': 'n', '西': 'x', '北': 'b',
    '京': 'j', '津': 'j', '沪': 'h', '渝': 'y', '冀': 'j', '晋': 'j', '蒙': 'm', '辽': 'l',
    '吉': 'j', '黑': 'h', '苏': 's', '浙': 'z', '皖': 'w', '闽': 'm', '赣': 'g', '鲁': 'l',
    '豫': 'y', '鄂': 'e', '湘': 'x', '粤': 'y', '桂': 'g', '琼': 'q', '川': 'c', '贵': 'g',
    '云': 'y', '藏': 'z', '陕': 's', '甘': 'g', '青': 'q', '宁': 'n', '新': 'x', '港': 'g',
    '澳': 'a', '台': 't',
    '的': 'd', '了': 'l', '和': 'h', '与': 'y', '或': 'h', '及': 'j', '等': 'd', '为': 'w',
    '是': 's', '在': 'z', '有': 'y', '无': 'w', '对': 'd', '从': 'c', '到': 'd', '把': 'b',
    '被': 'b', '将': 'j', '已': 'y', '未': 'w', '可': 'k', '能': 'n', '需': 'x', '要': 'y',
    '请': 'q', '按': 'a', '照': 'z', '根': 'g', '据': 'j', '通': 't', '过': 'g', '使': 's',
    '用': 'y', '执': 'z', '行': 'x', '运': 'y', '行': 'x', '启': 'q', '动': 'd', '停': 't',
    '止': 'z', '关': 'g', '闭': 'b', '保': 'b', '存': 'c', '读': 'd', '取': 'q', '写': 'x',
    '入': 'r', '查': 'c', '询': 'x', '统': 't', '计': 'j', '分': 'f', '析': 'x', '处': 'c',
    '理': 'l', '转': 'z', '换': 'h', '格': 'g', '式': 's', '化': 'h', '校': 'x', '验': 'y',
    '检': 'j', '查': 'c', '修': 'x', '复': 'f', '恢': 'h', '复': 'f', '备': 'b', '份': 'f',
    '导': 'd', '入': 'r', '导': 'd', '出': 'c', '下': 'x', '载': 'z', '上': 's', '传': 'c',
    '打': 'd', '开': 'k', '创': 'c', '建': 'j', '新': 'x', '增': 'z', '编': 'b', '辑': 'j',
    '修': 'x', '改': 'g', '删': 's', '除': 'c', '复': 'f', '制': 'z', '粘': 'z', '贴': 't',
    '剪': 'j', '切': 'q', '撤': 'c', '销': 'x', '重': 'z', '做': 'z', '确': 'q', '认': 'r',
    '取': 'q', '消': 'x', '返': 'f', '回': 'h', '下': 'x', '一': 'y', '步': 'b', '上': 's',
    '一': 'y', '步': 'b', '完': 'w', '成': 'c', '提': 't', '交': 'j', '应': 'y', '用': 'y',
    '批': 'p', '次': 'c', '序': 'x', '号': 'h', '编': 'b', '号': 'h', '标': 'b', '识': 's',
    '名': 'm', '称': 'c', '标': 'b', '题': 't', '描': 'm', '述': 's', '备': 'b', '注': 'z',
    '说': 's', '明': 'm', '帮': 'b', '助': 'z', '关': 'g', '于': 'y', '版': 'b', '本': 'b',
    '权': 'q', '限': 'x', '角': 'j', '色': 's', '账': 'z', '号': 'h', '密': 'm', '码': 'm',
    # 注意：不把真实密码写入任何索引
    '邮': 'y', '箱': 'x', '电': 'd', '话': 'h', '地': 'd', '址': 'z', '省': 's', '市': 's',
    '区': 'q', '县': 'x', '街': 'j', '道': 'd', '号': 'h', '楼': 'l', '室': 's',
    '金': 'j', '额': 'e', '费': 'f', '率': 'l', '保': 'b', '费': 'f', '赔': 'p', '付': 'f',
    '理': 'l', '算': 's', '核': 'h', '赔': 'p', '结': 'j', '案': 'a', '立': 'l', '案': 'a',
    '报': 'b', '案': 'a', '查': 'c', '勘': 'k', '定': 'd', '损': 's', '复': 'f', '核': 'h',
    '签': 'q', '发': 'f', '批': 'p', '改': 'g', '退': 't', '保': 'b', '续': 'x', '保': 'b',
    '批': 'p', '单': 'd', '保': 'b', '单': 'd', '意': 'y', '外': 'w', '意': 'y', '健': 'j',
    '财': 'c', '产': 'c', '责': 'z', '任': 'r', '船': 'c', '货': 'h', '工': 'g', '程': 'c',
    '农': 'n', '业': 'y', '信': 'x', '用': 'y', '证': 'z', '券': 'q', '投': 't', '资': 'z',
    '银': 'y', '行': 'h', '支': 'z', '付': 'f', '结': 'j', '算': 's', '账': 'z', '务': 'w',
    '会': 'h', '计': 'j', '审': 's', '计': 'j', '合': 'h', '规': 'g', '风': 'f', '控': 'k',
    '安': 'a', '全': 'q', '防': 'f', '火': 'h', '墙': 'q', '代': 'd', '理': 'l', '证': 'z',
    '书': 's', '令': 'l', '牌': 'p', '会': 'h', '话': 'h', '超': 'c', '时': 's', '重': 'z',
    '试': 's', '失': 's', '败': 'b', '成': 'c', '功': 'g', '警': 'j', '告': 'g', '错': 'c',
    '误': 'w', '异': 'y', '常': 'c', '超': 'c', '时': 's', '断': 'd', '开': 'k', '连': 'l',
    '接': 'j', '监': 'j', '听': 't', '抓': 'z', '包': 'b', '代': 'd', '理': 'l', '端': 'd',
    '口': 'k', '主': 'z', '机': 'j', '域': 'y', '名': 'm', '地': 'd', '址': 'z', '路': 'l',
    '径': 'j', '方': 'f', '法': 'f', '状': 'z', '态': 't', '码': 'm', '响': 'x', '应': 'y',
    '请': 'q', '求': 'q', '头': 't', '体': 't', '消': 'x', '息': 'x', '报': 'b', '文': 'w',
    '明': 'm', '文': 'w', '密': 'm', '文': 'w', '钥': 'y', '匙': 's', '算': 's', '法': 'f',
    '模': 'm', '式': 's', '填': 't', '充': 'c', '编': 'b', '码': 'm', '解': 'j', '码': 'm',
}

# 补全拼音全拼降级：仅首字母场景必须可用；全拼在无 pypinyin 时用空串（原文仍可搜）
_VOWEL_STRIP = re.compile(r'[^a-z0-9]+')


def normalize_query(text: str) -> str:
    """大小写、重音、空白归一。"""
    s = unicodedata.normalize('NFKD', str(text or ''))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold().strip()
    s = re.sub(r'[\s_\-]+', ' ', s)
    return s


def _char_initial(ch: str) -> str:
    if not ch:
        return ''
    if ('a' <= ch <= 'z') or ('0' <= ch <= '9'):
        return ch
    if 'A' <= ch <= 'Z':
        return ch.lower()
    return _INITIAL_MAP.get(ch, '')


@lru_cache(maxsize=8192)
def pinyin_full(text: str) -> str:
    """全拼（小写、空格分隔）。"""
    s = str(text or '')
    if not s:
        return ''
    if _PYPINYIN and lazy_pinyin is not None:
        try:
            parts = lazy_pinyin(s, style=Style.NORMAL, errors='ignore')
            return ' '.join(p for p in parts if p).casefold()
        except Exception:
            pass
    # 降级：拉丁字符保留，汉字无可靠全拼时留空段
    out = []
    buf = []
    for ch in s:
        if ch.isascii() and ch.isalnum():
            buf.append(ch.lower())
        else:
            if buf:
                out.append(''.join(buf))
                buf = []
            # 无全拼库时跳过汉字全拼
    if buf:
        out.append(''.join(buf))
    return ' '.join(out)


@lru_cache(maxsize=8192)
def pinyin_initials(text: str) -> str:
    """拼音首字母连续串，如 车险承保 → cxcb。"""
    s = str(text or '')
    if not s:
        return ''
    if _PYPINYIN and lazy_pinyin is not None:
        try:
            parts = lazy_pinyin(s, style=Style.FIRST_LETTER, errors='ignore')
            return ''.join(p for p in parts if p).casefold()
        except Exception:
            pass
    return ''.join(_char_initial(ch) for ch in s)


def build_search_blob(*parts: object) -> str:
    """构建可搜索大文本：原文 + 全拼 + 首字母（不含敏感字段调用方责任）。"""
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
    initials = ' '.join(x for x in init_bits if x)
    # 无空格首字母串便于 cxcb 命中
    compact_init = ''.join(init_bits)
    compact_full = _VOWEL_STRIP.sub('', full)
    blob = '\n'.join([raw, full, initials, compact_init, compact_full])
    return normalize_query(blob)


def match_query(blob_or_text: str, query: str) -> bool:
    """多词 AND；每个词可命中原文/全拼/首字母。"""
    q = normalize_query(query)
    if not q:
        return True
    blob = normalize_query(blob_or_text) if blob_or_text else ''
    # 若调用方传入原文，补充拼音
    if blob and ' ' not in blob[:40] and any('\u4e00' <= c <= '\u9fff' for c in blob_or_text or ''):
        blob = build_search_blob(blob_or_text)
    terms = [t for t in q.split() if t]
    if not terms:
        return True
    return all(term in blob for term in terms)


def filter_by_query(items: Iterable, query: str, text_getter) -> list:
    """通用过滤：text_getter(item) -> str | list[str]。"""
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
    """返回带 【】 标记的高亮文本（纯文本场景）。"""
    q = normalize_query(query)
    if not q or not text:
        return text or ''
    result = text
    for term in q.split():
        if not term:
            continue
        # 仅对原文可见片段做简单包裹
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        result = pattern.sub(lambda m: f'【{m.group(0)}】', result)
    return result


def clear_pinyin_cache() -> None:
    pinyin_full.cache_clear()
    pinyin_initials.cache_clear()


def backend_name() -> str:
    return 'pypinyin' if _PYPINYIN else 'builtin_initials'
