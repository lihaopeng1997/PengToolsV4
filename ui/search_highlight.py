# -*- coding: utf-8 -*-
"""搜索命中高亮与定位（列表项背景 + 文本控件 ExtraSelections）。"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Union

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
)

from tools.pinyin_search import find_term_spans, normalize_query


TextEdit = Union[QPlainTextEdit, QTextEdit]


def search_colors() -> tuple[QColor, QColor, QColor]:
    """(match_bg, current_bg, mark_fg)"""
    try:
        from ui.theme_manager import ThemeManager
        pal = ThemeManager.instance().palette()
        return (
            QColor(pal.get('SEARCH_MATCH', '#FFF0A6')),
            QColor(pal.get('SEARCH_CURRENT', '#FFD86B')),
            QColor(pal.get('HIGHLIGHT_MARK', '#B24A24')),
        )
    except Exception:
        return QColor('#FFF0A6'), QColor('#FFD86B'), QColor('#B24A24')


def paint_tree_item(
    item: QTreeWidgetItem,
    *,
    matched: bool,
    current: bool = False,
    columns: Optional[Sequence[int]] = None,
) -> None:
    if item is None:
        return
    match_bg, current_bg, mark_fg = search_colors()
    cols = list(columns) if columns is not None else list(range(item.columnCount()))
    for col in cols:
        if matched:
            item.setBackground(col, QBrush(current_bg if current else match_bg))
            item.setForeground(col, QBrush(mark_fg))
            font = item.font(col)
            font.setBold(True)
            item.setFont(col, font)
        else:
            item.setBackground(col, QBrush())
            # 不强制清 foreground/font：调用方可能另有样式


def paint_list_item(
    item: QListWidgetItem,
    *,
    matched: bool,
    current: bool = False,
) -> None:
    if item is None:
        return
    match_bg, current_bg, mark_fg = search_colors()
    if matched:
        item.setBackground(QBrush(current_bg if current else match_bg))
        item.setForeground(QBrush(mark_fg))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    else:
        item.setBackground(QBrush())


def focus_tree_item(tree: QTreeWidget, item: QTreeWidgetItem) -> None:
    if tree is None or item is None:
        return
    parent = item.parent()
    while parent is not None:
        parent.setExpanded(True)
        parent = parent.parent()
    tree.setCurrentItem(item)
    tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)


def focus_list_item(list_widget: QListWidget, item: QListWidgetItem) -> None:
    if list_widget is None or item is None:
        return
    list_widget.setCurrentItem(item)
    list_widget.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)


def clear_text_highlights(edit: TextEdit) -> None:
    if edit is None:
        return
    if hasattr(edit, 'set_search_selections'):
        try:
            edit.set_search_selections([])
            return
        except Exception:
            pass
    try:
        edit.setExtraSelections([])
    except Exception:
        pass


def collect_text_spans(edit: TextEdit, query: str) -> list[tuple[int, int]]:
    if edit is None:
        return []
    text = edit.toPlainText() if hasattr(edit, 'toPlainText') else ''
    return find_term_spans(text, query)


def build_text_extra_selections(
    edit: TextEdit,
    spans: Sequence[tuple[int, int]],
    current_index: int = 0,
) -> list:
    """构造 ExtraSelection 列表（不写入编辑器）。"""
    if edit is None or not spans:
        return []
    match_bg, current_bg, mark_fg = search_colors()
    selections = []
    doc = edit.document()
    for index, (start, end) in enumerate(spans):
        cursor = QTextCursor(doc)
        cursor.setPosition(int(start))
        cursor.setPosition(int(end), QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(current_bg if index == current_index else match_bg)
        fmt.setForeground(mark_fg)
        font = QFont(edit.font())
        font.setBold(True)
        fmt.setFont(font)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        selections.append(selection)
    return selections


def _set_edit_extra_selections(edit: TextEdit, selections: list) -> None:
    """兼容 FoldablePlainTextEdit（保留行高亮合并入口）。"""
    if edit is None:
        return
    if hasattr(edit, 'set_search_selections'):
        edit.set_search_selections(selections)
        return
    try:
        edit.setExtraSelections(selections)
    except Exception:
        pass


def ensure_text_pos_unfolded(edit: TextEdit, pos: int) -> None:
    """若命中落在折叠块内，展开相关折叠。"""
    if edit is None or not hasattr(edit, 'document'):
        return
    try:
        block = edit.document().findBlock(int(pos))
        if not block.isValid():
            return
        line = block.blockNumber()
    except Exception:
        return
    if not hasattr(edit, 'fold_regions') or not hasattr(edit, 'collapsed_starts'):
        return
    try:
        regions = list(edit.fold_regions() or [])
        collapsed = set(edit.collapsed_starts() or set())
    except Exception:
        return
    # 从内到外展开覆盖该行的折叠
    starts = sorted(
        (s for s, e in regions if s in collapsed and s < line <= e),
        reverse=True,
    )
    for start in starts:
        try:
            edit.toggle_fold_at_line(start)
        except Exception:
            pass


def focus_text_span(edit: TextEdit, start: int, end: int) -> None:
    """跳转到文本区间并保证可见。"""
    if edit is None:
        return
    ensure_text_pos_unfolded(edit, start)
    doc = edit.document()
    caret = QTextCursor(doc)
    caret.setPosition(int(start))
    edit.setTextCursor(caret)
    edit.ensureCursorVisible()
    # 短暂选中当前命中，便于用户辨认
    sel = QTextCursor(doc)
    sel.setPosition(int(start))
    sel.setPosition(int(end), QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(sel)
    edit.ensureCursorVisible()
    # 再放回起点光标（高亮靠 ExtraSelection）
    caret = QTextCursor(doc)
    caret.setPosition(int(start))
    edit.setTextCursor(caret)
    edit.ensureCursorVisible()
    try:
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
    except Exception:
        edit.setFocus()


def apply_text_highlights(edit: TextEdit, query: str, *, select_first: bool = True) -> int:
    """在 QPlainTextEdit/QTextEdit 中高亮全部字面命中，并滚动到第一处。返回命中数。"""
    if edit is None:
        return 0
    clear_text_highlights(edit)
    if hasattr(edit, 'set_search_selections'):
        edit.set_search_selections([])
    spans = collect_text_spans(edit, query)
    if not spans:
        return 0
    selections = build_text_extra_selections(edit, spans, current_index=0)
    _set_edit_extra_selections(edit, selections)
    if select_first:
        focus_text_span(edit, spans[0][0], spans[0][1])
    return len(spans)


def apply_text_match_index(
    edit: TextEdit,
    spans: Sequence[tuple[int, int]],
    index: int,
) -> None:
    """按索引高亮并跳转到某一处命中。"""
    if edit is None or not spans:
        return
    idx = int(index) % len(spans)
    selections = build_text_extra_selections(edit, spans, current_index=idx)
    _set_edit_extra_selections(edit, selections)
    start, end = spans[idx]
    focus_text_span(edit, start, end)


def status_text(matched: int, total: Optional[int] = None, language: str = 'zh') -> str:
    if language != 'zh':
        if total is None:
            return f'{matched} match(es)'
        return f'{matched} / {total} match(es)'
    if total is None:
        return f'命中 {matched} 处'
    return f'命中 {matched} / {total}'
