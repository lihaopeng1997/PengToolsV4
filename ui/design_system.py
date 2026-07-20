# -*- coding: utf-8 -*-
"""PengTools 设计系统基础（UI 重构 r2 · surfaces + buttons）。

目标：统一按钮角色、控件尺寸语义与表/树默认外观入口。
弹窗 / Loading / XML 工作区样式在各自模块 + style.qss 落地。

按钮角色（objectName）：
  primary   → primary-btn   主操作
  secondary → btn-secondary 次操作（与默认按钮视觉对齐，可显式标注）
  danger    → btn-danger    破坏性（兼容 ops-delete-custom）
  ghost     → btn-ghost     低强调
  nav       → nav-btn       侧栏导航（MainWindow 专用）

兼容旧名：primary-btn / card-action / ops-delete-custom / compactAction。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTreeWidget, QWidget

from ui.field_metrics import FIELD_H, size_compact_button, size_field_height
from ui.icons import apply_icon

# —— 视觉 token（Python 侧常量；QSS 以同色值落地）——
COLOR_BG_APP = '#E8ECF5'
COLOR_SURFACE = '#FFFFFF'
COLOR_BORDER = '#D8DEEA'
COLOR_BORDER_STRONG = '#C5CEDF'
COLOR_TEXT = '#1A2438'
COLOR_TEXT_MUTED = '#5F6E88'
COLOR_PRIMARY = '#4A61F0'
COLOR_PRIMARY_SOFT = '#5B73FF'
COLOR_DANGER = '#B33B48'
COLOR_SUCCESS = '#1F9D5A'
COLOR_SELECTION = '#E0E8FF'

RADIUS_CONTROL = 9
RADIUS_CARD = 12
RADIUS_BUTTON = 10
CONTROL_HEIGHT = FIELD_H

BUTTON_ROLES = {
    'primary': 'primary-btn',
    'secondary': 'btn-secondary',
    'danger': 'btn-danger',
    'ghost': 'btn-ghost',
    'nav': 'nav-btn',
    # 兼容别名
    'delete': 'btn-danger',
    'card': 'card-action',
    'default': 'btn-secondary',
}


def apply_button(
    button,
    role: str = 'secondary',
    *,
    compact: bool = False,
    icon: str | None = None,
    icon_size: int = 18,
) -> None:
    """为按钮打上设计系统角色，不改 clicked 信号与文案。

    icon：icons 角色名（如 'delete' / 'copy'），仅本地 SVG。
    """
    object_name = BUTTON_ROLES.get(role, BUTTON_ROLES['secondary'])
    # primary / card-action 历史调用可直接传 role='primary'
    if role == 'primary':
        object_name = 'primary-btn'
    button.setObjectName(object_name)
    if compact:
        size_compact_button(button)
        button.setProperty('compactAction', True)
    else:
        size_field_height(button, CONTROL_HEIGHT)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon:
        apply_icon(button, icon, size=icon_size)
    # 触发 QSS 对动态 objectName / property 的刷新
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)
    button.update()


def apply_tree(tree: QTreeWidget, *, alternating: bool = True) -> None:
    """统一树控件交互基线（不改列模型与业务数据）。"""
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
    header = tree.header()
    if header is not None:
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def apply_table(table: QTableWidget, *, alternating: bool = True) -> None:
    """统一表格交互基线（不改列定义与业务填充）。"""
    table.setAlternatingRowColors(alternating)
    table.setShowGrid(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    if header is not None:
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(52)


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
