# -*- coding: utf-8 -*-
"""全窗口启动虚化遮罩（StartupVeil）。

在主窗口展示到 WebEngine 首页就绪并完成全部 14 个本地面板预热期间，
以全窗口半透明毛玻璃虚化层覆盖主窗口，居中展示品牌棱镜加载动画与平滑进度条；
全部加载完成后以平滑淡出一次性呈现完整工作台，避免窗口组件逐块闪现或右上角小浮层打扰。

设计约束：
- 作为主窗口的独立无边框 Tool 子顶层窗口挂载（不设 WindowStaysOnTopHint，切走应用不遮挡其它程序），
  避免 Windows 下 QWebEngineView 原生子句柄与普通子 QWidget 的 Z 序穿透/闪烁；
- 监听宿主窗口 Move / Resize / Show / Hide / WindowStateChange / Close 事件实时贴合；
- 收到宿主首次渲染就绪回调时捕获并柔化模糊底层画面作为真实虚化底图（自动过滤黑帧）；
- 内置 14 秒兜底超时与 offscreen/sync-boot 静默跳过，绝不会锁死窗口。
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QWidget

DEFAULT_FAILSAFE_MS = 14000
ANIM_INTERVAL_MS = 28


def _brand_pixmap(size: int = 56, tint: str = '#6C58D9') -> QPixmap:
    try:
        from ui.icons import brand_pixmap
        for name in ('app', 'app_mark'):
            pix = brand_pixmap(name, size=size, tint=tint)
            if pix is not None and not pix.isNull():
                return pix
    except Exception:
        pass
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    return pix


def _palette() -> dict:
    try:
        from ui.theme_manager import ThemeManager
        return dict(ThemeManager.instance().palette())
    except Exception:
        return {}


def _qcolor(pal: dict, key: str, fallback: str) -> QColor:
    raw = pal.get(key) or fallback
    try:
        from ui.theme_manager import parse_color
        rgba = parse_color(raw)
        if rgba:
            return QColor(*rgba)
    except Exception:
        pass
    color = QColor(raw)
    return color if color.isValid() else QColor(fallback)


def _pixmap_is_usable(pix: QPixmap) -> bool:
    """过滤尚未首帧绘制的全黑/透明截图，避免虚化底图发黑。"""
    if pix.isNull() or pix.width() < 32 or pix.height() < 32:
        return False
    tiny = pix.scaled(16, 16, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    img = tiny.toImage()
    if img.isNull():
        return False
    bright = 0
    total = img.width() * img.height()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 20 and (c.red() + c.green() + c.blue()) > 120:
                bright += 1
    return bright >= total * 0.4


class StartupVeil(QWidget):
    """覆盖主窗口全屏区域的虚化启动遮罩 + 居中棱镜加载卡片。"""

    def __init__(
        self,
        host: QWidget,
        *,
        language: str = 'zh',
        failsafe_ms: int = DEFAULT_FAILSAFE_MS,
    ):
        super().__init__(
            host,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._host = host
        self._language = 'en' if str(language).lower().startswith('en') else 'zh'
        self._palette = _palette()
        self._title = 'PengToolsHub'
        try:
            from config import APP_NAME
            self._title = APP_NAME
        except Exception:
            pass
        self._subtitle = (
            '正在初始化工作台…' if self._language == 'zh' else 'Initializing workspace…'
        )
        self._target_progress = 4.0
        self._display_progress = 4.0
        self._started_at = time.monotonic()
        self._active = False
        self._revealing = False
        self._dismissed = False
        self._blurred_backdrop: Optional[QPixmap] = None
        self._fade_anim: Optional[QPropertyAnimation] = None

        primary_hex = self._palette.get('PRIMARY') or '#6C58D9'
        self._logo = _brand_pixmap(56, tint=primary_hex)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(ANIM_INTERVAL_MS)
        self._anim_timer.timeout.connect(self._on_tick)

        self._failsafe_timer = QTimer(self)
        self._failsafe_timer.setSingleShot(True)
        self._failsafe_timer.timeout.connect(self._on_failsafe)
        self._failsafe_ms = max(2000, int(failsafe_ms))

        if host is not None:
            host.installEventFilter(self)

    @property
    def is_active(self) -> bool:
        return self._active and not self._dismissed

    def _motion_enabled(self) -> bool:
        try:
            from ui.motion import motion_enabled
            return motion_enabled()
        except Exception:
            return True

    def sync_to_host(self) -> None:
        if self._host is None or self._dismissed:
            return
        if self._host.isMinimized() or not self._host.isVisible():
            if self.isVisible():
                self.hide()
            return
        top_left = self._host.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left, self._host.size())
        if rect.width() > 0 and rect.height() > 0:
            if self.geometry() != rect:
                self.setGeometry(rect)
            if self._active and not self.isVisible():
                self.show()
                self.raise_()
                self.repaint()

    def capture_blurred_backdrop(self) -> None:
        """捕获底层窗口画面并做双段降采样柔化，形成真实内容虚化背景。"""
        if self._host is None or self._dismissed or not self._host.isVisible():
            return
        try:
            raw = self._host.grab()
            if not _pixmap_is_usable(raw):
                return
            w = max(1, raw.width())
            h = max(1, raw.height())
            # 双级下采样再平滑放大，2ms 内获得自然均匀的高斯级虚化质感
            mid_w, mid_h = max(48, w // 8), max(32, h // 8)
            low_w, low_h = max(24, w // 18), max(16, h // 18)
            step1 = raw.scaled(
                mid_w, mid_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            step2 = step1.scaled(
                low_w, low_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._blurred_backdrop = step2.scaled(
                w, h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.update()
        except Exception:
            pass

    def begin(self, message: str = '', progress: float = 6.0) -> None:
        if self._dismissed:
            return
        self._active = True
        self._revealing = False
        self._started_at = time.monotonic()
        if message:
            self._subtitle = message
        self._target_progress = max(self._target_progress, min(100.0, float(progress)))
        self.setWindowOpacity(1.0)
        self.sync_to_host()
        if self._host is not None and self._host.isVisible() and not self._host.isMinimized():
            self.show()
            self.raise_()
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        self._failsafe_timer.start(self._failsafe_ms)
        self.update()

    def set_progress(self, progress: float, message: Optional[str] = None) -> None:
        if self._dismissed:
            return
        val = max(0.0, min(100.0, float(progress)))
        if val > self._target_progress:
            self._target_progress = val
        if message:
            self._subtitle = message
        if self.isVisible():
            self.update()

    def complete_and_reveal(self, message: str = '') -> None:
        """全部模块加载完成后置 100% 并平滑淡出，一次性展示完整界面。"""
        if self._dismissed or self._revealing:
            return
        self._revealing = True
        self._target_progress = 100.0
        self._display_progress = 100.0
        if message:
            self._subtitle = message
        elif self._language == 'zh':
            self._subtitle = '全部模块已就绪'
        else:
            self._subtitle = 'Workspace ready'
        self._failsafe_timer.stop()

        if not self.isVisible() or not self._motion_enabled():
            self.dismiss_now()
            return

        self.update()
        # 短暂停留 120ms 让用户看清 100% 完成状态，再 220ms 柔和淡出
        QTimer.singleShot(120, self._start_fade_out)

    def _start_fade_out(self) -> None:
        if self._dismissed:
            return
        if not self.isVisible() or not self._motion_enabled():
            self.dismiss_now()
            return
        anim = QPropertyAnimation(self, b'windowOpacity', self)
        anim.setDuration(220)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self.dismiss_now)
        self._fade_anim = anim
        anim.start()

    def _on_failsafe(self) -> None:
        if not self._dismissed:
            self.complete_and_reveal()

    def dismiss_now(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self._active = False
        self._revealing = False
        self._failsafe_timer.stop()
        if self._anim_timer.isActive():
            self._anim_timer.stop()
        if self._fade_anim is not None:
            try:
                self._fade_anim.stop()
            except Exception:
                pass
            self._fade_anim = None
        if self._host is not None:
            try:
                self._host.removeEventFilter(self)
            except Exception:
                pass
        self._blurred_backdrop = None
        self.hide()
        self.close()
        self.deleteLater()

    def _on_tick(self) -> None:
        if self._dismissed:
            self._anim_timer.stop()
            return
        # 平滑插值进度条，消除模块预热步进之间的阶跃感
        diff = self._target_progress - self._display_progress
        if abs(diff) > 0.15:
            self._display_progress += diff * 0.22
        else:
            self._display_progress = self._target_progress
        if self.isVisible():
            self.update()

    def eventFilter(self, watched, event):
        if watched is self._host and not self._dismissed:
            etype = event.type()
            if etype in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowStateChange):
                self.sync_to_host()
            elif etype == QEvent.Type.Hide:
                if self.isVisible():
                    self.hide()
            elif etype == QEvent.Type.Close:
                self.dismiss_now()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())
        if w <= 2 or h <= 2:
            return

        pal = self._palette
        primary = _qcolor(pal, 'PRIMARY', '#6C58D9')
        primary_light = QColor('#8F7CEB')
        text_strong = _qcolor(pal, 'TEXT_STRONG', '#262438')
        text_muted = _qcolor(pal, 'TEXT_MUTED', '#615D73')
        border_col = _qcolor(pal, 'BORDER', '#D9D3EB')
        track_col = _qcolor(pal, 'LOADING_TRACK', '#E7E2F5')

        is_max = bool(self._host is not None and self._host.isMaximized())
        radius = 0.0 if is_max else 12.0

        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0.0, 0.0, w, h), radius, radius)
        painter.setClipPath(clip_path)

        # 1. 底层虚化背景：优先绘制真实柔化画面 + 高通透毛玻璃雾层；首帧前使用柔紫极光磨砂幕
        if self._blurred_backdrop is not None and not self._blurred_backdrop.isNull():
            painter.drawPixmap(QRect(0, 0, int(w), int(h)), self._blurred_backdrop)
            veil_grad = QLinearGradient(0.0, 0.0, w, h)
            veil_grad.setColorAt(0.0, QColor(245, 242, 253, 206))
            veil_grad.setColorAt(0.5, QColor(249, 248, 255, 214))
            veil_grad.setColorAt(1.0, QColor(240, 236, 252, 212))
            painter.fillRect(QRectF(0.0, 0.0, w, h), veil_grad)
        else:
            veil_grad = QLinearGradient(0.0, 0.0, w, h)
            veil_grad.setColorAt(0.0, QColor(243, 239, 252, 244))
            veil_grad.setColorAt(0.55, QColor(248, 247, 254, 246))
            veil_grad.setColorAt(1.0, QColor(238, 234, 250, 244))
            painter.fillRect(QRectF(0.0, 0.0, w, h), veil_grad)

        # 2. 柔和的极光环境光晕（Prism calm 紫 + 浅天蓝，轻盈不刺眼）
        elapsed = time.monotonic() - self._started_at
        drift = math.sin(elapsed * 0.9) * 18.0 if self._motion_enabled() else 0.0

        bloom1 = QRadialGradient(w * 0.34 + drift, h * 0.36, max(w, h) * 0.38)
        bloom1.setColorAt(0.0, QColor(108, 88, 217, 36))
        bloom1.setColorAt(1.0, QColor(108, 88, 217, 0))
        painter.fillRect(QRectF(0.0, 0.0, w, h), bloom1)

        bloom2 = QRadialGradient(w * 0.68 - drift, h * 0.64, max(w, h) * 0.34)
        bloom2.setColorAt(0.0, QColor(14, 165, 233, 24))
        bloom2.setColorAt(1.0, QColor(14, 165, 233, 0))
        painter.fillRect(QRectF(0.0, 0.0, w, h), bloom2)

        # 3. 居中毛玻璃加载卡片
        card_w = min(400.0, w - 48.0)
        card_h = 224.0
        card_x = (w - card_w) / 2.0
        card_y = (h - card_h) / 2.0
        card_rect = QRectF(card_x, card_y, card_w, card_h)

        # 柔和多层弥散阴影（8 级微步渐变，无可见阶梯边）
        for spread, y_off, alpha in (
            (22.0, 8.0, 3),
            (18.0, 7.0, 4),
            (14.0, 6.0, 4),
            (11.0, 5.0, 5),
            (8.0, 4.0, 5),
            (6.0, 3.0, 6),
            (4.0, 2.0, 6),
            (2.0, 1.0, 7),
        ):
            shadow_rect = card_rect.adjusted(-spread, -spread + y_off, spread, spread + y_off)
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(shadow_rect, 20.0 + spread * 0.45, 20.0 + spread * 0.45)
            painter.fillPath(shadow_path, QColor(64, 52, 128, alpha))

        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 20.0, 20.0)
        card_bg = QLinearGradient(card_x, card_y, card_x, card_y + card_h)
        card_bg.setColorAt(0.0, QColor(255, 255, 255, 246))
        card_bg.setColorAt(1.0, QColor(251, 250, 255, 238))
        painter.fillPath(card_path, card_bg)

        pen = QPen(QColor(border_col.red(), border_col.green(), border_col.blue(), 210), 1.0)
        painter.setPen(pen)
        painter.drawPath(card_path)

        # 4. 品牌棱镜图标 + 顺滑双弧旋转光环
        cx = card_x + card_w / 2.0
        ring_cy = card_y + 66.0
        ring_r = 34.0
        ring_rect = QRectF(cx - ring_r, ring_cy - ring_r, ring_r * 2.0, ring_r * 2.0)

        # 柔和光环底座轨道
        painter.setPen(QPen(QColor(primary.red(), primary.green(), primary.blue(), 28), 3.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring_rect)

        angle_deg = ((elapsed * 320.0) % 360.0) if self._motion_enabled() else 45.0
        arc_pen1 = QPen(primary, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen1)
        painter.drawArc(ring_rect, int(-angle_deg * 16), int(-110 * 16))

        arc_pen2 = QPen(QColor(primary_light.red(), primary_light.green(), primary_light.blue(), 145), 2.5,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen2)
        painter.drawArc(ring_rect, int((-angle_deg + 180.0) * 16), int(-65 * 16))

        if not self._logo.isNull():
            logo_size = 40
            painter.drawPixmap(
                QRect(int(cx - logo_size / 2), int(ring_cy - logo_size / 2), logo_size, logo_size),
                self._logo,
            )

        # 5. 应用名称
        title_font = QFont('Microsoft YaHei UI', 13)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(text_strong)
        painter.drawText(
            QRectF(card_x + 24.0, card_y + 112.0, card_w - 48.0, 26.0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

        # 6. 动态状态文案
        sub_font = QFont('Microsoft YaHei UI', 9)
        painter.setFont(sub_font)
        painter.setPen(text_muted)
        fm = painter.fontMetrics()
        elided = fm.elidedText(self._subtitle, Qt.TextElideMode.ElideRight, int(card_w - 64.0))
        painter.drawText(
            QRectF(card_x + 32.0, card_y + 142.0, card_w - 64.0, 20.0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )

        # 7. 平滑进度轨道 + 百分比
        track_w = card_w - 84.0
        track_h = 6.0
        track_x = card_x + 42.0
        track_y = card_y + 176.0
        track_rect = QRectF(track_x, track_y, track_w, track_h)

        track_path = QPainterPath()
        track_path.addRoundedRect(track_rect, 3.0, 3.0)
        painter.fillPath(track_path, track_col)

        pct = max(0.0, min(100.0, self._display_progress))
        fill_w = max(10.0, track_w * (pct / 100.0)) if pct > 0 else 0.0
        if fill_w > 0:
            fill_rect = QRectF(track_x, track_y, min(track_w, fill_w), track_h)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, 3.0, 3.0)
            bar_grad = QLinearGradient(track_x, track_y, track_x + track_w, track_y)
            bar_grad.setColorAt(0.0, primary)
            bar_grad.setColorAt(1.0, primary_light)
            painter.fillPath(fill_path, bar_grad)

            # 轻量流动光斑（1.5s 周期）
            if self._motion_enabled() and pct < 100.0:
                phase = (elapsed % 1.5) / 1.5
                shimmer_x = track_x + (fill_w + 48.0) * phase - 48.0
                shimmer_grad = QLinearGradient(shimmer_x, track_y, shimmer_x + 48.0, track_y)
                shimmer_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
                shimmer_grad.setColorAt(0.5, QColor(255, 255, 255, 115))
                shimmer_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.save()
                painter.setClipPath(fill_path)
                painter.fillRect(fill_rect, shimmer_grad)
                painter.restore()

        pct_font = QFont('Segoe UI', 9)
        pct_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(pct_font)
        painter.setPen(primary)
        painter.drawText(
            QRectF(track_x, track_y + 10.0, track_w, 18.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f'{int(round(pct))}%',
        )

        # 外边框微妙高光收边
        if not is_max:
            painter.setClipping(False)
            outer_pen = QPen(QColor(border_col.red(), border_col.green(), border_col.blue(), 190), 1.0)
            painter.setPen(outer_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)
