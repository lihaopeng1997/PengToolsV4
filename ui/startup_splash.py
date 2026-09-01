# -*- coding: utf-8 -*-
"""启动闪屏：尽早出画面，自适应 Light / Dark 主题语义色，主窗口就绪后 finish。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen


def _brand_pixmap(size: int = 96) -> QPixmap:
    try:
        from ui.icons import brand_pixmap
        pix = brand_pixmap(size)
        if pix is not None and not pix.isNull():
            return pix
    except Exception:
        pass
    # 退化：纯色块
    pix = QPixmap(size, size)
    pix.fill(QColor('#4058C8'))
    return pix


def _resolve_palette() -> dict:
    try:
        from config import load_settings
        from ui.theme_manager import ThemeManager, DEFAULT_THEME_ID
        settings = load_settings()
        theme_id = settings.get('ui_theme', DEFAULT_THEME_ID)
        manager = ThemeManager.instance()
        theme = manager._themes.get(theme_id) or manager._themes.get(DEFAULT_THEME_ID)
        if theme and hasattr(theme, 'palette'):
            return dict(theme.palette)
    except Exception:
        pass
    return {}


import time

DEFAULT_SPLASH_DELAY_MS = 300


class StartupSplash(QSplashScreen):
    """轻量闪屏：自适应 Light / Dark 主题语义色，延迟展示，慢启动提供反馈，快启动静默。"""

    def __init__(self, app: QApplication | None = None, *, delay_ms: int = DEFAULT_SPLASH_DELAY_MS):
        self._delay_ms = max(0, int(delay_ms))
        self._start_time = time.monotonic()
        self._is_visible = False
        self._palette = _resolve_palette()
        bg_hex = self._palette.get('SURFACE') or self._palette.get('APP_BG') or '#F5F7FB'
        base = QPixmap(420, 240)
        base.fill(QColor(bg_hex))
        super().__init__(base, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._message = '正在启动…'
        self._title = 'PengToolsHub'
        try:
            from config import APP_NAME
            self._title = APP_NAME
        except Exception:
            pass
        self._logo = _brand_pixmap(72)
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())

    @property
    def is_visible_to_user(self) -> bool:
        return self._is_visible and not self.isHidden()

    def check_delayed_show(self) -> bool:
        """根据已耗时检查是否达到展示阈值（>=delay_ms）。达到阈值才展示闪屏。"""
        if self._is_visible:
            return True
        elapsed_ms = (time.monotonic() - self._start_time) * 1000.0
        if elapsed_ms >= self._delay_ms:
            self._is_visible = True
            self.show()
            self.raise_()
            self.repaint()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
            return True
        return False

    def drawContents(self, painter: QPainter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self._palette
        bg_color = QColor(pal.get('SURFACE') or pal.get('APP_BG') or '#F5F7FB')
        text_color = QColor(pal.get('TEXT_STRONG') or '#1E2A44')
        muted_color = QColor(pal.get('TEXT_MUTED') or pal.get('TEXT_SECONDARY') or '#5A6A86')
        border_color = QColor(pal.get('BORDER') or '#E2E8F0')

        painter.fillRect(self.rect(), bg_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # 品牌图
        logo_x = (self.width() - self._logo.width()) // 2
        painter.drawPixmap(logo_x, 36, self._logo)
        # 标题
        painter.setPen(text_color)
        title_font = QFont('Microsoft YaHei UI', 14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            0, 120, self.width(), 32,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._title,
        )
        # 状态
        painter.setPen(muted_color)
        painter.setFont(QFont('Microsoft YaHei UI', 10))
        painter.drawText(
            24, 170, self.width() - 48, 40,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            self._message,
        )

    def show_status(self, text: str):
        """更新状态文字。若尚未达到展示阈值则仅记录文字，不强制弹出与重绘。"""
        self._message = text or '正在启动…'
        if self.check_delayed_show():
            self.showMessage('')
            self.repaint()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()

    def finish(self, window):
        if self._is_visible:
            super().finish(window)
        else:
            self.close()
            self.hide()
