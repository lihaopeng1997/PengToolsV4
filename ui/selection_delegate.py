# -*- coding: utf-8 -*-
"""列表/表格选中态高对比绘制：忽略 item.setForeground 对选中字色的覆盖。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem


def theme_select_colors():
    """选中：主题软底 + 主色字（各主题一致，避免系统蓝/实心主色块）。"""
    try:
        from ui.theme_manager import ThemeManager
        pal = ThemeManager.instance().palette()
        return (
            QColor(pal.get('TABLE_SELECT', '#E9F1EB')),
            QColor(pal.get('PRIMARY_ACTIVE', '#3D594A')),
            QColor(pal.get('TEXT_STRONG', '#272B29')),
        )
    except Exception:
        return QColor('#E9F1EB'), QColor('#3D594A'), QColor('#272B29')


def _theme_select_colors():
    return theme_select_colors()


class HighContrastSelectDelegate(QStyledItemDelegate):
    """选中：TABLE_SELECT 底 + PRIMARY_ACTIVE 字。"""

    def paint(self, painter, option: QStyleOptionViewItem, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if not selected:
            super().paint(painter, option, index)
            return
        fill, accent, _text = theme_select_colors()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        painter.fillRect(opt.rect, fill)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        painter.setPen(accent)
        text_rect = opt.rect.adjusted(8, 0, -6, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )
        painter.restore()


class WrappingSelectDelegate(QStyledItemDelegate):
    """多列表格/树：路径等长文本换行，选中跟主题走。"""

    def __init__(self, parent=None, max_lines: int = 3):
        super().__init__(parent)
        self._max_lines = max(1, int(max_lines))

    def paint(self, painter, option: QStyleOptionViewItem, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        fill, accent, text_strong = theme_select_colors()
        opt.textElideMode = Qt.TextElideMode.ElideNone
        opt.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        if selected:
            opt.backgroundBrush = fill
            opt.palette.setColor(opt.palette.ColorRole.Text, accent)
            opt.palette.setColor(opt.palette.ColorRole.HighlightedText, accent)
            opt.palette.setColor(opt.palette.ColorRole.WindowText, accent)
        else:
            opt.palette.setColor(opt.palette.ColorRole.Text, text_strong)
        super().paint(painter, opt, index)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        if not text:
            return hint
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtCore import QSize
        fm = QFontMetrics(option.font)
        width = option.rect.width() if option.rect.width() > 40 else 180
        wrapped = fm.boundingRect(0, 0, max(60, width - 28), 400, int(Qt.TextFlag.TextWordWrap), text)
        lines = min(self._max_lines, max(1, wrapped.height() // max(1, fm.lineSpacing())))
        height = max(hint.height(), lines * fm.lineSpacing() + 10)
        return QSize(hint.width(), height)
