# -*- coding: utf-8 -*-
"""接口排查中心：Chromium CDP + IE 本机代理，内存会话，仅生成验证草稿。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from tools.browser_debug import (
    BrowserDebugError, connect_page_session, discover_browsers, fetch_cdp_targets,
    is_loopback_host, launch_debug_browser, mask_sensitive_value, mask_url_query,
    pick_default_page_target, port_open, should_keep_record, wait_debug_port,
)
from tools.ie_proxy import (
    IeProxyWorker, install_user_root_cert, remove_recorded_cert, restore_proxy_from_snapshot,
)
from tools.interface_debug_store import (
    load_interface_debug_config, save_interface_debug_config,
)
from tools.interface_drafts import (
    DraftError, build_curl, build_postman_collection, drafts_as_json_text, rewrite_url,
    validate_base_url,
)
from ui.aurora_progress import AuroraProgress
from ui.confirm_dialog import confirm_action, show_info, show_success, show_warning
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import size_combo
from ui.page_chrome import make_page_header


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


class InterfaceDebugPanel(QWidget):
    """Private 版接口排查面板。"""

    open_gateway = pyqtSignal(str)
    open_format_json = pyqtSignal(str)
    open_format_xml = pyqtSignal(str)

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._config = load_interface_debug_config()
        self._records: list[dict] = []  # 内存会话
        self._records_by_id: dict[str, dict] = {}
        self._cdp_session = None
        self._ie_worker = None
        self._listening = False
        self._mode = 'chromium'  # chromium | ie
        self._reveal_sensitive = False
        self._show_static = False
        self._selected_id = None
        self._launch_worker = None
        self._setup_ui()
        self._reload_config_ui()
        self.set_language(language)
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
            '本机 CDP / IE 代理 · 报文仅内存 · 只生成验证草稿',
            'api-debug',
            trailing=self.offline_pill,
        )
        root.addWidget(header)

        # 顶部连接区
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
        self.mode_combo.addItems(['Chromium 调试端口', 'IE 本机代理'])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row1.addWidget(self.mode_combo)
        self.browser_combo = QComboBox()
        size_combo(self.browser_combo, 'lg')
        self.browser_combo.setMinimumWidth(280)
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

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.port_label = QLabel()
        row2.addWidget(self.port_label)
        self.port_edit = QLineEdit()
        self.port_edit.setMaximumWidth(90)
        self.port_edit.setText(str(self._config.get('debug_port') or 9222))
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
        self.target_combo = QComboBox()
        size_combo(self.target_combo, 'lg')
        self.target_combo.setMinimumWidth(220)
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
        cl.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(12)
        self.show_static_cb = QCheckBox()
        self.show_static_cb.toggled.connect(self._on_show_static)
        row3.addWidget(self.show_static_cb)
        self.reveal_cb = QCheckBox()
        self.reveal_cb.toggled.connect(self._on_reveal)
        row3.addWidget(self.reveal_cb)
        self.status_label = QLabel()
        self.status_label.setObjectName('field-hint')
        self.status_label.setWordWrap(True)
        row3.addWidget(self.status_label, 1)
        cl.addLayout(row3)
        root.addWidget(conn)

        # 中部：列表 + 详情
        mid = QSplitter(Qt.Orientation.Horizontal)
        mid.setChildrenCollapsible(False)
        mid.setHandleWidth(8)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText('筛选 path / method…')
        self.filter_edit.textChanged.connect(self._rebuild_table)
        filter_row.addWidget(self.filter_edit, 1)
        self.clear_list_btn = QPushButton()
        apply_button(self.clear_list_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_list_btn.clicked.connect(self.clear_session)
        filter_row.addWidget(self.clear_list_btn)
        ll.addLayout(filter_row)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName('iface-request-table')
        self.table.setHorizontalHeaderLabels(['时间', '方法', '路径', '状态', '耗时', '类型'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        ll.addWidget(self.table, 1)
        mid.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName('module-tabs')
        self.req_detail = QPlainTextEdit()
        self.req_detail.setReadOnly(True)
        self.req_detail.setObjectName('iface-detail-edit')
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.req_detail.setFont(mono)
        self.resp_detail = QPlainTextEdit()
        self.resp_detail.setReadOnly(True)
        self.resp_detail.setObjectName('iface-detail-edit')
        self.resp_detail.setFont(mono)
        self.detail_tabs.addTab(self.req_detail, '请求')
        self.detail_tabs.addTab(self.resp_detail, '响应')
        rl.addWidget(self.detail_tabs, 1)

        link_row = QHBoxLayout()
        self.to_format_btn = QPushButton()
        apply_button(self.to_format_btn, 'secondary', compact=True, icon='json', icon_size=16)
        self.to_format_btn.clicked.connect(self._send_to_format)
        self.to_format_btn.setEnabled(False)
        link_row.addWidget(self.to_format_btn)
        self.to_gateway_btn = QPushButton()
        apply_button(self.to_gateway_btn, 'secondary', compact=True, icon='shield-key', icon_size=16)
        self.to_gateway_btn.clicked.connect(self._send_to_gateway)
        self.to_gateway_btn.setEnabled(False)
        link_row.addWidget(self.to_gateway_btn)
        link_row.addStretch(1)
        rl.addLayout(link_row)
        mid.addWidget(right)
        mid.setSizes([520, 480])
        root.addWidget(mid, 1)

        # 底部草稿区
        draft = QFrame()
        apply_surface(draft, 'zone')
        draft.setObjectName('iface-draft-zone')
        dl = QVBoxLayout(draft)
        dl.setContentsMargins(12, 10, 12, 10)
        dl.setSpacing(8)
        dhead = QHBoxLayout()
        self.draft_title = QLabel()
        self.draft_title.setObjectName('zone-title')
        dhead.addWidget(self.draft_title)
        self.draft_badge = QLabel()
        self.draft_badge.setObjectName('offline-pill')
        dhead.addWidget(self.draft_badge)
        dhead.addStretch(1)
        dl.addLayout(dhead)

        drow = QHBoxLayout()
        drow.setSpacing(8)
        self.target_label = QLabel()
        drow.addWidget(self.target_label)
        self.local_target_combo = QComboBox()
        size_combo(self.local_target_combo, 'md')
        self.local_target_combo.setMinimumWidth(200)
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

        self.draft_preview = QPlainTextEdit()
        self.draft_preview.setReadOnly(True)
        self.draft_preview.setObjectName('iface-draft-preview')
        self.draft_preview.setFont(mono)
        self.draft_preview.setMaximumHeight(120)
        dl.addWidget(self.draft_preview)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.copy_postman_btn = QPushButton()
        apply_button(self.copy_postman_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_postman_btn.clicked.connect(self._copy_postman)
        brow.addWidget(self.copy_postman_btn)
        self.export_postman_btn = QPushButton()
        apply_button(self.export_postman_btn, 'secondary', compact=True, icon='export', icon_size=16)
        self.export_postman_btn.clicked.connect(self._export_postman)
        brow.addWidget(self.export_postman_btn)
        self.copy_curl_btn = QPushButton()
        apply_button(self.copy_curl_btn, 'primary', compact=True, icon='terminal', icon_size=16)
        self.copy_curl_btn.clicked.connect(self._copy_curl)
        brow.addWidget(self.copy_curl_btn)
        brow.addStretch(1)
        self.draft_hint = QLabel()
        self.draft_hint.setObjectName('field-hint')
        brow.addWidget(self.draft_hint)
        dl.addLayout(brow)
        root.addWidget(draft)

        self.loading = AuroraProgress(self)
        self._refresh_browsers()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading'):
            self.loading.place_overlay(self)

    # ── 配置 / 浏览器 ──────────────────────────────────
    def _reload_config_ui(self):
        self._config = load_interface_debug_config()
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
            self.browser_combo.addItem(f"{b['name']}{tag} — {b['path']}", b['path'])
            idx = self.browser_combo.count() - 1
            self.browser_combo.setItemData(idx, b, Qt.ItemDataRole.UserRole + 1)
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
        save_interface_debug_config(self._config)
        self._refresh_browsers()
        if 'firefox' in path.lower():
            show_warning(
                self,
                '浏览器',
                'Firefox 暂不支持实时监听；请使用 Chromium 内核浏览器。'
                if self.language == 'zh' else
                'Firefox is not supported for live capture; use a Chromium browser.',
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
        self._config['debug_port'] = self._current_port()
        path = self.browser_combo.currentData() or ''
        if path:
            self._config['browser_path'] = path
        save_interface_debug_config(self._config)

    # ── 模式 ──────────────────────────────────────────
    def _on_mode_changed(self, index):
        if self._listening:
            self._stop_listen()
        self._mode = 'ie' if index == 1 else 'chromium'
        ie = self._mode == 'ie'
        self.browser_combo.setVisible(not ie)
        self.refresh_browsers_btn.setVisible(not ie)
        self.pick_browser_btn.setVisible(not ie)
        self.launch_btn.setVisible(not ie)
        self.target_combo.setVisible(not ie)
        self.port_label.setText(
            ('IE 代理端口' if ie else '调试端口') if self.language == 'zh' else
            ('IE proxy port' if ie else 'Debug port')
        )
        if ie:
            self.port_edit.setText(str(self._config.get('ie_proxy_port') or 8899))
        else:
            self.port_edit.setText(str(self._config.get('debug_port') or 9222))
        self.ie_install_cert_btn.setVisible(ie)
        self.ie_remove_cert_btn.setVisible(ie)
        self.clear_session()

    def _on_show_static(self, checked):
        self._show_static = bool(checked)
        self._rebuild_table()

    def _on_reveal(self, checked):
        self._reveal_sensitive = bool(checked)
        self._refresh_detail()

    # ── 连接 / 监听 ──────────────────────────────────
    def _launch_browser(self):
        meta = self._selected_browser_meta()
        path = meta.get('path') or ''
        if meta.get('is_firefox'):
            show_warning(
                self, '浏览器',
                'Firefox 暂不支持实时监听；请使用 Chromium 内核浏览器。',
            )
            return
        if not path or not os.path.isfile(path):
            show_warning(self, '浏览器', '请先选择有效的 Chromium 浏览器 EXE')
            return
        self._save_port()
        self.loading.start_busy('正在启动调试浏览器…' if self.language == 'zh' else 'Launching debug browser…')
        self._launch_worker = _LaunchBrowserWorker(path, self._current_port(), self)
        self._launch_worker.finished_ok.connect(self._on_launch_ok)
        self._launch_worker.failed.connect(self._on_launch_fail)
        self._launch_worker.start()

    def _on_launch_ok(self, port):
        self.loading.finish('浏览器已就绪' if self.language == 'zh' else 'Browser ready')
        self.status_label.setText(
            f'调试浏览器已启动 · 端口 {port} · 请打开业务页后点击「连接」'
            if self.language == 'zh' else
            f'Debug browser ready · port {port}'
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
        if self._mode == 'ie':
            self._start_ie_proxy()
        else:
            self._connect_cdp()

    def _connect_cdp(self):
        port = self._current_port()
        host = '127.0.0.1'
        if not is_loopback_host(host):
            show_warning(self, '连接', 'CDP 仅允许 127.0.0.1')
            return
        self._save_port()
        if not port_open(port):
            show_warning(
                self, '连接',
                f'端口 {port} 不可用。请先「一键启动调试浏览器」，'
                f'或用 --remote-debugging-port={port} --remote-debugging-address=127.0.0.1 启动。',
            )
            return
        self.loading.start_busy('正在连接 CDP…' if self.language == 'zh' else 'Connecting CDP…')
        try:
            self._refresh_targets()
            target = self.target_combo.currentData()
            if not isinstance(target, dict):
                targets = fetch_cdp_targets(port)
                target = pick_default_page_target(targets)
            session = connect_page_session(
                port,
                target=target,
                host=host,
                on_event=self._on_cdp_event_thread,
                on_error=self._on_cdp_error_thread,
                on_closed=self._on_cdp_closed_thread,
            )
            self._cdp_session = session
            self._listening = True
            self._set_listening_ui(True)
            self.loading.finish('已开始监听' if self.language == 'zh' else 'Listening')
            self.status_label.setText(
                f'CDP 监听中 · {target.get("title") or target.get("url") or port}'
                if self.language == 'zh' else
                f'CDP listening · port {port}'
            )
        except Exception as exc:
            self.loading.fail(str(exc))
            show_warning(self, '连接 CDP', str(exc))

    def _on_cdp_event_thread(self, method, params):
        # 从后台线程调度到主线程
        QTimer.singleShot(0, lambda m=method, p=dict(params or {}): self._handle_cdp_event(m, p))

    def _on_cdp_error_thread(self, msg):
        QTimer.singleShot(0, lambda: self.status_label.setText(f'CDP 错误：{msg}'))

    def _on_cdp_closed_thread(self):
        QTimer.singleShot(0, self._on_cdp_closed)

    def _on_cdp_closed(self):
        if self._listening and self._mode == 'chromium':
            self.status_label.setText('CDP 连接已关闭' if self.language == 'zh' else 'CDP closed')
            self._listening = False
            self._set_listening_ui(False)

    def _handle_cdp_event(self, method, params):
        if not self._cdp_session:
            return
        # 从 session.records 同步
        with self._cdp_session._lock:
            records = dict(self._cdp_session.records)
        for rid, rec in records.items():
            if not should_keep_record(rec, self._show_static):
                continue
            self._records_by_id[rid] = dict(rec)
        self._records = list(self._records_by_id.values())
        self._records.sort(key=lambda r: r.get('started_at') or 0, reverse=True)
        self._rebuild_table()

    def _start_ie_proxy(self):
        zh = self.language == 'zh'
        ok = confirm_action(
            self,
            '启用 IE 代理监听' if zh else 'Enable IE proxy',
            (
                '将临时修改当前用户 Windows 代理为 127.0.0.1，'
                '并可能安装本机根证书以解密 HTTPS。\n'
                '停止监听后会自动恢复原代理。\n'
                '报文仅内存，不落盘、不外发。'
                if zh else
                'Will temporarily change your Windows proxy to 127.0.0.1 '
                'and may install a local root certificate for HTTPS.\n'
                'Proxy is restored when capture stops.'
            ),
            confirm_text='启用监听' if zh else 'Enable',
            danger=True,
        )
        if not ok:
            return
        try:
            port = self._current_port()
            self._config['ie_proxy_port'] = port
            save_interface_debug_config(self._config)
        except Exception:
            port = 8899
        self.loading.start_busy('正在启动 IE 代理…' if zh else 'Starting IE proxy…')
        try:
            worker = IeProxyWorker(
                port=port,
                on_record=self._on_ie_record_thread,
                on_error=self._on_ie_error_thread,
                on_stopped=self._on_ie_stopped_thread,
                show_static=self._show_static,
            )
            worker.start()
            self._ie_worker = worker
            self._listening = True
            self._set_listening_ui(True)
            self.loading.finish('IE 代理已启用' if zh else 'IE proxy on')
            self.status_label.setText(
                f'IE 代理监听 127.0.0.1:{port} · 请在 IE 中操作业务页'
                if zh else
                f'IE proxy listening 127.0.0.1:{port}'
            )
        except Exception as exc:
            self.loading.fail(str(exc))
            show_warning(self, 'IE 代理', str(exc))
            try:
                restore_proxy_from_snapshot()
            except Exception:
                pass

    def _on_ie_record_thread(self, rec):
        QTimer.singleShot(0, lambda r=dict(rec): self._ingest_record(r))

    def _on_ie_error_thread(self, msg):
        QTimer.singleShot(0, lambda: self._on_ie_error(msg))

    def _on_ie_stopped_thread(self):
        QTimer.singleShot(0, lambda: self._set_listening_ui(False))

    def _on_ie_error(self, msg):
        self.status_label.setText(f'IE 代理错误：{msg}')
        show_warning(self, 'IE 代理', msg)
        self._listening = False
        self._set_listening_ui(False)

    def _ingest_record(self, rec: dict):
        if not should_keep_record(rec, self._show_static):
            return
        rid = rec.get('id') or ''
        self._records_by_id[rid] = rec
        self._records = list(self._records_by_id.values())
        self._records.sort(key=lambda r: r.get('started_at') or 0, reverse=True)
        self._rebuild_table()

    def _stop_listen(self):
        self.loading.start_busy('正在停止监听…' if self.language == 'zh' else 'Stopping…')
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
            self._set_listening_ui(False)
            self.loading.finish('已停止' if self.language == 'zh' else 'Stopped')
            self.status_label.setText(
                '已停止监听，会话已清空' if self.language == 'zh' else 'Stopped · session cleared'
            )

    def _set_listening_ui(self, active: bool):
        self.connect_btn.setEnabled(not active)
        self.launch_btn.setEnabled(not active and self._mode == 'chromium')
        self.stop_btn.setEnabled(active)
        self.mode_combo.setEnabled(not active)

    def clear_session(self):
        self._records.clear()
        self._records_by_id.clear()
        self._selected_id = None
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
        self.req_detail.clear()
        self.resp_detail.clear()
        self.draft_preview.clear()
        self.to_format_btn.setEnabled(False)
        self.to_gateway_btn.setEnabled(False)

    # ── IE 证书 ──────────────────────────────────────
    def _install_ie_cert(self):
        zh = self.language == 'zh'
        ok = confirm_action(
            self,
            '安装本机抓包证书' if zh else 'Install capture CA',
            (
                '将把 mitmproxy 根证书安装到「当前用户」受信任根证书颁发机构。\n'
                '仅用于本机解密 IE HTTPS，不会以管理员身份运行。\n'
                '可随时用「移除本机抓包证书」删除记录的指纹。'
                if zh else
                'Install mitmproxy CA into current-user Root store for local HTTPS capture only.'
            ),
            confirm_text='安装证书' if zh else 'Install',
            danger=True,
        )
        if not ok:
            return
        self.loading.start_busy('正在安装证书…' if zh else 'Installing cert…')
        try:
            thumb = install_user_root_cert()
            self._config = load_interface_debug_config()
            self.loading.finish('证书已安装' if zh else 'Cert installed')
            show_success(
                self, '证书',
                f'已安装，指纹 {thumb[:16]}…' if zh else f'Installed, thumb {thumb[:16]}…',
            )
        except Exception as exc:
            self.loading.fail(str(exc))
            show_warning(self, '证书', str(exc))

    def _remove_ie_cert(self):
        zh = self.language == 'zh'
        cfg = load_interface_debug_config()
        thumb = (cfg.get('ie_certificate_thumbprint') or '').strip()
        if not thumb:
            show_info(self, '证书', '没有已记录的抓包证书指纹' if zh else 'No recorded thumbprint')
            return
        ok = confirm_action(
            self,
            '移除本机抓包证书' if zh else 'Remove capture CA',
            f'将仅删除指纹 {thumb} 对应的证书。' if zh else f'Remove cert with thumbprint {thumb}.',
            confirm_text='移除证书' if zh else 'Remove',
            danger=True,
        )
        if not ok:
            return
        try:
            remove_recorded_cert(thumb)
            self._config = load_interface_debug_config()
            show_success(self, '证书', '已移除' if zh else 'Removed')
        except Exception as exc:
            show_warning(self, '证书', str(exc))

    def _check_orphan_proxy_snapshot(self):
        cfg = load_interface_debug_config()
        snap = cfg.get('proxy_restore_snapshot')
        if not isinstance(snap, dict):
            return
        zh = self.language == 'zh'
        ok = confirm_action(
            self,
            '检测到未恢复的代理设置' if zh else 'Unrestored proxy snapshot',
            (
                '上次异常退出可能未恢复 Windows 代理。是否立即恢复？'
                if zh else
                'Previous session may have left system proxy changed. Restore now?'
            ),
            confirm_text='一键恢复' if zh else 'Restore',
            danger=False,
        )
        if ok:
            try:
                restore_proxy_from_snapshot(snap)
                show_success(self, '代理', '已恢复原代理设置' if zh else 'Proxy restored')
            except Exception as exc:
                show_warning(self, '代理', str(exc))

    # ── 表格 / 详情 ──────────────────────────────────
    def _rebuild_table(self):
        filt = (self.filter_edit.text() or '').strip().lower()
        rows = []
        for rec in self._records:
            if not should_keep_record(rec, self._show_static):
                continue
            if filt:
                blob = f"{rec.get('method','')} {rec.get('path','')} {rec.get('url','')}".lower()
                if filt not in blob:
                    continue
            rows.append(rec)
        self.table.setRowCount(len(rows))
        for i, rec in enumerate(rows):
            ts = rec.get('started_at') or time.time()
            tstr = datetime.fromtimestamp(ts).strftime('%H:%M:%S')
            path = rec.get('path') or '/'
            if rec.get('query'):
                path = path + '?' + mask_url_query('x?' + rec['query'], self._reveal_sensitive).split('?', 1)[-1]
            status = rec.get('status')
            status_s = '' if status is None else str(status)
            if rec.get('failure'):
                status_s = status_s or 'ERR'
            dur = rec.get('duration_ms')
            dur_s = '' if dur is None else f'{dur}ms'
            rtype = rec.get('resource_type') or rec.get('source') or ''
            vals = [tstr, rec.get('method') or '', path, status_s, dur_s, rtype]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, rec.get('id'))
                self.table.setItem(i, c, item)
        # 保持选中
        if self._selected_id:
            for i in range(self.table.rowCount()):
                it = self.table.item(i, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                    self.table.selectRow(i)
                    break

    def _on_row_selected(self):
        items = self.table.selectedItems()
        if not items:
            self._selected_id = None
            return
        rid = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_id = rid
        self._refresh_detail()
        self._refresh_draft_preview()

    def _selected_record(self) -> dict | None:
        if not self._selected_id:
            return None
        return self._records_by_id.get(self._selected_id)

    def _format_headers(self, headers: dict) -> str:
        lines = []
        for k, v in (headers or {}).items():
            lines.append(f'{k}: {mask_sensitive_value(k, v, self._reveal_sensitive)}')
        return '\n'.join(lines)

    def _refresh_detail(self):
        rec = self._selected_record()
        if not rec:
            self.req_detail.clear()
            self.resp_detail.clear()
            self.to_format_btn.setEnabled(False)
            self.to_gateway_btn.setEnabled(False)
            return
        url = mask_url_query(rec.get('url') or '', self._reveal_sensitive)
        req_lines = [
            f"{rec.get('method') or 'GET'} {url}",
            '',
            '—— Headers ——',
            self._format_headers(rec.get('request_headers') or {}),
            '',
            '—— Body ——',
            rec.get('request_body') or '',
        ]
        self.req_detail.setPlainText('\n'.join(req_lines))
        fail = rec.get('failure') or ''
        resp_lines = [
            f"Status: {rec.get('status') if rec.get('status') is not None else '-'}",
            f"MIME: {rec.get('mime_type') or '-'}",
            f"Duration: {rec.get('duration_ms') if rec.get('duration_ms') is not None else '-'} ms",
        ]
        if fail:
            resp_lines.append(f'Failure: {fail}')
        resp_lines += [
            '',
            '—— Headers ——',
            self._format_headers(rec.get('response_headers') or {}),
            '',
            '—— Body ——',
            rec.get('response_body') or '',
        ]
        self.resp_detail.setPlainText('\n'.join(resp_lines))
        body = rec.get('response_body') or rec.get('request_body') or ''
        self.to_format_btn.setEnabled(bool(body.strip()) and (_looks_json(body) or _looks_xml(body)))
        self.to_gateway_btn.setEnabled(bool(body.strip()) and (
            _looks_base64ish(body) or not (_looks_json(body) or _looks_xml(body))
        ))

    def _active_body(self) -> str:
        rec = self._selected_record()
        if not rec:
            return ''
        # 当前 tab
        if self.detail_tabs.currentIndex() == 1:
            return rec.get('response_body') or ''
        return rec.get('request_body') or rec.get('response_body') or ''

    def _send_to_format(self):
        body = self._active_body()
        if not body.strip():
            return
        if _looks_xml(body):
            self.open_format_xml.emit(body)
        else:
            self.open_format_json.emit(body)

    def _send_to_gateway(self):
        body = self._active_body()
        if not body.strip():
            # 尝试另一侧
            rec = self._selected_record()
            if rec:
                body = rec.get('request_body') or rec.get('response_body') or ''
        if body.strip():
            self.open_gateway.emit(body)

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

    def _refresh_draft_preview(self):
        rec = self._selected_record()
        base = self._selected_base_url()
        if not rec or not base:
            self.draft_preview.setPlainText('')
            return
        try:
            rewritten = rewrite_url(rec.get('url') or '', base)
            self.draft_preview.setPlainText(
                f"{rec.get('method') or 'GET'} {rewritten}\n"
                f"（仅生成验证草稿，不发送请求）"
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
                'Draft may contain Authorization/Cookie. Import only into local Postman.\n'
                'PengTools will not send any request.'
            ),
            confirm_text='继续生成' if zh else 'Continue',
            danger=False,
        )

    def _copy_postman(self):
        rec = self._selected_record()
        base = self._selected_base_url()
        if not rec:
            show_warning(self, '草稿', '请先选择一条请求')
            return
        if not base:
            show_warning(self, '草稿', '请先配置本地地址')
            return
        if not self._warn_sensitive_draft():
            return
        try:
            payload = build_postman_collection(rec, base)
            text = drafts_as_json_text(payload)
            QApplication.clipboard().setText(text)
            show_success(self, '草稿', 'Postman JSON 已复制' if self.language == 'zh' else 'Postman JSON copied')
        except DraftError as exc:
            show_warning(self, '草稿', str(exc))

    def _export_postman(self):
        rec = self._selected_record()
        base = self._selected_base_url()
        if not rec or not base:
            show_warning(self, '草稿', '请选择请求并配置本地地址')
            return
        if not self._warn_sensitive_draft():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, '导出 Postman Collection', 'pengtools_local_draft.json',
            'JSON (*.json)',
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
        rec = self._selected_record()
        base = self._selected_base_url()
        if not rec or not base:
            show_warning(self, '草稿', '请选择请求并配置本地地址')
            return
        if not self._warn_sensitive_draft():
            return
        try:
            text = build_curl(rec, base)
            QApplication.clipboard().setText(text)
            show_success(self, '草稿', 'cURL 已复制' if self.language == 'zh' else 'cURL copied')
        except DraftError as exc:
            show_warning(self, '草稿', str(exc))

    def _add_local_target(self):
        from PyQt6.QtWidgets import QInputDialog
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
        import uuid
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
            self,
            '删除本地地址' if zh else 'Delete target',
            '确定删除该本地地址配置？' if zh else 'Delete this local target?',
            confirm_text='删除' if zh else 'Delete',
            danger=True,
        ):
            return
        self._config['local_targets'] = [
            t for t in (self._config.get('local_targets') or []) if t.get('id') != tid
        ]
        if self._config.get('default_target_id') == tid:
            self._config['default_target_id'] = ''
        save_interface_debug_config(self._config)
        self._fill_local_targets()

    # ── 语言 / 清理 ──────────────────────────────────
    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('接口排查' if zh else 'API Debug')
        self.page_subtitle.setText(
            '本机 CDP / IE 代理 · 报文仅内存 · 只生成验证草稿' if zh else
            'Local CDP / IE proxy · in-memory only · draft generation only'
        )
        self.offline_pill.setText('● 本地' if zh else '● Local')
        self.mode_label.setText('模式' if zh else 'Mode')
        self.mode_combo.setItemText(0, 'Chromium 调试端口' if zh else 'Chromium CDP')
        self.mode_combo.setItemText(1, 'IE 本机代理' if zh else 'IE local proxy')
        self.refresh_browsers_btn.setText('刷新' if zh else 'Refresh')
        self.pick_browser_btn.setText('手动选择' if zh else 'Browse…')
        self.port_label.setText(
            ('IE 代理端口' if self._mode == 'ie' else '调试端口') if zh else
            ('IE proxy port' if self._mode == 'ie' else 'Debug port')
        )
        self.launch_btn.setText('一键启动调试浏览器' if zh else 'Launch debug browser')
        self.connect_btn.setText(
            ('启用 IE 代理监听' if self._mode == 'ie' else '连接已有调试浏览器') if zh else
            ('Start IE proxy' if self._mode == 'ie' else 'Connect debug browser')
        )
        self.stop_btn.setText('停止监听' if zh else 'Stop')
        self.ie_install_cert_btn.setText('安装抓包证书' if zh else 'Install CA')
        self.ie_remove_cert_btn.setText('移除抓包证书' if zh else 'Remove CA')
        self.show_static_cb.setText('显示静态资源' if zh else 'Show static assets')
        self.reveal_cb.setText('显示敏感字段' if zh else 'Reveal secrets')
        self.filter_edit.setPlaceholderText('筛选 path / method…' if zh else 'Filter path / method…')
        self.clear_list_btn.setText('清空列表' if zh else 'Clear list')
        self.detail_tabs.setTabText(0, '请求' if zh else 'Request')
        self.detail_tabs.setTabText(1, '响应' if zh else 'Response')
        self.to_format_btn.setText('在格式工具中打开' if zh else 'Open in Format tools')
        self.to_gateway_btn.setText('送入加解密' if zh else 'Send to Crypto')
        self.draft_title.setText('本地验证草稿' if zh else 'Local verification draft')
        self.draft_badge.setText('仅生成验证草稿' if zh else 'Draft only · no send')
        self.target_label.setText('本地地址' if zh else 'Local base URL')
        self.add_target_btn.setText('新增' if zh else 'Add')
        self.edit_target_btn.setText('编辑' if zh else 'Edit')
        self.del_target_btn.setText('删除' if zh else 'Delete')
        self.copy_postman_btn.setText('复制 Postman JSON' if zh else 'Copy Postman JSON')
        self.export_postman_btn.setText('导出 Postman 文件' if zh else 'Export Postman')
        self.copy_curl_btn.setText('复制 cURL' if zh else 'Copy cURL')
        self.draft_hint.setText(
            '不提供发送 / 重放' if zh else 'No send / replay'
        )
        self.table.setHorizontalHeaderLabels(
            ['时间', '方法', '路径', '状态', '耗时', '类型'] if zh else
            ['Time', 'Method', 'Path', 'Status', 'Dur', 'Type']
        )

    def shutdown_cleanup(self):
        """应用退出时调用：停监听、恢复代理、清空内存。"""
        try:
            if self._cdp_session:
                self._cdp_session.stop()
                self._cdp_session = None
            if self._ie_worker:
                self._ie_worker.stop()
                self._ie_worker = None
        except Exception:
            pass
        self.clear_session()
        try:
            restore_proxy_from_snapshot()
        except Exception:
            pass
