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
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from tools.browser_debug import (
    BrowserDebugError, connect_page_session, discover_browsers, fetch_cdp_targets,
    is_loopback_host, launch_debug_browser, mask_sensitive_value, mask_url_query,
    pick_default_page_target, port_open, wait_debug_port,
)
from tools.ie_proxy import (
    IeProxyWorker, install_user_root_cert, remove_recorded_cert, restore_proxy_from_snapshot,
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
    format_size, host_of, host_path_display, is_failed, pretty_body, protocol_of,
    query_pairs, response_size_bytes, split_cookies, url_path_display,
)
from ui.aurora_progress import AuroraProgress
from ui.confirm_dialog import confirm_action, show_info, show_success, show_warning
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import size_combo
from ui.page_chrome import make_page_header
from ui.responsive import apply_splitter_orientation, editor_min_height, set_subtitle_visible


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

    def __init__(self, method: str, url: str, headers: dict, body: str, parent=None):
        super().__init__(parent)
        self.method = method
        self.url = url
        self.headers = headers or {}
        self.body = body or ''

    def run(self):
        try:
            from tools.iface_request_test import RequestTestError, send_http_request
            result = send_http_request(
                self.method, self.url, headers=self.headers, body=self.body,
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
    _sig_capture_record = pyqtSignal(dict)
    _sig_capture_error = pyqtSignal(str)
    _sig_capture_stopped = pyqtSignal()

    # 对齐 Fiddler Session 列表列名
    COL_LABELS_ZH = {
        'seq': '#', 'status': '结果', 'protocol': '协议', 'method': '方法',
        'host': '主机', 'url': 'URL', 'body': 'Body', 'type': '类型',
        'duration': '耗时', 'time': '时间',
    }
    COL_LABELS_EN = {
        'seq': '#', 'status': 'Result', 'protocol': 'Protocol', 'method': 'Method',
        'host': 'Host', 'url': 'URL', 'body': 'Body', 'type': 'Type',
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
        self._active_filters = list(self._prefs.get('active_filters') or [FILTER_ALL])
        self._sort_key = self._prefs.get('sort_key') or 'time'
        self._sort_desc = bool(self._prefs.get('sort_desc', True))
        self._follow_latest = True
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._rebuild_table)
        self._wait_hint_timer = QTimer(self)
        self._wait_hint_timer.setSingleShot(True)
        self._wait_hint_timer.setInterval(10000)
        self._wait_hint_timer.timeout.connect(self._on_wait_hint)
        self._status_tick = QTimer(self)
        self._status_tick.setInterval(2000)
        self._status_tick.timeout.connect(self._refresh_live_status)
        self._sensitive_copy_warned = False
        # 跨线程投递：QueuedConnection
        self._sig_capture_record.connect(self._ingest_record)
        self._sig_capture_error.connect(self._on_ie_error)
        self._sig_capture_stopped.connect(self._on_proxy_stopped)
        self._setup_ui()
        self._reload_config_ui()
        self.set_language(language)
        self._apply_column_visibility()
        self._apply_mode_ui()
        # 离屏/测试环境跳过延时恢复提示
        if os.environ.get('QT_QPA_PLATFORM', '').lower() != 'offscreen':
            QTimer.singleShot(200, self._check_orphan_proxy_snapshot)

    # ── UI ──────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.offline_pill = QLabel()
        self.offline_pill.setObjectName('offline-pill')
        header, self.page_title, self.page_subtitle = make_page_header(
            '接口排查',
            '抓 HTTP / HTTPS 请求 · 列表看 URL · 仅内存',
            'api-debug',
            trailing=self.offline_pill,
        )
        root.addWidget(header)

        # 连接控制区：只要开始/停止抓包，不暴露模式/证书/代理术语
        conn = QFrame()
        apply_surface(conn, 'card')
        conn.setObjectName('iface-conn-zone')
        cl = QVBoxLayout(conn)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)

        # 兼容旧属性（隐藏，逻辑代码仍可引用）
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

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.connect_btn = QPushButton()
        apply_button(self.connect_btn, 'primary', compact=True, icon='external-open', icon_size=16)
        self.connect_btn.clicked.connect(self._connect_or_start)
        row2.addWidget(self.connect_btn)
        self.stop_btn = QPushButton()
        apply_button(self.stop_btn, 'ghost', compact=True, icon='lock', icon_size=16)
        self.stop_btn.clicked.connect(self._stop_listen)
        self.stop_btn.setEnabled(False)
        row2.addWidget(self.stop_btn)
        self.test_listen_btn = QPushButton()
        apply_button(self.test_listen_btn, 'ghost', compact=True, icon='terminal', icon_size=16)
        self.test_listen_btn.clicked.connect(self._test_listen_loopback)
        row2.addWidget(self.test_listen_btn)
        self.restore_proxy_btn = QPushButton()
        apply_button(self.restore_proxy_btn, 'ghost', compact=True, icon='refresh', icon_size=16)
        self.restore_proxy_btn.setToolTip('若抓包异常退出导致网页/接口不通，点此恢复系统代理')
        self.restore_proxy_btn.clicked.connect(self._manual_restore_proxy)
        row2.addWidget(self.restore_proxy_btn)
        row2.addStretch(1)
        cl.addLayout(row2)

        self.status_label = QLabel()
        self.status_label.setObjectName('field-hint')
        self.status_label.setWordWrap(True)
        cl.addWidget(self.status_label)
        self.live_status = QLabel()
        self.live_status.setObjectName('field-hint')
        self.live_status.setWordWrap(True)
        cl.addWidget(self.live_status)
        root.addWidget(conn)

        # 会话工具条
        tools = QFrame()
        apply_surface(tools, 'zone')
        tools.setObjectName('iface-session-toolbar')
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(6)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText('搜索 URL / host / path / method / 状态…')
        self.filter_edit.textChanged.connect(lambda *_: self._search_timer.start())
        tl.addWidget(self.filter_edit, 1)

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

        self.session_count = QLabel('0 / 0')
        self.session_count.setObjectName('field-hint')
        tl.addWidget(self.session_count)

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
        self.clear_list_btn = QPushButton()
        apply_button(self.clear_list_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_list_btn.clicked.connect(self._confirm_clear_session)
        tl.addWidget(self.clear_list_btn)
        root.addWidget(tools)

        # 中部：列表 + 详情
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setObjectName('iface-mid-splitter')
        self.mid_splitter.setChildrenCollapsible(False)
        self.mid_splitter.setHandleWidth(10)
        self.mid_splitter.setOpaqueResize(True)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
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
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        # 超长 URL/主机：不截断为「被挤扁」，允许左右滚动看全
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(False)
        header_view.setSectionsMovable(True)
        header_view.setMinimumSectionSize(40)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 短列 Interactive；URL 列 Stretch 吃掉尾部空白，表头随视口变化
        _default_w = {
            'seq': 44, 'status': 64, 'protocol': 56, 'method': 64,
            'host': 160, 'url': 360, 'body': 72, 'type': 72,
            'duration': 72, 'time': 96,
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
        self.mid_splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName('module-tabs')
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        # Tab 0 概览
        self.overview_page = QWidget()
        ov = QVBoxLayout(self.overview_page)
        ov.setContentsMargins(0, 8, 0, 0)
        ov_tools = QHBoxLayout()
        self.reveal_cb = QCheckBox()
        self.reveal_cb.toggled.connect(self._on_reveal)
        ov_tools.addWidget(self.reveal_cb)
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
        self.overview_edit.setMinimumHeight(180)
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
        req_tools.addStretch(1)
        rq.addLayout(req_tools)
        self.req_detail = QPlainTextEdit()
        self.req_detail.setReadOnly(True)
        self.req_detail.setObjectName('iface-detail-edit')
        self.req_detail.setFont(mono)
        self.req_detail.setMinimumHeight(180)
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
        resp_tools.addStretch(1)
        rs.addLayout(resp_tools)
        self.resp_detail = QPlainTextEdit()
        self.resp_detail.setReadOnly(True)
        self.resp_detail.setObjectName('iface-detail-edit')
        self.resp_detail.setFont(mono)
        self.resp_detail.setMinimumHeight(180)
        rs.addWidget(self.resp_detail, 1)
        self.detail_tabs.addTab(self.resp_page, '响应')

        # Tab 3 请求测试（Postman 风格 · 按已保存环境发送）
        self.draft_page = QWidget()
        self.draft_page.setAcceptDrops(True)
        self.draft_page.installEventFilter(self)
        dl = QVBoxLayout(self.draft_page)
        dl.setContentsMargins(0, 8, 0, 0)
        dl.setSpacing(6)
        self.draft_badge = QLabel()
        self.draft_badge.setObjectName('offline-pill')
        dl.addWidget(self.draft_badge)

        self.include_auth_cb = QCheckBox()
        self.include_auth_cb.hide()
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

        # 环境：下拉选择已保存地址 + 当前 base 可编辑 + 保存/管理
        env_row = QHBoxLayout()
        self.target_label = QLabel('环境')
        env_row.addWidget(self.target_label)
        self.local_target_combo = QComboBox()
        self.local_target_combo.setMinimumWidth(160)
        self.local_target_combo.currentIndexChanged.connect(self._on_env_selected)
        env_row.addWidget(self.local_target_combo, 1)
        self.add_target_btn = QPushButton()
        apply_button(self.add_target_btn, 'secondary', compact=True, icon='add', icon_size=14)
        self.add_target_btn.clicked.connect(self._add_local_target)
        env_row.addWidget(self.add_target_btn)
        self.edit_target_btn = QPushButton()
        apply_button(self.edit_target_btn, 'ghost', compact=True, icon='edit', icon_size=14)
        self.edit_target_btn.clicked.connect(self._edit_local_target)
        env_row.addWidget(self.edit_target_btn)
        self.del_target_btn = QPushButton()
        apply_button(self.del_target_btn, 'ghost', compact=True, icon='delete', icon_size=14)
        self.del_target_btn.clicked.connect(self._delete_local_target)
        env_row.addWidget(self.del_target_btn)
        dl.addLayout(env_row)

        base_row = QHBoxLayout()
        self.base_label = QLabel('Base')
        base_row.addWidget(self.base_label)
        self.rt_base_edit = QLineEdit()
        self.rt_base_edit.setText('http://localhost:18031')
        self.rt_base_edit.setPlaceholderText('http://host:port（可保存为环境）')
        base_row.addWidget(self.rt_base_edit, 1)
        self.rt_save_env_btn = QPushButton()
        apply_button(self.rt_save_env_btn, 'secondary', compact=True, icon='save', icon_size=14)
        self.rt_save_env_btn.clicked.connect(self._rt_save_current_as_env)
        base_row.addWidget(self.rt_save_env_btn)
        self.rt_fill_btn = QPushButton()
        apply_button(self.rt_fill_btn, 'secondary', compact=True, icon='refresh', icon_size=14)
        self.rt_fill_btn.clicked.connect(self._rt_fill_from_selection)
        base_row.addWidget(self.rt_fill_btn)
        dl.addLayout(base_row)

        method_row = QHBoxLayout()
        self.rt_method = QComboBox()
        self.rt_method.addItems(['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
        size_combo(self.rt_method, 'sm')
        method_row.addWidget(self.rt_method)
        self.rt_url = QLineEdit()
        self.rt_url.setPlaceholderText('http://host:port/path')
        method_row.addWidget(self.rt_url, 1)
        self.rt_send_btn = QPushButton()
        apply_button(self.rt_send_btn, 'primary', compact=True, icon='external-open', icon_size=16)
        self.rt_send_btn.clicked.connect(self._rt_send)
        method_row.addWidget(self.rt_send_btn)
        dl.addLayout(method_row)

        self.rt_tabs = QTabWidget()
        self.rt_tabs.setDocumentMode(True)
        self.rt_headers = QPlainTextEdit()
        self.rt_headers.setPlaceholderText('Header-Name: value\nContent-Type: application/json')
        self.rt_headers.setFont(mono)
        self.rt_headers.setMaximumHeight(100)
        self.rt_tabs.addTab(self.rt_headers, 'Headers')
        self.rt_params = QPlainTextEdit()
        self.rt_params.setPlaceholderText('key=value\npage=1')
        self.rt_params.setFont(mono)
        self.rt_params.setMaximumHeight(100)
        self.rt_tabs.addTab(self.rt_params, 'Params')
        self.rt_body = QPlainTextEdit()
        self.rt_body.setPlaceholderText('请求 Body（优先解密后的明文）')
        self.rt_body.setFont(mono)
        self.rt_body.setMinimumHeight(80)
        self.rt_tabs.addTab(self.rt_body, 'Body')
        dl.addWidget(self.rt_tabs)

        io_row = QHBoxLayout()
        self.export_detail_btn = QPushButton()
        apply_button(self.export_detail_btn, 'secondary', compact=True, icon='export', icon_size=16)
        self.export_detail_btn.clicked.connect(self._export_session_detail)
        io_row.addWidget(self.export_detail_btn)
        self.rt_import_btn = QPushButton()
        apply_button(self.rt_import_btn, 'secondary', compact=True, icon='import', icon_size=16)
        self.rt_import_btn.clicked.connect(self._rt_import_file)
        io_row.addWidget(self.rt_import_btn)
        self.rt_req_copy_btn = QPushButton()
        apply_button(self.rt_req_copy_btn, 'ghost', compact=True, icon='copy', icon_size=14)
        self.rt_req_copy_btn.clicked.connect(self._rt_copy_request_body)
        io_row.addWidget(self.rt_req_copy_btn)
        self.rt_req_format_btn = QPushButton()
        apply_button(self.rt_req_format_btn, 'secondary', compact=True, icon='json', icon_size=14)
        self.rt_req_format_btn.clicked.connect(self._rt_send_request_to_format)
        io_row.addWidget(self.rt_req_format_btn)
        self.rt_resp_copy_btn = QPushButton()
        apply_button(self.rt_resp_copy_btn, 'ghost', compact=True, icon='copy', icon_size=14)
        self.rt_resp_copy_btn.clicked.connect(self._rt_copy_response_body)
        io_row.addWidget(self.rt_resp_copy_btn)
        self.rt_resp_format_btn = QPushButton()
        apply_button(self.rt_resp_format_btn, 'secondary', compact=True, icon='json', icon_size=14)
        self.rt_resp_format_btn.clicked.connect(self._rt_send_response_to_format)
        io_row.addWidget(self.rt_resp_format_btn)
        io_row.addStretch(1)
        dl.addLayout(io_row)

        resp_head = QHBoxLayout()
        self.rt_resp_label = QLabel('响应')
        self.rt_resp_label.setObjectName('field-caption')
        resp_head.addWidget(self.rt_resp_label)
        self.rt_resp_meta = QLabel('')
        self.rt_resp_meta.setObjectName('field-hint')
        self.rt_resp_meta.setWordWrap(True)
        resp_head.addWidget(self.rt_resp_meta, 1)
        dl.addLayout(resp_head)
        # 响应区：摘要 + 完整 Body（不截断）
        self.draft_preview = QPlainTextEdit()
        self.draft_preview.setReadOnly(True)
        self.draft_preview.setObjectName('iface-draft-preview')
        self.draft_preview.setFont(mono)
        self.draft_preview.setMinimumHeight(140)
        self.draft_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.draft_preview.setPlaceholderText(
            '发送后此处显示完整响应 Body（不截断）；上方可一键送格式工具'
        )
        # 取消块数限制，保证大报文也能看全
        try:
            self.draft_preview.document().setMaximumBlockCount(0)
        except Exception:
            pass
        dl.addWidget(self.draft_preview, 1)
        self._rt_last_request_body = ''
        self._rt_last_response_body = ''
        self._rt_last_response_headers = {}
        self.detail_tabs.addTab(self.draft_page, '请求测试')
        self.detail_tabs.currentChanged.connect(self._on_detail_tab_changed)

        rl.addWidget(self.detail_tabs, 1)
        self.mid_splitter.addWidget(right)
        sizes = (self._prefs.get('splitter_sizes') or {}).get('standard') or [420, 580]
        self.mid_splitter.setSizes(sizes)
        self.mid_splitter.splitterMoved.connect(self._save_splitter_sizes)
        root.addWidget(self.mid_splitter, 1)

        self.loading = AuroraProgress(self)
        self._refresh_browsers()
        self._rebuild_table()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading'):
            self.loading.place_overlay(self)

    # ── 列 / 筛选 ────────────────────────────────────
    def _rebuild_column_menu(self):
        self._cols_menu.clear()
        visible = set(self._prefs.get('visible_columns') or [])
        labels = self.COL_LABELS_ZH if self.language == 'zh' else self.COL_LABELS_EN
        for key in COLUMN_KEYS:
            act = QAction(labels.get(key, key), self._cols_menu)
            act.setCheckable(True)
            core = ('seq', 'status', 'method', 'host', 'url')
            act.setChecked(key in visible or key in core)
            if key in core:
                act.setEnabled(False)
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

    def _apply_column_visibility(self):
        visible = set(self._prefs.get('visible_columns') or [])
        widths = self._prefs.get('column_widths') or {}
        core = ('seq', 'status', 'method', 'host', 'url')
        header = self.table.horizontalHeader()
        last_visible = -1
        for i, key in enumerate(COLUMN_KEYS):
            show = key in visible or key in core
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
        self.connect_btn.setText('开始抓包' if zh else 'Start capture')
        self.stop_btn.setText('停止抓包' if zh else 'Stop')
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

    def _connect_or_start(self):
        # 产品面只有抓包一条路
        self._mode = 'proxy'
        self._start_local_proxy(ie_mode=False)

    def _ensure_capture_ready_silently(self):
        """HTTPS 解密所需 CA：启动时静默准备，不打扰用户。"""
        try:
            ensure_mitm_ca_exists = __import__(
                'tools.ie_proxy', fromlist=['ensure_mitm_ca_exists']
            ).ensure_mitm_ca_exists
            ensure_mitm_ca_exists()
        except Exception:
            pass
        try:
            cfg = load_interface_debug_config()
            if not (cfg.get('ie_certificate_thumbprint') or '').strip():
                install_user_root_cert()
                self._config = load_interface_debug_config()
        except Exception:
            # 证书失败时 HTTP 仍可抓；HTTPS 可能只有 host
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

    def _start_local_proxy(self, ie_mode: bool = False):
        zh = self.language == 'zh'
        title = '开始抓包' if zh else 'Start capture'
        port = self._current_port()
        self._config['ie_proxy_port'] = port
        save_interface_debug_config(self._config)
        self.loading.start_busy('正在开始抓包…' if zh else 'Starting capture…')
        # HTTPS 解密准备：静默，不出现「安装证书」按钮
        self._ensure_capture_ready_silently()
        source = 'http_capture'
        try:
            from tools.http_capture import HttpCaptureWorker
            worker = HttpCaptureWorker(
                port=port,
                on_record=self._on_ie_record_thread,
                on_error=self._on_ie_error_thread,
                on_stopped=self._on_ie_stopped_thread,
                show_static=True,
                source_label=source,
                apply_system_proxy=True,
            )
            worker.start()
            self._ie_worker = worker
            deadline = time.time() + 14.0
            while time.time() < deadline:
                if getattr(worker, 'ready', False):
                    break
                if getattr(worker, '_stop', None) is not None and worker._stop.is_set():
                    break
                QApplication.processEvents()
                time.sleep(0.05)
            if not getattr(worker, 'ready', False):
                err = '抓包未就绪（端口可能被占用）。请关闭占用后重试。'
                try:
                    worker.stop()
                except Exception:
                    pass
                self._ie_worker = None
                self.loading.fail(err)
                show_warning(self, title, err)
                return
            # 自检：经本机代理打一条 HTTP，验证「代理→抓取引擎→列表」整条链路
            self._probe_capture_pipeline(port)
            self._mark_listen_success(
                f'抓包中 · 系统代理已指向 127.0.0.1:{port} · 请用浏览器打开业务页（必要时重启浏览器）'
            )
            self.loading.finish('抓包已开始' if zh else 'Capture started')
        except Exception as exc:
            self.loading.fail(str(exc))
            show_warning(self, title, str(exc))
            try:
                restore_proxy_from_snapshot()
            except Exception:
                pass

    def _probe_capture_pipeline(self, port: int):
        """经本地代理发一条 HTTP 探测；成功则列表至少出现探测会话。"""
        def _run():
            try:
                import urllib.request
                proxy = urllib.request.ProxyHandler({
                    'http': f'http://127.0.0.1:{int(port)}',
                    'https': f'http://127.0.0.1:{int(port)}',
                })
                opener = urllib.request.build_opener(proxy)
                # 访问公网探测：若内网不通，仍会留下 CONNECT/失败记录；优先本机无效端口无意义
                try:
                    opener.open('http://example.com/', timeout=4)
                except Exception:
                    # 即使失败，mitm 也应留下请求记录
                    pass
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
        # Fiddler 式状态：正在抓取 / 会话条数 / 最近一条时间
        self.live_status.setText(
            f'抓包中 · 本机 HTTP/HTTPS · 会话 {n} · 最近 {last}'
            if zh else
            f'Capturing · HTTP/HTTPS · sessions {n} · last {last}'
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

    def _on_ie_record_thread(self, rec):
        # 后台线程必须用信号投递主线程（QTimer.singleShot 跨线程不可靠）
        try:
            self._sig_capture_record.emit(dict(rec or {}))
        except Exception:
            pass

    def _on_ie_error_thread(self, msg):
        try:
            self._sig_capture_error.emit(str(msg or ''))
        except Exception:
            pass

    def _on_ie_stopped_thread(self):
        try:
            self._sig_capture_stopped.emit()
        except Exception:
            pass

    def _on_proxy_stopped(self):
        self._listening = False
        self._channel_ready = False
        self._set_listening_ui(False)
        self._wait_hint_timer.stop()
        self._status_tick.stop()

    def _on_ie_error(self, msg):
        self.status_label.setText(f'代理错误：{msg}')
        show_warning(self, '本机代理', msg)
        self._listening = False
        self._channel_ready = False
        self._set_listening_ui(False)
        self._wait_hint_timer.stop()
        self._status_tick.stop()
        if self._ie_worker:
            try:
                self._ie_worker.stop()
            except Exception:
                pass
            self._ie_worker = None

    def _ingest_record(self, rec: dict):
        rid = rec.get('id') or ''
        if not rid:
            return
        prev_selected = self._selected_id
        # 合并同 id（request→response）
        old = self._records_by_id.get(rid) or {}
        merged = dict(old)
        merged.update({k: v for k, v in rec.items() if v is not None and v != ''})
        # status 允许 0；failure 允许覆盖
        if 'status' in rec:
            merged['status'] = rec.get('status')
        if rec.get('failure'):
            merged['failure'] = rec.get('failure')
        if rec.get('response_body') is not None:
            merged['response_body'] = rec.get('response_body')
        if rec.get('request_body') is not None and rec.get('request_body') != '':
            merged['request_body'] = rec.get('request_body')
        # Fiddler 式会话序号：首次入库编号
        if 'seq' not in old:
            merged['seq'] = len(self._records_by_id) + 1
        else:
            merged['seq'] = old.get('seq')
        self._records_by_id[rid] = merged
        self._records = list(self._records_by_id.values())
        self._last_request_at = time.time()
        self._rebuild_table()
        if prev_selected and prev_selected in self._records_by_id:
            self._selected_id = prev_selected
        self._refresh_live_status()
        if self._records and self.recheck_btn.isVisible():
            self.recheck_btn.hide()

    def _stop_listen(self):
        self.loading.start_busy('正在停止抓包…')
        self._wait_hint_timer.stop()
        self._status_tick.stop()
        try:
            if self._cdp_session:
                try:
                    self._cdp_session.stop()
                except Exception:
                    pass
                self._cdp_session = None
            if self._ie_worker:
                try:
                    # 停止引擎但保留面板会话列表（清空请用「清空」按钮）
                    if hasattr(self._ie_worker, 'clear_session'):
                        # 先摘掉 stop 内清空对 UI 的影响：仅停代理
                        pass
                    self._ie_worker.stop()
                except Exception:
                    pass
                self._ie_worker = None
        finally:
            # 不清空列表；再确保系统代理已恢复
            self._listening = False
            self._channel_ready = False
            self._set_listening_ui(False)
            try:
                from tools.ie_proxy import ensure_system_proxy_safe
                ensure_system_proxy_safe(reason='stop_listen')
            except Exception:
                pass
            n = len(self._records)
            self.loading.finish('已停止')
            self.status_label.setText(
                f'已停止抓包 · 系统代理已恢复 · 会话保留 {n} 条（可继续导出/请求测试）'
            )
            self.live_status.setText('')
            self.recheck_btn.hide()

    def _set_listening_ui(self, active: bool):
        self.connect_btn.setEnabled(not active)
        self.stop_btn.setEnabled(active)
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
                '点「开始抓包」→ 完全退出并重新打开 Chrome/Edge → 再访问业务页。\n'
                '列表会显示 # / 结果 / 协议 / 方法 / 主机 / URL。'
                if zh else
                'Start capture → fully restart Chrome/Edge → open your app pages.'
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

    # ── IE 证书 ──────────────────────────────────────
    def _install_ie_cert(self):
        zh = self.language == 'zh'
        if not confirm_action(
            self, '安装本机抓包证书' if zh else 'Install CA',
            '将把 mitmproxy 根证书安装到当前用户受信任根证书库。' if zh else 'Install mitmproxy CA for current user.',
            confirm_text='安装证书' if zh else 'Install', danger=True,
        ):
            return
        self.loading.start_busy('正在安装证书…')
        try:
            thumb = install_user_root_cert()
            self._config = load_interface_debug_config()
            self.loading.finish('证书已安装')
            show_success(self, '证书', f'已安装，指纹 {thumb[:16]}…')
        except Exception as exc:
            self.loading.fail(str(exc))
            show_warning(self, '证书', str(exc))

    def _remove_ie_cert(self):
        zh = self.language == 'zh'
        cfg = load_interface_debug_config()
        thumb = (cfg.get('ie_certificate_thumbprint') or '').strip()
        if not thumb:
            show_info(self, '证书', '没有已记录的抓包证书指纹')
            return
        if not confirm_action(
            self, '移除本机抓包证书', f'将仅删除指纹 {thumb} 对应的证书。',
            confirm_text='移除证书', danger=True,
        ):
            return
        try:
            remove_recorded_cert(thumb)
            self._config = load_interface_debug_config()
            show_success(self, '证书', '已移除')
        except Exception as exc:
            show_warning(self, '证书', str(exc))

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
            url_col = url_path_display(rec)
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
            return
        url = mask_url_query(rec.get('url') or '', self._reveal_sensitive)
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
        from tools.iface_request_test import fill_request_form_from_item, plaintext_bodies
        base = self._selected_base_url()
        item = plaintext_bodies(rec)
        form = fill_request_form_from_item(item, base)
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
        self.rt_headers.setPlainText(form.get('headers_text') or '')
        self.rt_params.setPlainText(form.get('params_text') or '')
        self.rt_body.setPlainText(form.get('body') or '')
        self._rt_last_request_body = form.get('body') or ''
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
            body = self.rt_body.toPlainText() or ''
        except RequestTestError as exc:
            show_warning(self, '请求测试', str(exc))
            return
        except Exception as exc:
            show_warning(self, '请求测试', str(exc))
            return

        self._rt_send_meta = {'method': method, 'url': url}
        self._rt_last_request_body = body
        self.rt_send_btn.setEnabled(False)
        self.loading.start_busy('正在发送请求…')
        # 先刷新界面再进后台线程，确保 Loading 可见
        QApplication.processEvents()

        worker = _RequestTestWorker(method, url, headers, body, parent=self)
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
        finally:
            self.rt_send_btn.setEnabled(True)
            self._rt_worker = None

    def _rt_send_failed(self, message: str):
        try:
            self.loading.fail(message or '请求失败')
            show_warning(self, '请求测试', message or '请求失败')
        finally:
            self.rt_send_btn.setEnabled(True)
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

    def apply_layout_mode(self, mode, low_height=False):
        self._layout_mode = mode
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        prev_orient = self.mid_splitter.orientation()
        apply_splitter_orientation(self.mid_splitter, mode, min_editor=editor_min_height())
        # 方向切换时用对应模式尺寸；不反转左右顺序
        self.mid_splitter.setChildrenCollapsible(False)
        self.mid_splitter.setOpaqueResize(True)
        sizes = (self._prefs.get('splitter_sizes') or {}).get(mode)
        if sizes and len(sizes) >= 2:
            a, b = int(sizes[0]), int(sizes[1])
            # 防止历史脏数据导致「反向」观感（一侧过小）
            if a < 120:
                a = 320 if self.mid_splitter.orientation() == Qt.Orientation.Horizontal else 200
            if b < 120:
                b = 480 if self.mid_splitter.orientation() == Qt.Orientation.Horizontal else 280
            self.mid_splitter.setSizes([a, b])
        # 横向：左列表 / 右详情；保持 stretch 合理
        if self.mid_splitter.orientation() == Qt.Orientation.Horizontal:
            self.mid_splitter.setStretchFactor(0, 1)
            self.mid_splitter.setStretchFactor(1, 1)
        else:
            self.mid_splitter.setStretchFactor(0, 1)
            self.mid_splitter.setStretchFactor(1, 2)
        _ = prev_orient  # 保留变量便于后续差异处理
        # 任何断点都只保留抓包按钮，不恢复模式/证书入口
        self._apply_mode_ui()
        self.connect_btn.show()
        self.stop_btn.show()
        self.test_listen_btn.show()
        for edit in (self.overview_edit, self.req_detail, self.resp_detail, self.draft_preview):
            edit.setMinimumHeight(editor_min_height())

    # ── 语言 / 清理 ──────────────────────────────────
    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('接口排查' if zh else 'API Debug')
        self.page_subtitle.setText(
            '抓 HTTP / HTTPS 请求 · 列表看 URL · 仅内存' if zh else
            'Capture HTTP/HTTPS · URL list · memory only'
        )
        self.offline_pill.setText('● 本地' if zh else '● Local')
        self.connect_btn.setText('开始抓包' if zh else 'Start capture')
        self.stop_btn.setText('停止抓包' if zh else 'Stop')
        self.test_listen_btn.setText('测试' if zh else 'Test')
        self.test_listen_btn.setToolTip(
            '本机探测，确认抓包链路可用' if zh else 'Loopback probe'
        )
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
        self.clear_list_btn.setText('清空' if zh else 'Clear')
        self.cols_btn.setText('列设置' if zh else 'Columns')
        self._rebuild_column_menu()
        self.detail_tabs.setTabText(0, '概览' if zh else 'Overview')
        self.detail_tabs.setTabText(1, '请求' if zh else 'Request')
        self.detail_tabs.setTabText(2, '响应' if zh else 'Response')
        self.detail_tabs.setTabText(3, '请求测试' if zh else 'Request Test')
        self.reveal_cb.setText('显示敏感内容' if zh else 'Reveal secrets')
        self.copy_safe_url_btn.setText('复制安全 URL' if zh else 'Copy safe URL')
        self.copy_req_btn.setText('复制请求' if zh else 'Copy request')
        self.format_req_btn.setText('送格式工具' if zh else 'Format tools')
        self.gateway_req_btn.setText('送入加解密' if zh else 'Crypto')
        self.copy_resp_btn.setText('复制响应' if zh else 'Copy response')
        self.format_resp_btn.setText('送格式工具' if zh else 'Format tools')
        self.gateway_resp_btn.setText('送入加解密' if zh else 'Crypto')
        self.draft_badge.setText(
            '请求测试 · 按环境发送' if zh else
            'Request test · by environment'
        )
        self.target_label.setText('环境' if zh else 'Environment')
        if hasattr(self, 'base_label'):
            self.base_label.setText('Base')
        if hasattr(self, 'rt_fill_btn'):
            self.rt_fill_btn.setText('从会话填充' if zh else 'Fill from session')
            self.rt_send_btn.setText('发送' if zh else 'Send')
            self.export_detail_btn.setText('导出明细' if zh else 'Export detail')
            self.rt_import_btn.setText('导入明细' if zh else 'Import')
            self.rt_resp_label.setText('响应' if zh else 'Response')
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
        if hasattr(self, 'add_target_btn'):
            self.add_target_btn.setToolTip('新增环境' if zh else 'Add environment')
            self.edit_target_btn.setToolTip('编辑环境' if zh else 'Edit environment')
            self.del_target_btn.setToolTip('删除环境' if zh else 'Delete environment')
        if hasattr(self, 'export_list_btn'):
            self.export_list_btn.setText('导出明细' if zh else 'Export')
        self.draft_hint.setText(
            '选择或保存环境 Base（scheme://host:port），从会话填充时自动替换 host，保留 path/query。'
            if zh else
            'Pick a saved environment base; fill rewrites host and keeps path/query.'
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
