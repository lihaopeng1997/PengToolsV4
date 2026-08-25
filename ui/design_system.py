# -*- coding: utf-8 -*-
"""PengTools 全局设计系统基础。"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QSizePolicy, QTableWidget, QTabWidget, QTreeWidget, QWidget,
)

from ui.field_metrics import size_compact_button
from ui.icons import apply_icon
from ui import layout_metrics as _lm

# 视觉 token（与 layout_metrics / style.qss 保持一致）
COLOR_BG_APP = _lm.APP_BG
COLOR_SURFACE = _lm.SURFACE
COLOR_BORDER = _lm.BORDER
COLOR_BORDER_STRONG = _lm.BORDER_STRONG
COLOR_TEXT = _lm.TEXT
COLOR_TEXT_MUTED = _lm.TEXT_MUTED
COLOR_PRIMARY = _lm.PRIMARY
COLOR_PRIMARY_SOFT = _lm.PRIMARY_SOFT
COLOR_DANGER = _lm.DANGER
COLOR_SUCCESS = _lm.SUCCESS
COLOR_SELECTION = _lm.PRIMARY_SOFT

RADIUS_CONTROL = _lm.RADIUS_CONTROL
RADIUS_CARD = _lm.RADIUS_CARD
RADIUS_BUTTON = _lm.RADIUS_BUTTON
CONTROL_HEIGHT = _lm.BTN_H

BUTTON_ROLES = {
    'primary': 'primary-btn',
    'secondary': 'btn-secondary',
    'danger': 'btn-danger',
    'ghost': 'btn-ghost',
    'fold': 'fold-action-btn',
    'nav': 'nav-btn',
    'delete': 'btn-danger',
    'card': 'card-action',
    'default': 'btn-secondary',
}


@dataclass(frozen=True)
class DensityMetrics:
    """单一信息密度下的基础控件尺寸（单位：px）。"""

    control_height: int
    row_height: int


DENSITY_METRICS = {
    'compact': DensityMetrics(control_height=32, row_height=32),
    'comfortable': DensityMetrics(control_height=36, row_height=40),
}


def density_metrics(name: str | None) -> DensityMetrics:
    """返回稳定的密度指标；未知值安全回退到紧凑模式。"""
    return DENSITY_METRICS.get(str(name or '').strip().lower(), DENSITY_METRICS['compact'])


def apply_button(
    button,
    role: str = 'secondary',
    *,
    compact: bool = False,
    icon: str | None = None,
    icon_size: int = 18,
) -> None:
    """为按钮打上设计系统角色，不改 clicked 信号与文案。"""
    object_name = BUTTON_ROLES.get(role, BUTTON_ROLES['secondary'])
    button.setObjectName(object_name)
    size_compact_button(button)
    button.setProperty('compactAction', True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon:
        icon_kwargs = {}
        if role == 'primary':
            try:
                from ui.theme_manager import ThemeManager
                on_primary = ThemeManager.instance().token('ON_PRIMARY') or '#FFFFFF'
            except Exception:
                on_primary = '#FFFFFF'
            icon_kwargs = {'normal': on_primary, 'active': on_primary}
        elif role in ('danger', 'delete'):
            try:
                from ui.theme_manager import ThemeManager
                danger = ThemeManager.instance().token('DANGER') or '#B42318'
            except Exception:
                danger = '#B42318'
            icon_kwargs = {'normal': danger, 'active': danger}
        elif role == 'fold':
            try:
                from ui.theme_manager import ThemeManager
                accent = ThemeManager.instance().token('PRIMARY_ACTIVE') or '#3D594A'
            except Exception:
                accent = '#3D594A'
            icon_kwargs = {'normal': accent, 'active': accent}
        apply_icon(button, icon, size=icon_size, **icon_kwargs)
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)
    button.update()


def apply_fold_button(button, kind: str = 'expand', *, text: str | None = None) -> None:
    """展开/折叠按钮：可见边框 + 主题色图标，避免只剩两个看不懂的字。"""
    if text is not None:
        button.setText(text)
    icon = 'expand' if kind == 'expand' else 'collapse'
    apply_button(button, 'fold', compact=True, icon=icon, icon_size=14)


def apply_tree(tree: QTreeWidget, *, alternating: bool = True) -> None:
    """统一树控件交互基线，不改列模型与业务数据。"""
    tree.setAlternatingRowColors(alternating)
    tree.setAnimated(True)
    tree.setUniformRowHeights(True)
    tree.setExpandsOnDoubleClick(True)
    tree.setRootIsDecorated(True)
    tree.setItemsExpandable(True)
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    tree.setTextElideMode(Qt.TextElideMode.ElideRight)
    apply_list_header(tree.header())


def apply_module_tabs(tabs: QTabWidget) -> None:
    """模块 Tab：按文案完整显示，不够宽时出滚动箭头，不截成省略号。"""
    if tabs is None:
        return
    tabs.setObjectName('module-tabs')
    tabs.setDocumentMode(False)
    tabs.setElideMode(Qt.TextElideMode.ElideNone)
    tabs.setUsesScrollButtons(True)
    bar = tabs.tabBar()
    if bar is not None:
        bar.setExpanding(False)
        bar.setElideMode(Qt.TextElideMode.ElideNone)


def apply_list_header(header) -> None:
    """列表/表格标题栏：统一矮栏，不改列宽策略。"""
    if header is None:
        return
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setFixedHeight(int(_lm.TABLE_HEADER_H))
    header.setMinimumSectionSize(48)


def apply_table(table: QTableWidget, *, alternating: bool = True) -> None:
    """统一表格交互基线，不改列定义与业务填充。"""
    table.setAlternatingRowColors(alternating)
    table.setShowGrid(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setMinimumHeight(320)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    vertical = table.verticalHeader()
    vertical.setVisible(False)
    vertical.setDefaultSectionSize(32)
    vertical.setMinimumSectionSize(32)
    vertical.setMaximumSectionSize(36)
    vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    apply_list_header(table.horizontalHeader())
    header = table.horizontalHeader()
    if header is not None:
        header.setMinimumSectionSize(52)


def finish_result_rows(table: QTableWidget, row_height: int = 32) -> None:
    """生成结果后锁行高，避免样式把除第一行外的行挤没。"""
    vertical = table.verticalHeader()
    vertical.setDefaultSectionSize(row_height)
    vertical.setMinimumSectionSize(row_height)
    vertical.setMaximumSectionSize(row_height)
    vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    for row in range(table.rowCount()):
        table.setRowHeight(row, row_height)
    table.scrollToTop()
    view = table.viewport()
    if view is not None:
        view.update()


def apply_surface(frame: QWidget, kind: str = 'card') -> None:
    """轻量表面角色：card | zone | muted。"""
    names = {
        'card': 'ds-card',
        'zone': 'ds-zone',
        'muted': 'ds-muted',
    }
    frame.setObjectName(names.get(kind, 'ds-card'))
    style = frame.style()
    if style is not None:
        style.unpolish(frame)
        style.polish(frame)
    frame.update()
