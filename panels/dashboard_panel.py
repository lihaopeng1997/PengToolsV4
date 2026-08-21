# -*- coding: utf-8 -*-
"""首页工作台 — 最近需求 + 待升级事项 + 紧凑常用工具。"""

from __future__ import annotations

import datetime
import os

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QLabel, QMenu, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
    QHBoxLayout, QBoxLayout, QScrollArea,
)

from config import DASHBOARD_RELEASE_ITEMS_FILE, REQUIREMENTS_FILE
from tools.dashboard_release_items import (
    collect_release_months,
    is_board_item_completed,
    load_release_board,
    release_month_for,
    save_release_board,
)
from tools.requirements import load_requirements, systems_display_text, test_points_button_text
from ui.design_system import apply_button
from ui.icons import apply_icon, icon_pixmap
from ui.page_chrome import make_page_header
from ui.responsive import set_subtitle_visible


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


class DashboardPanel(QWidget):
    open_credit = pyqtSignal()
    open_sql = pyqtSignal()
    open_docx = pyqtSignal()
    open_vin = pyqtSignal()
    open_gateway = pyqtSignal()
    open_ops = pyqtSignal()
    open_requirements = pyqtSignal()
    open_requirement = pyqtSignal(object)  # 具体需求 dict 或 id
    requirements_updated = pyqtSignal()  # 工作台改了需求台账（标记上线/恢复待办）

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._mode = 'standard'
        self._completed_section_collapsed = True
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
        )
        layout.addWidget(header)

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
        self._tool_buttons = []
        for btn, icon, signal in (
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

        # set_language 末尾会 refresh 一次；勿再重复 rebuild
        self.set_language(language)

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
        # 布局模式只影响可视行数/方向；列表数据无需重读盘 rebuild
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
        """双卡等高撑满中间区域，条目在卡片内滚动；底栏常用工具固定。"""
        floor = self._scroll_height_for_count(self._list_limit())
        for scroll in (self.recent_scroll, self.release_scroll):
            scroll.setMinimumHeight(floor)
            scroll.setMaximumHeight(16777215)
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
        self._pending_show_refresh = False
        if not self.isVisible():
            return
        if self._sources_changed():
            self.refresh()

    def refresh(self, preferred_release_month=None):
        """刷新工作台。preferred 仅在有明确目标月份时传入；普通刷新保留用户有效月份选择。"""
        self.setUpdatesEnabled(False)
        try:
            requirements = load_requirements()
            self._fill_recent(requirements)
            self._fill_release(requirements, preferred_release_month=preferred_release_month)
            self._apply_list_geometry()
            self._source_stamp = self._current_source_stamp()
        finally:
            self.setUpdatesEnabled(True)

    def refresh_for_requirement(self, requirement):
        """需求编辑保存后刷新；仅当入选上线相关字段时定位到目标月份。"""
        month = ''
        if isinstance(requirement, dict) and requirement.get('is_monthly_release'):
            month = release_month_for(requirement, fallback_current=True)
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
        items = (pinned + plain)[:20]
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
        completed_requirement_keys = set(board.get('completed_requirement_keys', []))
        pending = []
        done_items = []
        for item in requirements:
            if not item.get('is_monthly_release'):
                continue
            item_month = release_month_for(item, fallback_current=True)
            if not item_month or item_month != month_key:
                continue
            entry = ('requirement', item, _parse_date(item.get('planned_online_date')))
            if is_board_item_completed(item, month_key, completed_requirement_keys):
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
            if not self._completed_section_collapsed:
                for kind, item, planned_date in done_items:
                    self.release_list.addWidget(
                        self._build_release_row(kind, item, planned_date, month_key, completed=True)
                    )
        self.release_list.addStretch(1)

    def _build_release_row(self, kind, item, planned_date, month_key, *, completed: bool):
        from tools.list_pin import decorate_title, is_pinned
        zh = self.language == 'zh'
        title = item.get('title') or ('未命名' if zh else 'Untitled')
        title = decorate_title(title or item.get('code') or ('未命名' if zh else 'Untitled'), is_pinned(item))
        identifier = str(item.get('code') or '').strip() or str(
            item.get('record_kind') or ('需求' if zh else 'Requirement')
        )
        date_text = planned_date.isoformat() if planned_date else month_key
        badge = f'计划 {date_text}' if zh else f'Plan {date_text}'
        system = systems_display_text(item, empty=('未选系统' if zh else 'No system'))
        meta = f'{system} · {badge}'
        if completed:
            action = (
                '撤销完成' if zh else 'Undo',
                lambda _checked=False, current=item: self._set_release_item_completed(
                    'requirement', current, month_key, False
                ),
            )
            status_text = '已完成' if zh else 'Done'
        else:
            action = (
                '已完成' if zh else 'Complete',
                lambda _checked=False, current=item: self._set_release_item_completed(
                    'requirement', current, month_key, True
                ),
            )
            status_text = item.get('status') or ''
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
        board = load_release_board()
        prefs = board.setdefault('ui_prefs', {})
        self._completed_section_collapsed = not bool(prefs.get('completed_section_collapsed', True))
        prefs['completed_section_collapsed'] = self._completed_section_collapsed
        save_release_board(board)
        self.setUpdatesEnabled(False)
        try:
            requirements = load_requirements()
            self._fill_release_items(requirements, board)
            self._apply_list_geometry()
            self._source_stamp = self._current_source_stamp()
        finally:
            self.setUpdatesEnabled(True)

    def _save_release_board(self, board):
        save_release_board(board)

    def _refresh_release_after_action(self):
        """在按钮点击事件返回后刷新，避免事件派发中销毁当前任务行。"""
        # 普通刷新：不传 preferred，保留用户当前月份选择
        QTimer.singleShot(0, lambda: self.refresh(preferred_release_month=None))

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
            self.release_empty.setText('该月份暂无待升级事项。可在需求中勾选「是否本月上线」。')
            if hasattr(self, 'release_summary') and not self.release_summary.text():
                self.release_summary.setText('待处理 0 · 已完成 0')
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
            self.release_empty.setText('No release items for this month. Tick monthly release on a requirement.')
            if hasattr(self, 'release_summary') and not self.release_summary.text():
                self.release_summary.setText('Open 0 · Done 0')
            self.tools_label.setText('TOOLS')
            self.gateway.setText('Crypto')
            self.credit.setText('Documents')
            self.docx.setText('Interface Docs')
            self.vin.setText('Vehicle VIN')
            self.ops.setText('Ops Workbench')
        self.refresh()
