# -*- coding: utf-8 -*-
"""企业级浮层 Loading：与布局隔离，API 保持 start_busy / set_progress / finish / fail。

使用约定：
- 作为 parent 子控件创建（不进 layout），由 place_overlay() 居中浮于宿主上方
- 仅长任务触发；成功 / 失败 / 异常均需 finish 或 fail
- 标签优先写任务语义（「正在提交 SVN…」），不要只写「请稍候」
- 颜色一律来自 ThemeManager，禁止硬编码浅色白卡
"""

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._value = -1
        self._label = ''
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(62)
        # 仅作视觉反馈：不拦截鼠标，避免「Loading 盖住界面 → 点什么都没反应」
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 浮层默认不占布局；hide 时也不会把按钮顶上/顶下
        self.hide()

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

    def start_busy(self, label):
        self._label = label or ''
        self._value = -1
        self._phase = 0
        self.place_overlay()
        self.show()
        self.raise_()
        self._timer.start(28)
        self.update()
        # 同步长任务前给界面一次绘制机会（调用方若 processEvents 更稳）
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass

    def set_progress(self, value, label=None):
        self._value = max(0, min(100, int(value)))
        if label is not None:
            self._label = label
        self.place_overlay()
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start(28)
        self.update()

    def finish(self, label):
        self._label = label or ''
        self._value = 100
        self.place_overlay()
        self.show()
        self.raise_()
        self._timer.start(28)
        self.update()
        # 缩短停留，减少遮挡感（不挡鼠标，但仍尽快消失）
        QTimer.singleShot(600, self._fade_out)

    def fail(self, label):
        self._label = label or ''
        self._value = 0
        self._timer.stop()
        self.place_overlay()
        self.show()
        self.raise_()
        self.update()
        QTimer.singleShot(3600, self._fade_out_failed)

    def _fade_out(self):
        if self._value == 100:
            self._timer.stop()
            self.hide()

    def _fade_out_failed(self):
        if self._value == 0 and not self._timer.isActive():
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

        # left brand bar (primary, not neon blue)
        accent = QRectF(bounds.left() + 2, bounds.top() + 12, 3.5, bounds.height() - 24)
        painter.setPen(Qt.PenStyle.NoPen)
        accent_grad = QLinearGradient(accent.topLeft(), accent.bottomLeft())
        accent_grad.setColorAt(0.0, primary)
        accent_grad.setColorAt(1.0, _qc(pal, 'PRIMARY_ACTIVE', primary))
        painter.setBrush(accent_grad)
        painter.drawRoundedRect(accent, 2, 2)

        # status chip
        is_fail = self._value == 0 and not self._timer.isActive()
        chip_w = 54 if self._value >= 0 else 62
        chip = QRectF(bounds.right() - chip_w - 12, bounds.top() + 10, chip_w, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        if is_fail:
            painter.setBrush(danger_bg)
            painter.setPen(QPen(danger_border, 1))
            chip_fg = danger
        elif self._value >= 100:
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
        elif self._value >= 100:
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
