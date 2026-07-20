# -*- coding: utf-8 -*-
import datetime

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QInputDialog, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QStatusBar, QToolButton, QVBoxLayout, QWidget,
)

from panels.credit_panel import CreditCodePanel
from panels.dashboard_panel import DashboardPanel
from panels.docx_panel import DocxUpdatePanel
from panels.gateway_panel import GatewayDecodePanel
from panels.ops_panel import OpsPanel
from panels.personal_panel import PersonalPanel
from panels.requirement_panel import RequirementPanel
from panels.settings_panel import SettingsPanel
from panels.sql_panel import SqlToolPanel
from panels.vin_panel import VinPanel
from ui.confirm_dialog import ask_close_action
from ui.hotkey_service import HotkeyService
from ui.keep_awake_service import KeepAwakeService
from ui.field_metrics import size_combo
from ui.icons import NAV_ICON_BY_INDEX, apply_icon, brand_pixmap, qicon
from ui.navigation_model import GROUP_LABELS, NAV_MODEL, display_name
from ui.quick_panel import QuickPanel
from ui.responsive import LayoutModeController, content_margin_for_mode, is_icon_nav, nav_width_for_mode
from ui.tray_service import TrayService
from config import APP_BUILD_DATE, APP_VERSION_LABEL, app_version_text, load_settings, save_settings


class MainWindow(QMainWindow):
    layout_mode_changed = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        self.language = self._settings['default_language']
        self.hotkey_service = None
        self._force_exit = False
        self._shutting_down = False
        self._private_unlocked = False
        self._current_nav_index = 0
        self._layout_mode = 'standard'
        self._nav_icon_only = False
        self.setWindowTitle(f'PengTools Hub {app_version_text(with_date=False)}')
        self.setMinimumSize(960, 640)
        self.resize(1440, 900)
        self._center_on_screen()
        self._layout_controller = LayoutModeController(self)
        self._layout_controller.layout_mode_changed.connect(self._on_layout_mode)
        self._setup_ui()
        self._egg_clicks = 0
        self._completed_tasks = 0
        self.version_label.installEventFilter(self)
        self.clock_label.installEventFilter(self)
        self.user_chip.installEventFilter(self)
        self.quick_panel = QuickPanel(self, self.language)
        self.settings_panel.floating_opacity_preview.connect(self.quick_panel.set_opacity)
        self.settings_panel.edit_floating_shortcuts.connect(self._open_floating_shortcuts_editor)
        self.quick_panel.apply_preferences(
            self._settings['floating_opacity'], self._settings['floating_always_on_top']
        )
        self.quick_panel.apply_shortcuts(
            self._settings.get('floating_shortcuts'),
            private_unlocked=self._private_unlocked,
        )
        if self._settings['floating_show_on_startup']:
            self.quick_panel.show()
        self.tray_service = TrayService(self)
        self.keep_awake_service = KeepAwakeService(self)
        self._setup_hotkeys()
        self._setup_clock()
        language_index = 0 if self.language == 'zh' else 1
        self._language_index = language_index
        self._set_language(language_index)
        self._apply_settings(self._settings)
        QTimer.singleShot(0, lambda: self._layout_controller.force(self.width(), self.height()))

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._create_sidebar())

        content = QFrame()
        content.setObjectName('content_area')
        self._content_frame = content
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(24, 20, 24, 16)
        self._content_layout.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(
            self.stack.sizePolicy().horizontalPolicy(),
            self.stack.sizePolicy().verticalPolicy(),
        )
        self.dashboard_panel = DashboardPanel(self.language)
        self.credit_panel = CreditCodePanel()
        self.sql_panel = SqlToolPanel()
        self.docx_panel = DocxUpdatePanel(self.language)
        self.vin_panel = VinPanel(self.language)
        self.gateway_panel = GatewayDecodePanel(self.language)
        self.ops_panel = OpsPanel(self.language)
        self.settings_panel = SettingsPanel(self._settings, self.language)
        self.personal_panel = PersonalPanel(self.language)
        self.requirement_panel = RequirementPanel(self.language)
        for panel in (
            self.dashboard_panel, self.credit_panel, self.sql_panel, self.docx_panel,
            self.vin_panel, self.gateway_panel, self.ops_panel, self.settings_panel,
            self.personal_panel, self.requirement_panel,
        ):
            self.stack.addWidget(panel)
        self.dashboard_panel.open_credit.connect(lambda: self._show_panel(1))
        self.dashboard_panel.open_sql.connect(lambda: self._show_panel(2))
        self.dashboard_panel.open_docx.connect(lambda: self._show_panel(3))
        self.dashboard_panel.open_vin.connect(lambda: self._show_panel(4))
        self.dashboard_panel.open_gateway.connect(lambda: self._show_panel(5))
        self.dashboard_panel.open_ops.connect(lambda: self._show_panel(6))
        if hasattr(self.dashboard_panel, 'open_requirements'):
            self.dashboard_panel.open_requirements.connect(lambda: self._show_panel(10))
        if hasattr(self.dashboard_panel, 'open_requirement'):
            self.dashboard_panel.open_requirement.connect(self._open_requirement_from_dashboard)
        self.personal_panel.reminder_due.connect(self._show_private_notification)
        self.requirement_panel.send_to_sql.connect(self._receive_requirement_sql)
        self.requirement_panel.send_to_docx.connect(self._receive_requirement_docx)
        self.requirement_panel.add_to_daily.connect(self._add_requirement_to_daily)
        self.requirement_panel.open_system_config.connect(self._open_system_config)
        self.requirement_panel.open_release_prep.connect(self._open_release_prep)
        self.settings_panel.settings_changed.connect(self._apply_settings)
        self.settings_panel.reset_floating_position.connect(self._reset_floating_position)
        self.sql_panel.task_completed.connect(self._record_success)
        self.docx_panel.task_completed.connect(self._record_success)
        self._content_layout.addWidget(self.stack)
        layout.addWidget(content, 1)

        self.status_bar = QStatusBar()
        self.status_bar.setObjectName('status_bar')
        self.setStatusBar(self.status_bar)
        self.clock_label = QLabel()
        self.clock_label.setObjectName('clock-label')
        self.status_bar.addPermanentWidget(self.clock_label)
        self.layout_mode_changed.connect(self._broadcast_layout_mode)

    def _create_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        self._sidebar = sidebar
        sidebar.setFixedWidth(248)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(12, 14, 12, 12)
        outer.setSpacing(0)

        # 品牌区
        brand_block = QFrame()
        brand_block.setObjectName('sidebar-brand')
        self._brand_block = brand_block
        brand_layout = QHBoxLayout(brand_block)
        brand_layout.setContentsMargins(8, 8, 8, 8)
        brand_layout.setSpacing(10)
        self.brand_icon = QLabel()
        self.brand_icon.setObjectName('sidebar-brand-icon')
        self.brand_icon.setFixedSize(36, 36)
        self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_brand_icon()
        brand_layout.addWidget(self.brand_icon)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = QLabel('PengTools')
        brand.setObjectName('sidebar_title')
        self.version_label = QLabel(f'PRIVATE WORKBENCH · {APP_VERSION_LABEL}')
        self.version_label.setObjectName('sidebar_version')
        self.version_label.setToolTip(
            f'版本：{app_version_text()}\n更新日期：{APP_BUILD_DATE}\n双击解锁私人彩蛋'
        )
        brand_text.addWidget(brand)
        brand_text.addWidget(self.version_label)
        brand_layout.addLayout(brand_text, 1)
        outer.addWidget(brand_block)

        # 可滚动导航
        scroll = QScrollArea()
        scroll.setObjectName('sidebar-scroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_host = QWidget()
        nav_host.setObjectName('sidebar-nav-host')
        self._nav_layout = QVBoxLayout(nav_host)
        self._nav_layout.setContentsMargins(0, 10, 0, 0)
        self._nav_layout.setSpacing(2)

        self.nav_buttons = [None] * 11
        self._group_labels = {}
        self._nav_order = []

        for group_key, items in NAV_MODEL:
            section = QLabel()
            section.setObjectName('sidebar-section')
            self._group_labels[group_key] = section
            self._nav_layout.addWidget(section)
            for nav_index, _zh, _en, icon_role in items:
                button = QPushButton()
                button.setObjectName('nav-btn')
                button.setCheckable(True)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setProperty('navIndex', nav_index)
                button.clicked.connect(lambda checked=False, value=nav_index: self._show_panel(value))
                apply_icon(button, icon_role, size=20)
                self._nav_layout.addWidget(button)
                self.nav_buttons[nav_index] = button
                self._nav_order.append(nav_index)
                if nav_index == 8:
                    button.hide()
                    section.hide()

        self._nav_layout.addStretch(1)
        scroll.setWidget(nav_host)
        outer.addWidget(scroll, 1)

        footer_sep = QFrame()
        footer_sep.setObjectName('sidebar-sep')
        footer_sep.setFixedHeight(1)
        outer.addWidget(footer_sep)

        # 底部：设置 + 用户芯片
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(8)
        self.settings_button = QPushButton()
        self.settings_button.setObjectName('nav-btn-settings')
        self.settings_button.setCheckable(True)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(lambda checked=False: self._show_panel(7))
        apply_icon(self.settings_button, 'settings', size=20)
        self.nav_buttons[7] = self.settings_button
        footer.addWidget(self.settings_button, 1)

        self.user_chip = QToolButton()
        self.user_chip.setObjectName('user-chip')
        self.user_chip.setText('LH')
        self.user_chip.setToolTip('账户与偏好 · Ctrl+Shift+P 悬浮栏')
        self.user_chip.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.user_chip.setFixedSize(32, 32)
        self._user_menu = QMenu(self)
        self.user_chip.setMenu(self._user_menu)
        footer.addWidget(self.user_chip, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(footer)

        # 兼容旧属性（彩蛋/语言）— 隐藏控件仍供逻辑使用
        self.author_label = QLabel('Author · Lihp')
        self.author_label.hide()
        self.language_label = QLabel()
        self.language_label.hide()
        self.language_combo = QComboBox()
        self.language_combo.hide()
        size_combo(self.language_combo, 'sm')
        self.language_combo.addItems(['中文', 'English'])
        self.language_combo.currentIndexChanged.connect(self._set_language)
        self.float_hint = QLabel('Ctrl + Shift + P')
        self.float_hint.hide()
        self.build_date_label = QLabel(f'更新 {APP_BUILD_DATE}')
        self.build_date_label.hide()
        self._rebuild_user_menu()
        return sidebar

    def _rebuild_user_menu(self):
        menu = self._user_menu
        menu.clear()
        zh = self.language == 'zh'
        ver = menu.addAction(f'{app_version_text()} · {APP_BUILD_DATE}')
        ver.setEnabled(False)
        menu.addSeparator()
        lang_menu = menu.addMenu('语言' if zh else 'Language')
        act_zh = lang_menu.addAction('中文')
        act_en = lang_menu.addAction('English')
        act_zh.triggered.connect(lambda: self._set_language(0))
        act_en.triggered.connect(lambda: self._set_language(1))
        hotkey = menu.addAction('悬浮栏  Ctrl+Shift+P' if zh else 'Floating bar  Ctrl+Shift+P')
        hotkey.triggered.connect(self.toggle_quick_panel)
        menu.addSeparator()
        about = menu.addAction('关于' if zh else 'About')
        about.triggered.connect(lambda: self.status_bar.showMessage(
            f'PengTools {app_version_text()} · 离线工作台 · 构建 {APP_BUILD_DATE}', 5000
        ))
        quit_act = menu.addAction('退出软件' if zh else 'Exit')
        quit_act.triggered.connect(self.exit_application)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_controller.observe(self.width(), self.height())

    def _on_layout_mode(self, mode: str, low_height: bool):
        self._layout_mode = mode
        icon_only = is_icon_nav(mode)
        self._nav_icon_only = icon_only
        self._sidebar.setFixedWidth(nav_width_for_mode(mode))
        margin = content_margin_for_mode(mode)
        self._content_layout.setContentsMargins(margin, margin - 4, margin, 12)
        # 分组标题 / 导航文字
        for key, label in self._group_labels.items():
            if key == 'personal' and not self._private_unlocked:
                label.setVisible(False)
            else:
                label.setVisible(not icon_only)
        for index, button in enumerate(self.nav_buttons):
            if button is None:
                continue
            if index == 8 and not self._private_unlocked:
                button.hide()
                continue
            # 图标模式：只显示图标
            if icon_only:
                button.setText('')
                button.setToolTip(self._nav_tooltip(index))
                button.setProperty('iconOnly', True)
            else:
                button.setProperty('iconOnly', False)
                button.setToolTip('')
            button.style().unpolish(button)
            button.style().polish(button)
        # 品牌副标题
        self.version_label.setVisible(not icon_only and not low_height)
        self.brand_icon.setVisible(True)
        if icon_only:
            self.settings_button.setText('')
            self.settings_button.setToolTip('设置' if self.language == 'zh' else 'Settings')
        self.layout_mode_changed.emit(mode, low_height)
        # 刷新导航文案（非 icon 模式）
        if not icon_only:
            self._apply_nav_texts()

    def _nav_tooltip(self, index: int) -> str:
        return display_name(index, self.language)

    def _refresh_brand_icon(self):
        try:
            from ui.theme_manager import ThemeManager
            accent = ThemeManager.instance().token('PRIMARY_ACTIVE')
        except Exception:
            accent = '#4F735F'
        # 侧栏：36px 底板内 24px 品牌标识
        pix = brand_pixmap('app_mark', size=24, tint=accent)
        if pix.isNull():
            pix = brand_pixmap('floating', size=24, tint=accent)
        if not pix.isNull():
            self.brand_icon.setPixmap(pix)

    def _apply_nav_texts(self):
        zh = self.language == 'zh'
        for group_key, items in NAV_MODEL:
            label = self._group_labels.get(group_key)
            if label is not None:
                label.setText(GROUP_LABELS[group_key][0 if zh else 1])
            for nav_index, name_zh, name_en, icon_role in items:
                button = self.nav_buttons[nav_index]
                if button is None:
                    continue
                if not self._nav_icon_only:
                    button.setText(name_zh if zh else name_en)
                apply_icon(button, icon_role, size=20)
        if self.nav_buttons[7] is not None and not self._nav_icon_only:
            self.nav_buttons[7].setText('设置' if zh else 'Settings')
            apply_icon(self.nav_buttons[7], 'settings', size=20)

    def _broadcast_layout_mode(self, mode: str, low_height: bool):
        for panel in (
            self.dashboard_panel, self.credit_panel, self.sql_panel, self.docx_panel,
            self.vin_panel, self.gateway_panel, self.ops_panel, self.settings_panel,
            self.personal_panel, self.requirement_panel,
        ):
            if hasattr(panel, 'apply_layout_mode'):
                try:
                    panel.apply_layout_mode(mode, low_height)
                except Exception:
                    pass

    def _show_panel(self, index):
        if index == 8 and not self._private_unlocked:
            return
        self._current_nav_index = index
        stack_index = 8 if index in (8, 9) else (9 if index == 10 else index)
        if index == 8:
            self.personal_panel.open_learning()
        elif index == 9:
            self.personal_panel.open_daily_report()
        elif index == 10:
            self.requirement_panel.refresh_systems()
        self.stack.setCurrentIndex(stack_index)
        for position, button in enumerate(self.nav_buttons):
            if button is not None:
                button.setChecked(position == index)
        statuses_zh = [
            '离线工作台已就绪', '个人与单位证件模拟生成', 'SQL 脚本整理、回滚与验证',
            'SQL 驱动接口文档更新', '中国车辆 VIN 测试数据', '网关国密解密 · XML 工具',
            'Linux 运维命令搜索与安全引导', '界面与悬浮工具栏设置',
            '自我学习资料整理与全文搜索', '每日日报与定时提醒', '需求归档、上线台账与工具联动',
        ]
        statuses_en = [
            'Offline workspace ready', 'Personal and unit document test data',
            'SQL classify, validate and export', 'SQL-driven interface document updater',
            'China vehicle VIN test data', 'Gateway SM crypto · XML tools',
            'Linux operations command search and safety guidance',
            'Interface and floating toolbar settings',
            'Learning library and full-text search', 'Daily reports and reminders',
            'Requirement tracking and tool links',
        ]
        self.status_bar.showMessage((statuses_zh if self.language == 'zh' else statuses_en)[index])

    def _open_requirement_from_dashboard(self, requirement):
        self._show_panel(10)
        try:
            self.requirement_panel.focus_requirement(requirement)
        except Exception:
            pass

    def _open_system_config(self):
        self.sql_panel.refresh_config()
        self.sql_panel.tabs.setCurrentIndex(2)
        self._show_panel(2)

    def _open_release_prep(self, requirement=None):
        self.sql_panel.refresh_config()
        self.sql_panel.tabs.setCurrentIndex(0)
        date_text = ''
        if isinstance(requirement, dict):
            date_text = str(
                requirement.get('actual_online_date')
                or requirement.get('planned_online_date')
                or ''
            )[:10]
            if not date_text and requirement.get('online_month'):
                month = str(requirement.get('online_month'))
                if len(month) >= 7:
                    date_text = f'{month[:7]}-01'
        if date_text:
            from PyQt6.QtCore import QDate
            parsed = QDate.fromString(date_text, 'yyyy-MM-dd')
            if parsed.isValid():
                self.sql_panel.release_date.setDate(parsed)
        self.sql_panel._load_release_candidates()
        self._show_panel(2)
        title = ''
        if isinstance(requirement, dict):
            title = requirement.get('title') or requirement.get('code') or ''
        if title:
            self.status_bar.showMessage(f'已进入升级准备，并刷新候选（来自：{title}）', 5000)
        else:
            self.status_bar.showMessage('已进入升级准备', 3000)

    def _set_language(self, combo_index):
        self.language = 'zh' if combo_index == 0 else 'en'
        self._language_index = combo_index
        if self.language_combo.currentIndex() != combo_index:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(combo_index)
            self.language_combo.blockSignals(False)
        self._apply_nav_texts()
        self._rebuild_user_menu()
        for panel in (
            self.dashboard_panel, self.credit_panel, self.sql_panel, self.docx_panel,
            self.vin_panel, self.gateway_panel, self.ops_panel, self.settings_panel,
            self.personal_panel, self.requirement_panel,
        ):
            if hasattr(panel, 'set_language'):
                panel.set_language(self.language)
        self.quick_panel.set_language(self.language)
        if hasattr(self, 'tray_service'):
            self.tray_service.set_language(self.language)
        self._show_panel(self._current_nav_index)

    def _apply_settings(self, settings):
        self._settings = dict(settings)
        app = QApplication.instance()
        font = app.font()
        font.setPointSize(max(8, int(self._settings['font_size']) - 2))
        app.setFont(font)
        # 主题即时注入（唯一 QSS 入口）
        try:
            from ui.theme_manager import ThemeManager, DEFAULT_THEME_ID
            ThemeManager.instance().apply(
                app,
                self._settings.get('ui_theme', DEFAULT_THEME_ID),
                font_size=self._settings.get('font_size', 12),
            )
        except Exception:
            base_qss = app.property('base_stylesheet') or app.styleSheet()
            app.setStyleSheet(base_qss + f"\nQWidget {{ font-size: {self._settings['font_size']}px; }}")
        # 导航图标随主题重新染色
        self._apply_nav_texts()
        self._refresh_brand_icon()
        self.quick_panel.apply_preferences(
            self._settings['floating_opacity'], self._settings['floating_always_on_top']
        )
        self.quick_panel.apply_shortcuts(
            self._settings.get('floating_shortcuts'),
            private_unlocked=self._private_unlocked,
        )
        self.quick_panel.refresh_brand_icons()
        if hasattr(self, 'tray_service'):
            self.tray_service.refresh_icon()
        self.ops_panel.set_copy_feedback_duration(self._settings['copy_feedback_ms'])
        self.keep_awake_service.apply_preferences(
            self._settings['keep_awake_enabled'],
            self._settings['keep_awake_interval_minutes'],
        )
        wanted_index = 0 if self._settings['default_language'] == 'zh' else 1
        if self._language_index != wanted_index:
            self._set_language(wanted_index)
        self.status_bar.showMessage('设置已应用并保存' if self.language == 'zh' else 'Settings applied and saved', 3000)

    def _open_floating_shortcuts_editor(self):
        from ui.floating_shortcuts_editor import open_floating_shortcuts_editor
        open_floating_shortcuts_editor(
            self,
            self._settings,
            language=self.language,
            private_unlocked=self._private_unlocked,
            on_saved=self._apply_settings,
        )

    def apply_theme(self, theme_id: str) -> None:
        """设置页主题卡即时预览入口。"""
        self._settings['ui_theme'] = theme_id
        from config import save_settings
        self._settings = save_settings(self._settings)
        self._apply_settings(self._settings)

    def _reset_floating_position(self):
        self.quick_panel.reset_position()
        self.status_bar.showMessage(
            '悬浮工具栏已重置到屏幕右侧' if self.language == 'zh' else 'Floating toolbar reset to screen right',
            3000,
        )

    def _setup_hotkeys(self):
        self.hotkey_service = HotkeyService(QApplication.instance(), self.quick_panel.show_panel)
        self.hotkey_service.registration_failed.connect(
            lambda: self.status_bar.showMessage('Ctrl+Shift+P 已被其他程序占用', 5000)
        )
        self.hotkey_service.register()

    def _setup_clock(self):
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _update_clock(self):
        self.clock_label.setText(datetime.datetime.now().strftime('%Y-%m-%d  %H:%M:%S'))

    def toggle_quick_panel(self):
        self.quick_panel.show_panel()

    def navigate_to(self, index):
        self._show_panel(index)

    def _unlock_private_tools(self):
        if self._private_unlocked:
            return True
        key, accepted = QInputDialog.getText(
            self, 'PengTools 彩蛋', '请输入私人功能密钥：', QLineEdit.EchoMode.Password
        )
        if not accepted:
            return False
        if key != 'Lihp':
            QMessageBox.warning(self, 'PengTools 彩蛋', '密钥不正确。')
            return False
        self._private_unlocked = True
        if self.nav_buttons[8] is not None:
            self.nav_buttons[8].show()
        personal_label = self._group_labels.get('personal')
        if personal_label is not None and not self._nav_icon_only:
            personal_label.show()
        self.quick_panel.set_private_unlocked(True)
        self.status_bar.showMessage('彩蛋已解锁：自我学习已开启', 7000)
        self._show_panel(8)
        return True

    def _show_private_notification(self, title, message):
        self.tray_service.show_message(title, message)

    def _receive_requirement_sql(self, title, sql):
        self.sql_panel._append_sql_parts([(title, sql)], 'paste')
        self._show_panel(2)
        self.status_bar.showMessage(f'已把“{title}”的 SQL 发送到 SQL 整理', 5000)

    def _receive_requirement_docx(self, title, sql):
        current = self.docx_panel.sql_editor.toPlainText().strip()
        block = f'-- 来源需求：{title}\n{sql}'
        self.docx_panel.sql_editor.setPlainText('\n\n'.join(part for part in (current, block) if part))
        self._show_panel(3)
        self.status_bar.showMessage(f'已把“{title}”的结构 SQL 发送到接口文档更新', 5000)

    def _add_requirement_to_daily(self, requirement):
        self.personal_panel.add_requirement_to_daily(requirement)
        self._show_panel(9)

    def _record_success(self):
        self._completed_tasks += 1
        if self._completed_tasks % 7 == 0:
            message = (
                '数据库没有情绪，但今天它选择配合。' if self.language == 'zh'
                else 'Databases have no feelings, but today this one chose cooperation.'
            )
            self.status_bar.showMessage(message, 7000)

    def eventFilter(self, watched, event):
        if watched is self.version_label and event.type() == QEvent.Type.MouseButtonDblClick:
            self._unlock_private_tools()
            return True
        if watched is self.clock_label and event.type() == QEvent.Type.MouseButtonDblClick:
            self.status_bar.showMessage(
                '这不是摸鱼，是在等待进度条完成它的艺术表演。' if self.language == 'zh'
                else 'Not procrastination—just letting the progress bar finish its performance art.',
                7000,
            )
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        action = 'exit' if self._force_exit else self._settings['close_default_action']
        if not self._force_exit and self._settings['close_ask_each_time']:
            result = self._ask_close_action()
            if result is None:
                event.ignore()
                return
            action, dont_ask = result
            if dont_ask and action in ('minimize', 'exit'):
                self._settings['close_ask_each_time'] = False
                self._settings['close_default_action'] = action
                self._settings = save_settings(self._settings)
                if hasattr(self, 'settings_panel'):
                    self.settings_panel.load_values(self._settings)
        if action != 'exit':
            event.ignore()
            if action == 'minimize':
                self.hide()
            return
        self._shutdown(event)

    def _ask_close_action(self):
        return ask_close_action(
            self,
            language=self.language,
            default_action=self._settings.get('close_default_action', 'minimize'),
        )

    def exit_application(self):
        self._force_exit = True
        self.close()

    def _shutdown(self, event):
        if self._shutting_down:
            event.accept()
            return
        self._shutting_down = True
        if self.hotkey_service:
            self.hotkey_service.unregister()
        keep_awake_service = getattr(self, 'keep_awake_service', None)
        if keep_awake_service:
            keep_awake_service.stop()
        self.quick_panel.close()
        self.tray_service.hide()
        event.accept()
        QApplication.instance().quit()
