# -*- coding: utf-8 -*-
"""交互式「下一步 → 下一步」高亮向导浮层（Spotlight Guided Tour）。

设计原则：
- 不在业务页面里常驻占地，点击「使用引导」按钮才弹出；
- 用半透明遮罩 + 圆角挖孔高亮真实目标控件，配套浮窗逐步说明；
- 目标控件不可见时自动居中展示，不崩溃、不越界；
- 纯 UI 表现层（位于 ui/），零业务逻辑耦合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.design_system import apply_button
from ui.theme_manager import ThemeManager


@dataclass
class TourStep:
    target: QWidget | Callable[[], QWidget | None] | None
    title: str
    description: str


def make_guide_button(on_click, language: str = 'zh', parent: QWidget | None = None, *, label: str | None = None) -> QPushButton:
    """构建统一的轻量「使用引导」入口按钮。"""
    zh = language == 'zh'
    btn = QPushButton(label or ('引导' if zh else 'Guide'), parent)
    btn.setObjectName('guided-tour-trigger')
    apply_button(btn, 'ghost', compact=True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip('打开分步交互引导（下一步 / 下一步）' if zh else 'Open step-by-step interactive tour')
    btn.clicked.connect(on_click)
    return btn


class GuidedTourOverlay(QWidget):
    """覆盖在顶层窗口上的分步聚光灯引导浮层。"""

    def __init__(
        self,
        host: QWidget,
        steps: list[TourStep],
        *,
        module_title: str = '',
        language: str = 'zh',
    ):
        top = host.window() if host is not None else None
        parent = top if isinstance(top, QWidget) else host
        super().__init__(parent)
        self._host = host
        self._parent_win = parent
        self._steps = list(steps or [])
        self._module_title = module_title
        self._language = language
        self._index = 0
        self._spot_rect: QRect | None = None

        self.setObjectName('guided-tour-overlay')
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        tm = ThemeManager.instance()
        self._primary = tm.token('PRIMARY') or '#6C58D9'
        self._primary_soft = tm.token('PRIMARY_SOFT') or '#EEE9FF'
        self._border = tm.token('BORDER') or '#DFD9F0'
        self._text_strong = tm.token('TEXT_STRONG') or '#262438'
        self._text = tm.token('TEXT') or '#3D3952'
        self._text_muted = tm.token('TEXT_MUTED') or '#615D73'
        if self._border.lower() == '#000000':
            self._border = '#DFD9F0'

        self._card = QFrame(self)
        self._card.setObjectName('guided-tour-card')
        self._card.setFixedWidth(348)
        self._card.setStyleSheet(
            f'QFrame#guided-tour-card {{'
            f' background-color: #FFFFFF;'
            f' border: 1.5px solid {self._border};'
            f' border-radius: 14px;'
            f'}}'
            f'QLabel#tour-step-pill {{'
            f' background-color: {self._primary_soft};'
            f' color: {self._primary};'
            f' border-radius: 9px;'
            f' padding: 2px 8px;'
            f' font-size: 11px;'
            f' font-weight: 700;'
            f'}}'
            f'QLabel#tour-module {{'
            f' color: {self._text_muted};'
            f' font-size: 11px;'
            f' background: transparent;'
            f'}}'
            f'QLabel#tour-title {{'
            f' color: {self._text_strong};'
            f' font-size: 14px;'
            f' font-weight: 700;'
            f' background: transparent;'
            f'}}'
            f'QLabel#tour-desc {{'
            f' color: {self._text};'
            f' font-size: 13px;'
            f' line-height: 1.5;'
            f' background: transparent;'
            f'}}'
            f'QPushButton#tour-close-x {{'
            f' border: none; background: transparent; color: {self._text_muted};'
            f' font-size: 15px; font-weight: 700; min-width: 22px; min-height: 22px;'
            f' padding: 0px; border-radius: 6px;'
            f'}}'
            f'QPushButton#tour-close-x:hover {{'
            f' background-color: {self._primary_soft}; color: {self._primary};'
            f'}}'
        )
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(24, 20, 44, 72))
        self._card.setGraphicsEffect(shadow)

        card_l = QVBoxLayout(self._card)
        card_l.setContentsMargins(16, 14, 16, 14)
        card_l.setSpacing(8)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)
        self._pill = QLabel('', self._card)
        self._pill.setObjectName('tour-step-pill')
        head_row.addWidget(self._pill)
        self._module_lbl = QLabel(module_title, self._card)
        self._module_lbl.setObjectName('tour-module')
        head_row.addWidget(self._module_lbl, 1)
        close_x = QPushButton('×', self._card)
        close_x.setObjectName('tour-close-x')
        close_x.setCursor(Qt.CursorShape.PointingHandCursor)
        close_x.clicked.connect(self.finish)
        head_row.addWidget(close_x)
        card_l.addLayout(head_row)

        self._title_lbl = QLabel('', self._card)
        self._title_lbl.setObjectName('tour-title')
        self._title_lbl.setWordWrap(True)
        card_l.addWidget(self._title_lbl)

        self._desc_lbl = QLabel('', self._card)
        self._desc_lbl.setObjectName('tour-desc')
        self._desc_lbl.setWordWrap(True)
        card_l.addWidget(self._desc_lbl)

        foot_row = QHBoxLayout()
        foot_row.setContentsMargins(0, 4, 0, 0)
        foot_row.setSpacing(6)
        self._dots_row = QHBoxLayout()
        self._dots_row.setContentsMargins(0, 0, 0, 0)
        self._dots_row.setSpacing(5)
        self._dot_widgets: list[QFrame] = []
        for _ in range(len(self._steps)):
            dot = QFrame(self._card)
            dot.setFixedSize(6, 6)
            self._dots_row.addWidget(dot)
            self._dot_widgets.append(dot)
        foot_row.addLayout(self._dots_row)
        foot_row.addStretch(1)

        zh = language == 'zh'
        self._skip_btn = QPushButton('跳过' if zh else 'Skip', self._card)
        apply_button(self._skip_btn, 'ghost', compact=True)
        self._skip_btn.clicked.connect(self.finish)
        foot_row.addWidget(self._skip_btn)

        self._prev_btn = QPushButton('上一步' if zh else 'Back', self._card)
        apply_button(self._prev_btn, 'secondary', compact=True)
        self._prev_btn.clicked.connect(self.prev_step)
        foot_row.addWidget(self._prev_btn)

        self._next_btn = QPushButton('下一步' if zh else 'Next', self._card)
        apply_button(self._next_btn, 'primary', compact=True)
        self._next_btn.clicked.connect(self.next_step)
        foot_row.addWidget(self._next_btn)
        card_l.addLayout(foot_row)

        if self._parent_win is not None:
            self._parent_win.installEventFilter(self)
            self.setGeometry(self._parent_win.rect())

    def start(self) -> None:
        if not self._steps:
            self.finish()
            return
        self._index = 0
        self._sync_geometry()
        self._render_step()
        self.show()
        self.raise_()
        self.setFocus()

    def next_step(self) -> None:
        if self._index + 1 >= len(self._steps):
            self.finish()
            return
        self._index += 1
        self._render_step()

    def prev_step(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._render_step()

    def finish(self) -> None:
        if self._parent_win is not None:
            try:
                self._parent_win.removeEventFilter(self)
            except Exception:
                pass
        self.hide()
        self.deleteLater()

    def _resolve_target(self, step: TourStep) -> QWidget | None:
        target = step.target
        if callable(target):
            try:
                target = target()
            except Exception:
                return None
        if isinstance(target, QWidget) and target.isVisible() and target.width() > 4 and target.height() > 4:
            return target
        return None

    def _sync_geometry(self) -> None:
        if self._parent_win is not None:
            self.setGeometry(self._parent_win.rect())

    def _render_step(self) -> None:
        total = len(self._steps)
        step = self._steps[self._index]
        zh = self._language == 'zh'
        self._pill.setText(f'步骤 {self._index + 1} / {total}' if zh else f'Step {self._index + 1} / {total}')
        self._title_lbl.setText(step.title)
        self._desc_lbl.setText(step.description)
        for i, dot in enumerate(self._dot_widgets):
            if i == self._index:
                dot.setFixedSize(16, 6)
                dot.setStyleSheet(f'background-color: {self._primary}; border-radius: 3px;')
            else:
                dot.setFixedSize(6, 6)
                dot.setStyleSheet(f'background-color: {self._border}; border-radius: 3px;')
        self._prev_btn.setVisible(self._index > 0)
        self._skip_btn.setVisible(self._index + 1 < total)
        self._next_btn.setText(('完成' if zh else 'Done') if self._index + 1 >= total else ('下一步' if zh else 'Next'))

        target = self._resolve_target(step)
        self._spot_rect = None
        if target is not None and self._parent_win is not None:
            top_left = target.mapTo(self._parent_win, QPoint(0, 0))
            rect = QRect(top_left, target.size()).adjusted(-6, -5, 6, 5)
            bounds = self.rect().adjusted(6, 6, -6, -6)
            clipped = rect.intersected(bounds)
            if clipped.width() > 8 and clipped.height() > 8:
                self._spot_rect = clipped

        self._card.adjustSize()
        self._position_card()
        self.update()

    def _position_card(self) -> None:
        cw = self._card.width()
        ch = self._card.height()
        margin = 16
        area = self.rect().adjusted(margin, margin, -margin, -margin)

        if self._spot_rect is None:
            x = area.left() + max(0, (area.width() - cw) // 2)
            y = area.top() + max(0, (area.height() - ch) // 2)
            self._card.move(x, y)
            return

        spot = self._spot_rect
        gap = 14
        # 优先下方 → 上方 → 右侧 → 左侧 → 居中兜底
        candidates = [
            QPoint(spot.left(), spot.bottom() + gap),
            QPoint(spot.left(), spot.top() - ch - gap),
            QPoint(spot.right() + gap, spot.top()),
            QPoint(spot.left() - cw - gap, spot.top()),
        ]
        chosen = None
        for pt in candidates:
            x = max(area.left(), min(pt.x(), area.right() - cw))
            y = max(area.top(), min(pt.y(), area.bottom() - ch))
            cand_rect = QRect(x, y, cw, ch)
            if not cand_rect.intersects(spot):
                chosen = QPoint(x, y)
                break
        if chosen is None:
            x = area.left() + max(0, (area.width() - cw) // 2)
            y = area.bottom() - ch
            chosen = QPoint(x, max(area.top(), y))
        self._card.move(chosen)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        if self._spot_rect is not None:
            path.addRoundedRect(QRectF(self._spot_rect), 10.0, 10.0)
        painter.fillPath(path, QColor(24, 20, 44, 118))

        if self._spot_rect is not None:
            # 柔光紫圈 + 清晰高光边框
            glow_pen = QPen(QColor(108, 88, 217, 72), 6.0)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self._spot_rect), 10.0, 10.0)

            ring_pen = QPen(QColor(self._primary), 2.0)
            painter.setPen(ring_pen)
            painter.drawRoundedRect(QRectF(self._spot_rect), 10.0, 10.0)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._parent_win and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._sync_geometry()
            if self._steps and self.isVisible():
                self._render_step()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.finish()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.next_step()
            return
        if key == Qt.Key.Key_Left:
            self.prev_step()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if not self._card.geometry().contains(event.position().toPoint()):
            self.next_step()
            return
        super().mousePressEvent(event)


def start_guided_tour(
    host: QWidget,
    steps: list[TourStep] | list[tuple],
    *,
    module_title: str = '',
    language: str = 'zh',
) -> GuidedTourOverlay:
    """启动分步交互式高亮向导；若已有未关闭的同窗口向导，会先关闭旧实例。"""
    top = host.window() if host is not None else None
    if isinstance(top, QWidget):
        for child in top.findChildren(GuidedTourOverlay):
            child.finish()
    normalized = [
        s if isinstance(s, TourStep) else TourStep(s[0], s[1], s[2])
        for s in (steps or [])
    ]
    overlay = GuidedTourOverlay(host, normalized, module_title=module_title, language=language)
    overlay.start()
    return overlay
