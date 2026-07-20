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
from ui import layout_metrics as _lm

# —— 视觉 token（与 layout_metrics / style.qss 对齐 V2.0 Astra）——
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
