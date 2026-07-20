# -*- coding: utf-8 -*-
"""首页工作台 — Astra V2.0：核心快捷卡片 + 紧凑工具入口。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.icons import apply_icon, icon_pixmap
from ui.page_chrome import make_page_header


class ToolCard(QFrame):
    clicked = pyqtSignal()

    def __init__(self, icon_role, accent='blue'):
        super().__init__()
        self.setObjectName('tool-card')
        self.setProperty('accent', accent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(120)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        top = QHBoxLayout()
        icon = QLabel()
        icon.setFixedSize(32, 32)
        icon.setObjectName('page-header-icon')
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = icon_pixmap(icon_role, 18, '#4056CF')
        if not pix.isNull():
            icon.setPixmap(pix)
        top.addWidget(icon)
        top.addStretch(1)
        layout.addLayout(top)
        self.title = QLabel()
        self.title.setObjectName('card-title')
        self.description = QLabel()
        self.description.setObjectName('card-description')
        self.description.setWordWrap(True)
        self.button = QPushButton()
        self.button.setObjectName('card-action')
        self.button.clicked.connect(self.clicked.emit)
        layout.addWidget(self.title)
        layout.addWidget(self.description, 1)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def set_copy(self, title, description, action):
        self.title.setText(title)
        self.description.setText(description)
        self.button.setText(action)


class DashboardPanel(QWidget):
    open_credit = pyqtSignal()
    open_sql = pyqtSignal()
    open_docx = pyqtSignal()
    open_vin = pyqtSignal()
    open_gateway = pyqtSignal()
    open_ops = pyqtSignal()
    open_requirements = pyqtSignal()

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._mode = 'standard'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.offline = QLabel()
        self.offline.setObjectName('offline-pill')
        header, self.title, self.subtitle = make_page_header(
            '开发工具工作台',
            '常用交付与开发工具 · 文件仅保留在本机',
            'home',
            trailing=self.offline,
        )
        layout.addWidget(header)

        # 核心三卡
        self.core_grid = QGridLayout()
        self.core_grid.setSpacing(16)
        self.req_card = ToolCard('requirements', 'violet')
        self.sql = ToolCard('release', 'violet')
        self.gateway = ToolCard('shield-key', 'blue')
        self.req_card.clicked.connect(self.open_requirements.emit)
        self.sql.clicked.connect(self.open_sql.emit)
        self.gateway.clicked.connect(self.open_gateway.emit)
        self.core_grid.addWidget(self.req_card, 0, 0)
        self.core_grid.addWidget(self.sql, 0, 1)
        self.core_grid.addWidget(self.gateway, 0, 2)
        layout.addLayout(self.core_grid)

        # 次级入口
        secondary_label = QLabel()
        secondary_label.setObjectName('sidebar-section')
        self._secondary_label = secondary_label
        layout.addWidget(secondary_label)
        self.tools_row = QHBoxLayout()
        self.tools_row.setSpacing(10)
        self.credit = QPushButton()
        self.docx = QPushButton()
        self.vin = QPushButton()
        self.ops = QPushButton()
        for btn, icon, signal in (
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
        self.tools_row.addStretch(1)
        layout.addLayout(self.tools_row)

        self.hint = QLabel()
        self.hint.setObjectName('shortcut-hint')
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint)
        layout.addStretch(1)
        self.set_language(language)

    def apply_layout_mode(self, mode, low_height=False):
        self._mode = mode
        # 三列 / 两列 / 一列
        for i in reversed(range(self.core_grid.count())):
            item = self.core_grid.itemAt(i)
            if item and item.widget():
                self.core_grid.removeWidget(item.widget())
        cards = [self.req_card, self.sql, self.gateway]
        if mode == 'wide' or mode == 'standard':
            for col, card in enumerate(cards):
                self.core_grid.addWidget(card, 0, col)
                card.show()
        elif mode == 'compact':
            self.core_grid.addWidget(self.req_card, 0, 0)
            self.core_grid.addWidget(self.sql, 0, 1)
            self.core_grid.addWidget(self.gateway, 1, 0, 1, 2)
        else:
            for row, card in enumerate(cards):
                self.core_grid.addWidget(card, row, 0)

    def set_language(self, language):
        self.language = language
        if language == 'zh':
            self.title.setText('开发工具工作台')
            self.subtitle.setText('常用交付与开发工具 · 文件仅保留在本机')
            self.offline.setText('●  离线可用')
            self._secondary_label.setText('更多工具')
            self.req_card.set_copy('需求管理', '台账、文件、SVN 与升级联动一站完成。', '打开需求管理')
            self.sql.set_copy('升级准备', '按日期勾选需求，生成发版清单与 SQL 包。', '准备升级材料')
            self.gateway.set_copy('加解密', '国密解密 + XML/JSON 工具同一工作台。', '打开加解密')
            self.credit.setText('证件类型')
            self.docx.setText('接口文档')
            self.vin.setText('车辆 VIN')
            self.ops.setText('运维助手')
            self.hint.setText('Ctrl + Shift + P  随时展开桌面右侧悬浮工具栏')
        else:
            self.title.setText('Developer workspace')
            self.subtitle.setText('Delivery and developer tools · local-only')
            self.offline.setText('●  OFFLINE')
            self._secondary_label.setText('MORE TOOLS')
            self.req_card.set_copy('Requirements', 'Tracking, files, SVN and release links.', 'Open requirements')
            self.sql.set_copy('Release Prep', 'Pick requirements by date and generate packages.', 'Prepare release')
            self.gateway.set_copy('Crypto', 'SM decrypt with XML/JSON tools.', 'Open crypto')
            self.credit.setText('Documents')
            self.docx.setText('Interface Docs')
            self.vin.setText('Vehicle VIN')
            self.ops.setText('Operations')
            self.hint.setText('Ctrl + Shift + P  Expand the floating toolbar anytime')
