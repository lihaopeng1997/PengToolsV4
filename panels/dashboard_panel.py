# -*- coding: utf-8 -*-
"""首页工作台 — 最近需求 + 待升级事项 + 紧凑常用工具。"""

from __future__ import annotations

from ui.navigation_model import get_dashboard_quick_tools, get_nav_item

import datetime
import json
import os

from PyQt6.QtCore import QEvent, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QLabel, QMenu, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
    QHBoxLayout, QBoxLayout, QScrollArea, QGridLayout, QProgressBar,
)

from config import DASHBOARD_RELEASE_ITEMS_FILE, REQUIREMENTS_FILE
from tools.dashboard_release_items import (
    collect_release_months,
    effective_release_month,
    is_board_item_completed,
    load_release_board,
    release_display_state,
    save_release_board,
    valid_iso_date,
)
from tools.dashboard_summary import build_dashboard_summary
from tools.requirements import load_requirements, systems_display_text, test_points_button_text
from ui.design_system import apply_button
from ui.icons import apply_icon, icon_pixmap, resource_path
from ui.page_chrome import make_page_header
from ui.responsive import set_subtitle_visible
from ui.motion import motion_enabled


try:
    from PyQt6.sip import isdeleted as _sip_isdeleted
except ImportError:
    try:
        import sip
        _sip_isdeleted = sip.isdeleted
    except ImportError:
        _sip_isdeleted = None


def _is_alive(obj) -> bool:
    if obj is None:
        return False
    if _sip_isdeleted is not None:
        try:
            if _sip_isdeleted(obj):
                return False
        except Exception:
            return False
    return True


def _parse_date(text: str):
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


class SectionHeader(QFrame):
    """列表分区标题；可折叠时点击切换。"""

    toggled = pyqtSignal()

    def __init__(self, title: str, *, collapsible: bool = False, collapsed: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName('dashboard-section-header')
        self.setProperty('collapsible', bool(collapsible))
        self._collapsible = collapsible
        self._collapsed = collapsed
        self._title = title
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        self.chevron = QLabel()
        self.chevron.setObjectName('dashboard-section-chevron')
        self.chevron.setVisible(collapsible)
        layout.addWidget(self.chevron)
        self.title_label = QLabel(title)
        self.title_label.setObjectName('dashboard-section-title')
        layout.addWidget(self.title_label, 1)
        self.setFixedHeight(28)
        if collapsible:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._sync_chevron()
            style = self.style()
            if style is not None:
                style.unpolish(self)
                style.polish(self)

    def _sync_chevron(self):
        if not self._collapsible:
            return
        self.chevron.setText('▶' if self._collapsed else '▼')
        self.chevron.setToolTip('点击展开此分区' if self._collapsed else '点击收起此分区')

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._sync_chevron()

    def mouseReleaseEvent(self, event):
        if self._collapsible and event.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit()
        super().mouseReleaseEvent(event)


class TaskRow(QFrame):
    """列表中的一条可点击任务。"""

    clicked = pyqtSignal(object)

    ROW_HEIGHT = 64
    LIST_SPACING = 4

    def __init__(
        self,
        payload,
        title,
        meta,
        status='',
        *,
        identifier='',
        fixed_height=None,
        highlight: bool = False,
        done: bool = False,
        actions=(),
    ):
        super().__init__()
        self._payload = payload
        if done:
            self.setObjectName('dashboard-task-row-done')
        elif highlight:
            self.setObjectName('dashboard-task-row-today')
        else:
            self.setObjectName('dashboard-task-row')
        self.setProperty('todayRelease', bool(highlight and not done))
        self.setProperty('releaseDone', bool(done))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if fixed_height is not None:
            self.setFixedHeight(fixed_height)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        mark = QLabel('✓' if done else '○')
        mark.setObjectName('dashboard-task-mark-done' if done else 'dashboard-task-mark')
        mark.setFixedWidth(14)
        layout.addWidget(mark, 0)
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self.identifier_label = QLabel(identifier)
        self.identifier_label.setObjectName('dashboard-task-identifier')
        self.identifier_label.setVisible(bool(identifier))
        title_row.addWidget(self.identifier_label, 0)
        self._full_title = str(title or '')
        self.title_label = QLabel(self._full_title)
        self.title_label.setObjectName('dashboard-task-title')
        self.title_label.setWordWrap(False)
        self.title_label.setToolTip(self._full_title)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        title_row.addWidget(self.title_label, 1)
        body.addLayout(title_row)
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName('small-label')
        self.meta_label.setWordWrap(False)
        self.meta_label.setMinimumWidth(0)
        self.meta_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        body.addWidget(self.meta_label)
        layout.addLayout(body, 1)
        self.status_label = QLabel(status)
        if done:
            self.status_label.setObjectName('status-pill-done')
        elif highlight:
            self.status_label.setObjectName('status-pill-today')
        else:
            self.status_label.setObjectName('status-pill')
        self.status_label.setVisible(bool(status))
        layout.addWidget(self.status_label)
        self.action_buttons = []
        for text, callback in actions:
            action = QPushButton(text)
            apply_button(action, 'ghost', compact=True)
            action.clicked.connect(callback)
            self.action_buttons.append(action)
            layout.addWidget(action)
        arrow = QLabel('›')
        arrow.setObjectName('dashboard-row-arrow')
        layout.addWidget(arrow)

    def _update_title_elision(self):
        if not _is_alive(self):
            return
        if not hasattr(self, 'title_label') or not _is_alive(self.title_label):
            return
        available = self.title_label.width()
        if available > 0:
            elided = self.title_label.fontMetrics().elidedText(
                self._full_title,
                Qt.TextElideMode.ElideRight,
                available,
            )
            self.title_label.setText(elided)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_title_elision()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._update_title_elision)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint()) if hasattr(event, 'position') else None
            current = widget
            while current is not None and current is not self:
                if isinstance(current, QPushButton):
                    super().mouseReleaseEvent(event)
                    return
                current = current.parentWidget()
            self.clicked.emit(self._payload)
        super().mouseReleaseEvent(event)



def _load_daily_quotes() -> list[dict]:
    """从 resources/ui/daily-quotes.json 读取 12 条公版经典句，唯一真实数据源。"""
    try:
        path = resource_path('resources', 'ui', 'daily-quotes.json')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
    except Exception:
        pass
    return []


class PrismOrbWidget(QWidget):
    """120x120 晴空棱镜装饰图形；启用动效时由单一 QVariantAnimation 驱动小幅浮动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 120)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._offset_y = 0.0
        self._angle = 0.0
        self._scale = 1.0
        self._anim = None
        if motion_enabled():
            self._init_animation()

    def _init_animation(self):
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(4800)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._anim is not None and self._anim.state() == self._anim.State.Running:
            self._anim.pause()

    def showEvent(self, event):
        super().showEvent(event)
        if motion_enabled():
            if self._anim is None:
                self._init_animation()
            elif self._anim.state() == self._anim.State.Paused:
                self._anim.resume()
            elif self._anim.state() == self._anim.State.Stopped:
                self._anim.start()

    def _on_anim_value(self, val: float):
        import math
        rad = val * 2.0 * math.pi
        self._offset_y = -2.5 * (1.0 - math.cos(rad))
        self._angle = 5.0 * math.sin(rad)
        self._scale = 1.0 + 0.02 * (1.0 - math.cos(rad))
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = 60.0, 60.0 + self._offset_y
        painter.translate(cx, cy)
        painter.rotate(self._angle)
        painter.scale(self._scale, self._scale)

        # 外环 1
        painter.setPen(QPen(QColor(183, 161, 228, 140), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(-48, -44, 96, 88)

        # 外环 2
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.0))
        painter.drawEllipse(-44, -48, 88, 96)

        # 中心棱镜宝石
        grad = QLinearGradient(-26, -26, 26, 26)
        grad.setColorAt(0.0, QColor(255, 255, 255, 210))
        grad.setColorAt(1.0, QColor(198, 175, 248, 130))
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
        painter.drawRoundedRect(-28, -28, 56, 56, 16, 16)
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPolygonF
        painter.setPen(QPen(QColor(140, 116, 199), 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([QPointF(0, -13), QPointF(4, -4), QPointF(13, 0),
            QPointF(4, 4), QPointF(0, 13), QPointF(-4, 4), QPointF(-13, 0), QPointF(-4, -4)]))
        painter.end()


class DashboardPanel(QWidget):
    navigate_requested = pyqtSignal(int)
    open_credit = pyqtSignal()
    open_sql = pyqtSignal()
    open_docx = pyqtSignal()
    open_vin = pyqtSignal()
    open_gateway = pyqtSignal()
    open_ops = pyqtSignal()
    open_ai_workbench = pyqtSignal()
    open_requirements = pyqtSignal()
    create_requirement = pyqtSignal()
    open_requirement = pyqtSignal(object)  # 具体需求 dict 或 id
    requirements_updated = pyqtSignal()  # 工作台改了需求台账（标记上线/恢复待办）

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._mode = 'standard'
        self._completed_section_collapsed = True
        self._completed_header = None
        self._completed_rows = []
        # 数据源 mtime 指纹：切回主页时若未变则跳过全量 rebuild
        self._source_stamp = None
        self._pending_show_refresh = False
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(12)
        layout = self._root

        self.local_status = QLabel()
        self.local_status.setObjectName('dashboard-local-status')
        header, self.title, self.subtitle = make_page_header(
            '工作台',
            '今天先处理最近的交付事项',
            'home',
            trailing=self.local_status,
            show_home=False,
        )
        layout.addWidget(header)

        # 12 条公版经典句唯一数据源加载与本地 UTC 序数轮换 (V2.0 规范 7.3 & 11)
        self._quotes = _load_daily_quotes()
        self._quote_offset = 0

        # Hero 卡片：最小176px高度，包含问候、日期、每日经典句、换一句与 120x120 装饰图形
        self.hero_card = QFrame()
        self.hero_card.setObjectName('dashboard-hero-card')
        self.hero_card.setMinimumHeight(176)
        hero_layout = QHBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(24, 18, 24, 18)
        hero_layout.setSpacing(16)

        left_hero = QVBoxLayout()
        left_hero.setContentsMargins(0, 0, 0, 0)
        left_hero.setSpacing(5)

        self.hero_eyebrow = QLabel('YOUR NEXT MOVE / PRISM WORKSPACE')
        self.hero_eyebrow.setObjectName('hero-eyebrow')
        left_hero.addWidget(self.hero_eyebrow)

        self.hero_title = QLabel('让每个想法，轻盈落地。')
        self.hero_title.setObjectName('hero-title')
        left_hero.addWidget(self.hero_title)

        # 每日经典句行：日期 · 诗文 + 28x28 换一句按钮
        daily_row = QHBoxLayout()
        daily_row.setContentsMargins(0, 0, 0, 0)
        daily_row.setSpacing(6)

        self.quote_date_lbl = QLabel()
        self.quote_date_lbl.setObjectName('quote-date-label')
        daily_row.addWidget(self.quote_date_lbl)

        self.quote_text_lbl = QLabel()
        self.quote_text_lbl.setObjectName('quote-text-label')
        self.quote_text_lbl.setWordWrap(True)
        self.quote_text_lbl.setMinimumWidth(0)
        daily_row.addWidget(self.quote_text_lbl, 1)

        self.quote_refresh_btn = QToolButton()
        self.quote_refresh_btn.setObjectName('quote-refresh-btn')
        self.quote_refresh_btn.setFixedSize(28, 28)
        apply_icon(self.quote_refresh_btn, 'refresh', 14)
        self.quote_refresh_btn.setToolTip('换一句经典诗文')
        self.quote_refresh_btn.clicked.connect(self.next_quote)
        daily_row.addWidget(self.quote_refresh_btn)

        left_hero.addLayout(daily_row)

        # 快捷动作
        hero_acts = QHBoxLayout()
        hero_acts.setContentsMargins(0, 4, 0, 0)
        hero_acts.setSpacing(8)
        self.hero_create_req = QPushButton('新建需求')
        apply_button(self.hero_create_req, 'primary', compact=True, icon='add')
        self.hero_create_req.clicked.connect(self.create_requirement.emit)
        hero_acts.addWidget(self.hero_create_req)
        self.hero_daily = QPushButton('写日报')
        apply_button(self.hero_daily, 'ghost', compact=True, icon='daily-report')
        self.hero_daily.clicked.connect(lambda: self.navigate_requested.emit(9))
        hero_acts.addWidget(self.hero_daily)
        hero_acts.addStretch(1)
        left_hero.addLayout(hero_acts)

        hero_layout.addLayout(left_hero, 1)

        self.prism_orb = PrismOrbWidget(self.hero_card)
        hero_layout.addWidget(self.prism_orb, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.hero_card)

        # 60s 跨日轮换定时器
        self._quote_timer = QTimer(self)
        self._quote_timer.setInterval(60000)
        self._quote_timer.timeout.connect(self._update_daily_quote)
        self._quote_timer.start()

        self._update_daily_quote()

        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(8)
        self.stat_todo = QLabel()
        self.stat_daily = QLabel()
        self.stat_countdown = QLabel()
        self.stat_release = QLabel()
        for lbl in (self.stat_todo, self.stat_daily, self.stat_countdown, self.stat_release):
            lbl.setObjectName('dashboard-stat-chip')
            lbl.setWordWrap(True)
            self.stats_row.addWidget(lbl, 1)
        layout.addLayout(self.stats_row)

        # 两列任务卡撑满中间；任务增多只在列表内滚动，常用工具钉在底部
        self.tasks_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.tasks_row.setSpacing(12)

        self.recent_card = QFrame()
        self.recent_card.setObjectName('dashboard-task-card')
        # 自然高度：少任务收缩，多任务滚动；双卡再对齐底边
        self.recent_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        recent_layout = QVBoxLayout(self.recent_card)
        recent_layout.setContentsMargins(14, 12, 14, 12)
        recent_layout.setSpacing(8)
        recent_head = QHBoxLayout()
        self.recent_title = QLabel()
        self.recent_title.setObjectName('zone-title')
        recent_head.addWidget(self.recent_title)
        recent_head.addStretch(1)
        self.recent_more = QPushButton()
        apply_button(self.recent_more, 'ghost', compact=True)
        self.recent_more.clicked.connect(self.open_requirements.emit)
        recent_head.addWidget(self.recent_more)
        recent_layout.addLayout(recent_head)
        self.recent_scroll = QScrollArea()
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.recent_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.recent_list_host = QWidget()
        self.recent_list = QVBoxLayout(self.recent_list_host)
        self.recent_list.setContentsMargins(0, 0, 4, 0)
        self.recent_list.setSpacing(TaskRow.LIST_SPACING)
        self.recent_empty = QLabel()
        self.recent_empty.setObjectName('field-hint')
        self.recent_empty.setWordWrap(True)
        self.recent_list.addWidget(self.recent_empty)
        self.recent_list.addStretch(1)
        self.recent_scroll.setWidget(self.recent_list_host)
        recent_layout.addWidget(self.recent_scroll, 1)
        self.tasks_row.addWidget(self.recent_card, 1)
        # Keep legacy references/signals for compatibility; the home task surface is monthly only.
        self.recent_card.hide()

        self.release_card = QFrame()
        self.release_card.setObjectName('dashboard-task-card')
        self.release_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        release_layout = QVBoxLayout(self.release_card)
        release_layout.setContentsMargins(14, 12, 14, 12)
        release_layout.setSpacing(8)
        release_head = QHBoxLayout()
        self.release_title = QLabel()
        self.release_title.setObjectName('zone-title')
        release_head.addWidget(self.release_title)
        release_head.addStretch(1)
        self.release_more = QPushButton()
        apply_button(self.release_more, 'ghost', compact=True)
        self.release_more.clicked.connect(self.open_sql.emit)
        release_head.addWidget(self.release_more)
        self.release_month_combo = QComboBox()
        self.release_month_combo.setObjectName('release-month-filter')
        self.release_month_combo.setMinimumWidth(116)
        # 不可直接 connect(self.refresh)：Qt 会把 index 当成 preferred_release_month
        self.release_month_combo.currentIndexChanged.connect(self._on_release_month_changed)
        release_head.addWidget(self.release_month_combo)
        release_layout.addLayout(release_head)
        self.release_summary = QLabel()
        self.release_summary.setObjectName('field-hint')
        self.release_summary.setWordWrap(False)
        release_layout.addWidget(self.release_summary)
        target_row = QHBoxLayout()
        self.release_target_edit = QLabel()
        self.release_target_edit.setObjectName('field-hint')
        target_row.addWidget(self.release_target_edit, 1)
        self.release_target_clear = QPushButton()
        apply_button(self.release_target_clear, 'ghost', compact=True)
        self.release_target_clear.clicked.connect(self._clear_release_target)
        target_row.addWidget(self.release_target_clear)
        release_layout.addLayout(target_row)
        self.release_scroll = QScrollArea()
        self.release_scroll.setWidgetResizable(True)
        self.release_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.release_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.release_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.release_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.release_list_host = QWidget()
        self.release_list = QVBoxLayout(self.release_list_host)
        self.release_list.setContentsMargins(0, 0, 4, 0)
        self.release_list.setSpacing(TaskRow.LIST_SPACING)
        self.release_empty = QLabel()
        self.release_empty.setObjectName('field-hint')
        self.release_empty.setWordWrap(True)
        self.release_list.addWidget(self.release_empty)
        self.release_list.addStretch(1)
        self.release_scroll.setWidget(self.release_list_host)
        release_layout.addWidget(self.release_scroll, 1)
        self.tasks_row.addWidget(self.release_card, 1)
        # 双卡占满中间；常用工具钉在页面最底部
        layout.addLayout(self.tasks_row, 1)
        self._apply_list_geometry()

        # 常用工具：固定底部
        tools_head = QHBoxLayout()
        self.tools_label = QLabel()
        self.tools_label.setObjectName('sidebar-section')
        tools_head.addWidget(self.tools_label)
        tools_head.addStretch(1)
        layout.addLayout(tools_head, 0)

        self.tools_row = QHBoxLayout()
        self.tools_row.setSpacing(8)
        self.gateway = QPushButton()
        self.credit = QPushButton()
        self.docx = QPushButton()
        self.vin = QPushButton()
        self.ops = QPushButton()
        self.ai_workbench = QPushButton()
        self._tool_buttons = []
        for btn, icon, signal in (
            (self.ai_workbench, 'database', self.open_ai_workbench),
            (self.gateway, 'shield-key', self.open_gateway),
            (self.credit, 'document-id', self.open_credit),
            (self.docx, 'doc-update', self.open_docx),
            (self.vin, 'vin', self.open_vin),
            (self.ops, 'operations', self.open_ops),
        ):
            apply_button(btn, 'secondary', compact=True, icon=icon, icon_size=16)
            btn.clicked.connect(signal.emit)
            self.tools_row.addWidget(btn)
            self._tool_buttons.append(btn)
        self.tools_more = QToolButton()
        self.tools_more.setObjectName('responsive-more-btn')
        self.tools_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_more.setText('更多工具')
        apply_icon(self.tools_more, 'more', 16)
        self._tools_menu = QMenu(self.tools_more)
        self.tools_more.setMenu(self._tools_menu)
        self.tools_more.hide()
        self.tools_row.addWidget(self.tools_more)
        self.tools_row.addStretch(1)
        layout.addLayout(self.tools_row, 0)

        # 兼容旧属性，避免外部引用崩溃
        self.offline = self.local_status
        self.hint = QLabel()
        self.hint.hide()
        self.req_card = self.recent_card
        self.sql = self.release_card

        self._assemble_prism_layout(header)

        # set_language 末尾会 refresh 一次；勿再重复 rebuild
        self.set_language(language)

    def _assemble_prism_layout(self, header):
        # Reparent existing editors/actions; task data, signals and persistence stay intact.
        while self._root.count():
            self._root.takeAt(0)
        for button in self._tool_buttons:
            button.hide()
        self.tools_more.hide()
        self.recent_card.hide()
        self.home_scroll = QScrollArea()
        self.home_scroll.setWidgetResizable(True)
        self.home_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.home_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 8)
        body.setSpacing(16)
        body.addWidget(header)
        self.home_columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.home_columns.setSpacing(16)
        body.addLayout(self.home_columns)
        self.home_left = QWidget()
        left = QVBoxLayout(self.home_left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(16)
        left.addWidget(self.hero_card)
        self.stat_grid = QGridLayout()
        self.stat_grid.setSpacing(12)
        left.addLayout(self.stat_grid)
        left.addWidget(self.release_card)
        left.addStretch(1)
        self.home_columns.addWidget(self.home_left, 1)
        self.home_right = QWidget()
        right = QVBoxLayout(self.home_right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)
        overview = QFrame()
        overview.setObjectName('dashboard-overview-card')
        overview.setMinimumHeight(300)
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(20, 20, 20, 20)
        overview_layout.setSpacing(16)
        self.overview_title = QLabel('上线总览')
        self.overview_title.setObjectName('zone-title')
        self.overview_date = QLabel()
        self.overview_date.setWordWrap(True)
        self.overview_value = QLabel()
        self.overview_value.setObjectName('dashboard-overview-value')
        self.overview_progress = QProgressBar()
        self.overview_progress.setRange(0, 100)
        self.overview_progress.setTextVisible(False)
        self.overview_progress.setFixedHeight(6)
        self.overview_note = QLabel()
        self.overview_note.setWordWrap(True)
        for widget in (self.overview_title, self.overview_date, self.overview_value,
                       self.overview_progress, self.overview_note):
            overview_layout.addWidget(widget)
        overview_layout.addStretch(1)
        right.addWidget(overview)
        tools_card = QFrame()
        tools_card.setObjectName('dashboard-overview-card')
        tools_layout = QVBoxLayout(tools_card)
        tools_layout.setContentsMargins(16, 16, 16, 16)
        tools_layout.setSpacing(12)
        tools_layout.addWidget(self.tools_label)
        tools_grid = QGridLayout()
        tools_grid.setSpacing(8)
        self.prism_tool_buttons = []
        for index, item in enumerate(get_dashboard_quick_tools()):
            btn = QPushButton(item['zh'])
            btn.setToolTip(item['ds'])
            apply_button(btn, 'secondary', icon=item['icon'], icon_size=18)
            btn.setObjectName('dashboard-quick-tool')
            btn.setMinimumHeight(64)
            btn.setMinimumWidth(0)
            btn.clicked.connect(lambda checked=False, nav=item['i']: self.navigate_requested.emit(nav))
            tools_grid.addWidget(btn, index // 2, index % 2)
            self.prism_tool_buttons.append(btn)
        tools_layout.addLayout(tools_grid)
        right.addWidget(tools_card)
        right.addStretch(1)
        self.home_columns.addWidget(self.home_right)
        body.addStretch(1)
        self.home_scroll.setWidget(host)
        self.home_scroll.viewport().installEventFilter(self)
        self._root.addWidget(self.home_scroll)
        self._layout_prism_columns()

    def _layout_prism_columns(self):
        if not hasattr(self, 'home_columns'):
            return
        width = self.home_scroll.viewport().width()
        stacked = width < 1020
        self.home_columns.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.home_right.setMinimumWidth(0 if stacked else (330 if width >= 1200 else 300))
        self.home_right.setMaximumWidth(16777215 if stacked else (330 if width >= 1200 else 300))
        left_width = width if stacked else width - self.home_right.minimumWidth() - 16
        columns = 2 if left_width < 700 else 4
        for i, label in enumerate((self.stat_todo, self.stat_daily, self.stat_countdown, self.stat_release)):
            self.stat_grid.addWidget(label, i // columns, i % columns)
            label.setMinimumHeight(108)
            label.setMinimumWidth(0)
        for i in range(4):
            self.stat_grid.setColumnStretch(i, 1 if i < columns else 0)

    def eventFilter(self, watched, event):
        if hasattr(self, 'home_scroll') and watched is self.home_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._layout_prism_columns()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_prism_columns()

    def apply_layout_mode(self, mode, low_height=False):
        self._mode = mode
        set_subtitle_visible(self.subtitle, low_height)
        self._root.setSpacing(16)
        self._layout_prism_columns()
        self._apply_list_geometry()

    def _list_limit(self) -> int:
        """各布局模式下列表最大可见行数（超出再滚动）。"""
        if self._mode == 'narrow':
            return 4
        if self._mode == 'compact':
            return 5
        return 8

    def _scroll_height_for_count(self, count: int) -> int:
        """按任务行数计算列表视口高度：0 条给空态高度，否则 min(n, max_rows)*64。"""
        if count <= 0:
            return 40
        visible = min(int(count), self._list_limit())
        return visible * TaskRow.ROW_HEIGHT + max(0, visible - 1) * TaskRow.LIST_SPACING

    @staticmethod
    def _count_task_rows(layout) -> int:
        total = 0
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is not None and hasattr(widget, '_payload'):
                total += 1
        return total

    def _apply_list_geometry(self):
        """Show short task lists naturally; long lists scroll within the card."""
        for scroll, rows in ((self.recent_scroll, self.recent_list),
                             (self.release_scroll, self.release_list)):
            floor = max(96, min(280, self._scroll_height_for_count(self._count_task_rows(rows))))
            scroll.setMinimumHeight(floor)
            scroll.setMaximumHeight(440)
            scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for card in (self.recent_card, self.release_card):
            card.setMinimumHeight(0)
            card.setMaximumHeight(16777215)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    @staticmethod
    def _file_mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _current_source_stamp(self):
        """需求台账 + 待升级看板 的磁盘指纹。"""
        return (
            self._file_mtime(REQUIREMENTS_FILE),
            self._file_mtime(DASHBOARD_RELEASE_ITEMS_FILE),
        )

    def _sources_changed(self) -> bool:
        if self._source_stamp is None:
            return True
        return self._current_source_stamp() != self._source_stamp

    def showEvent(self, event):
        super().showEvent(event)
        # 切回主页：数据未变则直接展示已有列表，避免每次读盘+销毁重建 TaskRow
        if not self._sources_changed():
            return
        if self._pending_show_refresh:
            return
        self._pending_show_refresh = True
        # 先让面板切出再异步刷新，减轻「点了导航还要等一会」的体感
        QTimer.singleShot(0, self._refresh_if_stale_after_show)

    def _refresh_if_stale_after_show(self):
        if not _is_alive(self):
            return
        self._pending_show_refresh = False
        if not self.isVisible():
            return
        if self._sources_changed():
            self.refresh()

    def _update_daily_quote(self):
        """按本地日历计算 UTC day ordinal 并索引 12 条经典句。"""
        if not hasattr(self, '_quotes') or not self._quotes:
            return
        today = datetime.date.today()
        epoch = datetime.date(1970, 1, 1)
        day_ordinal = (today - epoch).days
        idx = ((day_ordinal + self._quote_offset) % len(self._quotes) + len(self._quotes)) % len(self._quotes)
        quote = self._quotes[idx]
        weekdays = ['一', '二', '三', '四', '五', '六', '日']
        w = weekdays[today.weekday()]
        self.quote_date_lbl.setText(f"{today.month} 月 {today.day} 日，星期{w} · ")
        self.quote_text_lbl.setText(f"“{quote.get('text', '')}”")
        source = quote.get('source', '') or quote.get('author', '')
        self.quote_text_lbl.setToolTip(source)
        self.quote_refresh_btn.setToolTip(f"换一句 · {source}")

    def next_quote(self):
        """换一句经典诗文，仅递增会话内临时偏移。"""
        self._quote_offset += 1
        self._update_daily_quote()

    def refresh(self, preferred_release_month=None):
        """刷新工作台。preferred 仅在有明确目标月份时传入；普通刷新保留用户有效月份选择。"""
        self.setUpdatesEnabled(False)
        try:
            requirements = load_requirements()
            self._apply_summary(requirements)
            self._fill_recent(requirements)
            self._fill_release(requirements, preferred_release_month=preferred_release_month)
            self._apply_list_geometry()
            self._source_stamp = self._current_source_stamp()
        finally:
            self.setUpdatesEnabled(True)

    def refresh_for_requirement(self, requirement):
        """需求编辑保存后刷新；有有效发版月份则定位。"""
        month = effective_release_month(requirement) if isinstance(requirement, dict) else ''
        self.refresh(preferred_release_month=month or None)

    def _on_release_month_changed(self, *_args):
        """月份切换：只刷新列表并重算高度，不把 combo index 误当成 preferred month。"""
        if self.release_month_combo.signalsBlocked():
            return
        self.setUpdatesEnabled(False)
        try:
            requirements = load_requirements()
            board = load_release_board()
            self._fill_release_items(requirements, board)
            self._apply_list_geometry()
            self._source_stamp = self._current_source_stamp()
        finally:
            self.setUpdatesEnabled(True)

    def _clear_task_rows(self, layout, keep_widgets=()):
        """清掉任务行，保留 empty 标签等常驻控件。"""
        keep = set(keep_widgets)
        for index in range(layout.count() - 1, -1, -1):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None:
                layout.takeAt(index)
                continue
            if widget in keep:
                continue
            layout.takeAt(index)
            widget.deleteLater()

    def _fill_recent(self, requirements):
        from tools.list_pin import decorate_title, is_pinned, pinned_at_rank
        self._clear_task_rows(self.recent_list, keep_widgets=(self.recent_empty,))
        # 去掉末尾 stretch，填充后再加回
        while self.recent_list.count() and self.recent_list.itemAt(self.recent_list.count() - 1).spacerItem():
            self.recent_list.takeAt(self.recent_list.count() - 1)
        pinned = [r for r in requirements if is_pinned(r)]
        plain = [r for r in requirements if not is_pinned(r)]
        pinned.sort(
            key=lambda item: (pinned_at_rank(item), str(item.get('updated_at') or item.get('created_at') or '')),
            reverse=True,
        )
        plain.sort(
            key=lambda item: str(item.get('updated_at') or item.get('created_at') or ''),
            reverse=True,
        )
        # 列表视口固定高度；条数可超过可见槽位，多出部分滚动查看
        # 摘要条数：工作台只作入口，不与需求管理完整目录重复
        items = (pinned + plain)[:8]
        self.recent_empty.setVisible(not items)
        for item in items:
            title = decorate_title(item.get('title') or item.get('code') or '未命名', is_pinned(item))
            system = systems_display_text(item, empty='未选系统')
            status = item.get('status') or ''
            updated = str(item.get('updated_at') or '')[:16].replace('T', ' ')
            meta = f'{system} · {updated}' if updated else system
            if is_pinned(item):
                meta = f'置顶 · {meta}'
            row = TaskRow(
                item, title, meta, status,
                fixed_height=TaskRow.ROW_HEIGHT,
                highlight=is_pinned(item),
            )
            row.clicked.connect(self._on_requirement_clicked)
            self.recent_list.addWidget(row)
        self.recent_list.addStretch(1)

    @staticmethod
    def _release_key(kind, item, month):
        return f"{item.get('id') or ''}@{month}"

    def _fill_release_months(self, requirements, preferred_month=None):
        """月份下拉与列表共用 collect_release_months / release_month_for。"""
        months = collect_release_months(requirements)
        preferred = preferred_month
        if isinstance(preferred, int):
            preferred = None
        preferred = str(preferred)[:7] if preferred else None
        if preferred and preferred not in months:
            preferred = None
        manual = self.release_month_combo.currentData() if hasattr(self, 'release_month_combo') else None
        if isinstance(manual, int):
            manual = None
        manual = str(manual)[:7] if manual else None
        current_month = datetime.date.today().strftime('%Y-%m')
        # 优先级：保存目标月 → 仍有效的手动选择 → 当前自然月 → 最新可用月
        if preferred and preferred in months:
            select = preferred
        elif manual and manual in months:
            select = manual
        elif current_month in months:
            select = current_month
        elif months:
            select = months[0]
        else:
            select = None

        self.release_month_combo.blockSignals(True)
        self.release_month_combo.clear()
        for month in months:
            self.release_month_combo.addItem(month.replace('-', '年', 1) + '月', month)
        if select:
            index = self.release_month_combo.findData(select)
            self.release_month_combo.setCurrentIndex(index if index >= 0 else 0)
        self.release_month_combo.blockSignals(False)

    def _fill_release(self, requirements, preferred_release_month=None):
        """按用户选择月份展示已勾选入选的需求。"""
        board = load_release_board()
        self._fill_release_months(requirements, preferred_month=preferred_release_month)
        self._fill_release_items(requirements, board)

    def _fill_release_items(self, requirements, board=None):
        board = board if board is not None else load_release_board()
        prefs = board.get('ui_prefs') if isinstance(board.get('ui_prefs'), dict) else {}
        self._completed_section_collapsed = bool(prefs.get('completed_section_collapsed', True))
        self._completed_header = None
        self._completed_rows = []
        self._clear_task_rows(self.release_list, keep_widgets=(self.release_empty,))
        while self.release_list.count() and self.release_list.itemAt(self.release_list.count() - 1).spacerItem():
            self.release_list.takeAt(self.release_list.count() - 1)
        month_key = str(self.release_month_combo.currentData() or '')
        zh = self.language == 'zh'
        if not month_key:
            self.release_empty.setVisible(True)
            self.release_summary.setText('待处理 0 · 已完成 0' if zh else 'Open 0 · Done 0')
            self.release_list.addStretch(1)
            return
        pending = []
        done_items = []
        completed_keys = set(board.get('completed_requirement_keys', []) if isinstance(board, dict) else [])
        for item in requirements:
            item_month = effective_release_month(item)
            if not item_month or item_month != month_key:
                continue
            display = release_display_state(item)
            actual_date = valid_iso_date(item.get('actual_release_date')) or valid_iso_date(item.get('actual_online_date'))
            entry = ('requirement', item, _parse_date(actual_date))
            if is_board_item_completed(item, month_key, completed_keys) or display.get('done'):
                done_items.append(entry)
            else:
                pending.append(entry)

        def _sort_key(entry):
            _kind, item, date_value = entry
            return (date_value or datetime.date.max, str(item.get('title') or ''))

        pending.sort(key=_sort_key)
        done_items.sort(key=_sort_key)
        total = len(pending) + len(done_items)
        self.release_empty.setVisible(total == 0)
        if zh:
            self.release_summary.setText(f'待处理 {len(pending)} · 已完成 {len(done_items)}')
            self.release_summary.setToolTip('「已完成」仅记录工作台升级进度，不修改需求业务状态。')
        else:
            self.release_summary.setText(f'Open {len(pending)} · Done {len(done_items)}')
            self.release_summary.setToolTip('Done tracks board progress only; requirement status is unchanged.')

        if pending:
            if done_items:
                self.release_list.addWidget(SectionHeader('待处理' if zh else 'Open'))
            for kind, item, planned_date in pending:
                self.release_list.addWidget(
                    self._build_release_row(kind, item, planned_date, month_key, completed=False)
                )

        if done_items:
            header = SectionHeader(
                f'已完成 ({len(done_items)})' if zh else f'Done ({len(done_items)})',
                collapsible=True,
                collapsed=self._completed_section_collapsed,
            )
            header.toggled.connect(self._toggle_completed_section)
            self.release_list.addWidget(header)
            self._completed_header = header
            for kind, item, planned_date in done_items:
                row = self._build_release_row(kind, item, planned_date, month_key, completed=True)
                row.setVisible(not self._completed_section_collapsed)
                self.release_list.addWidget(row)
                self._completed_rows.append(row)
        self.release_list.addStretch(1)

    def _build_release_row(self, kind, item, planned_date, month_key, *, completed: bool):
        from tools.list_pin import decorate_title, is_pinned
        zh = self.language == 'zh'
        title = item.get('title') or ('未命名' if zh else 'Untitled')
        title = decorate_title(title or item.get('code') or ('未命名' if zh else 'Untitled'), is_pinned(item))
        identifier = str(item.get('code') or '').strip() or str(
            item.get('record_kind') or ('需求' if zh else 'Requirement')
        )
        display = release_display_state(item)
        actual = valid_iso_date(item.get('actual_release_date')) or valid_iso_date(item.get('actual_online_date'))
        system = systems_display_text(item, empty=('未选系统' if zh else 'No system'))
        dates = []
        if actual:
            dates.append(f'实际 {actual}' if zh else f'Actual {actual}')
        progress = test_points_button_text(item.get('test_points'), zh=zh)
        meta = ' · '.join([p for p in (system, progress, *dates) if p])
        status_text = display.get('state') or (item.get('status') or '')
        if completed:
            action = (
                '撤销完成' if zh else 'Undo',
                lambda _checked=False, current=item: self._set_release_item_completed(
                    'requirement', current, month_key, False
                ),
            )
        else:
            action = (
                '已完成' if zh else 'Complete',
                lambda _checked=False, current=item: self._set_release_item_completed(
                    'requirement', current, month_key, True
                ),
            )
        test_action = (
            test_points_button_text(item.get('test_points'), zh=zh),
            lambda _checked=False, current=item: self._open_test_points(current),
        )
        row = TaskRow(
            item, title, meta, status_text,
            identifier=identifier,
            fixed_height=TaskRow.ROW_HEIGHT,
            highlight=is_pinned(item) and not completed,
            done=completed,
            actions=(test_action, action),
        )
        row.clicked.connect(self._on_requirement_clicked)
        if row.action_buttons:
            row.test_points_btn = row.action_buttons[0]
        return row

    def _toggle_completed_section(self):
        """只显隐已完成行，不拆列表、不读盘重建，避免折叠时闪 Loading。"""
        self._completed_section_collapsed = not self._completed_section_collapsed
        header = self._completed_header
        sender = self.sender()
        if isinstance(sender, SectionHeader):
            header = sender
            self._completed_header = sender
        if header is not None:
            header.set_collapsed(self._completed_section_collapsed)
        for row in self._completed_rows:
            row.setVisible(not self._completed_section_collapsed)
        try:
            board = load_release_board()
            prefs = board.setdefault('ui_prefs', {})
            prefs['completed_section_collapsed'] = self._completed_section_collapsed
            save_release_board(board)
            self._source_stamp = self._current_source_stamp()
        except Exception:
            pass

    def _save_release_board(self, board):
        save_release_board(board)

    def _refresh_release_after_action(self):
        """在按钮点击事件返回后刷新，避免事件派发中销毁当前任务行。"""
        # 普通刷新：不传 preferred，保留用户当前月份选择
        def _safe_refresh():
            if not _is_alive(self):
                return
            self.refresh(preferred_release_month=None)
        QTimer.singleShot(0, _safe_refresh)

    def _set_release_item_completed(self, kind, item, month, completed):
        """仅更新工作台独立完成态，不修改需求业务状态/实际上线日期。"""
        board = load_release_board()
        keys = set(board.get('completed_requirement_keys', []))
        key = self._release_key(kind, item, month)
        if completed:
            keys.add(key)
        else:
            keys.discard(key)
        board['completed_requirement_keys'] = sorted(keys)
        self._save_release_board(board)
        self._refresh_release_after_action()

    def _open_test_points(self, item):
        """首页直接维护测试点，不进入完整需求编辑。"""
        from panels.test_points_editor import TestPointsDialog

        current = item if isinstance(item, dict) else {}
        req_id = str(current.get('id') or '')
        if req_id:
            fresh = next(
                (entry for entry in load_requirements() if str(entry.get('id') or '') == req_id),
                None,
            )
            if fresh:
                current = fresh
        dialog = TestPointsDialog(current, parent=self, persist=True)
        dialog.exec()
        if dialog.saved():
            self.requirements_updated.emit()
        self._refresh_release_after_action()

    def _apply_summary(self, requirements):
        summary = build_dashboard_summary(
            language=self.language,
            requirements=requirements,
            board=load_release_board(),
            tools=get_dashboard_quick_tools(),
        )
        stats = summary.get('stats') or {}
        rel = summary.get('release') or {}
        zh = self.language == 'zh'
        self.stat_todo.setText(
            f"{'待办' if zh else 'Open'}\n{stats.get('req_open') or 0}"
        )
        self.stat_daily.setText(
            f"{'日报' if zh else 'Daily'}\n{stats.get('daily_done') or 0}/{stats.get('daily_total') or 5}\n{stats.get('daily_note') or ''}"
        )
        days = rel.get('days_left')
        if rel.get('countdown_state') == 'unset' or days is None:
            count_text = '–'
        elif days < 0:
            count_text = rel.get('date_text') or f'D{days}'
        else:
            count_text = f'D-{days}'
        self.stat_countdown.setText(
            f"{'发版倒计时' if zh else 'Countdown'}\n{count_text}\n{rel.get('date_text') or ''}"
        )
        self.stat_release.setText(
            f"{'发版清单' if zh else 'Release'}\n{rel.get('total') or 0}\n{'已完成' if zh else 'Done'} {rel.get('done') or 0}"
        )
        from html import escape
        for label in (self.stat_todo, self.stat_daily, self.stat_countdown, self.stat_release):
            plain = label.text()
            lines = plain.split('\n')
            label.setAccessibleName(plain)
            label.setText('<span style="font-size:12px">' + escape(lines[0]) + '</span><br>'
                + '<span style="font-size:30px;font-weight:600">' + escape(lines[1]) + '</span>'
                + ('<br><span style="font-size:12px">' + escape(' '.join(lines[2:])) + '</span>' if len(lines) > 2 else ''))
        total = int(rel.get('total') or 0)
        done = int(rel.get('done') or 0)
        self.overview_date.setText(rel.get('date_text') or '')
        self.overview_value.setText(f'{done} / {total}')
        self.overview_progress.setValue(round(done * 100 / total) if total else 0)
        self.overview_note.setText(('已完成 / 本月任务' if zh else 'Completed / Monthly tasks'))
        target = rel.get('target_date') or ''
        self.release_target_edit.setText(
            (f'发版日 {target}' if target else '发版日：自动（按本月实际上线日期）') if zh
            else (f'Release {target}' if target else 'Release date: auto')
        )

    def _clear_release_target(self):
        board = load_release_board()
        board['release_target_date'] = ''
        save_release_board(board)
        self.refresh(preferred_release_month=None)

    def _on_requirement_clicked(self, item):
        if isinstance(item, dict):
            self.open_requirement.emit(item)
        self.open_requirements.emit()

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.overview_title.setText('上线总览' if zh else 'Release overview')
        self.hero_daily.setText('写日报' if zh else 'Write daily')
        for button, tool in zip(self.prism_tool_buttons, get_dashboard_quick_tools()):
            nav = get_nav_item(tool['i'])
            button.setText(tool['zh'] if zh else nav.name_en)
            button.setToolTip(tool['ds'] if zh else nav.tooltip_en)
        today = datetime.date.today()
        if zh:
            self.title.setText('工作台')
            self.subtitle.setText(f'{today.strftime("%Y-%m-%d")} · 今天先处理最近的交付事项')
            self.local_status.setText('● 本地工作')
            self.recent_title.setText('最近需求（摘要）')
            self.recent_more.setText('全部')
            self.recent_empty.setText('暂无需求记录。可在需求管理中新增或扫描目录。')
            self.recent_more.setToolTip('打开需求管理查看完整目录')
            self.release_title.setText('本月上线任务')
            self.release_more.setText('发版联动')
            self.release_month_combo.setToolTip('选择要查看的上线月份')
            self.release_empty.setText('该月份暂无上线任务。填写实际上线日期后会出现在这里。')
            if hasattr(self, 'release_target_clear'):
                self.release_target_clear.setText('清除发版日')
            if hasattr(self, 'release_summary') and not self.release_summary.text():
                self.release_summary.setText('待处理 0 · 已完成 0')
            self.tools_label.setText('常用工具')
            self.gateway.setText('加解密')
            self.credit.setText('证件类型')
            self.docx.setText('接口文档')
            self.vin.setText('车辆 VIN')
            self.ops.setText('运维工作台')
            self.ai_workbench.setText('SQL 控制台')
        else:
            self.title.setText('Workbench')
            self.subtitle.setText(f'{today.strftime("%Y-%m-%d")} · Focus on nearby delivery work')
            self.local_status.setText('● Local')
            self.recent_title.setText('Recent requirements (summary)')
            self.recent_more.setText('All')
            self.recent_empty.setText('No requirements yet. Add or scan in Requirements.')
            self.recent_more.setToolTip('Open Requirements for the full library')
            self.release_title.setText('Monthly upgrade tasks')
            self.release_more.setText('Release prep')
            self.release_month_combo.setToolTip('Choose a release month')
            self.release_empty.setText('No upgrade tasks this month. Set an actual release date to include a task here.')
            if hasattr(self, 'release_target_clear'):
                self.release_target_clear.setText('Clear date')
            if hasattr(self, 'release_summary') and not self.release_summary.text():
                self.release_summary.setText('Open 0 · Done 0')
            self.tools_label.setText('TOOLS')
            self.gateway.setText('Crypto')
            self.credit.setText('Documents')
            self.docx.setText('Interface Docs')
            self.vin.setText('Vehicle VIN')
            self.ops.setText('Ops Workbench')
            self.ai_workbench.setText('SQL Console')
        self.refresh()
