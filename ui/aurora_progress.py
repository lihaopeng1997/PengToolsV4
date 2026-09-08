# -*- coding: utf-8 -*-
"""企业级浮层 Loading：与布局隔离，API 保持 start_busy / set_progress / finish / fail。

晴空棱镜 V2.0 规范 (LD-02 / LD-08):
- 视觉语言：“棱镜环 + 克制的光带”，品牌单菱形静置，外围 24x24 细弧环缓转 (900ms 周期)；
- 浮层定位：固定高 62px，目标宽 320px（max 宿主宽-32），右上 16px（窄宿主居中）；
- 状态反馈：成功为静态绿色对勾（淡入 150ms，禁止放礼花），失败为静态红色错误图标（无 shake）；
- 鼠标穿透：WA_TransparentForMouseEvents 保持，不拦截底层操作；
- 状态机契约：300ms 延迟展示、500ms 最少可视驻留、finish 驻留 (remaining + success_linger)、fail 2200ms、generation token 隔离；
- 无障碍与动效：支持 prefers-reduced-motion / motion-disabled 静态降级。
"""

import math
import time
from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QBrush
from PyQt6.QtWidgets import QWidget

DEFAULT_DELAY_SHOW_MS = 300
DEFAULT_MIN_VISIBLE_MS = 500
DEFAULT_SUCCESS_LINGER_MS = 350
FAIL_LINGER_MS = 2200
DEFAULT_ANIM_TICK_MS = 28


def _palette():
    try:
        from ui.theme_manager import ThemeManager
        return ThemeManager.instance().palette()
    except Exception:
        return {}


def _qc(pal: dict, key: str, fallback: str = '#29332E') -> QColor:
    raw = pal.get(key) or fallback
    try:
        from ui.theme_manager import parse_color
        parsed = parse_color(raw)
        if parsed:
            r, g, b, a = parsed
            return QColor(r, g, b, a)
    except Exception:
        pass
    c = QColor(raw)
    return c if c.isValid() else QColor(fallback)


class AuroraProgress(QWidget):
    """Floating enterprise progress chip — visual only; trigger logic stays in callers."""

    def __init__(
        self,
        parent=None,
        *,
        delay_show_ms: int = DEFAULT_DELAY_SHOW_MS,
        min_visible_ms: int = DEFAULT_MIN_VISIBLE_MS,
        success_linger_ms: int = DEFAULT_SUCCESS_LINGER_MS,
        fail_linger_ms: int = FAIL_LINGER_MS,
    ):
        super().__init__(parent)
        self._delay_show_ms = max(0, int(delay_show_ms))
        self._min_visible_ms = max(0, int(min_visible_ms))
        self._success_linger_ms = max(0, int(success_linger_ms))
        self._fail_linger_ms = max(0, int(fail_linger_ms))

        self._generation = 0
        self._is_shown = False
        self._shown_timestamp = 0.0
        self._finish_timestamp = 0.0
        self._value = -1
        self._label = ''
        self._state = 'idle'  # idle, pending_busy, busy, progress, finish, fail

        self._delay_timer: QTimer | None = None
        self._linger_timer: QTimer | None = None

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(DEFAULT_ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._tick)

        self.setFixedHeight(62)
        self.setFixedWidth(320)
        # 仅作视觉反馈：不拦截鼠标，避免「Loading 盖住界面 → 点什么都没反应」
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)

    @property
    def is_visible_to_user(self) -> bool:
        """是否实际已在界面对用户可见。"""
        return self._is_shown and not self.isHidden()

    @property
    def current_token(self) -> int:
        """当前世代 token。"""
        return self._generation

    def _is_motion_enabled(self) -> bool:
        try:
            from ui.motion import motion_enabled
            return motion_enabled()
        except Exception:
            return True

    def eventFilter(self, watched, event):
        if watched == self.parentWidget():
            if event.type() == QEvent.Type.Close:
                self._cancel_all_visual_timers()
                if not self._is_shown:
                    self._state = 'idle'
            elif event.type() == QEvent.Type.Hide:
                if hasattr(self, '_anim_timer') and self._anim_timer.isActive():
                    self._anim_timer.stop()
            elif event.type() == QEvent.Type.Show:
                if self._is_shown and self._state in ('busy', 'progress', 'finish'):
                    if not self._anim_timer.isActive():
                        self._anim_timer.start(DEFAULT_ANIM_TICK_MS)
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, '_anim_timer') and self._anim_timer.isActive():
            self._anim_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._is_shown and self._state in ('busy', 'progress', 'finish'):
            if not self._anim_timer.isActive():
                self._anim_timer.start(DEFAULT_ANIM_TICK_MS)

    def closeEvent(self, event):
        super().closeEvent(event)
        self._cancel_all_visual_timers()
        if not self._is_shown:
            self._state = 'idle'

    def _cancel_all_visual_timers(self):
        if hasattr(self, '_anim_timer') and self._anim_timer.isActive():
            self._anim_timer.stop()
        self._cancel_timers()

    def _cancel_delay_timer(self):
        if self._delay_timer is not None:
            try:
                self._delay_timer.stop()
                self._delay_timer.deleteLater()
            except Exception:
                pass
            self._delay_timer = None

    def _cancel_linger_timer(self):
        if self._linger_timer is not None:
            try:
                self._linger_timer.stop()
                self._linger_timer.deleteLater()
            except Exception:
                pass
            self._linger_timer = None

    def _cancel_timers(self):
        self._cancel_delay_timer()
        self._cancel_linger_timer()

    def place_overlay(self, host=None):
        """相对宿主定位：目标宽 320，max host 宽-32；右上 16；窄 host 居中。"""
        host = host or self.parentWidget()
        if host is None:
            return
        host_w = max(host.width(), 1)
        width = min(320, max(240, host_w - 32))
        self.setFixedWidth(width)

        if host_w >= 480:
            x = host_w - width - 16
        else:
            x = max(16, (host_w - width) // 2)

        y = 16 if host.height() >= 100 else max(8, host.height() // 8)
        self.move(x, y)
        self.raise_()

    def start_busy(self, label: str, *, immediate: bool = False) -> int:
        """开始忙碌状态。默认延迟 300ms 展示，防止短操作闪烁。返回当前 generation token。"""
        self._generation += 1
        gen = self._generation
        self._label = label or ''
        self._value = -1
        self._cancel_timers()

        if self._is_shown or immediate or self._delay_show_ms <= 0:
            self._state = 'busy'
            self._show_overlay_now()
        else:
            self._state = 'pending_busy'
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda g=gen: self._on_delay_show_timeout(g))
            self._delay_timer = timer
            timer.start(self._delay_show_ms)
        return gen

    def _show_overlay_now(self):
        """立即展示浮层，并记录展示时间戳。"""
        self._is_shown = True
        self._shown_timestamp = time.monotonic()
        self.place_overlay()
        self.show()
        self.raise_()
        if self._state != 'fail':
            if not self._anim_timer.isActive():
                self._anim_timer.start(DEFAULT_ANIM_TICK_MS)
        else:
            self._anim_timer.stop()
        self.update()

    def _on_delay_show_timeout(self, gen: int | None = None):
        if gen is not None and gen != self._generation:
            return
        if self._state not in ('pending_busy', 'progress'):
            return
        self._state = 'busy' if self._value < 0 else 'progress'
        self._show_overlay_now()

    def set_progress(self, value, label=None, *, token: int | None = None):
        """设置显式百分比进度（0-100）。若传入 token 且已过期则静默忽略。"""
        if token is not None and token != self._generation:
            return
        self._value = max(0, min(100, int(value)))
        if label is not None:
            self._label = label
        self._cancel_timers()

        self._state = 'progress'
        if not self._is_shown:
            self._show_overlay_now()
        else:
            self.update()

    def finish(self, label='', *, token: int | None = None):
        """任务成功完成。若尚未实际展示（短任务）则静默收起；若已展示则保障最小可视时长后渐隐。"""
        if token is not None and token != self._generation:
            return
        self._generation += 1
        gen = self._generation
        self._cancel_delay_timer()

        if not self._is_shown:
            self._state = 'idle'
            self._value = -1
            self._label = ''
            self.hide()
            return

        self._state = 'finish'
        self._value = 100
        self._finish_timestamp = time.monotonic()
        if label:
            self._label = label
        self.update()

        elapsed_ms = (time.monotonic() - self._shown_timestamp) * 1000.0
        remaining_min_ms = max(0.0, self._min_visible_ms - elapsed_ms)
        total_linger_ms = int(remaining_min_ms + self._success_linger_ms)

        self._cancel_linger_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda g=gen: self._on_linger_hide(g))
        self._linger_timer = timer
        timer.start(total_linger_ms)

    def fail(self, label='', *, token: int | None = None):
        """任务失败。立即展示失败浮层并驻留，取消任何 pending 延迟。"""
        if token is not None and token != self._generation:
            return
        self._generation += 1
        gen = self._generation
        self._cancel_timers()

        self._state = 'fail'
        self._value = 0
        self._label = label or self._label or '失败'
        self._show_overlay_now()
        self._anim_timer.stop()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda g=gen: self._on_linger_hide(g))
        self._linger_timer = timer
        timer.start(self._fail_linger_ms)

    def _on_linger_hide(self, gen: int | None = None):
        if gen is not None and gen != self._generation:
            return
        self._is_shown = False
        self._state = 'idle'
        self._value = -1
        self._label = ''
        self._anim_timer.stop()
        self.hide()

    def hide_now(self):
        """立刻收起并重置所有定时器，用于切页或显式中断。"""
        self._generation += 1
        self._cancel_timers()
        self._anim_timer.stop()
        self._is_shown = False
        self._state = 'idle'
        self._value = -1
        self._label = ''
        self.hide()

    def reset(self):
        """完全重置状态机与定时器。"""
        self.hide_now()

    def _tick(self):
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        bounds = QRectF(4.0, 4.0, self.width() - 8, self.height() - 8)
        pal = _palette()

        surface = _qc(pal, 'ELEVATED_SURFACE', pal.get('SURFACE', '#FFFFFF'))
        border = _qc(pal, 'ELEVATED_BORDER', pal.get('BORDER', '#E6E2F0'))
        text_strong = _qc(pal, 'TEXT_STRONG', '#262438')
        text_muted = _qc(pal, 'TEXT_MUTED', '#615D73')
        primary = _qc(pal, 'PRIMARY', '#6C58D9')
        primary_soft = _qc(pal, 'PRIMARY_SOFT', '#EEE9FF')
        track_color = _qc(pal, 'LOADING_TRACK', '#E6EBF5')
        success = _qc(pal, 'SUCCESS', '#247F75')
        success_bg = _qc(pal, 'SUCCESS_BG', '#F0F9F6')
        success_border = _qc(pal, 'SUCCESS_BORDER', '#A3D9C9')
        danger = _qc(pal, 'DANGER', '#C45371')
        danger_bg = _qc(pal, 'DANGER_BG', '#FDF2F4')
        danger_border = _qc(pal, 'DANGER_BORDER', '#F5BAC7')

        # 1. 柔和阴影层
        shadow_base = _qc(pal, 'SHADOW', 'rgba(38, 36, 56, 45)')
        painter.setPen(Qt.PenStyle.NoPen)
        for i in (3, 2, 1):
            sc = QColor(shadow_base)
            sc.setAlpha(max(4, shadow_base.alpha() // (i + 1)))
            painter.setBrush(sc)
            painter.drawRoundedRect(bounds.adjusted(-i * 1.2, -i * 0.8 + 1.5, i * 1.2, i * 1.5 + 1.5), 14, 14)

        # 2. 容器卡片背景与边框 (高斯毛玻璃感/高层表面)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(surface)
        painter.drawRoundedRect(bounds, 12, 12)

        is_fail = self._state == 'fail'
        is_finish = self._state == 'finish'
        has_progress = self._value >= 0 and not is_fail and not is_finish
        motion_on = self._is_motion_enabled() and (self._anim_timer.isActive() or is_finish)

        now = time.monotonic()
        elapsed = now - self._shown_timestamp if self._shown_timestamp > 0 else 0.0

        # 3. 左侧指示器区域：24x24 环 / 静态成功 / 静态失败 (LD-02 / LD-08)
        ind_size = 24.0
        ind_x = bounds.left() + 14.0
        ind_y = bounds.top() + (bounds.height() - ind_size) / 2.0 - (2.0 if has_progress else 0.0)
        ind_rect = QRectF(ind_x, ind_y, ind_size, ind_size)
        center_x = ind_x + ind_size / 2.0
        center_y = ind_y + ind_size / 2.0

        if is_finish:
            # LD-08: 静态 18px 绿色对勾（淡入 150ms，禁止放礼花）
            finish_elapsed = (now - self._finish_timestamp) if self._finish_timestamp > 0 else 0.15
            fade = min(1.0, max(0.2, finish_elapsed / 0.15))
            c = QColor(success)
            c.setAlpha(int(fade * 255))
            painter.setPen(QPen(c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # 对勾折线: (cx-5, cy) -> (cx-1, cy+4) -> (cx+6, cy-4)
            path = QPainterPath()
            path.moveTo(center_x - 5.5, center_y)
            path.lineTo(center_x - 1.5, center_y + 4.0)
            path.lineTo(center_x + 6.0, center_y - 4.5)
            painter.drawPath(path)

        elif is_fail:
            # LD-08: 静态 18px 红色错误叉号（无 shake，无循环动画）
            painter.setPen(QPen(danger, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # 叉号两条线: (cx-4.5, cy-4.5) <-> (cx+4.5, cy+4.5)
            painter.drawLine(QPointF(center_x - 4.5, center_y - 4.5), QPointF(center_x + 4.5, center_y + 4.5))
            painter.drawLine(QPointF(center_x + 4.5, center_y - 4.5), QPointF(center_x - 4.5, center_y + 4.5))

        else:
            # LD-02: 24x24 棱镜环，描边 2，前景 90 度圆弧；900ms 循环；中心单菱形静置
            # 背景圆环 (Loading Track)
            track_pen = QPen(track_color, 2.0)
            painter.setPen(track_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(ind_rect.adjusted(1, 1, -1, -1))

            # 前景 90 度圆弧 (900ms 周期线性旋转)
            if motion_on:
                rot_deg = (elapsed / 0.9 * 360.0) % 360.0
            else:
                rot_deg = 45.0
            arc_pen = QPen(primary, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.drawArc(ind_rect.adjusted(1, 1, -1, -1), int(rot_deg * 16), int(90 * 16))

            # 中心单菱形微晶静置 (4x4 菱形)
            gem_path = QPainterPath()
            gem_path.moveTo(center_x, center_y - 2.5)
            gem_path.lineTo(center_x + 2.5, center_y)
            gem_path.lineTo(center_x, center_y + 2.5)
            gem_path.lineTo(center_x - 2.5, center_y)
            gem_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(primary)
            painter.drawPath(gem_path)

        # 4. 右侧状态徽标 (Status Chip)
        chip_w = 54 if has_progress else 60
        chip_h = 22.0
        chip = QRectF(bounds.right() - chip_w - 12.0, bounds.top() + (bounds.height() - chip_h) / 2.0 - (3.0 if has_progress else 0.0), chip_w, chip_h)
        painter.setPen(Qt.PenStyle.NoPen)

        if is_fail:
            painter.setBrush(danger_bg)
            painter.setPen(QPen(danger_border, 1.0))
            chip_fg = danger
            chip_text = '失败'
        elif is_finish:
            painter.setBrush(success_bg)
            painter.setPen(QPen(success_border, 1.0))
            chip_fg = success
            chip_text = '完成'
        elif has_progress:
            painter.setBrush(primary_soft)
            painter.setPen(QPen(border, 1.0))
            chip_fg = primary
            chip_text = f'{self._value}%'
        else:
            painter.setBrush(primary_soft)
            painter.setPen(QPen(border, 1.0))
            chip_fg = primary
            chip_text = '处理中'

        painter.drawRoundedRect(chip, 10, 10)
        painter.setPen(chip_fg)
        painter.setFont(QFont('Microsoft YaHei UI', 8, QFont.Weight.Bold))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)

        # 5. 主文本描述 (13px / TEXT_STRONG)
        label_x = ind_x + ind_size + 10.0
        label_w = chip.left() - label_x - 8.0
        label_y = bounds.top() + 12.0 if has_progress else bounds.top() + (bounds.height() - 20.0) / 2.0
        label_rect = QRectF(label_x, label_y, max(0.0, label_w), 20.0)

        painter.setPen(text_strong)
        painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label)

        # 6. 细进度轨道（有真实进度时显示：4px 细轨道，无任务阻断鼠标）
        if has_progress:
            track_rect = QRectF(label_x, bounds.bottom() - 14.0, bounds.right() - label_x - 14.0, 4.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track_color)
            painter.drawRoundedRect(track_rect, 2.0, 2.0)

            val_pct = max(0.0, min(1.0, self._value / 100.0))
            fill_rect = QRectF(track_rect.left(), track_rect.top(), track_rect.width() * val_pct, track_rect.height())

            fill_grad = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            fill_grad.setColorAt(0.0, _qc(pal, 'PRIMARY_GRAD_START', primary))
            fill_grad.setColorAt(1.0, _qc(pal, 'PRIMARY_GRAD_END', primary))

            path = QPainterPath()
            path.addRoundedRect(track_rect, 2.0, 2.0)
            painter.save()
            painter.setClipPath(path)
            painter.setBrush(fill_grad)
            painter.drawRoundedRect(fill_rect, 2.0, 2.0)
            painter.restore()
