# -*- coding: utf-8 -*-
"""首页工作台 — 最近需求 + 待升级事项 + 紧凑常用工具。"""

from __future__ import annotations

import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame, QLabel, QMenu, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
    QHBoxLayout, QBoxLayout, QScrollArea, QInputDialog,
)

from tools.dashboard_release_items import load_release_board, save_release_board
from tools.requirements import load_requirements
from ui.confirm_dialog import confirm_action
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

    def __init__(self, payload, title, meta, status='', *, highlight: bool = False, actions=()):
        super().__init__()
        self._payload = payload
        self.setObjectName('dashboard-task-row-today' if highlight else 'dashboard-task-row')
        self.setProperty('todayRelease', bool(highlight))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        body = QVBoxLayout()
        body.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName('dashboard-task-title')
        self.title_label.setWordWrap(False)
        body.addWidget(self.title_label)
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName('small-label')
        body.addWidget(self.meta_label)
        layout.addLayout(body, 1)
        if status:
            pill = QLabel(status)
            pill.setObjectName('status-pill-today' if highlight else 'status-pill')
            layout.addWidget(pill)
        for text, callback in actions:
            action = QPushButton(text)
            action.setObjectName('ghost-btn')
            action.setProperty('compactAction', True)
            action.clicked.connect(callback)
            layout.addWidget(action)
        arrow = QLabel('›')
        arrow.setObjectName('dashboard-row-arrow')
        layout.addWidget(arrow)

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
        self.release_add_requirement_btn = QPushButton()
        self.release_add_requirement_btn.setObjectName('ghost-btn')
        self.release_add_requirement_btn.setProperty('compactAction', True)
        self.release_add_requirement_btn.clicked.connect(self._add_requirement_to_release_board)
        release_head.addWidget(self.release_add_requirement_btn)
        self.release_add_manual_btn = QPushButton()
        self.release_add_manual_btn.setObjectName('btn-secondary')
        self.release_add_manual_btn.setProperty('compactAction', True)
        self.release_add_manual_btn.clicked.connect(self._add_manual_release_item)
        release_head.addWidget(self.release_add_manual_btn)
        release_layout.addLayout(release_head)
        self.release_scroll = QScrollArea()
        self.release_scroll.setWidgetResizable(True)
        self.release_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.release_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.release_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.release_scroll.setMinimumHeight(180)
        self.release_list_host = QWidget()
        self.release_list = QVBoxLayout(self.release_list_host)
        self.release_list.setContentsMargins(0, 0, 4, 0)
        self.release_list.setSpacing(4)
        self.release_scroll.setWidget(self.release_list_host)
        release_layout.addWidget(self.release_scroll, 1)
        self.release_empty = QLabel()
        self.release_empty.setObjectName('field-hint')
        self.release_empty.setWordWrap(True)
        release_layout.addWidget(self.release_empty)
        self.tasks_row.addWidget(self.release_card, 1)
        layout.addLayout(self.tasks_row, 1)

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

    def _fill_release(self, requirements):
        """待升级：仅展示当前自然月计划上线需求与当前月手工事项。"""
        self._clear_layout(self.release_list)
        today = datetime.date.today()
        month_key = today.strftime('%Y-%m')
        exclude = {'已上线', '已关闭', '已取消', '暂停'}
        board = load_release_board()
        hidden_ids = set(board['hidden_requirement_ids'])
        upcoming = []
        for item in requirements:
            status = str(item.get('status') or '')
            planned = str(item.get('planned_online_date') or '')[:10]
            parsed = _parse_date(planned)
            if (status in exclude or str(item.get('id') or '') in hidden_ids
                    or item.get('actual_online_date') or parsed is None
                    or planned[:7] != month_key):
                continue
            upcoming.append(('requirement', item, parsed))
        for item in board['manual_items']:
            parsed = _parse_date(item.get('planned_date'))
            if parsed is not None and str(item.get('planned_date') or '')[:7] == month_key:
                upcoming.append(('manual', item, parsed))

        from tools.list_pin import decorate_title, is_pinned
        upcoming.sort(key=lambda t: (0 if t[2] == today else 1, t[2].toordinal(), str(t[1].get('title') or '')))
        zh = self.language == 'zh'
        self.release_empty.setVisible(not upcoming)
        for kind, item, planned_date in upcoming:
            is_manual = kind == 'manual'
            title = item.get('title') or ('未命名' if zh else 'Untitled')
            if not is_manual:
                title = decorate_title(title or item.get('code') or ('未命名' if zh else 'Untitled'), is_pinned(item))
            planned = planned_date.isoformat()
            badge = ('今天上线' if zh else 'Ships today') if planned_date == today else (f'计划 {planned}' if zh else f'Plan {planned}')
            if is_manual:
                meta = f'{item.get("note") or ("手工事项" if zh else "Manual item")} · {badge}'
                actions = ((('编辑' if zh else 'Edit'), lambda current=item: self._edit_manual_release_item(current)),
                           (('删除' if zh else 'Delete'), lambda current=item: self._delete_manual_release_item(current)))
                row = TaskRow(item, title, meta, '', highlight=planned_date == today, actions=actions)
            else:
                system = item.get('system') or ('未选系统' if zh else 'No system')
                meta = f'{system} · {badge}'
                actions = ((('移除' if zh else 'Remove'), lambda current=item: self._hide_requirement_from_release_board(current)),)
                row = TaskRow(item, title, meta, item.get('status') or '', highlight=planned_date == today or is_pinned(item), actions=actions)
                row.clicked.connect(self._on_requirement_clicked)
            self.release_list.addWidget(row)

    def _save_release_board(self, board):
        save_release_board(board)
        self.refresh()

    def _add_requirement_to_release_board(self):
        requirements = load_requirements()
        candidates = [item for item in requirements if item.get('id')]
        labels = [f"{item.get('code') or 'REQ'} · {item.get('title') or '未命名'}" for item in candidates]
        if not labels:
            return
        value, accepted = QInputDialog.getItem(self, '添加需求事项', '选择需求：', labels, 0, False)
        if not accepted:
            return
        selected = candidates[labels.index(value)]
        board = load_release_board()
        hidden = set(board['hidden_requirement_ids'])
        hidden.discard(str(selected['id']))
        board['hidden_requirement_ids'] = list(hidden)
        self._save_release_board(board)

    def _hide_requirement_from_release_board(self, item):
        zh = self.language == 'zh'
        if not confirm_action(
            self, '移除待升级事项' if zh else 'Remove release item',
            '仅从工作台待升级事项移除，不会删除需求管理中的原需求。是否继续？' if zh else 'This only removes the item from the dashboard. The requirement remains unchanged.',
            confirm_text='移除' if zh else 'Remove', danger=False,
        ):
            return
        board = load_release_board()
        hidden = set(board['hidden_requirement_ids'])
        hidden.add(str(item.get('id') or ''))
        board['hidden_requirement_ids'] = list(hidden)
        self._save_release_board(board)

    def _add_manual_release_item(self):
        self._edit_manual_release_item({'id': '', 'title': '', 'planned_date': datetime.date.today().isoformat(), 'note': ''})

    def _edit_manual_release_item(self, item):
        title, accepted = QInputDialog.getText(self, '手工升级事项', '事项标题：', text=item.get('title') or '')
        if not accepted or not title.strip():
            return
        planned, accepted = QInputDialog.getText(self, '手工升级事项', '计划日期（YYYY-MM-DD）：', text=item.get('planned_date') or datetime.date.today().isoformat())
        if not accepted or _parse_date(planned) is None:
            return
        note, accepted = QInputDialog.getText(self, '手工升级事项', '备注（可留空）：', text=item.get('note') or '')
        if not accepted:
            return
        board = load_release_board()
        updated = dict(item, title=title.strip(), planned_date=planned[:10], note=note.strip())
        if updated.get('id'):
            board['manual_items'] = [updated if row.get('id') == updated['id'] else row for row in board['manual_items']]
        else:
            board['manual_items'].append(updated)
        self._save_release_board(board)

    def _delete_manual_release_item(self, item):
        zh = self.language == 'zh'
        if not confirm_action(
            self, '删除手工事项' if zh else 'Delete manual item',
            f'确定删除“{item.get("title") or ""}”吗？此操作无法恢复。' if zh else 'Delete this manual item? This cannot be undone.',
            confirm_text='删除' if zh else 'Delete', danger=True,
        ):
            return
        board = load_release_board()
        board['manual_items'] = [row for row in board['manual_items'] if row.get('id') != item.get('id')]
        self._save_release_board(board)

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
            self.release_add_requirement_btn.setText('从需求添加')
            self.release_add_manual_btn.setText('手工添加')
            self.release_empty.setText('暂无本月待升级事项')
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
            self.release_add_requirement_btn.setText('Add requirement')
            self.release_add_manual_btn.setText('Add manual')
            self.release_empty.setText('No releases planned this month')
            self.tools_label.setText('TOOLS')
            self.gateway.setText('Crypto')
            self.credit.setText('Documents')
            self.docx.setText('Interface Docs')
            self.vin.setText('Vehicle VIN')
            self.ops.setText('Ops Workbench')
        self.refresh()
