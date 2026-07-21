# -*- coding: utf-8 -*-
"""接口排查中心：Fiddler 式工作台（Chromium CDP + IE 本机代理）。

报文仅内存；只生成验证草稿，不发送业务请求。
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
    format_size, host_path_display, is_failed, pretty_body, query_pairs,
    response_size_bytes, split_cookies,
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

    open_gateway = pyqtSignal(str)
    open_format_json = pyqtSignal(str)
    open_format_xml = pyqtSignal(str)

    COL_LABELS_ZH = {
        'status': '状态', 'method': '方法', 'path': '接口路径', 'duration': '耗时',
        'type': '类型', 'time': '时间', 'size': '大小', 'source': '来源',
    }
    COL_LABELS_EN = {
        'status': 'Status', 'method': 'Method', 'path': 'Path', 'duration': 'Time',
        'type': 'Type', 'time': 'At', 'size': 'Size', 'source': 'Src',
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
        # 默认：本机通用代理（无需先选浏览器）
        self._mode = str(self._prefs.get('listen_mode') or 'proxy')
        if self._mode not in ('proxy', 'chromium', 'ie'):
            self._mode = 'proxy'
        self._reveal_sensitive = False
        self._show_static = bool(self._prefs.get('show_static'))
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
            '本机监听 · 仅内存 · 草稿验证',
            'api-debug',
            trailing=self.offline_pill,
        )
        root.addWidget(header)

        # 连接控制区
        conn = QFrame()
        apply_surface(conn, 'card')
        conn.setObjectName('iface-conn-zone')
        cl = QVBoxLayout(conn)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.mode_label = QLabel()
        row1.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        size_combo(self.mode_combo, 'md')
        # 0 本机通用代理(默认) · 1 Chromium CDP 高级 · 2 IE 兼容
        self.mode_combo.addItems(['本机通用代理', 'Chromium CDP（高级）', 'IE 代理（兼容）'])
        mode_index = {'proxy': 0, 'chromium': 1, 'ie': 2}.get(self._mode, 0)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(mode_index)
        self.mode_combo.blockSignals(False)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row1.addWidget(self.mode_combo)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName('field-hint')
        self.mode_hint.setWordWrap(True)
        self.browser_combo = QComboBox()
        size_combo(self.browser_combo, 'lg')
        self.browser_combo.setMinimumWidth(240)
        self.browser_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row1.addWidget(self.browser_combo, 1)
        self.refresh_browsers_btn = QPushButton()
        apply_button(self.refresh_browsers_btn, 'ghost', compact=True, icon='refresh', icon_size=16)
        self.refresh_browsers_btn.clicked.connect(self._refresh_browsers)
        row1.addWidget(self.refresh_browsers_btn)
        self.pick_browser_btn = QPushButton()
        apply_button(self.pick_browser_btn, 'secondary', compact=True, icon='folder-open', icon_size=16)
        self.pick_browser_btn.clicked.connect(self._pick_browser)
        row1.addWidget(self.pick_browser_btn)
        cl.addLayout(row1)
        cl.addWidget(self.mode_hint)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.port_label = QLabel()
        row2.addWidget(self.port_label)
        self.port_edit = QLineEdit()
        self.port_edit.setMaximumWidth(90)
        # 默认通用代理端口
        default_port = self._config.get('ie_proxy_port') or 8899
        if self._mode == 'chromium':
            default_port = self._config.get('debug_port') or 9222
        self.port_edit.setText(str(default_port))
        row2.addWidget(self.port_edit)
        self.launch_btn = QPushButton()
        apply_button(self.launch_btn, 'primary', compact=True, icon='external-open', icon_size=16)
        self.launch_btn.clicked.connect(self._launch_browser)
        row2.addWidget(self.launch_btn)
        self.connect_btn = QPushButton()
        apply_button(self.connect_btn, 'secondary', compact=True, icon='unlock', icon_size=16)
        self.connect_btn.clicked.connect(self._connect_or_start)
        row2.addWidget(self.connect_btn)
        self.stop_btn = QPushButton()
        apply_button(self.stop_btn, 'ghost', compact=True, icon='lock', icon_size=16)
        self.stop_btn.clicked.connect(self._stop_listen)
        self.stop_btn.setEnabled(False)
        row2.addWidget(self.stop_btn)
        self.recheck_btn = QPushButton()
        apply_button(self.recheck_btn, 'ghost', compact=True, icon='refresh', icon_size=16)
        self.recheck_btn.clicked.connect(self._recheck_channel)
        self.recheck_btn.hide()
        row2.addWidget(self.recheck_btn)
        self.target_combo = QComboBox()
        size_combo(self.target_combo, 'lg')
        self.target_combo.setMinimumWidth(180)
        row2.addWidget(self.target_combo, 1)
        self.ie_install_cert_btn = QPushButton()
        apply_button(self.ie_install_cert_btn, 'secondary', compact=True, icon='shield-key', icon_size=16)
        self.ie_install_cert_btn.clicked.connect(self._install_ie_cert)
        self.ie_install_cert_btn.hide()
        row2.addWidget(self.ie_install_cert_btn)
        self.ie_remove_cert_btn = QPushButton()
        apply_button(self.ie_remove_cert_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.ie_remove_cert_btn.clicked.connect(self._remove_ie_cert)
        self.ie_remove_cert_btn.hide()
        row2.addWidget(self.ie_remove_cert_btn)
        self.conn_more_btn = QToolButton()
        self.conn_more_btn.setObjectName('responsive-more-btn')
        self.conn_more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._conn_more_menu = QMenu(self.conn_more_btn)
        self.conn_more_btn.setMenu(self._conn_more_menu)
        self.conn_more_btn.hide()
        row2.addWidget(self.conn_more_btn)
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

        self.clear_list_btn = QPushButton()
        apply_button(self.clear_list_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_list_btn.clicked.connect(self._confirm_clear_session)
        tl.addWidget(self.clear_list_btn)
        root.addWidget(tools)

        # 中部：列表 + 详情
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setChildrenCollapsible(False)
        self.mid_splitter.setHandleWidth(8)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        self.table = QTableWidget(0, len(COLUMN_KEYS))
        self.table.setObjectName('iface-request-table')
        self.table.setHorizontalHeaderLabels([self.COL_LABELS_ZH[k] for k in COLUMN_KEYS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(False)
        header_view.setSectionsMovable(False)
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        path_idx = COLUMN_KEYS.index('path')
        header_view.setSectionResizeMode(path_idx, QHeaderView.ResizeMode.Stretch)
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

        # Tab 3 验证草稿
        self.draft_page = QWidget()
        dl = QVBoxLayout(self.draft_page)
        dl.setContentsMargins(0, 8, 0, 0)
        self.draft_badge = QLabel()
        self.draft_badge.setObjectName('offline-pill')
        dl.addWidget(self.draft_badge)
        drow = QHBoxLayout()
        self.target_label = QLabel()
        drow.addWidget(self.target_label)
        self.local_target_combo = QComboBox()
        size_combo(self.local_target_combo, 'md')
        drow.addWidget(self.local_target_combo, 1)
        self.add_target_btn = QPushButton()
        apply_button(self.add_target_btn, 'ghost', compact=True, icon='add', icon_size=16)
        self.add_target_btn.clicked.connect(self._add_local_target)
        drow.addWidget(self.add_target_btn)
        self.edit_target_btn = QPushButton()
        apply_button(self.edit_target_btn, 'ghost', compact=True, icon='edit', icon_size=16)
        self.edit_target_btn.clicked.connect(self._edit_local_target)
        drow.addWidget(self.edit_target_btn)
        self.del_target_btn = QPushButton()
        apply_button(self.del_target_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.del_target_btn.clicked.connect(self._delete_local_target)
        drow.addWidget(self.del_target_btn)
        dl.addLayout(drow)
        self.include_auth_cb = QCheckBox()
        self.include_auth_cb.setChecked(bool(self._prefs.get('include_auth_in_draft', True)))
        self.include_auth_cb.toggled.connect(self._on_include_auth)
        dl.addWidget(self.include_auth_cb)
        self.draft_preview = QPlainTextEdit()
        self.draft_preview.setReadOnly(True)
        self.draft_preview.setObjectName('iface-draft-preview')
        self.draft_preview.setFont(mono)
        self.draft_preview.setMinimumHeight(120)
        dl.addWidget(self.draft_preview, 1)
        brow = QHBoxLayout()
        self.gen_draft_btn = QPushButton()
        apply_button(self.gen_draft_btn, 'primary', compact=True, icon='refresh', icon_size=16)
        self.gen_draft_btn.clicked.connect(self._refresh_draft_preview)
        brow.addWidget(self.gen_draft_btn)
        self.copy_postman_btn = QPushButton()
        apply_button(self.copy_postman_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_postman_btn.clicked.connect(self._copy_postman)
        brow.addWidget(self.copy_postman_btn)
        self.export_postman_btn = QPushButton()
        apply_button(self.export_postman_btn, 'secondary', compact=True, icon='export', icon_size=16)
        self.export_postman_btn.clicked.connect(self._export_postman)
        brow.addWidget(self.export_postman_btn)
        self.copy_curl_btn = QPushButton()
        apply_button(self.copy_curl_btn, 'secondary', compact=True, icon='terminal', icon_size=16)
        self.copy_curl_btn.clicked.connect(self._copy_curl)
        brow.addWidget(self.copy_curl_btn)
        brow.addStretch(1)
        self.draft_hint = QLabel()
        self.draft_hint.setObjectName('field-hint')
        brow.addWidget(self.draft_hint)
        dl.addLayout(brow)
        self.detail_tabs.addTab(self.draft_page, '验证草稿')

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
            act.setChecked(key in visible or key in ('status', 'method', 'path'))
            if key in ('status', 'method', 'path'):
                act.setEnabled(False)
            act.toggled.connect(lambda checked, k=key: self._toggle_column(k, checked))
            self._cols_menu.addAction(act)

    def _toggle_column(self, key: str, checked: bool):
        visible = list(self._prefs.get('visible_columns') or [])
        if checked and key not in visible:
            visible.append(key)
        if not checked and key in visible and key not in ('status', 'method', 'path'):
            visible.remove(key)
        self._prefs['visible_columns'] = visible
        update_ui_prefs({'visible_columns': visible})
        self._apply_column_visibility()

    def _apply_column_visibility(self):
        visible = set(self._prefs.get('visible_columns') or [])
        widths = self._prefs.get('column_widths') or {}
        for i, key in enumerate(COLUMN_KEYS):
            show = key in visible or key in ('status', 'method', 'path')
            self.table.setColumnHidden(i, not show)
            w = widths.get(key)
            if w and key != 'path':
                self.table.setColumnWidth(i, int(w))

    def _on_column_resized(self, index: int, _old: int, new: int):
        if index < 0 or index >= len(COLUMN_KEYS):
            return
        key = COLUMN_KEYS[index]
        if key == 'path':
            return
        widths = dict(self._prefs.get('column_widths') or {})
        widths[key] = new
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
            self._sort_desc = key in ('time', 'duration', 'size', 'status')
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
            label = f"{t.get('name') or '本地'} · {t.get('base_url') or ''}"
            self.local_target_combo.addItem(label, t.get('id'))
            if t.get('id') == default_id:
                sel = i
        if not targets:
            self.local_target_combo.addItem('（未配置本地地址）', '')
        self.local_target_combo.setCurrentIndex(sel)
        self.local_target_combo.blockSignals(False)

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
        zh = self.language == 'zh'
        proxy_like = self._mode in ('proxy', 'ie')
        cdp = self._mode == 'chromium'
        self.browser_combo.setVisible(cdp)
        self.refresh_browsers_btn.setVisible(cdp)
        self.pick_browser_btn.setVisible(cdp)
        self.launch_btn.setVisible(cdp)
        self.target_combo.setVisible(cdp)
        if self._mode == 'proxy':
            self.port_label.setText('代理端口' if zh else 'Proxy port')
            self.port_edit.setText(str(self._config.get('ie_proxy_port') or 8899))
            self.mode_hint.setText(
                '默认模式：点击「开始监听」后，Chromium / IE / 遵循系统代理的程序将经 127.0.0.1 捕获请求；无需先选浏览器。'
                if zh else
                'Default: start local 127.0.0.1 proxy for Chromium/IE and system-proxy apps.'
            )
        elif self._mode == 'ie':
            self.port_label.setText('IE 代理端口' if zh else 'IE proxy port')
            self.port_edit.setText(str(self._config.get('ie_proxy_port') or 8899))
            self.mode_hint.setText(
                'IE 兼容入口：复用本机 MITM 代理与证书流程；无需选择 Chromium 浏览器。'
                if zh else
                'IE compatibility mode uses the same local MITM proxy.'
            )
        else:
            self.port_label.setText('调试端口' if zh else 'Debug port')
            self.port_edit.setText(str(self._config.get('debug_port') or 9222))
            self.mode_hint.setText(
                '高级：Chromium CDP。可手选 EXE、启动独立调试浏览器或连接已有 127.0.0.1 调试端口。Firefox 首版不支持。'
                if zh else
                'Advanced Chromium CDP. Firefox is not supported in v1.'
            )
        self.ie_install_cert_btn.setVisible(proxy_like)
        self.ie_remove_cert_btn.setVisible(proxy_like)
        self.connect_btn.setText(
            ('开始监听' if zh else 'Start')
            if self._mode != 'ie' else
            ('启用 IE 监听' if zh else 'Start IE proxy')
        )
        self._update_empty_hint()

    def _on_mode_changed(self, index):
        if self._listening:
            self._stop_listen()
        self._mode = self._mode_from_index(index)
        self._prefs['listen_mode'] = self._mode
        update_ui_prefs({'listen_mode': self._mode})
        self._apply_mode_ui()
        self.clear_session()
        self.apply_layout_mode(self._layout_mode, False)

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
        if self._mode == 'chromium':
            self._connect_cdp()
        else:
            self._start_local_proxy(ie_mode=(self._mode == 'ie'))

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
        title = '启用 IE 代理监听' if ie_mode else '启用本机通用代理'
        body = (
            '将临时修改当前用户 Windows 代理为 127.0.0.1，'
            '并可能需要安装本机根证书以解密 HTTPS。\n'
            '适用于 Chromium、IE 及遵循系统代理的程序。\n'
            '停止监听后会自动恢复原代理。报文仅内存，不落盘、不外发。\n'
            '仅绑定 127.0.0.1，不会监听局域网或公网。'
            if zh else
            'Temporarily set current-user Windows proxy to 127.0.0.1 for local capture only.'
        )
        if os.environ.get('QT_QPA_PLATFORM', '').lower() != 'offscreen':
            ok = confirm_action(
                self,
                title if zh else 'Enable local proxy',
                body,
                confirm_text='启用监听' if zh else 'Enable',
                danger=True,
            )
            if not ok:
                return
        port = self._current_port()
        self._config['ie_proxy_port'] = port
        save_interface_debug_config(self._config)
        self.loading.start_busy(
            '正在启动本机代理并等待端口就绪…' if not ie_mode else '正在启动 IE 代理…'
        )
        source = 'ie_proxy' if ie_mode else 'local_proxy'
        try:
            worker = IeProxyWorker(
                port=port,
                on_record=self._on_ie_record_thread,
                on_error=self._on_ie_error_thread,
                on_stopped=self._on_ie_stopped_thread,
                show_static=self._show_static,
                source_label=source,
                apply_system_proxy=True,
            )
            worker.start()
            self._ie_worker = worker
            # 轮询就绪，期间泵事件，避免整窗冻结；未就绪不显示成功
            deadline = time.time() + 12.0
            while time.time() < deadline:
                if getattr(worker, 'ready', False):
                    break
                if getattr(worker, '_stop', None) is not None and worker._stop.is_set():
                    break
                QApplication.processEvents()
                time.sleep(0.05)
            if not getattr(worker, 'ready', False):
                err = '本机代理未在时限内就绪（端口占用、证书或 mitmproxy 依赖问题）'
                try:
                    worker.stop()
                except Exception:
                    pass
                self._ie_worker = None
                self.loading.fail(err)
                show_warning(self, title, err)
                return
            label = (
                f'{"IE 兼容" if ie_mode else "本机通用"}代理监听 127.0.0.1:{port} · '
                f'代理仅当前用户 · 停止后自动恢复'
            )
            self._mark_listen_success(label)
            self.loading.finish('代理通道已建立')
        except Exception as exc:
            self.loading.fail(str(exc))
            show_warning(self, title, str(exc))
            try:
                restore_proxy_from_snapshot()
            except Exception:
                pass

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
            '监听已建立，等待浏览器请求。可点击「检查代理/重新连接」。'
            if zh else
            'Listener ready — waiting for browser requests.'
        )
        self.recheck_btn.show()
        self._update_empty_hint()

    def _refresh_live_status(self):
        if not self._listening:
            self.live_status.setText('')
            return
        zh = self.language == 'zh'
        mode_name = {
            'proxy': '本机通用代理' if zh else 'Local proxy',
            'chromium': 'Chromium CDP' if zh else 'Chromium CDP',
            'ie': 'IE 代理' if zh else 'IE proxy',
        }.get(self._mode, self._mode)
        port = self._current_port()
        n = len(self._records)
        last = (
            datetime.fromtimestamp(self._last_request_at).strftime('%H:%M:%S')
            if self._last_request_at else '—'
        )
        ready = '已就绪' if self._channel_ready else '未就绪'
        self.live_status.setText(
            f'模式 {mode_name} · 127.0.0.1:{port} · 通道{ready} · 已捕获 {n} · 最近请求 {last}'
            if zh else
            f'{mode_name} · 127.0.0.1:{port} · ready={self._channel_ready} · n={n} · last={last}'
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

    def _on_ie_record_thread(self, rec):
        # 禁止后台线程直接操作 QWidget：投递主线程
        QTimer.singleShot(0, lambda r=dict(rec): self._ingest_record(r))

    def _on_ie_error_thread(self, msg):
        QTimer.singleShot(0, lambda: self._on_ie_error(msg))

    def _on_ie_stopped_thread(self):
        QTimer.singleShot(0, self._on_proxy_stopped)

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
        self.loading.start_busy('正在停止监听…')
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
                    self._ie_worker.stop()
                except Exception:
                    pass
                self._ie_worker = None
        finally:
            self.clear_session()
            self._listening = False
            self._channel_ready = False
            self._set_listening_ui(False)
            self.loading.finish('已停止')
            self.status_label.setText('已停止监听，会话已清空')
            self.live_status.setText('')
            self.recheck_btn.hide()

    def _set_listening_ui(self, active: bool):
        self.connect_btn.setEnabled(not active)
        self.launch_btn.setEnabled(not active and self._mode == 'chromium')
        self.stop_btn.setEnabled(active)
        self.mode_combo.setEnabled(not active)
        if not active:
            self.recheck_btn.hide()

    def _update_empty_hint(self):
        zh = self.language == 'zh'
        if self._listening and self._channel_ready and not self._records:
            self.empty_hint.setText(
                '监听已建立，等待浏览器请求…\n请用 Chrome / Edge / IE 打开业务页并操作；HTTPS 首次请安装本机证书。'
                if zh else
                'Listener ready — waiting for browser traffic.'
            )
        elif self._mode == 'chromium':
            self.empty_hint.setText(
                '暂无请求。高级模式：选择 Chromium → 启动或连接调试端口 → 在业务页中操作。'
                if zh else
                'No requests. Advanced CDP: pick Chromium, launch/connect, then use the page.'
            )
        else:
            self.empty_hint.setText(
                '暂无请求。点击「开始监听」后，在浏览器中访问业务页面即可捕获（默认无需选择浏览器）。'
                if zh else
                'No requests yet. Click Start, then browse as usual — no browser pick required.'
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
        # 自动化/离屏测试不弹交互框
        if os.environ.get('QT_QPA_PLATFORM', '').lower() == 'offscreen':
            return
        cfg = load_interface_debug_config()
        snap = cfg.get('proxy_restore_snapshot')
        if not isinstance(snap, dict):
            return
        zh = self.language == 'zh'
        if confirm_action(
            self, '检测到未恢复的代理设置' if zh else 'Unrestored proxy',
            '上次异常退出可能未恢复 Windows 代理。是否立即恢复？' if zh else 'Restore previous proxy settings?',
            confirm_text='一键恢复' if zh else 'Restore', danger=False,
        ):
            try:
                restore_proxy_from_snapshot(snap)
                show_success(self, '代理', '已恢复原代理设置')
            except Exception as exc:
                show_warning(self, '代理', str(exc))

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
            path = host_path_display(rec)
            dur = rec.get('duration_ms')
            dur_s = '—' if dur is None else f'{int(dur)} ms'
            kind = content_kind(rec)
            ts = rec.get('started_at') or time.time()
            tstr = datetime.fromtimestamp(ts).strftime('%H:%M:%S.%f')[:-3]
            size_s = format_size(response_size_bytes(rec))
            src = self._source_label(rec.get('source'))
            vals = [status_s, method, path, dur_s, kind, tstr, size_s, src]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, rec.get('id'))
                if c == 0:
                    if is_failed(rec):
                        item.setForeground(QBrush(danger))
                    elif status is not None:
                        try:
                            if 200 <= int(status) < 300:
                                item.setForeground(QBrush(success))
                        except (TypeError, ValueError):
                            pass
                if c == 1:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    item.setForeground(QBrush(primary))
                if c == 2:
                    item.setToolTip(mask_url_query(rec.get('url') or '', self._reveal_sensitive))
                if c == 3:
                    sev = duration_severity(dur)
                    if sev == 'danger':
                        item.setForeground(QBrush(danger))
                    elif sev == 'warn':
                        item.setForeground(QBrush(warn))
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if c == 4:
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
        self._refresh_draft_preview()

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
            self.open_gateway.emit(body)
            return
        # format
        if _looks_xml(body):
            self.open_format_xml.emit(body)
        else:
            kind, pretty, _err = pretty_body(body)
            self.open_format_json.emit(pretty if kind == 'json' else body)

    # ── 草稿 ─────────────────────────────────────────
    def _selected_base_url(self) -> str:
        tid = self.local_target_combo.currentData()
        for t in self._config.get('local_targets') or []:
            if t.get('id') == tid:
                return t.get('base_url') or ''
        targets = self._config.get('local_targets') or []
        if targets:
            return targets[0].get('base_url') or ''
        return ''

    def _draft_record(self) -> dict | None:
        rec = self._selected_record()
        if not rec:
            return None
        out = dict(rec)
        if not self.include_auth_cb.isChecked():
            headers = {
                k: v for k, v in (rec.get('request_headers') or {}).items()
                if str(k).lower() not in ('authorization', 'cookie', 'proxy-authorization')
            }
            out['request_headers'] = headers
        return out

    def _refresh_draft_preview(self):
        rec = self._draft_record()
        base = self._selected_base_url()
        if not rec or not base:
            self.draft_preview.setPlainText('')
            return
        try:
            rewritten = rewrite_url(rec.get('url') or '', base)
            curl = build_curl(rec, base)
            self.draft_preview.setPlainText(
                f'{(rec.get("method") or "GET").upper()} {rewritten}\n\n'
                f'# 仅生成验证草稿，不会发送请求\n\n{curl}'
            )
        except DraftError as exc:
            self.draft_preview.setPlainText(str(exc))

    def _warn_sensitive_draft(self) -> bool:
        zh = self.language == 'zh'
        return confirm_action(
            self,
            '生成验证草稿' if zh else 'Generate draft',
            (
                '草稿包含 Authorization、Cookie 等敏感信息，仅应导入本机 Postman。\n'
                'PengTools 不会实际发送请求。'
                if zh else
                'Draft may contain secrets. Import only into local Postman.\n'
                'PengTools will not send any request.'
            ),
            confirm_text='继续生成' if zh else 'Continue',
            danger=False,
        )

    def _copy_postman(self):
        rec = self._draft_record()
        base = self._selected_base_url()
        if not rec or not base:
            show_warning(self, '草稿', '请选择请求并配置本地地址')
            return
        if not self._warn_sensitive_draft():
            return
        try:
            payload = build_postman_collection(rec, base)
            QApplication.clipboard().setText(drafts_as_json_text(payload))
            show_success(self, '草稿', 'Postman JSON 已复制')
        except DraftError as exc:
            show_warning(self, '草稿', str(exc))

    def _export_postman(self):
        rec = self._draft_record()
        base = self._selected_base_url()
        if not rec or not base:
            show_warning(self, '草稿', '请选择请求并配置本地地址')
            return
        if not self._warn_sensitive_draft():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, '导出 Postman Collection', 'pengtools_local_draft.json', 'JSON (*.json)',
        )
        if not path:
            return
        try:
            payload = build_postman_collection(rec, base)
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(drafts_as_json_text(payload))
            show_success(self, '草稿', f'已导出：{path}')
        except Exception as exc:
            show_warning(self, '草稿', str(exc))

    def _copy_curl(self):
        rec = self._draft_record()
        base = self._selected_base_url()
        if not rec or not base:
            show_warning(self, '草稿', '请选择请求并配置本地地址')
            return
        if not self._warn_sensitive_draft():
            return
        try:
            QApplication.clipboard().setText(build_curl(rec, base))
            show_success(self, '草稿', 'cURL 已复制')
        except DraftError as exc:
            show_warning(self, '草稿', str(exc))

    def _add_local_target(self):
        from PyQt6.QtWidgets import QInputDialog
        import uuid
        zh = self.language == 'zh'
        name, ok = QInputDialog.getText(self, '本地地址', '名称：' if zh else 'Name:')
        if not ok:
            return
        url, ok = QInputDialog.getText(self, '本地地址', 'base URL (http://host:port)：')
        if not ok:
            return
        try:
            url = validate_base_url(url)
        except DraftError as exc:
            show_warning(self, '本地地址', str(exc))
            return
        item = {'id': uuid.uuid4().hex, 'name': (name or '本地服务').strip(), 'base_url': url}
        self._config.setdefault('local_targets', []).append(item)
        self._config['default_target_id'] = item['id']
        save_interface_debug_config(self._config)
        self._fill_local_targets()

    def _edit_local_target(self):
        from PyQt6.QtWidgets import QInputDialog
        tid = self.local_target_combo.currentData()
        targets = self._config.get('local_targets') or []
        item = next((t for t in targets if t.get('id') == tid), None)
        if not item:
            return
        name, ok = QInputDialog.getText(self, '编辑', '名称：', text=item.get('name') or '')
        if not ok:
            return
        url, ok = QInputDialog.getText(self, '编辑', 'base URL：', text=item.get('base_url') or '')
        if not ok:
            return
        try:
            url = validate_base_url(url)
        except DraftError as exc:
            show_warning(self, '本地地址', str(exc))
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
            self, '删除本地地址' if zh else 'Delete',
            '确定删除该本地地址配置？' if zh else 'Delete this local target?',
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
        s = (source or '').lower()
        if s in ('local_proxy', 'proxy'):
            return '本机代理'
        if s in ('ie_proxy', 'ie'):
            return 'IE'
        if s in ('cdp', 'chromium'):
            return 'Chromium'
        return source or '—'

    def apply_layout_mode(self, mode, low_height=False):
        self._layout_mode = mode
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        apply_splitter_orientation(self.mid_splitter, mode, min_editor=editor_min_height())
        sizes = (self._prefs.get('splitter_sizes') or {}).get(mode)
        if sizes and len(sizes) >= 2:
            self.mid_splitter.setSizes(sizes)
        secondary = [
            self.port_label, self.port_edit, self.target_combo,
            self.refresh_browsers_btn, self.pick_browser_btn,
            self.ie_install_cert_btn, self.ie_remove_cert_btn,
        ]
        self._conn_more_menu.clear()
        zh = self.language == 'zh'
        self.conn_more_btn.setText('更多' if zh else 'More')
        proxy_like = self._mode in ('proxy', 'ie')
        if mode == 'narrow':
            for w in secondary + [self.browser_combo, self.launch_btn, self.mode_combo]:
                if w is not None:
                    w.hide()
            self.connect_btn.show()
            self.stop_btn.show()
            self.conn_more_btn.show()
            for label, slot in (
                ('选择浏览器', self._pick_browser),
                ('刷新识别', self._refresh_browsers),
                ('启动调试浏览器', self._launch_browser),
            ):
                act = QAction(label if zh else label, self)
                act.triggered.connect(slot)
                self._conn_more_menu.addAction(act)
            if proxy_like:
                a1 = QAction('安装证书' if zh else 'Install CA', self)
                a1.triggered.connect(self._install_ie_cert)
                a2 = QAction('移除证书' if zh else 'Remove CA', self)
                a2.triggered.connect(self._remove_ie_cert)
                self._conn_more_menu.addAction(a1)
                self._conn_more_menu.addAction(a2)
            a3 = QAction('检查代理/重新连接' if zh else 'Recheck', self)
            a3.triggered.connect(self._recheck_channel)
            self._conn_more_menu.addAction(a3)
        elif mode == 'compact':
            for w in [self.port_label, self.port_edit, self.target_combo]:
                w.hide()
            self.browser_combo.setVisible(self._mode == 'chromium')
            self.mode_combo.show()
            self.launch_btn.setVisible(self._mode == 'chromium')
            self.connect_btn.show()
            self.stop_btn.show()
            self.conn_more_btn.show()
            a = QAction('端口与页面目标…' if zh else 'Port & targets…', self)
            a.triggered.connect(lambda: show_info(
                self, '连接', f'当前端口 {self._current_port()}。可在 Wide 模式下编辑端口与目标页。'
            ))
            self._conn_more_menu.addAction(a)
            if proxy_like:
                self.ie_install_cert_btn.hide()
                self.ie_remove_cert_btn.hide()
                a1 = QAction('安装证书', self)
                a1.triggered.connect(self._install_ie_cert)
                self._conn_more_menu.addAction(a1)
        else:
            self.conn_more_btn.hide()
            self.mode_combo.show()
            self._apply_mode_ui()
            self.port_label.show()
            self.port_edit.show()
            self.connect_btn.show()
            self.stop_btn.show()
        for edit in (self.overview_edit, self.req_detail, self.resp_detail, self.draft_preview):
            edit.setMinimumHeight(editor_min_height())

    # ── 语言 / 清理 ──────────────────────────────────
    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('接口排查' if zh else 'API Debug')
        self.page_subtitle.setText(
            '本机监听 · 仅内存 · 草稿验证' if zh else
            'Local capture · in-memory · draft only'
        )
        self.offline_pill.setText('● 本地' if zh else '● Local')
        self.mode_label.setText('模式' if zh else 'Mode')
        self.mode_combo.setItemText(0, '本机通用代理' if zh else 'Local proxy')
        self.mode_combo.setItemText(1, 'Chromium CDP（高级）' if zh else 'Chromium CDP')
        self.mode_combo.setItemText(2, 'IE 代理（兼容）' if zh else 'IE proxy')
        self.refresh_browsers_btn.setText('刷新' if zh else 'Refresh')
        self.pick_browser_btn.setText('选择 EXE' if zh else 'Browse…')
        self.launch_btn.setText('启动调试浏览器' if zh else 'Launch')
        self.connect_btn.setText(
            ('启用 IE 监听' if self._mode == 'ie' else '开始监听') if zh else
            ('Start IE proxy' if self._mode == 'ie' else 'Start')
        )
        self.stop_btn.setText('停止' if zh else 'Stop')
        self.recheck_btn.setText('检查代理/重新连接' if zh else 'Recheck')
        self.ie_install_cert_btn.setText('安装证书' if zh else 'Install CA')
        self.ie_remove_cert_btn.setText('移除证书' if zh else 'Remove CA')
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
        self.detail_tabs.setTabText(3, '验证草稿' if zh else 'Draft')
        self.reveal_cb.setText('显示敏感内容' if zh else 'Reveal secrets')
        self.copy_safe_url_btn.setText('复制安全 URL' if zh else 'Copy safe URL')
        self.copy_req_btn.setText('复制请求' if zh else 'Copy request')
        self.format_req_btn.setText('送格式工具' if zh else 'Format tools')
        self.gateway_req_btn.setText('送入加解密' if zh else 'Crypto')
        self.copy_resp_btn.setText('复制响应' if zh else 'Copy response')
        self.format_resp_btn.setText('送格式工具' if zh else 'Format tools')
        self.gateway_resp_btn.setText('送入加解密' if zh else 'Crypto')
        self.draft_badge.setText('仅生成验证草稿 · 不发送请求' if zh else 'Draft only · no send')
        self.target_label.setText('本地地址' if zh else 'Local base')
        self.add_target_btn.setText('新增' if zh else 'Add')
        self.edit_target_btn.setText('编辑' if zh else 'Edit')
        self.del_target_btn.setText('删除' if zh else 'Delete')
        self.include_auth_cb.setText(
            '草稿携带 Authorization / Cookie' if zh else 'Include auth headers in draft'
        )
        self.gen_draft_btn.setText('生成草稿' if zh else 'Generate draft')
        self.copy_postman_btn.setText('复制 Postman' if zh else 'Copy Postman')
        self.export_postman_btn.setText('导出 Collection' if zh else 'Export')
        self.copy_curl_btn.setText('复制 cURL' if zh else 'Copy cURL')
        self.draft_hint.setText('不会发送请求' if zh else 'No HTTP send')
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
