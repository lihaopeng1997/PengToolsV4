# -*- coding: utf-8 -*-
"""PengTools 全局设计系统基础。"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, QVariantAnimation, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QSizePolicy, QTableWidget, QTabWidget, QTreeWidget, QWidget,
)

from ui.field_metrics import size_compact_button
from ui.icons import apply_icon
from ui.table_utils import autofit_columns
from ui import layout_metrics as _lm
from ui.theme_manager import FONT_MONO_FAMILIES, FONT_MONO_POINT, FONT_ROLES, FONT_UI_FAMILY

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


def mono_font() -> QFont:
    """SQL / 代码 / 终端共用的等宽字体，字号只有 FONT_MONO_POINT 一档。"""
    font = QFont()
    font.setFamilies(list(FONT_MONO_FAMILIES))
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setKerning(False)
    font.setPointSize(FONT_MONO_POINT)
    return font


def apply_text_role(widget, role: str) -> None:
    """把控件字号锁到字体阶梯。不改文案、信号和 objectName。"""
    spec = FONT_ROLES[role]
    font = widget.font()
    font.setFamily(FONT_UI_FAMILY)
    font.setPixelSize(int(spec['size']))
    weight = QFont.Weight.DemiBold if int(spec['weight']) >= 600 else QFont.Weight.Normal
    font.setWeight(weight)
    widget.setFont(font)
    widget.setProperty('textRole', role)

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


class _RippleOverlay(QWidget):
    """清风玻璃原生按钮点击水波纹覆盖层：零点击劫持，隐藏/销毁自动停止。"""

    def __init__(self, target: QWidget, *, primary: bool = False):
        super().__init__(target)
        self._target = target
        self._primary = bool(primary)
        self._center = QPointF(0.0, 0.0)
        self._progress = 0.0
        self._active = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(360)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_step)
        self._anim.finished.connect(self._on_done)
        self.hide()

    def set_primary(self, primary: bool) -> None:
        self._primary = bool(primary)

    def trigger(self, pos: QPointF) -> None:
        if not self._target.isVisible() or not self._target.isEnabled():
            return
        self.setGeometry(self._target.rect())
        self._center = QPointF(pos)
        self._progress = 0.0
        self._active = True
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.start()

    def stop_now(self) -> None:
        self._anim.stop()
        self._active = False
        self.hide()

    def _on_step(self, value) -> None:
        self._progress = float(value or 0.0)
        self.update()

    def _on_done(self) -> None:
        self._active = False
        self.hide()

    def paintEvent(self, _event) -> None:
        if not self._active:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            radius_clip = float(min(h / 2.0, 16.0 if self._primary else 10.0))
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, max(0.0, w - 1.0), max(0.0, h - 1.0)), radius_clip, radius_clip)
            painter.setClipPath(path)
            max_r = max(w, h) * 1.15
            cur_r = max_r * self._progress
            alpha = int(max(0.0, (1.0 - self._progress) * (115.0 if self._primary else 82.0)))
            if self._primary:
                color = QColor(255, 255, 255, alpha)
            else:
                try:
                    from ui.theme_manager import ThemeManager
                    base = QColor(ThemeManager.instance().token('PRIMARY') or '#6C58D9')
                except Exception:
                    base = QColor('#6C58D9')
                color = QColor(base.red(), base.green(), base.blue(), alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(self._center, cur_r, cur_r)
        finally:
            painter.end()


class _AiryRippleFilter(QObject):
    def __init__(self, button: QWidget, *, primary: bool = False):
        super().__init__(button)
        self._button = button
        self._overlay = _RippleOverlay(button, primary=primary)

    def set_primary(self, primary: bool) -> None:
        self._overlay.set_primary(primary)

    def eventFilter(self, watched, event) -> bool:
        if watched is not self._button:
            return False
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress and hasattr(event, 'position'):
            if event.button() == Qt.MouseButton.LeftButton:
                self._overlay.trigger(event.position())
        elif etype == QEvent.Type.Resize:
            if self._overlay.isVisible():
                self._overlay.setGeometry(self._button.rect())
        elif etype in (QEvent.Type.Hide, QEvent.Type.EnabledChange):
            if etype == QEvent.Type.Hide or not self._button.isEnabled():
                self._overlay.stop_now()
        return False


class _GlobalButtonRippleFilter(QObject):
    """全局原生 QPushButton 水波纹钩子：覆盖未显式调用 apply_button() 的工作台按钮。"""

    _SKIP_NAMES = frozenset({'floating-toggle', 'prism-win-min', 'prism-win-max', 'prism-win-close'})

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and hasattr(event, 'position'):
            if event.button() == Qt.MouseButton.LeftButton:
                from PyQt6.QtWidgets import QPushButton
                if isinstance(watched, QPushButton) and watched.isEnabled():
                    if getattr(watched, '_airy_ripple_filter', None) is None:
                        obj_name = watched.objectName() or ''
                        if obj_name not in self._SKIP_NAMES and watched.width() >= 26 and watched.height() >= 20:
                            is_primary = (
                                obj_name == 'primary-btn'
                                or watched.property('actionRole') == 'primary'
                            )
                            install_button_ripple(watched, primary=is_primary)
                            flt = getattr(watched, '_airy_ripple_filter', None)
                            if isinstance(flt, _AiryRippleFilter):
                                flt._overlay.trigger(event.position())
        return False


def install_global_button_ripples(app) -> None:
    """保留兼容入口：已关闭点击扩散水波纹，保持桌面按钮即时利落反馈。"""
    _ = app


def install_button_ripple(button, *, primary: bool = False) -> None:
    """保留兼容入口：已关闭点击扩散水波纹，保持桌面按钮即时利落反馈。"""
    _, _ = button, primary


def apply_button(
    button,
    role: str = 'secondary',
    *,
    compact: bool = True,
    icon: str | None = None,
    icon_size: int = 18,
) -> None:
    """为按钮打上设计系统角色，不改 clicked 信号与文案。"""
    object_name = BUTTON_ROLES.get(role, BUTTON_ROLES['secondary'])
    button.setObjectName(object_name)
    button.setProperty('actionRole', role)
    if compact:
        size_compact_button(button)
        button.setProperty('compactAction', True)
    else:
        button.setFixedHeight(32)
        button.setProperty('compactAction', False)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    install_button_ripple(button, primary=(role == 'primary'))
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
                danger = ThemeManager.instance().token('DANGER') or '#C45371'
            except Exception:
                danger = '#C45371'
            icon_kwargs = {'normal': danger, 'active': danger}
        elif role == 'fold':
            try:
                from ui.theme_manager import ThemeManager
                accent = ThemeManager.instance().token('PRIMARY_ACTIVE') or '#503DBE'
            except Exception:
                accent = '#503DBE'
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
    """统一表格交互基线与自动列宽。"""
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
    autofit_columns(table)


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
    """轻量表面角色：card | zone | muted | glass | elevated | tech。"""
    names = {
        'card': 'ds-card',
        'zone': 'ds-zone',
        'muted': 'ds-muted',
        'glass': 'ds-glass',
        'elevated': 'ds-elevated',
        'tech': 'ds-tech',
    }
    frame.setObjectName(names.get(kind, 'ds-card'))
    frame.setProperty('surfaceRole', kind)
    style = frame.style()
    if style is not None:
        style.unpolish(frame)
        style.polish(frame)
    frame.update()
