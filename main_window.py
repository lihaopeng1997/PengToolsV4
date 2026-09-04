# -*- coding: utf-8 -*-
import datetime
import os

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QInputDialog, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QStatusBar, QToolButton, QVBoxLayout, QWidget,
)

# 业务面板按需 import/构造（见 _ensure_*），避免启动时一次加载全部模块
from ui.confirm_dialog import ask_close_action
from ui.field_metrics import size_combo
from ui.icons import NAV_ICON_BY_INDEX, apply_icon, brand_pixmap, qicon
from ui.navigation_model import (
    GROUP_LABELS, NAV_MODEL, SQL_CONSOLE_NAV, AI_PARENT_NAV, AI_CHAT_NAV, AI_WORKBENCH_NAV,
    SQL_DB_NAV_START, SQL_DB_NAV_MAX, FIXED_DB_PAGES,
    dialect_for_nav, display_name, icon_role_for, is_parent_nav,
    nav_is_db_slot, resolve_db_slot_index,
)
from ui import web_shell as _web_shell
WEB_SHELL_AVAILABLE = _web_shell.WEB_SHELL_AVAILABLE
from ui.web_diagnostics import log_web_event
from ui.responsive import LayoutModeController, NAV_ICON, content_margin_for_mode, is_icon_nav, nav_width_for_mode
from config import (
    APP_BUILD_DATE, APP_NAME, APP_VERSION_LABEL, app_version_text,
    load_settings, normalize_settings, save_settings,
)

# stack index → 属性名（与 _stack_index_for_nav 一致）
# 0–12 历史内置面板；13=模型聊天；14=Agent 工作台；15–20=六数据库面板
_BUILTIN_ATTRS = (
    'dashboard_panel', 'credit_panel', 'sql_panel', 'docx_panel',
    'vin_panel', 'gateway_panel', 'ops_panel', 'settings_panel',
    'personal_panel', 'requirement_panel', 'format_panel',
    'interface_debug_panel', 'ops_log_panel', 'model_chat_panel',
    'agent_workbench_panel',
)
DB_ATTRS = tuple(f'db_panel_{i}' for i in range(32))
_STACK_PANEL_ATTRS = _BUILTIN_ATTRS + DB_ATTRS
STACK_DB_START = 15  # stack index 15 → nav 18 (SQL_DB_NAV_START) Oracle 面板


class MainWindow(QMainWindow):
    layout_mode_changed = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        self.language = self._settings['default_language']
        self.hotkey_service = None
        self.tray_service = None
        self.keep_awake_service = None
        self.quick_panel = None
        self._force_exit = False
        self._shutting_down = False
        self._startup_ready = False
        self._boot_step = 0
        self._user_navigated = False
        # 从 data/settings.json 恢复彩蛋解锁（升级替换程序文件不丢）
        self._private_unlocked = bool(self._settings.get('private_unlocked', False))
        self._current_nav_index = 0
        self._layout_mode = 'standard'
        self._nav_icon_only = False
        self._nav_collapsed = bool(self._settings.get('sidebar_collapsed', False))
        self.setWindowTitle(APP_NAME)  # V2：标题不再带版本/版本文案
        self.setMinimumSize(960, 640)
        self.resize(1440, 900)
        self._center_on_screen()
        self._layout_controller = LayoutModeController(self)
        self._layout_controller.layout_mode_changed.connect(self._on_layout_mode)
        # 面板槽位先占位，仅工作台即时创建 → 主窗口可立刻 show
        for attr in _STACK_PANEL_ATTRS:
            setattr(self, attr, None)
        self._setup_ui_shell()
        self._egg_clicks = 0
        self._completed_tasks = 0
        self.version_label.installEventFilter(self)
        self.clock_label.installEventFilter(self)
        self.user_chip.installEventFilter(self)
        self._setup_clock()
        language_index = 0 if self.language == 'zh' else 1
        self._language_index = language_index
        self._apply_nav_texts()
        self._apply_density_preferences(self._settings, apply_startup_sidebar=True)
        # 先建工作台，用户立刻看到首页骨架
        self._ensure_dashboard_panel()
        self.stack.setCurrentIndex(0)
        log_web_event(
            'renderers_initialized',
            main_shell=self.main_shell_renderer,
            dashboard=self.dashboard_renderer,
        )
        self._show_startup_loading('正在加载模块…' if self.language == 'zh' else 'Loading modules…')
        # 测试/offscreen：同步 boot，避免用例拿到半成品窗口
        if os.environ.get('QT_QPA_PLATFORM') == 'offscreen' or os.environ.get('PENGTOOLS_SYNC_BOOT') == '1':
            self._complete_startup_sync()
        else:
            QTimer.singleShot(0, self._startup_tick)

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def _make_stack_host(self):
        host = QWidget()
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        return host, layout

    def _setup_ui_shell(self):
        """仅壳：侧栏 + Stack 占位 + 状态栏。重面板延后构造。"""
        central = QWidget()
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # ── V2 Web 铬层：可用且未禁用时启用（侧栏双页栈：0=原生保底 1=Web）──
        ui_web_setting = bool(self._settings.get('ui_web_shell', True))
        runtime_web_available = _web_shell.runtime_web_shell_available()
        self._web_shell_enabled = (
            runtime_web_available
            and ui_web_setting
        )
        chrome_local_path = _web_shell.webui_url('vue/chrome.html').toLocalFile()
        dashboard_local_path = _web_shell.webui_url('vue/dashboard.html').toLocalFile()
        log_web_event(
            'startup_web_diagnostics',
            web_shell_available=_web_shell.WEB_SHELL_AVAILABLE,
            import_error=getattr(_web_shell, 'WEB_SHELL_IMPORT_ERROR', ''),
            runtime_available=runtime_web_available,
            ui_web_shell_setting=ui_web_setting,
            web_shell_enabled=self._web_shell_enabled,
            chrome_resolved_path=chrome_local_path,
            chrome_exists=os.path.exists(chrome_local_path),
            dashboard_resolved_path=dashboard_local_path,
            dashboard_exists=os.path.exists(dashboard_local_path),
        )
        self._chrome_bridge = None
        self._dash_web = None
        self._sidebar_stack = None
        self._web_health = _web_shell.WebHealthTracker(expected=('chrome', 'dashboard'), parent=self)
        self._web_timeout_timer = QTimer(self)
        self._web_timeout_timer.setSingleShot(True)
        self._web_timeout_timer.timeout.connect(self._on_web_shell_timeout)
        self._web_shell_ready_announced = False
        if self._web_shell_enabled:
            log_web_event('web_shell_starting', pages='chrome,dashboard')
            self._chrome_bridge = _web_shell.HomeBridge(self)
            self._chrome_bridge.set_nav_model(self._build_web_nav_model())
            self._chrome_bridge.set_username(str(self._settings.get('home_username') or 'Lihp'))
            self._chrome_bridge.navigateRequested.connect(self._show_panel)
            self._chrome_bridge.paletteRequested.connect(self._open_quick_panel)
            self._chrome_bridge.set_summary_provider(self._dashboard_summary_payload)
            self._chrome_bridge.pageReadyReceived.connect(self._on_web_page_ready)
            self._chrome_web = _web_shell.create_chrome_widget(self._chrome_bridge)
            self._chrome_web.web_view.loadFinished.connect(
                lambda ok: self._on_web_load_finished('chrome', ok))
            self._chrome_web.web_page.renderProcessTerminated.connect(
                lambda status, code: self._on_web_render_terminated('chrome', status, code))
            side_stack = QStackedWidget()
            side_stack.addWidget(self._create_legacy_sidebar())
            side_stack.addWidget(self._chrome_web)
            side_stack.setCurrentIndex(1)
            self._sidebar_stack = side_stack
            layout.addWidget(side_stack)
            self._web_timeout_timer.start(10000)
        else:
            reason = (
                'ui_web_shell_setting_disabled'
                if not ui_web_setting
                else 'runtime_web_shell_unavailable'
            )
            log_web_event('web_shell_disabled_startup', reason=reason)
            layout.addWidget(self._create_legacy_sidebar())

        content = QFrame()
        content.setObjectName('content_area')
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._content_frame = content
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(24, 20, 24, 16)
        self._content_layout.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(
            self.stack.sizePolicy().horizontalPolicy(),
            self.stack.sizePolicy().verticalPolicy(),
        )
        self._panel_hosts = []
        self._panel_host_layouts = []
        for _ in range(len(_STACK_PANEL_ATTRS)):
            host, host_layout = self._make_stack_host()
            self._panel_hosts.append(host)
            self._panel_host_layouts.append(host_layout)
            self.stack.addWidget(host)
        # 兼容旧引用：接口/日志 host
        self._iface_host = self._panel_hosts[11]
        self._iface_host_layout = self._panel_host_layouts[11]
        self._ops_log_host = self._panel_hosts[12]
        self._ops_log_host_layout = self._panel_host_layouts[12]

        # ── V2 Web 首页视图（不挂 Stack，等原生首页 mount 时合成 holder 页）──
        if self._web_shell_enabled:
            self._dash_bridge = _web_shell.HomeBridge(self)
            self._dash_bridge.set_username(str(self._settings.get('home_username') or 'Lihp'))
            self._dash_bridge.set_summary_provider(self._dashboard_summary_payload)
            self._dash_bridge.pageReadyReceived.connect(self._on_web_page_ready)
            self._dash_bridge.navigateRequested.connect(self._show_panel)
            self._dash_web = _web_shell.create_dashboard_widget(self._dash_bridge)
            self._dash_web.web_view.loadFinished.connect(
                lambda ok: self._on_web_load_finished('dashboard', ok))
            self._dash_web.web_page.renderProcessTerminated.connect(
                lambda status, code: self._on_web_render_terminated('dashboard', status, code))
            self._dash_holder = None
            self._sync_web_theme()

        self._content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)

        self.status_bar = QStatusBar()
        self.status_bar.setObjectName('status_bar')
        self.setStatusBar(self.status_bar)
        self.clock_label = QLabel()
        self.clock_label.setObjectName('clock-label')
        self.status_bar.addPermanentWidget(self.clock_label)
        self.layout_mode_changed.connect(self._broadcast_layout_mode)

        from ui.aurora_progress import AuroraProgress
        self._startup_loading = AuroraProgress(self.centralWidget())
        self._startup_loading.hide()

    def _show_startup_loading(self, message: str):
        if not hasattr(self, '_startup_loading') or self._startup_loading is None:
            return
        self._startup_loading.setParent(self.centralWidget())
        self._startup_loading.start_busy(message)
        self._startup_loading.place_overlay(self.centralWidget())
        self._startup_loading.raise_()

    def _hide_startup_loading(self):
        if hasattr(self, '_startup_loading') and self._startup_loading is not None:
            if hasattr(self._startup_loading, 'hide_now'):
                self._startup_loading.hide_now()
                return
            try:
                self._startup_loading._timer.stop()
            except Exception:
                pass
            self._startup_loading.hide()

    def _mount_panel(self, stack_index: int, panel: QWidget):
        """把面板挂到 Stack 固定下标；替换占位 host，使 currentWidget() 即业务面板。"""
        old = self.stack.widget(stack_index)
        was_current = self.stack.currentIndex() == stack_index
        # 先 insert 再 remove，避免 index 漂移
        self.stack.insertWidget(stack_index, panel)
        if old is not None and old is not panel:
            self.stack.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        # insert 后同 index 可能变成 panel 在 stack_index，再清掉挤到 stack_index+1 的旧件
        # 上面 remove 已处理；再校正 current
        if was_current:
            self.stack.setCurrentIndex(stack_index)
        if stack_index < len(self._panel_hosts):
            self._panel_hosts[stack_index] = panel
        if stack_index == 11:
            self._iface_host = panel
        elif stack_index == 12:
            self._ops_log_host = panel

    def _apply_panel_chrome(self, panel):
        if panel is None:
            return
        if hasattr(panel, 'set_language'):
            try:
                panel.set_language(self.language)
            except Exception:
                pass
        if hasattr(panel, 'apply_layout_mode'):
            try:
                panel.apply_layout_mode(self._layout_mode, False)
            except Exception:
                pass

    def _ensure_dashboard_panel(self):
        if self.dashboard_panel is not None:
            return self.dashboard_panel
        from panels.dashboard_panel import DashboardPanel
        panel = DashboardPanel(self.language)
        panel.open_credit.connect(lambda: self._show_panel(1))
        panel.open_sql.connect(lambda: self._show_panel(2))
        panel.open_docx.connect(lambda: self._show_panel(3))
        panel.open_vin.connect(lambda: self._show_panel(4))
        panel.open_gateway.connect(lambda: self._show_panel(5))
        panel.open_ops.connect(lambda: self._show_panel(13))
        if hasattr(panel, 'open_ai_workbench'):
            panel.open_ai_workbench.connect(lambda: self._show_panel(SQL_DB_NAV_START))
        if hasattr(panel, 'open_requirements'):
            panel.open_requirements.connect(lambda: self._show_panel(10))
        if hasattr(panel, 'open_requirement'):
            panel.open_requirement.connect(self._open_requirement_from_dashboard)
        if getattr(self, '_web_shell_enabled', False) and getattr(self, '_dash_web', None) is not None:
            # Stack[0] = holder：0=V2 Web 首页  1=原生首页（加载失败/关闭 web 壳时回退显示）
            from PyQt6.QtWidgets import QStackedWidget as _QSW
            self._dash_holder = _QSW()
            self._dash_holder.addWidget(self._dash_web)
            self._dash_holder.addWidget(panel)
            self._dash_holder.setCurrentIndex(0)
            self._mount_panel(0, self._dash_holder)
            if hasattr(self._dash_web, 'web_view') and hasattr(self._dash_web.web_view, 'loadFinished'):
                self._dash_web.web_view.loadFinished.connect(lambda ok: (not ok) and self._disable_web_shell_live('dashboard_load_failed'))
        else:
            self._mount_panel(0, panel)
        self.dashboard_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_credit_panel(self):
        if self.credit_panel is not None:
            return self.credit_panel
        from panels.credit_panel import CreditCodePanel
        panel = CreditCodePanel()
        self._mount_panel(1, panel)
        self.credit_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_sql_panel(self):
        if self.sql_panel is not None:
            return self.sql_panel
        from panels.sql_panel import SqlToolPanel
        panel = SqlToolPanel()
        panel.task_completed.connect(self._record_success)
        self._mount_panel(2, panel)
        self.sql_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_docx_panel(self):
        if self.docx_panel is not None:
            return self.docx_panel
        from panels.docx_panel import DocxUpdatePanel
        panel = DocxUpdatePanel(self.language)
        panel.task_completed.connect(self._record_success)
        self._mount_panel(3, panel)
        self.docx_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_vin_panel(self):
        if self.vin_panel is not None:
            return self.vin_panel
        from panels.vin_panel import VinPanel
        panel = VinPanel(self.language)
        self._mount_panel(4, panel)
        self.vin_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_gateway_panel(self):
        if self.gateway_panel is not None:
            return self.gateway_panel
        from panels.gateway_panel import GatewayDecodePanel
        panel = GatewayDecodePanel(self.language)
        panel.open_format_xml.connect(self._open_format_xml)
        panel.open_interface_debug.connect(lambda: self._show_panel(12))
        self._mount_panel(5, panel)
        self.gateway_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_ops_panel(self):
        if self.ops_panel is not None:
            return self.ops_panel
        from panels.ops_panel import OpsPanel
        panel = OpsPanel(self.language)
        self._mount_panel(6, panel)
        self.ops_panel = panel
        self._apply_panel_chrome(panel)
        if self._settings:
            try:
                panel.set_copy_feedback_duration(self._settings.get('copy_feedback_ms', 1500))
            except Exception:
                pass
        return panel

    def _ensure_settings_panel(self):
        if self.settings_panel is not None:
            return self.settings_panel
        from panels.settings_panel import SettingsPanel
        panel = SettingsPanel(self._settings, self.language)
        panel.settings_changed.connect(self._apply_settings)
        panel.reset_floating_position.connect(self._reset_floating_position)
        panel.layout_prefs_reset.connect(self._on_layout_prefs_reset)
        self._mount_panel(7, panel)
        self.settings_panel = panel
        self._apply_panel_chrome(panel)
        # 与日报/悬浮栏的交叉信号在对方就绪后再接
        self._try_wire_settings_cross()
        return panel

    def _ensure_personal_panel(self):
        if self.personal_panel is not None:
            return self.personal_panel
        from panels.personal_panel import PersonalPanel
        panel = PersonalPanel(self.language)
        panel.reminder_due.connect(self._show_private_notification)
        self._mount_panel(8, panel)
        self.personal_panel = panel
        self._apply_panel_chrome(panel)
        self._try_wire_settings_cross()
        return panel

    def _ensure_requirement_panel(self):
        if self.requirement_panel is not None:
            return self.requirement_panel
        from panels.requirement_panel import RequirementPanel
        panel = RequirementPanel(self.language)
        panel.send_to_sql.connect(self._receive_requirement_sql)
        panel.send_to_docx.connect(self._receive_requirement_docx)
        panel.add_to_daily.connect(self._add_requirement_to_daily)
        panel.open_system_config.connect(self._open_system_config)
        panel.open_release_prep.connect(self._open_release_prep)
        if self.dashboard_panel is not None:
            panel.requirement_saved.connect(self.dashboard_panel.refresh_for_requirement)
            panel.requirements_changed.connect(
                lambda: self.dashboard_panel.refresh(preferred_release_month=None)
            )
            if hasattr(self.dashboard_panel, 'requirements_updated'):
                self.dashboard_panel.requirements_updated.connect(panel.reload_requirements)
        self._mount_panel(9, panel)
        self.requirement_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_format_panel(self):
        if self.format_panel is not None:
            return self.format_panel
        from panels.format_panel import FormatToolsPanel
        panel = FormatToolsPanel(self.language)
        self._mount_panel(10, panel)
        self.format_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _try_wire_settings_cross(self):
        """设置 ↔ 日报提醒 / 悬浮栏：双方都创建后才接线。"""
        settings = self.settings_panel
        personal = self.personal_panel
        if settings is not None and personal is not None:
            if hasattr(settings, 'reminder_settings_changed') and not getattr(self, '_wired_reminder', False):
                settings.reminder_settings_changed.connect(personal.reload_reminder_settings)
                self._wired_reminder = True
        if settings is not None and self.quick_panel is not None and not getattr(self, '_wired_float', False):
            settings.floating_opacity_preview.connect(self.quick_panel.set_opacity)
            settings.edit_floating_shortcuts.connect(self._open_floating_shortcuts_editor)
            self._wired_float = True

    def _ensure_services(self):
        """托盘 / 热键 / 悬浮栏 / 防休眠（窗口显示后再建，缩短白屏前耗时）。"""
        if self.quick_panel is None:
            from ui.quick_panel import QuickPanel
            self.quick_panel = QuickPanel(self, self.language)
            self.quick_panel.apply_preferences(
                self._settings['floating_opacity'], self._settings['floating_always_on_top']
            )
            self.quick_panel.apply_shortcuts(
                self._settings.get('floating_shortcuts'),
                private_unlocked=self._private_unlocked,
            )
            self._try_wire_settings_cross()
            if self._private_unlocked:
                self._apply_private_unlocked_ui(persist=False, navigate=False, status_message=False)
            if self._settings.get('floating_show_on_startup') and self._startup_ready:
                self.quick_panel.show()
        if self.tray_service is None:
            from ui.tray_service import TrayService
            self.tray_service = TrayService(self)
        if self.keep_awake_service is None:
            from ui.keep_awake_service import KeepAwakeService
            self.keep_awake_service = KeepAwakeService(self)
            self.keep_awake_service.apply_preferences(
                self._settings.get('keep_awake_enabled', False),
                self._settings.get('keep_awake_interval_minutes', 5),
            )
        if self.hotkey_service is None:
            self._setup_hotkeys()

    def _panel_needs_create(self, nav_index: int) -> bool:
        # 父级导航（SQL 控制台 / 模型）不创建面板
        if is_parent_nav(nav_index):
            return False
        # 对于 DB 子菜单索引，检查对应 DB 面板是否已创建
        if nav_is_db_slot(nav_index):
            slot = resolve_db_slot_index(nav_index)
            stack_i = STACK_DB_START + slot
            if stack_i >= len(_STACK_PANEL_ATTRS):
                return False
            return getattr(self, _STACK_PANEL_ATTRS[stack_i], None) is None
        stack = self._stack_index_for_nav(nav_index)
        if stack < 0 or stack >= len(_STACK_PANEL_ATTRS):
            return False
        return getattr(self, _STACK_PANEL_ATTRS[stack], None) is None

    def _ensure_panel_for_nav(self, nav_index: int):
        """按导航进入时确保对应面板已创建。父级不创建面板。"""
        if is_parent_nav(nav_index):
            return None
        if nav_is_db_slot(nav_index):
            return self._ensure_db_panel(nav_index)
        stack = self._stack_index_for_nav(nav_index)
        ensure_map = {
            0: self._ensure_dashboard_panel,
            1: self._ensure_credit_panel,
            2: self._ensure_sql_panel,
            3: self._ensure_docx_panel,
            4: self._ensure_vin_panel,
            5: self._ensure_gateway_panel,
            6: self._ensure_ops_panel,
            7: self._ensure_settings_panel,
            8: self._ensure_personal_panel,
            9: self._ensure_requirement_panel,
            10: self._ensure_format_panel,
            11: self._ensure_interface_debug_panel,
            12: self._ensure_ops_log_panel,
            13: self._ensure_model_chat_panel,
            14: self._ensure_agent_workbench_panel,
        }
        fn = ensure_map.get(stack)
        if fn is not None:
            return fn()
        return None

    def _interactive_boot_steps(self):
        zh = self.language == 'zh'
        return [
            ('正在加载设置…' if zh else 'Loading settings…', self._ensure_settings_panel),
            ('正在启动托盘与快捷键…' if zh else 'Starting tray…', self._ensure_services),
        ]

    def _warmup_boot_steps(self):
        zh = self.language == 'zh'
        return [
            ('证件与 VIN' if zh else 'IDs & VIN', lambda: (self._ensure_credit_panel(), self._ensure_vin_panel())),
            ('运维与网关' if zh else 'Ops & gateway', lambda: (self._ensure_ops_panel(), self._ensure_gateway_panel())),
            ('SQL 与文档' if zh else 'SQL & docs', lambda: (self._ensure_sql_panel(), self._ensure_docx_panel())),
            ('格式工具' if zh else 'Format tools', self._ensure_format_panel),
            ('需求管理' if zh else 'Requirements', self._ensure_requirement_panel),
            ('个人模块' if zh else 'Personal', self._ensure_personal_panel),
        ]

    def _become_interactive(self):
        """首页可用后立刻收起全局 Loading，其余面板后台预热。"""
        if self._startup_ready:
            return
        self._startup_ready = True
        self._hide_startup_loading()
        if self.quick_panel is not None:
            self.quick_panel.set_language(self.language)
            if self._settings.get('floating_show_on_startup'):
                self.quick_panel.show()
        if self.tray_service is not None:
            try:
                self.tray_service.set_language(self.language)
            except Exception:
                pass
        try:
            self._apply_settings(self._settings, persist=False)
        except Exception:
            pass
        self._apply_density_preferences(self._settings, apply_startup_sidebar=False)
        QTimer.singleShot(0, lambda: self._layout_controller.force(self.width(), self.height()))
        self.status_bar.showMessage(
            '离线工作台已就绪' if self.language == 'zh' else 'Offline workspace ready',
            3000,
        )

    def _startup_tick(self):
        """先让窗口可点，再后台预热其余面板，不再用全局浮层罩到全部完成。"""
        if self._shutting_down:
            return
        interactive = self._interactive_boot_steps()
        if self._boot_step < len(interactive):
            message, action = interactive[self._boot_step]
            if not self._user_navigated:
                self._show_startup_loading(message)
            try:
                action()
            except Exception as exc:
                self.status_bar.showMessage(f'模块加载异常：{exc}', 5000)
            self._boot_step += 1
            QApplication.processEvents()
            QTimer.singleShot(0, self._startup_tick)
            return
        if not self._startup_ready:
            self._become_interactive()
        warmup = self._warmup_boot_steps()
        warmup_index = self._boot_step - len(interactive)
        if warmup_index < len(warmup):
            label, action = warmup[warmup_index]
            if self.language == 'zh':
                self.status_bar.showMessage(f'后台预热{label}…', 1500)
            else:
                self.status_bar.showMessage(f'Warming up {label}…', 1500)
            try:
                action()
            except Exception as exc:
                self.status_bar.showMessage(f'模块加载异常：{exc}', 5000)
            self._boot_step += 1
            QTimer.singleShot(0, self._startup_tick)
            return
        self._apply_language_to_created_panels()

    def _apply_language_to_created_panels(self):
        for panel in self._iter_created_panels():
            if hasattr(panel, 'set_language'):
                try:
                    panel.set_language(self.language)
                except Exception:
                    pass

    def _complete_startup_sync(self):
        """测试/同步模式：一次建齐常用面板（接口/日志仍按需）。"""
        self._ensure_settings_panel()
        self._ensure_credit_panel()
        self._ensure_vin_panel()
        self._ensure_ops_panel()
        self._ensure_gateway_panel()
        self._ensure_sql_panel()
        self._ensure_docx_panel()
        self._ensure_format_panel()
        self._ensure_requirement_panel()
        self._ensure_personal_panel()
        self._ensure_services()
        self._finish_startup()

    def _finish_startup(self):
        self._become_interactive()
        self._apply_language_to_created_panels()

    def _create_legacy_sidebar(self):
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
        brand = QLabel(APP_NAME)
        brand.setObjectName('sidebar_title')
        # 常驻只显示作者；版本/构建/彩蛋进 tooltip（双击仍解锁）
        self.version_label = QLabel('作者：Lihp')
        self.version_label.setObjectName('sidebar_version')
        self.version_label.setToolTip(
            f'版本：{app_version_text()}\n更新日期：{APP_BUILD_DATE}\n双击解锁私人彩蛋'
        )
        brand_text.addWidget(brand)
        brand_text.addWidget(self.version_label)
        brand_layout.addLayout(brand_text, 1)
        brand_block.setToolTip(
            f'{APP_NAME} {app_version_text()}\n作者：Lihp\n更新：{APP_BUILD_DATE}\n双击作者行可解锁私人彩蛋'
        )
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

        # 0–13 历史 + 14 SQL 控制台（可展开组）+ 15 模型（可展开组）
        self.nav_buttons = [None] * 48
        self._group_labels = {}
        self._nav_order = []
        self._sql_subnav_container = None
        self._sql_subnav_buttons = {}
        self._sql_console_expanded = bool(self._settings.get('sidebar_expanded_sql', True))
        self._db_subnav_indices = []
        self._ai_subnav_container = None
        self._ai_subnav_buttons = {}
        self._ai_expanded = bool(self._settings.get('sidebar_expanded_ai', True))

        for group_key, items in NAV_MODEL:
            section = QLabel()
            section.setObjectName('sidebar-section')
            self._group_labels[group_key] = section
            self._nav_layout.addWidget(section)
            for nav_index, _zh, _en, icon_role in items:
                if nav_index == 14:
                    # SQL 控制台 → 可折叠组（header + 6 数据库子项）
                    self._render_sql_console_group()
                    continue
                if nav_index == 15:
                    # 模型 → 可折叠组（header + 聊天/工作子项）
                    self._render_ai_group()
                    continue
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
                # 自我学习：仅未解锁时隐藏；已持久化解锁则保持展示
                if nav_index == 8 and not self._private_unlocked:
                    button.hide()
                    section.hide()

        self._nav_layout.addStretch(1)
        scroll.setWidget(nav_host)
        outer.addWidget(scroll, 1)

        # 折叠/展开切换按钮（居中，在分界线上方）
        collapse_row = QHBoxLayout()
        collapse_row.setContentsMargins(0, 4, 0, 2)
        collapse_row.addStretch(1)
        self._collapse_btn = QPushButton()
        self._collapse_btn.setObjectName('sidebar-collapse-btn')
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.setFixedSize(26, 26)
        apply_icon(self._collapse_btn, 'collapse', size=14)
        self._collapse_btn.setToolTip('收起导航栏' if self.language == 'zh' else 'Collapse sidebar')
        self._collapse_btn.clicked.connect(self._toggle_nav_collapse)
        collapse_row.addWidget(self._collapse_btn)
        collapse_row.addStretch(1)
        outer.addLayout(collapse_row)

        # 分界线（全宽）
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

        # 快速主题切换：一键在四套主题间循环，无需进入设置页
        self.theme_cycle_button = QPushButton()
        self.theme_cycle_button.setObjectName('nav-btn-settings')
        self.theme_cycle_button.setCheckable(False)
        self.theme_cycle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_cycle_button.setProperty('iconOnly', True)
        self.theme_cycle_button.clicked.connect(self._cycle_theme)
        apply_icon(self.theme_cycle_button, 'filter', size=20)
        self.theme_cycle_button.setToolTip(self._theme_cycle_tooltip())
        footer.addWidget(self.theme_cycle_button, 0)

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

    def _render_sql_console_group(self):
        """渲染 SQL 控制台为可折叠导航组（header + 6 固定数据库子项）。"""
        zh = self.language == 'zh'
        header = QPushButton()
        header.setObjectName('nav-btn')
        header.setCheckable(False)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setProperty('navIndex', 14)
        header.setProperty('sqlConsoleHeader', True)
        apply_icon(header, 'database', size=20)
        header.clicked.connect(lambda: self._toggle_sql_console())
        self.nav_buttons[14] = header
        self._nav_order.append(14)

        self._sql_expand_btn = QToolButton()
        self._sql_expand_btn.setObjectName('nav-sub-expand')
        self._sql_expand_btn.setCheckable(True)
        self._sql_expand_btn.setChecked(self._sql_console_expanded)
        self._sql_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sql_expand_btn.setFixedSize(18, 18)
        apply_icon(self._sql_expand_btn, 'collapse' if self._sql_console_expanded else 'expand', size=10)
        self._sql_expand_btn.setToolTip(
            '收起数据库列表' if zh else 'Collapse DB list'
        )
        self._sql_expand_btn.clicked.connect(self._toggle_sql_console)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(4)
        row.addWidget(header, 1)
        row.addWidget(self._sql_expand_btn, 0)
        wrapper = QWidget()
        wrapper.setObjectName('sql-console-header-row')
        wrapper.setLayout(row)
        self._nav_layout.addWidget(wrapper)

        self._sql_subnav_container = QFrame()
        self._sql_subnav_container.setObjectName('sql-subnav')
        self._sql_subnav_container.setVisible(self._sql_console_expanded)
        sub_l = QVBoxLayout(self._sql_subnav_container)
        sub_l.setContentsMargins(20, 2, 8, 4)
        sub_l.setSpacing(1)
        self._sql_subnav_layout = sub_l
        self._nav_layout.addWidget(self._sql_subnav_container)
        self._rebuild_sql_console_subnav()

    def _render_ai_group(self):
        """渲染"模型"为可折叠导航组（header + 聊天/工作子项）。"""
        zh = self.language == 'zh'
        header = QPushButton()
        header.setObjectName('nav-btn')
        header.setCheckable(False)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setProperty('navIndex', 15)
        header.setProperty('aiHeader', True)
        apply_icon(header, 'chat', size=20)
        header.clicked.connect(lambda: self._toggle_ai_group())
        self.nav_buttons[15] = header
        self._nav_order.append(15)

        self._ai_expand_btn = QToolButton()
        self._ai_expand_btn.setObjectName('nav-sub-expand')
        self._ai_expand_btn.setCheckable(True)
        self._ai_expand_btn.setChecked(self._ai_expanded)
        self._ai_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_expand_btn.setFixedSize(18, 18)
        apply_icon(self._ai_expand_btn, 'collapse' if self._ai_expanded else 'expand', size=10)
        self._ai_expand_btn.setToolTip('收起模型子菜单' if zh else 'Collapse AI menu')
        self._ai_expand_btn.clicked.connect(self._toggle_ai_group)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(4)
        row.addWidget(header, 1)
        row.addWidget(self._ai_expand_btn, 0)
        wrapper = QWidget()
        wrapper.setObjectName('ai-header-row')
        wrapper.setLayout(row)
        self._nav_layout.addWidget(wrapper)

        self._ai_subnav_container = QFrame()
        self._ai_subnav_container.setObjectName('sql-subnav')
        self._ai_subnav_container.setVisible(self._ai_expanded)
        sub_l = QVBoxLayout(self._ai_subnav_container)
        sub_l.setContentsMargins(20, 2, 8, 4)
        sub_l.setSpacing(1)
        self._ai_subnav_layout = sub_l
        self._nav_layout.addWidget(self._ai_subnav_container)
        # 子项：聊天(16)、工作(17)
        for nav_index, icon_role in ((AI_CHAT_NAV, 'chat'), (AI_WORKBENCH_NAV, 'workbench')):
            btn = QPushButton()
            btn.setObjectName('nav-sub-item')
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty('navIndex', nav_index)
            apply_icon(btn, icon_role, size=16)
            btn.clicked.connect(lambda checked=False, ni=nav_index: self._show_panel(ni))
            self._ai_subnav_layout.addWidget(btn)
            self.nav_buttons[nav_index] = btn
            self._ai_subnav_buttons[nav_index] = btn

    def _toggle_sql_console(self):
        """展开/折叠 SQL 控制台数据库子菜单。"""
        self._sql_console_expanded = not self._sql_console_expanded
        if self._sql_subnav_container:
            self._sql_subnav_container.setVisible(self._sql_console_expanded)
        self._sql_expand_btn.setChecked(self._sql_console_expanded)
        zh = self.language == 'zh'
        apply_icon(self._sql_expand_btn, 'collapse' if self._sql_console_expanded else 'expand', size=10)
        self._sql_expand_btn.setToolTip(
            '收起数据库列表' if zh and self._sql_console_expanded else
            '展开数据库列表' if zh else
            'Collapse DB list' if self._sql_console_expanded else 'Expand DB list'
        )
        self._persist_sidebar_expand()

    def _toggle_ai_group(self):
        """展开/折叠模型子菜单（聊天/工作）。"""
        self._ai_expanded = not self._ai_expanded
        if self._ai_subnav_container:
            self._ai_subnav_container.setVisible(self._ai_expanded)
        self._ai_expand_btn.setChecked(self._ai_expanded)
        zh = self.language == 'zh'
        apply_icon(self._ai_expand_btn, 'collapse' if self._ai_expanded else 'expand', size=10)
        self._ai_expand_btn.setToolTip(
            '收起模型子菜单' if zh and self._ai_expanded else
            '展开模型子菜单' if zh else
            'Collapse AI menu' if self._ai_expanded else 'Expand AI menu'
        )
        self._persist_sidebar_expand()

    def _persist_sidebar_expand(self):
        """持久化 SQL 控制台 / 模型两组折叠状态。"""
        try:
            self._settings['sidebar_expanded_sql'] = bool(self._sql_console_expanded)
            self._settings['sidebar_expanded_ai'] = bool(self._ai_expanded)
            from config import save_settings
            self._settings = save_settings(self._settings)
        except Exception:
            pass

    def _rebuild_sql_console_subnav(self):
        """重建 SQL 控制台数据库子菜单（固定 6 面板，v3.0）。"""
        if self._sql_subnav_layout is None:
            return
        while self._sql_subnav_layout.count():
            child = self._sql_subnav_layout.takeAt(0)
            if child.widget():
                child.widget().setParent(None)
                child.widget().deleteLater()
        self._sql_subnav_buttons.clear()
        self._db_subnav_indices.clear()
        for name_zh, dialect, nav_index, icon_role in FIXED_DB_PAGES:
            btn = QPushButton()
            btn.setObjectName('nav-sub-item')
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty('navIndex', nav_index)
            btn.setProperty('dbDialect', dialect)
            apply_icon(btn, icon_role, size=16)
            btn.clicked.connect(lambda checked=False, ni=nav_index: self._show_panel(ni))
            self._sql_subnav_layout.addWidget(btn)
            self.nav_buttons[nav_index] = btn
            self._sql_subnav_buttons[nav_index] = btn
            self._db_subnav_indices.append(nav_index)
        self._refresh_nav_texts_db()

    def _refresh_nav_texts_db(self):
        """刷新侧栏 DB 子菜单的文案和图标（语言切换时）。"""
        if self._nav_icon_only:
            return
        zh = self.language == 'zh'
        for name_zh, dialect, nav_index, icon_role in FIXED_DB_PAGES:
            btn = self._sql_subnav_buttons.get(nav_index)
            if btn is not None:
                btn.setText(name_zh)
        # 模型子菜单文案
        if hasattr(self, '_ai_subnav_buttons'):
            chat_btn = self._ai_subnav_buttons.get(AI_CHAT_NAV)
            if chat_btn is not None:
                chat_btn.setText('聊天' if zh else 'Chat')
            work_btn = self._ai_subnav_buttons.get(AI_WORKBENCH_NAV)
            if work_btn is not None:
                work_btn.setText('工作' if zh else 'Work')

    def _active_db_context(self) -> dict | None:
        """返回当前活跃的数据库连接上下文（供模型对话面板感知）。"""
        current = getattr(self, '_current_nav_index', None)
        if current is not None and nav_is_db_slot(current):
            panel = self._ensure_db_panel(current)
            if panel is not None and hasattr(panel, '_current_conn'):
                try:
                    ctx = panel._current_conn()
                    return dict(ctx) if isinstance(ctx, dict) else None
                except Exception:
                    return None
        return None

    def _ensure_db_panel(self, nav_index: int):
        """确保六数据库面板按 dialect 创建（Oracle/MySQL/OceanBase/达梦复用 AiWorkbenchPanel）。"""
        slot = resolve_db_slot_index(nav_index)
        stack_index = STACK_DB_START + slot
        if stack_index >= len(_STACK_PANEL_ATTRS):
            return None
        attr = _STACK_PANEL_ATTRS[stack_index]
        panel = getattr(self, attr, None)
        if panel is not None:
            return panel
        dialect = dialect_for_nav(nav_index)
        if dialect in ('oracle', 'mysql', 'oceanbase', 'dameng'):
            from panels.ai_workbench_panel import AiWorkbenchPanel
            panel = AiWorkbenchPanel(self.language, dialect=dialect)
        elif dialect == 'redis':
            from panels.db_redis_panel import RedisWorkbenchPanel
            panel = RedisWorkbenchPanel(self.language)
        elif dialect == 'mongodb':
            from panels.db_mongodb_panel import MongoDBWorkbenchPanel
            panel = MongoDBWorkbenchPanel(self.language)
        else:
            return None
        self._mount_panel(stack_index, panel)
        setattr(self, attr, panel)
        self._apply_panel_chrome(panel)
        return panel

    def _rebuild_user_menu(self):
        menu = self._user_menu
        menu.clear()
        zh = self.language == 'zh'
        ver = menu.addAction(app_version_text())
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
        about.triggered.connect(self._show_about)
        help_act = menu.addAction('使用说明' if zh else 'User Guide')
        help_act.triggered.connect(self._show_user_guide)
        menu.addSeparator()
        quit_act = menu.addAction('退出软件' if zh else 'Exit')
        quit_act.triggered.connect(self.exit_application)

    def _show_about(self):
        """左下角关于：励志搞笑文案，署名 Lihp。"""
        from ui.confirm_dialog import show_info
        zh = self.language == 'zh'
        if zh:
            title = f'关于 {APP_NAME}'
            message = (
                f'👋 嗨，我是 {APP_NAME}，Lihp 亲手喂大的离线打工人。\n\n'
                f'今天也要记得：Bug 怕认真的人，需求怕写清楚的人，'
                f'而加班最怕的是——你其实已经写完了却还在刷新邮箱。\n\n'
                f'☕ 建议：先喝口水，再点一次「保存」。\n'
                f'🚀 励志一句：代码可以重构，青春不行；但你可以先把日报写了。\n\n'
                f'作者：Lihp\n'
                f'版本：{app_version_text()}\n'
                f'构建：{APP_BUILD_DATE}\n'
                f'口号：离线也能起飞，摸鱼也要有工具感。\n\n'
                f'完整操作请看菜单中的「使用说明」。'
            )
            btn = '笑完继续干'
        else:
            title = f'About {APP_NAME}'
            message = (
                f'Hi, I am {APP_NAME} — raised offline by Lihp.\n\n'
                f'Bugs fear careful people. Specs fear clear people. '
                f'Overtime fears the person who already finished but still refreshes email.\n\n'
                f'Author: Lihp\n'
                f'Version: {app_version_text()}\n'
                f'Build: {APP_BUILD_DATE}\n'
                f'Motto: Ship offline. Keep smiling. Save the daily report.\n\n'
                f'See User Guide in the menu for full documentation.'
            )
            btn = 'Back to work'
        show_info(self, title, message, kind='info', button_text=btn)
        self.status_bar.showMessage(
            f'{APP_NAME} {app_version_text()} · Lihp · {APP_BUILD_DATE}', 5000
        )

    def _show_user_guide(self):
        """左下角菜单：内置 HTML 使用说明（关于下方）。"""
        from ui.help_dialog import show_user_guide
        show_user_guide(parent=self, language=self.language)
        self.status_bar.showMessage(
            '已打开使用说明' if self.language == 'zh' else 'User guide opened', 3000
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_controller.observe(self.width(), self.height())

    def _on_layout_mode(self, mode: str, low_height: bool):
        self._layout_mode = mode
        # 手动折叠时强制 icon-only 模式
        if self._nav_collapsed:
            icon_only = True
            self._sidebar.setFixedWidth(NAV_ICON)
        else:
            icon_only = is_icon_nav(mode)
            self._sidebar.setFixedWidth(nav_width_for_mode(mode))
        self._nav_icon_only = icon_only
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
        # 手动折叠后隐藏设置按钮，底部仅保留 LH 图标
        if hasattr(self, '_nav_collapsed'):
            self.settings_button.setVisible(not self._nav_collapsed)
        self.layout_mode_changed.emit(mode, low_height)
        # 刷新导航文案（非 icon 模式）
        if not icon_only:
            self._apply_nav_texts()

    def _set_nav_collapsed(self, collapsed: bool, *, persist: bool = False) -> None:
        """应用侧栏折叠状态，不改变导航数组或面板映射。"""
        zh = self.language == 'zh'
        self._nav_collapsed = bool(collapsed)
        if self._nav_collapsed:
            apply_icon(self._collapse_btn, 'expand', size=14)
            self._collapse_btn.setToolTip('展开导航栏' if zh else 'Expand sidebar')
        else:
            apply_icon(self._collapse_btn, 'collapse', size=14)
            self._collapse_btn.setToolTip('收起导航栏' if zh else 'Collapse sidebar')
        self._sidebar.setProperty('collapsed', self._nav_collapsed)
        self._on_layout_mode(self._layout_mode, False)
        if persist:
            from config import save_settings
            self._settings['sidebar_collapsed'] = self._nav_collapsed
            self._settings = save_settings(self._settings)

    def _toggle_nav_collapse(self):
        """手动折叠/展开侧边栏导航。"""
        self._set_nav_collapsed(not self._nav_collapsed, persist=True)

    def _apply_density_preferences(self, settings, *, apply_startup_sidebar: bool = False) -> None:
        """将纯视觉偏好注入窗口，不重建任何业务面板。"""
        from ui.design_system import density_metrics

        density = str(settings.get('ui_density') or 'compact').strip().lower()
        metrics = density_metrics(density)
        self.setProperty('uiDensity', density)
        self.setProperty('uiControlHeight', metrics.control_height)
        self.setProperty('uiRowHeight', metrics.row_height)
        if apply_startup_sidebar:
            self._set_nav_collapsed(bool(settings.get('sidebar_collapsed', False)), persist=False)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

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
        if hasattr(self, 'theme_cycle_button') and self.theme_cycle_button is not None:
            self.theme_cycle_button.setToolTip(self._theme_cycle_tooltip())
        # 刷新 DB 子菜单文案（语言切换时）
        self._refresh_nav_texts_db()

    def _iter_created_panels(self):
        """已实例化面板（懒加载未创建的跳过）。"""
        for panel in (
            self.dashboard_panel, self.credit_panel, self.sql_panel, self.docx_panel,
            self.vin_panel, self.gateway_panel, self.ops_panel,
            self.settings_panel, self.personal_panel, self.requirement_panel,
            self.format_panel, self.interface_debug_panel, self.ops_log_panel,
            self.model_chat_panel, self.agent_workbench_panel,
        ):
            if panel is not None:
                yield panel
        # 已创建的 DB 面板
        for attr in DB_ATTRS:
            panel = getattr(self, attr, None)
            if panel is not None:
                yield panel

    def _ensure_interface_debug_panel(self):
        """首次进入接口排查时再构造，节省启动内存。"""
        if self.interface_debug_panel is not None:
            return self.interface_debug_panel
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel(self.language)
        panel.open_gateway.connect(self._open_gateway_from_iface)
        panel.open_format_json.connect(self._open_format_json)
        panel.open_format_xml.connect(self._open_format_xml)
        self._mount_panel(11, panel)
        self.interface_debug_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_ops_log_panel(self):
        """首次进入日志排查时再构造。"""
        if self.ops_log_panel is not None:
            return self.ops_log_panel
        from panels.ops_log_panel import OpsLogPanel
        panel = OpsLogPanel(self.language)
        self._mount_panel(12, panel)
        self.ops_log_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_model_chat_panel(self):
        if self.model_chat_panel is not None:
            return self.model_chat_panel
        from panels.model_chat_panel import ModelChatPanel
        panel = ModelChatPanel(self.language)
        self._mount_panel(13, panel)
        self.model_chat_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _ensure_agent_workbench_panel(self):
        if self.agent_workbench_panel is not None:
            return self.agent_workbench_panel
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        panel = AgentWorkbenchPanel(self.language)
        self._mount_panel(14, panel)
        self.agent_workbench_panel = panel
        self._apply_panel_chrome(panel)
        return panel

    def _broadcast_layout_mode(self, mode: str, low_height: bool):
        for panel in self._iter_created_panels():
            if hasattr(panel, 'apply_layout_mode'):
                try:
                    panel.apply_layout_mode(mode, low_height)
                except Exception:
                    pass

    @staticmethod
    def _stack_index_for_nav(index: int) -> int:
        """nav index → stack index。

        v3.0：0–13 历史含义不变；16=聊天→13；17=工作→14；18–23 六数据库面板→15–20。
        14(SQL控制台)/15(模型) 为父级，不映射 stack。
        """
        if index in (8, 9):
            return 8  # personal
        if index == 10:
            return 9  # requirement
        if index == 11:
            return 10  # format tools
        if index == 12:
            return 11  # interface debug
        if index == 13:
            return 12  # ops log inspect
        if index == 16:
            return 13  # model chat (聊天)
        if index == 17:
            return 14  # agent workbench (工作)
        if nav_is_db_slot(index):
            return STACK_DB_START + resolve_db_slot_index(index)
        return index

    @property
    def main_shell_renderer(self) -> str:
        if getattr(self, '_web_shell_enabled', False) and getattr(self, '_sidebar_stack', None) is not None:
            if self._sidebar_stack.currentIndex() == 1:
                return 'web'
        return 'native'

    @property
    def dashboard_renderer(self) -> str:
        if getattr(self, '_web_shell_enabled', False) and getattr(self, '_dash_holder', None) is not None:
            if self._dash_holder.currentIndex() == 0:
                return 'web'
        return 'native'

    def _disable_web_shell_live(self, reason='unknown'):
        """整壳回退经典 UI（幂等）：Web 侧栏→原生侧栏，Web 首页→原生首页。
        仅当前会话回退，不修改用户 settings.json。"""
        if not getattr(self, '_web_shell_enabled', False):
            return
        self._web_shell_enabled = False
        log_web_event('web_shell_fallback', reason=reason,
                      main_shell='native', dashboard='native')
        try:
            self._web_timeout_timer.stop()
        except Exception:
            pass
        try:
            zh = getattr(self, 'language', 'zh') == 'zh'
            msg = f'V2 界面加载失败 ({reason})，已回退经典界面' if zh else f'Web Shell fallback ({reason}), using legacy UI'
            self.status_bar.showMessage(msg, 8000)
        except Exception:
            pass
        if getattr(self, '_sidebar_stack', None) is not None:
            self._sidebar_stack.setCurrentIndex(0)
        holder = getattr(self, '_dash_holder', None)
        if holder is not None:
            holder.setCurrentIndex(1)

    def _check_web_shell_ready(self):
        """统一健康判定：loadFinished(True) 与 bridge ready 双四条件全部满足才 announce 一次。"""
        if not getattr(self, '_web_shell_enabled', False):
            return
        if getattr(self, '_web_shell_ready_announced', False):
            return
        if not self._web_health.is_ready():
            return
        self._web_shell_ready_announced = True
        self._web_timeout_timer.stop()
        log_web_event('web_shell_ready',
                      loaded=sorted(self._web_health.loaded_pages),
                      bridge_ready=sorted(self._web_health.bridge_ready_pages))
        try:
            self.status_bar.showMessage('V2 界面已就绪', 3000)
        except Exception:
            pass

    def _on_web_page_ready(self, page_name):
        """pageReady 仅代表 QWebChannel + 页面 JS 初始化完成。"""
        self._web_health.mark_bridge_ready(str(page_name))
        log_web_event('page_bridge_ready', page=str(page_name),
                      loaded=sorted(self._web_health.loaded_pages),
                      bridge_ready=sorted(self._web_health.bridge_ready_pages))
        self._check_web_shell_ready()

    def _on_web_shell_timeout(self):
        missing_load = sorted(self._web_health.missing_loaded_pages())
        missing_bridge = sorted(self._web_health.missing_bridge_pages())
        if not missing_load and not missing_bridge:
            return
        log_web_event('web_shell_timeout', missing_load=missing_load,
                      missing_bridge=missing_bridge)
        self._disable_web_shell_live(
            reason=f'timeout missing_load={missing_load} missing_bridge={missing_bridge}')

    def _on_web_load_finished(self, page_name, ok):
        if ok:
            self._web_health.mark_loaded(str(page_name), True)
            self._check_web_shell_ready()
        else:
            self._web_health.mark_failed(str(page_name), 'load_failed')
            self._disable_web_shell_live(reason=f'load_failed:{page_name}')

    def _on_web_render_terminated(self, page_name, status, exit_code):
        self._web_health.mark_failed(str(page_name), 'renderer_crashed')
        # 枚举转换绝不抛异常，保证 fallback 一定执行
        status_value = _web_shell.enum_value(status)
        code_value = _web_shell.enum_value(exit_code)
        try:
            log_web_event('web_render_crashed', page=str(page_name),
                          status=status_value, exit_code=code_value)
        finally:
            self._disable_web_shell_live(reason=f'renderer_crashed:{page_name}')

    def _open_quick_panel(self):
        """Web 铬层发起的快速面板（与 Ctrl+Shift+P 同源）。"""
        try:
            if getattr(self, 'quick_panel', None) is not None:
                self.quick_panel.show_panel()
        except Exception:
            pass

    def _build_web_nav_model(self):
        """侧栏 Web 渲染数据：唯一权威 ui/navigation_model.py，JS 端不硬编码。"""
        from ui.navigation_model import (
            NAV_MODEL, GROUP_LABELS, NAV_ITEMS, FIXED_DB_PAGES, AI_CHAT_NAV, AI_WORKBENCH_NAV,
        )
        dia_short = {'oracle': 'ORA', 'mysql': 'MY', 'oceanbase': 'OB',
                     'dameng': 'DM', 'redis': 'KV', 'mongodb': 'DOC'}
        groups = []
        for key, entries in NAV_MODEL:
            items = []
            for nav_index, name_zh, name_en, icon_role in entries:
                if nav_index == 8 and not self._private_unlocked:
                    continue
                info = NAV_ITEMS[nav_index]
                entry = {'i': nav_index, 'zh': name_zh, 'en': name_en,
                         'icon': icon_role, 'tip': info.tooltip_zh}
                if nav_index == 14:
                    entry['children'] = [
                        {'i': i, 'zh': zh, 'en': zh, 'icon': icon,
                         'dia': dia_short.get(dialect, dialect[:2].upper())}
                        for zh, dialect, i, icon in FIXED_DB_PAGES
                    ]
                elif nav_index == 15:
                    entry['children'] = [
                        {'i': AI_CHAT_NAV, 'zh': '聊天', 'en': 'CHAT', 'icon': 'chat'},
                        {'i': AI_WORKBENCH_NAV, 'zh': '工作', 'en': 'AGENT', 'icon': 'spark'},
                    ]
                items.append(entry)
            zh_label, en_label = GROUP_LABELS[key]
            groups.append({'key': key, 'zh': zh_label, 'en': en_label, 'items': items})
        return {'groups': groups,
                'settings': {'i': 7, 'zh': '设置', 'en': 'SET', 'icon': 'gear'},
                'current': int(getattr(self, '_current_nav_index', 0) or 0)}

    def _dashboard_summary_payload(self):
        """首页 Web/Native 共用 summary（失败返回可渲染默认）。"""
        try:
            from tools.dashboard_summary import build_dashboard_summary
            return build_dashboard_summary(
                language=self.language,
                username=str(self._settings.get('home_username') or 'Lihp'),
            )
        except Exception:
            return {
                'username': str(self._settings.get('home_username') or 'Lihp'),
                'greeting': '下午好',
                'date_line': '本地数据已同步',
                'stats': {'req_open': 0, 'req_trend': '', 'daily_done': 0, 'daily_total': 5, 'daily_note': ''},
                'release': {
                    'version': 'RELEASE', 'total': 0, 'done': 0, 'percent': 0,
                    'days_left': None, 'date_text': '计划日期待定', 'countdown_state': 'unset',
                },
                'recent': [], 'checklist': [], 'tools': [], 'monthly_release_tasks': [],
            }

    def _show_panel(self, index):
        if index == 8 and not self._private_unlocked:
            return
        # 父级导航（SQL 控制台 / 模型）：不切换页面，仅展开/折叠子菜单
        if is_parent_nav(index):
            if index == SQL_CONSOLE_NAV:
                self._toggle_sql_console()
            elif index == AI_PARENT_NAV:
                self._toggle_ai_group()
            return
        prev = getattr(self, '_current_nav_index', None)
        if prev is not None and prev != index:
            # 离开旧页面：调用 on_panel_deactivated 清理 loading / 定时器 / 代理
            current_panel = self.stack.currentWidget()
            if current_panel is not None and hasattr(current_panel, 'on_panel_deactivated'):
                try:
                    current_panel.on_panel_deactivated()
                except Exception:
                    pass
            # 离开接口排查专项：暂停系统代理
            if prev == 12 and index != 12 and self.interface_debug_panel is not None:
                try:
                    if hasattr(self.interface_debug_panel, 'on_panel_deactivated') and self.interface_debug_panel != current_panel:
                        self.interface_debug_panel.on_panel_deactivated()
                except Exception:
                    pass
        self._current_nav_index = index
        stack_index = self._stack_index_for_nav(index)
        # 用户已点导航：立刻收起启动浮层，不要等后台预热结束
        self._user_navigated = True
        self._hide_startup_loading()
        creating = self._panel_needs_create(index)
        if creating:
            from ui.navigation_model import display_name
            name = display_name(index, self.language)
            prefix = '正在打开' if self.language == 'zh' else 'Opening '
            self._show_startup_loading(f'{prefix}{name}…' if self.language == 'zh' else f'{prefix}{name}…')
        try:
            self._ensure_panel_for_nav(index)
        except Exception as exc:
            self._hide_startup_loading()
            self.status_bar.showMessage(f'打开页面失败：{exc}', 5000)
            return
        if creating:
            self._hide_startup_loading()
        if index == 12:
            try:
                panel = self.interface_debug_panel
                if panel is not None and hasattr(panel, 'on_panel_activated'):
                    panel.on_panel_activated()
            except Exception:
                pass
        if index == 7 and self.settings_panel is not None:
            if hasattr(self.settings_panel, 'reload_reminder_from_store'):
                try:
                    self.settings_panel.reload_reminder_from_store()
                except Exception:
                    pass
        elif index == 8 and self.personal_panel is not None:
            self.personal_panel.open_learning()
        elif index == 9 and self.personal_panel is not None:
            self.personal_panel.open_daily_report()
        elif index == 10 and self.requirement_panel is not None:
            self.requirement_panel.refresh_systems()
        self.stack.setCurrentIndex(stack_index)
        if self._web_shell_enabled:
            if self._chrome_bridge is not None:
                self._chrome_bridge.push_active(index)
        # 按钮选中状态：DB 子菜单索引只点亮对应的子项，普通导航点亮对应按钮
        is_db = nav_is_db_slot(index)
        for position, button in enumerate(self.nav_buttons):
            if button is None:
                continue
            # 父级 header 不参与选中态（SQL 控制台 header 保持未选中）
            if position in (SQL_CONSOLE_NAV, AI_PARENT_NAV):
                continue
            button.setChecked(position == index)
        statuses_zh = {
            0: '离线工作台已就绪', 1: '个人与单位证件模拟生成', 2: 'SQL 脚本整理、回滚与验证',
            3: 'SQL 驱动接口文档更新', 4: '中国车辆 VIN 测试数据', 5: '网关国密解密 · JSON 结果',
            6: '命令库 · 只生成/复制 Linux 命令，不连服务器', 7: '界面与悬浮工具栏设置',
            8: '自我学习资料整理与全文搜索', 9: '每日日报与定时提醒', 10: '需求归档、上线台账与工具联动',
            11: 'JSON / XML / SQL / 文本辅助离线格式化',
            12: '接口排查 · 抓包中会占用系统代理，离开本页自动暂停代理',
            13: '日志排查 · SSH 会话 / 多机日志导出',
            16: '模型聊天 · 内网多模型连续对话',
            17: 'Agent 工作台 · 绑定项目目录执行受控任务',
            18: 'Oracle 工作台 · SQL 编辑、对象树与结构快照',
            19: 'MySQL 工作台 · 库表浏览与 SQL 编辑',
            20: 'OceanBase 工作台 · SQL 编辑与分区表浏览',
            21: '达梦工作台 · 模式浏览与 SQL 编辑',
            22: 'Redis 工作台 · Key 树、TTL 管理与命令行',
            23: 'MongoDB 工作台 · 集合树、文档浏览器与 Shell',
        }
        statuses_en = {
            0: 'Offline workspace ready', 1: 'Personal and unit document test data',
            2: 'SQL classify, validate and export', 3: 'SQL-driven interface document updater',
            4: 'China vehicle VIN test data', 5: 'Gateway SM decrypt · JSON result',
            6: 'Command library · generate/copy only, no SSH',
            7: 'Interface and floating toolbar settings',
            8: 'Learning library and full-text search', 9: 'Daily reports and reminders',
            10: 'Requirement tracking and tool links',
            11: 'Offline JSON / XML / SQL / text helpers',
            12: 'API debug · system proxy paused when you leave this page',
            13: 'Log inspect · SSH session / multi-host export',
            16: 'Model chat · intranet multi-model conversation',
            17: 'Agent workbench · bind project dir and run controlled tasks',
            18: 'Oracle workbench · SQL editor, object tree, snapshot',
            19: 'MySQL workbench · schema tree and SQL editor',
            20: 'OceanBase workbench · SQL editor and partition view',
            21: 'Dameng workbench · schema tree and SQL editor',
            22: 'Redis workbench · key tree, TTL and CLI',
            23: 'MongoDB workbench · collections, documents and shell',
        }
        table = statuses_zh if self.language == 'zh' else statuses_en
        self.status_bar.showMessage(table.get(index, ''))

    def _open_format_xml(self, text: str):
        self._show_panel(11)
        try:
            if self.format_panel is not None:
                self.format_panel.open_xml(text or '')
        except Exception:
            pass

    def _open_format_json(self, text: str):
        self._show_panel(11)
        try:
            if self.format_panel is not None:
                self.format_panel.open_json(text or '')
        except Exception:
            pass

    def _open_gateway_from_iface(self, payload):
        """接口排查送入：支持纯文本报文，或 dict{cipher,key}。"""
        self._show_panel(5)
        try:
            if self.gateway_panel is None:
                return
            if isinstance(payload, dict):
                self.gateway_panel.set_cipher_and_key(
                    payload.get('cipher') or payload.get('body') or '',
                    payload.get('key') or payload.get('sm4_key_cipher') or '',
                )
            else:
                self.gateway_panel.set_cipher_text(str(payload or ''))
        except Exception:
            pass

    def _open_requirement_from_dashboard(self, requirement):
        self._show_panel(10)
        try:
            if self.requirement_panel is not None:
                self.requirement_panel.focus_requirement(requirement)
        except Exception:
            pass

    def _open_system_config(self):
        self._ensure_sql_panel()
        self.sql_panel.refresh_config()
        self.sql_panel.tabs.setCurrentIndex(2)
        self._show_panel(2)

    def _open_release_prep(self, requirement=None):
        self._ensure_sql_panel()
        self.sql_panel.refresh_config()
        self.sql_panel.focus_release_requirement(requirement if isinstance(requirement, dict) else None)
        self._show_panel(2)
        title = ''
        if isinstance(requirement, dict):
            title = requirement.get('title') or requirement.get('code') or ''
        if title:
            self.status_bar.showMessage(f'已进入升级准备，并勾选「{title}」', 5000)
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
        for panel in self._iter_created_panels():
            if hasattr(panel, 'set_language'):
                panel.set_language(self.language)
            for btn in panel.findChildren(QPushButton, 'header-home-btn'):
                btn.setText('返回首页' if self.language == 'zh' else 'Home')
                btn.setToolTip('返回首页' if self.language == 'zh' else 'Return to Home')
        if self.quick_panel is not None:
            self.quick_panel.set_language(self.language)
        if self.tray_service is not None:
            try:
                self.tray_service.set_language(self.language)
            except Exception:
                pass
        self._show_panel(self._current_nav_index)

    def _apply_settings(self, settings, *, persist: bool = True):
        """先完整应用主题，再原子保存并分发设置，避免视觉与持久化状态分裂。"""
        candidate = normalize_settings(settings)
        # 设置保存不得覆盖已解锁彩蛋；两边取真
        if bool(candidate.get('private_unlocked', False)) or self._private_unlocked:
            candidate['private_unlocked'] = True
        app = QApplication.instance()
        previous_settings = dict(self._settings)
        previous_font = app.font()
        font = app.font()
        font.setPointSize(max(8, int(candidate['font_size']) - 2))
        try:
            from ui.theme_manager import ThemeManager, DEFAULT_THEME_ID
            ThemeManager.instance().apply(
                app,
                candidate.get('ui_theme', DEFAULT_THEME_ID),
                font_size=candidate.get('font_size', 12),
            )
            app.setFont(font)
            saved = save_settings(candidate) if persist else candidate
        except Exception as exc:
            app.setFont(previous_font)
            try:
                from ui.theme_manager import ThemeManager
                ThemeManager.instance().apply(
                    app,
                    previous_settings.get('ui_theme', 'calm'),
                    font_size=previous_settings.get('font_size', 12),
                )
            except Exception:
                pass
            self.status_bar.showMessage(
                f'设置未应用：{exc}' if self.language == 'zh' else f'Settings were not applied: {exc}',
                5000,
            )
            return False
        self._settings = saved
        if self.settings_panel is not None:
            self.settings_panel.load_values(self._settings)
        if self._settings.get('private_unlocked'):
            self._private_unlocked = True
            self._apply_private_unlocked_ui(persist=False, navigate=False, status_message=False)
        self._apply_density_preferences(self._settings)
        # 导航图标与局部手工刷色随主题重新染色。
        self._apply_nav_texts()
        self._refresh_brand_icon()
        for panel in (
            self.personal_panel, self.requirement_panel,
            self.format_panel, self.interface_debug_panel,
        ):
            if panel is None:
                continue
            if hasattr(panel, 'refresh_theme'):
                try:
                    panel.refresh_theme()
                except Exception:
                    pass
        if self.quick_panel is not None:
            self.quick_panel.apply_preferences(
                self._settings['floating_opacity'], self._settings['floating_always_on_top']
            )
            self.quick_panel.apply_shortcuts(
                self._settings.get('floating_shortcuts'),
                private_unlocked=self._private_unlocked,
            )
            self.quick_panel.refresh_brand_icons()
        self._sync_web_theme()
        if self.tray_service is not None:
            try:
                self.tray_service.refresh_icon()
            except Exception:
                pass
        if self.ops_panel is not None:
            try:
                self.ops_panel.set_copy_feedback_duration(self._settings['copy_feedback_ms'])
            except Exception:
                pass
        if self.keep_awake_service is not None:
            self.keep_awake_service.apply_preferences(
                self._settings['keep_awake_enabled'],
                self._settings['keep_awake_interval_minutes'],
            )
        wanted_index = 0 if self._settings['default_language'] == 'zh' else 1
        if self._language_index != wanted_index:
            self._set_language(wanted_index)
        self.status_bar.showMessage('设置已应用并保存' if self.language == 'zh' else 'Settings applied and saved', 3000)
        return True

    def _sync_web_theme(self):
        """向 Chrome / Dashboard Web 视图同步当前主题 Token 与深浅色模式。"""
        try:
            from ui.theme_manager import ThemeManager, theme_mode
            tm = ThemeManager.instance()
            theme_id = tm.theme_id
            is_dark = (theme_mode(theme_id) == 'dark')
            payload = {
                'id': theme_id,
                'is_dark': is_dark,
                'tokens': tm.palette(),
            }
            if getattr(self, '_chrome_bridge', None) is not None:
                self._chrome_bridge.set_theme_payload(payload)
            if getattr(self, '_dash_bridge', None) is not None:
                self._dash_bridge.set_theme_payload(payload)
        except Exception:
            pass

    def _open_floating_shortcuts_editor(self):
        from ui.floating_shortcuts_editor import open_floating_shortcuts_editor
        open_floating_shortcuts_editor(
            self,
            self._settings,
            language=self.language,
            private_unlocked=self._private_unlocked,
            on_saved=self._apply_settings,
        )

    def apply_theme(self, theme_id: str) -> bool:
        """兼容主题切换入口：由统一设置事务负责应用与保存。"""
        settings = dict(self._settings)
        settings['ui_theme'] = theme_id
        return self._apply_settings(settings)

    def _theme_cycle_tooltip(self) -> str:
        """当前外观模式提示（随语言切换）。"""
        from ui.theme_manager import theme_mode
        current = self._settings.get('ui_theme', 'calm')
        mode = theme_mode(current)
        zh = self.language == 'zh'
        if mode == 'dark':
            curr_label = '深色' if zh else 'Dark'
            next_label = '浅色' if zh else 'Light'
        else:
            curr_label = '浅色' if zh else 'Light'
            next_label = '深色' if zh else 'Dark'
        if zh:
            return f'当前：{curr_label}\n点击切换到{next_label}'
        return f'Current: {curr_label}\nSwitch to {next_label}'

    def _cycle_theme(self):
        """在浅色与深色外观模式之间快速切换，即时应用并保存。"""
        from ui.theme_manager import theme_mode
        current = self._settings.get('ui_theme', 'calm')
        mode = theme_mode(current)
        nxt = 'calm' if mode == 'dark' else 'black'
        if not self.apply_theme(nxt):
            if hasattr(self, 'theme_cycle_button') and self.theme_cycle_button is not None:
                self.theme_cycle_button.setToolTip(self._theme_cycle_tooltip())
            return
        if hasattr(self, 'theme_cycle_button') and self.theme_cycle_button is not None:
            self.theme_cycle_button.setToolTip(self._theme_cycle_tooltip())
        zh = self.language == 'zh'
        target_mode = theme_mode(nxt)
        shown = ('深色' if target_mode == 'dark' else '浅色') if zh else ('Dark' if target_mode == 'dark' else 'Light')
        self.status_bar.showMessage(
            f'已切换到{shown}' if zh else f'Switched to {shown} theme',
            2000,
        )

    def _reset_floating_position(self):
        if self.quick_panel is None:
            self._ensure_services()
        if self.quick_panel is not None:
            self.quick_panel.reset_position()
        self.status_bar.showMessage(
            '悬浮工具栏已重置到屏幕右侧' if self.language == 'zh' else 'Floating toolbar reset to screen right',
            3000,
        )

    def _on_layout_prefs_reset(self):
        """设置页复位分栏后，对已加载面板即时套用默认尺寸。"""
        req = getattr(self, 'requirement_panel', None)
        if req is not None and hasattr(req, 'apply_default_splitter_sizes'):
            try:
                req.apply_default_splitter_sizes()
            except Exception:
                pass
        iface = getattr(self, 'interface_debug_panel', None)
        if iface is not None and hasattr(iface, 'apply_default_splitter_sizes'):
            try:
                iface.apply_default_splitter_sizes()
            except Exception:
                pass

    def _setup_hotkeys(self):
        from ui.hotkey_service import HotkeyService
        if self.quick_panel is None:
            # 热键回调在服务创建时再绑悬浮栏
            self.hotkey_service = HotkeyService(QApplication.instance(), self.toggle_quick_panel)
        else:
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

    def open_ticket_submit(self, compact=False):
        from panels.ticket_submit_dialog import open_ticket_submit_dialog
        from tools.requirements import load_requirements
        selected = []
        if self.requirement_panel is not None:
            try:
                from tools.requirements import requirement_identity
                selected = [
                    requirement_identity(item)
                    for item in self.requirement_panel._selected_requirements()
                    if requirement_identity(item)
                ]
            except Exception:
                selected = []
        open_ticket_submit_dialog(
            load_requirements(),
            selected_ids=selected,
            parent=self if not compact else self.quick_panel,
            compact=bool(compact),
        )

    def toggle_quick_panel(self):
        if self.quick_panel is None:
            self._ensure_services()
        if self.quick_panel is not None:
            self.quick_panel.show_panel()

    def navigate_to(self, index):
        self._show_panel(index)

    def _apply_private_unlocked_ui(self, *, persist=False, navigate=False, status_message=False):
        """展示自我学习导航；可选写入 data 持久化，升级重开后仍可见。"""
        self._private_unlocked = True
        self._settings['private_unlocked'] = True
        if self.nav_buttons[8] is not None:
            self.nav_buttons[8].show()
        personal_label = self._group_labels.get('personal')
        if personal_label is not None and not self._nav_icon_only:
            personal_label.show()
        if hasattr(self, 'quick_panel') and self.quick_panel is not None:
            self.quick_panel.set_private_unlocked(True)
        if persist:
            save_settings(self._settings)
        if status_message and hasattr(self, 'status_bar'):
            self.status_bar.showMessage('彩蛋已解锁：自我学习已开启（下次启动仍会显示）', 7000)
        if navigate:
            self._show_panel(8)

    def _unlock_private_tools(self):
        if self._private_unlocked:
            # 已解锁：确保导航可见（例如布局切换后）
            self._apply_private_unlocked_ui(persist=False, navigate=False, status_message=False)
            return True
        key, accepted = QInputDialog.getText(
            self, f'{APP_NAME} 彩蛋', '请输入私人功能密钥：', QLineEdit.EchoMode.Password
        )
        if not accepted:
            return False
        if key != 'Lihp':
            from ui.confirm_dialog import show_warning
            show_warning(self, f'{APP_NAME} 彩蛋', '密钥不正确。')
            return False
        self._apply_private_unlocked_ui(persist=True, navigate=True, status_message=True)
        return True

    def _show_private_notification(self, title, message):
        if self.tray_service is None:
            self._ensure_services()
        if self.tray_service is not None:
            self.tray_service.show_message(title, message)

    def _receive_requirement_sql(self, title, sql):
        self._ensure_sql_panel()
        current = None
        if self.requirement_panel is not None:
            current = getattr(self.requirement_panel, '_current', None)
        landed = self.sql_panel.receive_from_requirement(title, sql, current)
        self._show_panel(2)
        if landed == 'sql':
            self.status_bar.showMessage(f'已把“{title}”的 SQL 送到发版联动整理页', 5000)
        else:
            self.status_bar.showMessage(f'“{title}”还没有 SQL，已在升级准备中勾选该条', 5000)

    def _receive_requirement_docx(self, title, sql):
        self._ensure_docx_panel()
        current = self.docx_panel.sql_editor.toPlainText().strip()
        block = f'-- 来源需求：{title}\n{sql}'
        self.docx_panel.sql_editor.setPlainText('\n\n'.join(part for part in (current, block) if part))
        self._show_panel(3)
        self.status_bar.showMessage(f'已把“{title}”的结构 SQL 发送到接口文档更新', 5000)

    def _add_requirement_to_daily(self, requirement):
        self._ensure_personal_panel()
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
                if self.settings_panel is not None:
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
        # 接口排查：停止 CDP/IE 代理、恢复系统代理、清空内存报文（未打开过则无需）
        try:
            panel = getattr(self, 'interface_debug_panel', None)
            if panel is not None and hasattr(panel, 'shutdown_cleanup'):
                panel.shutdown_cleanup()
        except Exception:
            pass
        if self.quick_panel is not None:
            qp = self.quick_panel
            shutdown = getattr(qp, 'shutdown', None)
            try:
                if callable(shutdown):
                    shutdown()
                else:
                    qp.close()
            except Exception:
                try:
                    qp.close()
                except Exception:
                    pass
        if self.tray_service is not None:
            self.tray_service.hide()
        event.accept()
        QApplication.instance().quit()
