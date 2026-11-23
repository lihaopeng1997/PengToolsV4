# -*- coding: utf-8 -*-
"""全窗口启动虚化遮罩（StartupVeil）——趣味“棱镜打工崽”矢量动效版。

在主窗口展示到 WebEngine 首页就绪并完成全部 14 个本地面板预热期间，
以全窗口半透明毛玻璃虚化层覆盖主窗口，居中展示趣味矢量动画角色
（喝冰美式起床 → 狂敲键盘冒代码火花 → 冲刺拧螺丝 → 开心收工 ^ ^）
搭配开发/运维向趣味文案、真实预热进度与平滑进度条；全部加载完成后柔和淡出，
一次性完整呈现工作台。

设计约束：
- 无边框 Tool 叠加窗口，避免 Windows 下 QWebEngineView 原生句柄穿透，不抢占其他应用置顶；
- 监听宿主窗口 Move / Resize / Show / Hide / WindowStateChange / Close 事件实时贴合；
- Web 壳首次绘制就绪后自动抓取底层画面做双级柔化模糊作为真实虚化背景；
- 内置 14 秒兜底超时与 offscreen/sync-boot 静默跳过，绝不会锁死窗口。
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

DEFAULT_FAILSAFE_MS = 14000
ANIM_INTERVAL_MS = 28

_FUNNY_QUIPS_ZH = {
    'wake': (
        '正在给程序灌第一杯冰美式…',
        '正在叫醒昨晚还在加班的进程…',
    ),
    'shell': (
        '正在劝说浏览器内核起床上班…',
        '正在给首页贴膜顺便擦擦灰…',
    ),
    'warm1': (
        '正在把 SQL 和工具排排坐好…',
        '键盘敲冒烟了，正在搬运工作台…',
        '正在悄悄整理昨晚的临时脚本…',
    ),
    'warm2': (
        '正在给数据库通道抹点润滑油…',
        '别急别急，小马达已经踩到油门底了…',
        '正在排查今天有没有线上背锅风险…',
    ),
    'sprint': (
        '最后拧紧一颗螺丝，马上开门迎客…',
        '领带已系好，准备闪亮登场…',
    ),
    'done': (
        '全部就绪！今天也要优雅下班',
    ),
}

_FUNNY_QUIPS_EN = {
    'wake': ('Pouring the first iced coffee…', 'Waking up sleepy worker processes…'),
    'shell': ('Persuading Chromium to get out of bed…', 'Polishing the dashboard glass…'),
    'warm1': ('Lining up SQL & dev tools neatly…', 'Typing furiously to warm up panels…'),
    'warm2': ('Oiling database & SSH pipelines…', 'Almost there, turbo mode engaged…'),
    'sprint': ('Tightening the very last bolt…',),
    'done': ('All set! Have a smooth workday',),
}


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
    """覆盖主窗口全屏区域的虚化启动遮罩 + 居中趣味棱镜打工崽加载卡片。"""

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

    def _pick_funny_quip(self, pct: float, elapsed: float) -> str:
        bank = _FUNNY_QUIPS_ZH if self._language == 'zh' else _FUNNY_QUIPS_EN
        if pct >= 99.5 or self._revealing:
            stage = 'done'
        elif pct < 16.0:
            stage = 'wake'
        elif pct < 28.0:
            stage = 'shell'
        elif pct < 56.0:
            stage = 'warm1'
        elif pct < 86.0:
            stage = 'warm2'
        else:
            stage = 'sprint'
        lines = bank[stage]
        idx = int(elapsed / 1.55) % len(lines)
        return lines[idx]

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
        """全部模块加载完成后置 100% 展示笑脸，短暂驻留后平滑淡出。"""
        if self._dismissed or self._revealing:
            return
        self._revealing = True
        self._target_progress = 100.0
        self._display_progress = 100.0
        if message:
            self._subtitle = message
        elif self._language == 'zh':
            self._subtitle = '全部 14 个模块已就绪'
        else:
            self._subtitle = 'All modules ready'
        self._failsafe_timer.stop()

        if not self.isVisible() or not self._motion_enabled():
            self.dismiss_now()
            return

        self.update()
        QTimer.singleShot(260, self._start_fade_out)

    def _start_fade_out(self) -> None:
        if self._dismissed:
            return
        if not self.isVisible() or not self._motion_enabled():
            self.dismiss_now()
            return
        anim = QPropertyAnimation(self, b'windowOpacity', self)
        anim.setDuration(230)
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

    def _draw_mascot_stage(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        pct: float,
        elapsed: float,
        primary: QColor,
        motion: bool,
    ) -> None:
        """绘制趣味“棱镜打工崽”动画场景：喝咖啡起床 → 狂敲键盘冒火花 → 冲刺 → 开心笑脸。"""
        is_done = pct >= 99.5 or self._revealing
        is_wake = pct < 24.0 and not is_done
        is_sprint = 75.0 <= pct < 99.5 and not is_done
        t = elapsed if motion else 0.4

        speed = 11.5 if is_sprint else (5.5 if is_wake else 8.2)
        bob = abs(math.sin(t * speed)) * (5.5 if is_done else (4.2 if is_sprint else 3.2))
        tilt = math.sin(t * speed * 0.5) * (6.0 if not is_done else 3.0)

        # 地面弹性投影
        shadow_scale = max(0.72, 1.0 - bob * 0.045)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(primary.red(), primary.green(), primary.blue(), 34))
        painter.drawEllipse(QPointF(cx, cy + 33.0), 28.0 * shadow_scale, 5.5 * shadow_scale)

        # 右侧迷你咖啡杯（全程陪伴，起床阶段冒热气）
        cup_x = cx + 44.0
        cup_y = cy + 14.0
        painter.setBrush(QColor('#FFFFFF'))
        painter.setPen(QPen(QColor('#D4CEE6'), 1.4))
        painter.drawRoundedRect(QRectF(cup_x, cup_y, 16.0, 16.0), 4.0, 4.0)
        # 杯把
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(cup_x + 12.5, cup_y + 3.0, 8.0, 9.0), -90 * 16, 180 * 16)
        # 咖啡液面
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#8B5A2B'))
        painter.drawRoundedRect(QRectF(cup_x + 2.5, cup_y + 2.5, 11.0, 4.0), 2.0, 2.0)
        # 袅袅上升热气
        steam_pen = QPen(QColor(primary.red(), primary.green(), primary.blue(), 110), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(steam_pen)
        for idx, sx in enumerate((cup_x + 5.0, cup_x + 10.5)):
            phase = (t * 1.8 + idx * 0.55) % 1.0
            sy = cup_y - 2.0 - phase * 12.0
            sway = math.sin(t * 5.0 + idx * 2.1) * 2.2
            s_path = QPainterPath()
            s_path.moveTo(sx, sy + 5.0)
            s_path.quadTo(sx + sway, sy + 2.0, sx - sway * 0.6, sy - 2.5)
            painter.drawPath(s_path)

        # 左侧纯矢量漂浮代码火花（打字与冲刺阶段持续冒出，不依赖系统字体字形）
        if not is_wake and not is_done:
            for i in range(4):
                life = (t * (1.35 if is_sprint else 0.95) + i * 0.27) % 1.0
                fx = cx - 44.0 - (i % 2) * 14.0 + math.sin(life * 5.0 + i) * 5.0
                fy = cy + 16.0 - life * 44.0
                alpha = max(0, min(255, int(math.sin(life * math.pi) * 215)))
                col = QColor('#6C58D9' if i % 2 == 0 else '#0EA5E9')
                col.setAlpha(alpha)
                if i == 0:
                    # 矢量小菱形火花
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(col)
                    sp = QPainterPath()
                    sp.moveTo(fx, fy - 4.2)
                    sp.lineTo(fx + 3.2, fy)
                    sp.lineTo(fx, fy + 4.2)
                    sp.lineTo(fx - 3.2, fy)
                    sp.closeSubpath()
                    painter.drawPath(sp)
                elif i == 1:
                    # 矢量尖括号 <>
                    painter.setPen(QPen(col, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    cp = QPainterPath()
                    cp.moveTo(fx - 2.0, fy - 3.0)
                    cp.lineTo(fx - 5.2, fy)
                    cp.lineTo(fx - 2.0, fy + 3.0)
                    cp.moveTo(fx + 2.0, fy - 3.0)
                    cp.lineTo(fx + 5.2, fy)
                    cp.lineTo(fx + 2.0, fy + 3.0)
                    painter.drawPath(cp)
                elif i == 2:
                    # 矢量四角星
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(col)
                    st = QPainterPath()
                    for k in range(8):
                        a = k * math.pi / 4.0
                        rr = 4.4 if k % 2 == 0 else 1.6
                        pt = QPointF(fx + math.cos(a) * rr, fy + math.sin(a) * rr)
                        st.moveTo(pt) if k == 0 else st.lineTo(pt)
                    st.closeSubpath()
                    painter.drawPath(st)
                else:
                    # 矢量空心气泡
                    painter.setPen(QPen(col, 1.5))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(fx, fy), 3.0, 3.0)

        # 完成时四周弹出开心小星星
        if is_done:
            for i in range(4):
                ang = i * (math.pi / 2.0) + math.pi / 4.0 + t * 0.6
                dist = 38.0 + math.sin(t * 6.0 + i) * 3.0
                sx = cx + math.cos(ang) * dist
                sy = cy - 4.0 + math.sin(ang) * (dist * 0.72)
                star_col = QColor('#F59E0B' if i % 2 == 0 else '#6C58D9')
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(star_col)
                star = QPainterPath()
                r_out, r_in = 5.0, 2.0
                for k in range(8):
                    a = k * math.pi / 4.0
                    rr = r_out if k % 2 == 0 else r_in
                    pt = QPointF(sx + math.cos(a) * rr, sy + math.sin(a) * rr)
                    if k == 0:
                        star.moveTo(pt)
                    else:
                        star.lineTo(pt)
                star.closeSubpath()
                painter.drawPath(star)

        # 主体角色（带弹性上下跳动与轻微摇摆）
        painter.save()
        painter.translate(cx, cy - bob)
        painter.rotate(tilt)

        # 弹簧小天线
        ant_sway = math.sin(t * speed - 0.7) * 5.5
        painter.setPen(QPen(QColor('#6C58D9'), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ant_path = QPainterPath()
        ant_path.moveTo(0.0, -23.0)
        ant_path.quadTo(ant_sway * 0.5, -31.0, ant_sway, -36.0)
        painter.drawPath(ant_path)
        # 天线顶发光球
        orb_col = QColor('#22C55E' if is_done else ('#F59E0B' if is_sprint else '#38BDF8'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(orb_col.red(), orb_col.green(), orb_col.blue(), 65))
        painter.drawEllipse(QPointF(ant_sway, -37.0), 7.0, 7.0)
        painter.setBrush(orb_col)
        painter.drawEllipse(QPointF(ant_sway, -37.0), 3.8, 3.8)

        # 圆滚滚棱镜紫机身
        body_rect = QRectF(-26.0, -24.0, 52.0, 46.0)
        body_grad = QLinearGradient(-26.0, -24.0, 26.0, 22.0)
        body_grad.setColorAt(0.0, QColor('#8470F2'))
        body_grad.setColorAt(1.0, QColor('#5E49CE'))
        painter.setBrush(body_grad)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1.2))
        painter.drawRoundedRect(body_rect, 16.0, 16.0)

        # 顶部玻璃高光条
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 58))
        painter.drawRoundedRect(QRectF(-16.0, -20.5, 32.0, 5.0), 2.5, 2.5)

        # 奶白色表情屏幕
        face_rect = QRectF(-19.0, -12.0, 38.0, 23.0)
        painter.setBrush(QColor('#FCFbff'))
        painter.drawRoundedRect(face_rect, 9.5, 9.5)

        # 害羞腮红
        painter.setBrush(QColor(244, 114, 182, 95))
        painter.drawEllipse(QPointF(-12.5, 4.0), 3.6, 2.1)
        painter.drawEllipse(QPointF(12.5, 4.0), 3.6, 2.1)

        # 表情眼睛 & 嘴巴
        eye_col = QColor('#2A2546')
        blink = (t % 3.4) > 3.24 and not is_done and not is_wake
        if is_done:
            # 开心弯弯笑眼 ^ ^
            smile_pen = QPen(eye_col, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(smile_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for ex in (-8.0, 8.0):
                ep = QPainterPath()
                ep.moveTo(ex - 3.6, -1.0)
                ep.quadTo(ex, -6.2, ex + 3.6, -1.0)
                painter.drawPath(ep)
            mouth = QPainterPath()
            mouth.moveTo(-4.0, 3.8)
            mouth.quadTo(0.0, 8.2, 4.0, 3.8)
            painter.drawPath(mouth)
        elif is_wake:
            # 刚起床半睁惺忪眼 + 小哈欠嘴
            sleep_pen = QPen(eye_col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(sleep_pen)
            painter.drawLine(QPointF(-11.0, -2.0), QPointF(-5.0, -2.0))
            painter.drawLine(QPointF(5.0, -2.0), QPointF(11.0, -2.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#E07A9A'))
            yawn_r = 2.4 + abs(math.sin(t * 2.4)) * 1.1
            painter.drawEllipse(QPointF(0.0, 4.5), yawn_r, yawn_r * 1.15)
        elif is_sprint:
            # 冲刺拼命眼 > < + 额头小汗滴
            sprint_pen = QPen(eye_col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(sprint_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            lp = QPainterPath()
            lp.moveTo(-11.0, -4.8)
            lp.lineTo(-5.8, -2.2)
            lp.lineTo(-11.0, 0.4)
            painter.drawPath(lp)
            rp = QPainterPath()
            rp.moveTo(11.0, -4.8)
            rp.lineTo(5.8, -2.2)
            rp.lineTo(11.0, 0.4)
            painter.drawPath(rp)
            # 波浪小嘴
            mp = QPainterPath()
            mp.moveTo(-3.5, 4.5)
            mp.quadTo(0.0, 2.2, 3.5, 4.5)
            painter.drawPath(mp)
            # 额头一颗蓝汗滴
            sweat_y = -14.5 + (t * 8.0 % 4.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#38BDF8'))
            sw = QPainterPath()
            sw.moveTo(15.0, sweat_y - 4.0)
            sw.quadTo(18.0, sweat_y + 1.0, 15.0, sweat_y + 2.5)
            sw.quadTo(12.0, sweat_y + 1.0, 15.0, sweat_y - 4.0)
            painter.drawPath(sw)
        else:
            # 认真狂敲键盘左右看眼神
            gaze_x = math.sin(t * 3.8) * 2.2
            if blink:
                painter.setPen(QPen(eye_col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(QPointF(-10.5, -2.0), QPointF(-5.5, -2.0))
                painter.drawLine(QPointF(5.5, -2.0), QPointF(10.5, -2.0))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(eye_col)
                painter.drawEllipse(QPointF(-8.0 + gaze_x, -2.2), 2.9, 3.3)
                painter.drawEllipse(QPointF(8.0 + gaze_x, -2.2), 2.9, 3.3)
                # 眼里闪亮高光点
                painter.setBrush(QColor('#FFFFFF'))
                painter.drawEllipse(QPointF(-9.0 + gaze_x, -3.4), 1.0, 1.0)
                painter.drawEllipse(QPointF(7.0 + gaze_x, -3.4), 1.0, 1.0)
            # 可爱小猫嘴 w
            painter.setPen(QPen(eye_col, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            wp = QPainterPath()
            wp.moveTo(-4.0, 3.5)
            wp.quadTo(-2.0, 6.2, 0.0, 3.8)
            wp.quadTo(2.0, 6.2, 4.0, 3.5)
            painter.drawPath(wp)

        painter.restore()

        # 迷你键盘 + 狂敲键盘的小爪子（非完成态展示）
        kb_rect = QRectF(cx - 24.0, cy + 20.0, 48.0, 10.0)
        painter.setPen(QPen(QColor('#CFC7E8'), 1.1))
        painter.setBrush(QColor('#F4F1FC'))
        painter.drawRoundedRect(kb_rect, 4.0, 4.0)
        # 键盘按键小格
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#D4CCE8'))
        for ki in range(6):
            painter.drawRoundedRect(QRectF(cx - 19.5 + ki * 6.8, cy + 22.8, 4.8, 4.0), 1.2, 1.2)

        if is_done:
            # 开心举起双爪欢呼！
            paw_y = cy - 4.0 - bob
            painter.setPen(QPen(QColor('#5E49CE'), 1.1))
            painter.setBrush(QColor('#A292F5'))
            painter.drawEllipse(QPointF(cx - 31.0, paw_y), 5.5, 5.5)
            painter.drawEllipse(QPointF(cx + 31.0, paw_y), 5.5, 5.5)
        else:
            # 交替狂敲键盘的左右小肉爪
            tap_freq = 24.0 if is_sprint else (10.0 if is_wake else 17.0)
            left_tap = max(0.0, math.sin(t * tap_freq)) * 4.5
            right_tap = max(0.0, -math.sin(t * tap_freq)) * 4.5
            painter.setPen(QPen(QColor('#5E49CE'), 1.1))
            painter.setBrush(QColor('#A292F5'))
            painter.drawEllipse(QPointF(cx - 12.0, cy + 19.5 - left_tap), 5.2, 4.4)
            painter.drawEllipse(QPointF(cx + 12.0, cy + 19.5 - right_tap), 5.2, 4.4)

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
        primary_light = QColor('#9380F2')
        text_strong = _qcolor(pal, 'TEXT_STRONG', '#262438')
        text_muted = _qcolor(pal, 'TEXT_MUTED', '#615D73')
        border_col = _qcolor(pal, 'BORDER', '#D9D3EB')
        track_col = _qcolor(pal, 'LOADING_TRACK', '#E7E2F5')

        is_max = bool(self._host is not None and self._host.isMaximized())
        radius = 0.0 if is_max else 12.0
        motion = self._motion_enabled()

        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0.0, 0.0, w, h), radius, radius)
        painter.setClipPath(clip_path)

        # 1. 全窗口毛玻璃虚化底幕
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

        # 2. 柔和呼吸极光光晕
        elapsed = time.monotonic() - self._started_at
        drift = math.sin(elapsed * 0.9) * 18.0 if motion else 0.0

        bloom1 = QRadialGradient(w * 0.34 + drift, h * 0.36, max(w, h) * 0.38)
        bloom1.setColorAt(0.0, QColor(108, 88, 217, 36))
        bloom1.setColorAt(1.0, QColor(108, 88, 217, 0))
        painter.fillRect(QRectF(0.0, 0.0, w, h), bloom1)

        bloom2 = QRadialGradient(w * 0.68 - drift, h * 0.64, max(w, h) * 0.34)
        bloom2.setColorAt(0.0, QColor(14, 165, 233, 24))
        bloom2.setColorAt(1.0, QColor(14, 165, 233, 0))
        painter.fillRect(QRectF(0.0, 0.0, w, h), bloom2)

        # 3. 居中毛玻璃卡片（8 级无阶梯弥散阴影）
        card_w = min(440.0, w - 48.0)
        card_h = 268.0
        card_x = (w - card_w) / 2.0
        card_y = (h - card_h) / 2.0
        card_rect = QRectF(card_x, card_y, card_w, card_h)

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
            shadow_path.addRoundedRect(shadow_rect, 22.0 + spread * 0.45, 22.0 + spread * 0.45)
            painter.fillPath(shadow_path, QColor(64, 52, 128, alpha))

        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 22.0, 22.0)
        card_bg = QLinearGradient(card_x, card_y, card_x, card_y + card_h)
        card_bg.setColorAt(0.0, QColor(255, 255, 255, 248))
        card_bg.setColorAt(1.0, QColor(250, 249, 255, 240))
        painter.fillPath(card_path, card_bg)

        painter.setPen(QPen(QColor(border_col.red(), border_col.green(), border_col.blue(), 210), 1.0))
        painter.drawPath(card_path)

        # 4. 趣味“棱镜打工崽”主舞台
        pct = max(0.0, min(100.0, self._display_progress))
        cx = card_x + card_w / 2.0
        self._draw_mascot_stage(painter, cx, card_y + 78.0, pct, elapsed, primary, motion)

        # 5. 趣味主文案（随启动阶段自动变换）
        quip = self._pick_funny_quip(pct, elapsed)
        quip_font = QFont('Microsoft YaHei UI', 11)
        quip_font.setBold(True)
        painter.setFont(quip_font)
        painter.setPen(text_strong)
        painter.drawText(
            QRectF(card_x + 24.0, card_y + 138.0, card_w - 48.0, 26.0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            quip,
        )

        # 6. 真实加载进度副文案
        sub_font = QFont('Microsoft YaHei UI')
        sub_font.setPointSizeF(8.5)
        painter.setFont(sub_font)
        painter.setPen(text_muted)
        fm = painter.fontMetrics()
        elided = fm.elidedText(f'{self._title} · {self._subtitle}', Qt.TextElideMode.ElideRight, int(card_w - 64.0))
        painter.drawText(
            QRectF(card_x + 32.0, card_y + 168.0, card_w - 64.0, 20.0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )

        # 7. 平滑进度轨道 + 彗星光点 + 百分比
        track_w = card_w - 84.0
        track_h = 7.0
        track_x = card_x + 42.0
        track_y = card_y + 206.0
        track_rect = QRectF(track_x, track_y, track_w, track_h)

        track_path = QPainterPath()
        track_path.addRoundedRect(track_rect, 3.5, 3.5)
        painter.fillPath(track_path, track_col)

        fill_w = max(12.0, track_w * (pct / 100.0)) if pct > 0 else 0.0
        if fill_w > 0:
            clamped_w = min(track_w, fill_w)
            fill_rect = QRectF(track_x, track_y, clamped_w, track_h)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, 3.5, 3.5)
            bar_grad = QLinearGradient(track_x, track_y, track_x + track_w, track_y)
            bar_grad.setColorAt(0.0, primary)
            bar_grad.setColorAt(1.0, primary_light)
            painter.fillPath(fill_path, bar_grad)

            if motion and pct < 100.0:
                phase = (elapsed % 1.35) / 1.35
                shimmer_x = track_x + (clamped_w + 56.0) * phase - 56.0
                shimmer_grad = QLinearGradient(shimmer_x, track_y, shimmer_x + 56.0, track_y)
                shimmer_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
                shimmer_grad.setColorAt(0.5, QColor(255, 255, 255, 130))
                shimmer_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.save()
                painter.setClipPath(fill_path)
                painter.fillRect(fill_rect, shimmer_grad)
                painter.restore()

                # 进度条头部小光珠
                tip_x = track_x + clamped_w - 3.5
                tip_y = track_y + track_h / 2.0
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(108, 88, 217, 72))
                painter.drawEllipse(QPointF(tip_x, tip_y), 7.5, 7.5)
                painter.setBrush(QColor('#FFFFFF'))
                painter.drawEllipse(QPointF(tip_x, tip_y), 2.8, 2.8)

        pct_font = QFont('Segoe UI', 9)
        pct_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(pct_font)
        painter.setPen(primary)
        painter.drawText(
            QRectF(track_x, track_y + 13.0, track_w, 18.0),
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
