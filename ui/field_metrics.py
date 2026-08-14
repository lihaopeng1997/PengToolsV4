# -*- coding: utf-8 -*-
"""全应用统一的表单字段尺寸，保证下拉/录入/日期视觉整齐舒适。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QWidget

# 统一控件高度：与「检出代码」等紧凑按钮对齐
FIELD_H = 28

# 下拉框：按最长选项收窄，不再用大号保底宽度把短项撑肿
COMBO_SM = (56, 220)
COMBO_MD = (72, 280)
COMBO_LG = (96, 360)

# 日期（yyyy-MM-dd 统一 150–160，避免各页长短不一）
DATE_W = (150, 160)
DATE_MONTH_W = (128, 150)  # yyyy-MM

# 录入框
LINE_STD_MIN = 160         # 普通文本
LINE_PATH_MIN = 200        # 路径 / URL（布局里通常 stretch）
LINE_NUM_W = 56            # 数量等短数字（不含步进按钮）
LINE_SEARCH_MIN = 180      # 搜索框下限

# 标签与胶囊
CAPTION_W = (72, 92)
STATUS_PILL_MAX = 200
SYSTEM_CHIP_MAX = 220
BTN_COMPACT_MIN_W = 72
BTN_COMPACT_H = 28


def _apply_width(widget: QWidget, lo: int, hi: int | None = None) -> None:
    widget.setMinimumWidth(lo)
    if hi is not None:
        widget.setMaximumWidth(hi)
    else:
        widget.setMaximumWidth(16777215)


def size_field_height(widget: QWidget, height: int = FIELD_H) -> None:
    widget.setFixedHeight(height)


def size_combo(widget, size: str = 'md') -> None:
    """下拉框：高度与紧凑按钮一致，宽度跟最长选项走，不人为拉长。"""
    from PyQt6.QtWidgets import QComboBox
    mapping = {'sm': COMBO_SM, 'md': COMBO_MD, 'lg': COMBO_LG}
    lo, hi = mapping.get(size, COMBO_MD)
    size_field_height(widget)
    if isinstance(widget, QComboBox):
        widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        widget.setMinimumContentsLength(0)
    widget.setMinimumWidth(lo)
    widget.setMaximumWidth(hi)
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


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
    """设置行内操作的紧凑规格，避免工具条挤压列表展示空间。"""
    button.setProperty('compactAction', True)
    button.setMinimumWidth(BTN_COMPACT_MIN_W)
    size_field_height(button, BTN_COMPACT_H)
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


def apply_button_role(button, role: str = 'secondary', *, compact: bool = False) -> None:
    """按钮角色入口（转发 design_system，避免业务面板直接耦合）。"""
    from ui.design_system import apply_button
    apply_button(button, role, compact=compact)


class CompactStepper(QWidget):
    """主题一致的数量步进：− 数字 +，数字单独一格不被箭头挡住。"""

    valueChanged = pyqtSignal(int)

    def __init__(self, minimum=0, maximum=200, value=0, parent=None):
        super().__init__(parent)
        self._min = int(minimum)
        self._max = int(maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.minus_btn = QPushButton('−')
        self.plus_btn = QPushButton('+')
        from ui.design_system import apply_button
        for btn in (self.minus_btn, self.plus_btn):
            apply_button(btn, 'ghost', compact=True)
            btn.setFixedSize(28, 28)
            btn.setMinimumWidth(28)
        self.edit = QLineEdit()
        self.edit.setObjectName('compact-step-value')
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setFixedSize(LINE_NUM_W, FIELD_H)
        layout.addWidget(self.minus_btn)
        layout.addWidget(self.edit)
        layout.addWidget(self.plus_btn)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.minus_btn.clicked.connect(lambda: self.setValue(self.value() - 1))
        self.plus_btn.clicked.connect(lambda: self.setValue(self.value() + 1))
        self.edit.editingFinished.connect(self._commit_edit)
        self.setValue(value)

    def value(self) -> int:
        try:
            return int(self.edit.text().strip())
        except ValueError:
            return self._min

    def setValue(self, value: int):
        clamped = max(self._min, min(self._max, int(value)))
        current = self.edit.text().strip()
        text = str(clamped)
        if current != text:
            self.edit.setText(text)
            self.valueChanged.emit(clamped)
        self.minus_btn.setEnabled(clamped > self._min)
        self.plus_btn.setEnabled(clamped < self._max)

    def setMinimum(self, minimum: int):
        self._min = int(minimum)
        if self._max < self._min:
            self._max = self._min
        self.setValue(self.value())

    def setMaximum(self, maximum: int):
        self._max = int(maximum)
        if self._max < self._min:
            self._min = self._max
        self.setValue(self.value())

    def setRange(self, minimum: int, maximum: int):
        self._min = int(minimum)
        self._max = int(maximum)
        self.setValue(self.value())

    def _commit_edit(self):
        try:
            self.setValue(int(self.edit.text().strip()))
        except ValueError:
            self.setValue(self._min)
