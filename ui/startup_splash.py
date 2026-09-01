# -*- coding: utf-8 -*-
"""启动闪屏：现代化品牌浮层，自适应 Light / Dark 主题语义色，慢启动平滑反馈，快启动静默。"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget


DEFAULT_SPLASH_DELAY_MS = 300
MIN_VISIBLE_MS = 550


def _brand_pixmap(size: int = 56, tint: str = '#5B5FC7') -> QPixmap:
    try:
        from ui.icons import brand_pixmap
        pix = brand_pixmap('app', size=size, tint=tint)
        if pix is not None and not pix.isNull():
            return pix
        pix = brand_pixmap('app_mark', size=size, tint=tint)
        if pix is not None and not pix.isNull():
            return pix
    except Exception:
        pass
    # 退化：纯色块
    pix = QPixmap(size, size)
    pix.fill(QColor(tint))
    return pix


def _resolve_palette() -> dict:
    try:
        from config import load_settings
        from ui.theme_manager import THEMES, DEFAULT_THEME_ID, resolve_theme_id
        settings = load_settings()
        theme_id = resolve_theme_id(settings.get('ui_theme', DEFAULT_THEME_ID))
        theme_tokens = THEMES.get(theme_id) or THEMES.get(DEFAULT_THEME_ID)
        if theme_tokens:
            return dict(theme_tokens)
    except Exception:
        pass
    return {
        'SURFACE': '#FFFFFF',
        'ELEVATED_SURFACE': '#FFFFFF',
        'APP_BG': '#EEF0F6',
        'TEXT_STRONG': '#1B1E2A',
        'TEXT_MUTED': '#6E7486',
        'BORDER': '#DFE2EC',
        'GLASS_BORDER': 'rgba(221, 218, 210, 200)',
        'PRIMARY': '#5B5FC7',
        'PRIMARY_SOFT': '#ECEEF7',
        'LOADING_TRACK': '#E2E8F0',
        'SHADOW': 'rgba(27, 30, 42, 28)',
    }


class StartupSplash(QWidget):
    """现代圆角品牌启动卡片：
    - 延迟展示（>=300ms）：快启动完全静默无感知。
    - 最短展示时间（~550ms）：慢启动展示后平滑过渡，避免瞬间闪退。
    - 纯语义色主题自适应（calm / clear / warm / black）。
    - 低 CPU 占用轻量 loading track 动画。
    """

    def __init__(
        self,
        app: Optional[QApplication] = None,
        *,
        delay_ms: int = DEFAULT_SPLASH_DELAY_MS,
        min_visible_ms: int = MIN_VISIBLE_MS,
    ):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(480, 280)

        self._delay_ms = max(0, int(delay_ms))
        self._min_visible_ms = max(0, int(min_visible_ms))
        self._start_time = time.monotonic()
        self._visible_at = 0.0
        self._is_visible = False
        self._finish_requested = False
        self._is_finished = False

        self._palette = _resolve_palette()
        self._title = 'PengToolsHub'
        try:
            from config import APP_NAME
            self._title = APP_NAME
        except Exception:
            pass
        self._subtitle = 'Developer & Ops Workbench'
        self._message = '正在准备工作台…'

        primary_color = self._palette.get('PRIMARY') or '#5B5FC7'
        self._logo = _brand_pixmap(56, tint=primary_color)

        # 动效状态机
        self._anim_progress = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(35)  # ~28 FPS
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self._finish_timer: Optional[QTimer] = None

        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())

    @property
    def is_visible_to_user(self) -> bool:
        return self._is_visible and not self.isHidden()

    def _on_anim_tick(self):
        self._anim_progress = (self._anim_progress + 0.02) % 1.0
        if self._is_visible and self.isVisible():
            self.update()

    def check_delayed_show(self) -> bool:
        """根据已耗时检查是否达到展示阈值（>=delay_ms）。达到阈值才展示闪屏。"""
        if self._is_finished or self._finish_requested:
            return False
        if self._is_visible:
            return True
        elapsed_ms = (time.monotonic() - self._start_time) * 1000.0
        if elapsed_ms >= self._delay_ms:
            self._is_visible = True
            self._visible_at = time.monotonic()
            self.show()
            self.raise_()
            self._anim_timer.start()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
            return True
        return False

    def show_status(self, text: str):
        """更新状态文字。若尚未达到展示阈值则仅记录文字，不强制弹出与重绘。"""
        self._message = text or '正在准备工作台…'
        if self.check_delayed_show():
            self.update()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()

    def showMessage(self, message: str, alignment: int = 0, color: QColor | None = None):  # noqa: N802
        """兼容 QSplashScreen 接口。"""
        self.show_status(message)

    def finish(self, window=None):
        """主窗口就绪后调用：
        - 从未展示（快启动）：立即关闭，0延迟。
        - 已经展示且可见时间 >= min_visible_ms：立即关闭。
        - 已经展示但可见时间 < min_visible_ms：非阻塞单次 QTimer 延时关闭。
        """
        if self._is_finished:
            return
        self._finish_requested = True
        if not self._is_visible:
            self._do_finish()
            return

        visible_elapsed_ms = (time.monotonic() - self._visible_at) * 1000.0
        remaining_ms = int(self._min_visible_ms - visible_elapsed_ms)
        if remaining_ms <= 0:
            self._do_finish()
        else:
            if self._finish_timer is None:
                self._finish_timer = QTimer(self)
                self._finish_timer.setSingleShot(True)
                self._finish_timer.timeout.connect(self._do_finish)
                self._finish_timer.start(remaining_ms)

    def _do_finish(self):
        if self._is_finished:
            return
        self._is_finished = True
        if self._anim_timer.isActive():
            self._anim_timer.stop()
        if self._finish_timer is not None and self._finish_timer.isActive():
            self._finish_timer.stop()
        self.hide()
        self.close()
        self.deleteLater()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        if self._anim_timer.isActive():
            self._anim_timer.stop()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._is_finished and not self._anim_timer.isActive():
            self._anim_timer.start()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        pal = self._palette
        card_bg = QColor(pal.get('ELEVATED_SURFACE') or pal.get('SURFACE') or '#FFFFFF')
        text_strong = QColor(pal.get('TEXT_STRONG') or '#1B1E2A')
        text_muted = QColor(pal.get('TEXT_MUTED') or '#6E7486')
        border_color = QColor(pal.get('GLASS_BORDER') or pal.get('BORDER') or '#DFE2EC')
        primary_color = QColor(pal.get('PRIMARY') or '#5B5FC7')
        track_color = QColor(pal.get('LOADING_TRACK') or pal.get('SURFACE_TECH') or '#E2E8F0')

        # 1. 浮层卡片区域
        card_rect = QRectF(10.0, 10.0, float(self.width() - 20), float(self.height() - 20))
        radius = 20.0

        # 2. 阴影层（克制柔和分层）
        shadow_base = QColor(pal.get('SHADOW') or 'rgba(0, 0, 0, 24)')
        if shadow_base.alpha() > 40:
            shadow_base.setAlpha(36)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in (3, 2, 1):
            s_color = QColor(shadow_base)
            s_color.setAlpha(max(4, shadow_base.alpha() // (i + 1)))
            painter.setBrush(QBrush(s_color))
            painter.drawRoundedRect(card_rect.adjusted(-i * 1.5, -i * 1.0 + 2.0, i * 1.5, i * 2.0 + 2.0), radius + i, radius + i)

        # 3. 卡片背景与边框
        painter.setBrush(QBrush(card_bg))
        painter.setPen(QPen(border_color, 1.0))
        painter.drawRoundedRect(card_rect, radius, radius)

        # 4. 品牌 Logo
        logo_w = self._logo.width()
        logo_h = self._logo.height()
        logo_x = (self.width() - logo_w) // 2
        logo_y = 36
        painter.drawPixmap(logo_x, logo_y, self._logo)

        # 5. 主标题
        painter.setPen(text_strong)
        title_font = QFont('Microsoft YaHei UI', 14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            QRect(0, 102, self.width(), 28),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._title,
        )

        # 6. 副文案
        painter.setPen(text_muted)
        sub_font = QFont('Microsoft YaHei UI', 9)
        painter.setFont(sub_font)
        painter.drawText(
            QRect(0, 132, self.width(), 20),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._subtitle,
        )

        # 7. Loading 动效条 (220px track, 60px active indicator)
        track_w = 220.0
        track_h = 3.0
        track_x = (self.width() - track_w) / 2.0
        track_y = 176.0
        track_rect = QRectF(track_x, track_y, track_w, track_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(track_rect, 1.5, 1.5)

        # 平滑往复缓动计算
        indicator_w = 64.0
        max_travel = track_w - indicator_w
        # sine-eased back and forth
        ease = (math.sin(self._anim_progress * 2.0 * math.pi - math.pi / 2.0) + 1.0) / 2.0
        indicator_x = track_x + ease * max_travel
        indicator_rect = QRectF(indicator_x, track_y, indicator_w, track_h)

        painter.setBrush(QBrush(primary_color))
        painter.drawRoundedRect(indicator_rect, 1.5, 1.5)

        # 8. 状态文本
        painter.setPen(text_muted)
        status_font = QFont('Microsoft YaHei UI', 9)
        painter.setFont(status_font)
        painter.drawText(
            QRect(20, 196, self.width() - 40, 24),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._message,
        )
