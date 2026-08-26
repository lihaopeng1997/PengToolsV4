# -*- coding: utf-8 -*-
"""全应用统一的表单字段尺寸，保证下拉/录入/日期视觉整齐舒适。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QWidget

# 统一控件高度：与「检出代码」等紧凑按钮对齐
FIELD_H = 28

# 下拉框
# pick：无封闭码值（服务器/分类/日志文件）— 固定宽度，禁止随选项忽大忽小
# enum：封闭码值（GET/模式/主题）— 按最长项 + 箭头一次定宽
COMBO_PICK_W = 200
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

# 标签与胶囊：固定列宽，保证表单字段左缘对齐
CAPTION_W = 80
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


def size_combo(widget, size: str = 'md', *, fill: bool = False) -> None:
    """兼容入口。无封闭码值时请用 size_pick_combo；封闭码值用 size_enum_combo。"""
    if fill:
        size_field_height(widget)
        widget.setMinimumWidth(COMBO_MD[0])
        widget.setMaximumWidth(16777215)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return
    size_pick_combo(widget, COMBO_PICK_W)


def size_pick_combo(combo, width: int = COMBO_PICK_W) -> None:
    """动态列表：固定宽度，刷新选项时不要重算。"""
    from PyQt6.QtWidgets import QComboBox
    size_field_height(combo)
    combo.setFixedWidth(int(width))
    combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if isinstance(combo, QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        view = combo.view()
        if view is not None:
            view.setTextElideMode(Qt.TextElideMode.ElideRight)


def size_enum_combo(combo, *, extra: int = 16, min_w: int = 80, max_w: int = 360) -> None:
    """封闭码值：用 Qt sizeHint（已含箭头/内边距）一次定宽，避免汉字被裁。"""
    from PyQt6.QtWidgets import QComboBox
    if not isinstance(combo, QComboBox):
        return
    size_field_height(combo)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    combo.setMinimumContentsLength(0)
    combo.setMinimumWidth(0)
    combo.setMaximumWidth(16777215)
    width = max(int(min_w), min(int(max_w), int(combo.sizeHint().width()) + int(extra)))
    combo.setFixedWidth(width)


def fit_combo(combo, *, extra: int = 72, min_w: int = 72, max_w: int = 400) -> None:
    """按最长选项留足箭头和内边距，完整显示，不拉满整行。"""
    from PyQt6.QtGui import QFontMetrics
    from PyQt6.QtWidgets import QApplication, QComboBox
    if not isinstance(combo, QComboBox):
        return
    size_field_height(combo)
    font = combo.font()
    app = QApplication.instance()
    if app is not None and (font.pointSize() < 10 or font.pixelSize() in (0, -1)):
        font = app.font()
    metrics = QFontMetrics(font)
    widest = 0
    for index in range(combo.count()):
        widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
    # QSS 右侧箭头约 32px + 左右 padding，必须算进去，否则汉字被裁
    width = max(int(min_w), min(int(max_w), int(widest) + int(extra)))
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    combo.setMinimumContentsLength(0)
    combo.setMinimumWidth(width)
    combo.setMaximumWidth(width)
    combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


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


def apply_caption(label, width: int = CAPTION_W) -> None:
    """表单/行内短标题：固定宽、与 28px 控件垂直居中。"""
    label.setObjectName('field-caption')
    label.setFixedWidth(int(width))
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def size_caption(label) -> None:
    """兼容旧名，转发 apply_caption。"""
    apply_caption(label)


def apply_form(form) -> None:
    """统一表单：标签左齐垂直居中，行距 8，字段可伸展。"""
    from PyQt6.QtWidgets import QFormLayout
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(8)
    try:
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    except Exception:
        pass


def size_status_pill(label, max_width: int = STATUS_PILL_MAX) -> None:
    label.setMaximumWidth(max_width)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


def size_system_chip(label, max_width: int = SYSTEM_CHIP_MAX) -> None:
    label.setMaximumWidth(max_width)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


def wrap_secret_field(edit: QLineEdit, *, reveal_text='查看', hide_text='隐藏') -> tuple[QWidget, QPushButton]:
    """密码默认黑点隐藏，旁边按钮切换明文。"""
    from PyQt6.QtWidgets import QLineEdit as _QLineEdit
    size_line(edit, 'path')
    edit.setEchoMode(_QLineEdit.EchoMode.Password)
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    button = QPushButton(reveal_text)
    size_compact_button(button)
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)

    def _toggle(checked: bool):
        edit.setEchoMode(_QLineEdit.EchoMode.Normal if checked else _QLineEdit.EchoMode.Password)
        button.setText(hide_text if checked else reveal_text)

    button.toggled.connect(_toggle)
    layout.addWidget(edit, 1)
    layout.addWidget(button)
    row._reveal_texts = (reveal_text, hide_text)
    return row, button


def wrap_path_field(edit: QLineEdit, *buttons: QWidget) -> QWidget:
    """路径完整展示（可伸展），右侧放浏览等按钮。"""
    size_line(edit, 'path')
    edit.setToolTip(edit.text())
    edit.textChanged.connect(edit.setToolTip)
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(edit, 1)
    for button in buttons:
        if button is not None:
            layout.addWidget(button)
    return row


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

    def __init__(self, minimum=0, maximum=200, value=0, parent=None, *, edit_width=None, suffix=''):
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
        self.edit.setFixedSize(int(edit_width or LINE_NUM_W), FIELD_H)
        self.suffix_label = QLabel(suffix or '')
        self.suffix_label.setObjectName('field-hint')
        self.suffix_label.setVisible(bool(suffix))
        layout.addWidget(self.minus_btn)
        layout.addWidget(self.edit)
        layout.addWidget(self.plus_btn)
        layout.addWidget(self.suffix_label)
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

    def setSuffix(self, text: str):
        self.suffix_label.setText(text or '')
        self.suffix_label.setVisible(bool(text))

    def _commit_edit(self):
        try:
            self.setValue(int(self.edit.text().strip()))
        except ValueError:
            self.setValue(self._min)
