# -*- coding: utf-8 -*-
"""分区/列表 隐藏·显示 / 展开·收起 统一文案与样式。"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from ui.design_system import apply_button


# kind -> (zh_when_visible, zh_when_hidden, en_when_visible, en_when_hidden)
_TOGGLE_LABELS = {
    'list': ('隐藏', '显示', 'Hide', 'Show'),
    'session_list': ('隐藏列表', '显示列表', 'Hide list', 'Show list'),
    'log': ('日志 · 收起', '日志 · 展开', 'Log · Collapse', 'Log · Expand'),
    'section': ('收起', '展开', 'Collapse', 'Expand'),
}


def toggle_labels(kind: str = 'list', language: str = 'zh') -> tuple[str, str]:
    """返回 (visible_state_label, hidden_state_label)。

    visible_state_label：内容当前可见时按钮上的文案（点了会隐藏）。
    """
    pack = _TOGGLE_LABELS.get(kind) or _TOGGLE_LABELS['list']
    if language == 'zh':
        return pack[0], pack[1]
    return pack[2], pack[3]


def apply_visibility_toggle(
    button,
    *,
    content_visible: bool,
    language: str = 'zh',
    kind: str = 'list',
    tooltip: str | None = None,
) -> None:
    """统一 ghost 紧凑按钮 + 显隐文案。content_visible=True 表示面板当前显示中。"""
    apply_button(button, 'ghost', compact=True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    shown, hidden = toggle_labels(kind, language)
    button.setText(shown if content_visible else hidden)
    if tooltip is None:
        tooltip = (
            '隐藏或显示该区域' if language == 'zh' else 'Show or hide this area'
        )
    button.setToolTip(tooltip)
    button.setMinimumWidth(max(button.minimumWidth(), 72))


def apply_expand_toggle(
    button,
    *,
    expanded: bool,
    language: str = 'zh',
    kind: str = 'log',
    tooltip: str | None = None,
) -> None:
    """统一展开/收起类按钮（日志区、参数区等）。expanded=True 表示内容已展开。"""
    apply_button(button, 'ghost', compact=True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    collapse_label, expand_label = toggle_labels(kind, language)
    button.setText(collapse_label if expanded else expand_label)
    if tooltip is None:
        tooltip = (
            '展开或收起该区域' if language == 'zh' else 'Expand or collapse this area'
        )
    button.setToolTip(tooltip)
    button.setMinimumWidth(max(button.minimumWidth(), 88))
