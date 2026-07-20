# -*- coding: utf-8 -*-
import datetime

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QInputDialog, QLineEdit, QMessageBox, QPushButton, QStackedWidget,
    QStatusBar, QVBoxLayout, QWidget,
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
from ui.quick_panel import QuickPanel
from ui.tray_service import TrayService
from config import APP_BUILD_DATE, APP_VERSION_LABEL, app_version_text, load_settings, save_settings


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        self.language = self._settings['default_language']
        self.hotkey_service = None
        self._force_exit = False
        self._shutting_down = False
        self._private_unlocked = False
        self._current_nav_index = 0
        self.setWindowTitle(f'PengTools Hub {app_version_text(with_date=False)}')
        # 允许窗口缩小，布局内部用 splitter / stretch 自适应
        self.setMinimumSize(960, 640)
        self.resize(1220, 780)
        self._center_on_screen()
        self._setup_ui()
        self._egg_clicks = 0
        self._completed_tasks = 0
        self.author_label.installEventFilter(self)
        self.version_label.installEventFilter(self)
        self.clock_label.installEventFilter(self)
        self.quick_panel = QuickPanel(self, self.language)
        self.settings_panel.floating_opacity_preview.connect(self.quick_panel.set_opacity)
        self.quick_panel.apply_preferences(
            self._settings['floating_opacity'], self._settings['floating_always_on_top']
        )
        if self._settings['floating_show_on_startup']:
            self.quick_panel.show()
        self.tray_service = TrayService(self)
        self.keep_awake_service = KeepAwakeService(self)
        self._setup_hotkeys()
        self._setup_clock()
        language_index = 0 if self.language == 'zh' else 1
        self.language_combo.setCurrentIndex(language_index)
        self._set_language(language_index)
        self._apply_settings(self._settings)

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
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(12)
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
        for panel in (self.dashboard_panel, self.credit_panel, self.sql_panel, self.docx_panel, self.vin_panel, self.gateway_panel, self.ops_panel, self.settings_panel, self.personal_panel, self.requirement_panel):
            self.stack.addWidget(panel)
        self.dashboard_panel.open_credit.connect(lambda: self._show_panel(1))
        self.dashboard_panel.open_sql.connect(lambda: self._show_panel(2))
        self.dashboard_panel.open_docx.connect(lambda: self._show_panel(3))
        self.dashboard_panel.open_vin.connect(lambda: self._show_panel(4))
        self.dashboard_panel.open_gateway.connect(lambda: self._show_panel(5))
        self.dashboard_panel.open_ops.connect(lambda: self._show_panel(6))
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
        content_layout.addWidget(self.stack)
        layout.addWidget(content, 1)

        self.status_bar = QStatusBar()
        self.status_bar.setObjectName('status_bar')
        self.setStatusBar(self.status_bar)
        self.clock_label = QLabel()
        self.clock_label.setObjectName('clock-label')
        self.status_bar.addPermanentWidget(self.clock_label)

    def _create_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(236)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(4)

        brand_block = QFrame()
        brand_block.setObjectName('sidebar-brand')
        brand_layout = QVBoxLayout(brand_block)
        brand_layout.setContentsMargins(12, 12, 12, 12)
        brand_layout.setSpacing(2)
        brand = QLabel('PengTools')
        brand.setObjectName('sidebar_title')
        self.version_label = QLabel(f'UTILITY HUB  ·  {APP_VERSION_LABEL}')
        self.version_label.setObjectName('sidebar_version')
        self.version_label.setToolTip(f'版本：{app_version_text()}\n更新日期：{APP_BUILD_DATE}\n双击解锁私人彩蛋')
        brand_layout.addWidget(brand)
        brand_layout.addWidget(self.version_label)
        self.build_date_label = QLabel(f'更新 {APP_BUILD_DATE}')
        self.build_date_label.setObjectName('sidebar_version')
        self.build_date_label.setToolTip(f'本机构建/打包日期：{APP_BUILD_DATE}')
        brand_layout.addWidget(self.build_date_label)
        layout.addWidget(brand_block)

        self.nav_section_label = QLabel('工作区')
        self.nav_section_label.setObjectName('sidebar-section')
        layout.addWidget(self.nav_section_label)

        self.nav_buttons = [None] * 11
        for index in (*range(7), 8, 9, 10):
            button = QPushButton()
            button.setObjectName('nav-btn')
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, value=index: self._show_panel(value))
            layout.addWidget(button)
            self.nav_buttons[index] = button
            if index == 8:
                button.hide()
        layout.addStretch(1)

        footer_sep = QFrame()
        footer_sep.setObjectName('sidebar-sep')
        footer_sep.setFixedHeight(1)
        layout.addWidget(footer_sep)

        # 设置固定在左下角，独立 objectName 便于与主导航做轻微视觉区分
        self.settings_button = QPushButton()
        self.settings_button.setObjectName('nav-btn-settings')
        self.settings_button.setCheckable(True)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(lambda checked=False: self._show_panel(7))
        self.nav_buttons[7] = self.settings_button
        layout.addWidget(self.settings_button)
        self._apply_nav_icons()
        self.author_label = QLabel('Author · Lihp')
        self.author_label.setObjectName('author-label')
        layout.addWidget(self.author_label)
        self.language_label = QLabel()
        self.language_label.setObjectName('small-label')
        layout.addWidget(self.language_label)
        self.language_combo = QComboBox()
        size_combo(self.language_combo, 'sm')
        self.language_combo.addItems(['中文', 'English'])
        self.language_combo.currentIndexChanged.connect(self._set_language)
        layout.addWidget(self.language_combo)
        self.float_hint = QLabel('Ctrl + Shift + P')
        self.float_hint.setObjectName('hotkey-pill')
        self.float_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.float_hint)
        return sidebar

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
            button.setChecked(position == index)
        statuses_zh = ['离线工作台已就绪', '个人与单位证件模拟生成', 'SQL 脚本整理、回滚与验证', 'SQL 驱动接口文档更新', '中国车辆 VIN 测试数据', '网关国密解密 · XML 工具', 'Linux 运维命令搜索与安全引导', '界面与悬浮工具栏设置', '自我学习资料整理与全文搜索', '每日日报与定时提醒', '需求归档、上线台账与工具联动']
        statuses_en = ['Offline workspace ready', 'Personal and unit document test data', 'SQL classify, validate and export', 'SQL-driven interface document updater', 'China vehicle VIN test data', 'Gateway SM crypto · XML tools', 'Linux operations command search and safety guidance', 'Interface and floating toolbar settings', 'Learning library and full-text search', 'Daily reports and reminders', 'Requirement tracking and tool links']
        self.status_bar.showMessage((statuses_zh if self.language == 'zh' else statuses_en)[index])

    def _open_system_config(self):
        self.sql_panel.refresh_config()
        self.sql_panel.tabs.setCurrentIndex(2)
        self._show_panel(2)

    def _open_release_prep(self, requirement=None):
        """从需求工作台跳转到升级准备，并按需求日期刷新候选。"""
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

    def _apply_nav_icons(self):
        """主导航挂本地 SVG（有图标的模块）；无图标项保持纯文字。"""
        try:
            from ui.icons import apply_icon
        except Exception:
            return
        # index → 图标角色（resources/icons）
        mapping = {
            2: 'release',
            5: 'shield-key',
            7: 'settings',
            10: 'requirements',
        }
        for index, role in mapping.items():
            button = self.nav_buttons[index] if index < len(self.nav_buttons) else None
            if button is not None:
                apply_icon(button, role, size=18)

    def _set_language(self, combo_index):
        self.language = 'zh' if combo_index == 0 else 'en'
        zh = self.language == 'zh'
        names = (
            ['工作台', '证件类型', '升级准备', '接口文档更新', '车辆 VIN', '加解密', '运维助手', '设置', '自我学习', '日报', '需求管理']
            if zh else ['Workspace', 'Documents', 'SQL Processing', 'Interface Docs', 'Vehicle VIN', 'Crypto', 'Operations', 'Settings', 'Learning', 'Daily Report', 'Requirements']
        )
        for button, name in zip(self.nav_buttons, names):
            button.setText(name)
        self._apply_nav_icons()
        if hasattr(self, 'nav_section_label'):
            self.nav_section_label.setText('工作区' if zh else 'WORKSPACE')
        self.language_label.setText('界面语言' if zh else 'Language')
        for panel in (self.dashboard_panel, self.credit_panel, self.sql_panel, self.docx_panel, self.vin_panel, self.gateway_panel, self.ops_panel, self.settings_panel, self.personal_panel, self.requirement_panel):
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
        base_qss = app.property('base_stylesheet') or app.styleSheet()
        app.setStyleSheet(base_qss + f"\nQWidget {{ font-size: {self._settings['font_size']}px; }}")
        self.quick_panel.apply_preferences(
            self._settings['floating_opacity'], self._settings['floating_always_on_top']
        )
        self.ops_panel.set_copy_feedback_duration(self._settings['copy_feedback_ms'])
        self.keep_awake_service.apply_preferences(
            self._settings['keep_awake_enabled'],
            self._settings['keep_awake_interval_minutes'],
        )
        wanted_index = 0 if self._settings['default_language'] == 'zh' else 1
        if self.language_combo.currentIndex() != wanted_index:
            self.language_combo.setCurrentIndex(wanted_index)
        self.status_bar.showMessage('设置已应用并保存' if self.language == 'zh' else 'Settings applied and saved', 3000)

    def _reset_floating_position(self):
        self.quick_panel.reset_position()
        self.status_bar.showMessage('悬浮工具栏已重置到屏幕右侧' if self.language == 'zh' else 'Floating toolbar reset to screen right', 3000)

    def _setup_hotkeys(self):
        self.hotkey_service = HotkeyService(QApplication.instance(), self.quick_panel.show_panel)
        self.hotkey_service.registration_failed.connect(
            lambda: self.status_bar.showMessage('Ctrl+Shift+P 已被其他程序占用')
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
        self.nav_buttons[8].show()
        self.status_bar.showMessage('彩蛋已解锁：自我学习已开启', 7000)
        self._show_panel(8)
        return True

    def _show_private_notification(self, title, message):
        if hasattr(self, 'tray_service'):
            self.tray_service.show_notification(title, message)

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
            message = ('数据库没有情绪，但今天它选择配合。' if self.language == 'zh'
                       else 'Databases have no feelings, but today this one chose cooperation.')
            self.status_bar.showMessage(message, 7000)

    def eventFilter(self, watched, event):
        if watched is self.version_label and event.type() == QEvent.Type.MouseButtonDblClick:
            self._unlock_private_tools()
            return True
        if watched is self.author_label and event.type() == QEvent.Type.MouseButtonRelease:
            self._egg_clicks += 1
            if self._egg_clicks >= 5:
                self._egg_clicks = 0
                message = ('Lihp 专家模式已开启：Bug 看到你，已经开始写检讨了。' if self.language == 'zh'
                           else 'Lihp expert mode: the bugs have started writing apology letters.')
                self.status_bar.showMessage(message, 7000)
        elif watched is self.clock_label and event.type() == QEvent.Type.MouseButtonDblClick:
            message = ('这不是摸鱼，是在等待进度条完成它的艺术表演。' if self.language == 'zh'
                       else 'Not procrastination—just letting the progress bar finish its performance art.')
            self.status_bar.showMessage(message, 7000)
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        action = 'exit' if self._force_exit else self._settings['close_default_action']
        if not self._force_exit and self._settings['close_ask_each_time']:
            result = self._ask_close_action()
            if result is None:
                # 取消关闭
                event.ignore()
                return
            action, dont_ask = result
            if dont_ask and action in ('minimize', 'exit'):
                # 「不再提示」：写回设置，下次直接走默认操作（懒人）
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
        """返回 (action, dont_ask_again) 或 None（取消）。"""
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
