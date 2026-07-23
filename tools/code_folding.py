# -*- coding: utf-8 -*-
"""格式化文本折叠区域计算（HiJson 风格：按缩进 + 括号/标签闭合）。

纯逻辑，无 Qt 依赖，便于单测。
行号均为 0-based；区域为闭区间 [start, end]。
"""

from __future__ import annotations

from typing import Iterable


def leading_indent(line: str, tab_size: int = 2) -> int | None:
    """非空行的缩进列数；空行返回 None。"""
    if not line or not line.strip():
        return None
    n = 0
    for ch in line:
        if ch == ' ':
            n += 1
        elif ch == '\t':
            n += tab_size
        else:
            break
    return n


def _is_closing_line(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped[0] in '}]':
        return True
    if stripped.startswith('</'):
        return True
    if stripped in ('/>', '},', '],', '};', '];'):
        return True
    # XML 结束标签后可能还有注释
    if stripped.startswith('</') or stripped.startswith('/>'):
        return True
    return False


def _is_openish_line(stripped: str) -> bool:
    """可能开启子块：以 { [ 结尾，或 XML 开标签未自闭合。"""
    if not stripped:
        return False
    s = stripped.rstrip(',')
    if s.endswith('{') or s.endswith('['):
        return True
    if s.endswith('{') or s.endswith('['):
        return True
    # XML：<tag ...> 且非 </ 非 />
    if s.startswith('<') and not s.startswith('</') and not s.startswith('<?') and not s.startswith('<!'):
        if s.endswith('/>'):
            return False
        if '>' in s and not s.endswith('/>'):
            # 开标签或带内容的单行都可能；多行子块靠缩进判定
            return True
    return False


def compute_indent_fold_regions(
    text: str,
    *,
    tab_size: int = 2,
    min_span: int = 1,
) -> list[tuple[int, int]]:
    """根据缩进层级计算可折叠区间。

    规则（贴近 pretty JSON / pretty XML）：
    - 某行后存在更大缩进的内容，则从该行折叠到「回到本级缩进的闭合行」或最后一个子行。
    - min_span：至少折叠 min_span 行内容（end - start >= min_span）才生成区域。
    """
    if not text:
        return []
    lines = text.splitlines()
    if len(lines) < 2:
        return []

    indents: list[int | None] = [leading_indent(ln, tab_size) for ln in lines]
    # 空行继承上下文缩进（向后看下一个非空）
    filled: list[int] = []
    last = 0
    for ind in indents:
        if ind is None:
            filled.append(last)
        else:
            last = ind
            filled.append(ind)

    regions: list[tuple[int, int]] = []
    n = len(lines)
    for i in range(n):
        if indents[i] is None:
            continue
        base = filled[i]
        stripped = lines[i].strip()
        # 必须有真正更深的子行
        j = i + 1
        has_child = False
        while j < n:
            if indents[j] is None:
                j += 1
                continue
            if filled[j] > base:
                has_child = True
                break
            break
        if not has_child:
            # 同行开闭如 { } 不折叠；或仅括号同级
            continue
        # 扫描到缩进回到 base 或更小
        k = i + 1
        end = i
        while k < n:
            if indents[k] is None:
                end = k
                k += 1
                continue
            if filled[k] > base:
                end = k
                k += 1
                continue
            # 同级：若是闭合行则纳入折叠范围
            if filled[k] == base and _is_closing_line(lines[k].strip()):
                end = k
            break
        if end - i >= min_span:
            regions.append((i, end))

    # 去重：同一 start 只保留跨度最大的
    by_start: dict[int, int] = {}
    for start, end in regions:
        by_start[start] = max(by_start.get(start, -1), end)
    result = sorted((s, e) for s, e in by_start.items() if e > s)
    return result


def compute_bracket_fold_regions(text: str) -> list[tuple[int, int]]:
    """按括号匹配补充折叠（字符串感知），适合 JSON。"""
    if not text:
        return []
    lines = text.splitlines()
    # 扫描全文，记录括号位置 (line, col, ch)
    stack: list[tuple[str, int]] = []
    regions: list[tuple[int, int]] = []
    in_string = False
    escape = False
    line_no = 0
    col = 0
    i = 0
    raw = text
    # 统一用 \n 分行，兼容 \r\n
    while i < len(raw):
        ch = raw[i]
        if ch == '\r':
            i += 1
            continue
        if ch == '\n':
            line_no += 1
            col = 0
            i += 1
            escape = False
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            col += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            col += 1
            continue
        if ch in '{[':
            stack.append((ch, line_no))
        elif ch in '}]':
            open_ch = '{' if ch == '}' else '['
            # 找匹配
            while stack:
                och, start_line = stack.pop()
                if och == open_ch:
                    if start_line < line_no:
                        regions.append((start_line, line_no))
                    break
        i += 1
        col += 1

    by_start: dict[int, int] = {}
    for start, end in regions:
        by_start[start] = max(by_start.get(start, -1), end)
    return sorted((s, e) for s, e in by_start.items() if e > s)


def merge_fold_regions(*groups: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并多组区域：同一 start 取最大 end。"""
    by_start: dict[int, int] = {}
    for group in groups:
        for start, end in group:
            if end <= start:
                continue
            by_start[start] = max(by_start.get(start, -1), end)
    return sorted(by_start.items(), key=lambda x: x[0])


def compute_fold_regions(text: str, *, mode: str = 'auto', tab_size: int = 2) -> list[tuple[int, int]]:
    """计算折叠区域。

    mode:
      - auto: 缩进 + 括号合并（JSON/XML 通用）
      - indent: 仅缩进
      - bracket: 仅括号
    """
    mode = (mode or 'auto').lower()
    if mode == 'indent':
        return compute_indent_fold_regions(text, tab_size=tab_size)
    if mode == 'bracket':
        return compute_bracket_fold_regions(text)
    return merge_fold_regions(
        compute_indent_fold_regions(text, tab_size=tab_size),
        compute_bracket_fold_regions(text),
    )


def lines_hidden_by_collapsed(
    regions: list[tuple[int, int]],
    collapsed_starts: set[int],
) -> set[int]:
    """给定折叠起点集合，返回应隐藏的行号（不含起点行本身）。"""
    hidden: set[int] = set()
    region_map = {s: e for s, e in regions}
    for start in collapsed_starts:
        end = region_map.get(start)
        if end is None:
            continue
        for line in range(start + 1, end + 1):
            hidden.add(line)
    return hidden
