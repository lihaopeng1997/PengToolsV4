# -*- coding: utf-8 -*-
"""需求管理页的展示件：进度阶段、状态分组。

只放与业务无关的展示逻辑；需求数据读写仍由 tools.requirements 负责。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from tools.requirement_stages import (  # noqa: F401  (re-exported for the panel)
    GROUP_TARGET_STATUS, PAUSED_STATUS, STAGES, STATUS_GROUP_LABELS, STATUS_GROUP_ORDER,
    known_statuses, stage_index, status_group,
)

# 默认收起的分组，避免历史上线项淹没当前工作。
COLLAPSED_GROUPS = frozenset({'online', 'paused'})


def status_tone(status: str) -> str:
    """状态语义色名，对应主题 token。"""
    group = status_group(status)
    return {
        'develop': 'PRIMARY',
        'testing': 'WARNING',
        'release': 'SUCCESS',
        'online': 'SUCCESS',
    }.get(group, 'TEXT_MUTED')


# 各阶段专属颜色（与首页看板一致）：分析 浅紫 / 开发 主紫 / 测试 琥珀 / 待上线 青 / 已上线 绿
STAGE_COLOR_TOKENS = {
    'analysis': ('AURORA_END', '#A99AF5'),
    'develop': ('PRIMARY', '#6C58D9'),
    'testing': ('WARNING', '#A5772A'),
    'release': ('ACCENT_CYAN', '#0EA5E9'),
    'online': ('SUCCESS', '#247F75'),
}


class StageBar(QWidget):
    """节点式步骤条：每个阶段一种颜色；已完成实心带勾，当前放大带光晕并标出细状态，
    未到阶段空心灰色；暂停时整体置灰并标注「暂停」。"""

    _NODE = 16.0
    _CURRENT = 22.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('req-stage-bar')
        self._index = -1
        self._paused = False
        self._status = ''
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._height())

    def _height(self) -> int:
        line = self.fontMetrics().height()
        return int(self._CURRENT + 6 + line * 2 + 2)

    def set_status(self, status: str):
        self._status = str(status or '').strip()
        self._paused = self._status == PAUSED_STATUS
        # 没选需求时不标任何阶段
        self._index = stage_index(status) if self._status else -1
        self.setToolTip(' → '.join(label for _key, label, _members in STAGES))
        self.setFixedHeight(self._height())
        self.update()

    def changeEvent(self, event):  # noqa: N802 — 字体变化时重算高度
        super().changeEvent(event)
        if event.type() == event.Type.FontChange:
            self.setFixedHeight(self._height())

    def sizeHint(self):
        return QSize(360, self._height())

    def minimumSizeHint(self):
        return QSize(200, self._height())

    def _colors(self):
        try:
            from ui.theme_manager import ThemeManager
            manager = ThemeManager.instance()
            token = lambda name, fallback: QColor(manager.token(name) or fallback)  # noqa: E731
        except Exception:
            token = lambda _name, fallback: QColor(fallback)  # noqa: E731
        stage_colors = [token(*STAGE_COLOR_TOKENS.get(key, ('PRIMARY', '#6C58D9'))) for key, _l, _m in STAGES]
        return (
            stage_colors,
            token('BORDER_STRONG', '#D0CBDD'),
            token('TEXT_MUTED', '#615D73'),
            token('TEXT_STRONG', '#262438'),
            token('SURFACE', '#FDFDFF'),
        )

    def paintEvent(self, _event):
        stage_colors, idle, muted, strong, surface = self._colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = len(STAGES)
        pad = self._CURRENT / 2 + 4
        span = max(1.0, self.width() - 2 * pad)
        step = span / max(1, count - 1)
        cy = self._CURRENT / 2 + 2
        xs = [pad + i * step for i in range(count)]
        current = self._index
        paused = self._paused

        def color_for(i: int) -> QColor:
            return QColor(muted) if paused else QColor(stage_colors[i])

        # 连线：已走过的段用两端阶段色渐变，未到的段为灰
        for i in range(count - 1):
            x1 = xs[i] + self._NODE / 2 + 2
            x2 = xs[i + 1] - self._NODE / 2 - 2
            if x2 <= x1:
                continue
            if i < current:
                gradient = QLinearGradient(x1, cy, x2, cy)
                gradient.setColorAt(0.0, color_for(i))
                gradient.setColorAt(1.0, color_for(i + 1))
                pen = QPen(QBrush(gradient), 3)
            else:
                pen = QPen(idle, 2, Qt.PenStyle.DashLine if not paused else Qt.PenStyle.SolidLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(x1, cy), QPointF(x2, cy))

        label_font = QFont(self.font())
        bold_font = QFont(self.font())
        bold_font.setBold(True)
        small_font = QFont(self.font())
        small_font.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.85) if self.font().pointSizeF() > 0 else 8.0)
        line_h = self.fontMetrics().height()
        label_top = self._CURRENT + 6

        for i, (_key, label, _members) in enumerate(STAGES):
            x = xs[i]
            c = color_for(i)
            if i == current:
                halo = QColor(c)
                halo.setAlpha(46)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(halo)
                painter.drawEllipse(QPointF(x, cy), self._CURRENT / 2 + 3, self._CURRENT / 2 + 3)
                painter.setBrush(c)
                painter.drawEllipse(QPointF(x, cy), self._CURRENT / 2, self._CURRENT / 2)
                painter.setBrush(surface)
                painter.drawEllipse(QPointF(x, cy), 4, 4)
            elif i < current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(c)
                painter.drawEllipse(QPointF(x, cy), self._NODE / 2, self._NODE / 2)
                check = QPen(surface, 2)
                check.setCapStyle(Qt.PenCapStyle.RoundCap)
                check.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(check)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline([QPointF(x - 4, cy), QPointF(x - 1, cy + 3), QPointF(x + 4, cy - 3)])
            else:
                painter.setPen(QPen(idle, 2))
                painter.setBrush(surface)
                painter.drawEllipse(QPointF(x, cy), self._NODE / 2 - 1, self._NODE / 2 - 1)

            # 阶段名：当前加粗着色，已完成正常色，未到灰色
            is_current = i == current
            painter.setFont(bold_font if is_current else label_font)
            painter.setPen(c if is_current else (strong if i < current and not paused else muted))
            rect = QRectF(x - step / 2, label_top, step, line_h)
            align = Qt.AlignmentFlag.AlignHCenter
            if i == 0:
                rect = QRectF(0, label_top, step, line_h)
                align = Qt.AlignmentFlag.AlignLeft
            elif i == count - 1:
                rect = QRectF(self.width() - step, label_top, step, line_h)
                align = Qt.AlignmentFlag.AlignRight
            painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, label)
            if is_current:
                detail = self._status if self._status and self._status != label else ''
                if detail:
                    painter.setFont(small_font)
                    painter.setPen(muted)
                    painter.drawText(rect.translated(0, line_h), align | Qt.AlignmentFlag.AlignVCenter, detail)

        if paused:
            painter.setFont(bold_font)
            painter.setPen(muted)
            painter.drawText(QRectF(0, label_top + line_h, self.width(), line_h),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, '暂停')
        painter.end()
