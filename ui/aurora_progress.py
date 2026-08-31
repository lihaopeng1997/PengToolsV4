# -*- coding: utf-8 -*-
"""企业级浮层 Loading：与布局隔离，API 保持 start_busy / set_progress / finish / fail。

UX 契约：
- 短任务（<300ms 完成）：不展示 loading / busy 浮层，彻底杜绝短操作闪烁；
- 中长任务（>=300ms）：延迟 300ms 触发统一 busy 浮层；
- 浮层一旦实际展示，保障至少 500ms 最小可视驻留时长，避免闪现；
- 失败（fail）：立即展示，取消 pending 延迟，合理驻留供用户阅读；
- 状态与定时器安全：基于 generation 世代标记，废弃过期 timer，防止状态污染；
- 视觉权威：完全自适应 Light / Dark 主题语义 Tokens。
"""

import time
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

DEFAULT_DELAY_SHOW_MS = 300
DEFAULT_MIN_VISIBLE_MS = 500
SUCCESS_LINGER_MS = 180
DEFAULT_SUCCESS_LINGER_MS = 350
FAIL_LINGER_MS = 3200
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
        self._phase = 0
        self._value = -1
        self._label = ''
        self._state = 'idle'

        self._delay_timer: QTimer | None = None
        self._linger_timer: QTimer | None = None

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)

        self.setFixedHeight(62)
        # 仅作视觉反馈：不拦截鼠标，避免「Loading 盖住界面 → 点什么都没反应」
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 浮层默认不占布局；hide 时也不会把按钮顶上/顶下
        self.hide()

    @property
    def is_visible_to_user(self) -> bool:
        """是否实际已在界面对用户可见。"""
        return self._is_shown and not self.isHidden()

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
        """相对宿主水平居中浮于顶部附近。不修改宿主 layout。"""
        host = host or self.parentWidget()
        if host is None:
            return
        host_w = max(host.width(), 1)
        width = min(540, max(300, host_w - 48))
        self.setFixedWidth(width)
        x = max(24, (host_w - width) // 2)
        y = 56 if host.height() >= 160 else max(12, host.height() // 8)
        self.move(x, y)
        self.raise_()

    def start_busy(self, label: str, *, immediate: bool = False):
        """开始忙碌状态。默认延迟 300ms 展示，防止短操作闪烁。"""
        self._generation += 1
        gen = self._generation
        self._label = label or ''
        self._value = -1
        self._phase = 0
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

    def _show_overlay_now(self):
        """立即展示浮层，并记录展示时间戳。"""
        self._is_shown = True
        self._shown_timestamp = time.monotonic()
        self.place_overlay()
        self.show()
        self.raise_()
        if not self._anim_timer.isActive():
            self._anim_timer.start(DEFAULT_ANIM_TICK_MS)
        self.update()

    def _on_delay_show_timeout(self, gen: int | None = None):
        if gen is not None and gen != self._generation:
            return
        if self._state not in ('pending_busy', 'progress'):
            return
        self._state = 'busy' if self._value < 0 else 'progress'
        self._show_overlay_now()

    def set_progress(self, value, label=None):
        """设置显式百分比进度（0-100）。"""
        self._generation += 1
        self._value = max(0, min(100, int(value)))
        if label is not None:
            self._label = label
        self._cancel_timers()

        self._state = 'progress'
        if not self._is_shown:
            self._show_overlay_now()
        else:
            self.update()

    def finish(self, label=''):
        """任务成功完成。若尚未实际展示（短任务）则静默收起；若已展示则保障最小可视时长后渐隐。"""
        self._generation += 1
        gen = self._generation
        self._cancel_delay_timer()

        if not self._is_shown:
            # 短任务在 300ms 内完成，直接收起，绝不闪现
            self._state = 'idle'
            self._value = -1
            self._label = ''
            self.hide()
            return

        # 已实际展示过，满足最小可视驻留时长
        self._state = 'finish'
        self._value = 100
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

    def fail(self, label=''):
        """任务失败。立即展示失败浮层并驻留，取消任何 pending 延迟。"""
        self._generation += 1
        gen = self._generation
        self._cancel_timers()
        self._anim_timer.stop()

        self._state = 'fail'
        self._value = 0
        self._label = label or self._label or '失败'
        self._show_overlay_now()

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

    def _tick(self):
        self._phase = (self._phase + 4) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(5.0, 4.0, self.width() - 10, self.height() - 9)
        pal = _palette()

        surface = _qc(pal, 'ELEVATED_SURFACE', pal.get('SURFACE', '#29332E'))
        border = _qc(pal, 'BORDER', '#3C4942')
        text = _qc(pal, 'TEXT_STRONG', '#EDF2EE')
        primary = _qc(pal, 'PRIMARY', '#9ABAA6')
        primary_soft = _qc(pal, 'PRIMARY_SOFT', '#35483E')
        track = _qc(pal, 'LOADING_TRACK', '#425047')
        success = _qc(pal, 'SUCCESS', '#7BA88A')
        success_bg = _qc(pal, 'SUCCESS_BG', '#263D31')
        success_border = _qc(pal, 'SUCCESS_BORDER', '#4D765D')
        danger = _qc(pal, 'DANGER', '#C78A8A')
        danger_bg = _qc(pal, 'DANGER_BG', '#432E30')
        danger_border = _qc(pal, 'DANGER_BORDER', '#765055')
        info_bg = _qc(pal, 'INFO_BG', primary_soft)
        info_border = _qc(pal, 'INFO_BORDER', border)

        # soft shadow from theme SHADOW base
        shadow_base = _qc(pal, 'APP_BG', '#1B211E')
        for i, alpha in enumerate((30, 50, 70)):
            shadow = bounds.adjusted(-1 + i * 0.4, 1 + i * 0.5, 1 - i * 0.4, 2 + i * 0.55)
            painter.setPen(Qt.PenStyle.NoPen)
            sc = QColor(shadow_base)
            sc.setAlpha(alpha)
            painter.setBrush(sc)
            painter.drawRoundedRect(shadow, 14, 14)

        # elevated surface (deep for night, light for day)
        body = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        soft = _qc(pal, 'SURFACE_SOFT', surface)
        body.setColorAt(0.0, surface)
        body.setColorAt(0.65, soft)
        body.setColorAt(1.0, surface)
        painter.setPen(QPen(border, 1))
        painter.setBrush(body)
        painter.drawRoundedRect(bounds, 13, 13)

        # left brand bar
        accent = QRectF(bounds.left() + 2, bounds.top() + 12, 3.5, bounds.height() - 24)
        painter.setPen(Qt.PenStyle.NoPen)
        accent_grad = QLinearGradient(accent.topLeft(), accent.bottomLeft())
        accent_grad.setColorAt(0.0, primary)
        accent_grad.setColorAt(1.0, _qc(pal, 'PRIMARY_ACTIVE', primary))
        painter.setBrush(accent_grad)
        painter.drawRoundedRect(accent, 2, 2)

        # status chip
        is_fail = self._state == 'fail' or (self._value == 0 and not self._anim_timer.isActive())
        is_finish = self._state == 'finish' or self._value >= 100
        chip_w = 54 if self._value >= 0 else 62
        chip = QRectF(bounds.right() - chip_w - 12, bounds.top() + 10, chip_w, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        if is_fail:
            painter.setBrush(danger_bg)
            painter.setPen(QPen(danger_border, 1))
            chip_fg = danger
        elif is_finish:
            painter.setBrush(success_bg)
            painter.setPen(QPen(success_border, 1))
            chip_fg = success
        else:
            painter.setBrush(info_bg)
            painter.setPen(QPen(info_border, 1))
            chip_fg = primary
        painter.drawRoundedRect(chip, 11, 11)
        painter.setPen(chip_fg)
        painter.setFont(QFont('Microsoft YaHei UI', 8, QFont.Weight.Bold))
        if self._value < 0:
            chip_text = '处理中'
        elif is_fail:
            chip_text = '失败'
        elif is_finish:
            chip_text = '完成'
        else:
            chip_text = f'{self._value}%'
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)

        # progress track
        track_rect = QRectF(bounds.left() + 18, bounds.bottom() - 15, bounds.width() - 36, 5.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(track_rect, 3, 3)

        if self._value < 0:
            width = max(72.0, track_rect.width() * 0.22)
            x = track_rect.left() + ((self._phase / 360.0) * (track_rect.width() + width)) - width
            fill = QRectF(x, track_rect.top(), width, track_rect.height())
        else:
            fill = QRectF(
                track_rect.left(), track_rect.top(),
                track_rect.width() * self._value / 100.0, track_rect.height(),
            )

        gradient = QLinearGradient(fill.left(), fill.top(), fill.right(), fill.top())
        if is_fail:
            gradient.setColorAt(0.0, danger)
            gradient.setColorAt(1.0, _qc(pal, 'DANGER', danger))
        elif is_finish:
            gradient.setColorAt(0.0, success)
            gradient.setColorAt(1.0, _qc(pal, 'SUCCESS', success))
        else:
            gradient.setColorAt(0.0, primary)
            gradient.setColorAt(1.0, _qc(pal, 'PRIMARY_ACTIVE', primary))
        path = QPainterPath()
        path.addRoundedRect(track_rect, 3, 3)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(fill, gradient)
        painter.restore()

        # label
        painter.setPen(text)
        painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
        label_rect = QRectF(bounds.left() + 18, bounds.top() + 9, bounds.width() - chip_w - 40, 22)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label)
