# -*- coding: utf-8 -*-
"""全应用统一的表单字段尺寸，保证下拉/录入/日期视觉整齐舒适。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSizePolicy, QWidget

# 统一控件高度（含内边距后的视觉高度）
FIELD_H = 34

# 下拉框：短 / 中 / 长
COMBO_SM = (120, 140)   # 类型、状态、环境、性别等短选项
COMBO_MD = (180, 220)   # 系统名、分类、筛选
COMBO_LG = (240, 320)   # 系统配置主下拉

# 日期（yyyy-MM-dd 统一 150–160，避免各页长短不一）
DATE_W = (150, 160)
DATE_MONTH_W = (128, 150)  # yyyy-MM

# 录入框
LINE_STD_MIN = 160         # 普通文本
LINE_PATH_MIN = 200        # 路径 / URL（布局里通常 stretch）
LINE_NUM_W = 72            # 数量等短数字
LINE_SEARCH_MIN = 180      # 搜索框下限

# 标签与胶囊
CAPTION_W = (72, 92)
STATUS_PILL_MAX = 200
SYSTEM_CHIP_MAX = 220
BTN_COMPACT_MIN_W = 72


def _apply_width(widget: QWidget, lo: int, hi: int | None = None) -> None:
    widget.setMinimumWidth(lo)
    if hi is not None:
        widget.setMaximumWidth(hi)
    else:
        widget.setMaximumWidth(16777215)


def size_field_height(widget: QWidget, height: int = FIELD_H) -> None:
    widget.setFixedHeight(height)


def size_combo(widget, size: str = 'md') -> None:
    """统一下拉框宽度与高度。size: sm | md | lg"""
    mapping = {'sm': COMBO_SM, 'md': COMBO_MD, 'lg': COMBO_LG}
    lo, hi = mapping.get(size, COMBO_MD)
    _apply_width(widget, lo, hi)
    size_field_height(widget)
    widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)


def size_date(widget, month: bool = False) -> None:
    """统一日期控件；month=True 用于 yyyy-MM。"""
    lo, hi = DATE_MONTH_W if month else DATE_W
    _apply_width(widget, lo, hi)
    size_field_height(widget)
    if hasattr(widget, 'setAlignment'):
        widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if hasattr(widget, 'setCalendarPopup'):
        widget.setCalendarPopup(True)
    widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def size_line(widget, role: str = 'std') -> None:
    """统一录入框。role: std | path | search | num"""
    size_field_height(widget)
    if role == 'num':
        widget.setFixedWidth(LINE_NUM_W)
        if hasattr(widget, 'setAlignment'):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return
    if role == 'path':
        widget.setMinimumWidth(LINE_PATH_MIN)
        widget.setMaximumWidth(16777215)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return
    if role == 'search':
        widget.setMinimumWidth(LINE_SEARCH_MIN)
        widget.setMaximumWidth(16777215)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return
    # std
    widget.setMinimumWidth(LINE_STD_MIN)
    widget.setMaximumWidth(16777215)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def size_caption(label) -> None:
    """表单左侧短标题宽度统一。"""
    lo, hi = CAPTION_W
    _apply_width(label, lo, hi)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)


def size_status_pill(label, max_width: int = STATUS_PILL_MAX) -> None:
    label.setMaximumWidth(max_width)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


def size_system_chip(label, max_width: int = SYSTEM_CHIP_MAX) -> None:
    label.setMaximumWidth(max_width)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


def size_compact_button(button) -> None:
    button.setProperty('compactAction', True)
    button.setMinimumWidth(BTN_COMPACT_MIN_W)
    size_field_height(button)
