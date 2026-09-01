# -*- coding: utf-8 -*-
"""接口排查中心：HTTP/HTTPS 抓包（MITM）+ 请求测试 + 明细导出导入。

报文仅内存；停止抓包保留会话；清空/退出才 clear_session。
请求测试按用户保存的环境 base 替换 host 后发送。
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from tools.browser_debug import (
    BrowserDebugError, connect_page_session, discover_browsers, fetch_cdp_targets,
    is_loopback_host, launch_debug_browser, mask_sensitive_value, mask_url_query,
    pick_default_page_target, port_open, wait_debug_port,
)
from tools.ie_proxy import (
    IeProxyWorker, install_user_root_cert, is_recorded_root_cert_installed,
    remove_recorded_cert, restore_proxy_from_snapshot,
)
from tools.interface_debug_store import (
    load_interface_debug_config, save_interface_debug_config, update_ui_prefs,
)
from tools.interface_drafts import (
    DraftError, build_curl, build_postman_collection, drafts_as_json_text, rewrite_url,
    validate_base_url,
)
from tools.interface_session_view import (
    COLUMN_DEFS, COLUMN_KEYS, FILTER_ALL, FILTER_FAILED, FILTER_JSON_XML, FILTER_SLOW,
    FILTER_STATIC, FILTER_XHR, content_kind, duration_severity, filter_and_sort,
    format_size, host_of, host_path_display, is_failed, name_of, pretty_body, protocol_of,
    query_pairs, response_size_bytes, split_cookies, url_path_display,
)
from ui.aurora_progress import AuroraProgress
from ui.confirm_dialog import (
    confirm_action, confirm_https_cert_consent, show_info, show_success, show_warning,
)
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import apply_caption, size_combo, size_enum_combo, size_pick_combo
from ui.key_value_editor import KeyValueEditor
from ui.page_chrome import make_page_header, make_page_toolbar

# 会话仅内存：限制条数与单条 body，避免长时间抓包撑爆进程
MAX_SESSION_RECORDS = 3000
MAX_BODY_CHARS = 512 * 1024  # 512KB/字段
from ui.responsive import apply_splitter_orientation, editor_min_height, set_subtitle_visible


class _HintLabel(QLabel):
    """空文案不占布局，避免左栏顶部留白。"""

    def setText(self, text):
        super().setText(text or '')
        self.setVisible(bool((text or '').strip()))


def _looks_json(text: str) -> bool:
    s = (text or '').strip()
    return bool(s) and s[0] in '{['


def _looks_xml(text: str) -> bool:
    s = (text or '').strip()
    return bool(s) and (s.startswith('<') or s.startswith('<?xml'))


def _looks_base64ish(text: str) -> bool:
    s = (text or '').strip().replace('\n', '')
    if len(s) < 16:
        return False
    import re
    return bool(re.fullmatch(r'[A-Za-z0-9+/=]+', s)) and not _looks_json(s) and not _looks_xml(s)


def _theme_color(name: str, fallback: str) -> QColor:
    try:
        from ui.theme_manager import ThemeManager
        return QColor(ThemeManager.instance().token(name) or fallback)
    except Exception:
        return QColor(fallback)


class _LaunchBrowserWorker(QThread):
    finished_ok = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, exe, port, parent=None):
        super().__init__(parent)
        self.exe = exe
        self.port = port

    def run(self):
        try:
            launch_debug_browser(self.exe, self.port)
            if not wait_debug_port(self.port, timeout=15):
                self.failed.emit(f'调试端口 {self.port} 未就绪')
                return
            self.finished_ok.emit(self.port)
        except Exception as exc:
            self.failed.emit(str(exc))


class _RequestTestWorker(QThread):
    """请求测试后台发送，避免阻塞 UI 导致 Loading 不刷新。"""

    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(
        self,
        method: str,
        url: str,
        headers: dict,
        body: str,
        parent=None,
        verify_ssl: bool = True,
    ):
        super().__init__(parent)
        self.method = method
        self.url = url
        self.headers = headers or {}
        self.body = body or ''
        self.verify_ssl = bool(verify_ssl)

    def run(self):
        try:
            from tools.iface_request_test import send_http_request
            result = send_http_request(
                self.method,
                self.url,
                headers=self.headers,
                body=self.body,
                verify_ssl=self.verify_ssl,
            )
            self.finished_ok.emit(result if isinstance(result, dict) else {'ok': False, 'body': str(result)})
        except Exception as exc:
            # RequestTestError 与其它异常统一回主线程
            self.failed.emit(str(exc))


class _FilterChip(QPushButton):
    def __init__(self, key: str, label: str, parent=None):
        super().__init__(label, parent)
        self.filter_key = key
        self.setCheckable(True)
        self.setProperty('compactAction', True)
        self.setObjectName('iface-filter-chip')
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class InterfaceDebugPanel(QWidget):
    """Private 版 Fiddler 式接口排查面板。"""

    open_gateway = pyqtSignal(object)  # str 或 {cipher, key}
    open_format_json = pyqtSignal(str)
    open_format_xml = pyqtSignal(str)
    # 抓包后台线程 → 主线程（必须用信号，不能用 QTimer.singleShot）
    _sig_capture_record = pyqtSignal(int, dict)
    _sig_capture_error = pyqtSignal(int, str)
    _sig_capture_stopped = pyqtSignal(int)
    _sig_capture_stop_finalized = pyqtSignal(int, bool)

    # 对齐 Fiddler Session 列表列名
    COL_LABELS_ZH = {
        'seq': '#', 'status': '结果', 'protocol': '协议', 'method': '方法',
        'name': '名称', 'host': '主机', 'url': 'URL', 'body': 'Body', 'type': '类型',
        'duration': '耗时', 'time': '时间',
    }
    COL_LABELS_EN = {
        'seq': '#', 'status': 'Result', 'protocol': 'Protocol', 'method': 'Method',
        'name': 'Name', 'host': 'Host', 'url': 'URL', 'body': 'Body', 'type': 'Type',
        'duration': 'Duration', 'time': 'Time',
    }

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._config = load_interface_debug_config()
        self._prefs = dict(self._config.get('ui_prefs') or {})
        self._records: list[dict] = []
        self._records_by_id: dict[str, dict] = {}
        self._filtered: list[dict] = []
        self._cdp_session = None
        self._ie_worker = None
        self._listening = False
        self._channel_ready = False
        self._capture_epoch = 0
        self._capture_boot_worker = None
        from tools.capture_lifecycle import CaptureLifecycle
        self._lifecycle = CaptureLifecycle()
        self._capture_boot_epoch = 0
        self._capture_stop_thread = None
        self._listen_started_at = 0.0
        self._last_request_at = 0.0
        # 只做 HTTP/HTTPS 抓包，不再提供模式切换（CDP/代理等对用户隐藏）
        self._mode = 'proxy'
        self._reveal_sensitive = False
        # 默认显示静态资源，避免「页面打开了但列表全空」被过滤误伤
        self._show_static = bool(self._prefs.get('show_static', True))
        self._selected_id = None
        self._launch_worker = None
        self._layout_mode = 'standard'
        self._compact_session_view = True
        self._last_responsive_widths = None
        self._active_filters = list(self._prefs.get('active_filters') or [FILTER_ALL])
        self._sort_key = self._prefs.get('sort_key') or 'time'
        self._sort_desc = bool(self._prefs.get('sort_desc', True))
        self._follow_latest = True
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._rebuild_table)
        # 抓包高峰时每条流量都全表重建会卡死主线程 → 合并刷新
        self._ingest_flush_timer = QTimer(self)
        self._ingest_flush_timer.setSingleShot(True)
        self._ingest_flush_timer.setInterval(220)
        self._ingest_flush_timer.timeout.connect(self._flush_ingest_ui)
        self._ingest_dirty = False
        self._ingest_count_since_flush = 0
        self._wait_hint_timer = QTimer(self)
        self._wait_hint_timer.setSingleShot(True)
        self._wait_hint_timer.setInterval(10000)
        self._wait_hint_timer.timeout.connect(self._on_wait_hint)
        self._status_tick = QTimer(self)
        self._status_tick.setInterval(2000)
        self._status_tick.timeout.connect(self._refresh_live_status)
        self._sensitive_copy_warned = False
        # 跨线程投递：QueuedConnection
        self._sig_capture_record.connect(self._on_capture_record)
        self._sig_capture_error.connect(self._on_capture_error)
        self._sig_capture_stopped.connect(self._on_capture_stopped)
        self._sig_capture_stop_finalized.connect(self._on_capture_stop_finalized)
        self._setup_ui()
        self._reload_config_ui()
        self.set_language(language)
        self._apply_column_visibility()
        self._apply_mode_ui()
        # 离屏初始化无有效布局宽度；首次真实 resize/splitter 事件再计算收纳状态。
        # 离屏/测试环境跳过延时恢复提示
        if os.environ.get('QT_QPA_PLATFORM', '').lower() != 'offscreen':
            QTimer.singleShot(200, self._check_orphan_proxy_snapshot)

    # ── UI ──────────────────────────────────────────────
    def _setup_ui(self):
        from ui.layout_metrics import SPACING_PAGE
        from ui.responsive import editor_min_height
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACING_PAGE)
        self._page_root_layout = root

        self.offline_pill = QLabel()
        self.offline_pill.setObjectName('offline-pill')
        self.capture_toggle_btn = QPushButton()
        apply_button(self.capture_toggle_btn, 'primary', compact=True, icon='external-open', icon_size=16)
        self.capture_toggle_btn.clicked.connect(self._toggle_capture)
        self.clear_list_btn = QPushButton()
        apply_button(self.clear_list_btn, 'secondary', compact=True, icon='delete', icon_size=16)
        self.clear_list_btn.clicked.connect(self._confirm_clear_session)
        head_trailing = QWidget()
        head_tr = QHBoxLayout(head_trailing)
        head_tr.setContentsMargins(0, 0, 0, 0)
        head_tr.setSpacing(8)
        head_tr.addWidget(self.offline_pill)
        head_tr.addWidget(self.clear_list_btn)
        header, self.page_title, self.page_subtitle = make_page_header(
            '接口排查',
            '会话、请求与响应在当前应用会话中处理；停止监听不等于清空会话。',
            'api-debug',
            primary_button=self.capture_toggle_btn,
            trailing=head_trailing,
        )
        root.addWidget(header)

        # L2：测试监听 / 显示敏感 / 更多 + 脱敏提示
        page_toolbar, page_tool_l = make_page_toolbar(divided=True)
        self.page_toolbar = page_toolbar
        self.test_listen_btn = QPushButton()
        apply_button(self.test_listen_btn, 'secondary', compact=True, icon='terminal', icon_size=16)
        self.test_listen_btn.clicked.connect(self._test_listen_loopback)
        page_tool_l.addWidget(self.test_listen_btn)
        self.reveal_cb = QCheckBox()
        self.reveal_cb.toggled.connect(self._on_reveal)
        page_tool_l.addWidget(self.reveal_cb)
        self.restore_proxy_btn = QPushButton()
        apply_button(self.restore_proxy_btn, 'ghost', compact=True, icon='refresh', icon_size=16)
        self.restore_proxy_btn.setToolTip('若抓包异常退出导致网页/接口不通，点此恢复系统代理')
        self.restore_proxy_btn.clicked.connect(self._manual_restore_proxy)
        page_tool_l.addWidget(self.restore_proxy_btn)
        self.capture_actions_more_btn, self._capture_actions_menu = self._make_overflow_button(page_tool_l)
        self.toolbar_hint = QLabel()
        self.toolbar_hint.setObjectName('field-hint')
        self.toolbar_hint.setWordWrap(True)
        page_tool_l.addWidget(self.toolbar_hint, 1)
        root.addWidget(page_toolbar)

        # 连接控制区兼容壳（隐藏）
        conn = QFrame()
        apply_surface(conn, 'card')
        conn.setObjectName('iface-conn-zone')
        cl = QVBoxLayout(conn)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)
        self.mode_label = QLabel()
        self.mode_label.hide()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(['HTTP/HTTPS 抓包', 'Chromium CDP（高级）', 'IE 抓包（兼容）'])
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.hide()
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName('field-hint')
        self.mode_hint.setWordWrap(True)
        self.mode_hint.hide()
        self.browser_combo = QComboBox()
        self.browser_combo.hide()
        self.refresh_browsers_btn = QPushButton()
        self.refresh_browsers_btn.hide()
        self.pick_browser_btn = QPushButton()
        self.pick_browser_btn.hide()
        self.launch_btn = QPushButton()
        self.launch_btn.hide()
        self.target_combo = QComboBox()
        self.target_combo.hide()
        self.port_label = QLabel()
        self.port_label.hide()
        self.port_edit = QLineEdit()
        self.port_edit.setText(str(self._config.get('ie_proxy_port') or 8899))
        self.port_edit.hide()
        self.ie_install_cert_btn = QPushButton()
        self.ie_install_cert_btn.hide()
        self.ie_remove_cert_btn = QPushButton()
        self.ie_remove_cert_btn.hide()
        self.recheck_btn = QPushButton()
        self.recheck_btn.hide()
        self.conn_more_btn = QToolButton()
        self.conn_more_btn.hide()
        self._conn_more_menu = QMenu(self.conn_more_btn)
        # 保留旧属性供既有启动/停止代码兼容引用，不再作为可见操作入口。
        self.connect_btn = QPushButton(self)
        self.connect_btn.hide()
        self.stop_btn = QPushButton(self)
        self.stop_btn.hide()
        self.status_label = _HintLabel()
        self.status_label.setObjectName('field-hint')
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.live_status = _HintLabel()
        self.live_status.setObjectName('field-hint')
        self.live_status.setWordWrap(True)
        self.live_status.hide()
        self.capture_zone = conn
        self.capture_zone.setParent(self)
        self.capture_zone.hide()

        # 会话筛选条（搜索/chip，不含主监听按钮）；保留 iface-session-toolbar，避免 chip 计入 page-filter-bar 基线
        tools = QFrame()
        self.session_toolbar = tools
        apply_surface(tools, 'zone')
        tools.setObjectName('iface-session-toolbar')
        tv = QVBoxLayout(tools)
        tv.setContentsMargins(12, 8, 12, 8)
        tv.setSpacing(8)
        tl = QHBoxLayout()
        tl.setSpacing(8)
        self.filter_edit = QLineEdit()
        self.filter_edit.setObjectName('iface-session-search')
        self.filter_edit.setPlaceholderText('搜索 URL / host / path / method / 状态…')
        self.filter_edit.textChanged.connect(lambda *_: self._search_timer.start())
        self.filter_edit.setMinimumHeight(36)
        self.filter_edit.setMaximumHeight(40)

        self._filter_chips: dict[str, _FilterChip] = {}
        chip_defs = [
            (FILTER_ALL, '全部'),
            (FILTER_XHR, 'XHR/Fetch'),
            (FILTER_FAILED, '失败'),
            (FILTER_SLOW, '慢请求'),
            (FILTER_JSON_XML, 'JSON/XML'),
            (FILTER_STATIC, '静态资源'),
        ]
        for key, label in chip_defs:
            chip = _FilterChip(key, label)
            # 初始化后再连接，避免 setChecked 递归触发
            self._filter_chips[key] = chip
            tl.addWidget(chip)
        if FILTER_ALL in self._active_filters or not self._active_filters:
            self._filter_chips[FILTER_ALL].setChecked(True)
        else:
            for k in self._active_filters:
                if k in self._filter_chips:
                    self._filter_chips[k].setChecked(True)
        for key, chip in self._filter_chips.items():
            chip.toggled.connect(lambda checked, k=key: self._on_filter_chip(k, checked))

        tl.addStretch(1)

        self.cols_btn = QToolButton()
        self.cols_btn.setObjectName('responsive-more-btn')
        self.cols_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._cols_menu = QMenu(self.cols_btn)
        self.cols_btn.setMenu(self._cols_menu)
        self._rebuild_column_menu()
        tl.addWidget(self.cols_btn)

        self.export_list_btn = QPushButton()
        apply_button(self.export_list_btn, 'secondary', compact=True, icon='export', icon_size=16)
        self.export_list_btn.clicked.connect(self._export_session_detail)
        tl.addWidget(self.export_list_btn)

        # 左侧栏隐藏/显示切换按钮（与全局 section_toggle 一致）
        self._toggle_list_btn = QPushButton()
        from ui.section_toggle import apply_visibility_toggle
        apply_visibility_toggle(
            self._toggle_list_btn,
            content_visible=True,
            language=self.language,
            kind='session_list',
            tooltip='隐藏或显示左侧会话列表' if self.language == 'zh' else 'Show or hide session list',
        )
        self._toggle_list_btn.clicked.connect(self._toggle_session_list)
        tl.addWidget(self._toggle_list_btn)

        # 左栏收窄时将次要筛选和操作收纳到“更多”，避免按钮被水平裁切。
        self.session_actions_more_btn = QToolButton()
        self.session_actions_more_btn.setObjectName('responsive-more-btn')
        self.session_actions_more_btn.setText('更多')
        self.session_actions_more_btn.setToolTip('显示筛选与会话操作')
        self.session_actions_more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._session_actions_menu = QMenu(self.session_actions_more_btn)
        self.session_actions_more_btn.setMenu(self._session_actions_menu)
        self.session_actions_more_btn.hide()
        tl.addWidget(self.session_actions_more_btn)
        self._rebuild_session_actions_menu()
        tv.addLayout(tl)
        tv.addWidget(self.filter_edit)
        self.session_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        # 中部：左侧采集/定位/会话，右侧诊断/复测。
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setObjectName('iface-mid-splitter')
        self.mid_splitter.setChildrenCollapsible(False)
        self.mid_splitter.setHandleWidth(6)
        self.mid_splitter.setOpaqueResize(True)

        left = QWidget()
        left.setObjectName('iface-session-pane')
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        session_title_row = QHBoxLayout()
        self.session_pane_title = QLabel()
        self.session_pane_title.setObjectName('section-title')
        session_title_row.addWidget(self.session_pane_title, 1)
        self.session_count = QLabel('0 / 0')
        self.session_count.setObjectName('field-hint')
        session_title_row.addWidget(self.session_count)
        ll.addLayout(session_title_row)
        # 工具条保持单行；窄屏时通过横向滚动保留完整操作，不挤压按钮文本。
        self.session_toolbar_scroll = QScrollArea()
        self.session_toolbar_scroll.setObjectName('iface-session-toolbar-scroll')
        self.session_toolbar_scroll.setWidgetResizable(True)
        self.session_toolbar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.session_toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_toolbar.setMinimumWidth(0)
        self.session_toolbar_scroll.setWidget(self.session_toolbar)
        self.session_toolbar_scroll.setMinimumHeight(78)
        ll.addWidget(self.session_toolbar_scroll)
        ll.addWidget(self.status_label)
        ll.addWidget(self.live_status)

        self.table = QTableWidget(0, len(COLUMN_KEYS))
        self.table.setObjectName('iface-request-table')
        self.table.setHorizontalHeaderLabels([self.COL_LABELS_ZH[k] for k in COLUMN_KEYS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        try:
            from ui.selection_delegate import HighContrastSelectDelegate
            self._select_delegate = HighContrastSelectDelegate(self.table)
            self.table.setItemDelegate(self._select_delegate)
        except Exception:
            pass
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        # 一条会话在同一行展示“请求摘要 + 诊断元信息”两层内容，避免 11 列把定位信息压碎。
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        # 超长 URL/主机：不截断为「被挤扁」，允许左右滚动看全
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header_view = self.table.horizontalHeader()
        try:
            from ui.design_system import apply_list_header
            apply_list_header(header_view)
        except Exception:
            pass
        header_view.setStretchLastSection(False)
        header_view.setSectionsMovable(True)
        header_view.setMinimumSectionSize(40)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 短列 Interactive；URL 列 Stretch 吃掉尾部空白，表头随视口变化
        _default_w = {
            'seq': 44, 'status': 68, 'protocol': 56, 'method': 64,
            'host': 160, 'url': 360, 'body': 72, 'type': 72,
            'duration': 84, 'time': 110,
        }
        for i, key in enumerate(COLUMN_KEYS):
            if key == 'url':
                header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, int(_default_w.get(key, 80)))
        header_view.sectionClicked.connect(self._on_header_clicked)
        header_view.sectionResized.connect(self._on_column_resized)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)
        ll.addWidget(self.table, 1)

        self.empty_hint = QLabel()
        self.empty_hint.setObjectName('field-hint')
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.empty_hint)
        self._session_list_widget = left
        self.mid_splitter.addWidget(left)

        right = QWidget()
        self.detail_workspace = right
        right.setObjectName('iface-detail-workspace')
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        self.detail_summary = QLabel()
        self.detail_summary.setObjectName('status-banner')
        self.detail_summary.setWordWrap(True)
        self.detail_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_summary.hide()
        rl.addWidget(self.detail_summary)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName('module-tabs')
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        # Tab 0 概览
        self.overview_page = QWidget()
        ov = QVBoxLayout(self.overview_page)
        ov.setContentsMargins(0, 8, 0, 0)
        ov_tools = QHBoxLayout()
        self.copy_safe_url_btn = QPushButton()
        apply_button(self.copy_safe_url_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_safe_url_btn.clicked.connect(self._copy_safe_url)
        ov_tools.addWidget(self.copy_safe_url_btn)
        ov_tools.addStretch(1)
        ov.addLayout(ov_tools)
        self.overview_edit = QPlainTextEdit()
        self.overview_edit.setReadOnly(True)
        self.overview_edit.setObjectName('iface-detail-edit')
        self.overview_edit.setFont(mono)
        self.overview_edit.setMinimumHeight(editor_min_height())
        ov.addWidget(self.overview_edit, 1)
        self.detail_tabs.addTab(self.overview_page, '概览')

        # Tab 1 请求
        self.req_page = QWidget()
        rq = QVBoxLayout(self.req_page)
        rq.setContentsMargins(0, 8, 0, 0)
        req_tools = QHBoxLayout()
        self.copy_req_btn = QPushButton()
        apply_button(self.copy_req_btn, 'ghost', compact=True, icon='copy', icon_size=16)
        self.copy_req_btn.clicked.connect(lambda: self._copy_text(self.req_detail.toPlainText(), sensitive=True))
        req_tools.addWidget(self.copy_req_btn)
        self.format_req_btn = QPushButton()
        apply_button(self.format_req_btn, 'secondary', compact=True, icon='json', icon_size=16)
        self.format_req_btn.clicked.connect(lambda: self._send_body_side('request', 'format'))
        req_tools.addWidget(self.format_req_btn)
        self.gateway_req_btn = QPushButton()
        apply_button(self.gateway_req_btn, 'secondary', compact=True, icon='shield-key', icon_size=16)
        self.gateway_req_btn.clicked.connect(lambda: self._send_body_side('request', 'gateway'))
        req_tools.addWidget(self.gateway_req_btn)
        self.req_actions_more_btn, self._req_actions_menu = self._make_overflow_button(req_tools)
        req_tools.addStretch(1)
        rq.addLayout(req_tools)
        self.req_detail = QPlainTextEdit()
        self.req_detail.setReadOnly(True)
        self.req_detail.setObjectName('iface-detail-edit')
        self.req_detail.setFont(mono)
        self.req_detail.setMinimumHeight(editor_min_height())
        rq.addWidget(self.req_detail, 1)
        self.detail_tabs.addTab(self.req_page, '请求')

        # Tab 2 响应
        self.resp_page = QWidget()
        rs = QVBoxLayout(self.resp_page)
        rs.setContentsMargins(0, 8, 0, 0)
        resp_tools = QHBoxLayout()
        self.copy_resp_btn = QPushButton()
        apply_button(self.copy_resp_btn, 'ghost', compact=True, icon='copy', icon_size=16)
        self.copy_resp_btn.clicked.connect(lambda: self._copy_text(self.resp_detail.toPlainText(), sensitive=True))
        resp_tools.addWidget(self.copy_resp_btn)
        self.format_resp_btn = QPushButton()
        apply_button(self.format_resp_btn, 'secondary', compact=True, icon='json', icon_size=16)
        self.format_resp_btn.clicked.connect(lambda: self._send_body_side('response', 'format'))
        resp_tools.addWidget(self.format_resp_btn)
        self.gateway_resp_btn = QPushButton()
        apply_button(self.gateway_resp_btn, 'secondary', compact=True, icon='shield-key', icon_size=16)
        self.gateway_resp_btn.clicked.connect(lambda: self._send_body_side('response', 'gateway'))
        resp_tools.addWidget(self.gateway_resp_btn)
        self.resp_actions_more_btn, self._resp_actions_menu = self._make_overflow_button(resp_tools)
        resp_tools.addStretch(1)
        rs.addLayout(resp_tools)
        self.resp_detail = QPlainTextEdit()
        self.resp_detail.setReadOnly(True)
        self.resp_detail.setObjectName('iface-detail-edit')
        self.resp_detail.setFont(mono)
        self.resp_detail.setMinimumHeight(editor_min_height())
        rs.addWidget(self.resp_detail, 1)
        self.detail_tabs.addTab(self.resp_page, '响应')

        # Tab 3 请求测试（Postman 风格 · 按已保存环境发送）
        self.draft_page = QWidget()
        self.draft_page.setAcceptDrops(True)
        self.draft_page.installEventFilter(self)
        dl = QVBoxLayout(self.draft_page)
        dl.setContentsMargins(0, 8, 0, 0)
        dl.setSpacing(8)
        self.draft_badge = QLabel()
        self.draft_badge.setObjectName('offline-pill')
        dl.addWidget(self.draft_badge)

        self.include_auth_cb = QCheckBox()
        self.include_auth_cb.hide()
        # 草稿类入口冻结：不展示、不新增功能面
        self.gen_draft_btn = QPushButton()
        self.gen_draft_btn.hide()
        self.copy_postman_btn = QPushButton()
        self.copy_postman_btn.hide()
        self.export_postman_btn = QPushButton()
        self.export_postman_btn.hide()
        self.copy_curl_btn = QPushButton()
        self.copy_curl_btn.hide()
        self.draft_hint = QLabel()
        self.draft_hint.setObjectName('field-hint')
        self.draft_hint.setWordWrap(True)
        dl.addWidget(self.draft_hint)

        # 请求验证紧凑上下文（V1.2）：两行——环境+Base+方法 / URL+HTTPS+发送
        self.request_verify_context = QFrame()
        self.request_verify_context.setObjectName('request-verify-context')
        ctx_l = QVBoxLayout(self.request_verify_context)
        # V1.2 §8.3：单行控件高 36–40；两行紧凑上下文内边距统一
        ctx_l.setContentsMargins(12, 10, 12, 10)
        ctx_l.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.target_label = QLabel('环境')
        apply_caption(self.target_label, 36)
        row1.addWidget(self.target_label)
        self.local_target_combo = QComboBox()
        size_pick_combo(self.local_target_combo)
        self.local_target_combo.currentIndexChanged.connect(self._on_env_selected)
        self.local_target_combo.currentIndexChanged.connect(lambda *_: self._rt_refresh_send_label())
        row1.addWidget(self.local_target_combo)
        self.base_label = QLabel('Base')
        apply_caption(self.base_label, 36)
        row1.addWidget(self.base_label)
        self.rt_base_edit = QLineEdit()
        self.rt_base_edit.setText('http://localhost:18031')
        self.rt_base_edit.setPlaceholderText('http://host:port')
        self.rt_base_edit.textChanged.connect(lambda *_: self._rt_refresh_send_label())
        row1.addWidget(self.rt_base_edit, 1)
        self.rt_method = QComboBox()
        self.rt_method.addItems(['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
        size_enum_combo(self.rt_method)
        row1.addWidget(self.rt_method)
        self.rt_environment_config_btn = QPushButton()
        apply_button(self.rt_environment_config_btn, 'ghost', compact=True)
        self.rt_environment_config_btn.clicked.connect(self._show_environment_config_dialog)
        row1.addWidget(self.rt_environment_config_btn)
        ctx_l.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.rt_url = QLineEdit()
        self.rt_url.setPlaceholderText('http://host:port/path')
        self.rt_url.textChanged.connect(lambda *_: self._rt_refresh_send_label())
        row2.addWidget(self.rt_url, 1)
        self.rt_ssl_verify = QCheckBox()
        self.rt_ssl_verify.setChecked(True)
        self.rt_ssl_verify.setToolTip(
            '关闭仅用于内网自签证书；默认校验证书以满足安测要求'
        )
        row2.addWidget(self.rt_ssl_verify)
        self.rt_send_btn = QPushButton()
        # 页级唯一 primary 是「开始/停止监听」；此处用 secondary 承担请求验证主操作视觉
        apply_button(self.rt_send_btn, 'secondary', compact=True, icon='external-open', icon_size=16)
        self.rt_send_btn.clicked.connect(self._rt_send)
        row2.addWidget(self.rt_send_btn)
        ctx_l.addLayout(row2)
        for control in (
            self.local_target_combo, self.rt_base_edit, self.rt_method,
            self.rt_url, self.rt_send_btn,
        ):
            control.setMinimumHeight(36)
        dl.addWidget(self.request_verify_context)

        # 次要工具：填入/过滤（不挤进两行上下文）
        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.rt_fill_btn = QPushButton()
        apply_button(self.rt_fill_btn, 'secondary', compact=True)
        self.rt_fill_btn.clicked.connect(self._rt_fill_from_selection)
        tools_row.addWidget(self.rt_fill_btn)
        self.rt_filter_config_btn = QPushButton()
        apply_button(self.rt_filter_config_btn, 'ghost', compact=True)
        self.rt_filter_config_btn.clicked.connect(self._show_url_filter_config_dialog)
        tools_row.addWidget(self.rt_filter_config_btn)
        tools_row.addStretch(1)
        dl.addLayout(tools_row)

        # 旧控件保留隐藏兼容，避免破坏既有管理槽函数。
        self.add_target_btn = QPushButton(self)
        self.add_target_btn.hide()
        self.edit_target_btn = QPushButton(self)
        self.edit_target_btn.hide()
        self.del_target_btn = QPushButton(self)
        self.del_target_btn.hide()
        self.rt_save_env_btn = QPushButton(self)
        self.rt_save_env_btn.hide()
        self.rt_url_filter_edit = QLineEdit(self)
        self.rt_url_filter_edit.hide()
        self.rt_url_filter_save_btn = QPushButton(self)
        self.rt_url_filter_save_btn.hide()

        # 分类 + 保存到接口库
        cat_row = QHBoxLayout()
        cat_row.setSpacing(6)
        self.rt_cat_label = QLabel('分类')
        cat_row.addWidget(self.rt_cat_label)
        self.rt_category_combo = QComboBox()
        size_pick_combo(self.rt_category_combo, 160)
        cat_row.addWidget(self.rt_category_combo)
        self.rt_save_api_btn = QPushButton()
        apply_button(self.rt_save_api_btn, 'secondary', compact=True, icon='save', icon_size=16)
        self.rt_save_api_btn.clicked.connect(self._rt_save_api)
        cat_row.addWidget(self.rt_save_api_btn)
        self.rt_manage_cat_btn = QPushButton()
        apply_button(self.rt_manage_cat_btn, 'ghost', compact=True, icon='edit', icon_size=16)
        self.rt_manage_cat_btn.clicked.connect(self._rt_manage_categories)
        cat_row.addWidget(self.rt_manage_cat_btn)
        cat_row.addStretch(1)
        self.rt_form_more_btn, self._rt_form_actions_menu = self._make_overflow_button(cat_row)
        dl.addLayout(cat_row)

        # 左：接口库/历史 · 右：表单与响应
        self.rt_split = QSplitter(Qt.Orientation.Horizontal)
        self.rt_split.setChildrenCollapsible(False)
        lib_panel = QWidget()
        lib_panel.setMinimumWidth(240)
        lib_panel.setMaximumWidth(480)
        lib_l = QVBoxLayout(lib_panel)
        lib_l.setContentsMargins(0, 0, 4, 0)
        lib_l.setSpacing(6)
        self.rt_lib_mode_label = QLabel('列表')
        self.rt_lib_mode_label.hide()
        self.rt_lib_mode = QComboBox()
        self.rt_lib_mode.addItem('已保存', 'library')
        self.rt_lib_mode.addItem('发送记录', 'history')
        self.rt_lib_mode.setToolTip('已保存：点「保存接口」收藏的请求。发送记录：点「发送」后自动留下的记录。')
        size_enum_combo(self.rt_lib_mode)
        self.rt_lib_mode.setMaximumWidth(16777215)
        self.rt_lib_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.rt_lib_mode.currentIndexChanged.connect(self._rt_lib_on_mode_changed)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(self.rt_lib_mode, 1)
        self.rt_history_cleanup_btn = QPushButton()
        apply_button(self.rt_history_cleanup_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.rt_history_cleanup_btn.clicked.connect(self._show_history_cleanup_dialog)
        self.rt_history_cleanup_btn.hide()
        mode_row.addWidget(self.rt_history_cleanup_btn)
        lib_l.addLayout(mode_row)
        self.rt_lib_cat_label = QLabel('分类')
        self.rt_lib_cat_label.hide()
        self.rt_lib_cat_filter = QComboBox()
        size_combo(self.rt_lib_cat_filter, fill=True)
        self.rt_lib_cat_filter.currentIndexChanged.connect(self._rt_lib_refresh_list)
        lib_l.addWidget(self.rt_lib_cat_filter)
        self.rt_lib_search = QLineEdit()
        self.rt_lib_search.setPlaceholderText('搜索名称 / URL')
        self.rt_lib_search.setClearButtonEnabled(True)
        self.rt_lib_search.textChanged.connect(self._rt_lib_refresh_list)
        lib_l.addWidget(self.rt_lib_search)
        self.rt_lib_list = QListWidget()
        self.rt_lib_list.setObjectName('iface-rt-lib-list')
        self.rt_lib_list.setWordWrap(True)
        self.rt_lib_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.rt_lib_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rt_lib_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rt_lib_list.customContextMenuRequested.connect(self._rt_lib_show_menu)
        self.rt_lib_list.itemDoubleClicked.connect(self._rt_lib_apply_selected)
        self.rt_lib_list.itemActivated.connect(self._rt_lib_apply_selected)
        lib_l.addWidget(self.rt_lib_list, 1)
        self.rt_lib_load_btn = QPushButton(lib_panel)
        self.rt_lib_resend_btn = QPushButton(lib_panel)
        self.rt_lib_del_btn = QPushButton(lib_panel)
        self.rt_lib_clear_btn = QPushButton(lib_panel)
        for btn, handler in (
            (self.rt_lib_load_btn, self._rt_lib_apply_selected),
            (self.rt_lib_resend_btn, self._rt_lib_resend_selected),
            (self.rt_lib_del_btn, self._rt_lib_delete_selected),
            (self.rt_lib_clear_btn, self._rt_lib_clear_history),
        ):
            btn.hide()
            btn.clicked.connect(handler)
        self.rt_lib_count = QLabel('')
        self.rt_lib_count.setObjectName('field-hint')
        lib_l.addWidget(self.rt_lib_count)
        self.rt_split.addWidget(lib_panel)

        right_form = QWidget()
        rf = QVBoxLayout(right_form)
        rf.setContentsMargins(4, 0, 0, 0)
        rf.setSpacing(8)

        self.rt_editor_response_splitter = QSplitter(Qt.Orientation.Vertical)
        self.rt_editor_response_splitter.setObjectName('iface-request-test-splitter')
        self.rt_editor_response_splitter.setChildrenCollapsible(False)
        self.rt_editor_response_splitter.setHandleWidth(6)
        self.rt_editor_response_splitter.setOpaqueResize(True)
        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)

        req_bar = QHBoxLayout()
        req_bar.setSpacing(6)
        self.rt_req_copy_btn = QPushButton()
        apply_button(self.rt_req_copy_btn, 'ghost', compact=True, icon='copy', icon_size=16)
        self.rt_req_copy_btn.clicked.connect(self._rt_copy_request_body)
        req_bar.addWidget(self.rt_req_copy_btn)
        self.rt_req_format_btn = QPushButton()
        apply_button(self.rt_req_format_btn, 'secondary', compact=True, icon='json', icon_size=16)
        self.rt_req_format_btn.clicked.connect(self._rt_send_request_to_format)
        req_bar.addWidget(self.rt_req_format_btn)
        req_bar.addStretch(1)
        self.export_detail_btn = QPushButton()
        apply_button(self.export_detail_btn, 'secondary', compact=True, icon='export', icon_size=16)
        self.export_detail_btn.clicked.connect(self._export_session_detail)
        req_bar.addWidget(self.export_detail_btn)
        self.rt_import_btn = QPushButton()
        apply_button(self.rt_import_btn, 'secondary', compact=True, icon='import', icon_size=16)
        self.rt_import_btn.clicked.connect(self._rt_import_file)
        req_bar.addWidget(self.rt_import_btn)
        self.rt_io_more_btn, self._rt_io_actions_menu = self._make_overflow_button(req_bar)
        editor_layout.addLayout(req_bar)

        self.rt_tabs = QTabWidget()
        self.rt_tabs.setObjectName('module-tabs')
        self.rt_tabs.setDocumentMode(False)
        self.rt_headers = KeyValueEditor(mode='header')
        self.rt_headers.setPlainText('Content-Type: application/json')
        self.rt_tabs.addTab(self.rt_headers, 'Headers')
        self.rt_params = KeyValueEditor(mode='query')
        self.rt_tabs.addTab(self.rt_params, 'Params')
        self.rt_body = QPlainTextEdit()
        self.rt_body.setObjectName('rt-json-body')
        self.rt_body.setPlaceholderText('{\n  "key": "value"\n}')
        self.rt_body.setFont(mono)
        self.rt_body.setPlainText('{\n  \n}')
        self.rt_body.setMinimumHeight(240)
        self.rt_tabs.addTab(self.rt_body, 'Body')
        self.rt_tabs.setCurrentWidget(self.rt_body)
        self.rt_tabs.setMinimumHeight(280)
        editor_layout.addWidget(self.rt_tabs, 1)
        self.rt_editor_response_splitter.addWidget(editor_panel)

        response_panel = QWidget()
        response_layout = QVBoxLayout(response_panel)
        response_layout.setContentsMargins(0, 0, 0, 0)
        response_layout.setSpacing(6)
        resp_head = QHBoxLayout()
        self.rt_resp_label = QLabel('响应')
        self.rt_resp_label.setObjectName('field-caption')
        resp_head.addWidget(self.rt_resp_label)
        self.rt_resp_meta = QLabel('')
        self.rt_resp_meta.setObjectName('field-hint')
        self.rt_resp_meta.setWordWrap(True)
        resp_head.addWidget(self.rt_resp_meta, 1)
        self.rt_resp_copy_btn = QPushButton()
        apply_button(self.rt_resp_copy_btn, 'ghost', compact=True, icon='copy', icon_size=16)
        self.rt_resp_copy_btn.clicked.connect(self._rt_copy_response_body)
        resp_head.addWidget(self.rt_resp_copy_btn)
        self.rt_resp_format_btn = QPushButton()
        apply_button(self.rt_resp_format_btn, 'secondary', compact=True, icon='json', icon_size=16)
        self.rt_resp_format_btn.clicked.connect(self._rt_send_response_to_format)
        resp_head.addWidget(self.rt_resp_format_btn)
        response_layout.addLayout(resp_head)
        # 响应区：摘要 + 完整 Body（不截断）
        self.draft_preview = QPlainTextEdit()
        self.draft_preview.setReadOnly(True)
        self.draft_preview.setObjectName('iface-draft-preview')
        self.draft_preview.setFont(mono)
        self.draft_preview.setMinimumHeight(220)
        self.draft_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.draft_preview.setPlaceholderText(
            '发送后此处显示完整响应 Body（不截断）；左侧可收藏接口与查看历史'
        )
        try:
            self.draft_preview.document().setMaximumBlockCount(0)
        except Exception:
            pass
        response_layout.addWidget(self.draft_preview, 1)
        self.rt_editor_response_splitter.addWidget(response_panel)
        self.rt_editor_response_splitter.setStretchFactor(0, 3)
        self.rt_editor_response_splitter.setStretchFactor(1, 2)
        request_test_sizes = self._prefs.get('request_test_splitter_sizes') or [560, 320]
        self.rt_editor_response_splitter.setSizes(request_test_sizes)
        self.rt_editor_response_splitter.splitterMoved.connect(self._save_request_test_splitter_sizes)
        rf.addWidget(self.rt_editor_response_splitter, 1)
        self.rt_split.addWidget(right_form)
        self.rt_split.setStretchFactor(0, 0)
        self.rt_split.setStretchFactor(1, 1)
        self.rt_split.setSizes([280, 520])
        dl.addWidget(self.rt_split, 1)

        self._rt_last_request_body = ''
        self._rt_last_response_body = ''
        self._rt_last_response_headers = {}
        self._rt_lib = None
        self._rt_editing_api_id = ''
        self._rt_send_started_at = 0.0
        self._rt_lib_reload(refresh_ui=True)
        self._rt_sync_ssl_checkbox_from_settings()
        self.detail_tabs.addTab(self.draft_page, '请求测试')
        self.detail_tabs.currentChanged.connect(self._on_detail_tab_changed)

        # 键盘路径：采集 → 定位 → 会话 → 详情 → 请求测试。
        QWidget.setTabOrder(self.capture_toggle_btn, self.filter_edit)
        QWidget.setTabOrder(self.filter_edit, self.table)
        QWidget.setTabOrder(self.table, self.detail_tabs)
        QWidget.setTabOrder(self.detail_tabs, self.rt_url)

        rl.addWidget(self.detail_tabs, 1)
        self.mid_splitter.addWidget(right)
        self.session_list_reveal_btn = QPushButton()
        apply_button(self.session_list_reveal_btn, 'secondary', compact=True, icon='external-open', icon_size=16)
        self.session_list_reveal_btn.setText('显示会话列表' if self.language == 'zh' else 'Show session list')
        self.session_list_reveal_btn.setToolTip(
            '恢复左侧会话列表' if self.language == 'zh' else 'Restore left session list'
        )
        self.session_list_reveal_btn.setMinimumWidth(110)
        self.session_list_reveal_btn.clicked.connect(self._toggle_session_list)
        self.session_list_reveal_btn.hide()
        rl.addWidget(self.session_list_reveal_btn, 0, Qt.AlignmentFlag.AlignLeft)
        from ui.splitter_prefs import install_splitter_prefs, layout_bucket
        sizes = (self._prefs.get('splitter_sizes') or {}).get('standard') or [420, 580]
        install_splitter_prefs(
            self.mid_splitter,
            defaults=[420, 580],
            saved=sizes,
            page_id='interface-debug',
            tab_id='session-detail',
            bucket=layout_bucket('standard'),
            min_sizes=[240, 480],
            accessible_name='接口排查会话/详情分隔',
            on_changed=lambda values: self._save_splitter_sizes(*values[:2]),
        )
        # splitterMoved 发生时子控件几何信息尚未稳定，下一事件循环再按最终宽度重算按钮和列。
        self.mid_splitter.splitterMoved.connect(
            lambda *_: QTimer.singleShot(0, self._update_responsive_workspace)
        )
        root.addWidget(self.mid_splitter, 1)

        self.loading = AuroraProgress(self)
        self._refresh_browsers()
        self._rebuild_table()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading'):
            self.loading.place_overlay(self)
        if hasattr(self, 'mid_splitter'):
            self._update_responsive_workspace()

    def _make_overflow_button(self, layout):
        """为动作行创建固定可达的收纳入口。"""
        button = QToolButton()
        button.setObjectName('responsive-more-btn')
        button.setText('更多')
        button.setToolTip('显示收纳操作')
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        button.setMenu(menu)
        button.hide()
        layout.addWidget(button)
        return button, menu

    @staticmethod
    def _set_widgets_visible(widgets, visible):
        for widget in widgets:
            widget.setVisible(visible)

    def _rebuild_overflow_menu(self, menu, actions):
        """菜单项直接复用现有控件行为，不复制业务逻辑。"""
        menu.clear()
        for label, callback in actions:
            action = menu.addAction(label)
            action.triggered.connect(callback)

    def _rebuild_capture_actions_menu(self):
        if not hasattr(self, '_capture_actions_menu') or self._capture_actions_menu is None:
            return
        menu = self._capture_actions_menu
        menu.clear()
        zh = self.language == 'zh'
        from tools.ie_proxy import is_recorded_root_cert_installed
        cert_installed = is_recorded_root_cert_installed()

        if cert_installed:
            remove_act = menu.addAction('移除 HTTPS 抓包证书' if zh else 'Remove HTTPS CA certificate')
            remove_act.triggered.connect(self._remove_ie_cert)
        else:
            install_act = menu.addAction('安装 HTTPS 抓包证书' if zh else 'Install HTTPS CA certificate')
            install_act.triggered.connect(self._install_ie_cert)

        menu.addSeparator()
        restore_act = menu.addAction('恢复系统代理' if zh else 'Restore system proxy')
        restore_act.triggered.connect(self._manual_restore_proxy)

        widths = getattr(self, '_last_responsive_widths', None) or (1000, 1000, 1000)
        if widths[0] < 500:
            test_act = menu.addAction('测试监听' if zh else 'Test listen')
            test_act.triggered.connect(self._test_listen_loopback)

    def _refresh_capture_status_text(self):
        zh = self.language == 'zh'
        from tools.ie_proxy import is_recorded_root_cert_installed, is_capture_proxy_suspended
        cert_installed = is_recorded_root_cert_installed()
        https_text = ('已启用' if cert_installed else '未启用') if zh else ('Enabled' if cert_installed else 'Disabled')

        if self._listening:
            port = self._current_port()
            if is_capture_proxy_suspended():
                proxy_text = '已暂停' if zh else 'Suspended'
            else:
                proxy_text = f'监听中 127.0.0.1:{port}' if zh else f'127.0.0.1:{port}'
        else:
            proxy_text = '正常' if zh else 'Normal'

        if hasattr(self, 'toolbar_hint') and self.toolbar_hint is not None:
            self.toolbar_hint.setText(
                f'系统代理：{proxy_text} · HTTPS 解密：{https_text}'
                if zh else
                f'System proxy: {proxy_text} · HTTPS decryption: {https_text}'
            )

    def _update_responsive_workspace(self, left_width=None, right_width=None, table_width=None):
        """按实际分隔条宽度收纳次要按钮，并同步压缩会话诊断列。"""
        explicit_widths = any(value is not None for value in (left_width, right_width, table_width))
        left_width = int(left_width if left_width is not None else self._session_list_widget.width())
        right_width = int(right_width if right_width is not None else self.detail_workspace.width())
        table_width = int(table_width if table_width is not None else self.table.viewport().width())
        # QTableWidget 的最小 SizeHint 可能让视口尺寸滞后于父栏；列断点不能超过左栏实际可用宽度。
        table_width = min(table_width, left_width)
        # 离屏初始化尚无布局宽度时不能误判为窄屏；真实 resize/splitter 事件会在可见后重算。
        if not explicit_widths and (left_width <= 0 or right_width <= 0):
            return
        self._last_responsive_widths = (left_width, right_width, table_width)
        self._update_session_toolbar_overflow(left_width)

        capture_compact = left_width < 500
        self._set_widgets_visible((self.test_listen_btn, self.restore_proxy_btn), not capture_compact)
        self.capture_actions_more_btn.setVisible(True)
        self._rebuild_capture_actions_menu()
        self._refresh_capture_status_text()

        detail_compact = right_width < 500
        self._set_widgets_visible((self.format_req_btn, self.gateway_req_btn), not detail_compact)
        self.req_actions_more_btn.setVisible(detail_compact)
        if detail_compact:
            self._rebuild_overflow_menu(self._req_actions_menu, [
                ('送格式工具' if self.language == 'zh' else 'Format tools', lambda: self._send_body_side('request', 'format')),
                ('送入加解密' if self.language == 'zh' else 'Crypto', lambda: self._send_body_side('request', 'gateway')),
            ])
        self._set_widgets_visible((self.format_resp_btn, self.gateway_resp_btn), not detail_compact)
        self.resp_actions_more_btn.setVisible(detail_compact)
        if detail_compact:
            self._rebuild_overflow_menu(self._resp_actions_menu, [
                ('送格式工具' if self.language == 'zh' else 'Format tools', lambda: self._send_body_side('response', 'format')),
                ('送入加解密' if self.language == 'zh' else 'Crypto', lambda: self._send_body_side('response', 'gateway')),
            ])

        request_test_compact = right_width < 560
        request_test_actions = (
            self.export_detail_btn, self.rt_import_btn, self.rt_req_copy_btn,
            self.rt_req_format_btn, self.rt_resp_copy_btn, self.rt_resp_format_btn,
        )
        self._set_widgets_visible(request_test_actions, not request_test_compact)
        self.rt_io_more_btn.setVisible(request_test_compact)
        if request_test_compact:
            self._rebuild_overflow_menu(self._rt_io_actions_menu, [
                ('导出明细' if self.language == 'zh' else 'Export detail', self._export_session_detail),
                ('导入明细' if self.language == 'zh' else 'Import', self._rt_import_file),
                ('复制请求' if self.language == 'zh' else 'Copy request', self._rt_copy_request_body),
                ('请求→格式工具' if self.language == 'zh' else 'Request → Format', self._rt_send_request_to_format),
                ('复制响应' if self.language == 'zh' else 'Copy response', self._rt_copy_response_body),
                ('响应→格式工具' if self.language == 'zh' else 'Response → Format', self._rt_send_response_to_format),
            ])

        form_compact = right_width < 560
        form_actions = (
            self.rt_environment_config_btn, self.rt_fill_btn, self.rt_filter_config_btn,
            self.rt_save_api_btn, self.rt_manage_cat_btn,
        )
        self._set_widgets_visible(form_actions, not form_compact)
        self.rt_form_more_btn.setVisible(form_compact)
        if form_compact:
            self._rebuild_overflow_menu(self._rt_form_actions_menu, [
                ('环境配置' if self.language == 'zh' else 'Environment settings', self._show_environment_config_dialog),
                ('从会话填充' if self.language == 'zh' else 'Fill from session', self._rt_fill_from_selection),
                ('过滤配置' if self.language == 'zh' else 'Filter settings', self._show_url_filter_config_dialog),
                ('保存接口' if self.language == 'zh' else 'Save API', self._rt_save_api),
                ('分类管理' if self.language == 'zh' else 'Manage categories', self._rt_manage_categories),
            ])

        self._adaptive_table_width = table_width
        self._apply_column_visibility()

    def _rebuild_session_actions_menu(self):
        """构建窄左栏的收纳菜单；复用原按钮行为，保证所有操作仍可访问。"""
        menu = self._session_actions_menu
        menu.clear()
        if self.test_listen_btn.isHidden():
            menu.addAction(
                '测试连接' if self.language == 'zh' else 'Test connection',
                self._test_listen_loopback,
            )
        if self.restore_proxy_btn.isHidden():
            menu.addAction(
                '恢复系统代理' if self.language == 'zh' else 'Restore proxy',
                self._manual_restore_proxy,
            )
        if self.test_listen_btn.isHidden() or self.restore_proxy_btn.isHidden():
            menu.addSeparator()
        for chip in self._filter_chips.values():
            action = menu.addAction(chip.text())
            action.setCheckable(True)
            action.setChecked(chip.isChecked())
            action.toggled.connect(chip.setChecked)
        menu.addSeparator()
        export_action = menu.addAction('导出会话明细' if self.language == 'zh' else 'Export session details')
        export_action.triggered.connect(self._export_session_detail)
        clear_action = menu.addAction('清空会话' if self.language == 'zh' else 'Clear sessions')
        clear_action.triggered.connect(self._confirm_clear_session)
        session_list = getattr(self, '_session_list_widget', None)
        list_hidden = bool(session_list is not None and session_list.isHidden())
        toggle_text = '显示会话列表' if list_hidden else '隐藏会话列表'
        toggle_action = menu.addAction(toggle_text if self.language == 'zh' else ('Show session list' if list_hidden else 'Hide session list'))
        toggle_action.triggered.connect(self._toggle_session_list)

    def _update_session_toolbar_overflow(self, available_width=None):
        """搜索已单独成行；第一行按宽度分档收纳抓包/筛选/导出。"""
        if not hasattr(self, 'session_actions_more_btn'):
            return
        width = int(available_width if available_width is not None else self._session_list_widget.width())
        show_chips = width >= 640
        show_io = width >= 760
        compact = not (show_chips and show_io)
        for chip in self._filter_chips.values():
            chip.setVisible(show_chips)
        self.export_list_btn.setVisible(show_io)
        self.clear_list_btn.setVisible(show_io)
        # 会话栏显隐是窄屏下恢复阅读区的主控件，始终保持可达。
        self._toggle_list_btn.show()
        self.session_actions_more_btn.setVisible(compact)
        if compact:
            self._rebuild_session_actions_menu()

    # ── 列 / 筛选 ────────────────────────────────────
    def _rebuild_column_menu(self):
        self._cols_menu.clear()
        visible = set(self._prefs.get('visible_columns') or [])
        labels = self.COL_LABELS_ZH if self.language == 'zh' else self.COL_LABELS_EN
        compact_core = ('status', 'method', 'url', 'duration', 'time')
        for key in COLUMN_KEYS:
            act = QAction(labels.get(key, key), self._cols_menu)
            act.setCheckable(True)
            if self._compact_session_view:
                # 高密度视图的列集合固定，避免菜单出现“已勾选但实际隐藏”的无效反馈。
                act.setChecked(key in compact_core)
                act.setEnabled(False)
            else:
                core = ('seq', 'status', 'method', 'name', 'host', 'url')
                act.setChecked(key in visible or key in core)
                act.setEnabled(key not in core)
                act.toggled.connect(lambda checked, k=key: self._toggle_column(k, checked))
            self._cols_menu.addAction(act)

    def _toggle_column(self, key: str, checked: bool):
        visible = list(self._prefs.get('visible_columns') or [])
        core = ('seq', 'status', 'method', 'host', 'url')
        if checked and key not in visible:
            visible.append(key)
        if not checked and key in visible and key not in core:
            visible.remove(key)
        self._prefs['visible_columns'] = visible
        update_ui_prefs({'visible_columns': visible})
        self._apply_column_visibility()

    def _column_index(self, key: str) -> int:
        return COLUMN_KEYS.index(key)

    def _apply_column_visibility(self):
        visible = set(self._prefs.get('visible_columns') or [])
        widths = self._prefs.get('column_widths') or {}
        # 默认高密度诊断视图只显示可扫读的关键列；完整字段可通过“列”菜单显式展开。
        compact_core = ('status', 'method', 'url', 'duration', 'time')
        # 会话表宽度不足时优先保留结果、方法和请求摘要；拉宽后自动恢复耗时与时间。
        table_width = int(getattr(self, '_adaptive_table_width', self.table.viewport().width()))
        if self._compact_session_view and table_width < 420:
            core = ('status', 'method', 'url')
        else:
            core = compact_core if self._compact_session_view else ('seq', 'status', 'method', 'host', 'url')
        header = self.table.horizontalHeader()
        last_visible = -1
        for i, key in enumerate(COLUMN_KEYS):
            show = key in core if self._compact_session_view else (key in visible or key in core)
            self.table.setColumnHidden(i, not show)
            if show:
                last_visible = i
            # URL 固定 Stretch 填满尾部空白；其余 Interactive 可拖宽
            if key == 'url':
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            w = widths.get(key)
            if w and key != 'url':
                try:
                    self.table.setColumnWidth(i, max(40, int(w)))
                except (TypeError, ValueError):
                    pass
        # 若 URL 被隐藏（不应），最后一列 Stretch 避免空白
        if last_visible >= 0 and COLUMN_KEYS[last_visible] != 'url':
            header.setSectionResizeMode(last_visible, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _on_column_resized(self, index: int, _old: int, new: int):
        if index < 0 or index >= len(COLUMN_KEYS):
            return
        key = COLUMN_KEYS[index]
        widths = dict(self._prefs.get('column_widths') or {})
        widths[key] = max(40, int(new))
        self._prefs['column_widths'] = widths
        # 防抖保存
        if not hasattr(self, '_width_save_timer'):
            self._width_save_timer = QTimer(self)
            self._width_save_timer.setSingleShot(True)
            self._width_save_timer.timeout.connect(
                lambda: update_ui_prefs({'column_widths': self._prefs.get('column_widths')})
            )
        self._width_save_timer.start(400)

    def _on_header_clicked(self, index: int):
        if index < 0 or index >= len(COLUMN_KEYS):
            return
        key = COLUMN_KEYS[index]
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = key in ('time', 'duration', 'body', 'status', 'seq')
        self._prefs['sort_key'] = self._sort_key
        self._prefs['sort_desc'] = self._sort_desc
        update_ui_prefs({'sort_key': self._sort_key, 'sort_desc': self._sort_desc})
        self._rebuild_table()

    def _on_filter_chip(self, key: str, checked: bool):
        chips = self._filter_chips
        if key == FILTER_ALL and checked:
            for k, chip in chips.items():
                if k != FILTER_ALL:
                    chip.blockSignals(True)
                    chip.setChecked(False)
                    chip.blockSignals(False)
            self._active_filters = [FILTER_ALL]
        else:
            if checked and FILTER_ALL in self._active_filters:
                self._active_filters = [f for f in self._active_filters if f != FILTER_ALL]
                chips[FILTER_ALL].blockSignals(True)
                chips[FILTER_ALL].setChecked(False)
                chips[FILTER_ALL].blockSignals(False)
            if checked and key not in self._active_filters:
                self._active_filters.append(key)
            if not checked and key in self._active_filters:
                self._active_filters.remove(key)
            if not self._active_filters:
                self._active_filters = [FILTER_ALL]
                chips[FILTER_ALL].blockSignals(True)
                chips[FILTER_ALL].setChecked(True)
                chips[FILTER_ALL].blockSignals(False)
        self._show_static = FILTER_STATIC in self._active_filters
        self._prefs['active_filters'] = list(self._active_filters)
        self._prefs['show_static'] = self._show_static
        update_ui_prefs({
            'active_filters': self._active_filters,
            'show_static': self._show_static,
        })
        self._rebuild_table()

    def _on_reveal(self, checked):
        if checked and not self._reveal_sensitive:
            zh = self.language == 'zh'
            ok = confirm_action(
                self,
                '显示敏感内容' if zh else 'Reveal secrets',
                (
                    '将显示 Authorization、Cookie、Token 等敏感字段。仅本会话有效，停止监听后清空。'
                    if zh else
                    'Reveal Authorization/Cookie/Token for this session only.'
                ),
                confirm_text='显示' if zh else 'Reveal',
                danger=True,
            )
            if not ok:
                self.reveal_cb.blockSignals(True)
                self.reveal_cb.setChecked(False)
                self.reveal_cb.blockSignals(False)
                return
        self._reveal_sensitive = bool(checked)
        self._refresh_detail()

    def _on_include_auth(self, checked):
        self._prefs['include_auth_in_draft'] = bool(checked)
        update_ui_prefs({'include_auth_in_draft': bool(checked)})
        self._refresh_draft_preview()

    # ── 配置 / 浏览器 ──────────────────────────────────
    def _reload_config_ui(self):
        self._config = load_interface_debug_config()
        self._prefs = dict(self._config.get('ui_prefs') or {})
        self.port_edit.setText(str(self._config.get('debug_port') or 9222))
        self._fill_local_targets()
        path = self._config.get('browser_path') or ''
        if path:
            for i in range(self.browser_combo.count()):
                if self.browser_combo.itemData(i) == path:
                    self.browser_combo.setCurrentIndex(i)
                    break

    def _fill_local_targets(self):
        self.local_target_combo.blockSignals(True)
        self.local_target_combo.clear()
        targets = self._config.get('local_targets') or []
        default_id = self._config.get('default_target_id') or ''
        sel = 0
        for i, t in enumerate(targets):
            label = f"{t.get('name') or '环境'} · {t.get('base_url') or ''}"
            self.local_target_combo.addItem(label, t.get('id'))
            if t.get('id') == default_id:
                sel = i
        if not targets:
            self.local_target_combo.addItem('（未保存环境 · 可填 Base 后点保存）', '')
        self.local_target_combo.setCurrentIndex(sel)
        self.local_target_combo.blockSignals(False)
        # 同步 Base 输入框
        self._on_env_selected(sel)

    def _on_env_selected(self, index: int = 0):
        tid = self.local_target_combo.currentData() if hasattr(self, 'local_target_combo') else None
        if not tid:
            return
        targets = self._config.get('local_targets') or []
        item = next((t for t in targets if t.get('id') == tid), None)
        if not item:
            return
        base = (item.get('base_url') or '').strip()
        if base and hasattr(self, 'rt_base_edit'):
            self.rt_base_edit.setText(base)
        self._config['default_target_id'] = tid
        try:
            save_interface_debug_config(self._config)
        except Exception:
            pass
        # 若 URL 已有内容，按新环境重写 host
        if hasattr(self, 'rt_url') and (self.rt_url.text() or '').strip():
            try:
                from tools.iface_request_test import rewrite_url_with_base
                cur = self.rt_url.text().strip()
                self.rt_url.setText(rewrite_url_with_base(cur, base))
            except Exception:
                pass
        self._rt_refresh_send_label()

    def _rt_resolve_send_host(self) -> str:
        raw = ''
        if hasattr(self, 'rt_url'):
            raw = (self.rt_url.text() or '').strip()
        if '://' not in raw and hasattr(self, 'rt_base_edit'):
            raw = (self.rt_base_edit.text() or '').strip()
        if not raw:
            return ''
        try:
            from urllib.parse import urlparse
            parsed = urlparse(raw if '://' in raw else f'http://{raw}')
            return (parsed.hostname or '').strip()
        except Exception:
            return ''

    def _rt_refresh_send_label(self):
        if not hasattr(self, 'rt_send_btn'):
            return
        zh = self.language == 'zh'
        host = self._rt_resolve_send_host()
        tid = self.local_target_combo.currentData() if hasattr(self, 'local_target_combo') else None
        if not host and not tid:
            self.rt_send_btn.setText('选择环境后发送' if zh else 'Select environment')
            self.rt_send_btn.setEnabled(False)
            return
        self.rt_send_btn.setEnabled(True)
        if host:
            self.rt_send_btn.setText(f'发送 · 到 {host}' if zh else f'Send · to {host}')
        else:
            self.rt_send_btn.setText('发送 · 到所选环境' if zh else 'Send · to environment')

    def _refresh_browsers(self):
        current = self.browser_combo.currentData()
        self.browser_combo.blockSignals(True)
        self.browser_combo.clear()
        browsers = discover_browsers()
        saved = (self._config.get('browser_path') or '').strip()
        recent = list(self._config.get('recent_browser_paths') or [])
        for path in recent:
            if path and os.path.isfile(path) and not any(
                (b.get('path') or '').lower() == path.lower() for b in browsers
            ):
                browsers.insert(0, {
                    'name': '最近使用',
                    'path': path,
                    'is_chromium': 'firefox' not in path.lower(),
                    'is_firefox': 'firefox' in path.lower(),
                })
        if saved and os.path.isfile(saved) and not any(
            (b.get('path') or '').lower() == saved.lower() for b in browsers
        ):
            browsers.insert(0, {
                'name': '已保存浏览器',
                'path': saved,
                'is_chromium': 'firefox' not in saved.lower(),
                'is_firefox': 'firefox' in saved.lower(),
            })
        for b in browsers:
            tag = '' if b.get('is_chromium') else ' [Firefox]'
            short = os.path.basename(b['path'] or '')
            label = f"{b['name']}{tag} · {short}"
            self.browser_combo.addItem(label, b['path'])
            idx = self.browser_combo.count() - 1
            self.browser_combo.setItemData(idx, b, Qt.ItemDataRole.UserRole + 1)
            self.browser_combo.setItemData(idx, b['path'], Qt.ItemDataRole.ToolTipRole)
        if current:
            for i in range(self.browser_combo.count()):
                if self.browser_combo.itemData(i) == current:
                    self.browser_combo.setCurrentIndex(i)
                    break
        elif saved:
            for i in range(self.browser_combo.count()):
                if self.browser_combo.itemData(i) == saved:
                    self.browser_combo.setCurrentIndex(i)
                    break
        self.browser_combo.blockSignals(False)

    def _pick_browser(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            '选择浏览器 EXE' if self.language == 'zh' else 'Pick browser EXE',
            os.environ.get('PROGRAMFILES', 'C:\\'),
            'Executable (*.exe);;All (*.*)',
        )
        if not path:
            return
        self._config['browser_path'] = path
        recent = list(self._config.get('recent_browser_paths') or [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self._config['recent_browser_paths'] = recent[:8]
        save_interface_debug_config(self._config)
        self._refresh_browsers()
        if 'firefox' in path.lower():
            show_warning(
                self, '浏览器',
                'Firefox 暂不支持实时监听；请使用 Chromium 内核浏览器。',
            )

    def _selected_browser_meta(self) -> dict:
        idx = self.browser_combo.currentIndex()
        meta = self.browser_combo.itemData(idx, Qt.ItemDataRole.UserRole + 1)
        if isinstance(meta, dict):
            return meta
        path = self.browser_combo.currentData() or ''
        return {
            'path': path,
            'is_chromium': 'firefox' not in (path or '').lower(),
            'is_firefox': 'firefox' in (path or '').lower(),
        }

    def _current_port(self) -> int:
        try:
            return max(1, min(65535, int(self.port_edit.text().strip() or '9222')))
        except ValueError:
            return 9222

    def _save_port(self):
        port = self._current_port()
        if self._mode == 'chromium':
            self._config['debug_port'] = port
        else:
            self._config['ie_proxy_port'] = port
        path = self.browser_combo.currentData() or ''
        if path:
            self._config['browser_path'] = path
        save_interface_debug_config(self._config)

    def _save_splitter_sizes(self, *_args):
        sizes = self.mid_splitter.sizes()
        if len(sizes) < 2:
            return
        all_sizes = dict(self._prefs.get('splitter_sizes') or {})
        all_sizes[self._layout_mode] = sizes
        self._prefs['splitter_sizes'] = all_sizes
        update_ui_prefs({'splitter_sizes': all_sizes})

    def _save_request_test_splitter_sizes(self, *_args):
        """只保存请求测试区域的非敏感视觉尺寸。"""
        sizes = self.rt_editor_response_splitter.sizes()
        if len(sizes) < 2:
            return
        saved = [int(sizes[0]), int(sizes[1])]
        self._prefs['request_test_splitter_sizes'] = saved
        update_ui_prefs({'request_test_splitter_sizes': saved})

    def apply_default_splitter_sizes(self):
        """套用已复位的 ui_prefs 分栏默认值（不改会话/报文）。"""
        self._config = load_interface_debug_config()
        self._prefs = dict(self._config.get('ui_prefs') or {})
        mode = getattr(self, '_layout_mode', 'standard') or 'standard'
        sizes = (self._prefs.get('splitter_sizes') or {}).get(mode)
        if hasattr(self, 'mid_splitter') and sizes and len(sizes) >= 2:
            self.mid_splitter.setSizes([int(sizes[0]), int(sizes[1])])
        rt_sizes = self._prefs.get('request_test_splitter_sizes')
        if (
            hasattr(self, 'rt_editor_response_splitter')
            and isinstance(rt_sizes, (list, tuple))
            and len(rt_sizes) >= 2
        ):
            self.rt_editor_response_splitter.setSizes([int(rt_sizes[0]), int(rt_sizes[1])])

    # ── 模式 ──────────────────────────────────────────
    def _mode_from_index(self, index: int) -> str:
        return {0: 'proxy', 1: 'chromium', 2: 'ie'}.get(int(index), 'proxy')

    def _apply_mode_ui(self):
        """界面只保留抓包；其它入口一律隐藏。"""
        zh = self.language == 'zh'
        self._mode = 'proxy'
        for w in (
            self.mode_label, self.mode_combo, self.mode_hint,
            self.browser_combo, self.refresh_browsers_btn, self.pick_browser_btn,
            self.launch_btn, self.target_combo, self.port_label, self.port_edit,
            self.ie_install_cert_btn, self.ie_remove_cert_btn, self.recheck_btn,
            self.conn_more_btn,
        ):
            if w is not None:
                w.hide()
        self._refresh_capture_action()
        self._update_empty_hint()

    def _on_mode_changed(self, index):
        # 模式切换已取消，固定抓包
        self._mode = 'proxy'
        self._apply_mode_ui()

    # ── 连接 / 监听 ──────────────────────────────────
    def _launch_browser(self):
        if self._mode != 'chromium':
            show_info(self, '浏览器', '仅 Chromium CDP 高级模式需要启动调试浏览器。')
            return
        meta = self._selected_browser_meta()
        path = meta.get('path') or ''
        if meta.get('is_firefox'):
            show_warning(self, '浏览器', 'Firefox 暂不支持实时监听；请使用 Chromium 内核浏览器。')
            return
        if not path or not os.path.isfile(path):
            show_warning(self, '浏览器', '请先选择有效的 Chromium 浏览器 EXE')
            return
        self._save_port()
        self.loading.start_busy('正在启动调试浏览器…' if self.language == 'zh' else 'Launching…')
        self._launch_worker = _LaunchBrowserWorker(path, self._current_port(), self)
        self._launch_worker.finished_ok.connect(self._on_launch_ok)
        self._launch_worker.failed.connect(self._on_launch_fail)
        self._launch_worker.start()

    def _on_launch_ok(self, port):
        self.loading.finish('浏览器已就绪' if self.language == 'zh' else 'Browser ready')
        self.status_label.setText(
            f'调试浏览器已启动 · 端口 {port} · 请打开业务页后点击「开始监听」'
        )
        QTimer.singleShot(400, self._refresh_targets)

    def _on_launch_fail(self, msg):
        self.loading.fail(msg)
        show_warning(self, '启动浏览器', msg)

    def _refresh_targets(self):
        try:
            targets = fetch_cdp_targets(self._current_port())
        except BrowserDebugError as exc:
            self.target_combo.clear()
            self.status_label.setText(str(exc))
            return
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for t in targets:
            if (t.get('type') or '') != 'page':
                continue
            title = (t.get('title') or '')[:40]
            url = (t.get('url') or '')[:60]
            self.target_combo.addItem(f'{title} · {url}', t)
        default = pick_default_page_target(targets)
        if default:
            for i in range(self.target_combo.count()):
                data = self.target_combo.itemData(i)
                if isinstance(data, dict) and data.get('id') == default.get('id'):
                    self.target_combo.setCurrentIndex(i)
                    break
        self.target_combo.blockSignals(False)

    def _refresh_capture_action(self, busy: bool = False):
        """统一刷新唯一抓包主操作，不触碰会话或代理状态。"""
        active = bool(self._listening)
        zh = self.language == 'zh'
        self.capture_toggle_btn.setText(
            ('停止监听' if zh else 'Stop listen') if active else
            ('开始监听' if zh else 'Start listen')
        )
        self.capture_toggle_btn.setEnabled(not busy)
        apply_button(
            self.capture_toggle_btn,
            'primary',
            compact=True,
            icon='lock' if active else 'external-open',
            icon_size=16,
        )
        # 兼容旧状态引用；这两个按钮始终隐藏。
        self.connect_btn.setEnabled(not active and not busy)
        self.stop_btn.setEnabled(active and not busy)
        self.connect_btn.hide()
        self.stop_btn.hide()
        self._refresh_listen_status_pill()

    def _refresh_listen_status_pill(self):
        zh = self.language == 'zh'
        total = len(getattr(self, '_records', []) or [])
        if self._listening:
            self.offline_pill.setText(
                (f'监听中 · {total} 条会话' if zh else f'Listening · {total} session(s)')
            )
        else:
            self.offline_pill.setText(
                (f'未监听 · {total} 条会话' if zh else f'Idle · {total} session(s)')
            )

    def _toggle_capture(self):
        if self._listening:
            self._stop_listen()
        else:
            self._connect_or_start()

    def _connect_or_start(self):
        # 产品面只有抓包一条路
        self._mode = 'proxy'
        self._start_local_proxy(ie_mode=False)

    def _ensure_capture_ready_silently(self):
        """仅确保本地 mitmproxy CA 证书文件已就绪，绝不静默修改 Windows 受信任根证书库。"""
        try:
            ensure_mitm_ca_exists = __import__(
                'tools.ie_proxy', fromlist=['ensure_mitm_ca_exists']
            ).ensure_mitm_ca_exists
            ensure_mitm_ca_exists()
        except Exception:
            pass

    def _connect_cdp(self):
        port = self._current_port()
        if not is_loopback_host('127.0.0.1'):
            show_warning(self, '连接', 'CDP 仅允许 127.0.0.1')
            return
        self._save_port()
        if not port_open(port):
            show_warning(
                self, '连接',
                f'端口 {port} 不可用。请先启动调试浏览器，或用 '
                f'--remote-debugging-port={port} --remote-debugging-address=127.0.0.1 启动。',
            )
            return
        self.loading.start_busy('正在连接 CDP 并注册 Network 事件…')
        try:
            self._refresh_targets()
            target = self.target_combo.currentData()
            if not isinstance(target, dict):
                targets = fetch_cdp_targets(port)
                target = pick_default_page_target(targets)
            # wait_ready=True：仅在 Network.enable 成功后才算监听成功
            session = connect_page_session(
                port, target=target, host='127.0.0.1',
                on_event=self._on_cdp_event_thread,
                on_error=self._on_cdp_error_thread,
                on_closed=self._on_cdp_closed_thread,
                wait_ready=True,
                ready_timeout=8.0,
            )
            if not getattr(session, 'ready', False):
                try:
                    session.stop()
                except Exception:
                    pass
                raise BrowserDebugError('CDP 事件通道未就绪，不能标记为监听成功')
            self._cdp_session = session
            self._mark_listen_success(
                f'CDP 监听中 · 127.0.0.1:{port} · {target.get("title") or target.get("url") or "page"}'
            )
            self.loading.finish('CDP 通道已建立')
        except Exception as exc:
            self.loading.fail(str(exc))
            self._channel_ready = False
            self._listening = False
            self._set_listening_ui(False)
            show_warning(self, '连接 CDP', str(exc))

    def _on_cdp_event_thread(self, method, params):
        QTimer.singleShot(0, lambda m=method, p=dict(params or {}): self._handle_cdp_event(m, p))

    def _on_cdp_error_thread(self, msg):
        QTimer.singleShot(0, lambda: self._on_cdp_error(msg))

    def _on_cdp_error(self, msg):
        self.status_label.setText(f'CDP 错误：{msg}')
        if self._listening and not self._channel_ready:
            self.loading.fail(str(msg))
            self._listening = False
            self._set_listening_ui(False)

    def _on_cdp_closed_thread(self):
        QTimer.singleShot(0, self._on_cdp_closed)

    def _on_cdp_closed(self):
        if self._listening and self._mode == 'chromium':
            self.status_label.setText('已断开 · CDP 连接已关闭')
            self._listening = False
            self._channel_ready = False
            self._set_listening_ui(False)
            self._wait_hint_timer.stop()
            self._status_tick.stop()

    def _handle_cdp_event(self, method, params):
        if not self._cdp_session:
            return
        with self._cdp_session._lock:
            records = dict(self._cdp_session.records)
        prev_selected = self._selected_id
        for rid, rec in records.items():
            self._records_by_id[rid] = dict(rec)
        self._records = list(self._records_by_id.values())
        if self._records:
            self._last_request_at = time.time()
        self._rebuild_table()
        # 新请求不得覆盖当前已选详情
        if prev_selected and prev_selected in self._records_by_id:
            self._selected_id = prev_selected

    def _detach_capture_worker(self, worker) -> None:
        """切断旧 worker 回调，避免 stop 晚到信号清掉新一轮监听。"""
        if worker is None:
            return
        for attr in ('on_record', 'on_error', 'on_stopped', 'on_ready'):
            try:
                setattr(worker, attr, None)
            except Exception:
                pass

    def _start_local_proxy(self, ie_mode: bool = False):
        """异步启动抓包：不在主线程 sleep 等待，避免点任何按钮都像超时。"""
        zh = self.language == 'zh'
        title = '开始监听' if zh else 'Start listen'
        if self._lifecycle.state in ('starting', 'running'):
            show_info(self, title, '已在监听中' if zh else 'Already listening')
            return

        # 首次/未安装 HTTPS 抓包根证书检查：必须先获得用户明确授权
        from tools.ie_proxy import is_recorded_root_cert_installed, install_user_root_cert
        if not is_recorded_root_cert_installed():
            from ui.confirm_dialog import confirm_https_cert_consent
            if not confirm_https_cert_consent(self, language=self.language, for_listen=True):
                # 用户取消：不调用 install，不启动 worker，不改系统代理，状态保持 idle
                return

            # 用户明确同意：执行安装
            self.loading.start_busy('正在安装抓包根证书…' if zh else 'Installing CA certificate…')
            try:
                install_user_root_cert()
                self._config = load_interface_debug_config()
                self._refresh_capture_status_text()
                self._rebuild_capture_actions_menu()
                self.loading.finish('证书已就绪' if zh else 'Certificate ready')
            except Exception as exc:
                self.loading.fail(str(exc))
                self._refresh_capture_status_text()
                self._rebuild_capture_actions_menu()
                show_warning(self, '安装证书失败' if zh else 'Install Failed', str(exc))
                return

        action = self._lifecycle.begin_start()
        if action is None:
            # STOPPING：记录 pending start，stop 线程收尾后自动重启；绝不阻塞 UI 主线程
            if self._lifecycle.state == 'stopping':
                self.loading.start_busy(
                    '正在等待上一轮监听结束…' if zh else 'Waiting for previous stop…')
            else:
                show_info(self, title, '已在监听中' if zh else 'Already listening')
            return
        boot_epoch = action
        self._capture_epoch = boot_epoch
        self._capture_boot_epoch = boot_epoch
        port = self._current_port()
        self._config['ie_proxy_port'] = port
        save_interface_debug_config(self._config)
        self.loading.start_busy('正在开始监听…' if zh else 'Starting listen…')
        self._refresh_capture_action(busy=True)
        self._refresh_capture_status_text()
        try:
            self._ensure_capture_ready_silently()
        except Exception:
            pass

        def _boot():
            from tools.http_capture import HttpCaptureWorker
            import socket as _socket

            def _port_in_use() -> bool:
                try:
                    with _socket.create_connection(('127.0.0.1', int(port)), timeout=0.35):
                        return True
                except OSError:
                    return False

            # 上一轮 stop 可能仍在后台释放端口；这里主动等端口关闭，避免新引擎抢不到端口。
            _port_deadline = time.time() + 4.0
            while _port_in_use() and time.time() < _port_deadline:
                time.sleep(0.08)

            def _try_once():
                worker = HttpCaptureWorker(
                    port=port,
                    on_record=lambda rec, e=boot_epoch: self._sig_capture_record.emit(int(e), dict(rec or {})),
                    on_error=lambda msg, e=boot_epoch: self._sig_capture_error.emit(int(e), str(msg or '')),
                    on_stopped=lambda e=boot_epoch: self._sig_capture_stopped.emit(int(e)),
                    show_static=True,
                    source_label='http_capture',
                    apply_system_proxy=True,
                )
                worker._pengtools_epoch = boot_epoch
                worker.start()
                ready = False
                try:
                    ready = bool(worker.wait_ready(timeout=12.0))
                except Exception:
                    ready = bool(getattr(worker, 'ready', False))
                if ready:
                    return {'ok': True, 'worker': worker, 'port': port, 'epoch': boot_epoch}
                self._detach_capture_worker(worker)
                try:
                    worker.stop(join_timeout=1.2)
                except Exception:
                    pass
                return None

            # 绑定失败（端口被上一轮占用）时快速失败，多等几轮重试：
            # 旧引擎释放通常在数秒内完成，避免直接把"端口被占用"抛给用户。
            result = None
            for attempt in range(3):
                first = _try_once()
                if first:
                    result = first
                    break
                if attempt < 2:
                    time.sleep(1.5)
            if result:
                return result
            return {
                'ok': False,
                'error': '抓包未就绪（端口可能被占用）。请关闭占用后重试。',
                'port': port,
                'epoch': boot_epoch,
            }

        class _Boot(QThread):
            done = pyqtSignal(object)

            def run(self_inner):
                try:
                    self_inner.done.emit(_boot())
                except Exception as exc:
                    self_inner.done.emit({'ok': False, 'error': str(exc), 'port': port, 'epoch': boot_epoch})

        boot = _Boot(self)
        self._capture_boot_worker = boot

        boot.done.connect(
            lambda result: self._on_capture_boot_result(
                result, boot_epoch=boot_epoch, port=port, title=title, zh=zh))
        boot.finished.connect(lambda: setattr(self, '_capture_boot_worker', None))
        boot.start()

    def _on_capture_boot_result(self, result, boot_epoch: int, port: int, title: str, zh: bool):
        """boot 结果处理（QThread 回调经 Qt 信号在主线程执行）。"""
        result = result or {}
        if int(result.get('epoch') or 0) != boot_epoch:
            # 过期 boot 结果：丢弃，不改当前状态
            worker = result.get('worker')
            self._detach_capture_worker(worker)
            if worker is not None:
                try:
                    worker.stop(join_timeout=0.8)
                except Exception:
                    pass
            return
        worker = result.get('worker')
        if not result.get('ok'):
            # 当前有效启动失败：lifecycle 回 IDLE，UI 回非监听并恢复代理
            if self._lifecycle.fail_start(boot_epoch):
                err = result.get('error') or '启动失败'
                self._ie_worker = None
                self._refresh_capture_action()
                self.loading.fail(err)
                show_warning(self, title, err)
                try:
                    restore_proxy_from_snapshot()
                except Exception:
                    pass
            # 过期 failure：不影响当前生命周期/UI
            return
        if not self._lifecycle.mark_running(boot_epoch):
            # 已 STOPPING / 已被新一轮取代：worker 不得进入 RUNNING
            self._detach_capture_worker(worker)
            if worker is not None:
                try:
                    worker.stop(join_timeout=0.8)
                except Exception:
                    pass
            return
        self._ie_worker = worker
        self._probe_capture_pipeline(int(result.get('port') or port))
        self._mark_listen_success(
            f'监听中 · 系统代理 127.0.0.1:{port} · 请重启浏览器后访问业务页 · '
            f'离开本页会自动暂停系统代理（引擎仍可运行），其它软件不再被拖死'
        )
        self.loading.finish('监听已开始' if zh else 'Listen started')
        try:
            self.loading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        except Exception:
            pass

    def _probe_capture_pipeline(self, port: int):
        """经本地代理发一条纯 loopback HTTP 探测。"""
        def _run():
            try:
                import socket
                request = (
                    b'GET http://127.0.0.1:9/pengtools-capture-probe HTTP/1.1\r\n'
                    b'Host: 127.0.0.1:9\r\n'
                    b'Connection: close\r\n\r\n'
                )
                with socket.create_connection(('127.0.0.1', int(port)), timeout=1.0) as sock:
                    sock.sendall(request)
            except Exception:
                pass
        import threading
        threading.Thread(target=_run, name='capture-probe', daemon=True).start()

    def _mark_listen_success(self, status_text: str):
        self._listening = True
        self._channel_ready = True
        self._listen_started_at = time.time()
        self._last_request_at = 0.0
        self._set_listening_ui(True)
        self.status_label.setText(status_text)
        self._update_empty_hint()
        self._refresh_live_status()
        self._wait_hint_timer.start()
        if not self._status_tick.isActive():
            self._status_tick.start()

    def _on_wait_hint(self):
        if not self._listening or not self._channel_ready:
            return
        if self._records:
            return
        zh = self.language == 'zh'
        self.status_label.setText(
            '仍未收到流量：请完全退出并重新打开 Chrome/Edge 后再访问页面；'
            '设置 → 系统 → 代理 中应能看到 127.0.0.1。公司代理/VPN 可能劫持流量。'
            if zh else
            'No traffic yet — fully restart Chrome/Edge; check system proxy is 127.0.0.1.'
        )
        self._update_empty_hint()

    def _refresh_live_status(self):
        if not self._listening:
            self.live_status.setText('')
            return
        zh = self.language == 'zh'
        n = len(self._records)
        last = (
            datetime.fromtimestamp(self._last_request_at).strftime('%H:%M:%S')
            if self._last_request_at else '—'
        )
        # Fiddler 式状态：正在抓取 / 会话条数 / 最近一条时间；触顶提示内存上限
        cap_note = ''
        if n >= MAX_SESSION_RECORDS:
            cap_note = (f' · 已达上限 {MAX_SESSION_RECORDS}（旧记录已淘汰）'
                        if zh else f' · cap {MAX_SESSION_RECORDS} (oldest dropped)')
        self.live_status.setText(
            f'抓包中 · 本机 HTTP/HTTPS · 会话 {n}{cap_note} · 最近 {last}'
            if zh else
            f'Capturing · HTTP/HTTPS · sessions {n}{cap_note} · last {last}'
        )

    def _recheck_channel(self):
        """检查代理/重新连接入口。"""
        if self._mode == 'chromium':
            if self._cdp_session and getattr(self._cdp_session, 'ready', False) and port_open(self._current_port()):
                show_info(self, '检查', 'CDP 端口可连接，通道仍在。请在业务页发起请求。')
            else:
                show_warning(self, '检查', 'CDP 通道异常，请停止后重新开始监听。')
            return
        port = self._current_port()
        if port_open(port):
            show_info(
                self, '检查代理',
                f'127.0.0.1:{port} 可连接。请确认浏览器使用系统代理，HTTPS 已安装抓包证书。',
            )
        else:
            show_warning(self, '检查代理', f'127.0.0.1:{port} 不可连接，请停止后重新开始监听。')

    def _test_listen_loopback(self):
        """本机最小测试：仅 127.0.0.1，不访问公网/内网业务系统。

        完全离线可用：注入内存探测记录 + 对本机代理端口发本地探测。
        """
        zh = self.language == 'zh'
        if not self._listening or not self._channel_ready:
            show_warning(
                self, '测试监听' if zh else 'Test',
                '请先开始监听并等待通道就绪。' if zh else 'Start listening first.',
            )
            return
        port = self._current_port()
        import socket
        import threading

        def _probe():
            err = ''
            try:
                # 仅探测本机代理端口是否可连（不发起外网 HTTP）
                with socket.create_connection(('127.0.0.1', int(port)), timeout=1.0):
                    pass
            except Exception as exc:
                err = str(exc)
            QTimer.singleShot(0, lambda: self._on_probe_done(err))

        # 同步注入内存探测记录，保证列表至少有一条（离线可见）
        self._ingest_record({
            'id': f'probe-{int(time.time() * 1000)}',
            'method': 'GET',
            'url': 'http://127.0.0.1/pengtools-listen-probe',
            'path': '/pengtools-listen-probe',
            'status': 200,
            'resource_type': 'XHR',
            'mime_type': 'text/plain',
            'source': 'local_proxy' if self._mode != 'chromium' else 'cdp',
            'started_at': time.time(),
            'duration_ms': 1,
            'failure': '',
            'request_headers': {'User-Agent': 'PengTools-Listen-Probe-Offline'},
            'response_body': 'offline-probe-ok',
            'response_headers': {'Content-Type': 'text/plain'},
        })
        threading.Thread(target=_probe, daemon=True).start()
        show_info(
            self, '测试监听' if zh else 'Test',
            (
                '已注入本机离线探测记录（仅 127.0.0.1，不访问外网/业务系统）。\n'
                '列表应出现探测请求；再用浏览器访问内网业务页可继续抓真实接口。'
                if zh else
                'Offline loopback probe only — no internet or business host.'
            ),
        )

    def _on_probe_done(self, err: str):
        if err and self._listening:
            # 连接被拒绝等属于探测目标端口（:9）预期失败，不算监听失败
            self.live_status.setText(
                (self.live_status.text() or '') + ' · 探测完成'
            )

    def _on_capture_record(self, epoch, rec):
        # 旧 worker 回调晚到：忽略，不得污染新一轮
        if int(epoch) != self._lifecycle.epoch:
            return
        self._ingest_record(dict(rec or {}))

    def _on_capture_error(self, epoch, msg):
        # 旧 epoch 错误：忽略
        if int(epoch) != self._lifecycle.epoch:
            return
        # 当前 worker 错误：lifecycle 同步 IDLE + 清理 + 恢复安全代理
        if self._lifecycle.fail_runtime(int(epoch)):
            self._ie_worker = None
            self._listening = False
            self._channel_ready = False
            self._set_listening_ui(False)
            self._wait_hint_timer.stop()
            self._status_tick.stop()
            try:
                from tools.ie_proxy import restore_proxy_from_snapshot, mark_capture_proxy_inactive, ensure_system_proxy_safe
                restore_proxy_from_snapshot()
                mark_capture_proxy_inactive()
                ensure_system_proxy_safe(reason='capture_error')
            except Exception:
                pass
            self.status_label.setText(f'代理错误：{msg} · 已恢复系统代理安全状态')
            show_warning(self, '本机代理', msg)

    def _on_capture_stopped(self, epoch):
        # 旧 epoch stopped：直接忽略
        if int(epoch) != self._lifecycle.epoch:
            return
        # 当前 epoch worker 意外退出：lifecycle 同步 IDLE
        if self._lifecycle.fail_runtime(int(epoch)):
            self._ie_worker = None
        self._listening = False
        self._channel_ready = False
        self._set_listening_ui(False)
        self._wait_hint_timer.stop()
        self._status_tick.stop()
        try:
            from tools.ie_proxy import ensure_system_proxy_safe
            ensure_system_proxy_safe(reason='capture_stopped')
        except Exception:
            pass

    def _on_capture_stop_finalized(self, stop_epoch, should_restart):
        """stop 线程收尾完成（Qt 主线程）：过期 finalized 直接丢弃；pending 时自动重启。

        旧 stop 的 finalized 晚到时，lifecycle.epoch 已前进——不得回写
        _capture_epoch / _capture_stop_thread，不得触发重启、不得动 worker/UI。
        """
        if int(stop_epoch) != self._lifecycle.epoch:
            return
        self._capture_stop_thread = None
        self._capture_epoch = int(stop_epoch)
        if should_restart:
            self._start_local_proxy()
    def _on_ie_error(self, msg):
        # 已切换到新 worker / 启动中时，忽略旧错误
        if getattr(self, '_capture_boot_worker', None) is not None:
            return
        self.status_label.setText(f'代理错误：{msg}')
        show_warning(self, '本机代理', msg)
        self._listening = False
        self._channel_ready = False
        self._set_listening_ui(False)
        self._wait_hint_timer.stop()
        self._status_tick.stop()
        if self._ie_worker:
            worker = self._ie_worker
            self._ie_worker = None
            self._detach_capture_worker(worker)
            try:
                worker.stop(join_timeout=0.8)
            except Exception:
                pass

    @staticmethod
    def _clip_body(value):
        """截断过大 body，降低内存峰值（仅内存会话，不落盘）。"""
        if value is None:
            return value
        if not isinstance(value, str):
            try:
                value = str(value)
            except Exception:
                return value
        if len(value) <= MAX_BODY_CHARS:
            return value
        return value[:MAX_BODY_CHARS] + f'\n…[truncated {len(value) - MAX_BODY_CHARS} chars]'

    def _evict_old_records_if_needed(self):
        """超限时按 seq 淘汰最旧，保持内存可预期。"""
        overflow = len(self._records_by_id) - MAX_SESSION_RECORDS
        if overflow <= 0:
            return 0
        ordered = sorted(
            self._records_by_id.items(),
            key=lambda kv: (kv[1].get('seq') is None, kv[1].get('seq') or 0, str(kv[0])),
        )
        removed = 0
        for rid, _rec in ordered:
            if removed >= overflow:
                break
            if rid == self._selected_id:
                continue
            self._records_by_id.pop(rid, None)
            removed += 1
        # 若仍超限（例如全部被当前选中占住），再强制丢最旧
        while len(self._records_by_id) > MAX_SESSION_RECORDS:
            rid = next(iter(sorted(
                self._records_by_id.keys(),
                key=lambda k: (self._records_by_id[k].get('seq') is None,
                               self._records_by_id[k].get('seq') or 0),
            )), None)
            if rid is None:
                break
            self._records_by_id.pop(rid, None)
            if rid == self._selected_id:
                self._selected_id = None
            removed += 1
        return removed

    def _ingest_record(self, rec: dict):
        rid = rec.get('id') or ''
        if not rid:
            return
        # 合并同 id（request→response）— 仅更新内存，UI 合并刷新
        old = self._records_by_id.get(rid) or {}
        merged = dict(old)
        merged.update({k: v for k, v in rec.items() if v is not None and v != ''})
        # status 允许 0；failure 允许覆盖
        if 'status' in rec:
            merged['status'] = rec.get('status')
        if rec.get('failure'):
            merged['failure'] = rec.get('failure')
        if rec.get('response_body') is not None:
            merged['response_body'] = self._clip_body(rec.get('response_body'))
        if rec.get('request_body') is not None and rec.get('request_body') != '':
            merged['request_body'] = self._clip_body(rec.get('request_body'))
        # Fiddler 式会话序号：首次入库编号
        if 'seq' not in old:
            merged['seq'] = len(self._records_by_id) + 1
        else:
            merged['seq'] = old.get('seq')
        self._records_by_id[rid] = merged
        if self._evict_old_records_if_needed():
            # 淘汰后同步 list，避免 _records 残留已删 id
            self._records = list(self._records_by_id.values())
        self._last_request_at = time.time()
        self._ingest_dirty = True
        self._ingest_count_since_flush += 1
        # 首屏立刻刷；高峰合并刷新，避免主线程被打满
        if len(self._records_by_id) <= 3 or self._ingest_count_since_flush >= 80:
            self._ingest_flush_timer.stop()
            self._flush_ingest_ui()
        elif not self._ingest_flush_timer.isActive():
            self._ingest_flush_timer.start()

    def _flush_ingest_ui(self):
        if not self._ingest_dirty and self._ingest_count_since_flush == 0:
            return
        prev_selected = self._selected_id
        self._records = list(self._records_by_id.values())
        self._ingest_dirty = False
        self._ingest_count_since_flush = 0
        self._rebuild_table()
        if prev_selected and prev_selected in self._records_by_id:
            self._selected_id = prev_selected
        self._refresh_live_status()
        if self._records and hasattr(self, 'recheck_btn') and self.recheck_btn.isVisible():
            self.recheck_btn.hide()

    def _stop_listen(self):
        if not self._lifecycle.begin_stop(self._capture_epoch):
            return  # IDLE/STOPPING 重复点击：不重复清理
        self.loading.start_busy('正在停止监听…')
        self._wait_hint_timer.stop()
        self._status_tick.stop()
        # boot 结果失效由 lifecycle epoch 权威判定（begin_stop 已进入 STOPPING）
        # 先立刻恢复系统代理（网络马上可用），引擎在后台收尾
        try:
            from tools.ie_proxy import restore_proxy_from_snapshot, ensure_system_proxy_safe, mark_capture_proxy_inactive
            restore_proxy_from_snapshot()
            mark_capture_proxy_inactive()
            ensure_system_proxy_safe(reason='stop_listen_pre')
        except Exception:
            pass
        worker = self._ie_worker
        cdp = self._cdp_session
        self._ie_worker = None
        self._cdp_session = None
        self._listening = False
        self._channel_ready = False
        self._set_listening_ui(False)
        # 先切断回调，避免异步 stop 晚到把下一轮监听状态清掉
        self._detach_capture_worker(worker)

        def _shutdown():
            if cdp is not None:
                try:
                    cdp.stop()
                except Exception:
                    pass
            if worker is not None:
                try:
                    worker.stop(join_timeout=2.0, clear_records=False)
                except TypeError:
                    try:
                        worker.stop()
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                from tools.ie_proxy import ensure_system_proxy_safe
                ensure_system_proxy_safe(reason='stop_listen')
            except Exception:
                pass

        import threading
        stop_epoch = self._capture_epoch

        def _stop_worker_thread():
            try:
                _shutdown()
            finally:
                # 生命周期必须完成（_shutdown 异常也不能卡 STOPPING）；
                # 跨线程只能用 Qt signal 投递主线程，禁止 QTimer.singleShot。
                should_restart = self._lifecycle.finish_stop(stop_epoch)
                try:
                    self._sig_capture_stop_finalized.emit(int(stop_epoch), bool(should_restart))
                except Exception:
                    pass

        stop_thread = threading.Thread(target=_stop_worker_thread, name='capture-stop', daemon=True)
        self._capture_stop_thread = stop_thread
        stop_thread.start()
        n = len(self._records)
        self.loading.finish('已停止')
        self.status_label.setText(
            f'已停止监听 · 系统代理已恢复 · 会话保留 {n} 条（可继续导出/请求测试）'
        )
        self.live_status.setText('')
        self._refresh_listen_status_pill()
        if hasattr(self, 'recheck_btn'):
            self.recheck_btn.hide()

    def on_panel_deactivated(self):
        """离开接口排查页：暂停系统代理，其它模块/软件不再被拖超时。引擎可仍运行。"""
        if hasattr(self, 'loading') and self.loading is not None:
            self.loading.hide_now()
        if not self._listening:
            return
        try:
            from tools.ie_proxy import suspend_capture_system_proxy, is_capture_proxy_suspended
            result = suspend_capture_system_proxy()
            if result == 'suspended' or is_capture_proxy_suspended():
                port = self._current_port()
                self.status_label.setText(
                    f'抓包引擎运行中 · 系统代理已暂停（其它软件可正常上网）· '
                    f'回到本页将自动恢复 127.0.0.1:{port}'
                )
                if hasattr(self, 'live_status'):
                    self.live_status.setText('系统代理已暂停')
        except Exception:
            pass

    def on_panel_activated(self):
        """回到接口排查页：若仍在抓包，恢复系统代理以便浏览器继续进流量。"""
        if not self._listening:
            return
        try:
            from tools.ie_proxy import resume_capture_system_proxy, is_capture_proxy_suspended
            from tools.capture_lifecycle import resolve_resume_action
            if not is_capture_proxy_suspended() and self._ie_worker is None:
                return
            port = self._current_port()
            import socket
            port_open = False
            try:
                probe = socket.create_connection(('127.0.0.1', port), timeout=0.3)
                probe.close()
                port_open = True
            except OSError:
                port_open = False
            worker = self._ie_worker
            action = resolve_resume_action(worker is not None, port_open)
            if action == 'resume':
                result = resume_capture_system_proxy(port)
                if result in ('resumed', 'noop'):
                    self.status_label.setText(
                        f'抓包中 · 系统代理 127.0.0.1:{port} · 请用浏览器访问业务页 · '
                        f'离开本页会自动暂停系统代理'
                    )
                    self._refresh_live_status()
            else:
                # worker 已死亡或端口未监听：绝不 resume 到死端口
                # lifecycle 同步 IDLE（否则用户再点开始会被 RUNNING 拒绝）
                self._lifecycle.fail_runtime(self._capture_epoch)
                from tools.ie_proxy import restore_proxy_from_snapshot, mark_capture_proxy_inactive
                restore_proxy_from_snapshot()
                mark_capture_proxy_inactive()
                self._listening = False
                self._channel_ready = False
                self._ie_worker = None
                self._set_listening_ui(False)
                self.status_label.setText(
                    '抓包引擎已退出，系统代理已恢复原设置。请重新点击开始监听。')
        except Exception:
            pass

    def _set_listening_ui(self, active: bool):
        self._refresh_capture_action()
        self._refresh_capture_status_text()
        self._rebuild_capture_actions_menu()
        if hasattr(self, 'launch_btn') and self.launch_btn is not None:
            self.launch_btn.setEnabled(False)
            self.launch_btn.hide()
        if hasattr(self, 'mode_combo') and self.mode_combo is not None:
            self.mode_combo.hide()
        if not active and hasattr(self, 'recheck_btn'):
            self.recheck_btn.hide()

    def _update_empty_hint(self):
        zh = self.language == 'zh'
        if self._listening and self._channel_ready and not self._records:
            self.empty_hint.setText(
                '抓包中，等待请求…\n请用浏览器打开业务页面并操作，列表会显示 method / URL / 状态。'
                if zh else
                'Capturing — open your browser and use the app; URL list will fill in.'
            )
        else:
            self.empty_hint.setText(
                '点「开始监听」→ 完全退出并重新打开 Chrome/Edge → 再访问业务页。\n'
                '列表会显示 # / 结果 / 协议 / 方法 / 主机 / URL。'
                if zh else
                'Start listen → fully restart Chrome/Edge → open your app pages.'
            )

    def _confirm_clear_session(self):
        zh = self.language == 'zh'
        if not self._records:
            self.clear_session()
            return
        if not confirm_action(
            self,
            '清空会话列表' if zh else 'Clear session',
            '将清空内存中的全部捕获请求（不可恢复）。' if zh else 'Clear all in-memory captures.',
            confirm_text='清空' if zh else 'Clear',
            danger=True,
        ):
            return
        self.clear_session()

    def clear_session(self):
        self._records.clear()
        self._records_by_id.clear()
        self._filtered = []
        self._selected_id = None
        self._reveal_sensitive = False
        self._sensitive_copy_warned = False
        if self.reveal_cb.isChecked():
            self.reveal_cb.blockSignals(True)
            self.reveal_cb.setChecked(False)
            self.reveal_cb.blockSignals(False)
        if self._cdp_session:
            try:
                self._cdp_session.clear_session()
            except Exception:
                pass
        if self._ie_worker:
            try:
                self._ie_worker.clear_session()
            except Exception:
                pass
        self.table.setRowCount(0)
        self.overview_edit.clear()
        self.req_detail.clear()
        self.resp_detail.clear()
        self.draft_preview.clear()
        self.session_count.setText('0 / 0')
        self.empty_hint.setVisible(True)

    # ── HTTPS 抓包证书 ──────────────────────────────────
    def _install_ie_cert(self):
        zh = self.language == 'zh'
        from tools.ie_proxy import is_recorded_root_cert_installed, install_user_root_cert
        if is_recorded_root_cert_installed():
            show_info(
                self,
                'HTTPS 抓包证书' if zh else 'HTTPS Certificate',
                'HTTPS 抓包证书已安装，无需重复安装。' if zh else 'HTTPS capture certificate is already installed.',
            )
            return
        from ui.confirm_dialog import confirm_https_cert_consent
        if not confirm_https_cert_consent(self, language=self.language, for_listen=False):
            return
        self.loading.start_busy('正在安装证书…' if zh else 'Installing certificate…')
        try:
            thumb = install_user_root_cert()
            self._config = load_interface_debug_config()
            self._refresh_capture_status_text()
            self._rebuild_capture_actions_menu()
            self.loading.finish('证书已安装' if zh else 'Certificate installed')
            show_success(
                self,
                'HTTPS 抓包证书' if zh else 'HTTPS Certificate',
                f'已成功安装抓包根证书（指纹：{thumb[:16]}…）' if zh else f'CA certificate installed successfully ({thumb[:16]}…)',
            )
        except Exception as exc:
            self.loading.fail(str(exc))
            self._refresh_capture_status_text()
            self._rebuild_capture_actions_menu()
            show_warning(self, '安装证书失败' if zh else 'Install Failed', str(exc))

    def _remove_ie_cert(self):
        zh = self.language == 'zh'
        if self._listening or self._lifecycle.state in ('starting', 'running'):
            show_warning(
                self,
                '移除证书' if zh else 'Remove Certificate',
                '请先停止监听，再移除 HTTPS 抓包证书。' if zh else 'Please stop listening before removing HTTPS certificate.',
            )
            return
        from tools.ie_proxy import is_recorded_root_cert_installed, remove_recorded_cert
        if not is_recorded_root_cert_installed():
            remove_recorded_cert()
            self._config = load_interface_debug_config()
            self._refresh_capture_status_text()
            self._rebuild_capture_actions_menu()
            show_info(
                self,
                'HTTPS 抓包证书' if zh else 'HTTPS Certificate',
                '当前受信任根证书库中未检测到抓包证书。' if zh else 'No capture certificate found in Trusted Root store.',
            )
            return
        if not confirm_action(
            self,
            '移除本机抓包证书' if zh else 'Remove CA Certificate',
            '将从 Windows 当前用户受信任根证书库中移除 PengTools 抓包 CA 证书。\n移除后将无法解密查看 HTTPS 报文。'
            if zh else
            'Remove PengTools CA certificate from Windows Trusted Root store.',
            confirm_text='移除证书' if zh else 'Remove',
            danger=True,
        ):
            return
        self.loading.start_busy('正在移除证书…' if zh else 'Removing certificate…')
        try:
            remove_recorded_cert()
            self._config = load_interface_debug_config()
            self._refresh_capture_status_text()
            self._rebuild_capture_actions_menu()
            self.loading.finish('证书已移除' if zh else 'Certificate removed')
            show_success(
                self,
                'HTTPS 抓包证书' if zh else 'HTTPS Certificate',
                '已成功移除抓包根证书。' if zh else 'CA certificate removed successfully.',
            )
        except Exception as exc:
            self.loading.fail(str(exc))
            self._refresh_capture_status_text()
            self._rebuild_capture_actions_menu()
            show_warning(self, '移除证书失败' if zh else 'Remove Failed', str(exc))

    def _check_orphan_proxy_snapshot(self):
        """启动时自动清理残留系统代理（无需用户确认，避免接口全挂）。"""
        try:
            from tools.ie_proxy import ensure_system_proxy_safe
            result = ensure_system_proxy_safe(reason='panel_startup')
        except Exception:
            return
        if os.environ.get('QT_QPA_PLATFORM', '').lower() == 'offscreen':
            return
        zh = self.language == 'zh'
        if result == 'restored_snapshot':
            self.status_label.setText(
                '已自动恢复系统代理（上次抓包可能未正常停止）' if zh else
                'System proxy restored automatically'
            )
        elif result == 'disabled_orphan':
            self.status_label.setText(
                '已关闭残留的本机抓包代理（端口已无服务）' if zh else
                'Orphan local capture proxy disabled'
            )

    def _manual_restore_proxy(self):
        """用户一键恢复系统代理。"""
        zh = self.language == 'zh'
        if self._listening:
            show_warning(
                self, '代理' if zh else 'Proxy',
                '请先停止抓包，再恢复系统代理。' if zh else 'Stop capture first.',
            )
            return
        try:
            from tools.ie_proxy import ensure_system_proxy_safe, restore_proxy_from_snapshot, is_loopback_capture_proxy, read_proxy_settings
            result = ensure_system_proxy_safe(reason='manual')
            # 若仍指向本机，再强制关一次
            if is_loopback_capture_proxy(read_proxy_settings()):
                if not restore_proxy_from_snapshot():
                    from tools.ie_proxy import disable_orphan_loopback_proxy
                    disable_orphan_loopback_proxy()
                    result = 'disabled_orphan'
            labels = {
                'ok': ('当前系统代理正常，无需恢复', 'Proxy looks fine'),
                'restored_snapshot': ('已恢复抓包前的系统代理', 'Restored previous proxy'),
                'disabled_orphan': ('已关闭残留的本机抓包代理', 'Disabled orphan capture proxy'),
                'cleared_stale_snapshot': ('已清理过期快照，当前代理无需改动', 'Cleared stale snapshot'),
            }
            msg = labels.get(result, labels['ok'])
            show_success(self, '代理' if zh else 'Proxy', msg[0 if zh else 1])
            self.status_label.setText(msg[0 if zh else 1])
        except Exception as exc:
            show_warning(self, '代理' if zh else 'Proxy', str(exc))

    # ── 表格 ─────────────────────────────────────────
    def refresh_theme(self):
        """以当前主题 Token 重建会话状态色，不改变抓包数据或选择状态。"""
        self._rebuild_table()

    def _rebuild_table(self):
        prev_id = self._selected_id
        at_top = self.table.rowCount() == 0 or (
            self.table.currentRow() <= 0 and self._follow_latest
        )
        query = self.filter_edit.text()
        self._filtered = filter_and_sort(
            self._records,
            query=query,
            filters=self._active_filters,
            sort_key=self._sort_key,
            sort_desc=self._sort_desc,
            show_static=self._show_static,
        )
        total = len(self._records)
        shown = len(self._filtered)
        self.session_count.setText(f'{shown} / {total}')
        self._refresh_listen_status_pill()
        self.empty_hint.setVisible(shown == 0)
        self.table.setRowCount(shown)
        labels = self.COL_LABELS_ZH if self.language == 'zh' else self.COL_LABELS_EN
        self.table.setHorizontalHeaderLabels([labels[k] for k in COLUMN_KEYS])

        warn = _theme_color('WARNING', '#C9A56A')
        danger = _theme_color('DANGER', '#C78A8A')
        success = _theme_color('SUCCESS', '#7BA88A')
        muted = _theme_color('TEXT_MUTED', '#BAC5BD')
        primary = _theme_color('PRIMARY', '#9ABAA6')

        for i, rec in enumerate(self._filtered):
            status = rec.get('status')
            status_s = '—' if status is None else str(status)
            if rec.get('failure'):
                status_s = f'● {status_s}' if status is not None else '● ERR'
            elif status is not None:
                try:
                    code = int(status)
                    if code >= 400:
                        status_s = f'● {code}'
                    elif 200 <= code < 300:
                        status_s = f'● {code}'
                    else:
                        status_s = f'○ {code}'
                except (TypeError, ValueError):
                    status_s = f'○ {status}'
            method = (rec.get('method') or 'GET').upper()
            proto = protocol_of(rec)
            host = host_of(rec)
            name_col = name_of(rec)
            # 主行放路径，次行放主机、资源类型和大小，形成可扫读的两行诊断摘要。
            url_col = f'{url_path_display(rec)}\n{host or "—"} · {content_kind(rec)} · {format_size(response_size_bytes(rec))}'
            dur = rec.get('duration_ms')
            dur_s = '—' if dur is None else f'{int(dur)} ms'
            kind = content_kind(rec)
            ts = rec.get('started_at') or time.time()
            tstr = datetime.fromtimestamp(ts).strftime('%H:%M:%S.%f')[:-3]
            size_s = format_size(response_size_bytes(rec))
            seq_s = str(rec.get('seq') or (i + 1))
            # 列顺序与 COLUMN_KEYS 一致
            cell_map = {
                'seq': seq_s,
                'status': status_s,
                'protocol': proto,
                'method': method,
                'name': name_col,
                'host': host or '—',
                'url': url_col,
                'body': size_s,
                'type': kind,
                'duration': dur_s,
                'time': tstr,
            }
            vals = [cell_map.get(k, '—') for k in COLUMN_KEYS]
            for c, v in enumerate(vals):
                key = COLUMN_KEYS[c]
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, rec.get('id'))
                if key == 'status':
                    if is_failed(rec):
                        item.setForeground(QBrush(danger))
                    elif status is not None:
                        try:
                            if 200 <= int(status) < 300:
                                item.setForeground(QBrush(success))
                        except (TypeError, ValueError):
                            pass
                if key == 'method':
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    item.setForeground(QBrush(primary))
                if key == 'protocol':
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    if proto == 'https':
                        item.setForeground(QBrush(success))
                    else:
                        item.setForeground(QBrush(muted))
                if key in ('url', 'host'):
                    item.setToolTip(mask_url_query(rec.get('url') or '', self._reveal_sensitive))
                if key == 'duration':
                    sev = duration_severity(dur)
                    if sev == 'danger':
                        item.setForeground(QBrush(danger))
                    elif sev == 'warn':
                        item.setForeground(QBrush(warn))
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if key in ('body', 'seq'):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if key == 'type':
                    item.setForeground(QBrush(muted))
                self.table.setItem(i, c, item)

        # 恢复选中 / 跟随最新
        if prev_id:
            for i in range(self.table.rowCount()):
                it = self.table.item(i, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == prev_id:
                    self.table.selectRow(i)
                    self._follow_latest = (i == 0)
                    break
            else:
                if at_top and shown:
                    self.table.selectRow(0)
                    self._follow_latest = True
        elif at_top and shown:
            self.table.selectRow(0)
            self._follow_latest = True

    def _on_row_selected(self):
        items = self.table.selectedItems()
        if not items:
            self._selected_id = None
            self._refresh_detail()
            return
        rid = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_id = rid
        row = self.table.currentRow()
        self._follow_latest = (row == 0)
        self._refresh_detail()
        # 在请求测试页时自动按选中会话填充（静默，不弹窗）
        if self.detail_tabs.currentWidget() is getattr(self, 'draft_page', None):
            try:
                self._rt_fill_from_selection(silent=True)
            except Exception:
                pass

    def _on_detail_tab_changed(self, index: int):
        try:
            if self.detail_tabs.widget(index) is getattr(self, 'draft_page', None):
                self._rt_fill_from_selection(silent=True)
        except Exception:
            pass

    def _selected_record(self) -> dict | None:
        if not self._selected_id:
            return None
        return self._records_by_id.get(self._selected_id)

    def _table_context_menu(self, pos):
        rec = self._selected_record()
        if not rec:
            return
        menu = QMenu(self)
        act_copy_url = menu.addAction('复制 URL')
        act_copy_path = menu.addAction('复制路径')
        act_format = menu.addAction('送格式工具')
        act_gw = menu.addAction('送入加解密')
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act_copy_url:
            self._copy_text(mask_url_query(rec.get('url') or '', True), sensitive=True)
        elif chosen == act_copy_path:
            self._copy_text(host_path_display(rec), sensitive=False)
        elif chosen == act_format:
            self._send_body_side('response', 'format')
        elif chosen == act_gw:
            self._send_body_side('response', 'gateway')

    # ── 详情 ─────────────────────────────────────────
    def _format_headers(self, headers: dict) -> str:
        lines = []
        for k, v in (headers or {}).items():
            lines.append(f'{k}: {mask_sensitive_value(k, v, self._reveal_sensitive)}')
        return '\n'.join(lines) if lines else '（无）'

    def _refresh_detail(self):
        rec = self._selected_record()
        if not rec:
            self.overview_edit.clear()
            self.req_detail.clear()
            self.resp_detail.clear()
            self.detail_summary.clear()
            self.detail_summary.hide()
            return
        url = mask_url_query(rec.get('url') or '', self._reveal_sensitive)
        summary_url = mask_url_query(rec.get('url') or '', False)
        status = rec.get('status')
        dur = rec.get('duration_ms')
        size = format_size(response_size_bytes(rec))
        kind = content_kind(rec)
        src = self._source_label(rec.get('source'))
        ts = rec.get('started_at') or time.time()
        tstr = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        notes = []
        if rec.get('failure'):
            notes.append(f'失败原因：{rec.get("failure")}')
        if is_failed(rec):
            notes.append('HTTP 失败（4xx/5xx）或加载失败')
        if duration_severity(dur) != 'normal':
            notes.append(f'慢请求：{dur} ms')
        rtype = (rec.get('resource_type') or '').lower()
        if rtype == 'websocket':
            notes.append('WebSocket 会话（以状态说明展示，报文可能不完整）')
        if not (rec.get('response_body') or '').strip() and status:
            notes.append('响应体为空或未能读取（仅保留元信息，不视为程序异常）')
        environment_id = self.local_target_combo.currentData()
        environment_item = next(
            (item for item in (self._config.get('local_targets') or []) if item.get('id') == environment_id),
            None,
        )
        environment = (environment_item or {}).get('name') or '未选择'
        summary_parts = [
            (rec.get('method') or 'GET').upper(),
            summary_url,
            f'状态 {status if status is not None else "—"}',
            f'耗时 {dur if dur is not None else "—"} ms',
            f'大小 {size}',
            f'时间 {tstr}',
            f'环境 {environment}',
        ]
        self.detail_summary.setText(' · '.join(summary_parts))
        summary_status = 'danger' if is_failed(rec) else 'info'
        try:
            if status is not None and 200 <= int(status) < 300:
                summary_status = 'success'
        except (TypeError, ValueError):
            pass
        self.detail_summary.setProperty('status', summary_status)
        style = self.detail_summary.style()
        if style is not None:
            style.unpolish(self.detail_summary)
            style.polish(self.detail_summary)
        self.detail_summary.show()
        overview = [
            f'URL：{url}',
            f'方法：{(rec.get("method") or "GET").upper()}',
            f'状态：{status if status is not None else "—"}',
            f'耗时：{dur if dur is not None else "—"} ms',
            f'类型：{kind}',
            f'资源类型：{rec.get("resource_type") or "—"}',
            f'来源：{src}',
            f'开始：{tstr}',
            f'响应大小：{size}',
            f'MIME：{rec.get("mime_type") or "—"}',
        ]
        if notes:
            overview.append('')
            overview.append('—— 说明 ——')
            overview.extend(notes)
        self.overview_edit.setPlainText('\n'.join(overview))

        # 请求
        pairs = query_pairs(rec.get('url') or rec.get('query') or '')
        q_lines = [f'{k}={v if self._reveal_sensitive or "token" not in k.lower() else "********"}' for k, v in pairs] or ['（无）']
        headers = rec.get('request_headers') or {}
        cookie_raw = ''
        for k, v in headers.items():
            if str(k).lower() == 'cookie':
                cookie_raw = v
                break
        cookie_lines = []
        for k, v in split_cookies(cookie_raw):
            cookie_lines.append(f'{k}={v if self._reveal_sensitive else "********"}')
        if not cookie_lines:
            cookie_lines = ['（无）']
        body = rec.get('request_body') or ''
        kind_b, pretty, err = pretty_body(body)
        body_block = pretty if pretty else '（无）'
        if err:
            body_block = f'{body}\n\n# {err}'
        req_text = [
            f'{(rec.get("method") or "GET").upper()} {url}',
            '',
            '—— Query ——',
            *q_lines,
            '',
            '—— Headers ——',
            self._format_headers(headers),
            '',
            '—— Cookie ——',
            *cookie_lines,
            '',
            f'—— Body ({kind_b}) ——',
            body_block,
        ]
        self.req_detail.setPlainText('\n'.join(req_text))

        # 响应
        rbody = rec.get('response_body') or ''
        rkind, rpretty, rerr = pretty_body(rbody)
        rblock = rpretty if rpretty else '（无）'
        if rerr:
            rblock = f'{rbody}\n\n# {rerr}'
        resp_text = [
            f'Status: {status if status is not None else "—"}',
            f'MIME: {rec.get("mime_type") or "—"}',
            f'Duration: {dur if dur is not None else "—"} ms',
            f'Size: {size}',
        ]
        if rec.get('failure'):
            resp_text.append(f'Failure: {rec.get("failure")}')
        resp_text += [
            '',
            '—— Headers ——',
            self._format_headers(rec.get('response_headers') or {}),
            '',
            f'—— Body ({rkind}) ——',
            rblock,
        ]
        self.resp_detail.setPlainText('\n'.join(resp_text))

        has_req = bool((body or '').strip())
        has_resp = bool((rbody or '').strip())
        self.format_req_btn.setEnabled(has_req and (_looks_json(body) or _looks_xml(body)))
        self.gateway_req_btn.setEnabled(has_req and (
            _looks_base64ish(body) or not (_looks_json(body) or _looks_xml(body))
        ))
        self.format_resp_btn.setEnabled(has_resp and (_looks_json(rbody) or _looks_xml(rbody)))
        self.gateway_resp_btn.setEnabled(has_resp and (
            _looks_base64ish(rbody) or not (_looks_json(rbody) or _looks_xml(rbody))
        ))

    def _copy_safe_url(self):
        rec = self._selected_record()
        if not rec:
            return
        self._copy_text(mask_url_query(rec.get('url') or '', False), sensitive=False)

    def _copy_text(self, text: str, *, sensitive: bool = False):
        if not text:
            return
        if sensitive and not self._reveal_sensitive:
            zh = self.language == 'zh'
            if not self._sensitive_copy_warned:
                if not confirm_action(
                    self,
                    '复制可能含敏感信息' if zh else 'Sensitive copy',
                    (
                        '内容可能包含 Authorization、Cookie、Token。仅应粘贴到本机可信工具。'
                        if zh else
                        'Content may include secrets. Paste only into trusted local tools.'
                    ),
                    confirm_text='继续复制' if zh else 'Copy',
                    danger=False,
                ):
                    return
                self._sensitive_copy_warned = True
        QApplication.clipboard().setText(text)

    def _send_body_side(self, side: str, target: str):
        rec = self._selected_record()
        if not rec:
            return
        body = rec.get('request_body') if side == 'request' else rec.get('response_body')
        body = body or ''
        if not body.strip():
            body = rec.get('response_body') or rec.get('request_body') or ''
        if not body.strip():
            return
        if target == 'gateway':
            from tools.iface_request_test import extract_sm4_key_cipher
            key = extract_sm4_key_cipher(rec, side=side)
            self.open_gateway.emit({'cipher': body, 'key': key, 'sm4_key_cipher': key})
            return
        # format：尽量送解密后明文
        from tools.iface_request_test import extract_sm4_key_cipher, try_decrypt_body
        key = extract_sm4_key_cipher(rec, side=side)
        plain, ok = try_decrypt_body(body, key, preferred_side=side)
        body = plain if ok else body
        if _looks_xml(body):
            self.open_format_xml.emit(body)
        else:
            kind, pretty, _err = pretty_body(body)
            self.open_format_json.emit(pretty if kind == 'json' else body)

    # ── 请求测试 / 导出导入 ─────────────────────────────
    def _selected_base_url(self) -> str:
        if hasattr(self, 'rt_base_edit'):
            return (self.rt_base_edit.text() or '').strip() or 'http://localhost:18031'
        return 'http://localhost:18031'

    def _export_session_detail(self):
        """导出选中或当前列表会话：URL + 请求/响应明文（能解则解，解不了则原文）。"""
        from tools.iface_request_test import build_export_document, export_document_to_text
        one = self._selected_record()
        if one:
            recs = [one]
        else:
            recs = list(self._filtered or self._records or [])
        if not recs:
            show_warning(self, '导出', '没有可导出的会话')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, '导出会话明细', 'pengtools_iface_session.json', 'JSON (*.json)',
        )
        if not path:
            return
        try:
            doc = build_export_document(recs)
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(export_document_to_text(doc))
            tip = '（当前选中）' if one else '（当前列表）'
            show_success(self, '导出', f'已导出 {len(doc.get("items") or [])} 条{tip}：{path}')
        except Exception as exc:
            show_warning(self, '导出', str(exc))

    def _rt_fill_from_selection(self, silent: bool = False):
        rec = self._selected_record()
        if not rec:
            if not silent:
                show_warning(self, '请求测试', '请先在左侧列表选择一条会话')
            return
        from tools.iface_request_test import fill_request_form_from_item, plaintext_bodies, strip_url_prefixes
        base = self._selected_base_url()
        item = plaintext_bodies(rec)
        form = fill_request_form_from_item(item, base)
        # 剥离网关前缀（如 /prpcar-api/car）
        prefixes = self._config.get('url_filter_prefixes') or []
        if prefixes and form.get('url'):
            form['url'] = strip_url_prefixes(form['url'], prefixes)
        self._rt_apply_form(form)
        if not silent:
            self.detail_tabs.setCurrentWidget(self.draft_page)
            show_info(self, '请求测试', '已按抓包会话填充（Body 优先解密明文）')

    def _rt_apply_form(self, form: dict):
        if not form:
            return
        base = form.get('base_host') or 'http://localhost:18031'
        self.rt_base_edit.setText(base)
        method = (form.get('method') or 'GET').upper()
        idx = self.rt_method.findText(method)
        self.rt_method.setCurrentIndex(max(0, idx))
        self.rt_url.setText(form.get('url') or '')
        self.rt_headers.setPlainText(form.get('headers_text') or 'Content-Type: application/json')
        self.rt_params.setPlainText(form.get('params_text') or '')
        body = str(form.get('body') or '')
        self.rt_body.setPlainText(body if body.strip() else '{\n  \n}')
        self._rt_last_request_body = body
        if form.get('category_id'):
            self._rt_select_category(form.get('category_id'))
        sample = form.get('response_body_sample') or ''
        if sample:
            # 完整展示，不截断
            self._rt_set_response_view(
                body=sample,
                meta='原抓包响应参考（完整）',
                headers=None,
            )
        else:
            self._rt_set_response_view(body='', meta='', headers=None)

    def _rt_body_payload(self) -> str:
        raw = self.rt_body.toPlainText() if hasattr(self, 'rt_body') else ''
        stripped = (raw or '').strip()
        if stripped in ('{', '{ }', '{\n}', '{\n  \n}'):
            return '{}'
        return raw or ''

    def _rt_set_response_view(self, body: str, meta: str = '', headers: dict | None = None):
        """写入响应预览：元信息 + 完整 Body（pretty 失败则原文）。"""
        raw = body if body is not None else ''
        self._rt_last_response_body = raw
        self._rt_last_response_headers = dict(headers or {})
        if hasattr(self, 'rt_resp_meta'):
            self.rt_resp_meta.setText(meta or '')
        if not raw.strip():
            self.draft_preview.clear()
            return
        kind, pretty, err = pretty_body(raw)
        display = pretty if pretty else raw
        # 头部信息放在 meta 标签；正文区只放完整 body，避免「被摘要挤掉」的感觉
        parts = []
        if err:
            parts.append(f'# {err}')
            parts.append('')
        parts.append(display)
        self.draft_preview.setPlainText('\n'.join(parts))
        # 滚到开头，方便通读
        cursor = self.draft_preview.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.draft_preview.setTextCursor(cursor)

    def _rt_current_request_body(self) -> str:
        return (self.rt_body.toPlainText() if hasattr(self, 'rt_body') else '') or self._rt_last_request_body or ''

    def _rt_current_response_body(self) -> str:
        body = self._rt_last_response_body or ''
        if str(body).strip():
            return body
        # 兼容：从预览区取「Body」段，去掉 Headers 与 # 注释
        preview = self.draft_preview.toPlainText() if hasattr(self, 'draft_preview') else ''
        if '—— Response Body' in preview:
            body = preview.split('—— Response Body', 1)[-1]
            # 去掉标题行
            lines = body.splitlines()
            if lines and lines[0].startswith('（') or (lines and '——' in lines[0]):
                lines = lines[1:]
            body = '\n'.join(ln for ln in lines if not ln.startswith('#')).strip()
            return body
        lines = [ln for ln in preview.splitlines() if not ln.startswith('#') and not ln.startswith('——')]
        return '\n'.join(lines).strip()

    def _rt_copy_request_body(self):
        body = self._rt_current_request_body()
        if not str(body).strip():
            show_warning(self, '请求测试', '当前请求 Body 为空')
            return
        self._copy_text(body, sensitive=True)
        show_success(self, '请求测试', f'已复制请求 Body（{len(body)} 字符）')

    def _rt_copy_response_body(self):
        body = self._rt_current_response_body()
        if not str(body).strip():
            show_warning(self, '请求测试', '当前没有响应 Body，请先发送请求')
            return
        self._copy_text(body, sensitive=True)
        show_success(self, '请求测试', f'已复制响应 Body（{len(body)} 字符）')

    def _rt_send_request_to_format(self):
        """当前请求 Body → 格式工具。"""
        body = self._rt_current_request_body()
        if not str(body).strip():
            show_warning(self, '请求测试', '当前请求 Body 为空')
            return
        self._rt_open_format(body)

    def _rt_send_response_to_format(self):
        """最近响应 Body → 格式工具。"""
        body = self._rt_current_response_body()
        if not str(body).strip():
            show_warning(self, '请求测试', '当前没有响应 Body，请先发送请求')
            return
        self._rt_open_format(body)

    def _rt_open_format(self, text: str):
        body = text or ''
        if _looks_xml(body):
            self.open_format_xml.emit(body)
            return
        kind, pretty, _err = pretty_body(body)
        self.open_format_json.emit(pretty if kind == 'json' else body)

    def _rt_security_settings(self) -> dict:
        """读取安测相关设置（失败时用收紧默认）。"""
        try:
            from config import load_settings
            return load_settings()
        except Exception:
            return {
                'security_ssl_verify': True,
                'security_confirm_remote_request': True,
                'security_prod_host_hints': ['prod', 'production', '生产', 'hxutf', 'prd'],
            }

    def _rt_sync_ssl_checkbox_from_settings(self):
        if not hasattr(self, 'rt_ssl_verify'):
            return
        settings = self._rt_security_settings()
        # 仅在用户尚未手动改过时跟随全局；首次/进入面板时对齐
        self.rt_ssl_verify.blockSignals(True)
        self.rt_ssl_verify.setChecked(bool(settings.get('security_ssl_verify', True)))
        self.rt_ssl_verify.blockSignals(False)

    def _rt_looks_like_prod(self, host: str, settings: dict) -> bool:
        host_l = (host or '').lower()
        hints = settings.get('security_prod_host_hints') or []
        for hint in hints:
            text = str(hint or '').strip().lower()
            if text and text in host_l:
                return True
        return False

    def _rt_confirm_remote_if_needed(self, url: str, method: str) -> bool:
        """非本机目标且开启确认时弹窗；返回 False 表示用户取消。"""
        from tools.iface_request_test import is_loopback_host
        from urllib.parse import urlparse
        from ui.confirm_dialog import confirm_action

        settings = self._rt_security_settings()
        if not bool(settings.get('security_confirm_remote_request', True)):
            return True
        parsed = urlparse(url or '')
        host = (parsed.hostname or '').strip()
        if is_loopback_host(host):
            return True
        zh = self.language == 'zh'
        prod = self._rt_looks_like_prod(host, settings)
        title = (
            ('生产环境请求确认' if prod else '远程请求确认')
            if zh else
            ('Confirm production request' if prod else 'Confirm remote request')
        )
        msg = (
            f'即将向非本机目标发送 HTTP 请求：\n\n{method} {url}\n\n'
            f'主机：{host}\n'
            + ('⚠ 主机名疑似生产环境，请确认已获授权。\n' if prod else '')
            + '仅在授权联调环境使用，勿对未授权系统压测或写操作。'
            if zh else
            f'About to send a non-loopback request:\n\n{method} {url}\n\n'
            f'Host: {host}\n'
            + ('⚠ Host looks like production — confirm authorization.\n' if prod else '')
            + 'Use only on authorized environments.'
        )
        return confirm_action(
            self,
            title,
            msg,
            confirm_text='确认发送' if zh else 'Send',
            danger=prod,
        )

    def _rt_send(self):
        from tools.iface_request_test import (
            RequestTestError, headers_dict_from_text, merge_url_with_params,
            normalize_base_host,
        )
        # 避免重复点击
        if getattr(self, '_rt_worker', None) is not None and self._rt_worker.isRunning():
            return
        try:
            base = normalize_base_host(self.rt_base_edit.text())
            self.rt_base_edit.setText(base)
            url = (self.rt_url.text() or '').strip()
            if not url:
                raise RequestTestError('请填写 URL')
            if '://' not in url:
                url = base.rstrip('/') + '/' + url.lstrip('/')
            url = merge_url_with_params(url, self.rt_params.toPlainText())
            method = self.rt_method.currentText() or 'GET'
            headers = headers_dict_from_text(self.rt_headers.toPlainText())
            body = self._rt_body_payload()
        except RequestTestError as exc:
            show_warning(self, '请求测试', str(exc))
            return
        except Exception as exc:
            show_warning(self, '请求测试', str(exc))
            return

        if body and not any(str(key).lower() == 'content-type' for key in headers):
            headers['Content-Type'] = 'application/json'
        if not self._rt_confirm_remote_if_needed(url, method):
            return

        verify_ssl = True
        if hasattr(self, 'rt_ssl_verify'):
            verify_ssl = self.rt_ssl_verify.isChecked()

        self._rt_send_meta = {
            'method': method,
            'url': url,
            'headers_text': self.rt_headers.toPlainText() or '',
            'params_text': self.rt_params.toPlainText() or '',
            'body': body,
            'base_host': self.rt_base_edit.text() if hasattr(self, 'rt_base_edit') else '',
            'category_id': self._rt_current_category_id(),
            'verify_ssl': verify_ssl,
        }
        self._rt_last_request_body = body
        self._rt_send_started_at = time.time()
        self.rt_send_btn.setEnabled(False)
        self.loading.start_busy('正在发送请求…')

        worker = _RequestTestWorker(
            method, url, headers, body, parent=self, verify_ssl=verify_ssl,
        )
        self._rt_worker = worker
        worker.finished_ok.connect(self._rt_send_finished)
        worker.failed.connect(self._rt_send_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _rt_send_finished(self, result: dict):
        meta = getattr(self, '_rt_send_meta', {}) or {}
        method = meta.get('method') or 'GET'
        url = meta.get('url') or ''
        try:
            self.loading.finish('请求完成')
            rbody = result.get('body') or ''
            headers = result.get('headers') or {}
            status = result.get('status')
            ok = result.get('ok')
            err = result.get('error') or ''
            # 元信息：状态 + 长度 + 头数量（完整 body 放预览区，不截断）
            meta_bits = [
                f'{method} {url}',
                f'Status: {status}',
                f'OK: {ok}',
                f'Body: {len(rbody)} 字符',
            ]
            if err:
                meta_bits.append(f'Error: {err}')
            if headers:
                # 头单独列几行在 meta，完整头可进预览前缀
                head_lines = [f'{k}: {v}' for k, v in headers.items()]
                meta_text = ' · '.join(meta_bits[:4])
                if err:
                    meta_text += f' · Error: {err}'
                # Body 区：可选 Headers 全文 + Body 全文
                body_parts = []
                body_parts.append('—— Response Headers ——')
                body_parts.extend(head_lines if head_lines else ['（无）'])
                body_parts.append('')
                body_parts.append('—— Response Body（完整）——')
                kind, pretty, perr = pretty_body(rbody)
                if perr:
                    body_parts.append(f'# {perr}')
                body_parts.append(pretty if pretty else rbody)
                self._rt_last_response_body = rbody
                self._rt_last_response_headers = dict(headers)
                if hasattr(self, 'rt_resp_meta'):
                    self.rt_resp_meta.setText(meta_text)
                self.draft_preview.setPlainText('\n'.join(body_parts))
                cursor = self.draft_preview.textCursor()
                cursor.movePosition(cursor.MoveOperation.Start)
                self.draft_preview.setTextCursor(cursor)
            else:
                self._rt_set_response_view(
                    body=rbody,
                    meta=' · '.join(meta_bits),
                    headers=headers,
                )
            self._rt_append_history_from_send(
                status=status, ok=ok, error=err, response_body=rbody,
            )
        finally:
            self._rt_refresh_send_label()
            self._rt_worker = None

    def _rt_send_failed(self, message: str):
        try:
            self.loading.fail(message or '请求失败')
            show_warning(self, '请求测试', message or '请求失败')
            self._rt_append_history_from_send(
                status=None, ok=False, error=message or '请求失败', response_body='',
            )
        finally:
            self._rt_refresh_send_label()
            self._rt_worker = None

    def _rt_import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '导入会话明细', '', 'JSON (*.json);;All (*.*)',
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as stream:
                text = stream.read()
            self._rt_import_text(text)
        except Exception as exc:
            show_warning(self, '导入', str(exc))

    def _rt_import_text(self, text: str):
        from tools.iface_request_test import (
            RequestTestError, fill_request_form_from_item, parse_import_document,
        )
        try:
            items = parse_import_document(text)
        except RequestTestError as exc:
            show_warning(self, '导入', str(exc))
            return
        item = items[0]
        base = self._selected_base_url()
        form = fill_request_form_from_item(item, base)
        self._rt_apply_form(form)
        self.detail_tabs.setCurrentWidget(self.draft_page)
        show_success(self, '导入', f'已加载 {len(items)} 条中的第 1 条到请求测试')

    def eventFilter(self, watched, event):
        # 请求测试页拖入 JSON
        if watched is getattr(self, 'draft_page', None):
            et = event.type()
            from PyQt6.QtCore import QEvent
            if et == QEvent.Type.DragEnter:
                md = event.mimeData()
                if md and md.hasUrls():
                    event.acceptProposedAction()
                    return True
            if et == QEvent.Type.Drop:
                md = event.mimeData()
                if md and md.hasUrls():
                    for url in md.urls():
                        path = url.toLocalFile()
                        if path and path.lower().endswith('.json'):
                            try:
                                with open(path, 'r', encoding='utf-8') as stream:
                                    self._rt_import_text(stream.read())
                            except Exception as exc:
                                show_warning(self, '导入', str(exc))
                            event.acceptProposedAction()
                            return True
        return super().eventFilter(watched, event)

    def _on_row_selected_hook_fill(self):
        pass

    def _refresh_draft_preview(self):
        # 兼容旧调用：改为从选中会话填充请求测试
        self._rt_fill_from_selection()

    def _copy_postman(self):
        show_info(self, '请求测试', '请使用「导出明细」或在请求测试中直接发送本机请求')

    def _export_postman(self):
        self._export_session_detail()

    def _copy_curl(self):
        from tools.iface_request_test import RequestTestError, plaintext_bodies, rewrite_url_with_base
        rec = self._selected_record()
        if not rec:
            show_warning(self, '请求测试', '请先选择会话')
            return
        try:
            base = self._selected_base_url()
            item = plaintext_bodies(rec)
            target = rewrite_url_with_base(item.get('url') or '', base)
            method = (item.get('method') or 'GET').upper()
            lines = [f'{method} {target}']
            for k, v in (item.get('request_headers') or {}).items():
                lines.append(f'{k}: {v}')
            body = item.get('request_body') or ''
            if body:
                lines.extend(['', body])
            QApplication.clipboard().setText('\n'.join(lines))
            show_success(self, '请求测试', '已复制（Body 优先解密明文）')
        except RequestTestError as exc:
            show_warning(self, '请求测试', str(exc))

    # ── 请求测试：接口库 / 历史 / 分类 ─────────────────
    def _rt_lib_reload(self, refresh_ui: bool = True):
        from tools.iface_request_library import load_library, normalize_library
        try:
            self._rt_lib = load_library()
        except Exception:
            self._rt_lib = normalize_library()
        if refresh_ui:
            self._rt_lib_fill_category_combos()
            self._rt_lib_sync_mode_combo()
            self._rt_lib_refresh_list()

    def _rt_lib_data(self) -> dict:
        if not isinstance(getattr(self, '_rt_lib', None), dict):
            self._rt_lib_reload(refresh_ui=False)
        return self._rt_lib or {}

    def _rt_current_category_id(self) -> str:
        from tools.iface_request_library import UNCATEGORIZED_ID
        if not hasattr(self, 'rt_category_combo'):
            return UNCATEGORIZED_ID
        data = self.rt_category_combo.currentData()
        return str(data or UNCATEGORIZED_ID)

    def _rt_select_category(self, category_id: str):
        from tools.iface_request_library import UNCATEGORIZED_ID
        if not hasattr(self, 'rt_category_combo'):
            return
        cid = category_id or UNCATEGORIZED_ID
        for i in range(self.rt_category_combo.count()):
            if self.rt_category_combo.itemData(i) == cid:
                self.rt_category_combo.setCurrentIndex(i)
                return
        # 找不到则保持

    def _rt_lib_fill_category_combos(self):
        from tools.iface_request_library import UNCATEGORIZED_ID
        lib = self._rt_lib_data()
        cats = lib.get('categories') or []
        last = lib.get('last_category_id') or UNCATEGORIZED_ID
        # 表单分类
        if hasattr(self, 'rt_category_combo'):
            self.rt_category_combo.blockSignals(True)
            self.rt_category_combo.clear()
            for c in cats:
                self.rt_category_combo.addItem(c.get('name') or '', c.get('id'))
            self._rt_select_category(last)
            self.rt_category_combo.blockSignals(False)
            size_pick_combo(self.rt_category_combo)
        # 列表筛选：全部 + 各分类
        if hasattr(self, 'rt_lib_cat_filter'):
            self.rt_lib_cat_filter.blockSignals(True)
            cur = self.rt_lib_cat_filter.currentData()
            self.rt_lib_cat_filter.clear()
            zh = self.language == 'zh'
            self.rt_lib_cat_filter.addItem('全部分类' if zh else 'All categories', 'all')
            for c in cats:
                self.rt_lib_cat_filter.addItem(c.get('name') or '', c.get('id'))
            # 恢复筛选
            sel = 0
            for i in range(self.rt_lib_cat_filter.count()):
                if self.rt_lib_cat_filter.itemData(i) == cur:
                    sel = i
                    break
            self.rt_lib_cat_filter.setCurrentIndex(sel)
            self.rt_lib_cat_filter.blockSignals(False)
            size_combo(self.rt_lib_cat_filter, fill=True)

    def _rt_lib_sync_mode_combo(self):
        if not hasattr(self, 'rt_lib_mode'):
            return
        lib = self._rt_lib_data()
        mode = lib.get('last_mode') or 'library'
        self.rt_lib_mode.blockSignals(True)
        for i in range(self.rt_lib_mode.count()):
            if self.rt_lib_mode.itemData(i) == mode:
                self.rt_lib_mode.setCurrentIndex(i)
                break
        self.rt_lib_mode.blockSignals(False)
        if hasattr(self, 'rt_lib_clear_btn'):
            self.rt_lib_clear_btn.hide()
        if hasattr(self, 'rt_history_cleanup_btn'):
            self.rt_history_cleanup_btn.setVisible(mode == 'history')

    def _rt_lib_mode_value(self) -> str:
        if not hasattr(self, 'rt_lib_mode'):
            return 'library'
        return self.rt_lib_mode.currentData() or 'library'

    def _rt_lib_on_mode_changed(self, *_args):
        from tools.iface_request_library import set_last_mode
        mode = self._rt_lib_mode_value()
        try:
            self._rt_lib = set_last_mode(self._rt_lib_data(), mode)
        except Exception:
            pass
        if hasattr(self, 'rt_lib_clear_btn'):
            self.rt_lib_clear_btn.hide()
        if hasattr(self, 'rt_history_cleanup_btn'):
            self.rt_history_cleanup_btn.setVisible(mode == 'history')
        self._rt_lib_refresh_list()

    def _rt_lib_refresh_list(self, *_args):
        if not hasattr(self, 'rt_lib_list'):
            return
        from tools.iface_request_library import display_label, filter_items
        lib = self._rt_lib_data()
        mode = self._rt_lib_mode_value()
        cat = 'all'
        if hasattr(self, 'rt_lib_cat_filter'):
            cat = self.rt_lib_cat_filter.currentData() or 'all'
        kw = self.rt_lib_search.text() if hasattr(self, 'rt_lib_search') else ''
        source = lib.get('history') if mode == 'history' else lib.get('apis')
        items = filter_items(source or [], category_id=cat, keyword=kw)
        cat_map = {c.get('id'): c.get('name') for c in (lib.get('categories') or [])}
        # 搜索仅过滤：列表文案与未搜索时一致
        self.rt_lib_list.blockSignals(True)
        self.rt_lib_list.clear()
        first_hit = None
        for it in items:
            label = display_label(it, mode=mode, category_map=cat_map)
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, it.get('id'))
            tip = (
                f"{it.get('method') or ''} {it.get('url') or ''}\n"
                f"{cat_map.get(it.get('category_id'), '')}"
            )
            row.setToolTip(tip)
            self.rt_lib_list.addItem(row)
            if kw and first_hit is None:
                first_hit = row
        self.rt_lib_list.blockSignals(False)
        if first_hit is not None:
            self.rt_lib_list.setCurrentItem(first_hit)
        zh = self.language == 'zh'
        total = len(source or [])
        shown = len(items)
        if mode == 'history':
            text = f'发送记录 {shown}/{total}' if zh else f'Sent {shown}/{total}'
        else:
            text = f'已保存 {shown}/{total}' if zh else f'Saved {shown}/{total}'
        if hasattr(self, 'rt_lib_count'):
            self.rt_lib_count.setText(text)

    def _rt_lib_selected_item(self) -> dict | None:
        if not hasattr(self, 'rt_lib_list'):
            return None
        row = self.rt_lib_list.currentItem()
        if not row:
            return None
        iid = row.data(Qt.ItemDataRole.UserRole)
        if not iid:
            return None
        lib = self._rt_lib_data()
        mode = self._rt_lib_mode_value()
        pool = lib.get('history') if mode == 'history' else lib.get('apis')
        return next((x for x in (pool or []) if x.get('id') == iid), None)

    def _rt_lib_show_menu(self, point):
        row = self.rt_lib_list.itemAt(point)
        if not row:
            return
        self.rt_lib_list.setCurrentItem(row)
        item = self._rt_lib_selected_item()
        if not item:
            return
        mode = self._rt_lib_mode_value()
        from tools.list_pin import is_pinned, pin_action_label
        menu = QMenu(self)
        zh = getattr(self, 'language', 'zh') == 'zh'
        if mode == 'library':
            pinned = is_pinned(item)
            act = menu.addAction(pin_action_label(pinned, self.language if hasattr(self, 'language') else 'zh'))
            act.triggered.connect(lambda _=False, it=item, p=pinned: self._rt_lib_toggle_pin(it, p))
            menu.addSeparator()
            menu.addAction('加载到表单' if zh else 'Load', self._rt_lib_apply_selected)
            menu.addAction('删除接口' if zh else 'Delete API', self._rt_lib_delete_selected)
        else:
            menu.addAction('填充到请求 URL' if zh else 'Fill request URL', self._rt_fill_history_url)
            menu.addAction('复制完整 URL' if zh else 'Copy full URL', self._rt_copy_history_url)
            menu.addAction('复制为 cURL' if zh else 'Copy as cURL', self._rt_copy_history_curl)
            menu.addAction('保存到接口库' if zh else 'Save to library', self._rt_save_selected_history_to_library)
            menu.addSeparator()
            menu.addAction('删除此条历史' if zh else 'Delete history item', self._rt_lib_delete_selected)
        menu.exec(self.rt_lib_list.viewport().mapToGlobal(point))

    def _rt_lib_toggle_pin(self, item, currently_pinned):
        from tools.iface_request_library import set_api_pinned
        api_id = (item or {}).get('id')
        if not api_id:
            return
        try:
            self._rt_lib = set_api_pinned(self._rt_lib_data(), api_id, not currently_pinned)
        except Exception as exc:
            show_warning(self, '请求测试', f'置顶失败：{exc}')
            return
        self._rt_lib_refresh_list()

    def _rt_lib_apply_selected(self, *_args):
        from tools.iface_request_library import form_fields_from_item
        item = self._rt_lib_selected_item()
        if not item:
            show_warning(self, '请求测试', '请先选择一条接口或历史')
            return
        form = form_fields_from_item(item)
        # base 优先用条目里的，否则当前
        if not form.get('base_host'):
            form['base_host'] = self.rt_base_edit.text() if hasattr(self, 'rt_base_edit') else ''
        self._rt_apply_form(form)
        if self._rt_lib_mode_value() == 'library':
            self._rt_editing_api_id = item.get('id') or ''
        else:
            self._rt_editing_api_id = ''
        show_success(self, '请求测试', f'已加载：{item.get("name") or item.get("url") or ""}')

    def _rt_fill_history_url(self):
        """仅回填历史 URL，避免覆盖用户正在编辑的请求内容。"""
        item = self._rt_lib_selected_item()
        if not item:
            return
        self.rt_url.setText(item.get('url') or '')
        show_success(self, '历史', '已填充请求 URL')

    def _rt_copy_history_url(self):
        item = self._rt_lib_selected_item()
        if not item:
            return
        QApplication.clipboard().setText(item.get('url') or '')
        show_success(self, '历史', '已复制完整 URL')

    def _rt_copy_history_curl(self):
        from tools.interface_drafts import build_curl
        item = self._rt_lib_selected_item()
        if not item:
            return
        record = {
            'method': item.get('method') or 'GET',
            'url': item.get('url') or '',
            'request_headers': self._rt_headers_to_dict(item.get('headers_text') or ''),
            'request_body': item.get('body') or '',
        }
        parsed = urlparse(record['url'])
        base_url = f'{parsed.scheme}://{parsed.netloc}' if parsed.scheme and parsed.netloc else (item.get('base_host') or '')
        QApplication.clipboard().setText(build_curl(record, base_url))
        show_success(self, '历史', '已复制 cURL')

    def _rt_headers_to_dict(self, raw: str) -> dict:
        headers = {}
        for line in (raw or '').splitlines():
            key, sep, value = line.partition(':')
            if sep and key.strip():
                headers[key.strip()] = value.strip()
        return headers

    def _rt_save_selected_history_to_library(self):
        item = self._rt_lib_selected_item()
        if not item:
            return
        from tools.iface_request_library import build_api_from_form, upsert_api
        api = build_api_from_form(
            name=item.get('name') or item.get('url') or '',
            category_id=item.get('category_id') or '',
            method=item.get('method') or 'GET',
            url=item.get('url') or '',
            base_host=item.get('base_host') or '',
            headers_text=item.get('headers_text') or '',
            params_text=item.get('params_text') or '',
            body=item.get('body') or '',
        )
        try:
            self._rt_lib = upsert_api(self._rt_lib_data(), api)
            self._rt_lib_refresh_list()
            show_success(self, '接口库', '已保存到接口库')
        except Exception as exc:
            show_warning(self, '接口库', str(exc))

    def _rt_lib_resend_selected(self, *_args):
        """兼容旧调用：加载选中条目到表单，但不自动发送。"""
        from tools.iface_request_library import form_fields_from_item
        item = self._rt_lib_selected_item()
        if not item:
            show_warning(self, '请求测试', '请先选择一条接口或历史')
            return
        form = form_fields_from_item(item)
        if not form.get('base_host'):
            form['base_host'] = self.rt_base_edit.text() if hasattr(self, 'rt_base_edit') else ''
        self._rt_apply_form(form)
        if self._rt_lib_mode_value() == 'library':
            self._rt_editing_api_id = item.get('id') or ''
        else:
            self._rt_editing_api_id = ''

    def _rt_collect_form_snapshot(self) -> dict:
        return {
            'method': self.rt_method.currentText() if hasattr(self, 'rt_method') else 'GET',
            'url': (self.rt_url.text() or '').strip() if hasattr(self, 'rt_url') else '',
            'base_host': self.rt_base_edit.text() if hasattr(self, 'rt_base_edit') else '',
            'headers_text': self.rt_headers.toPlainText() if hasattr(self, 'rt_headers') else '',
            'params_text': self.rt_params.toPlainText() if hasattr(self, 'rt_params') else '',
            'body': self.rt_body.toPlainText() if hasattr(self, 'rt_body') else '',
            'category_id': self._rt_current_category_id(),
        }

    def _rt_save_api(self):
        from PyQt6.QtWidgets import QInputDialog
        from tools.iface_request_library import (
            UNCATEGORIZED_ID, build_api_from_form, set_last_category, upsert_api,
        )
        snap = self._rt_collect_form_snapshot()
        if not snap.get('url'):
            show_warning(self, '保存接口', '请先填写 URL')
            return
        # 默认名：path
        from tools.iface_request_library import form_fields_from_item
        default_name = form_fields_from_item(snap).get('name') or snap['url']
        # 若正在编辑库内条目，带出原名
        lib = self._rt_lib_data()
        edit_id = getattr(self, '_rt_editing_api_id', '') or ''
        if edit_id:
            old = next((a for a in (lib.get('apis') or []) if a.get('id') == edit_id), None)
            if old and old.get('name'):
                default_name = old.get('name')
        zh = self.language == 'zh'
        name, ok = QInputDialog.getText(
            self,
            '保存接口' if zh else 'Save API',
            '接口名称：' if zh else 'Name:',
            text=default_name,
        )
        if not ok:
            return
        name = (name or '').strip() or default_name
        cat = snap.get('category_id') or UNCATEGORIZED_ID
        api = build_api_from_form(
            name=name,
            category_id=cat,
            method=snap['method'],
            url=snap['url'],
            base_host=snap.get('base_host') or '',
            headers_text=snap.get('headers_text') or '',
            params_text=snap.get('params_text') or '',
            body=snap.get('body') or '',
            api_id=edit_id,
        )
        try:
            self._rt_lib = upsert_api(lib, api)
            self._rt_lib = set_last_category(self._rt_lib, cat)
            self._rt_editing_api_id = api['id']
            # 切到接口库视图
            if hasattr(self, 'rt_lib_mode'):
                for i in range(self.rt_lib_mode.count()):
                    if self.rt_lib_mode.itemData(i) == 'library':
                        self.rt_lib_mode.setCurrentIndex(i)
                        break
            self._rt_lib_fill_category_combos()
            self._rt_lib_refresh_list()
            show_success(self, '保存接口', f'已保存「{name}」')
        except Exception as exc:
            show_warning(self, '保存接口', str(exc))

    def _rt_manage_categories(self):
        from PyQt6.QtWidgets import QInputDialog
        from tools.iface_request_library import (
            UNCATEGORIZED_ID, add_category, delete_category, rename_category,
        )
        zh = self.language == 'zh'
        actions = [
            '新增分类' if zh else 'Add category',
            '重命名分类' if zh else 'Rename category',
            '删除分类' if zh else 'Delete category',
        ]
        action, ok = QInputDialog.getItem(
            self,
            '管理分类' if zh else 'Categories',
            '操作：' if zh else 'Action:',
            actions,
            0,
            False,
        )
        if not ok:
            return
        lib = self._rt_lib_data()
        try:
            if action == actions[0]:
                name, ok2 = QInputDialog.getText(
                    self, '新增分类' if zh else 'Add', '分类名称：' if zh else 'Name:',
                )
                if not ok2 or not (name or '').strip():
                    return
                self._rt_lib = add_category(lib, name.strip())
                show_success(self, '分类', f'已新增「{name.strip()}」')
            elif action == actions[1]:
                names = [
                    c.get('name') for c in (lib.get('categories') or [])
                    if c.get('id') != UNCATEGORIZED_ID
                ]
                if not names:
                    show_warning(self, '分类', '没有可重命名的自定义分类')
                    return
                cur, ok2 = QInputDialog.getItem(
                    self, '重命名' if zh else 'Rename', '选择分类：' if zh else 'Category:',
                    names, 0, False,
                )
                if not ok2:
                    return
                hit = next(
                    (c for c in lib['categories'] if c.get('name') == cur), None,
                )
                if not hit:
                    return
                new_name, ok3 = QInputDialog.getText(
                    self, '重命名' if zh else 'Rename', '新名称：' if zh else 'New name:',
                    text=cur,
                )
                if not ok3 or not (new_name or '').strip():
                    return
                self._rt_lib = rename_category(lib, hit['id'], new_name.strip())
                show_success(self, '分类', f'已重命名为「{new_name.strip()}」')
            else:
                names = [
                    c.get('name') for c in (lib.get('categories') or [])
                    if c.get('id') != UNCATEGORIZED_ID
                ]
                if not names:
                    show_warning(self, '分类', '没有可删除的自定义分类')
                    return
                cur, ok2 = QInputDialog.getItem(
                    self, '删除分类' if zh else 'Delete', '选择分类：' if zh else 'Category:',
                    names, 0, False,
                )
                if not ok2:
                    return
                hit = next(
                    (c for c in lib['categories'] if c.get('name') == cur), None,
                )
                if not hit:
                    return
                if not confirm_action(
                    self,
                    '删除分类' if zh else 'Delete category',
                    (
                        f'删除「{cur}」后，其下接口将归入「未分类」。确定删除？'
                        if zh else
                        f'Delete "{cur}"? APIs move to Uncategorized.'
                    ),
                    confirm_text='删除' if zh else 'Delete',
                    danger=True,
                ):
                    return
                self._rt_lib = delete_category(lib, hit['id'])
                show_success(self, '分类', f'已删除「{cur}」')
        except Exception as exc:
            show_warning(self, '分类', str(exc))
            return
        self._rt_lib_fill_category_combos()
        self._rt_lib_refresh_list()

    def _rt_lib_delete_selected(self):
        from tools.iface_request_library import delete_api, delete_history
        item = self._rt_lib_selected_item()
        if not item:
            show_warning(self, '请求测试', '请先选择要删除的条目')
            return
        zh = self.language == 'zh'
        mode = self._rt_lib_mode_value()
        label = item.get('name') or item.get('url') or item.get('id')
        if not confirm_action(
            self,
            '删除' if zh else 'Delete',
            (f'确定删除「{label}」？' if zh else f'Delete "{label}"?'),
            confirm_text='删除' if zh else 'Delete',
            danger=True,
        ):
            return
        lib = self._rt_lib_data()
        try:
            if mode == 'history':
                self._rt_lib = delete_history(lib, item.get('id'))
            else:
                self._rt_lib = delete_api(lib, item.get('id'))
                if getattr(self, '_rt_editing_api_id', '') == item.get('id'):
                    self._rt_editing_api_id = ''
            self._rt_lib_refresh_list()
            show_success(self, '请求测试', '已删除')
        except Exception as exc:
            show_warning(self, '请求测试', str(exc))

    def _show_history_cleanup_dialog(self):
        """按明确范围预览并清理已持久化请求测试历史。"""
        if self._rt_lib_mode_value() != 'history':
            return
        from tools.iface_request_library import clear_history_items, history_items_for_cleanup

        zh = self.language == 'zh'
        dialog = QDialog(self)
        dialog.setWindowTitle('历史清理配置' if zh else 'History cleanup')
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        hint = QLabel('仅清理已保存的请求测试历史，不影响当前抓包会话。' if zh else 'Only saved request-test history is affected; captured sessions remain intact.')
        hint.setWordWrap(True)
        hint.setObjectName('field-hint')
        layout.addWidget(hint)
        scope_combo = QComboBox(dialog)
        scope_combo.addItem('全部历史' if zh else 'All history', 'all')
        scope_combo.addItem('7 天前历史' if zh else 'History older than 7 days', 'older_than_7_days')
        scope_combo.addItem('当前搜索结果' if zh else 'Current search results', 'current_search')
        layout.addWidget(scope_combo)
        impact = QLabel(dialog)
        impact.setObjectName('field-hint')
        layout.addWidget(impact)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, self.language)
        clean_btn = buttons.addButton('清理所选范围' if zh else 'Clean selected scope', QDialogButtonBox.ButtonRole.AcceptRole)
        clean_btn.setObjectName('btn-danger')
        layout.addWidget(buttons)

        def matching_items():
            lib = self._rt_lib_data()
            visible = []
            for row_index in range(self.rt_lib_list.count()):
                row = self.rt_lib_list.item(row_index)
                item_id = row.data(Qt.ItemDataRole.UserRole) if row else ''
                item = next((entry for entry in (lib.get('history') or []) if entry.get('id') == item_id), None)
                if item:
                    visible.append(item)
            return history_items_for_cleanup(
                lib.get('history') or [],
                scope_combo.currentData() or 'all',
                current_items=visible,
            )

        def refresh_impact(*_args):
            count = len(matching_items())
            impact.setText(f'预计影响 {count} 条历史' if zh else f'Estimated impact: {count} history items')
            clean_btn.setEnabled(count > 0)

        def clean_selected():
            items = matching_items()
            count = len(items)
            if not count:
                return
            message = (
                f'将删除 {count} 条请求测试历史，当前抓包会话不会受影响。确定继续？'
                if zh else
                f'Delete {count} saved request-test history items? Current captured sessions are unaffected.'
            )
            if not confirm_action(
                dialog,
                '清理历史' if zh else 'Clean history',
                message,
                confirm_text='清理' if zh else 'Clean',
                danger=True,
            ):
                return
            try:
                self._rt_lib = clear_history_items(
                    self._rt_lib_data(), {item.get('id') for item in items}
                )
                self._rt_lib_refresh_list()
                dialog.accept()
                show_success(self, '历史', f'已清理 {count} 条历史' if zh else f'Cleaned {count} history items')
            except Exception as exc:
                show_warning(dialog, '历史' if zh else 'History', str(exc))

        scope_combo.currentIndexChanged.connect(refresh_impact)
        buttons.rejected.connect(dialog.reject)
        clean_btn.clicked.connect(clean_selected)
        refresh_impact()
        dialog.exec()

    def _rt_lib_clear_history(self):
        """兼容旧槽位：通过带影响范围的清理配置完成操作。"""
        self._show_history_cleanup_dialog()

    def _rt_append_history_from_send(
        self,
        *,
        status=None,
        ok=None,
        error: str = '',
        response_body: str = '',
    ):
        from tools.iface_request_library import append_history, build_history_from_send
        meta = getattr(self, '_rt_send_meta', None) or {}
        url = meta.get('url') or ''
        if not url:
            return
        started = getattr(self, '_rt_send_started_at', 0) or 0
        duration = int((time.time() - started) * 1000) if started else 0
        entry = build_history_from_send(
            method=meta.get('method') or 'GET',
            url=url,
            base_host=meta.get('base_host') or '',
            headers_text=meta.get('headers_text') or '',
            params_text=meta.get('params_text') or '',
            body=meta.get('body') or '',
            category_id=meta.get('category_id') or self._rt_current_category_id(),
            status=status,
            ok=ok,
            error=error or '',
            response_body=response_body or '',
            duration_ms=duration,
        )
        try:
            self._rt_lib = append_history(self._rt_lib_data(), entry)
            # 列表在历史模式时刷新；库模式也更新计数
            self._rt_lib_refresh_list()
        except Exception:
            pass

    def _show_environment_config_dialog(self):
        """集中管理非敏感环境 Base；请求报文永不进入该配置。"""
        from PyQt6.QtWidgets import QInputDialog

        zh = self.language == 'zh'
        dialog = QDialog(self)
        dialog.setWindowTitle('环境配置' if zh else 'Environment configuration')
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        targets = QListWidget(dialog)
        targets.setObjectName('iface-environment-list')
        layout.addWidget(targets, 1)

        def reload_list(select_id=''):
            targets.clear()
            selected_row = -1
            for index, item in enumerate(self._config.get('local_targets') or []):
                row = QListWidgetItem(f"{item.get('name') or '环境'} · {item.get('base_url') or ''}")
                row.setData(Qt.ItemDataRole.UserRole, item.get('id'))
                targets.addItem(row)
                if item.get('id') == select_id:
                    selected_row = index
            if selected_row >= 0:
                targets.setCurrentRow(selected_row)

        def selected_target():
            row = targets.currentItem()
            target_id = row.data(Qt.ItemDataRole.UserRole) if row else ''
            return next((item for item in self._config.get('local_targets') or [] if item.get('id') == target_id), None)

        def add_target():
            import uuid
            from tools.iface_request_test import RequestTestError, normalize_base_host
            name, ok = QInputDialog.getText(dialog, '新增环境' if zh else 'Add environment', '名称：' if zh else 'Name:')
            if not ok:
                return
            base, ok = QInputDialog.getText(
                dialog, '新增环境' if zh else 'Add environment',
                'Base URL (http://host:port)：', text=self.rt_base_edit.text() or 'http://localhost:18031',
            )
            if not ok:
                return
            try:
                base = normalize_base_host(base)
            except RequestTestError as exc:
                show_warning(dialog, '环境' if zh else 'Environment', str(exc))
                return
            item = {'id': uuid.uuid4().hex, 'name': (name or '环境').strip() or '环境', 'base_url': base}
            self._config.setdefault('local_targets', []).append(item)
            self._config['default_target_id'] = item['id']
            save_interface_debug_config(self._config)
            self._fill_local_targets()
            reload_list(item['id'])

        def edit_target():
            from tools.iface_request_test import RequestTestError, normalize_base_host
            item = selected_target()
            if not item:
                show_warning(dialog, '环境' if zh else 'Environment', '请先选择环境' if zh else 'Select an environment first.')
                return
            name, ok = QInputDialog.getText(dialog, '编辑环境' if zh else 'Edit environment', '名称：' if zh else 'Name:', text=item.get('name') or '')
            if not ok:
                return
            base, ok = QInputDialog.getText(dialog, '编辑环境' if zh else 'Edit environment', 'Base URL：', text=item.get('base_url') or '')
            if not ok:
                return
            try:
                item['base_url'] = normalize_base_host(base)
            except RequestTestError as exc:
                show_warning(dialog, '环境' if zh else 'Environment', str(exc))
                return
            item['name'] = (name or item.get('name') or '环境').strip() or '环境'
            save_interface_debug_config(self._config)
            self._fill_local_targets()
            reload_list(item.get('id') or '')

        def delete_target():
            item = selected_target()
            if not item:
                return
            if not confirm_action(
                dialog, '删除环境' if zh else 'Delete environment',
                f"确定删除「{item.get('name') or ''}」？" if zh else 'Delete selected environment?',
                confirm_text='删除' if zh else 'Delete', danger=True,
            ):
                return
            target_id = item.get('id')
            self._config['local_targets'] = [
                value for value in (self._config.get('local_targets') or []) if value.get('id') != target_id
            ]
            if self._config.get('default_target_id') == target_id:
                remaining = self._config['local_targets']
                self._config['default_target_id'] = (remaining[0].get('id') if remaining else '')
            save_interface_debug_config(self._config)
            self._fill_local_targets()
            reload_list()

        actions = QHBoxLayout()
        for text, handler in (
            ('新增' if zh else 'Add', add_target),
            ('编辑' if zh else 'Edit', edit_target),
            ('删除' if zh else 'Delete', delete_target),
        ):
            button = QPushButton(text, dialog)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, self.language)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        reload_list(self.local_target_combo.currentData() or '')
        dialog.exec()

    def _show_url_filter_config_dialog(self):
        """以列表管理旧版 url_filter_prefixes，保持 list[str] 兼容。"""
        from PyQt6.QtWidgets import QInputDialog

        zh = self.language == 'zh'
        dialog = QDialog(self)
        dialog.setWindowTitle('过滤配置' if zh else 'Filter configuration')
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        rules = QListWidget(dialog)
        rules.setObjectName('iface-url-filter-list')
        for prefix in self._config.get('url_filter_prefixes') or []:
            rules.addItem(prefix)
        layout.addWidget(rules, 1)

        def add_rule():
            value, ok = QInputDialog.getText(dialog, '新增规则' if zh else 'Add rule', 'URL 前缀：' if zh else 'URL prefix:')
            if ok and (value or '').strip():
                rules.addItem(value.strip())

        def edit_rule():
            item = rules.currentItem()
            if not item:
                return
            value, ok = QInputDialog.getText(dialog, '编辑规则' if zh else 'Edit rule', 'URL 前缀：' if zh else 'URL prefix:', text=item.text())
            if ok and (value or '').strip():
                item.setText(value.strip())

        def delete_rule():
            row = rules.currentRow()
            if row < 0:
                return
            if confirm_action(dialog, '删除规则' if zh else 'Delete rule', '确定删除选中规则？' if zh else 'Delete selected rule?', confirm_text='删除' if zh else 'Delete', danger=True):
                rules.takeItem(row)

        def move_rule(offset):
            row = rules.currentRow()
            target = row + offset
            if row < 0 or target < 0 or target >= rules.count():
                return
            item = rules.takeItem(row)
            rules.insertItem(target, item)
            rules.setCurrentRow(target)

        actions = QHBoxLayout()
        for text, handler in (
            ('新增' if zh else 'Add', add_rule),
            ('编辑' if zh else 'Edit', edit_rule),
            ('删除' if zh else 'Delete', delete_rule),
            ('上移' if zh else 'Up', lambda: move_rule(-1)),
            ('下移' if zh else 'Down', lambda: move_rule(1)),
        ):
            button = QPushButton(text, dialog)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save,
            parent=dialog,
        )
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, self.language)
        buttons.rejected.connect(dialog.reject)

        def save_rules():
            prefixes = []
            for index in range(rules.count()):
                value = rules.item(index).text().strip()
                if value and value not in prefixes:
                    prefixes.append(value)
            self._config['url_filter_prefixes'] = prefixes
            save_interface_debug_config(self._config)
            dialog.accept()

        buttons.accepted.connect(save_rules)
        layout.addWidget(buttons)
        dialog.exec()

    def _save_url_filter_prefixes(self):
        """兼容旧槽函数：保存隐藏输入框中的前缀。"""
        raw = self.rt_url_filter_edit.text().strip()
        prefixes = [p.strip() for p in raw.split(',') if p.strip()]
        self._config['url_filter_prefixes'] = prefixes
        save_interface_debug_config(self._config)

    def _rt_save_current_as_env(self):
        """把当前 Base 保存为环境（有选中则更新，否则新建）。"""
        from PyQt6.QtWidgets import QInputDialog
        import uuid
        from tools.iface_request_test import RequestTestError, normalize_base_host
        try:
            base = normalize_base_host(self.rt_base_edit.text())
            self.rt_base_edit.setText(base)
        except RequestTestError as exc:
            show_warning(self, '环境', str(exc))
            return
        tid = self.local_target_combo.currentData()
        targets = self._config.setdefault('local_targets', [])
        item = next((t for t in targets if t.get('id') == tid), None) if tid else None
        if item:
            item['base_url'] = base
            save_interface_debug_config(self._config)
            self._fill_local_targets()
            show_success(self, '环境', f'已更新环境「{item.get("name")}」')
            return
        name, ok = QInputDialog.getText(self, '保存环境', '环境名称（如 开发 / UAT / 本机）：')
        if not ok:
            return
        item = {'id': uuid.uuid4().hex, 'name': (name or '环境').strip(), 'base_url': base}
        targets.append(item)
        self._config['default_target_id'] = item['id']
        save_interface_debug_config(self._config)
        self._fill_local_targets()
        show_success(self, '环境', f'已保存环境「{item["name"]}」')

    def _add_local_target(self):
        from PyQt6.QtWidgets import QInputDialog
        import uuid
        from tools.iface_request_test import RequestTestError, normalize_base_host
        zh = self.language == 'zh'
        name, ok = QInputDialog.getText(self, '新增环境' if zh else 'Add env', '名称：' if zh else 'Name:')
        if not ok:
            return
        url, ok = QInputDialog.getText(
            self, '新增环境' if zh else 'Add env',
            'Base URL (http://host:port)：',
            text=(self.rt_base_edit.text() if hasattr(self, 'rt_base_edit') else '') or 'http://localhost:18031',
        )
        if not ok:
            return
        try:
            url = normalize_base_host(url)
        except RequestTestError as exc:
            show_warning(self, '环境', str(exc))
            return
        item = {'id': uuid.uuid4().hex, 'name': (name or '环境').strip(), 'base_url': url}
        self._config.setdefault('local_targets', []).append(item)
        self._config['default_target_id'] = item['id']
        save_interface_debug_config(self._config)
        self._fill_local_targets()

    def _edit_local_target(self):
        from PyQt6.QtWidgets import QInputDialog
        from tools.iface_request_test import RequestTestError, normalize_base_host
        tid = self.local_target_combo.currentData()
        targets = self._config.get('local_targets') or []
        item = next((t for t in targets if t.get('id') == tid), None)
        if not item:
            show_warning(self, '环境', '请先选择一个已保存环境')
            return
        name, ok = QInputDialog.getText(self, '编辑环境', '名称：', text=item.get('name') or '')
        if not ok:
            return
        url, ok = QInputDialog.getText(self, '编辑环境', 'Base URL：', text=item.get('base_url') or '')
        if not ok:
            return
        try:
            url = normalize_base_host(url)
        except RequestTestError as exc:
            show_warning(self, '环境', str(exc))
            return
        item['name'] = (name or item['name']).strip()
        item['base_url'] = url
        save_interface_debug_config(self._config)
        self._fill_local_targets()

    def _delete_local_target(self):
        tid = self.local_target_combo.currentData()
        if not tid:
            return
        zh = self.language == 'zh'
        if not confirm_action(
            self, '删除环境' if zh else 'Delete',
            '确定删除该环境配置？' if zh else 'Delete this environment?',
            confirm_text='删除' if zh else 'Delete', danger=True,
        ):
            return
        self._config['local_targets'] = [
            t for t in (self._config.get('local_targets') or []) if t.get('id') != tid
        ]
        if self._config.get('default_target_id') == tid:
            self._config['default_target_id'] = ''
        save_interface_debug_config(self._config)
        self._fill_local_targets()

    # ── 响应式 ───────────────────────────────────────
    def _source_label(self, source) -> str:
        # http_capture / local_proxy / ie_proxy / cdp
        s = (source or '').lower()
        if s in ('http_capture', 'local_proxy', 'proxy', 'mitm'):
            return '抓包'
        if s in ('ie_proxy', 'ie'):
            return 'IE抓包'
        if s in ('cdp', 'chromium'):
            return 'Chromium'
        return source or '—'

    def _sync_session_list_toggle_labels(self):
        """按当前显隐状态刷新工具条与右侧恢复按钮文案。"""
        from ui.section_toggle import apply_visibility_toggle
        w = getattr(self, '_session_list_widget', None)
        visible = w is not None and not w.isHidden()
        apply_visibility_toggle(
            self._toggle_list_btn,
            content_visible=visible,
            language=self.language,
            kind='session_list',
            tooltip='隐藏或显示左侧会话列表' if self.language == 'zh' else 'Show or hide session list',
        )
        zh = self.language == 'zh'
        self.session_list_reveal_btn.setText('显示会话列表' if zh else 'Show session list')
        self.session_list_reveal_btn.setToolTip(
            '恢复左侧会话列表' if zh else 'Restore left session list'
        )
        self.session_list_reveal_btn.setVisible(not visible)

    def _toggle_session_list(self):
        """隐藏/显示会话列表（mid_splitter 左侧）。"""
        w = getattr(self, '_session_list_widget', None)
        if w is None:
            return
        if w.isHidden():
            w.show()
        else:
            w.hide()
        self._sync_session_list_toggle_labels()
        self._rebuild_session_actions_menu()

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import page_spacing_for_mode
        from ui.splitter_prefs import install_splitter_prefs, layout_bucket
        previous_mode = self._layout_mode
        self._layout_mode = mode
        if hasattr(self, '_page_root_layout') and self._page_root_layout is not None:
            self._page_root_layout.setSpacing(page_spacing_for_mode(mode, low_height))
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height or mode == 'narrow')
        prev_orient = self.mid_splitter.orientation()
        # Compact/Narrow：会话/详情改为上下堆叠，并抬高编辑区下限（§8.2/8.3）
        apply_splitter_orientation(
            self.mid_splitter,
            mode,
            min_editor=240 if mode in ('compact', 'narrow') else editor_min_height(),
        )
        self.mid_splitter.setChildrenCollapsible(False)
        self.mid_splitter.setOpaqueResize(True)
        if self.mid_splitter.orientation() == Qt.Orientation.Horizontal:
            self.mid_splitter.setStretchFactor(0, 1)
            self.mid_splitter.setStretchFactor(1, 2)
            # prefs 夹紧用 240/480；宽屏下再把详情区硬下限提到 520（小窗不强塞）
            mins = [240, 480]
            defaults = [420, 580]
            right = self.mid_splitter.widget(1)
            if right is not None:
                right.setMinimumWidth(520 if mode in ('wide', 'standard') else 420)
        else:
            self.mid_splitter.setStretchFactor(0, 1)
            self.mid_splitter.setStretchFactor(1, 2)
            mins = [200, 240]
            defaults = [220, 420]
            right = self.mid_splitter.widget(1)
            if right is not None:
                right.setMinimumWidth(0)
        prefs = (self._prefs.get('splitter_sizes') or {}).get(mode)
        install_splitter_prefs(
            self.mid_splitter,
            defaults=defaults,
            saved=prefs if previous_mode != mode or prev_orient != self.mid_splitter.orientation() else list(self.mid_splitter.sizes()),
            page_id='interface-debug',
            tab_id='session-detail',
            bucket=layout_bucket(mode),
            min_sizes=mins,
            accessible_name='接口排查会话/详情分隔',
            on_changed=lambda values: self._save_splitter_sizes(*values[:2]),
        )
        # 任何断点都只保留唯一抓包主按钮，不恢复模式/证书入口。
        self._apply_mode_ui()
        self.capture_toggle_btn.show()
        self.connect_btn.hide()
        self.stop_btn.hide()
        self.test_listen_btn.show()
        for edit in (self.overview_edit, self.req_detail, self.resp_detail, self.draft_preview):
            edit.setMinimumHeight(240 if mode in ('compact', 'narrow') else editor_min_height())
        # 窄屏：请求验证两行上下文全宽，细节区最小高度守住
        if hasattr(self, 'request_verify_context'):
            self.request_verify_context.setMinimumHeight(0)
        self._update_responsive_workspace()

    # ── 语言 / 清理 ──────────────────────────────────
    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('接口排查' if zh else 'API Debug')
        self.page_subtitle.setText(
            '抓 HTTP / HTTPS 请求 · 列表看 URL · 仅内存' if zh else
            'Capture HTTP/HTTPS · URL list · memory only'
        )
        self._refresh_capture_action()
        self.clear_list_btn.setText('清空本次会话' if zh else 'Clear session')
        self.test_listen_btn.setText('测试监听' if zh else 'Test listen')
        self.test_listen_btn.setToolTip(
            '本机探测，确认抓包链路可用' if zh else 'Loopback probe'
        )
        if hasattr(self, '_refresh_capture_status_text'):
            self._refresh_capture_status_text()
        if hasattr(self, '_rebuild_capture_actions_menu'):
            self._rebuild_capture_actions_menu()
        if hasattr(self, 'session_pane_title'):
            self.session_pane_title.setText('会话与筛选' if zh else 'Sessions & filters')
        if hasattr(self, 'restore_proxy_btn'):
            self.restore_proxy_btn.setText('恢复系统代理' if zh else 'Restore proxy')
            self.restore_proxy_btn.setToolTip(
                '抓包异常退出导致网页/接口不通时，点此恢复 Windows 系统代理'
                if zh else
                'Restore Windows system proxy if capture left it broken'
            )
        self.filter_edit.setPlaceholderText(
            '搜索 URL / host / path / method / 状态…' if zh else
            'Search URL / host / path / method / status…'
        )
        chip_labels = {
            FILTER_ALL: ('全部', 'All'),
            FILTER_XHR: ('XHR/Fetch', 'XHR/Fetch'),
            FILTER_FAILED: ('失败', 'Failed'),
            FILTER_SLOW: ('慢请求', 'Slow'),
            FILTER_JSON_XML: ('JSON/XML', 'JSON/XML'),
            FILTER_STATIC: ('静态资源', 'Static'),
        }
        for k, chip in self._filter_chips.items():
            chip.setText(chip_labels[k][0 if zh else 1])
        self.cols_btn.setText('列设置' if zh else 'Columns')
        for button in (
            self.session_actions_more_btn, self.capture_actions_more_btn,
            self.req_actions_more_btn, self.resp_actions_more_btn,
            self.rt_io_more_btn, self.rt_form_more_btn,
        ):
            button.setText('更多' if zh else 'More')
            button.setToolTip('显示收纳操作' if zh else 'Show overflow actions')
        if hasattr(self, '_sync_session_list_toggle_labels'):
            self._sync_session_list_toggle_labels()
        self._rebuild_column_menu()
        widths = self._last_responsive_widths
        if widths:
            self._update_responsive_workspace(*widths)
        self.detail_tabs.setTabText(0, '概览' if zh else 'Overview')
        self.detail_tabs.setTabText(1, '请求' if zh else 'Request')
        self.detail_tabs.setTabText(2, '响应' if zh else 'Response')
        self.detail_tabs.setTabText(3, '请求验证' if zh else 'Request verify')
        self.reveal_cb.setText('显示敏感内容' if zh else 'Reveal secrets')
        self.copy_safe_url_btn.setText('复制安全 URL' if zh else 'Copy safe URL')
        self.copy_req_btn.setText('复制请求' if zh else 'Copy request')
        self.format_req_btn.setText('送格式工具' if zh else 'Format tools')
        self.gateway_req_btn.setText('送入加解密' if zh else 'Crypto')
        self.copy_resp_btn.setText('复制响应' if zh else 'Copy response')
        self.format_resp_btn.setText('送格式工具' if zh else 'Format tools')
        self.gateway_resp_btn.setText('送入加解密' if zh else 'Crypto')
        self.draft_badge.setText(
            '请求验证' if zh else 'Request verify'
        )
        self.target_label.setText('环境' if zh else 'Env')
        if hasattr(self, 'base_label'):
            self.base_label.setText('Base')
        if hasattr(self, 'rt_fill_btn'):
            self.rt_fill_btn.setText('从会话填充' if zh else 'Fill from session')
            if hasattr(self, 'rt_environment_config_btn'):
                self.rt_environment_config_btn.setText('环境' if zh else 'Env')
            if hasattr(self, 'rt_filter_config_btn'):
                self.rt_filter_config_btn.setText('过滤' if zh else 'Filter')
            self._rt_refresh_send_label()
            self.export_detail_btn.setText('导出明细' if zh else 'Export detail')
            self.rt_import_btn.setText('导入明细' if zh else 'Import')
            self.rt_resp_label.setText('响应' if zh else 'Response')
        if hasattr(self, 'rt_ssl_verify'):
            self.rt_ssl_verify.setText('HTTPS' if zh else 'HTTPS')
            self.rt_ssl_verify.setToolTip(
                '关闭仅用于内网自签证书；默认校验证书以满足安测要求'
                if zh else
                'Disable only for self-signed intranet hosts; verification is on by default'
            )
        if hasattr(self, 'rt_req_copy_btn'):
            self.rt_req_copy_btn.setText('复制请求' if zh else 'Copy req')
            self.rt_req_copy_btn.setToolTip(
                '一键复制当前请求 Body（完整）' if zh else 'Copy full request body'
            )
            self.rt_resp_copy_btn.setText('复制响应' if zh else 'Copy resp')
            self.rt_resp_copy_btn.setToolTip(
                '一键复制完整响应 Body' if zh else 'Copy full response body'
            )
        if hasattr(self, 'rt_req_format_btn'):
            self.rt_req_format_btn.setText('请求→格式工具' if zh else 'Req → Format')
            self.rt_req_format_btn.setToolTip(
                '把当前请求 Body 送入格式工具' if zh else 'Send request body to Format Tools'
            )
            self.rt_resp_format_btn.setText('响应→格式工具' if zh else 'Resp → Format')
            self.rt_resp_format_btn.setToolTip(
                '把完整响应 Body 送入格式工具' if zh else 'Send full response body to Format Tools'
            )
        if hasattr(self, 'rt_save_env_btn'):
            self.rt_save_env_btn.setText('保存环境' if zh else 'Save env')
        if hasattr(self, 'rt_cat_label'):
            self.rt_cat_label.setText('分类' if zh else 'Category')
            self.rt_save_api_btn.setText('保存接口' if zh else 'Save API')
            self.rt_save_api_btn.setToolTip(
                '把当前请求保存到接口库（可分类）' if zh else 'Save current request to library'
            )
            self.rt_manage_cat_btn.setText('管理分类' if zh else 'Categories')
            self.rt_manage_cat_btn.setToolTip(
                '新增 / 重命名 / 删除接口分类' if zh else 'Add / rename / delete categories'
            )
        if hasattr(self, 'rt_lib_mode'):
            if hasattr(self, 'rt_lib_mode_label'):
                self.rt_lib_mode_label.setText('列表' if zh else 'List')
            if hasattr(self, 'rt_lib_cat_label'):
                self.rt_lib_cat_label.setText('分类' if zh else 'Category')
            # 保留 data，只改显示文案
            for i in range(self.rt_lib_mode.count()):
                data = self.rt_lib_mode.itemData(i)
                if data == 'library':
                    self.rt_lib_mode.setItemText(i, '已保存' if zh else 'Saved')
                elif data == 'history':
                    self.rt_lib_mode.setItemText(i, '发送记录' if zh else 'Sent')
            self.rt_lib_mode.setToolTip(
                '已保存：点「保存接口」收藏的请求。发送记录：点「发送」后自动留下的记录。'
                if zh else
                'Saved: requests you bookmarked. Sent: auto-logged after Send.'
            )
            size_enum_combo(self.rt_lib_mode)
            self.rt_lib_search.setPlaceholderText(
                '搜索名称 / URL' if zh else 'Search name / URL'
            )
            self.rt_lib_load_btn.setText('加载' if zh else 'Load')
            self.rt_lib_load_btn.setToolTip(
                '双击或点加载：填入表单' if zh else 'Load into form'
            )
            self.rt_lib_del_btn.setText('删除' if zh else 'Del')
            self.rt_lib_clear_btn.setText('清空历史' if zh else 'Clear')
            self.rt_lib_clear_btn.setToolTip(
                '清空全部请求测试历史' if zh else 'Clear all history'
            )
            if hasattr(self, 'rt_history_cleanup_btn'):
                self.rt_history_cleanup_btn.setText('历史清理' if zh else 'Clean history')
                self.rt_history_cleanup_btn.setToolTip(
                    '按范围预览并清理请求测试历史' if zh else 'Preview and clean request-test history by scope'
                )
            # 刷新「全部分类」文案
            if self.rt_lib_cat_filter.count() > 0 and self.rt_lib_cat_filter.itemData(0) == 'all':
                self.rt_lib_cat_filter.setItemText(0, '全部分类' if zh else 'All categories')
            self._rt_lib_refresh_list()
        if hasattr(self, 'add_target_btn'):
            self.add_target_btn.setToolTip('新增环境' if zh else 'Add environment')
            self.edit_target_btn.setToolTip('编辑环境' if zh else 'Edit environment')
            self.del_target_btn.setToolTip('删除环境' if zh else 'Delete environment')
        if hasattr(self, 'export_list_btn'):
            self.export_list_btn.setText('导出明细' if zh else 'Export')
        self.draft_hint.setText(
            '「已保存」是收藏的请求；「发送记录」是点发送后自动留下的。环境 Base 只替换 host。'
            if zh else
            'Saved = bookmarked requests. Sent = auto log after Send. Env rewrites host only.'
        )
        self._apply_mode_ui()
        labels = self.COL_LABELS_ZH if zh else self.COL_LABELS_EN
        self.table.setHorizontalHeaderLabels([labels[k] for k in COLUMN_KEYS])

    def shutdown_cleanup(self):
        try:
            self._wait_hint_timer.stop()
            self._status_tick.stop()
        except Exception:
            pass
        try:
            if self._cdp_session:
                self._cdp_session.stop()
                self._cdp_session = None
            if self._ie_worker:
                self._ie_worker.stop()
                self._ie_worker = None
        except Exception:
            pass
        self._listening = False
        self._channel_ready = False
        self.clear_session()
        try:
            restore_proxy_from_snapshot()
        except Exception:
            pass
        try:
            from tools.ie_proxy import ensure_system_proxy_safe
            ensure_system_proxy_safe(reason='shutdown')
        except Exception:
            pass
