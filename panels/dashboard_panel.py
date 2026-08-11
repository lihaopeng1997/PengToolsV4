# -*- coding: utf-8 -*-
"""首页工作台 — 最近需求 + 待升级事项 + 紧凑常用工具。"""

from __future__ import annotations

import datetime

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QLabel, QMenu, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
    QHBoxLayout, QBoxLayout, QScrollArea,
)

from tools.dashboard_release_items import load_release_board, save_release_board
from tools.requirements import load_requirements
from ui.icons import apply_icon, icon_pixmap
from ui.page_chrome import make_page_header
from ui.responsive import set_subtitle_visible


def _online_date(item: dict) -> str:
    return str(item.get('actual_online_date') or item.get('planned_online_date') or '')[:10]


def _parse_date(text: str):
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _iso_rank(value) -> int:
    """ISO 时间字符串越大越新；无法解析返回 0。"""
    text = str(value or '').strip().replace('-', '').replace('T', '').replace(':', '').replace(' ', '')
    digits = ''.join(ch for ch in text if ch.isdigit())[:14]
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


class TaskRow(QFrame):
    """列表中的一条可点击任务。"""

    clicked = pyqtSignal(object)

    ROW_HEIGHT = 64

    def __init__(self, payload, title, meta, status='', *, identifier='', fixed_height=None, highlight: bool = False, actions=()):
        super().__init__()
        self._payload = payload
        self.setObjectName('dashboard-task-row-today' if highlight else 'dashboard-task-row')
        self.setProperty('todayRelease', bool(highlight))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if fixed_height is not None:
            self.setFixedHeight(fixed_height)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
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
        self.status_label.setObjectName('status-pill-today' if highlight else 'status-pill')
        self.status_label.setVisible(bool(status))
        layout.addWidget(self.status_label)
        for text, callback in actions:
            action = QPushButton(text)
            action.setObjectName('ghost-btn')
            action.setProperty('compactAction', True)
            action.clicked.connect(callback)
            layout.addWidget(action)
        arrow = QLabel('›')
        arrow.setObjectName('dashboard-row-arrow')
        layout.addWidget(arrow)

    def _update_title_elision(self):
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
            self.clicked.emit(self._payload)
        super().mouseReleaseEvent(event)


class DashboardPanel(QWidget):
    open_credit = pyqtSignal()
    open_sql = pyqtSignal()
    open_docx = pyqtSignal()
    open_vin = pyqtSignal()
    open_gateway = pyqtSignal()
    open_ops = pyqtSignal()
    open_requirements = pyqtSignal()
    open_requirement = pyqtSignal(object)  # 具体需求 dict 或 id

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._mode = 'standard'
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
        )
        layout.addWidget(header)

        # 两列任务卡（方向可随模式切换）
        self.tasks_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.tasks_row.setSpacing(12)

        self.recent_card = QFrame()
        self.recent_card.setObjectName('dashboard-task-card')
        self.recent_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        recent_layout = QVBoxLayout(self.recent_card)
        recent_layout.setContentsMargins(14, 12, 14, 12)
        recent_layout.setSpacing(8)
        recent_head = QHBoxLayout()
        self.recent_title = QLabel()
        self.recent_title.setObjectName('zone-title')
        recent_head.addWidget(self.recent_title)
        recent_head.addStretch(1)
        self.recent_more = QPushButton()
        self.recent_more.setObjectName('ghost-btn')
        self.recent_more.setProperty('compactAction', True)
        self.recent_more.clicked.connect(self.open_requirements.emit)
        recent_head.addWidget(self.recent_more)
        recent_layout.addLayout(recent_head)
        self.recent_list = QVBoxLayout()
        self.recent_list.setSpacing(4)
        recent_layout.addLayout(self.recent_list, 1)
        self.recent_empty = QLabel()
        self.recent_empty.setObjectName('field-hint')
        self.recent_empty.setWordWrap(True)
        recent_layout.addWidget(self.recent_empty)
        self.tasks_row.addWidget(self.recent_card, 1)

        self.release_card = QFrame()
        self.release_card.setObjectName('dashboard-task-card')
        self.release_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        release_layout = QVBoxLayout(self.release_card)
        release_layout.setContentsMargins(14, 12, 14, 12)
        release_layout.setSpacing(8)
        release_head = QHBoxLayout()
        self.release_title = QLabel()
        self.release_title.setObjectName('zone-title')
        release_head.addWidget(self.release_title)
        release_head.addStretch(1)
        self.release_more = QPushButton()
        self.release_more.setObjectName('ghost-btn')
        self.release_more.setProperty('compactAction', True)
        self.release_more.clicked.connect(self.open_sql.emit)
        release_head.addWidget(self.release_more)
        self.release_month_combo = QComboBox()
        self.release_month_combo.setObjectName('release-month-filter')
        self.release_month_combo.setMinimumWidth(116)
        self.release_month_combo.currentIndexChanged.connect(self.refresh)
        release_head.addWidget(self.release_month_combo)
        release_layout.addLayout(release_head)
        self.release_scroll = QScrollArea()
        self.release_scroll.setWidgetResizable(True)
        self.release_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.release_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.release_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.release_scroll.setMinimumHeight(0)
        self.release_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.release_list_host = QWidget()
        self.release_list = QVBoxLayout(self.release_list_host)
        self.release_list.setContentsMargins(0, 0, 4, 0)
        self.release_list.setSpacing(4)
        self.release_scroll.setWidget(self.release_list_host)
        release_layout.addWidget(self.release_scroll)
        self.release_empty = QLabel()
        self.release_empty.setObjectName('field-hint')
        self.release_empty.setWordWrap(True)
        release_layout.addWidget(self.release_empty)
        self.tasks_row.addWidget(self.release_card, 1)
        layout.addLayout(self.tasks_row)

        # 常用工具：紧凑图标+文字
        tools_head = QHBoxLayout()
        self.tools_label = QLabel()
        self.tools_label.setObjectName('sidebar-section')
        tools_head.addWidget(self.tools_label)
        tools_head.addStretch(1)
        layout.addLayout(tools_head)

        self.tools_row = QHBoxLayout()
        self.tools_row.setSpacing(8)
        self.gateway = QPushButton()
        self.credit = QPushButton()
        self.docx = QPushButton()
        self.vin = QPushButton()
        self.ops = QPushButton()
        self._tool_buttons = []
        for btn, icon, signal in (
            (self.gateway, 'shield-key', self.open_gateway),
            (self.credit, 'document-id', self.open_credit),
            (self.docx, 'doc-update', self.open_docx),
            (self.vin, 'vin', self.open_vin),
            (self.ops, 'operations', self.open_ops),
        ):
            btn.setObjectName('btn-secondary')
            btn.setProperty('compactAction', True)
            apply_icon(btn, icon, 16)
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
        layout.addLayout(self.tools_row)
        layout.addStretch(0)

        # 兼容旧属性，避免外部引用崩溃
        self.offline = self.local_status
        self.hint = QLabel()
        self.hint.hide()
        self.req_card = self.recent_card
        self.sql = self.release_card

        self.set_language(language)
        self.refresh()

    def apply_layout_mode(self, mode, low_height=False):
        self._mode = mode
        set_subtitle_visible(self.subtitle, low_height)
        # Compact/Narrow：任务卡纵向
        if mode in ('compact', 'narrow'):
            self.tasks_row.setDirection(QBoxLayout.Direction.TopToBottom)
            self.tasks_row.setSpacing(10 if low_height else 12)
        else:
            self.tasks_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.tasks_row.setSpacing(10 if low_height else 14)
        self._root.setSpacing(10 if low_height else 14)
        # 常用工具：Narrow 仅前 4 项，其余进更多
        self._tools_menu.clear()
        zh = self.language == 'zh'
        self.tools_more.setText('更多工具' if zh else 'More tools')
        if mode == 'narrow':
            for i, btn in enumerate(self._tool_buttons):
                if i < 4:
                    btn.show()
                    if btn.text():
                        btn.setToolTip(btn.text())
                else:
                    btn.hide()
                    act = QAction(btn.text() or btn.toolTip() or 'Tool', self)
                    act.triggered.connect(btn.click)
                    self._tools_menu.addAction(act)
            self.tools_more.setVisible(bool(self._tools_menu.actions()))
        else:
            for btn in self._tool_buttons:
                btn.show()
            self.tools_more.hide()
        # 列表条数随模式变化
        self.refresh()

    def _list_limit(self) -> int:
        return 3 if self._mode in ('compact', 'narrow') else 5

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        requirements = load_requirements()
        self._fill_recent(requirements)
        self._fill_release(requirements)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _fill_recent(self, requirements):
        from tools.list_pin import decorate_title, is_pinned, pinned_at_rank
        self._clear_layout(self.recent_list)
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
        items = (pinned + plain)[: self._list_limit()]
        self.recent_empty.setVisible(not items)
        for item in items:
            title = decorate_title(item.get('title') or item.get('code') or '未命名', is_pinned(item))
            system = item.get('system') or '未选系统'
            status = item.get('status') or ''
            updated = str(item.get('updated_at') or '')[:16].replace('T', ' ')
            meta = f'{system} · {updated}' if updated else system
            if is_pinned(item):
                meta = f'置顶 · {meta}'
            row = TaskRow(item, title, meta, status, highlight=is_pinned(item))
            row.clicked.connect(self._on_requirement_clicked)
            self.recent_list.addWidget(row)

    @staticmethod
    def _release_key(kind, item, month):
        return f"{item.get('id') or ''}@{month}" if kind == 'requirement' else f"{item.get('id') or ''}@{month}"

    def _fill_release_months(self, requirements, board):
        current = self.release_month_combo.currentData() if hasattr(self, 'release_month_combo') else None
        months = set()
        months.update(
            str(item.get('online_month') or '')[:7]
            for item in requirements
            if item.get('is_monthly_release') and str(item.get('online_month') or '')[:7]
        )
        months = sorted(months, reverse=True)
        self.release_month_combo.blockSignals(True)
        self.release_month_combo.clear()
        for month in months:
            self.release_month_combo.addItem(month.replace('-', '年', 1) + '月', month)
        index = self.release_month_combo.findData(current)
        self.release_month_combo.setCurrentIndex(index if index >= 0 else 0)
        self.release_month_combo.blockSignals(False)

    def _sync_release_list_height(self):
        """按当前任务数收紧列表高度，超出可用空间时再由滚动区处理。"""
        count = self.release_list.count()
        spacing = self.release_list.spacing()
        margins = self.release_list.contentsMargins()
        height = margins.top() + margins.bottom()
        if count:
            height += count * TaskRow.ROW_HEIGHT + max(0, count - 1) * spacing
        self.release_scroll.setFixedHeight(height)

    def _fill_release(self, requirements):
        """按用户选择月份展示已勾选入选的需求。"""
        self._clear_layout(self.release_list)
        board = load_release_board()
        self._fill_release_months(requirements, board)
        month_key = str(self.release_month_combo.currentData() or '')
        if not month_key:
            self.release_empty.setVisible(True)
            self._sync_release_list_height()
            return
        completed_requirement_keys = set(board.get('completed_requirement_keys', []))
        upcoming = []
        for item in requirements:
            if not item.get('is_monthly_release') or str(item.get('online_month') or '')[:7] != month_key:
                continue
            upcoming.append(('requirement', item, _parse_date(item.get('planned_online_date'))))
        def _sort_key(entry):
            kind, item, date_value = entry
            done = self._release_key(kind, item, month_key) in completed_requirement_keys
            return (done, date_value or datetime.date.max, str(item.get('title') or ''))

        from tools.list_pin import decorate_title, is_pinned
        upcoming.sort(key=_sort_key)
        zh = self.language == 'zh'
        self.release_empty.setVisible(not upcoming)
        for kind, item, planned_date in upcoming:
            completed = self._release_key(kind, item, month_key) in completed_requirement_keys
            title = item.get('title') or ('未命名' if zh else 'Untitled')
            title = decorate_title(title or item.get('code') or ('未命名' if zh else 'Untitled'), is_pinned(item))
            identifier = str(item.get('code') or '').strip() or str(item.get('record_kind') or ('需求' if zh else 'Requirement'))
            date_text = planned_date.isoformat() if planned_date else month_key
            badge = f'计划 {date_text}' if zh else f'Plan {date_text}'
            system = item.get('system') or ('未选系统' if zh else 'No system')
            meta = f'{system} · {badge}'
            action = (
                ('撤销完成' if zh else 'Undo', lambda _checked=False, current=item: self._set_release_item_completed('requirement', current, month_key, False))
                if completed else
                ('已完成' if zh else 'Complete', lambda _checked=False, current=item: self._set_release_item_completed('requirement', current, month_key, True))
            )
            row = TaskRow(item, title, meta, '已完成' if completed and zh else (item.get('status') or ''), identifier=identifier, fixed_height=TaskRow.ROW_HEIGHT, highlight=completed or is_pinned(item), actions=(action,))
            row.clicked.connect(self._on_requirement_clicked)
            self.release_list.addWidget(row)
        self._sync_release_list_height()

    def _save_release_board(self, board):
        save_release_board(board)

    def _refresh_release_after_action(self):
        """在按钮点击事件返回后刷新，避免事件派发中销毁当前任务行。"""
        QTimer.singleShot(0, self.refresh)

    def _set_release_item_completed(self, kind, item, month, completed):
        """仅更新工作台任务进度，不修改需求业务状态或上线日期。"""
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

    def _on_requirement_clicked(self, item):
        if isinstance(item, dict):
            self.open_requirement.emit(item)
        self.open_requirements.emit()

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        today = datetime.date.today()
        if zh:
            self.title.setText('工作台')
            self.subtitle.setText(f'{today.strftime("%Y-%m-%d")} · 今天先处理最近的交付事项')
            self.local_status.setText('● 本地工作')
            self.recent_title.setText('最近需求')
            self.recent_more.setText('全部')
            self.recent_empty.setText('暂无需求记录。可在需求管理中新增或扫描目录。')
            self.release_title.setText('待升级事项')
            self.release_more.setText('发版联动')
            self.release_month_combo.setToolTip('选择要查看的上线月份')
            self.release_empty.setText('该月份暂无待升级事项')
            self.tools_label.setText('常用工具')
            self.gateway.setText('加解密')
            self.credit.setText('证件类型')
            self.docx.setText('接口文档')
            self.vin.setText('车辆 VIN')
            self.ops.setText('运维工作台')
        else:
            self.title.setText('Workbench')
            self.subtitle.setText(f'{today.strftime("%Y-%m-%d")} · Focus on nearby delivery work')
            self.local_status.setText('● Local')
            self.recent_title.setText('Recent requirements')
            self.recent_more.setText('All')
            self.recent_empty.setText('No requirements yet. Add or scan in Requirements.')
            self.release_title.setText('Upcoming releases')
            self.release_more.setText('Release prep')
            self.release_month_combo.setToolTip('Choose a release month')
            self.release_empty.setText('No release items for this month')
            self.tools_label.setText('TOOLS')
            self.gateway.setText('Crypto')
            self.credit.setText('Documents')
            self.docx.setText('Interface Docs')
            self.vin.setText('Vehicle VIN')
            self.ops.setText('Ops Workbench')
        self.refresh()
