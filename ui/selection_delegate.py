# -*- coding: utf-8 -*-
"""列表/表格选中态高对比绘制：忽略 item.setForeground 对选中字色的覆盖。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem


def _theme_select_colors():
    try:
        from ui.theme_manager import ThemeManager
        pal = ThemeManager.instance().palette()
        return (
            QColor(pal.get('PRIMARY', '#668C78')),
            QColor(pal.get('ON_PRIMARY', '#FFFFFF')),
            QColor(pal.get('TEXT_STRONG', '#272B29')),
        )
    except Exception:
        return QColor('#668C78'), QColor('#FFFFFF'), QColor('#272B29')


class HighContrastSelectDelegate(QStyledItemDelegate):
    """选中：主色底 + ON_PRIMARY 字；未选中走默认绘制。"""

    def paint(self, painter, option: QStyleOptionViewItem, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if not selected:
            super().paint(painter, option, index)
            return
        primary, on_primary, _text = _theme_select_colors()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        painter.fillRect(opt.rect, primary)
        # 去掉 Selected 标记再交给 style 画图标/对齐，但文字我们自绘
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        painter.setPen(on_primary)
        # 内边距与表格一致
        text_rect = opt.rect.adjusted(8, 0, -6, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )
        painter.restore()
