# -*- coding: utf-8 -*-
"""可折叠的格式化文本编辑器。

- 左侧：折叠标记（+/-）
- 右侧：CodeGlance 风格缩略导航（可拖拽滚动 / 拖宽）
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QTextFormat
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from tools.code_folding import compute_fold_regions, lines_hidden_by_collapsed
from ui.code_glance import CodeGlanceBar


class _FoldMargin(QWidget):
    """左侧折叠标记栏。"""

    def __init__(self, editor: 'FoldablePlainTextEdit'):
        super().__init__(editor)
        self._editor = editor
        self.setObjectName('fold-margin')
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(self._editor.fold_margin_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_fold_margin(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._editor.toggle_fold_at_y(event.position().y())
        super().mousePressEvent(event)


class FoldablePlainTextEdit(QPlainTextEdit):
    """带折叠边栏 + 右侧缩略图的 QPlainTextEdit。"""

    folds_changed = pyqtSignal()

    def __init__(self, parent=None, *, fold_mode: str = 'auto', glance: bool = True):
        super().__init__(parent)
        self._fold_mode = fold_mode
        self._regions: list[tuple[int, int]] = []
        self._region_map: dict[int, int] = {}
        self._collapsed: set[int] = set()
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(280)
        self._rebuild_timer.timeout.connect(self._rebuild_folds_keep_collapsed)
        self._updating_visibility = False
        self._glance_enabled = bool(glance)
        self._search_selections: list = []

        self._fold_margin = _FoldMargin(self)
        self._fold_margin.show()
        self._glance = CodeGlanceBar(self) if self._glance_enabled else None
        # 初始无内容时不展示右侧缩略条，避免未布局时错贴到左侧
        if self._glance is not None:
            self._glance.hide()

        self.blockCountChanged.connect(self._update_margin_width)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.textChanged.connect(self._on_text_changed)

        self._update_margin_width(0)
        self._highlight_current_line()

    # ── public API ──────────────────────────────────────

    def fold_margin_width(self) -> int:
        return 18

    def glance_width(self) -> int:
        """仅当缩略条实际应显示时占用右边距。"""
        if not self._glance_should_show():
            return 0
        # 用内部宽度，勿依赖 isVisible（布局前可能仍为 hidden）
        return max(0, int(getattr(self._glance, '_width', 0) or 0))

    def _has_glance_content(self) -> bool:
        return bool((self.toPlainText() or '').strip())

    def _glance_should_show(self) -> bool:
        """用户开启 + 有正文 + 宽度够贴右侧。"""
        if not self._glance_enabled or self._glance is None:
            return False
        if not self._has_glance_content():
            return False
        # 过窄时不强行显示，否则会叠到左折叠栏
        cr = self.contentsRect()
        need = self.fold_margin_width() + self._glance._width + 48
        return cr.width() >= need

    def set_glance_visible(self, visible: bool):
        self._glance_enabled = bool(visible)
        if self._glance is None and self._glance_enabled:
            self._glance = CodeGlanceBar(self)
            self._glance.hide()
        self._update_margin_width()
        self._layout_side_bars()

    def set_fold_mode(self, mode: str):
        self._fold_mode = mode or 'auto'
        self.rebuild_folds(expand_all=False)

    def fold_regions(self) -> list[tuple[int, int]]:
        return list(self._regions)

    def collapsed_starts(self) -> set[int]:
        return set(self._collapsed)

    def rebuild_folds(self, *, expand_all: bool = False):
        """根据当前文本重算折叠区。"""
        text = self.toPlainText()
        self._regions = compute_fold_regions(text, mode=self._fold_mode)
        self._region_map = {s: e for s, e in self._regions}
        if expand_all:
            self._collapsed.clear()
        else:
            valid = set(self._region_map)
            self._collapsed = {s for s in self._collapsed if s in valid}
        self._apply_visibility()
        self._fold_margin.update()
        if self._glance is not None:
            self._glance.invalidate()
        self.folds_changed.emit()

    def expand_all_folds(self):
        self._collapsed.clear()
        self._apply_visibility()
        self._fold_margin.update()
        if self._glance is not None:
            self._glance.update()
        self.folds_changed.emit()

    def collapse_all_folds(self):
        self._collapsed = set(self._region_map.keys())
        self._apply_visibility()
        self._fold_margin.update()
        if self._glance is not None:
            self._glance.update()
        self.folds_changed.emit()

    def collapse_to_depth(self, max_depth: int = 1):
        """保留浅层展开：仅折叠深度 >= max_depth 的块。"""
        depths = {}
        for start, end in self._regions:
            depth = 0
            for s2, e2 in self._regions:
                if s2 < start and e2 >= end:
                    depth += 1
            depths[start] = depth
        self._collapsed = {s for s, d in depths.items() if d >= max_depth}
        self._apply_visibility()
        self._fold_margin.update()
        if self._glance is not None:
            self._glance.update()
        self.folds_changed.emit()

    def toggle_fold_at_line(self, line: int) -> bool:
        """切换某行起点的折叠；若该行不是折叠点返回 False。"""
        if line not in self._region_map:
            candidates = [s for s, e in self._regions if s <= line <= e and s in self._region_map]
            if not candidates:
                return False
            line = max(candidates)
            if line not in self._region_map:
                return False
        if line in self._collapsed:
            self._collapsed.discard(line)
        else:
            self._collapsed.add(line)
        self._apply_visibility()
        self._fold_margin.update()
        if self._glance is not None:
            self._glance.update()
        self.folds_changed.emit()
        return True

    def toggle_fold_at_y(self, y: float):
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        while block.isValid():
            geo = self.blockBoundingGeometry(block).translated(offset)
            if geo.top() <= y < geo.bottom():
                self.toggle_fold_at_line(block.blockNumber())
                return
            if geo.top() > y:
                return
            block = block.next()

    def setPlainText(self, text: str):  # noqa: N802 — Qt API
        super().setPlainText(text or '')
        self.rebuild_folds(expand_all=True)
        if self._glance is not None:
            self._glance.invalidate()

    # ── margin / layout ─────────────────────────────────

    def _update_margin_width(self, _count=0):
        left = self.fold_margin_width()
        right = self.glance_width()
        self.setViewportMargins(left, 0, right, 0)
        self._layout_side_bars()

    def _layout_side_bars(self):
        cr = self.contentsRect()
        if cr.width() <= 0 or cr.height() <= 0:
            return
        left_w = self.fold_margin_width()
        self._fold_margin.setGeometry(QRect(cr.left(), cr.top(), left_w, cr.height()))
        self._fold_margin.show()
        self._fold_margin.raise_()

        if self._glance is None:
            return
        show = self._glance_should_show()
        if not show:
            # 无内容或过窄：彻底藏到不可见，避免错贴到左侧
            self._glance.hide()
            return

        right_w = max(1, int(self._glance._width))
        # 贴编辑器右缘；x 不得侵入左侧折叠栏
        x = cr.left() + cr.width() - right_w
        min_x = cr.left() + left_w
        if x < min_x:
            self._glance.hide()
            return
        self._glance.setGeometry(QRect(x, cr.top(), right_w, cr.height()))
        self._glance.show()
        self._glance.raise_()

    def _on_update_request(self, rect: QRect, dy: int):
        if dy:
            self._fold_margin.scroll(0, dy)
        else:
            self._fold_margin.update(0, rect.y(), self._fold_margin.width(), rect.height())
        if self._glance is not None and self._glance.isVisible():
            self._glance.update()
        if rect.contains(self.viewport().rect()):
            self._update_margin_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_side_bars()

    def showEvent(self, event):
        super().showEvent(event)
        # 首次显示时 contentsRect 才可靠，再摆一次右侧条
        self._update_margin_width()

    def paint_fold_margin(self, event):
        painter = QPainter(self._fold_margin)
        try:
            from ui.theme_manager import ThemeManager
            pal = ThemeManager.instance().palette()
            bg = QColor(pal.get('CODE_BG', pal.get('SURFACE_MUTED', '#F0F2F5')))
            border = QColor(pal.get('BORDER', '#D0D5DD'))
            icon = QColor(pal.get('PRIMARY', '#2F6FED'))
            icon_muted = QColor(pal.get('TEXT_MUTED', '#667085'))
        except Exception:
            bg = QColor('#F3F4F6')
            border = QColor('#D0D5DD')
            icon = QColor('#2F6FED')
            icon_muted = QColor('#667085')

        painter.fillRect(event.rect(), bg)
        painter.setPen(border)
        painter.drawLine(
            self._fold_margin.width() - 1,
            event.rect().top(),
            self._fold_margin.width() - 1,
            event.rect().bottom(),
        )

        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        top = self.blockBoundingGeometry(block).translated(offset).top()
        bottom = top + self.blockBoundingRect(block).height()

        font = QFont(self.font())
        font.setPointSize(max(8, font.pointSize() - 1))
        font.setBold(True)
        painter.setFont(font)

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line = block.blockNumber()
                if line in self._region_map:
                    collapsed = line in self._collapsed
                    mark = '+' if collapsed else '−'
                    box = QRect(
                        (self.fold_margin_width() - 12) // 2,
                        int(top + (bottom - top - 12) / 2),
                        12,
                        12,
                    )
                    painter.setPen(icon if collapsed else icon_muted)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(box)
                    painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), mark)
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
        painter.end()

    def set_search_selections(self, selections: list | None):
        """搜索高亮（与当前行高亮合并，避免互相覆盖）。"""
        self._search_selections = list(selections or [])
        self._refresh_extra_selections()

    def _highlight_current_line(self):
        self._refresh_extra_selections()
        if self._glance is not None:
            self._glance.update()

    def _refresh_extra_selections(self):
        extras = list(self._search_selections or [])
        if not self.isReadOnly():
            sel = QTextEdit.ExtraSelection()
            try:
                from ui.theme_manager import ThemeManager
                pal = ThemeManager.instance().palette()
                color = QColor(pal.get('SURFACE_MUTED', '#F5F7FA'))
            except Exception:
                color = QColor('#F5F7FA')
            color.setAlpha(90)
            sel.format.setBackground(color)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            extras.append(sel)
        self.setExtraSelections(extras)

    # ── fold apply ──────────────────────────────────────

    def _on_text_changed(self):
        if self._updating_visibility:
            return
        if self._collapsed:
            self._collapsed.clear()
            self._apply_visibility()
        # 内容从空→有 / 有→空 时刷新右侧缩略条显隐
        self._update_margin_width()
        self._rebuild_timer.start()

    def _rebuild_folds_keep_collapsed(self):
        self.rebuild_folds(expand_all=False)

    def _apply_visibility(self):
        hidden = lines_hidden_by_collapsed(self._regions, self._collapsed)
        doc = self.document()
        self._updating_visibility = True
        try:
            block = doc.firstBlock()
            while block.isValid():
                line = block.blockNumber()
                visible = line not in hidden
                if block.isVisible() != visible:
                    block.setVisible(visible)
                block = block.next()
            self.viewport().update()
            self.document().markContentsDirty(0, self.document().characterCount())
            self._update_margin_width()
            cursor = self.textCursor()
            if not cursor.block().isVisible():
                b = cursor.block()
                while b.isValid() and not b.isVisible():
                    b = b.previous()
                if b.isValid():
                    cursor.setPosition(b.position())
                    self.setTextCursor(cursor)
        finally:
            self._updating_visibility = False
            self._fold_margin.update()
            if self._glance is not None:
                self._glance.update()
