# -*- coding: utf-8 -*-
"""可编辑桌面悬浮工具栏。

收起态：品牌 floating Logo（无 P/× 文字）。
展开态：用户配置的 1–6 个快捷入口 + 底部「打开完整工作台」。
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.design_system import apply_button
from ui.field_metrics import size_line
from ui.icons import apply_icon, brand_pixmap, icon_pixmap, qicon
from ui.navigation_model import (
    DEFAULT_FLOATING_SHORTCUTS,
    display_name,
    display_tooltip,
    icon_role_for,
    normalize_floating_shortcuts,
)


class QuickPanel(QWidget):
    """桌面悬浮工具栏；圆形品牌按钮是展开与收起状态唯一且不变的屏幕锚点。"""

    COMPACT_SIZE = (52, 52)
    BUTTON_SIZE = 44
    BUTTON_MARGIN = 4
    PANEL_WIDTH = 300
    LEARN_PANEL_WIDTH = 360
    HEADER_HEIGHT = 48
    FOOTER_HEIGHT = 40
    CARD_HEIGHT = 58
    GRID_GAP = 8
    PANEL_PAD = 12
    LEARN_SEARCH_HEIGHT = 520

    def __init__(self, main_window, language='zh'):
        super().__init__(None)
        self._main_window = main_window
        self.language = language
        self.expanded = False
        self._drag_offset = None
        self._dragging = False
        self._compact_position = QPoint()
        self._shortcuts = list(DEFAULT_FLOATING_SHORTCUTS)
        self._private_unlocked = False
        self._expand_right = False
        self.tool_buttons: list[QPushButton] = []
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._setup_ui()
        self.set_language(language)
        self._place_initially()

    # ── 生命周期 / 配置 ──────────────────────────────────────────

    def _setup_ui(self):
        # 外框和开关按钮使用固定几何位置，不参与动态布局，避免 Windows
        # 在下一帧重新布局后造成屏幕锚点漂移。
        self.shell = QFrame(self)
        self.shell.setObjectName('floating-toolbar')

        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setObjectName('floating-toggle')
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle_btn.clicked.connect(self._on_toggle_clicked)
        self._apply_toggle_icon()

        self.tools = QWidget(self)
        self.tools.setObjectName('floating-tools')
        tools_layout = QVBoxLayout(self.tools)
        tools_layout.setContentsMargins(12, 10, 12, 10)
        tools_layout.setSpacing(8)

        # 顶部：Logo + 标题 + 编辑 + 收起
        header = QHBoxLayout()
        header.setSpacing(8)
        self.header_logo = QLabel()
        self.header_logo.setFixedSize(22, 22)
        self.header_logo.setObjectName('floating-header-logo')
        header.addWidget(self.header_logo)
        self.title = QLabel()
        self.title.setObjectName('floating-title')
        header.addWidget(self.title, 1)
        self.edit_btn = QPushButton()
        self.edit_btn.setObjectName('floating-icon-btn')
        self.edit_btn.setFixedSize(28, 28)
        self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_btn.setToolTip('编辑快捷入口')
        self.edit_btn.clicked.connect(self.open_editor)
        apply_icon(self.edit_btn, 'edit', size=16)
        header.addWidget(self.edit_btn)
        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName('floating-icon-btn')
        self.collapse_btn.setFixedSize(28, 28)
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setToolTip('收起')
        self.collapse_btn.clicked.connect(self.hide_panel)
        apply_icon(self.collapse_btn, 'collapse', size=16)
        header.addWidget(self.collapse_btn)
        tools_layout.addLayout(header)

        # 中间：两列卡片网格
        self.grid_host = QWidget()
        self.grid_host.setObjectName('floating-grid')
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(self.GRID_GAP)
        self.grid.setVerticalSpacing(self.GRID_GAP)
        tools_layout.addWidget(self.grid_host, 1)

        self.preview = QWidget()
        self.preview.setObjectName('floating-preview')
        preview_l = QVBoxLayout(self.preview)
        preview_l.setContentsMargins(0, 0, 0, 0)
        preview_l.setSpacing(6)
        preview_head = QHBoxLayout()
        self.preview_back = QPushButton()
        self.preview_back.setObjectName('floating-icon-btn')
        self.preview_back.setFixedHeight(28)
        self.preview_back.clicked.connect(self._hide_result_preview)
        preview_head.addWidget(self.preview_back)
        self.preview_title = QLabel()
        self.preview_title.setObjectName('floating-title')
        preview_head.addWidget(self.preview_title, 1)
        self.preview_gen_personal = QPushButton()
        apply_button(self.preview_gen_personal, 'secondary', compact=True)
        self.preview_gen_personal.setMinimumWidth(80)
        self.preview_gen_personal.clicked.connect(lambda: self._generate_from_preview('personal'))
        preview_head.addWidget(self.preview_gen_personal)
        self.preview_gen_unit = QPushButton()
        apply_button(self.preview_gen_unit, 'secondary', compact=True)
        self.preview_gen_unit.setMinimumWidth(80)
        self.preview_gen_unit.clicked.connect(lambda: self._generate_from_preview('unit'))
        preview_head.addWidget(self.preview_gen_unit)
        self.preview_gen_vin = QPushButton()
        apply_button(self.preview_gen_vin, 'secondary', compact=True)
        self.preview_gen_vin.setMinimumWidth(80)
        self.preview_gen_vin.clicked.connect(lambda: self._generate_from_preview('vin'))
        preview_head.addWidget(self.preview_gen_vin)
        preview_l.addLayout(preview_head)
        self.preview_hint = QLabel()
        self.preview_hint.setObjectName('field-hint')
        self.preview_hint.setWordWrap(True)
        preview_l.addWidget(self.preview_hint)
        self.preview_table = QTableWidget(0, 2)
        self.preview_table.setObjectName('floating-preview-table')
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.preview_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.verticalHeader().setDefaultSectionSize(28)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview_table.setShowGrid(False)
        self.preview_table.itemDoubleClicked.connect(self._copy_preview_item)
        preview_l.addWidget(self.preview_table, 1)
        self.preview.hide()
        tools_layout.addWidget(self.preview, 1)

        self.learn_search = QWidget()
        self.learn_search.setObjectName('floating-learn-search')
        learn_l = QVBoxLayout(self.learn_search)
        learn_l.setContentsMargins(0, 0, 0, 0)
        learn_l.setSpacing(6)
        learn_head = QHBoxLayout()
        self.learn_back = QPushButton()
        self.learn_back.setObjectName('floating-icon-btn')
        self.learn_back.setFixedHeight(28)
        self.learn_back.clicked.connect(self._hide_learning_search)
        learn_head.addWidget(self.learn_back)
        self.learn_title = QLabel()
        self.learn_title.setObjectName('floating-title')
        learn_head.addWidget(self.learn_title, 1)
        self.learn_count = QLabel()
        self.learn_count.setObjectName('small-label')
        learn_head.addWidget(self.learn_count)
        learn_l.addLayout(learn_head)
        self.learn_search_edit = QLineEdit()
        self.learn_search_edit.setObjectName('ops-search')
        self.learn_search_edit.setClearButtonEnabled(True)
        size_line(self.learn_search_edit, 'search')
        self.learn_search_edit.textChanged.connect(self._on_learning_search_changed)
        learn_l.addWidget(self.learn_search_edit)
        self._learn_search_debounce = QTimer(self)
        self._learn_search_debounce.setSingleShot(True)
        self._learn_search_debounce.setInterval(220)
        self._learn_search_debounce.timeout.connect(self._run_learning_search)
        self.learn_hint = QLabel()
        self.learn_hint.setObjectName('field-hint')
        self.learn_hint.setWordWrap(True)
        learn_l.addWidget(self.learn_hint)
        self.learn_list = QListWidget()
        self.learn_list.setObjectName('floating-learn-list')
        self.learn_list.setUniformItemSizes(False)
        self.learn_list.currentItemChanged.connect(self._show_learning_entry)
        self.learn_list.itemDoubleClicked.connect(self._copy_learning_entry)
        learn_l.addWidget(self.learn_list, 1)
        self.learn_content = QPlainTextEdit()
        self.learn_content.setObjectName('floating-learn-content')
        self.learn_content.setReadOnly(True)
        learn_l.addWidget(self.learn_content, 1)
        learn_actions = QHBoxLayout()
        learn_actions.addStretch()
        self.learn_copy_btn = QPushButton()
        apply_button(self.learn_copy_btn, 'primary', compact=True)
        self.learn_copy_btn.clicked.connect(self._copy_learning_entry)
        learn_actions.addWidget(self.learn_copy_btn)
        learn_l.addLayout(learn_actions)
        self.learn_search.hide()
        tools_layout.addWidget(self.learn_search, 1)
        self._learn_entries = []
        self._learn_current = None

        # 底部：打开完整工作台 + 设置快捷入口
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.home_btn = QPushButton()
        self.home_btn.setObjectName('floating-home')
        self.home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_btn.clicked.connect(lambda: self._activate(0))
        footer.addWidget(self.home_btn, 1)
        self.footer_edit_btn = QPushButton()
        self.footer_edit_btn.setObjectName('floating-home')
        self.footer_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_edit_btn.clicked.connect(self.open_editor)
        footer.addWidget(self.footer_edit_btn)
        tools_layout.addLayout(footer)

        for widget in (self.toggle_btn, self.title, self.shell, self.header_logo):
            widget.installEventFilter(self)
        for widget in (self, self.toggle_btn, self.shell, self.title, self.tools):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda position, source=widget: self._show_context_menu(source.mapToGlobal(position))
            )
        self._rebuild_cards()
        self._apply_compact_geometry()
        self._refresh_header_logo()

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.title.setText('快捷工具' if zh else 'Quick Tools')
        self.home_btn.setText('打开完整工作台' if zh else 'Open workspace')
        self.footer_edit_btn.setText('设置快捷入口' if zh else 'Edit shortcuts')
        self.edit_btn.setToolTip('编辑快捷入口' if zh else 'Edit shortcuts')
        self.collapse_btn.setToolTip('收起' if zh else 'Collapse')
        self.toggle_btn.setToolTip('打开快捷工具' if zh else 'Open quick tools')
        self.preview_back.setText('返回' if zh else 'Back')
        self.preview_gen_personal.setText('生成个人' if zh else 'Personal')
        self.preview_gen_unit.setText('生成单位' if zh else 'Company')
        self.preview_gen_vin.setText('生成' if zh else 'Generate')
        self.preview_table.setToolTip('双击单元格复制该字段' if zh else 'Double-click a cell to copy that field')
        self.learn_back.setText('返回' if zh else 'Back')
        self.learn_title.setText('自我学习' if zh else 'Learning')
        self.learn_search_edit.setPlaceholderText(
            '全文搜索：标题、正文、标签、来源……' if zh else
            'Search title, content, tags and source…'
        )
        self.learn_copy_btn.setText('复制内容' if zh else 'Copy')
        self.learn_list.setToolTip('单击查看，双击复制内容' if zh else 'Click to view, double-click to copy')
        if self.learn_search.isVisible():
            self._run_learning_search()
        self._rebuild_cards()
        if self.preview.isVisible() and getattr(self, '_preview_index', None) is not None:
            self._fill_result_preview(self._preview_index)

    def apply_shortcuts(self, shortcuts, *, private_unlocked: bool | None = None):
        """刷新快捷配置；保留面板位置与展开/收起状态。"""
        if private_unlocked is not None:
            self._private_unlocked = bool(private_unlocked)
        self._shortcuts = normalize_floating_shortcuts(
            shortcuts, private_unlocked=self._private_unlocked
        )
        was_expanded = self.expanded
        self._rebuild_cards()
        if was_expanded:
            self._layout_expanded()
        else:
            self._apply_compact_geometry()

    def set_private_unlocked(self, unlocked: bool):
        self._private_unlocked = bool(unlocked)
        self._shortcuts = normalize_floating_shortcuts(
            self._shortcuts, private_unlocked=self._private_unlocked
        )
        self._rebuild_cards()
        if self.expanded:
            self._layout_expanded()

    def current_shortcuts(self) -> list[int]:
        return list(self._shortcuts)

    # ── 卡片网格 ────────────────────────────────────────────────

    def _rebuild_cards(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.tool_buttons = []
        for position, index in enumerate(self._shortcuts):
            button = QPushButton()
            button.setObjectName('floating-item')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(self.CARD_HEIGHT)
            name = display_name(index, self.language)
            button.setText(name)
            button.setToolTip(display_tooltip(index, self.language) or name)
            apply_icon(button, icon_role_for(index), size=20)
            button.clicked.connect(lambda checked=False, value=index: self._activate(value))
            row, col = divmod(position, 2)
            self.grid.addWidget(button, row, col)
            self.tool_buttons.append(button)

    def _content_height(self) -> int:
        if self.learn_search.isVisible():
            return self.LEARN_SEARCH_HEIGHT
        if self.preview.isVisible():
            return 420
        count = max(1, len(self._shortcuts))
        rows = (count + 1) // 2
        body = rows * self.CARD_HEIGHT + max(0, rows - 1) * self.GRID_GAP
        return (
            self.HEADER_HEIGHT
            + body
            + self.FOOTER_HEIGHT
            + self.PANEL_PAD * 2
            + 8
        )

    def _expanded_size(self) -> tuple[int, int]:
        if self.learn_search.isVisible():
            return self.LEARN_PANEL_WIDTH + 16, self.LEARN_SEARCH_HEIGHT + 16
        height = max(200, min(380, self._content_height()))
        return self.PANEL_WIDTH + 16, height + 16

    # ── 品牌图标 ────────────────────────────────────────────────

    def _theme_tint(self) -> str:
        try:
            from ui.theme_manager import ThemeManager
            return ThemeManager.instance().token('PRIMARY_ACTIVE')
        except Exception:
            return '#4F735F'

    def _surface_tint(self) -> str:
        try:
            from ui.theme_manager import ThemeManager
            return ThemeManager.instance().token('TEXT_STRONG')
        except Exception:
            return '#1E2A42'

    def _apply_toggle_icon(self):
        tint = self._surface_tint() if self.expanded else self._theme_tint()
        pix = brand_pixmap('floating', size=28, tint=tint)
        if pix.isNull():
            # 最后回退：app ico 缩放，仍不使用文字 P
            pix = brand_pixmap('app_ico', size=28, tint=tint)
        if not pix.isNull():
            self.toggle_btn.setIcon(QIcon(pix))
            self.toggle_btn.setIconSize(QSize(28, 28))
            self.toggle_btn.setText('')
        else:
            self.toggle_btn.setText('')

    def _refresh_header_logo(self):
        pix = brand_pixmap('floating', size=20, tint=self._theme_tint())
        if not pix.isNull():
            self.header_logo.setPixmap(pix)

    def refresh_brand_icons(self):
        """主题切换后刷新收起/头部 Logo 染色。"""
        self._apply_toggle_icon()
        self._refresh_header_logo()
        apply_icon(self.edit_btn, 'edit', size=16)
        apply_icon(self.collapse_btn, 'collapse', size=16)
        self._rebuild_cards()

    # ── 几何 ────────────────────────────────────────────────────

    def _place_initially(self):
        screen = QApplication.primaryScreen().availableGeometry()
        width, height = self.COMPACT_SIZE
        x = screen.right() - width - 18
        y = screen.center().y() - height // 2
        self._compact_position = QPoint(x, y)
        self.setGeometry(x, y, width, height)
        self._apply_compact_geometry()

    def reset_position(self):
        if self.expanded:
            self.toggle_expanded()
        self._place_initially()
        self.show()
        self.raise_()

    def apply_preferences(self, opacity=96, always_on_top=True):
        was_visible = self.isVisible()
        position = QPoint(self.pos())
        desired_on_top = bool(always_on_top)
        current_on_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if current_on_top != desired_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, desired_on_top)
            self.move(position)
            if was_visible:
                self.show()
                self.raise_()
        self.set_opacity(opacity)

    def set_opacity(self, opacity):
        opacity_value = max(0.45, min(1.0, float(opacity) / 100.0))
        self.setWindowOpacity(opacity_value)
        if self.isVisible():
            QTimer.singleShot(0, lambda value=opacity_value: self.setWindowOpacity(value))

    def _apply_compact_geometry(self):
        width, height = self.COMPACT_SIZE
        self.shell.setGeometry(0, 0, width, height)
        self.toggle_btn.setGeometry(
            self.BUTTON_MARGIN, self.BUTTON_MARGIN,
            self.BUTTON_SIZE, self.BUTTON_SIZE,
        )
        self.tools.hide()
        self.shell.setProperty('compact', True)
        self.shell.style().unpolish(self.shell)
        self.shell.style().polish(self.shell)
        self._apply_toggle_icon()

    def _apply_expanded_geometry(self):
        width, height = self._expanded_size()
        self.shell.setGeometry(8, 8, width - 16, height - 16)
        # 收起 Logo 锚点：向左展开时在右侧；向右展开时在左侧
        if self._expand_right:
            toggle_x = self.BUTTON_MARGIN + 8
        else:
            toggle_x = width - self.BUTTON_SIZE - self.BUTTON_MARGIN - 8
        self.toggle_btn.setGeometry(
            toggle_x,
            self.BUTTON_MARGIN + 8,
            self.BUTTON_SIZE,
            self.BUTTON_SIZE,
        )
        # tools 避开 toggle 区域
        tools_x = 18 if not self._expand_right else 18 + self.BUTTON_SIZE
        tools_w = width - 36 - (self.BUTTON_SIZE if self._expand_right else 0)
        # 实际面板内容在 shell 内，tools 覆盖主体
        self.tools.setGeometry(16, 16, width - 32, height - 32)
        self.tools.show()
        self.tools.raise_()
        self.toggle_btn.raise_()
        self.shell.setProperty('compact', False)
        self.shell.style().unpolish(self.shell)
        self.shell.style().polish(self.shell)
        self._apply_toggle_icon()
        # 展开时 toggle 叠在右上/左上角外，内容区自带 collapse 按钮
        self.toggle_btn.hide()

    def _layout_expanded(self):
        """根据 compact 锚点计算展开矩形，避免越界。"""
        width, height = self._expanded_size()
        anchor = QPoint(self._compact_position)
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        area = screen.availableGeometry()

        # 优先向左展开（锚点在右侧）
        left = anchor.x() - (width - self.COMPACT_SIZE[0])
        self._expand_right = False
        if left < area.left():
            left = anchor.x()
            self._expand_right = True
        if left + width > area.right() + 1:
            left = max(area.left(), area.right() - width + 1)

        top = anchor.y()
        if top + height > area.bottom() + 1:
            top = max(area.top(), area.bottom() - height + 1)
        if top < area.top():
            top = area.top()

        self.setGeometry(left, top, width, height)
        self._apply_expanded_geometry()

    def _button_global_top_left(self):
        if self.toggle_btn.isVisible():
            return self.toggle_btn.mapToGlobal(QPoint(0, 0))
        # 展开时 toggle 隐藏：用 compact 锚点
        return self._compact_position + QPoint(self.BUTTON_MARGIN, self.BUTTON_MARGIN)

    # ── 展开/收起 ───────────────────────────────────────────────

    def _on_toggle_clicked(self):
        if self._dragging:
            return
        self.toggle_expanded()

    def toggle_expanded(self):
        if not self.expanded:
            self._compact_position = QPoint(self.pos())
            self.expanded = True
            self._layout_expanded()
        else:
            self._hide_result_preview()
            self._hide_learning_search(relayout=False)
            self.expanded = False
            width, height = self.COMPACT_SIZE
            self.setGeometry(
                self._compact_position.x(), self._compact_position.y(), width, height,
            )
            self.toggle_btn.show()
            self._apply_compact_geometry()
        self.show()
        self.raise_()

    def show_panel(self):
        if not self.expanded:
            self.toggle_expanded()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def hide_panel(self):
        if self.preview.isVisible():
            self._hide_result_preview()
        if self.learn_search.isVisible():
            self._hide_learning_search()
        if self.expanded:
            self.toggle_expanded()

    def open_editor(self):
        settings = {}
        private_unlocked = self._private_unlocked
        if hasattr(self._main_window, '_settings'):
            settings = dict(self._main_window._settings)
        if hasattr(self._main_window, '_private_unlocked'):
            private_unlocked = bool(self._main_window._private_unlocked)
        settings['floating_shortcuts'] = list(self._shortcuts)

        def _on_saved(saved):
            if hasattr(self._main_window, '_apply_settings'):
                self._main_window._apply_settings(saved)
            else:
                self.apply_shortcuts(
                    saved.get('floating_shortcuts'),
                    private_unlocked=private_unlocked,
                )
            if hasattr(self._main_window, 'settings_panel'):
                try:
                    self._main_window.settings_panel.load_values(saved)
                except Exception:
                    pass

        from ui.floating_shortcuts_editor import open_floating_shortcuts_editor
        open_floating_shortcuts_editor(
            self,
            settings,
            language=self.language,
            private_unlocked=private_unlocked,
            on_saved=_on_saved,
        )

    def _activate(self, index):
        if index in (1, 4):
            self._open_result_preview(index)
            return
        if index == 8:
            self._open_learning_search()
            return
        self._main_window.showNormal()
        self._main_window.raise_()
        self._main_window.activateWindow()
        self._main_window.navigate_to(index)
        self.hide_panel()

    def _open_result_preview(self, index: int):
        if not self.expanded:
            self.toggle_expanded()
        self._preview_index = index
        if index == 1 and getattr(self, '_preview_credit_side', None) is None:
            self._preview_credit_side = 'personal'
        self._hide_learning_search(relayout=False)
        self.grid_host.hide()
        self.preview.show()
        self._fill_result_preview(index)
        self._layout_expanded()

    def _hide_result_preview(self):
        self.preview.hide()
        if not self.learn_search.isVisible():
            self.grid_host.show()
        self._preview_index = None
        if self.expanded:
            self._layout_expanded()

    def _open_learning_search(self):
        if not self.expanded:
            self.toggle_expanded()
        self.preview.hide()
        self.grid_host.hide()
        self.learn_search.show()
        self._learn_entries = self._load_learning_entries()
        self._run_learning_search()
        self._layout_expanded()
        self.learn_search_edit.setFocus()
        self.learn_search_edit.selectAll()

    def _hide_learning_search(self, *, relayout=True):
        if hasattr(self, '_learn_search_debounce'):
            self._learn_search_debounce.stop()
        self.learn_search.hide()
        if not self.preview.isVisible():
            self.grid_host.show()
        if relayout and self.expanded:
            self._layout_expanded()

    def _load_learning_entries(self) -> list:
        win = self._main_window
        panel = getattr(win, 'personal_panel', None)
        tab = getattr(panel, 'knowledge_tab', None) if panel is not None else None
        if tab is not None and hasattr(tab, 'all_entries'):
            try:
                return list(tab.all_entries() or [])
            except Exception:
                pass
        from tools.personal_knowledge import collect_knowledge_entries, rebuild_search_index
        entries = collect_knowledge_entries()
        rebuild_search_index(entries)
        return entries

    def _on_learning_search_changed(self, _text):
        self._learn_search_debounce.start()

    def _run_learning_search(self):
        from tools.personal_knowledge import search_entries
        zh = self.language == 'zh'
        query = (self.learn_search_edit.text() or '').strip()
        hits = search_entries(self._learn_entries, query)
        self.learn_count.setText(f'{len(hits)} 条' if zh else f'{len(hits)}')
        if not self._learn_entries:
            self.learn_hint.setText(
                '还没有学习资料。打开完整工作台后可粘贴或导入。' if zh else
                'No notes yet. Open the workspace to paste or import.'
            )
        elif not hits:
            self.learn_hint.setText('没有匹配内容' if zh else 'No matching notes')
        else:
            self.learn_hint.setText(
                '单击查看，双击或点「复制内容」复制。' if zh else
                'Click to view. Double-click or Copy to copy.'
            )
        current_id = self._learn_current.get('id') if self._learn_current else None
        self.learn_list.blockSignals(True)
        self.learn_list.clear()
        selected = None
        for entry in hits:
            item = QListWidgetItem(self._learning_item_label(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setSizeHint(QSize(0, 42))
            tooltip = self._learning_entry_text(entry)[:800]
            if tooltip:
                item.setToolTip(tooltip)
            self.learn_list.addItem(item)
            if current_id and entry.get('id') == current_id:
                selected = item
        if selected is None and self.learn_list.count():
            selected = self.learn_list.item(0)
        self.learn_list.blockSignals(False)
        if selected is not None:
            self.learn_list.setCurrentItem(selected)
        else:
            self._show_learning_entry(None)

    @staticmethod
    def _learning_item_label(entry: dict) -> str:
        from tools.list_pin import decorate_title, is_pinned
        from tools.personal_knowledge import CATEGORIES
        raw_title = entry.get('title') or '未命名'
        title = decorate_title(raw_title, is_pinned(entry))
        category = CATEGORIES.get(entry.get('category'), CATEGORIES['other'])
        source = '已更新' if entry.get('builtin_source') else ('内置' if entry.get('builtin') else '我的')
        file_type = entry.get('file_type') or (
            'EXCEL' if entry.get('content_type') == 'workbook_sheet' else 'TXT'
        )
        pin_tag = ' · 置顶' if is_pinned(entry) else ''
        return f'{title}\n{file_type} · {category} · {source}{pin_tag}'

    @staticmethod
    def _learning_entry_text(entry: dict | None) -> str:
        if not entry:
            return ''
        if entry.get('content_type') == 'workbook_sheet':
            rows = entry.get('rows') or []
            return '\n'.join(
                '\t'.join('' if cell is None else str(cell) for cell in (row or []))
                for row in rows
            )
        return str(entry.get('content') or '')

    def _show_learning_entry(self, current, _previous=None):
        entry = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._learn_current = entry if isinstance(entry, dict) else None
        text = self._learning_entry_text(self._learn_current)
        self.learn_content.setPlainText(text)
        self.learn_copy_btn.setEnabled(bool(text.strip()))

    def _copy_learning_entry(self, *_args):
        cursor = self.learn_content.textCursor()
        selected = cursor.selectedText().replace('\u2029', '\n').strip()
        text = selected or (self.learn_content.toPlainText() or '').strip()
        if text:
            QApplication.clipboard().setText(text)

    def _fill_result_preview(self, index: int):
        zh = self.language == 'zh'
        win = self._main_window
        self.preview_gen_personal.setVisible(index == 1)
        self.preview_gen_unit.setVisible(index == 1)
        self.preview_gen_vin.setVisible(index == 4)
        if index == 1:
            self.preview_title.setText('证件类型' if zh else 'Documents')
            ensure = getattr(win, '_ensure_credit_panel', None)
            if callable(ensure):
                try:
                    ensure()
                except Exception:
                    pass
            panel = getattr(win, 'credit_panel', None)
            headers = ('名称', '证件号码') if zh else ('Name', 'Number')
            side = getattr(self, '_preview_credit_side', None) or 'personal'
            if panel is not None:
                personal = list(getattr(panel, '_personal_results', None) or [])
                unit = list(getattr(panel, '_unit_results', None) or [])
                if side == 'personal' and not personal and unit:
                    side = 'unit'
                if side == 'unit' and not unit and personal:
                    side = 'personal'
                self._preview_credit_side = side
            rows = self._credit_preview_rows(panel, side)
        else:
            self.preview_title.setText('车辆 VIN' if zh else 'VIN')
            ensure = getattr(win, '_ensure_vin_panel', None)
            if callable(ensure):
                try:
                    ensure()
                except Exception:
                    pass
            panel = getattr(win, 'vin_panel', None)
            headers = ('车牌号', 'VIN', '发动机号') if zh else ('Plate', 'VIN', 'Engine')
            rows = self._vin_preview_rows(panel)
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        if not rows:
            self.preview_hint.setText(
                '点右上角生成。双击单元格复制该字段。' if zh else
                'Use Generate. Double-click a cell to copy that field.'
            )
            self.preview_table.setRowCount(0)
            return
        self.preview_hint.setText('双击单元格复制该字段' if zh else 'Double-click a cell to copy that field')
        self.preview_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(('双击复制：' if zh else 'Double-click to copy: ') + value)
                self.preview_table.setItem(row, column, item)
                self.preview_table.setRowHeight(row, 28)

    @staticmethod
    def _credit_preview_rows(panel, side: str = 'personal') -> list[list[str]]:
        rows = []
        if panel is None:
            return rows
        source = (
            getattr(panel, '_unit_results', None)
            if side == 'unit' else getattr(panel, '_personal_results', None)
        )
        for record in list(source or []):
            rows.append([
                str(record.get('name') or ''),
                str(record.get('document') or ''),
            ])
        return rows

    @staticmethod
    def _vin_preview_rows(panel) -> list[list[str]]:
        rows = []
        if panel is None:
            return rows
        for record in list(getattr(panel, '_results', None) or []):
            rows.append([
                str(record.get('plate') or ''),
                str(record.get('vin') or ''),
                str(record.get('engine_no') or ''),
            ])
        return rows

    def _generate_from_preview(self, kind: str):
        win = self._main_window
        count = 10
        if kind in ('personal', 'unit'):
            ensure = getattr(win, '_ensure_credit_panel', None)
            if callable(ensure):
                ensure()
            panel = getattr(win, 'credit_panel', None)
            if panel is None:
                return
            if kind == 'personal':
                from tools.id_documents import generate_personal_records
                doc_kind = 'resident_id'
                if hasattr(panel, 'personal_type') and panel.personal_type.currentData():
                    doc_kind = panel.personal_type.currentData()
                if hasattr(panel, 'personal_qty'):
                    try:
                        count = max(1, min(200, int(panel.personal_qty.value())))
                    except Exception:
                        count = 10
                panel._personal_results = generate_personal_records(doc_kind, count)
            else:
                from tools.credit_code import generate_unit_records
                if hasattr(panel, 'unit_qty'):
                    try:
                        count = max(1, min(200, int(panel.unit_qty.value())))
                    except Exception:
                        count = 10
                panel._unit_results = generate_unit_records(count)
            self._preview_credit_side = kind
            if hasattr(panel, 'isVisible') and panel.isVisible() and hasattr(panel, '_update_table'):
                if hasattr(panel, 'category_tabs'):
                    panel.category_tabs.setCurrentIndex(0 if kind == 'personal' else 1)
                panel._update_table()
            self._preview_index = 1
            self._fill_result_preview(1)
            return
        ensure = getattr(win, '_ensure_vin_panel', None)
        if callable(ensure):
            ensure()
        panel = getattr(win, 'vin_panel', None)
        if panel is None:
            return
        from tools.vin_generator import generate_vehicle_batch
        if hasattr(panel, 'qty'):
            try:
                count = max(1, min(200, int(panel.qty.value())))
            except Exception:
                count = 10
        year = 2026
        if hasattr(panel, 'year_combo') and panel.year_combo.currentText():
            try:
                year = int(panel.year_combo.currentText())
            except Exception:
                year = 2026
        panel._results = generate_vehicle_batch(count, year)
        if hasattr(panel, 'isVisible') and panel.isVisible() and hasattr(panel, '_generate'):
            # 主界面已打开时同步表格
            try:
                panel._generate()
            except Exception:
                pass
        self._preview_index = 4
        self._fill_result_preview(4)

    def _copy_preview_item(self, item):
        payload = item.data(Qt.ItemDataRole.UserRole) or {}
        navigate = payload.get('navigate')
        if navigate is not None:
            self._main_window.showNormal()
            self._main_window.raise_()
            self._main_window.activateWindow()
            self._main_window.navigate_to(int(navigate))
            return
        text = (item.text() or '').strip()
        if text:
            QApplication.clipboard().setText(text)

    def _show_context_menu(self, global_position):
        menu = QMenu()
        zh = self.language == 'zh'
        edit_action = QAction('编辑快捷入口' if zh else 'Edit shortcuts', menu)
        edit_action.triggered.connect(self.open_editor)
        menu.addAction(edit_action)
        close_action = QAction('关闭悬浮栏' if zh else 'Close floating toolbar', menu)
        close_action.triggered.connect(self.close_toolbar)
        menu.addAction(close_action)
        menu.exec(global_position)

    def close_toolbar(self):
        if self.expanded:
            self.expanded = False
            width, height = self.COMPACT_SIZE
            self.setGeometry(
                self._compact_position.x(), self._compact_position.y(), width, height,
            )
            self.toggle_btn.show()
            self._apply_compact_geometry()
        self.hide()

    def eventFilter(self, watched, event):
        if watched in (self.toggle_btn, self.title, self.shell, self.header_logo):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._dragging = False
            elif event.type() == QEvent.Type.MouseMove and self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
                target = event.globalPosition().toPoint() - self._drag_offset
                if (target - self.pos()).manhattanLength() > 3:
                    self._dragging = True
                    screen = QApplication.screenAt(event.globalPosition().toPoint()) or QApplication.primaryScreen()
                    area = screen.availableGeometry()
                    target.setX(max(area.left(), min(target.x(), area.right() - self.width() + 1)))
                    target.setY(max(area.top(), min(target.y(), area.bottom() - self.height() + 1)))
                    self.move(target)
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
                self._drag_offset = None
                if self._dragging:
                    if self.expanded:
                        # 展开拖动后：把 compact 锚点对齐到当前右上/左上
                        if self._expand_right:
                            self._compact_position = QPoint(self.pos().x(), self.pos().y())
                        else:
                            self._compact_position = QPoint(
                                self.pos().x() + self.width() - self.COMPACT_SIZE[0],
                                self.pos().y(),
                            )
                    else:
                        button_position = self._button_global_top_left()
                        self._compact_position = button_position - QPoint(self.BUTTON_MARGIN, self.BUTTON_MARGIN)
                    self._dragging = False
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide_panel()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # 点击面板外：若主窗也未持有焦点则收起（Tool 窗体较特殊，保留 Esc）
        super().focusOutEvent(event)
