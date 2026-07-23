# -*- coding: utf-8 -*-
"""CodeGlance 风格右侧缩略导航条（类似 CodeGlancePro）。

- 整份文档缩略预览
- 半透明视口框可拖拽滚动
- 点击跳转
- 左边可拖宽/收窄
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

_DEFAULT_WIDTH = 80
_MIN_WIDTH = 48
_MAX_WIDTH = 160
_RESIZE_GRIP = 5

_KEY_LINE = re.compile(r'^\s*"[^"]+"\s*:')
_NUM_LINE = re.compile(r'^\s*-?\d')
_STR_LINE = re.compile(r'^\s*"')
_BOOL_LINE = re.compile(r'^\s*(true|false|null)\b', re.I)
_TAG_LINE = re.compile(r'^\s*</?[A-Za-z_:]')


class CodeGlanceBar(QWidget):
    """附着在 QPlainTextEdit 右侧的缩略图。"""

    def __init__(self, editor: QPlainTextEdit, parent=None, *, width: int = _DEFAULT_WIDTH):
        super().__init__(parent or editor)
        self._editor = editor
        self._width = max(_MIN_WIDTH, min(_MAX_WIDTH, int(width)))
        self._dragging_view = False
        self._resizing = False
        self._drag_offset_y = 0.0
        self._lines_cache: list[str] = []
        self._cache_rev = -1
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(80)
        self._refresh_timer.timeout.connect(self.update)

        self.setObjectName('code-glance')
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        # 同步：滚动 / 文本 / 光标
        sb = editor.verticalScrollBar()
        sb.valueChanged.connect(self._schedule_refresh)
        sb.rangeChanged.connect(self._schedule_refresh)
        editor.cursorPositionChanged.connect(self._schedule_refresh)
        editor.textChanged.connect(self._on_text_changed)
        editor.updateRequest.connect(lambda *_: self._schedule_refresh())

    # ── public ──────────────────────────────────────────

    def preferred_width(self) -> int:
        """配置宽度（是否展示由宿主决定）。"""
        return self._width

    def set_glance_width(self, width: int):
        self._width = max(_MIN_WIDTH, min(_MAX_WIDTH, int(width)))
        self.updateGeometry()
        self.update()
        # 通知宿主重算 viewportMargins
        host = self._editor
        if hasattr(host, '_update_margin_width'):
            host._update_margin_width()
        if hasattr(host, '_layout_side_bars'):
            host._layout_side_bars()

    def sizeHint(self) -> QSize:
        return QSize(self._width, 100)

    def invalidate(self):
        self._cache_rev = -1
        self._schedule_refresh()

    # ── data ────────────────────────────────────────────

    def _on_text_changed(self):
        self._cache_rev = -1
        self._schedule_refresh()

    def _schedule_refresh(self):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _ensure_lines(self) -> list[str]:
        doc = self._editor.document()
        rev = doc.revision()
        if rev != self._cache_rev:
            text = self._editor.toPlainText()
            self._lines_cache = text.splitlines() or ['']
            self._cache_rev = rev
        return self._lines_cache

    def _theme(self) -> dict:
        try:
            from ui.theme_manager import ThemeManager
            return ThemeManager.instance().palette()
        except Exception:
            return {}

    def _line_color(self, line: str, pal: dict) -> QColor:
        s = line.strip()
        if not s:
            return QColor(0, 0, 0, 0)
        # 折叠提示 / 括号
        if s in ('{', '}', '[', ']', '},', '],', '},', '],'):
            return QColor(pal.get('TEXT_MUTED', '#98A2B3'))
        if _KEY_LINE.match(line) or (':' in s and s.startswith('"')):
            return QColor(pal.get('PRIMARY', '#547A9D'))
        if _BOOL_LINE.match(s):
            return QColor(pal.get('WARNING', '#B8893D'))
        if _NUM_LINE.match(s):
            return QColor(pal.get('CYAN', '#1A9FC4'))
        if _TAG_LINE.match(s):
            return QColor(pal.get('PRIMARY_ACTIVE', '#3E6588'))
        if _STR_LINE.match(s) or '"' in s:
            return QColor(pal.get('SUCCESS', '#3E7A5C'))
        return QColor(pal.get('TEXT', '#424A45'))

    def _viewport_fraction(self) -> tuple[float, float]:
        """返回视口在文档中的 [start, end) 比例 0~1。"""
        editor = self._editor
        sb = editor.verticalScrollBar()
        lo, hi = sb.minimum(), sb.maximum()
        page = max(1, sb.pageStep())
        if hi <= lo:
            return 0.0, 1.0
        span = hi - lo + page
        start = (sb.value() - lo) / span
        end = (sb.value() - lo + page) / span
        return max(0.0, min(1.0, start)), max(0.0, min(1.0, end))

    def _y_to_scroll(self, y: float):
        h = max(1, self.height())
        ratio = max(0.0, min(1.0, y / h))
        sb = self._editor.verticalScrollBar()
        lo, hi = sb.minimum(), sb.maximum()
        page = max(1, sb.pageStep())
        if hi <= lo:
            return
        # 让点击位置尽量落在视口中部
        span = hi - lo
        center = lo + ratio * (span + page) - page * 0.5
        sb.setValue(int(max(lo, min(hi, center))))

    def _scroll_by_viewport_top(self, y: float):
        """拖拽视口：y 为视口顶边目标。"""
        h = max(1, self.height())
        start, end = self._viewport_fraction()
        view_h = max(0.04, end - start)
        # 扣掉拖拽点在视口内的偏移
        top_ratio = (y - self._drag_offset_y) / h
        top_ratio = max(0.0, min(1.0 - view_h, top_ratio))
        sb = self._editor.verticalScrollBar()
        lo, hi = sb.minimum(), sb.maximum()
        page = max(1, sb.pageStep())
        if hi <= lo:
            return
        span = hi - lo + page
        value = lo + top_ratio * span
        sb.setValue(int(max(lo, min(hi, value))))

    # ── paint ───────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pal = self._theme()
        bg = QColor(pal.get('CODE_BG', pal.get('SURFACE_SOFT', '#F4F6F4')))
        border = QColor(pal.get('BORDER', '#D0D5DD'))
        view_fill = QColor(pal.get('PRIMARY', '#668C78'))
        view_fill.setAlpha(48)
        view_border = QColor(pal.get('PRIMARY', '#668C78'))
        view_border.setAlpha(160)
        cursor_c = QColor(pal.get('DANGER', '#B85C5C'))
        cursor_c.setAlpha(180)
        grip = QColor(pal.get('BORDER_STRONG', '#C8D2E2'))

        painter.fillRect(self.rect(), bg)
        # 左侧分隔 + 拖拽把手暗示
        painter.setPen(QPen(border, 1))
        painter.drawLine(0, 0, 0, self.height())
        for gy in (self.height() // 2 - 8, self.height() // 2 - 2, self.height() // 2 + 4):
            painter.setPen(QPen(grip, 1))
            painter.drawLine(2, gy, 2, gy + 4)

        lines = self._ensure_lines()
        n = max(1, len(lines))
        h = max(1, self.height())
        w = max(8, self.width() - 6)
        left = 5

        # 大文件用色条；小文件叠微型字符
        use_text = n <= 900 and h / n >= 1.6
        if use_text:
            font = QFont('Consolas', 1)
            font.setPixelSize(max(1, min(3, int(h / n))))
            painter.setFont(font)

        for i, line in enumerate(lines):
            y0 = int(i * h / n)
            y1 = int((i + 1) * h / n)
            if y1 <= y0:
                y1 = y0 + 1
            if y1 < event.rect().top() or y0 > event.rect().bottom():
                continue
            color = self._line_color(line, pal)
            if color.alpha() == 0:
                continue
            stripped = line.rstrip('\r\n')
            # 缩进 → 水平起点
            indent = 0
            for ch in stripped:
                if ch == ' ':
                    indent += 1
                elif ch == '\t':
                    indent += 2
                else:
                    break
            x0 = left + min(w // 3, indent // 2)
            content_len = max(1, len(stripped) - indent)
            bar_w = max(3, min(w - (x0 - left), int(content_len * 0.9) + 4))
            painter.fillRect(x0, y0, bar_w, max(1, y1 - y0), color)
            if use_text and (y1 - y0) >= 2:
                painter.setPen(color)
                snippet = stripped[indent: indent + 48]
                painter.drawText(x0, y0, w - (x0 - left), y1 - y0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, snippet)

        # 视口框
        start, end = self._viewport_fraction()
        vy0 = int(start * h)
        vy1 = max(vy0 + 4, int(end * h))
        view_rect = QRect(1, vy0, self.width() - 2, vy1 - vy0)
        painter.fillRect(view_rect, view_fill)
        painter.setPen(QPen(view_border, 1))
        painter.drawRect(view_rect.adjusted(0, 0, -1, -1))

        # 光标行
        try:
            cur_line = self._editor.textCursor().blockNumber()
            if n > 0:
                cy = int((cur_line + 0.5) * h / n)
                painter.setPen(QPen(cursor_c, 1))
                painter.drawLine(1, cy, self.width() - 2, cy)
        except Exception:
            pass

        painter.end()

    # ── mouse ───────────────────────────────────────────

    def _hit_resize(self, pos: QPoint) -> bool:
        return pos.x() <= _RESIZE_GRIP

    def _hit_viewport(self, y: float) -> bool:
        start, end = self._viewport_fraction()
        h = max(1, self.height())
        return start * h - 2 <= y <= end * h + 2

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position()
        if self._hit_resize(pos.toPoint()):
            self._resizing = True
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
            return
        y = pos.y()
        if self._hit_viewport(y):
            start, end = self._viewport_fraction()
            h = max(1, self.height())
            self._drag_offset_y = y - start * h
            self._dragging_view = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self._dragging_view = False
            self._y_to_scroll(y)
        event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._resizing:
            # 拖左边：向左变宽（editor 坐标下 glance 在右侧）
            # 鼠标相对 glance 左缘：x 减小 → 加宽
            delta = -int(event.position().x())
            if abs(delta) >= 1:
                self.set_glance_width(self._width + delta)
            event.accept()
            return
        if self._dragging_view:
            self._scroll_by_viewport_top(pos.y())
            event.accept()
            return
        if self._hit_resize(pos.toPoint()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._hit_viewport(pos.y()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging_view = False
        self._resizing = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        # 把滚轮交给编辑器
        self._editor.wheelEvent(event)
