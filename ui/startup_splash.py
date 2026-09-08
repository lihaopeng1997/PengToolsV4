# -*- coding: utf-8 -*-
"""启动闪屏：现代圆角品牌启动卡片。

晴空棱镜 V2.0 规范 (LD-01):
- 尺寸：480×280px，卡片圆角 20px；
- 品牌图标：64×64px 位于 (208, 48)；
- 品牌外圈：900ms 线性循环旋转（外围两段细弧缓转）；
- 主标题：居中 y=132，高 28px；
- 说明副文案：居中 y=172，高 20px；
- 进度轨道：坐标 (48, 216, 384, 4)；
- 往返光带：无真实进度时，活动光带片段宽 96px 水平往返 1.6s 周期；
- 文案与反馈：沿用启动阶段真实文字，不显示“100%”直到已有完成；
- 契约：300ms 延迟展示（快启动静默），550ms 最少可视驻留，非阻塞 finish。
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPixmap, QPainterPath
from PyQt6.QtWidgets import QApplication, QWidget


DEFAULT_SPLASH_DELAY_MS = 300
MIN_VISIBLE_MS = 550


def _brand_pixmap(size: int = 64, tint: str = '#6C58D9') -> QPixmap:
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
        'TEXT_STRONG': '#262438',
        'TEXT_MUTED': '#615D73',
        'BORDER': '#E6E2F0',
        'GLASS_BORDER': 'rgba(221, 218, 210, 200)',
        'PRIMARY': '#6C58D9',
        'PRIMARY_SOFT': '#EEE9FF',
        'LOADING_TRACK': '#E6EBF5',
        'SHADOW': 'rgba(38, 36, 56, 45)',
    }


class StartupSplash(QWidget):
    """现代圆角品牌启动卡片 (LD-01 晴空棱镜规范)。"""

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

        primary_color = self._palette.get('PRIMARY') or '#6C58D9'
        self._logo = _brand_pixmap(64, tint=primary_color)

        # 动效计时与定时器 (~30 FPS)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        # 延迟展示 timer
        self._show_timer: Optional[QTimer] = None
        if self._delay_ms > 0:
            self._show_timer = QTimer(self)
            self._show_timer.setSingleShot(True)
            self._show_timer.timeout.connect(self._on_show_timer_timeout)
            self._show_timer.start(self._delay_ms)
        else:
            self.check_delayed_show()

        self._finish_timer: Optional[QTimer] = None

        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())

    @property
    def is_visible_to_user(self) -> bool:
        return self._is_visible and not self.isHidden()

    def _is_motion_enabled(self) -> bool:
        try:
            from ui.motion import motion_enabled
            return motion_enabled()
        except Exception:
            return True

    def _on_anim_tick(self):
        if self._is_visible and self.isVisible():
            self.update()

    def _on_show_timer_timeout(self):
        if not self._finish_requested and not self._is_finished and not self._is_visible:
            self.check_delayed_show()

    def check_delayed_show(self) -> bool:
        """根据已耗时检查是否达到展示阈值（>=delay_ms）。"""
        if self._is_finished or self._finish_requested:
            if self._show_timer is not None and self._show_timer.isActive():
                self._show_timer.stop()
            return False
        if self._is_visible:
            if self._show_timer is not None and self._show_timer.isActive():
                self._show_timer.stop()
            return True
        elapsed_ms = (time.monotonic() - self._start_time) * 1000.0
        if elapsed_ms >= self._delay_ms:
            self._is_visible = True
            self._visible_at = time.monotonic()
            if self._show_timer is not None and self._show_timer.isActive():
                self._show_timer.stop()
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
        if self._show_timer is not None and self._show_timer.isActive():
            self._show_timer.stop()
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
        if self._show_timer is not None and self._show_timer.isActive():
            self._show_timer.stop()
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
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        pal = self._palette
        card_bg = QColor(pal.get('ELEVATED_SURFACE') or pal.get('SURFACE') or '#FFFFFF')
        text_strong = QColor(pal.get('TEXT_STRONG') or '#262438')
        text_muted = QColor(pal.get('TEXT_MUTED') or '#615D73')
        border_color = QColor(pal.get('GLASS_BORDER') or pal.get('BORDER') or '#E6E2F0')
        primary_color = QColor(pal.get('PRIMARY') or '#6C58D9')
        track_color = QColor(pal.get('LOADING_TRACK') or pal.get('SURFACE_TECH') or '#E6EBF5')

        # 1. 浮层卡片区域：480x280，圆角 20px
        card_rect = QRectF(10.0, 10.0, float(self.width() - 20), float(self.height() - 20))
        radius = 20.0

        # 2. 柔和阴影层
        shadow_base = QColor(pal.get('SHADOW') or 'rgba(38, 36, 56, 45)')
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

        # 4. 品牌 Logo：64x64，精确位于 (208, 48)
        logo_x = 208
        logo_y = 48
        logo_w = 64
        logo_h = 64
        painter.drawPixmap(logo_x, logo_y, self._logo)

        now = time.monotonic()
        elapsed = now - self._start_time if self._start_time > 0 else 0.0
        motion_on = self._is_motion_enabled()

        # 4.1 品牌外圈：900ms 周期线性旋转细弧（两段对称细弧，半径 42px）
        cx = float(logo_x + logo_w / 2.0)  # 240.0
        cy = float(logo_y + logo_h / 2.0)  # 80.0
        ring_r = 42.0
        ring_rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2.0, ring_r * 2.0)

        # 900ms 周期线性旋转
        angle_deg = (elapsed / 0.9 * 360.0) % 360.0 if motion_on else 0.0
        arc_pen = QPen(primary_color, 1.6)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 绘制两段对称的 65 度弧线
        painter.drawArc(ring_rect, int((angle_deg) * 16), int(65 * 16))
        painter.drawArc(ring_rect, int((angle_deg + 180) * 16), int(65 * 16))

        # 5. 主标题：居中 y=132，高 28px
        painter.setPen(text_strong)
        title_font = QFont('Microsoft YaHei UI', 14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            QRect(0, 132, self.width(), 28),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._title,
        )

        # 6. 副文案：居中 y=172，高 20px
        painter.setPen(text_muted)
        sub_font = QFont('Microsoft YaHei UI', 9)
        painter.setFont(sub_font)
        painter.drawText(
            QRect(0, 172, self.width(), 20),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._subtitle,
        )

        # 7. 进度轨道：精确坐标 (48, 216, 384, 4)
        track_x = 48.0
        track_y = 216.0
        track_w = 384.0
        track_h = 4.0
        track_rect = QRectF(track_x, track_y, track_w, track_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(track_rect, 2.0, 2.0)

        # 7.1 往返活动光带：片段宽 96px，1.6s 周期水平往返
        indicator_w = 96.0
        max_travel = track_w - indicator_w  # 288.0

        if motion_on:
            ease = (math.sin((elapsed / 1.6) * 2.0 * math.pi - math.pi / 2.0) + 1.0) / 2.0
            indicator_x = track_x + ease * max_travel
        else:
            indicator_x = track_x + max_travel * 0.5

        indicator_rect = QRectF(indicator_x, track_y, indicator_w, track_h)

        # 限制在轨道圆角范围内绘制
        path = QPainterPath()
        path.addRoundedRect(track_rect, 2.0, 2.0)
        painter.save()
        painter.setClipPath(path)
        painter.setBrush(QBrush(primary_color))
        painter.drawRoundedRect(indicator_rect, 2.0, 2.0)
        painter.restore()

        # 8. 状态文本：y=228，高 20px，展示真实启动步骤文字，不显示“100%”直到完成
        painter.setPen(text_muted)
        status_font = QFont('Microsoft YaHei UI', 8)
        painter.setFont(status_font)
        painter.drawText(
            QRect(20, 226, self.width() - 40, 20),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._message,
        )
