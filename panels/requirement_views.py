# -*- coding: utf-8 -*-
"""需求管理页的展示件：进度阶段、状态分组。

只放与业务无关的展示逻辑；需求数据读写仍由 tools.requirements 负责。
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
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


class StageBar(QWidget):
    """五段进度条：已过阶段实色，未到阶段浅色，暂停整体置灰。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('req-stage-bar')
        self._index = -1
        self._paused = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(6)

    def set_status(self, status: str):
        self._paused = str(status or '').strip() == PAUSED_STATUS
        self._index = stage_index(status)
        self.setToolTip(' → '.join(label for _key, label, _members in STAGES))
        self.update()

    def sizeHint(self):
        return QSize(240, 6)

    def paintEvent(self, _event):
        try:
            from ui.theme_manager import ThemeManager
            manager = ThemeManager.instance()
            done = QColor(manager.token('PRIMARY'))
            todo = QColor(manager.token('BORDER'))
            muted = QColor(manager.token('TEXT_MUTED'))
        except Exception:
            done, todo, muted = QColor('#6C58D9'), QColor('#E6E2F0'), QColor('#615D73')
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        count = len(STAGES)
        gap = 4.0
        width = max(1.0, (self.width() - gap * (count - 1)) / count)
        for index in range(count):
            if self._paused:
                color = muted if index <= max(self._index, 0) else todo
            else:
                color = done if index <= self._index else todo
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(index * (width + gap), 0, width, self.height()), 3, 3)
        painter.end()
