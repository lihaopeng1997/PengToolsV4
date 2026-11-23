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
DEFAULT_SUCCESS_LINGER_MS = 450
FAIL_LINGER_MS = 2200
DEFAULT_ANIM_TICK_MS = 28

_CONTEXT_QUIPS_ZH = (
    (('数据库', '目录', '表', 'sql', 'redis', 'mongo', '集合', '查询', '连接'), (
        '数据库正在努力翻箱倒柜…',
        '索引小哥正踩着风火轮查表…',
        '连接池正在热身做广播体操…',
        '正在和数据库深情对视中…',
    )),
    (('ssh', '日志', '远程', '服务器', '主机'), (
        'SSH 小哥正顺着网线爬过去…',
        '正在礼貌敲开远程服务器的门…',
        '正在日志海里捞针，马上捞上来…',
    )),
    (('模型', '探测', 'ai', 'llm', 'agent'), (
        '正在喊大模型起床干活…',
        '正在确认模型今天没偷懒掉线…',
        '神经网络正在疯狂转动脑筋…',
    )),
    (('svn', '需求', '文档', '导入', '导出', 'docx', '矩阵', '整理'), (
        '文档小助手正在疯狂翻页整理…',
        '正在把文件排好队立正站好…',
        'SVN 正在搬砖，马上搬完…',
    )),
    (('抓包', '代理', '证书', '监听', '浏览器', 'cdp', '请求'), (
        '正在支起抓包小雷达…',
        '数据包正在排队过安检…',
        '正在礼貌地敲接口大门…',
    )),
)

_DEFAULT_QUIPS_ZH = (
    '小马达已拉满，喝口水马上好…',
    '正在疯狂敲键盘赶工中，别催别催…',
    '马上就好，先顺手活动下脖子吧…',
    '齿轮飞转中，请稍等片刻…',
)

_DEFAULT_QUIPS_EN = (
    'Tiny prism bot is typing furiously…',
    'Brewing coffee while crunching this…',
    'Almost there, stretching our circuits…',
)


def _pick_context_quip(label: str, elapsed: float, is_en: bool) -> str:
    if is_en:
        return _DEFAULT_QUIPS_EN[int(elapsed / 2.2) % len(_DEFAULT_QUIPS_EN)]
    low = (label or '').lower()
    for keywords, quips in _CONTEXT_QUIPS_ZH:
        if any(kw in low for kw in keywords):
            return quips[int(elapsed / 2.2) % len(quips)]
    return _DEFAULT_QUIPS_ZH[int(elapsed / 2.2) % len(_DEFAULT_QUIPS_ZH)]


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
        self._state = 'idle'  # idle, pending_busy, busy, progress, finish, fail, cancelled

        self._delay_timer: QTimer | None = None
        self._linger_timer: QTimer | None = None

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(DEFAULT_ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._tick)

        self.setFixedHeight(72)
        self.setFixedWidth(348)
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

    def _cancel_chip_label(self) -> str:
        language = str(getattr(self.parentWidget(), 'language', 'zh') or 'zh').lower()
        return 'Cancelled' if language.startswith('en') else '已取消'

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
        """相对宿主定位：目标宽 348，max host 宽-32；右上 16；窄 host 居中。"""
        host = host or self.parentWidget()
        if host is None:
            return
        host_w = max(host.width(), 1)
        width = min(348, max(260, host_w - 32))
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
        if self._state not in ('fail', 'cancelled'):
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

    def cancel(self, label='', *, token: int | None = None):
        """Show a neutral, static cancellation terminal state without changing task ownership."""
        if token is not None and token != self._generation:
            return
        self._generation += 1
        gen = self._generation
        self._cancel_timers()

        self._state = 'cancelled'
        self._value = -1
        self._label = label or self._label or '已取消'
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
        is_cancelled = self._state == 'cancelled'
        has_progress = self._value >= 0 and not is_fail and not is_finish and not is_cancelled
        motion_on = self._is_motion_enabled() and (self._anim_timer.isActive() or is_finish)

        now = time.monotonic()
        elapsed = now - self._shown_timestamp if self._shown_timestamp > 0 else 0.0

        # 3. 左侧迷你趣味棱镜小人（34x34）：忙碌时弹性跳动左右张望；完成微笑 ^ ^；失败晕眼 x x
        ind_size = 34.0
        ind_x = bounds.left() + 12.0
        ind_y = bounds.top() + (bounds.height() - ind_size) / 2.0 - 2.0
        center_x = ind_x + ind_size / 2.0
        center_y = ind_y + ind_size / 2.0

        is_en = str(getattr(self.parentWidget(), 'language', 'zh') or 'zh').lower().startswith('en')

        if is_finish:
            finish_elapsed = (now - self._finish_timestamp) if self._finish_timestamp > 0 else 0.15
            fade = min(1.0, max(0.78, finish_elapsed / 0.15))
            bg_c = QColor(success_bg)
            bg_c.setAlpha(int(fade * 255))
            fg_c = QColor(success)
            fg_c.setAlpha(int(fade * 255))
            painter.setPen(QPen(QColor(success_border), 1.2))
            painter.setBrush(bg_c)
            painter.drawRoundedRect(QRectF(ind_x + 2, ind_y + 2, ind_size - 4, ind_size - 4), 10, 10)
            # 开心笑眼 + 小笑嘴
            painter.setPen(QPen(fg_c, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for ex in (center_x - 5.0, center_x + 5.0):
                ep = QPainterPath()
                ep.moveTo(ex - 2.4, center_y - 1.5)
                ep.quadTo(ex, center_y - 5.0, ex + 2.4, center_y - 1.5)
                painter.drawPath(ep)
            mp = QPainterPath()
            mp.moveTo(center_x - 3.5, center_y + 2.8)
            mp.quadTo(center_x, center_y + 6.5, center_x + 3.5, center_y + 2.8)
            painter.drawPath(mp)

        elif is_fail:
            painter.setPen(QPen(danger_border, 1.2))
            painter.setBrush(danger_bg)
            painter.drawRoundedRect(QRectF(ind_x + 2, ind_y + 2, ind_size - 4, ind_size - 4), 10, 10)
            # 晕眼 x x + 委屈小波浪嘴
            painter.setPen(QPen(danger, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for ex in (center_x - 5.0, center_x + 5.0):
                painter.drawLine(QPointF(ex - 2.2, center_y - 4.2), QPointF(ex + 2.2, center_y + 0.2))
                painter.drawLine(QPointF(ex + 2.2, center_y - 4.2), QPointF(ex - 2.2, center_y + 0.2))
            mp = QPainterPath()
            mp.moveTo(center_x - 3.5, center_y + 5.2)
            mp.quadTo(center_x, center_y + 2.5, center_x + 3.5, center_y + 5.2)
            painter.drawPath(mp)

        elif is_cancelled:
            neutral = _qc(pal, 'TEXT_MUTED', '#615D73')
            painter.setPen(QPen(border, 1.2))
            painter.setBrush(_qc(pal, 'SURFACE_SOFT', '#F4F2FA'))
            painter.drawRoundedRect(QRectF(ind_x + 2, ind_y + 2, ind_size - 4, ind_size - 4), 10, 10)
            painter.setPen(QPen(neutral, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(center_x - 5, center_y - 5, 10, 10), 2.0, 2.0)

        else:
            t = elapsed if motion_on else 0.3
            hop = abs(math.sin(t * 7.5)) * 2.8
            tilt = math.sin(t * 3.75) * 5.0

            # 地面小影子
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(primary.red(), primary.green(), primary.blue(), 36))
            painter.drawEllipse(QPointF(center_x, ind_y + ind_size - 1.5), 10.5 - hop * 0.7, 2.4)

            painter.save()
            painter.translate(center_x, center_y + 1.0 - hop)
            painter.rotate(tilt)
            # 迷你小天线
            sway = math.sin(t * 7.5 - 0.6) * 2.8
            painter.setPen(QPen(primary, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(0.0, -11.0), QPointF(sway, -15.5))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#38BDF8'))
            painter.drawEllipse(QPointF(sway, -16.0), 2.1, 2.1)
            # 小棱镜身体
            bg_grad = QLinearGradient(-12, -11, 12, 11)
            bg_grad.setColorAt(0.0, QColor('#8470F2'))
            bg_grad.setColorAt(1.0, primary)
            painter.setBrush(bg_grad)
            painter.drawRoundedRect(QRectF(-12.5, -11.0, 25.0, 22.0), 8.0, 8.0)
            # 白色小脸
            painter.setBrush(QColor('#FCFbff'))
            painter.drawRoundedRect(QRectF(-9.0, -5.5, 18.0, 11.5), 4.5, 4.5)
            # 灵动眼神 + 眨眼
            blink = (t % 3.1) > 2.95
            gaze = math.sin(t * 4.2) * 1.3
            if blink:
                painter.setPen(QPen(QColor('#2A2546'), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(QPointF(-5.5, -0.5), QPointF(-2.5, -0.5))
                painter.drawLine(QPointF(2.5, -0.5), QPointF(5.5, -0.5))
            else:
                painter.setBrush(QColor('#2A2546'))
                painter.drawEllipse(QPointF(-4.0 + gaze, -0.5), 1.6, 1.9)
                painter.drawEllipse(QPointF(4.0 + gaze, -0.5), 1.6, 1.9)
            # 小腮红
            painter.setBrush(QColor(244, 114, 182, 95))
            painter.drawEllipse(QPointF(-6.4, 2.6), 1.7, 1.0)
            painter.drawEllipse(QPointF(6.4, 2.6), 1.7, 1.0)
            painter.restore()

        # 4. 右侧状态徽标 (Status Chip)
        chip_w = 52 if has_progress else 56
        chip_h = 22.0
        chip = QRectF(bounds.right() - chip_w - 11.0, bounds.top() + 12.0, chip_w, chip_h)
        painter.setPen(Qt.PenStyle.NoPen)

        if is_fail:
            painter.setBrush(danger_bg)
            painter.setPen(QPen(danger_border, 1.0))
            chip_fg = danger
            chip_text = 'Failed' if is_en else '卡壳了'
        elif is_finish:
            painter.setBrush(success_bg)
            painter.setPen(QPen(success_border, 1.0))
            chip_fg = success
            chip_text = 'Done' if is_en else '搞定'
        elif is_cancelled:
            neutral = _qc(pal, 'TEXT_MUTED', '#615D73')
            neutral_bg = _qc(pal, 'SURFACE', '#F7F7FA')
            painter.setBrush(neutral_bg)
            painter.setPen(QPen(border, 1.0))
            chip_fg = neutral
            chip_text = self._cancel_chip_label()
        elif has_progress:
            painter.setBrush(primary_soft)
            painter.setPen(QPen(border, 1.0))
            chip_fg = primary
            chip_text = f'{self._value}%'
        else:
            painter.setBrush(primary_soft)
            painter.setPen(QPen(border, 1.0))
            chip_fg = primary
            chip_text = 'Busy' if is_en else '搬砖中'

        painter.drawRoundedRect(chip, 10, 10)
        painter.setPen(chip_fg)
        painter.setFont(QFont('Microsoft YaHei UI', 8, QFont.Weight.Bold))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)

        # 5. 主文案 + 趣味场景副文案（两行排版）
        label_x = ind_x + ind_size + 10.0
        label_w = max(0.0, chip.left() - label_x - 8.0)
        title_rect = QRectF(label_x, bounds.top() + 10.0, label_w, 20.0)
        painter.setPen(text_strong)
        title_font = QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        elided_title = painter.fontMetrics().elidedText(self._label, Qt.TextElideMode.ElideRight, int(label_w))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_title)

        if is_finish:
            sub_text = 'All done!' if is_en else '顺顺利利搞定啦'
        elif is_fail:
            sub_text = 'Check settings and retry' if is_en else '稍微卡壳了，检查一下再试'
        elif is_cancelled:
            sub_text = 'Stopped cleanly' if is_en else '已按要求及时刹车'
        else:
            sub_text = _pick_context_quip(self._label, elapsed, is_en)
        sub_w = max(0.0, bounds.right() - label_x - 14.0)
        sub_rect = QRectF(label_x, bounds.top() + 31.0, sub_w, 18.0)
        painter.setPen(text_muted)
        sub_font = QFont('Microsoft YaHei UI', 8)
        painter.setFont(sub_font)
        elided_sub = painter.fontMetrics().elidedText(sub_text, Qt.TextElideMode.ElideRight, int(sub_w))
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_sub)

        # 6. 底部纤细进度轨（有真实百分比显示实进度，忙碌无百分比显示灵动往返极光光带）
        if not is_fail and not is_cancelled:
            track_rect = QRectF(label_x, bounds.bottom() - 10.0, bounds.right() - label_x - 14.0, 3.5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track_color)
            painter.drawRoundedRect(track_rect, 1.8, 1.8)

            clip_p = QPainterPath()
            clip_p.addRoundedRect(track_rect, 1.8, 1.8)
            painter.save()
            painter.setClipPath(clip_p)
            if is_finish:
                painter.setBrush(success)
                painter.drawRect(track_rect)
            elif has_progress:
                val_pct = max(0.0, min(1.0, self._value / 100.0))
                fill_rect = QRectF(track_rect.left(), track_rect.top(), track_rect.width() * val_pct, track_rect.height())
                fill_grad = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
                fill_grad.setColorAt(0.0, _qc(pal, 'PRIMARY_GRAD_START', primary))
                fill_grad.setColorAt(1.0, _qc(pal, 'PRIMARY_GRAD_END', primary))
                painter.setBrush(fill_grad)
                painter.drawRoundedRect(fill_rect, 1.8, 1.8)
            else:
                # 柔滑往返光带（1.4s 周期，一眼感知仍在流畅运转）
                phase = (elapsed % 1.4) / 1.4 if motion_on else 0.35
                seg_w = max(44.0, track_rect.width() * 0.34)
                travel = track_rect.width() + seg_w
                seg_x = track_rect.left() - seg_w + travel * phase
                wave_grad = QLinearGradient(seg_x, track_rect.top(), seg_x + seg_w, track_rect.top())
                wave_grad.setColorAt(0.0, QColor(primary.red(), primary.green(), primary.blue(), 0))
                wave_grad.setColorAt(0.5, primary)
                wave_grad.setColorAt(1.0, QColor(primary.red(), primary.green(), primary.blue(), 0))
                painter.setBrush(wave_grad)
                painter.drawRect(QRectF(seg_x, track_rect.top(), seg_w, track_rect.height()))
            painter.restore()
