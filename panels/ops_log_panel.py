# -*- coding: utf-8 -*-
"""运维工作台 · 日志排查：开源 SSH 交互终端 + 远端目录 + 批量导出。

技术栈均为开源：Python / Paramiko(SSH) / PyQt6。自研实现，未使用任何商业终端源码或资源。
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PyQt6.QtCore import QFileInfo, QSize, Qt, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFileIconProvider, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

# 与需求管理「文件库」一致：系统文件夹/文件类型图标
_REMOTE_FILE_ICON_PROVIDER = QFileIconProvider()

from tools.ops_ssh import (
    DEFAULT_CATEGORY_NAME, UNCATEGORIZED_ID, OpsSshError,
    build_export_jobs, build_remote_grep_command, category_name_map, close_ssh_client,
    decrypt_secret, delete_category, ensure_category, exec_remote, extract_logs_parallel,
    format_connection_ok, list_remote_dir, list_remote_log_files, load_categories, load_log_settings,
    load_server_store, load_servers, make_job_key, open_ssh_client, paramiko_available,
    parent_remote_path, primary_log_path, remote_home_dir, save_log_settings,
    parse_keywords, save_server_store, save_servers, server_services, split_extra_keywords, test_connection,
)
from tools.ops_cmd_history import append_command, command_list, load_history, save_history
from ui.confirm_dialog import confirm_action, show_error, show_success, show_warning
from ui.design_system import apply_button, apply_surface, apply_table
from ui.field_metrics import size_combo, size_line
from ui.page_chrome import make_page_header
from ui.ssh_terminal import SshTerminalWidget


class _SshTestWorker(QThread):
    """后台测试 SSH，避免卡住界面。"""

    finished_ok = pyqtSignal(dict)
    finished_err = pyqtSignal(str)

    def __init__(self, server: dict, password_override: str | None = None, timeout_sec: int = 15, parent=None):
        super().__init__(parent)
        self._server = dict(server or {})
        self._password = password_override
        self._timeout = int(timeout_sec or 15)

    def run(self):
        try:
            result = test_connection(
                self._server,
                password_override=self._password,
                timeout_sec=self._timeout,
            )
            self.finished_ok.emit(result if isinstance(result, dict) else {'ok': True})
        except OpsSshError as exc:
            self.finished_err.emit(str(exc) or 'SSH 连接失败')
        except Exception as exc:
            self.finished_err.emit(str(exc) or exc.__class__.__name__)


class PasswordLineEdit(QLineEdit):
    """密码框：黑点隐藏；允许 Ctrl+V / 右键粘贴 / 拖放；可切换明文查看。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setInputMethodHints(
            Qt.InputMethodHint.ImhHiddenText
            | Qt.InputMethodHint.ImhNoAutoUppercase
            | Qt.InputMethodHint.ImhNoPredictiveText
        )
        # 允许粘贴与标准右键菜单（含粘贴）
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setAcceptDrops(True)


class _ExportBridge(QObject):
    result_ready = pyqtSignal(dict)
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)


class _Worker(QThread):
    ok = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except OpsSshError as exc:
            self.fail.emit(str(exc))
        except Exception as exc:
            self.fail.emit(str(exc) or exc.__class__.__name__)


class CategoryManageDialog(QDialog):
    """用户自定义服务器分类：新增 / 重命名 / 删除（删除后机器归入未分类）。"""

    def __init__(self, language='zh', categories=None, servers=None, parent=None):
        super().__init__(parent)
        self.language = language
        self._categories = [dict(c) for c in (categories or [])]
        self._servers = [dict(s) for s in (servers or [])]
        zh = language == 'zh'
        self.setWindowTitle('管理服务器分类' if zh else 'Manage server categories')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        hint = QLabel(
            '自定义分类，例如：集成服务器、模拟服务器、生产服务器。删除分类后，其下服务器会回到「未分类」。'
            if zh else
            'Define categories like Integration / Simulation / Production. Deleting moves hosts to Uncategorized.'
        )
        hint.setObjectName('field-hint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.add_btn = QPushButton('新增分类' if zh else 'Add')
        self.rename_btn = QPushButton('重命名' if zh else 'Rename')
        self.del_btn = QPushButton('删除' if zh else 'Delete')
        self.add_btn.clicked.connect(self._add)
        self.rename_btn.clicked.connect(self._rename)
        self.del_btn.clicked.connect(self._delete)
        for b in (self.add_btn, self.rename_btn, self.del_btn):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton('确定' if zh else 'OK', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('取消' if zh else 'Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self):
        self.list.clear()
        cats = sorted(self._categories, key=lambda c: (int(c.get('sort') or 0), c.get('name') or ''))
        counts = {}
        for s in self._servers:
            cid = s.get('category_id') or UNCATEGORIZED_ID
            counts[cid] = counts.get(cid, 0) + 1
        for cat in cats:
            n = counts.get(cat.get('id'), 0)
            label = f"{cat.get('name')}  ·  {n} 台"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, cat.get('id'))
            if cat.get('id') == UNCATEGORIZED_ID:
                item.setToolTip('系统内置，不可删除' if self.language == 'zh' else 'Built-in, cannot delete')
            self.list.addItem(item)

    def _add(self):
        zh = self.language == 'zh'
        dlg = QInputDialog(self)
        dlg.setWindowTitle('新增分类' if zh else 'Add')
        dlg.setLabelText('分类名称' if zh else 'Name')
        dlg.setOkButtonText('确定' if zh else 'OK')
        dlg.setCancelButtonText('取消' if zh else 'Cancel')
        if not dlg.exec():
            return
        name = (dlg.textValue() or '').strip()
        if not name:
            return
        self._categories, _cid = ensure_category(self._categories, name)
        self._reload()

    def _rename(self):
        item = self.list.currentItem()
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        cat = next((c for c in self._categories if c.get('id') == cid), None)
        if not cat:
            return
        zh = self.language == 'zh'
        if cid == UNCATEGORIZED_ID:
            show_warning(self, 'PengTools', '「未分类」不可重命名' if zh else 'Cannot rename Uncategorized')
            return
        dlg = QInputDialog(self)
        dlg.setWindowTitle('重命名分类' if zh else 'Rename')
        dlg.setLabelText('分类名称' if zh else 'Name')
        dlg.setTextValue(cat.get('name') or '')
        dlg.setOkButtonText('确定' if zh else 'OK')
        dlg.setCancelButtonText('取消' if zh else 'Cancel')
        if not dlg.exec():
            return
        name = (dlg.textValue() or '').strip()
        if not name:
            return
        for c in self._categories:
            if c.get('id') != cid and c.get('name') == name:
                show_warning(self, 'PengTools', '分类名已存在' if zh else 'Name already exists')
                return
        cat['name'] = name
        for s in self._servers:
            if s.get('category_id') == cid:
                s['group'] = name
        self._reload()

    def _delete(self):
        item = self.list.currentItem()
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        zh = self.language == 'zh'
        if cid == UNCATEGORIZED_ID:
            show_warning(self, 'PengTools', '「未分类」不可删除' if zh else 'Cannot delete Uncategorized')
            return
        cat = next((c for c in self._categories if c.get('id') == cid), None)
        n = sum(1 for s in self._servers if s.get('category_id') == cid)
        msg = (
            f'确定删除分类「{cat.get("name") if cat else cid}」吗？\n其下 {n} 台服务器将归入「未分类」。'
            if zh else
            f'Delete category? {n} server(s) move to Uncategorized.'
        )
        if not confirm_action(self, '删除分类' if zh else 'Delete', msg, confirm_text='删除' if zh else 'Delete', danger=True):
            return
        self._categories, self._servers = delete_category(self._categories, self._servers, cid)
        self._reload()

    def result_data(self) -> tuple[list[dict], list[dict]]:
        return list(self._categories), list(self._servers)


class ServerEditorDialog(QDialog):
    def __init__(self, language='zh', server=None, categories=None, parent=None):
        super().__init__(parent)
        self.language = language
        self._server = dict(server or {})
        self._categories = [dict(c) for c in (categories or load_categories())]
        self._password_dirty = False
        zh = language == 'zh'
        self.setObjectName('server-editor-dialog')
        self.setWindowTitle('编辑服务器' if self._server.get('id') else ('新增服务器' if zh else 'Add server'))
        self.setMinimumWidth(640)
        self.setMinimumHeight(520)
        self.resize(720, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        title = QLabel(self.windowTitle())
        title.setObjectName('dialog-title')
        layout.addWidget(title)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.name_edit = QLineEdit(self._server.get('name', ''))
        size_line(self.name_edit, 'std')
        self.host_edit = QLineEdit(self._server.get('host', ''))
        size_line(self.host_edit, 'std')
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self._server.get('port') or 22))
        self.user_edit = QLineEdit(self._server.get('username', ''))
        size_line(self.user_edit, 'std')
        # 密码：可粘贴 + 可保存（DPAPI/Fernet 加密写入 data/）
        pwd_row = QHBoxLayout()
        pwd_row.setContentsMargins(0, 0, 0, 0)
        pwd_row.setSpacing(6)
        self.password_edit = PasswordLineEdit()
        size_line(self.password_edit, 'std')
        has_saved = bool(self._server.get('password_token'))
        if has_saved:
            self.password_edit.setPlaceholderText(
                '已保存（留空则保持原密码；可粘贴新密码覆盖）'
                if zh else
                'Saved (blank keeps current; paste to replace)'
            )
        else:
            self.password_edit.setPlaceholderText(
                '输入或粘贴密码（Ctrl+V / 右键粘贴）'
                if zh else
                'Type or paste password (Ctrl+V)'
            )
        self.password_edit.setToolTip(
            '支持粘贴；点保存后加密存本机 data，下次连接无需再输'
            if zh else
            'Paste supported; saved encrypted to local data/'
        )
        self.password_edit.textChanged.connect(self._on_password_changed)
        pwd_row.addWidget(self.password_edit, 1)
        self.show_password_check = QCheckBox('显示' if zh else 'Show')
        self.show_password_check.setToolTip('临时显示明文，便于确认粘贴是否正确' if zh else 'Reveal password text')
        self.show_password_check.toggled.connect(self._toggle_password_visible)
        pwd_row.addWidget(self.show_password_check)
        pwd_wrap = QWidget()
        pwd_wrap.setLayout(pwd_row)
        self.save_password_check = QCheckBox('保存密码到本机' if zh else 'Save password locally')
        self.save_password_check.setChecked(True)
        self.save_password_check.setToolTip(
            '勾选后密码加密写入 data（Windows 优先 DPAPI）；取消则本次可用但不落盘'
            if zh else
            'Encrypt to local data/ when checked; uncheck to use once without saving'
        )
        # 多服务日志路径表
        svc_box = QVBoxLayout()
        svc_box.setSpacing(4)
        self.services_table = QTableWidget(0, 3)
        self.services_table.setHorizontalHeaderLabels(
            ['服务名', '日志路径', '启用'] if zh else ['Service', 'Log path', 'On']
        )
        self.services_table.horizontalHeader().setStretchLastSection(True)
        self.services_table.setMinimumHeight(180)
        self.services_table.setMaximumHeight(280)
        self.services_table.setColumnWidth(0, 120)
        self.services_table.setColumnWidth(1, 360)
        try:
            self.services_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        except Exception:
            pass
        svc_btns = QHBoxLayout()
        self.add_svc_btn = QPushButton('添加服务' if zh else 'Add service')
        apply_button(self.add_svc_btn, 'ghost', compact=True)
        self.add_svc_btn.clicked.connect(self._add_service_row)
        self.del_svc_btn = QPushButton('删除' if zh else 'Remove')
        apply_button(self.del_svc_btn, 'ghost', compact=True)
        self.del_svc_btn.clicked.connect(self._del_service_row)
        svc_btns.addWidget(self.add_svc_btn)
        svc_btns.addWidget(self.del_svc_btn)
        svc_btns.addStretch(1)
        svc_box.addWidget(self.services_table)
        svc_box.addLayout(svc_btns)
        svc_wrap = QWidget()
        svc_wrap.setLayout(svc_box)
        self._load_services_table()
        # 分类：下拉选择 + 可手写新分类名
        cat_row = QHBoxLayout()
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        size_combo(self.category_combo, 'md')
        self._fill_category_combo()
        current_cid = self._server.get('category_id') or UNCATEGORIZED_ID
        idx = self.category_combo.findData(current_cid)
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        elif self._server.get('group'):
            self.category_combo.setEditText(str(self._server.get('group')))
        self.new_cat_btn = QPushButton('新建' if zh else 'New')
        apply_button(self.new_cat_btn, 'ghost', compact=True)
        self.new_cat_btn.clicked.connect(self._quick_new_category)
        cat_row.addWidget(self.category_combo, 1)
        cat_row.addWidget(self.new_cat_btn)
        cat_wrap = QWidget()
        cat_wrap.setLayout(cat_row)
        form.addRow('名称' if zh else 'Name', self.name_edit)
        form.addRow('主机' if zh else 'Host', self.host_edit)
        form.addRow('端口' if zh else 'Port', self.port_spin)
        form.addRow('用户名' if zh else 'Username', self.user_edit)
        form.addRow('密码' if zh else 'Password', pwd_wrap)
        form.addRow('', self.save_password_check)
        form.addRow('服务与日志' if zh else 'Services / logs', svc_wrap)
        form.addRow('分类' if zh else 'Category', cat_wrap)
        layout.addLayout(form)
        hint = QLabel(
            '一台机可配多个服务路径。密码可粘贴并加密保存。导出时可多机多路径并行截取。'
            if zh else
            'Multiple service log paths per host. Password paste + encrypted save. Batch multi-path export.'
        )
        hint.setObjectName('field-hint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        # 不用 StandardButton 文案（系统可能显示英文 Save/Cancel）
        buttons = QDialogButtonBox()
        self.save_btn = buttons.addButton(
            '保存' if zh else 'Save', QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.cancel_btn = buttons.addButton(
            '取消' if zh else 'Cancel', QDialogButtonBox.ButtonRole.RejectRole,
        )
        self.test_btn = buttons.addButton(
            '测试连接' if zh else 'Test', QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.test_btn.clicked.connect(self._test)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


    def _load_services_table(self):
        from tools.ops_ssh import server_services
        services = server_services(self._server, only_enabled=False)
        self.services_table.setRowCount(0)
        if not services:
            self._add_service_row(name='默认服务', path=str(self._server.get('default_log_path') or ''))
            return
        for svc in services:
            self._add_service_row(
                name=str(svc.get('name') or ''),
                path=str(svc.get('log_path') or ''),
                enabled=bool(svc.get('enabled', True)),
                sid=str(svc.get('id') or ''),
            )

    def _add_service_row(self, name='', path='', enabled=True, sid=''):
        import uuid
        row = self.services_table.rowCount()
        self.services_table.insertRow(row)
        name_item = QTableWidgetItem(name or f'服务{row + 1}')
        path_item = QTableWidgetItem(path or '')
        en_item = QTableWidgetItem()
        en_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        en_item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        if not sid:
            sid = uuid.uuid4().hex[:10]
        name_item.setData(Qt.ItemDataRole.UserRole, sid)
        self.services_table.setItem(row, 0, name_item)
        self.services_table.setItem(row, 1, path_item)
        self.services_table.setItem(row, 2, en_item)

    def _del_service_row(self):
        row = self.services_table.currentRow()
        if row >= 0:
            self.services_table.removeRow(row)

    def _collect_services(self) -> list[dict]:
        out = []
        for row in range(self.services_table.rowCount()):
            name_item = self.services_table.item(row, 0)
            path_item = self.services_table.item(row, 1)
            en_item = self.services_table.item(row, 2)
            name = (name_item.text() if name_item else '').strip()
            path = (path_item.text() if path_item else '').strip()
            if not name and not path:
                continue
            sid = ''
            if name_item:
                sid = str(name_item.data(Qt.ItemDataRole.UserRole) or '')
            enabled = True
            if en_item is not None:
                enabled = en_item.checkState() == Qt.CheckState.Checked
            out.append({
                'id': sid or __import__('uuid').uuid4().hex[:10],
                'name': name or f'服务{row + 1}',
                'log_path': path,
                'enabled': enabled,
            })
        return out

    def _fill_category_combo(self):
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for cat in sorted(self._categories, key=lambda c: (int(c.get('sort') or 0), c.get('name') or '')):
            self.category_combo.addItem(cat.get('name') or DEFAULT_CATEGORY_NAME, cat.get('id'))
        self.category_combo.blockSignals(False)

    def _quick_new_category(self):
        zh = self.language == 'zh'
        dlg = QInputDialog(self)
        dlg.setWindowTitle('新建分类' if zh else 'New category')
        dlg.setLabelText('分类名称，例如：集成服务器' if zh else 'Name')
        dlg.setOkButtonText('确定' if zh else 'OK')
        dlg.setCancelButtonText('取消' if zh else 'Cancel')
        if not dlg.exec():
            return
        name = (dlg.textValue() or '').strip()
        if not name:
            return
        self._categories, cid = ensure_category(self._categories, name)
        self._fill_category_combo()
        idx = self.category_combo.findData(cid)
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        else:
            self.category_combo.setEditText(name)

    def categories(self) -> list[dict]:
        return list(self._categories)

    def _on_password_changed(self, *_args):
        self._password_dirty = True

    def _toggle_password_visible(self, checked: bool):
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _password_for_use(self) -> str:
        from tools.ops_ssh import _clean_password
        typed = _clean_password(self.password_edit.text())
        if typed:
            return typed
        # 未改动时沿用已保存密文
        if self._server.get('password_token') and not self._password_dirty:
            return _clean_password(decrypt_secret(self._server.get('password_token')))
        return ''

    def _test(self):
        zh = self.language == 'zh'
        if not self.host_edit.text().strip() or not self.user_edit.text().strip():
            show_warning(self, 'PengTools', '请先填写主机和用户名' if zh else 'Host and username required')
            return
        if not self._password_for_use():
            show_warning(self, 'PengTools', '请先输入或粘贴密码（或使用已保存密码）' if zh else 'Password required')
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText('测试中…' if zh else 'Testing…')
        worker = _SshTestWorker(
            self._draft_server(for_persist=False),
            password_override=self._password_for_use(),
            timeout_sec=15,
            parent=self,
        )
        worker.finished_ok.connect(self._on_test_ok)
        worker.finished_err.connect(self._on_test_err)
        worker.finished.connect(lambda: self._restore_test_btn())
        self._test_worker = worker
        worker.start()

    def _restore_test_btn(self):
        self.test_btn.setEnabled(True)
        self.test_btn.setText('测试连接' if self.language == 'zh' else 'Test')

    def _on_test_ok(self, result: dict):
        show_success(self, 'PengTools', format_connection_ok(result, self.language))

    def _on_test_err(self, message: str):
        show_error(self, 'PengTools', message or ('连接失败' if self.language == 'zh' else 'Failed'))

    def _draft_server(self, *, for_persist: bool = True) -> dict:
        data = dict(self._server)
        # 分类：优先 combo data；手写新名称则 ensure_category
        cid = self.category_combo.currentData()
        typed_name = self.category_combo.currentText().strip()
        if cid and self.category_combo.findData(cid) == self.category_combo.currentIndex():
            cat = next((c for c in self._categories if c.get('id') == cid), None)
            gname = (cat.get('name') if cat else typed_name) or ''
            if cat and cat.get('id') == UNCATEGORIZED_ID:
                gname = ''
        else:
            self._categories, cid = ensure_category(self._categories, typed_name)
            gname = typed_name if typed_name and typed_name != DEFAULT_CATEGORY_NAME else ''
        services = self._collect_services()
        default_path = ''
        for svc in services:
            if svc.get('enabled', True) and str(svc.get('log_path') or '').strip():
                default_path = str(svc['log_path']).strip()
                break
        if not default_path and services:
            default_path = str(services[0].get('log_path') or '').strip()
        data.update({
            'name': self.name_edit.text().strip() or self.host_edit.text().strip(),
            'host': self.host_edit.text().strip(),
            'port': self.port_spin.value(),
            'username': self.user_edit.text().strip(),
            'default_log_path': default_path,
            'services': services,
            'category_id': cid or UNCATEGORIZED_ID,
            'group': gname,
            'enabled': True,
        })
        from tools.ops_ssh import _clean_password
        typed = _clean_password(self.password_edit.text())
        save_pwd = bool(self.save_password_check.isChecked()) if hasattr(self, 'save_password_check') else True
        if for_persist:
            if typed and save_pwd:
                # 明文交给 normalize → encrypt_secret 落盘
                data['password'] = typed
                data.pop('password_token', None)
            elif typed and not save_pwd:
                # 本次连接可用，但不写入 token
                data['password'] = typed
                data['password_token'] = ''
                data['_skip_persist_password'] = True
            elif self._server.get('password_token') and not self._password_dirty:
                # 保留原加密 token
                data['password_token'] = self._server.get('password_token')
                data.pop('password', None)
            elif self._server.get('password_token') and self._password_dirty and not typed:
                # 用户清空密码框：删除已存密码
                data['password_token'] = ''
                data.pop('password', None)
            else:
                data.pop('password', None)
        else:
            # 测试连接：只带可用密码，不强行改存储语义
            if typed:
                data['password'] = typed
            elif self._server.get('password_token') and not self._password_dirty:
                data['password_token'] = self._server.get('password_token')
            # 若框里有「显示后改过」的空串，仍尽量用已存 token
            elif self._server.get('password_token') and not typed:
                data['password_token'] = self._server.get('password_token')
        return data

    def _accept(self):
        zh = self.language == 'zh'
        if not self.host_edit.text().strip() or not self.user_edit.text().strip():
            show_warning(self, 'PengTools', '请填写主机和用户名' if zh else 'Host and username required')
            return
        if not self._password_for_use():
            show_warning(
                self, 'PengTools',
                '请输入或粘贴密码（已保存过的可留空）' if zh else 'Enter/paste password (or leave blank if saved)',
            )
            return
        self.accept()

    def server_data(self) -> dict:
        return self._draft_server(for_persist=True)



class LogSettingsDialog(QDialog):
    """截取/导出可配置项（不占左侧主操作区）。"""

    def __init__(self, language: str = 'zh', parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setObjectName('log-settings-dialog')
        self.setWindowTitle('截取设置' if zh else 'Capture settings')
        self.setMinimumWidth(420)
        self.setMinimumHeight(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        s = load_log_settings()
        self.context_spin = QSpinBox()
        self.context_spin.setRange(0, 200)
        self.context_spin.setValue(int(s.get('context_lines') or 20))
        self.tail_spin = QSpinBox()
        self.tail_spin.setRange(20, 5000)
        self.tail_spin.setValue(int(s.get('tail_lines') or 100))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(int(s.get('max_workers') or 4))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(int(s.get('timeout_sec') or 30))
        self.case_check = QCheckBox('忽略大小写' if zh else 'Ignore case')
        self.case_check.setChecked(bool(s.get('case_insensitive', True)))
        self.remote_check = QCheckBox('默认展开远端目录' if zh else 'Show remote browser by default')
        self.remote_check.setChecked(bool(s.get('show_remote_browser', False)))
        form.addRow('上下文行数' if zh else 'Context lines', self.context_spin)
        form.addRow('看尾部行数' if zh else 'Tail lines', self.tail_spin)
        form.addRow('并行数' if zh else 'Parallel', self.workers_spin)
        form.addRow('超时(秒)' if zh else 'Timeout (s)', self.timeout_spin)
        form.addRow(self.case_check)
        form.addRow(self.remote_check)
        layout.addLayout(form)
        note = QLabel(
            '这些选项会保存到本机 data，下次打开仍有效。日常截取左侧只填关键字与路径。'
            if zh else
            'Saved to local data/. Left panel only needs path and keywords day-to-day.'
        )
        note.setObjectName('field-hint')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox()
        buttons.addButton('确定' if zh else 'OK', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('取消' if zh else 'Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            'context_lines': self.context_spin.value(),
            'tail_lines': self.tail_spin.value(),
            'max_workers': self.workers_spin.value(),
            'timeout_sec': self.timeout_spin.value(),
            'case_insensitive': self.case_check.isChecked(),
            'show_remote_browser': self.remote_check.isChecked(),
        }



class ServerManageDialog(QDialog):
    """管理服务器：弹框列表，不在主面板展开。"""

    def __init__(self, language='zh', servers=None, categories=None, parent=None):
        super().__init__(parent)
        self.language = language
        self._servers = [dict(s) for s in (servers or [])]
        self._categories = [dict(c) for c in (categories or load_categories())]
        self._changed = False
        zh = language == 'zh'
        self.setObjectName('server-manage-dialog')
        self.setWindowTitle('管理服务器' if zh else 'Manage servers')
        self.setMinimumSize(640, 480)
        self.resize(720, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        title = QLabel('管理服务器' if zh else 'Manage servers')
        title.setObjectName('dialog-title')
        root.addWidget(title)
        hint = QLabel(
            '在此新增/编辑/删除服务器与分类。主界面只保留当前主机下拉，避免挤占操作区。'
            if zh else
            'Add/edit/delete hosts and categories here. Main UI keeps a compact host combo.'
        )
        hint.setObjectName('field-hint')
        hint.setWordWrap(True)
        root.addWidget(hint)

        filter_row = QHBoxLayout()
        self.category_filter = QComboBox()
        size_combo(self.category_filter, 'md')
        self.category_filter.currentIndexChanged.connect(self._reload)
        filter_row.addWidget(self.category_filter, 1)
        self.manage_cat_btn = QPushButton('分类…' if zh else 'Categories…')
        apply_button(self.manage_cat_btn, 'ghost', compact=True)
        self.manage_cat_btn.clicked.connect(self._manage_categories)
        filter_row.addWidget(self.manage_cat_btn)
        root.addLayout(filter_row)

        self.list = QListWidget()
        self.list.setObjectName('ops-command-list')
        self.list.setMinimumHeight(260)
        self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        root.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton('新增' if zh else 'Add')
        apply_button(self.add_btn, 'secondary', compact=True, icon='add')
        self.add_btn.clicked.connect(self._add)
        self.edit_btn = QPushButton('编辑' if zh else 'Edit')
        apply_button(self.edit_btn, 'ghost', compact=True, icon='edit')
        self.edit_btn.clicked.connect(self._edit)
        self.test_btn = QPushButton('测试连接' if zh else 'Test')
        apply_button(self.test_btn, 'ghost', compact=True)
        self.test_btn.clicked.connect(self._test)
        self.del_btn = QPushButton('删除' if zh else 'Delete')
        apply_button(self.del_btn, 'danger', compact=True, icon='delete')
        self.del_btn.clicked.connect(self._delete)
        for b in (self.add_btn, self.edit_btn, self.test_btn, self.del_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        close_btn = QPushButton('完成' if zh else 'Done')
        apply_button(close_btn, 'primary', compact=True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
        self._fill_filter()
        self._reload()

    def _fill_filter(self):
        zh = self.language == 'zh'
        self.category_filter.blockSignals(True)
        cur = self.category_filter.currentData() if self.category_filter.count() else 'all'
        self.category_filter.clear()
        self.category_filter.addItem('全部分类' if zh else 'All', 'all')
        for cat in sorted(self._categories, key=lambda c: (int(c.get('sort') or 0), c.get('name') or '')):
            self.category_filter.addItem(cat.get('name') or DEFAULT_CATEGORY_NAME, cat.get('id'))
        idx = self.category_filter.findData(cur or 'all')
        self.category_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.category_filter.blockSignals(False)

    def _server_label(self, server: dict) -> str:
        name = str(server.get('name') or server.get('host') or '')
        host = str(server.get('host') or '')
        user = str(server.get('username') or '')
        return f'{name}  ·  {user}@{host}' if host else name

    def _reload(self):
        self.list.clear()
        cid = self.category_filter.currentData() or 'all'
        for s in self._servers:
            if cid != 'all' and (s.get('category_id') or UNCATEGORIZED_ID) != cid:
                continue
            item = QListWidgetItem(self._server_label(s))
            item.setData(Qt.ItemDataRole.UserRole, s.get('id'))
            self.list.addItem(item)

    def _current_server(self) -> dict | None:
        item = self.list.currentItem()
        if not item:
            return None
        sid = item.data(Qt.ItemDataRole.UserRole)
        return next((s for s in self._servers if s.get('id') == sid), None)

    def _persist(self):
        save_server_store(servers=self._servers, categories=self._categories)
        self._changed = True

    @staticmethod
    def _for_store(data: dict) -> dict:
        item = dict(data or {})
        skip = bool(item.pop('_skip_persist_password', False))
        if skip:
            item.pop('password', None)
            item['password_token'] = ''
        return item

    def _add(self):
        dlg = ServerEditorDialog(self.language, server=None, categories=self._categories, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.server_data()
        import uuid
        data['id'] = data.get('id') or uuid.uuid4().hex[:12]
        self._categories = dlg.categories()
        self._servers.append(self._for_store(data))
        self._persist()
        self._fill_filter()
        self._reload()

    def _edit(self):
        server = self._current_server()
        if not server:
            show_warning(self, 'PengTools', '请先选择服务器' if self.language == 'zh' else 'Select a server')
            return
        dlg = ServerEditorDialog(self.language, server=server, categories=self._categories, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.server_data()
        data['id'] = server.get('id')
        self._categories = dlg.categories()
        updated = self._for_store(data)
        self._servers = [updated if s.get('id') == server.get('id') else s for s in self._servers]
        self._persist()
        self._fill_filter()
        self._reload()

    def _delete(self):
        server = self._current_server()
        if not server:
            return
        zh = self.language == 'zh'
        if not confirm_action(
            self,
            '删除服务器' if zh else 'Delete server',
            f'确定删除「{server.get("name") or server.get("host")}」？' if zh else f'Delete {server.get("name")}?',
            confirm_text='删除' if zh else 'Delete',
            danger=True,
        ):
            return
        sid = server.get('id')
        self._servers = [s for s in self._servers if s.get('id') != sid]
        self._persist()
        self._reload()

    def _test(self):
        server = self._current_server()
        if not server:
            show_warning(self, 'PengTools', '请先选择服务器' if self.language == 'zh' else 'Select a server')
            return
        worker = _SshTestWorker(server, parent=self)
        worker.finished_ok.connect(lambda r: show_success(self, 'PengTools', format_connection_ok(r, self.language)))
        worker.finished_err.connect(lambda m: show_error(self, 'PengTools', m or '连接失败'))
        self._test_worker = worker
        worker.start()

    def _manage_categories(self):
        dlg = CategoryManageDialog(self.language, self._categories, self._servers, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cats, servers = dlg.result_data()
        self._categories = cats
        self._servers = servers
        self._persist()
        self._fill_filter()
        self._reload()

    def result_data(self) -> tuple[list[dict], list[dict], bool]:
        return list(self._servers), list(self._categories), self._changed


class CommandHistoryDialog(QDialog):
    """历史命令弹框：按日期展示，右键带入/发送到终端。"""

    insert_requested = pyqtSignal(str)
    send_requested = pyqtSignal(str)

    def __init__(self, language='zh', parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setObjectName('cmd-history-dialog')
        self.setWindowTitle('命令历史' if zh else 'Command history')
        self.setMinimumSize(560, 420)
        self.resize(620, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        title = QLabel('命令历史' if zh else 'Command history')
        title.setObjectName('dialog-title')
        root.addWidget(title)
        hint = QLabel(
            '双击或点「填入命令框」写入下方输入框；右键可发送到当前终端。仅保存在本机。'
            if zh else
            'Double-click to fill command bar; right-click to send to terminal. Local only.'
        )
        hint.setObjectName('field-hint')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.list = QTreeWidget()
        self.list.setObjectName('ops-cmd-history-list')
        self.list.setHeaderLabels(['时间', '命令', '主机'] if zh else ['Time', 'Command', 'Host'])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(self._on_double)
        hdr = self.list.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        fill_btn = QPushButton('填入命令框' if zh else 'Fill bar')
        apply_button(fill_btn, 'secondary', compact=True)
        fill_btn.clicked.connect(self._emit_insert)
        send_btn = QPushButton('发送到终端' if zh else 'Send to term')
        apply_button(send_btn, 'primary', compact=True)
        send_btn.clicked.connect(self._emit_send)
        clear_btn = QPushButton('清空历史' if zh else 'Clear')
        apply_button(clear_btn, 'ghost', compact=True)
        clear_btn.clicked.connect(self._clear)
        close_btn = QPushButton('关闭' if zh else 'Close')
        apply_button(close_btn, 'ghost', compact=True)
        close_btn.clicked.connect(self.accept)
        row.addWidget(fill_btn)
        row.addWidget(send_btn)
        row.addWidget(clear_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        root.addLayout(row)
        self._reload()

    def _reload(self):
        self.list.clear()
        items = list(reversed(load_history()))
        for it in items:
            ts = str(it.get('ts') or '')
            # 2026-07-23T12:00:00 -> 07-23 12:00
            display_ts = ts.replace('T', ' ')
            if len(display_ts) >= 16:
                display_ts = display_ts[5:16]
            cmd = str(it.get('cmd') or '')
            host = str(it.get('host') or '')
            row = QTreeWidgetItem([display_ts, cmd, host])
            row.setData(0, Qt.ItemDataRole.UserRole, cmd)
            row.setToolTip(1, cmd)
            self.list.addTopLevelItem(row)

    def _current_cmd(self) -> str:
        item = self.list.currentItem()
        if not item:
            return ''
        return str(item.data(0, Qt.ItemDataRole.UserRole) or item.text(1) or '').strip()

    def _on_double(self, *_):
        self._emit_insert()

    def _emit_insert(self):
        cmd = self._current_cmd()
        if cmd:
            self.insert_requested.emit(cmd)

    def _emit_send(self):
        cmd = self._current_cmd()
        if cmd:
            self.send_requested.emit(cmd)

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        zh = self.language == 'zh'
        menu = QMenu(self)
        a1 = menu.addAction('填入命令框' if zh else 'Fill command bar')
        a2 = menu.addAction('发送到终端' if zh else 'Send to terminal')
        a3 = menu.addAction('复制命令' if zh else 'Copy')
        chosen = menu.exec(self.list.mapToGlobal(pos))
        cmd = self._current_cmd()
        if not cmd:
            return
        if chosen is a1:
            self.insert_requested.emit(cmd)
        elif chosen is a2:
            self.send_requested.emit(cmd)
        elif chosen is a3:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(cmd)

    def _clear(self):
        zh = self.language == 'zh'
        if not confirm_action(
            self,
            '清空历史' if zh else 'Clear history',
            '确定清空本机命令历史？' if zh else 'Clear all local command history?',
            confirm_text='清空' if zh else 'Clear',
            danger=True,
        ):
            return
        save_history([])
        self._reload()


class OpsLogPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._servers: list[dict] = []
        self._categories: list[dict] = []
        self._category_filter = 'all'  # all | category_id
        self._running = False
        # 会话状态全部挂在终端标签上，禁止全局共用连接
        self._active_term_index: int | None = None
        self._restoring_session = False
        self._worker: _Worker | None = None
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._bridge = _ExportBridge(self)
        self._bridge.result_ready.connect(self._on_result_row)
        self._bridge.finished.connect(self._on_export_finished)
        self._bridge.failed.connect(self._on_export_failed)
        self._setup_ui()
        self.set_language(language)
        self._reload_servers()
        self._load_settings_to_ui()
        self._set_session_connected(False)
        # 主题切换时刷新终端控制台岛配色
        try:
            from ui.theme_manager import ThemeManager
            ThemeManager.instance().add_listener(self._on_theme_changed_for_terms)
        except Exception:
            pass

    # ── UI ──────────────────────────────────────────────

    # ── 多标签终端 / 每会话独立连接与左侧上下文 ───────────
    def _new_session_dict(self, widget) -> dict:
        return {
            'widget': widget,
            'client': None,
            'server_id': None,
            'remote_cwd': '/',
            'remote_entries': [],
            'log_path': '',
            'log_file': '',
            'service_path': '',
            'service_name': '',
            'keyword': '',
            'context_lines': 20,
            'connected': False,
            'title': '',
        }

    def _create_terminal_tab(self, title: str = '会话', *, copy_from_current: bool = False):
        from ui.ssh_terminal import SshTerminalWidget
        term = SshTerminalWidget()
        term.setMinimumHeight(180)
        if getattr(self, 'term_tabs', None) is not None and self.term_tabs.count() > 0:
            self._save_session_at(self.term_tabs.currentIndex())
        idx = self.term_tabs.addTab(term, title)
        sess = self._new_session_dict(term)
        sess['title'] = title
        if copy_from_current and self._term_sessions:
            prev_idx = self._active_term_index
            if prev_idx is None or not (0 <= prev_idx < len(self._term_sessions)):
                prev_idx = len(self._term_sessions) - 1
            prev = self._term_sessions[prev_idx]
            for k in ('server_id', 'log_path', 'log_file', 'service_path', 'service_name', 'keyword', 'context_lines'):
                sess[k] = prev.get(k)
        self._term_sessions.append(sess)
        self.term_tabs.blockSignals(True)
        self.term_tabs.setCurrentIndex(idx)
        self.term_tabs.blockSignals(False)
        self._active_term_index = idx
        self.terminal = term
        self.console = term
        self._restore_session_at(idx)
        return term

    def _current_session(self) -> dict | None:
        if not hasattr(self, '_term_sessions') or not self._term_sessions:
            return None
        idx = self.term_tabs.currentIndex() if hasattr(self, 'term_tabs') else 0
        if idx < 0 or idx >= len(self._term_sessions):
            idx = max(0, len(self._term_sessions) - 1)
        return self._term_sessions[idx]

    def _session_client(self):
        sess = self._current_session()
        return sess.get('client') if sess else None

    @property
    def _client(self):
        return self._session_client()

    @_client.setter
    def _client(self, value):
        sess = self._current_session()
        if sess is not None:
            sess['client'] = value
            sess['connected'] = bool(value)

    @property
    def _session_server_id(self):
        sess = self._current_session()
        return sess.get('server_id') if sess else None

    @_session_server_id.setter
    def _session_server_id(self, value):
        sess = self._current_session()
        if sess is not None:
            sess['server_id'] = value

    @property
    def _remote_cwd(self):
        sess = self._current_session()
        return (sess.get('remote_cwd') if sess else '/') or '/'

    @_remote_cwd.setter
    def _remote_cwd(self, value):
        sess = self._current_session()
        if sess is not None:
            sess['remote_cwd'] = value or '/'

    def _current_terminal(self):
        sess = self._current_session()
        if sess and sess.get('widget') is not None:
            return sess['widget']
        w = self.term_tabs.currentWidget() if hasattr(self, 'term_tabs') else None
        return w if w is not None else getattr(self, 'terminal', None)

    def _save_session_at(self, index: int):
        if index is None or index < 0 or index >= len(getattr(self, '_term_sessions', [])):
            return
        if self._restoring_session:
            return
        sess = self._term_sessions[index]
        if hasattr(self, 'server_combo'):
            sid = self.server_combo.currentData()
            if sid:
                sess['server_id'] = sid
        if hasattr(self, 'log_path_edit'):
            sess['log_path'] = self.log_path_edit.text().strip()
        if hasattr(self, 'log_file_combo') and self.log_file_combo.count():
            data = self.log_file_combo.currentData()
            sess['log_file'] = str(data or '').strip()
        if hasattr(self, 'service_combo'):
            sess['service_name'] = self.service_combo.currentText().strip()
            sess['service_path'] = str(self.service_combo.currentData() or '').strip()
        if hasattr(self, 'keyword_edit'):
            sess['keyword'] = self.keyword_edit.text()
        if hasattr(self, 'context_spin'):
            try:
                sess['context_lines'] = int(self.context_spin.value())
            except Exception:
                pass
        if hasattr(self, 'path_edit'):
            cwd = self.path_edit.text().strip()
            if cwd:
                sess['remote_cwd'] = cwd

    def _restore_session_at(self, index: int):
        if index is None or index < 0 or index >= len(getattr(self, '_term_sessions', [])):
            return
        self._restoring_session = True
        try:
            sess = self._term_sessions[index]
            term = sess.get('widget')
            self.terminal = term
            self.console = term
            for i in range(self.term_tabs.count()):
                w = self.term_tabs.widget(i)
                view = getattr(w, 'view', None)
                if view is not None and hasattr(view, 'set_ui_active'):
                    view.set_ui_active(i == index)
            prefer = sess.get('server_id')
            if prefer and hasattr(self, 'server_combo'):
                self.server_combo.blockSignals(True)
                idx = self.server_combo.findData(prefer)
                if idx >= 0:
                    self.server_combo.setCurrentIndex(idx)
                self.server_combo.blockSignals(False)
            server = self._current_server()
            self._refresh_service_combo(server)
            if hasattr(self, 'service_combo') and sess.get('service_name'):
                self.service_combo.blockSignals(True)
                found = False
                for i in range(self.service_combo.count()):
                    if self.service_combo.itemText(i) == sess.get('service_name'):
                        self.service_combo.setCurrentIndex(i)
                        found = True
                        break
                if not found and sess.get('service_path'):
                    for i in range(self.service_combo.count()):
                        if self.service_combo.itemData(i) == sess.get('service_path'):
                            self.service_combo.setCurrentIndex(i)
                            break
                self.service_combo.blockSignals(False)
            if hasattr(self, 'log_path_edit'):
                path = sess.get('log_path') or ''
                if not path and sess.get('service_path'):
                    path = sess.get('service_path') or ''
                self.log_path_edit.blockSignals(True)
                self.log_path_edit.setText(path)
                self.log_path_edit.blockSignals(False)
            if hasattr(self, 'keyword_edit'):
                self.keyword_edit.setText(sess.get('keyword') or '')
            if hasattr(self, 'context_spin') and sess.get('context_lines') is not None:
                try:
                    self.context_spin.setValue(int(sess.get('context_lines') or 20))
                except Exception:
                    pass
            if hasattr(self, 'path_edit'):
                self.path_edit.setText(sess.get('remote_cwd') or '/')
            self._fill_remote_tree(sess.get('remote_entries') or [])
            prefer_file = sess.get('log_file') or ''
            if sess.get('client') and (sess.get('log_path') or prefer_file):
                self._refresh_log_file_combo()
            elif prefer_file and hasattr(self, 'log_file_combo'):
                self._set_log_file_combo_items([{
                    'name': prefer_file.rsplit('/', 1)[-1],
                    'path': prefer_file,
                    'mtime_text': '',
                    'size_text': '',
                }], prefer_path=prefer_file)
            connected = bool(sess.get('client') and sess.get('connected'))
            self._set_session_connected(connected)
            self._update_session_status_label(sess)
            self._refresh_output_context()
            if term is not None:
                term.setFocus()
        finally:
            self._restoring_session = False

    def _update_session_status_label(self, sess: dict | None = None):
        zh = self.language == 'zh'
        sess = sess or self._current_session() or {}
        if not sess.get('client'):
            self.session_status.setText('未连接 · 本标签独立会话' if zh else 'Disconnected · per-tab session')
            return
        sid = sess.get('server_id')
        server = next((s for s in self._servers if s.get('id') == sid), None) if sid else None
        name = (server or {}).get('name') or (server or {}).get('host') or ''
        host = (server or {}).get('host') or ''
        cwd = sess.get('remote_cwd') or '/'
        alive = False
        w = sess.get('widget')
        if w is not None:
            alive = bool(getattr(w, 'shell_alive', False))
        if zh:
            self.session_status.setText(
                f'已连接 · {name} · {host} · cwd {cwd}' + (' · 终端就绪' if alive else ' · 终端未就绪')
            )
        else:
            self.session_status.setText(f'Connected · {name} · {cwd}')

    def _on_term_tab_changed(self, index: int):
        prev = self._active_term_index
        if prev is not None and prev != index:
            self._save_session_at(prev)
        self._active_term_index = index
        self._restore_session_at(index)

    def _close_session_resources(self, sess: dict | None):
        if not sess:
            return
        w = sess.get('widget')
        if w is not None:
            try:
                w.detach()
            except Exception:
                pass
        client = sess.get('client')
        if client is not None:
            try:
                close_ssh_client(client)
            except Exception:
                pass
        sess['client'] = None
        sess['connected'] = False
        sess['remote_entries'] = []

    def _close_term_tab(self, index: int):
        if index < 0 or index >= self.term_tabs.count():
            return
        zh = self.language == 'zh'
        if self.term_tabs.count() <= 1:
            if 0 <= index < len(self._term_sessions):
                self._close_session_resources(self._term_sessions[index])
                self._term_sessions[index] = self._new_session_dict(self.term_tabs.widget(index))
                self.term_tabs.setTabText(index, '会话1' if zh else 'Session 1')
            self._active_term_index = index
            self._restore_session_at(index)
            self._console_append('[本会话已断开，标签仍保留]' if zh else '[session disconnected]')
            return
        if 0 <= index < len(self._term_sessions):
            self._close_session_resources(self._term_sessions[index])
            del self._term_sessions[index]
        self.term_tabs.blockSignals(True)
        self.term_tabs.removeTab(index)
        self.term_tabs.blockSignals(False)
        new_idx = min(index, self.term_tabs.count() - 1)
        self._active_term_index = new_idx
        self.term_tabs.setCurrentIndex(new_idx)
        self._restore_session_at(new_idx)

    def _add_term_tab(self):
        if self.term_tabs.count() >= 4:
            show_warning(self, 'PengTools', '最多 4 个终端标签（省内存）' if self.language == 'zh' else 'Max 4 terminal tabs')
            return
        n = self.term_tabs.count() + 1
        self._create_terminal_tab(
            f'会话{n}' if self.language == 'zh' else f'Session {n}',
            copy_from_current=True,
        )
        self._console_append(
            f'[新会话{n}] 独立连接；左侧主机/服务/路径与本标签绑定'
            if self.language == 'zh' else
            f'[session {n}] independent connection'
        )

    def _reload_cmd_history(self):
        # 历史已改为弹框；保留空实现兼容
        return

    def _cmd_bar_text(self) -> str:
        if hasattr(self, 'cmd_input') and self.cmd_input is not None:
            return self.cmd_input.text().strip()
        if hasattr(self, 'cmd_history_combo'):
            return self.cmd_history_combo.currentText().strip()
        return ''

    def _set_cmd_bar_text(self, text: str):
        if hasattr(self, 'cmd_input') and self.cmd_input is not None:
            self.cmd_input.setText(text or '')
            self.cmd_input.setFocus()
            self.cmd_input.end(False)
            return
        if hasattr(self, 'cmd_history_combo'):
            self.cmd_history_combo.setEditText(text or '')

    def _send_cmd_bar(self, cmd: str | None = None):
        term = self._current_terminal()
        if term is None or not getattr(term, 'shell_alive', False):
            show_warning(self, 'PengTools', '请先连接服务器' if self.language == 'zh' else 'Connect first')
            return
        text = (cmd if isinstance(cmd, str) else None)
        if text is None:
            text = self._cmd_bar_text()
        text = (text or '').strip()
        if not text:
            return
        host = ''
        server = self._current_server()
        if server:
            host = str(server.get('host') or '')
        term.send_command_line(text)
        append_command(text, host=host)
        self._set_cmd_bar_text('')
        term.setFocus()

    def _fill_cmd_bar_from_history(self):
        self._open_cmd_history_dialog()

    def _open_cmd_history_dialog(self):
        dlg = CommandHistoryDialog(self.language, parent=self)
        dlg.insert_requested.connect(self._set_cmd_bar_text)
        dlg.send_requested.connect(lambda c: self._send_cmd_bar(c))
        dlg.exec()

    def _on_service_combo_changed(self, *_):
        path = self.service_combo.currentData()
        if path:
            self.log_path_edit.setText(str(path))
            self._refresh_log_file_combo()
        if hasattr(self, '_refresh_output_context'):
            self._refresh_output_context()

    def _on_log_path_edited(self):
        self._refresh_log_file_combo()

    def _on_log_file_combo_changed(self, *_):
        if self._log_files_loading:
            return
        if hasattr(self, '_refresh_output_context'):
            self._refresh_output_context()

    def _effective_log_path(self) -> str:
        """抓取用的实际文件路径：优先下拉选中的 .log，否则路径框。"""
        if hasattr(self, 'log_file_combo') and self.log_file_combo.count():
            data = self.log_file_combo.currentData()
            if data:
                return str(data).strip()
            # 无 data 时用展示文本里解析不出，退回路径框
            text = self.log_file_combo.currentText().strip()
            if text and text.startswith('/'):
                return text
        return (self.log_path_edit.text() if hasattr(self, 'log_path_edit') else '').strip()

    def _set_log_file_combo_items(self, files: list[dict], *, prefer_path: str = ''):
        if not hasattr(self, 'log_file_combo'):
            return
        self._log_files_loading = True
        self.log_file_combo.blockSignals(True)
        self.log_file_combo.clear()
        prefer = str(prefer_path or '').strip()
        pick = 0
        for i, f in enumerate(files or []):
            name = str(f.get('name') or '')
            path = str(f.get('path') or '')
            mtime = str(f.get('mtime_text') or '')
            size = str(f.get('size_text') or '')
            bits = [name]
            if mtime:
                bits.append(mtime)
            if size and size != '--':
                bits.append(size)
            label = '  ·  '.join(bits)
            self.log_file_combo.addItem(label, path)
            if prefer and path == prefer:
                pick = i
        if not files:
            zh = self.language == 'zh'
            base = (self.log_path_edit.text() or '').strip()
            if base:
                self.log_file_combo.addItem(
                    ('（当前路径，未列出更多日志）' if zh else '(path only)'),
                    base,
                )
            else:
                self.log_file_combo.addItem(
                    ('请先填写日志目录并连接服务器' if zh else 'Set log dir and connect'),
                    '',
                )
        self.log_file_combo.setCurrentIndex(pick)
        self.log_file_combo.blockSignals(False)
        self._log_files_loading = False
        self._refresh_output_context()

    def _refresh_log_file_combo(self):
        """连接后根据日志目录/路径刷新 .log 下拉。"""
        if not hasattr(self, 'log_file_combo'):
            return
        base = (self.log_path_edit.text() or '').strip()
        if not base:
            self._set_log_file_combo_items([])
            return
        if not self._client:
            # 未连接：若像文件名则直接作为唯一项
            name = base.rsplit('/', 1)[-1]
            if '.' in name:
                self._set_log_file_combo_items([{
                    'name': name, 'path': base, 'mtime_text': '', 'size_text': '',
                }], prefer_path=base)
            else:
                self._set_log_file_combo_items([])
            return
        client = self._client
        prefer = self._effective_log_path() if self.log_file_combo.count() else base

        def job():
            return list_remote_log_files(client, base)

        def ok(files):
            self._set_log_file_combo_items(files or [], prefer_path=prefer)
            n = len(files or [])
            if n:
                self._console_append(
                    f'[日志文件] 在 {base} 下找到 {n} 个，默认选最新'
                    if self.language == 'zh' else
                    f'[logs] {n} file(s) under {base}'
                )

        def fail(msg):
            # 列目录失败时仍把 base 当作路径
            name = base.rsplit('/', 1)[-1]
            self._set_log_file_combo_items([{
                'name': name or base, 'path': base, 'mtime_text': '', 'size_text': '',
            }], prefer_path=base)
            self._console_append(f'[日志文件] {msg}')

        self._run_bg(job, ok, '', on_fail=fail)

    def _refresh_service_combo(self, server: dict | None):
        if not hasattr(self, 'service_combo'):
            return
        self.service_combo.blockSignals(True)
        self.service_combo.clear()
        if not server:
            self.service_combo.blockSignals(False)
            return
        for svc in server_services(server, only_enabled=False):
            name = str(svc.get('name') or '服务').strip() or '服务'
            path = str(svc.get('log_path') or '')
            # 只显示服务名，路径放 tooltip / data
            self.service_combo.addItem(name, path)
            idx = self.service_combo.count() - 1
            if path:
                self.service_combo.setItemData(idx, path, Qt.ItemDataRole.ToolTipRole)
        if self.service_combo.count() == 0:
            p = primary_log_path(server)
            if p:
                self.service_combo.addItem('默认' if self.language == 'zh' else 'Default', p)
        self.service_combo.blockSignals(False)
        if self.service_combo.count():
            self.service_combo.setCurrentIndex(0)
            self._on_service_combo_changed()

    def _card(self):
        frame = QFrame()
        apply_surface(frame, 'card')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        return frame, layout

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.connect_btn = QPushButton()
        apply_button(self.connect_btn, 'primary', icon='terminal')
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.header, self.title_label, self.subtitle_label = make_page_header(
            '', '', icon_role='search', primary_button=self.connect_btn,
        )
        root.addWidget(self.header)

        if not paramiko_available():
            self.dep_note = QLabel()
            self.dep_note.setObjectName('ops-warning')
            self.dep_note.setWordWrap(True)
            root.addWidget(self.dep_note)
        else:
            self.dep_note = None

        # Main: left ops | right output
        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split.setObjectName('ops-main-split')
        self.main_split.setChildrenCollapsible(False)
        root.addWidget(self.main_split, 1)

        # ========== LEFT（可拖动变宽；内部可横向滚动避免截断）==========
        from PyQt6.QtWidgets import QScrollArea, QSizePolicy
        left_scroll = QScrollArea()
        left_scroll.setObjectName('ops-left-scroll')
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(300)
        # 不设 MaximumWidth，交给 splitter 左右拖
        left_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left_scroll_host = QWidget()
        left_scroll_host.setObjectName('ops-left-panel')
        left_scroll_host.setMinimumWidth(300)
        left_root = QVBoxLayout(left_scroll_host)
        left_root.setContentsMargins(4, 0, 8, 0)
        left_root.setSpacing(8)
        left_scroll.setWidget(left_scroll_host)

        mode_row = QHBoxLayout()
        self.mode_session_btn = QPushButton()
        self.mode_export_btn = QPushButton()
        self.mode_session_btn.setCheckable(True)
        self.mode_export_btn.setCheckable(True)
        self.mode_session_btn.setChecked(True)
        apply_button(self.mode_session_btn, 'secondary', compact=True)
        apply_button(self.mode_export_btn, 'ghost', compact=True)
        self.mode_session_btn.clicked.connect(lambda: self._set_work_mode('session'))
        self.mode_export_btn.clicked.connect(lambda: self._set_work_mode('export'))
        mode_row.addWidget(self.mode_session_btn, 1)
        mode_row.addWidget(self.mode_export_btn, 1)
        self.settings_btn = QPushButton()
        apply_button(self.settings_btn, 'ghost', compact=True, icon='settings')
        self.settings_btn.clicked.connect(self._open_log_settings)
        mode_row.addWidget(self.settings_btn)
        left_root.addLayout(mode_row)

        # —— 当前主机（管理走弹框，不在模块内展开）——
        left, left_l = self._card()
        left_l.setSpacing(6)
        pick_row = QHBoxLayout()
        self.server_title = QLabel()
        self.server_title.setObjectName('section-title')
        pick_row.addWidget(self.server_title)
        self.server_combo = QComboBox()
        size_combo(self.server_combo, 'md')
        self.server_combo.currentIndexChanged.connect(self._on_server_combo_changed)
        pick_row.addWidget(self.server_combo, 1)
        self.server_toggle_btn = QPushButton()  # 兼容旧名：现为「管理」弹框
        apply_button(self.server_toggle_btn, 'secondary', compact=True)
        self.server_toggle_btn.setCheckable(False)
        self.server_toggle_btn.clicked.connect(self._open_server_manage_dialog)
        pick_row.addWidget(self.server_toggle_btn)
        left_l.addLayout(pick_row)
        self.session_status = QLabel()
        self.session_status.setObjectName('small-label')
        self.session_status.setWordWrap(True)
        left_l.addWidget(self.session_status)

        # 兼容旧代码引用的隐藏控件（不再展开占用左侧）
        self.server_list_body = QWidget()
        self.server_list_body.hide()
        self.category_filter = QComboBox()
        self.category_filter.hide()
        self.manage_cat_btn = QPushButton()
        self.manage_cat_btn.hide()
        self.add_btn = QPushButton()
        self.add_btn.hide()
        self.edit_btn = QPushButton()
        self.edit_btn.hide()
        self.test_btn = QPushButton()
        self.test_btn.hide()
        self.del_btn = QPushButton()
        self.del_btn.hide()
        self.server_list = QListWidget()
        self.server_list.hide()
        left_root.addWidget(left)

        # —— 抓取日志（主区域，默认展开）——
        self.session_ops, sess_l = self._card()
        sess_l.setSpacing(4)
        self.quick_title = QLabel()
        self.quick_title.setObjectName('section-title')
        sess_l.addWidget(self.quick_title)
        form = QFormLayout()
        form.setSpacing(4)
        form.setContentsMargins(0, 0, 0, 0)
        self.service_combo = QComboBox()
        size_combo(self.service_combo, 'md')
        self.service_combo.currentIndexChanged.connect(self._on_service_combo_changed)
        # 可绑目录：路径框 + 刷新日志列表
        path_wrap = QWidget()
        path_l = QHBoxLayout(path_wrap)
        path_l.setContentsMargins(0, 0, 0, 0)
        path_l.setSpacing(4)
        self.log_path_edit = QLineEdit()
        size_line(self.log_path_edit, 'std')
        self.log_path_edit.editingFinished.connect(self._on_log_path_edited)
        self.refresh_logs_btn = QPushButton()
        apply_button(self.refresh_logs_btn, 'ghost', compact=True, icon='refresh')
        self.refresh_logs_btn.clicked.connect(self._refresh_log_file_combo)
        path_l.addWidget(self.log_path_edit, 1)
        path_l.addWidget(self.refresh_logs_btn)
        # 目录下的 .log 文件下拉（按日期新→旧）
        self.log_file_combo = QComboBox()
        size_combo(self.log_file_combo, 'md')
        self.log_file_combo.setMinimumWidth(0)
        self.log_file_combo.currentIndexChanged.connect(self._on_log_file_combo_changed)
        self.keyword_edit = QLineEdit()
        size_line(self.keyword_edit, 'std')
        # 兼容旧字段：隐藏，解析统一走 keyword_edit
        self.extra_edit = QPlainTextEdit()
        self.extra_edit.hide()
        # 会话区可直接调上下文行数（与设置同步）
        self.context_spin = QSpinBox()
        self.context_spin.setRange(0, 200)
        self.context_spin.setValue(20)
        self.context_spin.setToolTip('命中行上下各保留多少行')
        self.case_check = QCheckBox()
        self.case_check.hide()
        self.tail_spin = QSpinBox()
        self.tail_spin.setRange(20, 5000)
        self.tail_spin.hide()
        self._form_labels = {}
        self._log_files_loading = False
        for key, w in (
            ('service', self.service_combo),
            ('log_path', path_wrap),
            ('log_file', self.log_file_combo),
            ('keyword', self.keyword_edit),
            ('context', self.context_spin),
        ):
            lab = QLabel()
            self._form_labels[key] = lab
            form.addRow(lab, w)
        sess_l.addLayout(form)
        qbtn = QHBoxLayout()
        self.tail_btn = QPushButton()
        apply_button(self.tail_btn, 'secondary', compact=True)
        self.tail_btn.clicked.connect(self._run_tail_on_session)
        self.run_grep_btn = QPushButton()
        apply_button(self.run_grep_btn, 'secondary', compact=True)
        self.run_grep_btn.clicked.connect(self._run_grep_on_session)
        self.preview_btn = QPushButton()
        apply_button(self.preview_btn, 'ghost', compact=True)
        self.preview_btn.clicked.connect(self._preview_on_console)
        self.session_export_btn = QPushButton()
        apply_button(self.session_export_btn, 'primary', compact=True, icon='export')
        self.session_export_btn.clicked.connect(self._export_current_session)
        qbtn.addWidget(self.tail_btn)
        qbtn.addWidget(self.run_grep_btn)
        qbtn.addWidget(self.session_export_btn)
        qbtn.addWidget(self.preview_btn)
        sess_l.addLayout(qbtn)
        self.stream_note = QLabel()
        self.stream_note.setObjectName('field-hint')
        self.stream_note.setWordWrap(True)
        self.stream_note.hide()
        left_root.addWidget(self.session_ops)

        # —— 远端目录（默认展示；窄栏适配：路径单独一行 + 两列表）——
        self.remote_ops, mid_l = self._card()
        mid_l.setSpacing(4)
        remote_head = QHBoxLayout()
        self.remote_title = QLabel()
        self.remote_title.setObjectName('section-title')
        remote_head.addWidget(self.remote_title, 1)
        self.remote_toggle_btn = QPushButton()
        apply_button(self.remote_toggle_btn, 'ghost', compact=True)
        self.remote_toggle_btn.setCheckable(True)
        self.remote_toggle_btn.setChecked(True)
        self.remote_toggle_btn.toggled.connect(self._toggle_remote_browser)
        remote_head.addWidget(self.remote_toggle_btn)
        mid_l.addLayout(remote_head)
        self.remote_body = QWidget()
        rb = QVBoxLayout(self.remote_body)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(4)
        # 路径独占一行，避免与按钮抢宽（不用 path 角色的 200px 下限）
        from PyQt6.QtWidgets import QSizePolicy
        from ui.field_metrics import size_field_height
        self.path_edit = QLineEdit()
        size_field_height(self.path_edit)
        self.path_edit.setMinimumWidth(0)
        self.path_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.path_edit.setPlaceholderText('当前目录路径')
        self.path_edit.returnPressed.connect(self._remote_goto_path)
        rb.addWidget(self.path_edit)
        path_row = QHBoxLayout()
        path_row.setSpacing(4)
        self.path_up_btn = QPushButton()
        apply_button(self.path_up_btn, 'ghost', compact=True)
        self.path_up_btn.clicked.connect(self._remote_go_up)
        self.path_go_btn = QPushButton()
        apply_button(self.path_go_btn, 'secondary', compact=True)
        self.path_go_btn.clicked.connect(self._remote_goto_path)
        self.path_refresh_btn = QPushButton()
        apply_button(self.path_refresh_btn, 'ghost', compact=True, icon='refresh')
        self.path_refresh_btn.clicked.connect(self._refresh_remote_dir)
        self.use_path_btn = QPushButton()
        apply_button(self.use_path_btn, 'secondary', compact=True)
        self.use_path_btn.clicked.connect(self._use_selected_as_log_path)
        for b in (self.path_up_btn, self.path_go_btn, self.path_refresh_btn, self.use_path_btn):
            path_row.addWidget(b)
        rb.addLayout(path_row)
        self.remote_tree = QTreeWidget()
        self.remote_tree.setObjectName('ops-remote-tree')
        # 对齐需求管理文件库：名称(+图标) / 类型 / 大小 / 修改时间
        self.remote_tree.setColumnCount(4)
        self.remote_tree.setHeaderLabels(['名称', '类型', '大小', '修改时间'])
        self.remote_tree.setAlternatingRowColors(True)
        self.remote_tree.setRootIsDecorated(False)
        self.remote_tree.setItemsExpandable(False)
        self.remote_tree.setIndentation(8)
        self.remote_tree.setUniformRowHeights(True)
        self.remote_tree.setIconSize(QSize(18, 18))
        self.remote_tree.setMinimumHeight(160)
        # 内容过宽可横向滚动；每列可拖拽改宽
        self.remote_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.remote_tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.remote_tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.remote_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.remote_tree.setWordWrap(False)
        self.remote_tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.remote_tree.itemDoubleClicked.connect(self._remote_item_activated)
        self.remote_tree.itemClicked.connect(self._remote_item_clicked)
        hdr = self.remote_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(False)
        hdr.setMinimumSectionSize(48)
        # Interactive：用户可左右拖列宽
        for col in range(4):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self.remote_tree.setColumnWidth(0, 160)
        self.remote_tree.setColumnWidth(1, 72)
        self.remote_tree.setColumnWidth(2, 64)
        self.remote_tree.setColumnWidth(3, 120)
        rb.addWidget(self.remote_tree, 1)
        self.remote_hint = QLabel()
        self.remote_hint.setObjectName('field-hint')
        self.remote_hint.setWordWrap(True)
        rb.addWidget(self.remote_hint)
        mid_l.addWidget(self.remote_body, 1)
        left_root.addWidget(self.remote_ops, 1)

        # —— 批量导出：目标树 + 关键字/目录 ——
        self.export_ops, el_l = self._card()
        el_l.setSpacing(4)
        self.export_server_title = QLabel()
        self.export_server_title.setObjectName('section-title')
        el_l.addWidget(self.export_server_title)
        erow = QHBoxLayout()
        self.select_all_btn = QPushButton()
        apply_button(self.select_all_btn, 'ghost', compact=True)
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn = QPushButton()
        apply_button(self.select_none_btn, 'ghost', compact=True)
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        erow.addWidget(self.select_all_btn)
        erow.addWidget(self.select_none_btn)
        erow.addStretch(1)
        el_l.addLayout(erow)
        self.export_server_list = QTreeWidget()
        self.export_server_list.setObjectName('ops-export-tree')
        self.export_server_list.setColumnCount(2)
        self.export_server_list.setHeaderLabels(['服务器 / 服务', '日志路径'])
        self.export_server_list.setHeaderHidden(False)
        self.export_server_list.setRootIsDecorated(True)
        self.export_server_list.setUniformRowHeights(True)
        self.export_server_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.export_server_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.export_server_list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.export_server_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.export_server_list.itemChanged.connect(self._on_export_tree_item_changed)
        exp_hdr = self.export_server_list.header()
        exp_hdr.setStretchLastSection(False)
        exp_hdr.setMinimumSectionSize(60)
        exp_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        exp_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.export_server_list.setColumnWidth(0, 160)
        self.export_server_list.setColumnWidth(1, 220)
        el_l.addWidget(self.export_server_list, 1)
        left_root.addWidget(self.export_ops, 1)

        self.export_rules, em_l = self._card()
        em_l.setSpacing(4)
        self.export_rule_title = QLabel()
        self.export_rule_title.setObjectName('section-title')
        em_l.addWidget(self.export_rule_title)
        eform = QFormLayout()
        eform.setSpacing(4)
        self.export_keyword = QLineEdit()
        size_line(self.export_keyword, 'std')
        self.export_extra = QPlainTextEdit()
        self.export_extra.hide()
        # 已废弃「统一路径」：隐藏保留，避免旧代码引用报错
        self.export_log_path = QLineEdit()
        self.export_log_path.hide()
        self.export_dir_edit = QLineEdit()
        size_line(self.export_dir_edit, 'std')
        browse = QPushButton()
        apply_button(browse, 'ghost', compact=True, icon='folder-open')
        browse.clicked.connect(self._browse_export_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.export_dir_edit, 1)
        dir_row.addWidget(browse)
        dir_host = QWidget()
        dir_host.setLayout(dir_row)
        # 上下文字数（与会话区/设置同步）
        self.export_context = QSpinBox()
        self.export_context.setRange(0, 200)
        self.export_context.setValue(20)
        self.export_context.setToolTip('命中行上下各保留多少行')
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.hide()
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.hide()
        self.export_case = QCheckBox()
        self.export_case.hide()
        self._export_labels = {}
        for key, w in (
            ('keyword', self.export_keyword),
            ('context', self.export_context),
            ('export_dir', dir_host),
        ):
            lab = QLabel()
            self._export_labels[key] = lab
            eform.addRow(lab, w)
        exp_hint = QLabel()
        exp_hint.setObjectName('field-hint')
        exp_hint.setWordWrap(True)
        exp_hint.setText(
            '勾选上方多台服务器/服务即可批量并行导出；'
            '各机使用「服务与日志」里配置的路径（目录会自动取最新 .log）。'
        )
        self.export_path_hint = exp_hint
        em_l.addLayout(eform)
        em_l.addWidget(exp_hint)
        self.export_btn = QPushButton()
        apply_button(self.export_btn, 'primary', icon='export')
        self.export_btn.clicked.connect(self._start_export)
        em_l.addWidget(self.export_btn)
        left_root.addWidget(self.export_rules)

        self.main_split.addWidget(left_scroll)

        # ========== RIGHT ==========
        right_host = QWidget()
        right_host.setObjectName('ops-right-panel')
        right_l = QVBoxLayout(right_host)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(6)

        top_bar = QHBoxLayout()
        self.console_title = QLabel()
        self.console_title.setObjectName('section-title')
        top_bar.addWidget(self.console_title)
        self.output_context_label = QLabel()
        self.output_context_label.setObjectName('small-label')
        top_bar.addWidget(self.output_context_label, 1)
        self.focus_term_btn = QPushButton()
        apply_button(self.focus_term_btn, 'ghost', compact=True)
        self.focus_term_btn.clicked.connect(
            lambda: self._current_terminal() and self._current_terminal().setFocus()
        )
        self.clear_console_btn = QPushButton()
        apply_button(self.clear_console_btn, 'ghost', compact=True)
        self.clear_console_btn.clicked.connect(
            lambda: self._current_terminal() and self._current_terminal().clear()
        )
        self.new_term_btn = QPushButton()
        apply_button(self.new_term_btn, 'ghost', compact=True)
        self.new_term_btn.clicked.connect(self._add_term_tab)
        top_bar.addWidget(self.focus_term_btn)
        top_bar.addWidget(self.clear_console_btn)
        top_bar.addWidget(self.new_term_btn)
        right_l.addLayout(top_bar)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(6)
        self.cmd_input = QLineEdit()
        size_line(self.cmd_input, 'std')
        self.cmd_input.setPlaceholderText('输入命令后点发送，或打开历史…')
        self.cmd_input.returnPressed.connect(self._send_cmd_bar)
        # 兼容旧名
        self.cmd_history_combo = QComboBox()
        self.cmd_history_combo.hide()
        self.cmd_send_btn = QPushButton()
        apply_button(self.cmd_send_btn, 'secondary', compact=True)
        self.cmd_send_btn.clicked.connect(self._send_cmd_bar)
        self.cmd_fill_btn = QPushButton()  # 现为「历史」
        apply_button(self.cmd_fill_btn, 'ghost', compact=True)
        self.cmd_fill_btn.clicked.connect(self._open_cmd_history_dialog)
        cmd_row.addWidget(self.cmd_input, 1)
        cmd_row.addWidget(self.cmd_fill_btn)
        cmd_row.addWidget(self.cmd_send_btn)
        right_l.addLayout(cmd_row)

        # 浅色外壳 + 深色控制台：与页面 sheet 协调，终端仍鲜明
        self.term_shell = QFrame()
        self.term_shell.setObjectName('ops-term-shell')
        term_shell_l = QVBoxLayout(self.term_shell)
        term_shell_l.setContentsMargins(8, 8, 8, 8)
        term_shell_l.setSpacing(4)
        self.term_tabs = QTabWidget()
        self.term_tabs.setObjectName('ops-term-tabs')
        self.term_tabs.setTabsClosable(True)
        self.term_tabs.setDocumentMode(True)
        self.term_tabs.tabCloseRequested.connect(self._close_term_tab)
        self.term_tabs.currentChanged.connect(self._on_term_tab_changed)
        self._term_sessions: list[dict] = []
        self.terminal = self._create_terminal_tab('会话1')
        term_shell_l.addWidget(self.term_tabs, 1)
        right_l.addWidget(self.term_shell, 3)
        self._reload_cmd_history()

        self.term_hint = QLabel()
        self.term_hint.setObjectName('field-hint')
        self.term_hint.setWordWrap(True)
        right_l.addWidget(self.term_hint)

        result_card, er_l = self._card()
        result_card.setObjectName('ops-result-card')
        self.result_panel = result_card
        rtop = QHBoxLayout()
        self.result_title = QLabel()
        self.result_title.setObjectName('section-title')
        rtop.addWidget(self.result_title)
        self.status_label = QLabel()
        self.status_label.setObjectName('small-label')
        rtop.addWidget(self.status_label, 1)
        self.open_dir_btn = QPushButton()
        apply_button(self.open_dir_btn, 'secondary', compact=True, icon='folder-open')
        self.open_dir_btn.clicked.connect(self._open_export_dir)
        rtop.addWidget(self.open_dir_btn)
        er_l.addLayout(rtop)
        self.result_table = QTableWidget(0, 5)
        apply_table(self.result_table)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setMaximumHeight(160)
        self.result_table.doubleClicked.connect(self._open_selected_result)
        er_l.addWidget(self.result_table, 1)
        right_l.addWidget(result_card, 1)

        self.console = self.terminal
        self.cmd_edit = QLineEdit()
        self.cmd_edit.hide()
        self.cmd_run_btn = QPushButton()
        self.cmd_run_btn.hide()
        self.cmd_prompt = QLabel()
        self.cmd_prompt.hide()
        # compat for old set_language tabs.* calls
        self.tabs = QTabWidget()
        self.tabs.hide()

        self.main_split.addWidget(right_host)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([420, 860])
        # 允许把左侧拖宽看全路径/表单
        try:
            self.main_split.setCollapsible(0, False)
            self.main_split.setCollapsible(1, False)
            self.main_split.handle(1).setEnabled(True)
        except Exception:
            pass

        self._work_mode = 'session'
        self._set_work_mode('session')

    def _set_work_mode(self, mode: str):
        """session | export — toggle left ops panels."""
        mode = 'export' if mode == 'export' else 'session'
        self._work_mode = mode
        is_session = mode == 'session'
        if hasattr(self, 'session_ops'):
            self.session_ops.setVisible(is_session)
        if hasattr(self, 'remote_ops'):
            self.remote_ops.setVisible(is_session)
        if hasattr(self, 'export_ops'):
            self.export_ops.setVisible(not is_session)
        if hasattr(self, 'export_rules'):
            self.export_rules.setVisible(not is_session)
        # 导出进度表只在「批量导出」模式展示，会话模式不占右侧空间
        if hasattr(self, 'result_panel'):
            self.result_panel.setVisible(not is_session)
        if hasattr(self, 'result_table'):
            self.result_table.setMaximumHeight(260 if not is_session else 120)
        if hasattr(self, 'mode_session_btn'):
            self.mode_session_btn.setChecked(is_session)
            self.mode_export_btn.setChecked(not is_session)
            apply_button(self.mode_session_btn, 'secondary' if is_session else 'ghost', compact=True)
            apply_button(self.mode_export_btn, 'secondary' if not is_session else 'ghost', compact=True)


    def _capture_settings(self) -> dict:
        """当前截取配置（优先内存控件，否则读盘）。"""
        s = load_log_settings()
        # 导出区与会话区上下文取可见者
        ctx_widgets = []
        if hasattr(self, 'export_context') and self.export_context is not None and self.export_context.isVisible():
            ctx_widgets.append(self.export_context)
        if hasattr(self, 'context_spin') and self.context_spin is not None and self.context_spin.isVisible():
            ctx_widgets.append(self.context_spin)
        if not ctx_widgets:
            if hasattr(self, 'context_spin') and self.context_spin is not None:
                ctx_widgets.append(self.context_spin)
            elif hasattr(self, 'export_context') and self.export_context is not None:
                ctx_widgets.append(self.export_context)
        if ctx_widgets:
            try:
                s['context_lines'] = int(ctx_widgets[0].value())
            except Exception:
                pass
        if hasattr(self, 'case_check') and self.case_check is not None:
            s['case_insensitive'] = bool(self.case_check.isChecked())
        if hasattr(self, 'tail_spin') and self.tail_spin is not None:
            try:
                s['tail_lines'] = int(self.tail_spin.value())
            except Exception:
                pass
        if hasattr(self, 'workers_spin') and self.workers_spin is not None:
            try:
                s['max_workers'] = int(self.workers_spin.value())
            except Exception:
                pass
        if hasattr(self, 'timeout_spin') and self.timeout_spin is not None:
            try:
                s['timeout_sec'] = int(self.timeout_spin.value())
            except Exception:
                pass
        return s

    def _open_log_settings(self):
        dialog = LogSettingsDialog(self.language, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dialog.values()
        # keep export_dir from UI
        if hasattr(self, 'export_dir_edit'):
            vals['export_dir'] = self.export_dir_edit.text().strip()
        save_log_settings(vals)
        self._load_settings_to_ui()
        if hasattr(self, 'remote_toggle_btn'):
            show = bool(vals.get('show_remote_browser'))
            self.remote_toggle_btn.setChecked(show)
            self._toggle_remote_browser(show)

    def _toggle_remote_browser(self, checked: bool):
        if hasattr(self, 'remote_body'):
            self.remote_body.setVisible(bool(checked))
        zh = self.language == 'zh'
        if hasattr(self, 'remote_toggle_btn'):
            self.remote_toggle_btn.setText(('收起' if checked else '展开') if zh else ('Hide' if checked else 'Show'))

    def _toggle_server_list(self, checked: bool = False):
        """兼容旧调用：改为打开管理弹框。"""
        self._open_server_manage_dialog()

    def _open_server_manage_dialog(self):
        dlg = ServerManageDialog(
            self.language,
            servers=self._servers,
            categories=self._categories,
            parent=self,
        )
        dlg.exec()
        servers, categories, changed = dlg.result_data()
        prefer = self.server_combo.currentData() if hasattr(self, 'server_combo') else None
        self._servers = servers
        self._categories = categories
        if changed:
            # 已在对话框内 persist；这里刷新 UI
            pass
        self._reload_servers()
        if prefer and hasattr(self, 'server_combo'):
            idx = self.server_combo.findData(prefer)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)
        self._on_server_selected()

    def _on_server_combo_changed(self, *_args):
        if not hasattr(self, 'server_combo'):
            return
        self._on_server_selected()

    def _sync_server_combo(self, prefer_id: str | None = None):
        """刷新顶部服务器下拉。"""
        if not hasattr(self, 'server_combo'):
            return
        prefer = prefer_id
        if not prefer:
            prefer = self.server_combo.currentData()
        self.server_combo.blockSignals(True)
        self.server_combo.clear()
        for server in self._servers:
            label = self._server_label(server, with_category=True)
            self.server_combo.addItem(label, server.get('id'))
        if prefer:
            idx = self.server_combo.findData(prefer)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)
        self.server_combo.blockSignals(False)

    # ── 语言 ────────────────────────────────────────────
    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.title_label.setText('日志排查' if zh else 'Log Inspect')
        self.subtitle_label.setText(
            '连接主机 → 选服务与日志 → 关键字截取；右侧终端交互；批量导出支持多机并行'
            if zh else
            'Connect host → pick service/log → keyword extract; terminal on right; multi-host export'
        )
        if hasattr(self, 'mode_session_btn'):
            self.mode_session_btn.setText('会话' if zh else 'Session')
            self.mode_export_btn.setText('批量导出' if zh else 'Batch export')
        if hasattr(self, 'settings_btn'):
            self.settings_btn.setText('设置' if zh else 'Settings')
            self.settings_btn.setToolTip(
                '上下文行数 / 并行 / 超时 / 忽略大小写等' if zh else 'Context, parallel, timeout, case…'
            )
        if hasattr(self, 'remote_toggle_btn'):
            checked = self.remote_toggle_btn.isChecked()
            self.remote_toggle_btn.setText(('收起' if checked else '展开') if zh else ('Hide' if checked else 'Show'))
        if hasattr(self, 'server_toggle_btn'):
            self.server_toggle_btn.setText('管理服务器' if zh else 'Manage')
            self.server_toggle_btn.setToolTip(
                '在弹框中新增/编辑/删除服务器' if zh else 'Manage hosts in a dialog'
            )
        if hasattr(self, 'tabs') and self.tabs.isVisible():
            try:
                self.tabs.setTabText(0, '会话浏览' if zh else 'Session')
                self.tabs.setTabText(1, '批量导出' if zh else 'Batch export')
            except Exception:
                pass
        self.server_title.setText('主机' if zh else 'Host')
        self.remote_title.setText('远端目录' if zh else 'Remote files')
        self.quick_title.setText('日志截取' if zh else 'Log extract')
        self.console_title.setText('终端' if zh else 'Terminal')
        self.export_server_title.setText('导出目标（勾选多机/服务）' if zh else 'Export targets')
        self.export_rule_title.setText('截取与导出' if zh else 'Extract & export')
        self.result_title.setText('导出进度' if zh else 'Export status')
        self.add_btn.setText('新增' if zh else 'Add')
        self.edit_btn.setText('编辑' if zh else 'Edit')
        self.del_btn.setText('删除' if zh else 'Delete')
        if hasattr(self, 'test_btn'):
            self.test_btn.setText('测试连接' if zh else 'Test SSH')
            self.test_btn.setToolTip(
                '检测选中服务器的 SSH 连通性（不打开完整会话）'
                if zh else
                'Test SSH connectivity of the selected server'
            )
        if hasattr(self, 'manage_cat_btn'):
            self.manage_cat_btn.setText('分类' if zh else 'Categories')
            self.manage_cat_btn.setToolTip('管理服务器分类（集成/模拟/自定义）' if zh else 'Manage server categories')
        self._fill_category_filter(preserve=True)
        self.path_up_btn.setText('上级' if zh else 'Up')
        self.path_up_btn.setToolTip('返回上一级目录' if zh else 'Parent directory')
        self.path_go_btn.setText('打开' if zh else 'Go')
        self.path_go_btn.setToolTip('打开上方路径中的目录' if zh else 'Open path')
        self.path_refresh_btn.setToolTip('刷新当前目录' if zh else 'Refresh')
        self.use_path_btn.setText('选用' if zh else 'Use')
        self.use_path_btn.setToolTip(
            '目录：绑定为日志目录并刷新文件列表；文件：绑定父目录并选中该日志'
            if zh else
            'Folder: bind as log dir; file: bind parent dir and select file'
        )
        self.remote_hint.setText(
            '双击目录进入；双击 .log 填入「日志文件」；点「选用」绑定目录或文件。'
            if zh else
            'Double-click folder/file; Use to bind log dir or file.'
        )
        self.preview_btn.setText('预览' if zh else 'Preview')
        self.run_grep_btn.setText('终端截取' if zh else 'Grep')
        self.tail_btn.setText('看尾部' if zh else 'Tail')
        if hasattr(self, 'session_export_btn'):
            self.session_export_btn.setText('导出日志' if zh else 'Export')
            self.session_export_btn.setToolTip(
                '按当前服务/日志文件/关键字导出到本地（关键字建文件夹，文件名为 IP-服务.log）'
                if zh else
                'Export current log by keyword into keyword folder as IP-service.log'
            )
        self.clear_console_btn.setText('清屏' if zh else 'Clear')
        self.focus_term_btn.setText('聚焦终端' if zh else 'Focus term')
        if hasattr(self, 'new_term_btn'):
            self.new_term_btn.setText('新终端' if zh else 'New term')
            self.new_term_btn.setToolTip('新增多标签终端（最多4个）' if zh else 'New terminal tab (max 4)')
        self.select_all_btn.setText('全选' if zh else 'All')
        self.select_none_btn.setText('全不选' if zh else 'None')
        self.export_btn.setText('开始导出' if zh else 'Export')
        self.open_dir_btn.setText('打开目录' if zh else 'Open folder')
        self.case_check.setText('忽略大小写' if zh else 'Ignore case')
        self.export_case.setText('忽略大小写' if zh else 'Ignore case')
        self.stream_note.setText(
            '连接后可在右侧终端里直接输入 Linux 命令。'
            if zh else
            'After connect, type Linux commands in the right terminal.'
        )
        self.remote_hint.setText(
            '连接后可浏览服务器目录；双击文件可填到「日志路径」。'
            if zh else
            'Browse remote dirs after connect; double-click fills log path.'
        )
        # 底部灰字说明（用户问「提示」指的就是这行）
        self.term_hint.setText(
            '每个终端标签是独立会话：连接/主机/服务/路径互不影响。'
            '新开标签默认未连接，不会断开其它标签。Backspace 可删；「历史」可带入命令。'
            if zh else
            'Each terminal tab is an independent session. New tab does not disconnect others.'
        )
        self.path_edit.setPlaceholderText('例如 /app/logs' if zh else '/var/log')
        self.log_path_edit.setPlaceholderText('服务器上的日志完整路径' if zh else 'Remote log path')
        self.keyword_edit.setPlaceholderText(
            '多个关键字用逗号分隔，第一个为主关键字' if zh else 'Comma-separated; first is primary'
        )
        if hasattr(self, 'extra_edit'):
            self.extra_edit.setPlaceholderText('')
        self.export_keyword.setPlaceholderText(
            '多个关键字用逗号分隔，第一个为主关键字' if zh else 'Comma-separated; first is primary'
        )
        if hasattr(self, 'export_extra'):
            self.export_extra.setPlaceholderText('')
        for key, lab in {
            'service': '服务' if zh else 'Service',
            'log_path': '日志目录' if zh else 'Log dir',
            'log_file': '日志文件' if zh else 'Log file',
            'keyword': '关键字' if zh else 'Keywords',
            'context': '上下文行' if zh else 'Context lines',
            'extra': '关键字' if zh else 'Keywords',
        }.items():
            if key in self._form_labels:
                self._form_labels[key].setText(lab)
        if hasattr(self, 'refresh_logs_btn'):
            self.refresh_logs_btn.setToolTip(
                '刷新目录下的日志文件列表（最新在前）' if zh else 'Refresh log files'
            )
        if hasattr(self, 'log_file_combo'):
            self.log_file_combo.setToolTip(
                '绑定目录后选择具体 .log（如按天生成的文件）' if zh else 'Select .log under directory'
            )
        if hasattr(self, 'log_path_edit'):
            self.log_path_edit.setPlaceholderText(
                '可填日志目录（推荐）或单个文件路径' if zh else 'Log directory or file'
            )
        for key, lab in {
            'keyword': '关键字' if zh else 'Keywords',
            'context': '上下文行' if zh else 'Context lines',
            'export_dir': '保存到' if zh else 'Save to',
            'extra': '关键字' if zh else 'Keywords',
        }.items():
            if key in self._export_labels:
                self._export_labels[key].setText(lab)
        if hasattr(self, 'export_server_list') and self.export_server_list.columnCount() >= 2:
            self.export_server_list.setHeaderLabels(
                ['服务器 / 服务', '日志路径'] if zh else ['Host / service', 'Log path']
            )
        if hasattr(self, 'session_export_btn'):
            self.session_export_btn.setToolTip(
                '弹框选择导出文件夹，再按当前服务/日志/关键字导出'
                if zh else
                'Pick folder then export current session log'
            )
        if hasattr(self, 'export_path_hint'):
            self.export_path_hint.setText(
                '勾选上方多台服务器/服务即可批量并行导出；'
                '使用各机服务配置路径（目录自动取最新 .log）。'
                if zh else
                'Check hosts/services for parallel export; paths from each service config.'
            )
        self.remote_tree.setHeaderLabels(
            ['名称', '类型', '大小', '修改时间'] if zh else ['Name', 'Type', 'Size', 'Modified']
        )
        self.path_edit.setPlaceholderText('当前目录路径，回车打开' if zh else 'Current directory path')
        self.remote_hint.setText(
            '展示方式与需求管理「文件库」一致（文件夹/文件图标）。'
            '双击文件夹进入；双击日志文件绑定路径；「选用」绑定当前选中项。'
            if zh else
            'Same icon style as Requirements file library. Double-click folder/file; Use to bind path.'
        )
        self.result_table.setHorizontalHeaderLabels(
            ['服务器', '服务', '状态', '行数', '本地文件'] if zh else ['Server', 'Service', 'Status', 'Lines', 'File']
        )
        if self.dep_note is not None:
            self.dep_note.setText('未检测到 paramiko' if zh else 'paramiko missing')
        self.status_label.setText('就绪' if zh else 'Ready')

        if hasattr(self, 'cmd_send_btn'):
            self.cmd_send_btn.setText('发送' if zh else 'Send')
            self.cmd_send_btn.setToolTip('发送到当前终端并记入本机历史' if zh else 'Send to terminal + local history')
            self.cmd_fill_btn.setText('历史' if zh else 'History')
            self.cmd_fill_btn.setToolTip('打开带日期的命令历史，可右键带入/发送' if zh else 'History with dates')
        if hasattr(self, 'cmd_input'):
            self.cmd_input.setPlaceholderText(
                '输入命令后回车或点发送…' if zh else 'Type command, Enter to send…'
            )
        self._update_connect_button_text()

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.subtitle_label, low_height)

    # ── 服务器列表 / 分类 ──────────────────────────────────────
    def _fill_category_filter(self, preserve: bool = True):
        if not hasattr(self, 'category_filter'):
            return
        zh = self.language == 'zh'
        current = self._category_filter if preserve else 'all'
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem('全部分类' if zh else 'All categories', 'all')
        for cat in sorted(self._categories, key=lambda c: (int(c.get('sort') or 0), c.get('name') or '')):
            self.category_filter.addItem(cat.get('name') or DEFAULT_CATEGORY_NAME, cat.get('id'))
        idx = self.category_filter.findData(current)
        self.category_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._category_filter = self.category_filter.currentData() or 'all'
        self.category_filter.blockSignals(False)

    def _on_category_filter_changed(self, *_args):
        if not hasattr(self, 'category_filter'):
            return
        self._category_filter = self.category_filter.currentData() or 'all'
        self._reload_servers(keep_filter=True)

    def _manage_categories(self):
        dialog = CategoryManageDialog(
            self.language, categories=self._categories, servers=self._servers, parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cats, servers = dialog.result_data()
        save_server_store(servers=servers, categories=cats)
        self._reload_servers()

    def _server_label(self, server: dict, *, with_category: bool = True) -> str:
        names = category_name_map(self._categories)
        cid = server.get('category_id') or UNCATEGORIZED_ID
        cname = names.get(cid) or server.get('group') or ''
        base = f"{server.get('name')}  ·  {server.get('host')}:{server.get('port')}"
        if with_category and cname and cid != UNCATEGORIZED_ID:
            return f'[{cname}] {base}'
        return base

    def _ordered_servers_for_list(self) -> list[tuple[dict | None, dict | None]]:
        """返回 [(header_cat|None, server|None), ...] 用于分组展示。"""
        filt = getattr(self, '_category_filter', 'all') or 'all'
        servers = list(self._servers)
        if filt != 'all':
            servers = [s for s in servers if (s.get('category_id') or UNCATEGORIZED_ID) == filt]
        cat_order = {
            c.get('id'): i
            for i, c in enumerate(
                sorted(self._categories, key=lambda c: (int(c.get('sort') or 0), c.get('name') or ''))
            )
        }
        servers.sort(
            key=lambda s: (
                cat_order.get(s.get('category_id') or UNCATEGORIZED_ID, 999),
                (s.get('name') or '').casefold(),
                s.get('host') or '',
            )
        )
        rows: list[tuple[dict | None, dict | None]] = []
        if filt != 'all':
            for s in servers:
                rows.append((None, s))
            return rows
        last_cid = object()
        names = category_name_map(self._categories)
        for s in servers:
            cid = s.get('category_id') or UNCATEGORIZED_ID
            if cid != last_cid:
                rows.append((
                    {'id': cid, 'name': names.get(cid) or DEFAULT_CATEGORY_NAME},
                    None,
                ))
                last_cid = cid
            rows.append((None, s))
        return rows

    def _reload_servers(self, keep_filter: bool = False):
        store = load_server_store()
        self._servers = list(store.get('servers') or [])
        self._categories = list(store.get('categories') or [])
        if not keep_filter:
            self._fill_category_filter(preserve=True)
        current_id = None
        if hasattr(self, 'server_combo') and self.server_combo.currentData():
            current_id = self.server_combo.currentData()
        elif self.server_list.currentItem():
            data = self.server_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(data, str) and not str(data).startswith('__cat__:'):
                current_id = data
        self.server_list.clear()
        self.export_server_list.clear()
        zh = self.language == 'zh'
        for header, server in self._ordered_servers_for_list():
            if header is not None:
                title = header.get('name') or DEFAULT_CATEGORY_NAME
                item = QListWidgetItem(f'—— {title} ——')
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setData(Qt.ItemDataRole.UserRole, f"__cat__:{header.get('id')}")
                try:
                    from ui.theme_manager import ThemeManager
                    from PyQt6.QtGui import QColor, QBrush
                    pal = ThemeManager.instance().palette()
                    item.setForeground(QBrush(QColor(pal.get('TEXT_MUTED', '#7B847E'))))
                except Exception:
                    pass
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                self.server_list.addItem(item)
                continue
            if server is None:
                continue
            label = self._server_label(server, with_category=False)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, server.get('id'))
            tip_parts = [
                f"{'分类' if zh else 'Category'}：{category_name_map(self._categories).get(server.get('category_id') or UNCATEGORIZED_ID, DEFAULT_CATEGORY_NAME)}"
            ]
            if server.get('default_log_path'):
                tip_parts.append(str(server.get('default_log_path')))
            item.setToolTip('\n'.join(tip_parts))
            self.server_list.addItem(item)

        # 导出树：服务器 → 服务路径（可多选）
        self._fill_export_tree()
        if current_id:
            for i in range(self.server_list.count()):
                if self.server_list.item(i).data(Qt.ItemDataRole.UserRole) == current_id:
                    self.server_list.setCurrentRow(i)
                    break
        self._sync_server_combo(prefer_id=current_id)
        # 列表选中变化后同步抓取区
        if self.server_list.currentItem():
            self._on_server_selected()
        elif self.server_combo.count():
            self._on_server_combo_changed()

    def _on_server_activated(self, item):
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, str) and str(data).startswith('__cat__:'):
            return
        self._connect_session()

    def _load_settings_to_ui(self):
        s = load_log_settings()
        if hasattr(self, 'context_spin'):
            self.context_spin.setValue(int(s.get('context_lines') or 20))
        if hasattr(self, 'export_context'):
            self.export_context.setValue(int(s.get('context_lines') or 20))
        if hasattr(self, 'workers_spin'):
            self.workers_spin.setValue(int(s.get('max_workers') or 4))
        if hasattr(self, 'timeout_spin'):
            self.timeout_spin.setValue(int(s.get('timeout_sec') or 30))
        if hasattr(self, 'case_check'):
            self.case_check.setChecked(bool(s.get('case_insensitive', True)))
        if hasattr(self, 'export_case'):
            self.export_case.setChecked(bool(s.get('case_insensitive', True)))
        if hasattr(self, 'tail_spin'):
            self.tail_spin.setValue(int(s.get('tail_lines') or 100))
        if hasattr(self, 'export_dir_edit'):
            self.export_dir_edit.setText(str(s.get('export_dir') or ''))
        # 会话模式默认展示远端目录；设置项仅控制「是否默认展开」
        if hasattr(self, 'remote_toggle_btn'):
            show = bool(s.get('show_remote_browser', True))
            self.remote_toggle_btn.blockSignals(True)
            self.remote_toggle_btn.setChecked(show)
            self.remote_toggle_btn.blockSignals(False)
            self._toggle_remote_browser(show)
        # 管理服务器已改为弹框，不再在此切换展开状态

    def _persist_settings_from_ui(self):
        s = self._capture_settings()
        if hasattr(self, 'export_dir_edit'):
            s['export_dir'] = self.export_dir_edit.text().strip()
        if hasattr(self, 'remote_toggle_btn'):
            s['show_remote_browser'] = bool(self.remote_toggle_btn.isChecked())
        save_log_settings(s)

    def _current_server(self) -> dict | None:
        sid = None
        item = self.server_list.currentItem() if hasattr(self, 'server_list') else None
        if item:
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, str) and not str(data).startswith('__cat__:'):
                sid = data
        if not sid and hasattr(self, 'server_combo'):
            sid = self.server_combo.currentData()
        if not sid:
            return None
        for s in self._servers:
            if s.get('id') == sid:
                return s
        return None

    def _on_server_selected(self):
        if self._restoring_session:
            return
        server = self._current_server()
        self._refresh_service_combo(server)
        if server and not self.log_path_edit.text().strip():
            path = primary_log_path(server)
            if path:
                self.log_path_edit.setText(path)
        sess = self._current_session()
        if sess is not None and server:
            sess['server_id'] = server.get('id')
        self._refresh_output_context()

    def _refresh_output_context(self):
        if not hasattr(self, 'output_context_label'):
            return
        server = self._current_server()
        if not server:
            self.output_context_label.setText('')
            return
        name = str(server.get('name') or server.get('host') or '')
        svc = self.service_combo.currentText().strip() if hasattr(self, 'service_combo') else ''
        path = self._effective_log_path() if hasattr(self, '_effective_log_path') else (
            self.log_path_edit.text().strip() if hasattr(self, 'log_path_edit') else ''
        )
        parts = [name]
        if svc:
            parts.append(svc)
        # 右侧状态只展示主机+服务名，路径太长难看，放到 tooltip
        self.output_context_label.setText(' · '.join(parts))
        self.output_context_label.setToolTip(path or '')

    @staticmethod
    def _server_for_store(data: dict) -> dict:
        """落盘前处理：未勾选保存密码则不写 password_token。"""
        item = dict(data or {})
        skip = bool(item.pop('_skip_persist_password', False))
        if skip:
            item.pop('password', None)
            item['password_token'] = ''
        return item

    def _add_server(self):
        dialog = ServerEditorDialog(self.language, categories=self._categories, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._categories = dialog.categories()
        self._servers.append(self._server_for_store(dialog.server_data()))
        save_server_store(servers=self._servers, categories=self._categories)
        self._reload_servers()
        zh = self.language == 'zh'
        # 轻提示：是否已保存密码
        last = self._servers[-1] if self._servers else {}
        if last.get('password_token') or last.get('password'):
            show_success(self, 'PengTools', '服务器已添加，密码已加密保存' if zh else 'Server added; password saved encrypted')
        else:
            show_success(self, 'PengTools', '服务器已添加' if zh else 'Server added')

    def _edit_server(self):
        server = self._current_server()
        if not server:
            show_warning(self, 'PengTools', '请先选择服务器' if self.language == 'zh' else 'Select a server')
            return
        dialog = ServerEditorDialog(
            self.language, server=server, categories=self._categories, parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._categories = dialog.categories()
        updated = self._server_for_store(dialog.server_data())
        self._servers = [updated if s.get('id') == server.get('id') else s for s in self._servers]
        save_server_store(servers=self._servers, categories=self._categories)
        self._reload_servers()

    def _delete_server(self):
        server = self._current_server()
        if not server:
            return
        zh = self.language == 'zh'
        if not confirm_action(
            self,
            '删除服务器' if zh else 'Delete server',
            f'确定删除服务器「{server.get("name")}」吗？' if zh else f'Delete {server.get("name")}?',
            confirm_text='删除' if zh else 'Delete',
            danger=True,
        ):
            return
        sid = server.get('id')
        # 所有使用该服务器的会话标签都断开（不影响其它主机会话）
        for sess in list(getattr(self, '_term_sessions', []) or []):
            if sess.get('server_id') == sid:
                try:
                    self._close_session_resources(sess)
                    sess['server_id'] = None
                except Exception:
                    pass
        self._servers = [s for s in self._servers if s.get('id') != sid]
        save_server_store(servers=self._servers, categories=self._categories)
        self._reload_servers()
        if hasattr(self, 'term_tabs'):
            self._restore_session_at(self.term_tabs.currentIndex())

    def _show_server_menu(self, point):
        item = self.server_list.itemAt(point)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, str) and str(data).startswith('__cat__:'):
            return
        self.server_list.setCurrentItem(item)
        zh = self.language == 'zh'
        menu = QMenu(self)
        menu.addAction('测试连接' if zh else 'Test SSH', self._test_selected_server)
        menu.addAction('连接会话' if zh else 'Connect session', self._connect_session)
        menu.addSeparator()
        menu.addAction('编辑' if zh else 'Edit', self._edit_server)
        menu.addAction('删除' if zh else 'Delete', self._delete_server)
        menu.exec(self.server_list.viewport().mapToGlobal(point))

    def _test_selected_server(self):
        """列表上直接测 SSH，不打开编辑框、不进入交互终端。"""
        zh = self.language == 'zh'
        server = self._current_server()
        if not server:
            show_warning(self, 'PengTools', '请先选择一台服务器' if zh else 'Select a server first')
            return
        if not paramiko_available():
            show_warning(self, 'PengTools', '未安装 paramiko，无法测试 SSH' if zh else 'paramiko missing')
            return
        if not decrypt_secret(server.get('password_token') or ''):
            show_warning(
                self, 'PengTools',
                '该服务器未保存密码，请先「编辑」并保存密码后再测'
                if zh else
                'No saved password; edit the server first',
            )
            return
        if getattr(self, '_ssh_test_worker', None) is not None and self._ssh_test_worker.isRunning():
            show_warning(self, 'PengTools', '已有测试在进行中' if zh else 'A test is already running')
            return
        name = server.get('name') or server.get('host')
        self.session_status.setText(f'正在测试 SSH：{name} …' if zh else f'Testing SSH: {name} …')
        if hasattr(self, 'test_btn'):
            self.test_btn.setEnabled(False)
            self.test_btn.setText('测试中…' if zh else 'Testing…')
        timeout = 15
        if hasattr(self, 'timeout_spin'):
            try:
                timeout = max(5, min(60, int(self.timeout_spin.value())))
            except Exception:
                timeout = 15
        worker = _SshTestWorker(server, password_override=None, timeout_sec=timeout, parent=self)
        worker.finished_ok.connect(self._on_list_ssh_test_ok)
        worker.finished_err.connect(self._on_list_ssh_test_err)
        worker.finished.connect(self._on_list_ssh_test_done)
        self._ssh_test_worker = worker
        worker.start()

    def _on_list_ssh_test_done(self):
        if hasattr(self, 'test_btn'):
            self.test_btn.setEnabled(True)
            self.test_btn.setText('测试连接' if self.language == 'zh' else 'Test SSH')

    def _on_list_ssh_test_ok(self, result: dict):
        text = format_connection_ok(result, self.language)
        self.session_status.setText(text.replace('\n', '  ·  '))
        show_success(self, 'PengTools · SSH', text)

    def _on_list_ssh_test_err(self, message: str):
        zh = self.language == 'zh'
        msg = message or ('连接失败' if zh else 'Connection failed')
        self.session_status.setText(('测试失败：' if zh else 'Failed: ') + msg)
        show_error(self, 'PengTools · SSH', msg)

    # ── 控制台 / 终端 ──────────────────────────────────
    def _console_append(self, text: str, *, prefix: str = ''):
        msg = (prefix + text) if prefix else text
        if not msg.endswith('\n'):
            msg += '\n'
        term = self._current_terminal() if hasattr(self, '_current_terminal') else getattr(self, 'terminal', None)
        if term is not None and hasattr(term, 'append_system'):
            term.append_system(msg)
        elif hasattr(self, 'console') and hasattr(self.console, 'appendPlainText'):
            self.console.appendPlainText(msg.rstrip('\n'))

    def _console_banner(self, title: str):
        line = '─' * 48
        self._console_append(f'\n{line}\n{title}\n{line}')

    # ── 会话连接（仅当前标签） ──────────────────────────
    def _set_session_connected(self, connected: bool):
        widgets = [
            self.path_up_btn, self.path_edit, self.path_go_btn, self.path_refresh_btn,
            self.use_path_btn, self.remote_tree, self.focus_term_btn,
            self.run_grep_btn, self.tail_btn, self.preview_btn,
        ]
        if hasattr(self, 'session_export_btn'):
            widgets.append(self.session_export_btn)
        term = self._current_terminal()
        if term is not None:
            widgets.append(term)
        for w in widgets:
            if w is not None:
                try:
                    w.setEnabled(bool(connected))
                except Exception:
                    pass
        self._update_connect_button_text()

    def _update_connect_button_text(self):
        zh = self.language == 'zh'
        if self._session_client() is not None:
            self.connect_btn.setText('断开本会话' if zh else 'Disconnect tab')
        else:
            self.connect_btn.setText('连接本会话' if zh else 'Connect tab')

    def _toggle_connect(self):
        if self._session_client() is not None:
            self._disconnect_session()
        else:
            self._connect_session()

    def _disconnect_session(self):
        """只断开当前标签，不影响其它会话标签。"""
        zh = self.language == 'zh'
        sess = self._current_session()
        if sess is None:
            return
        self._close_session_resources(sess)
        sess['remote_cwd'] = '/'
        sess['remote_entries'] = []
        if hasattr(self, 'remote_tree'):
            self.remote_tree.clear()
        if hasattr(self, 'path_edit'):
            self.path_edit.clear()
        self._set_session_connected(False)
        self._update_session_status_label(sess)
        idx = self.term_tabs.currentIndex()
        if idx >= 0:
            base = sess.get('title') or (f'会话{idx + 1}' if zh else f'Session {idx + 1}')
            if sess.get('server_id'):
                s = next((x for x in self._servers if x.get('id') == sess.get('server_id')), None)
                if s:
                    base = str(s.get('name') or s.get('host') or base)
            self.term_tabs.setTabText(idx, f'{base} · 未连接' if zh else f'{base} · off')
        self._console_append('[本会话已断开，其它标签不受影响]' if zh else '[this tab disconnected only]')

    def _connect_session(self):
        zh = self.language == 'zh'
        if not paramiko_available():
            show_error(self, 'PengTools', '未安装 paramiko' if zh else 'paramiko missing')
            return
        server = self._current_server()
        if not server:
            show_warning(self, 'PengTools', '请先选择服务器' if zh else 'Select a server')
            return
        if not decrypt_secret(server.get('password_token') or ''):
            show_warning(self, 'PengTools', '密码无效，请编辑服务器重新输入' if zh else 'Password missing')
            return
        if self._worker and self._worker.isRunning():
            show_warning(self, 'PengTools', '请等待当前操作完成' if zh else 'Busy')
            return
        if self._session_client() is not None:
            self._disconnect_session()

        connect_tab_index = self.term_tabs.currentIndex()
        self.session_status.setText('正在连接本会话…' if zh else 'Connecting this tab…')
        self.connect_btn.setEnabled(False)
        self._console_banner(
            f"会话{connect_tab_index + 1} 连接 {server.get('name')} ({server.get('host')}:{server.get('port')})"
        )

        timeout = 30
        if hasattr(self, 'timeout_spin'):
            try:
                timeout = int(self.timeout_spin.value())
            except Exception:
                timeout = 30

        def job():
            client = open_ssh_client(server, timeout_sec=timeout)
            home = remote_home_dir(client)
            start = str(server.get('default_log_path') or '').strip()
            if start:
                if not start.endswith('/'):
                    parent = parent_remote_path(start)
                    start_dir = parent if parent else home
                else:
                    start_dir = start.rstrip('/') or '/'
            else:
                start_dir = home
            try:
                entries = list_remote_dir(client, start_dir)
                cwd = start_dir
            except OpsSshError:
                entries = list_remote_dir(client, home)
                cwd = home
            return {
                'client': client, 'server': server, 'cwd': cwd,
                'entries': entries, 'home': home, 'tab_index': connect_tab_index,
            }

        worker = _Worker(job, self)
        self._worker = worker

        def ok(payload):
            self.connect_btn.setEnabled(True)
            tab_index = int(payload.get('tab_index', connect_tab_index))
            if tab_index < 0 or tab_index >= len(self._term_sessions):
                try:
                    close_ssh_client(payload.get('client'))
                except Exception:
                    pass
                show_warning(self, 'PengTools', '会话标签已关闭，连接已取消' if zh else 'Tab closed')
                return
            sess = self._term_sessions[tab_index]
            if sess.get('client') is not None and sess.get('client') is not payload['client']:
                try:
                    close_ssh_client(sess.get('client'))
                except Exception:
                    pass
            sess['client'] = payload['client']
            sess['server_id'] = payload['server'].get('id')
            sess['remote_cwd'] = payload['cwd']
            sess['remote_entries'] = payload.get('entries') or []
            sess['connected'] = True
            title = str(payload['server'].get('name') or payload['server'].get('host') or f'会话{tab_index + 1}')
            sess['title'] = title
            self.term_tabs.setTabText(tab_index, title)

            if self.term_tabs.currentIndex() == tab_index:
                self.path_edit.setText(sess['remote_cwd'])
                self._fill_remote_tree(sess['remote_entries'])
                try:
                    term = sess.get('widget') or self._current_terminal()
                    if term is not None:
                        term.attach_client(sess['client'])
                    self._refresh_service_combo(payload['server'])
                except Exception as exc:
                    self._console_append(f'[终端启动失败] {exc}')
                if payload['server'].get('default_log_path') and not self.log_path_edit.text().strip():
                    self.log_path_edit.setText(str(payload['server'].get('default_log_path')))
                    sess['log_path'] = str(payload['server'].get('default_log_path'))
                self._set_session_connected(True)
                self._update_session_status_label(sess)
                self._set_work_mode('session')
                self._refresh_log_file_combo()
                self._refresh_output_context()
                term = self._current_terminal()
                if term is not None:
                    term.setFocus()
            else:
                try:
                    term = sess.get('widget')
                    if term is not None:
                        term.attach_client(sess['client'])
                        view = getattr(term, 'view', None)
                        if view is not None and hasattr(view, 'set_ui_active'):
                            view.set_ui_active(False)
                except Exception as exc:
                    self._console_append(f'[终端启动失败·后台标签] {exc}')
            self._console_append(
                f"[OK] 会话{tab_index + 1} 已连接 {payload['server'].get('name')}，cwd {payload['cwd']}"
            )

        def fail(msg):
            self.connect_btn.setEnabled(True)
            self.session_status.setText('连接失败' if zh else 'Failed')
            self._console_append(f'[ERROR] {msg}')
            show_error(self, 'PengTools', msg)
            self._update_connect_button_text()

        worker.ok.connect(ok)
        worker.fail.connect(fail)
        worker.finished.connect(lambda: setattr(self, '_worker', None))
        worker.start()

    # ── 远端目录 ────────────────────────────────────────
    def _remote_entry_icon(self, entry: dict) -> QIcon:
        """与需求管理文件库相同：系统文件夹/按扩展名文件图标。"""
        name = str(entry.get('name') or '')
        if entry.get('is_dir'):
            return _REMOTE_FILE_ICON_PROVIDER.icon(QFileIconProvider.IconType.Folder)
        # 用文件名扩展名匹配系统图标（路径无需真实存在于本机）
        return _REMOTE_FILE_ICON_PROVIDER.icon(QFileInfo(name or 'file'))

    def _remote_entry_type_label(self, entry: dict) -> str:
        zh = self.language == 'zh'
        if entry.get('is_dir'):
            return '文件夹' if zh else 'Folder'
        name = str(entry.get('name') or '')
        ext = ''
        if '.' in name and not name.startswith('.'):
            ext = name.rsplit('.', 1)[-1].lower()
        if not ext:
            return '文件' if zh else 'File'
        # 常见日志/压缩类型友好名
        mapping = {
            'log': '日志' if zh else 'Log',
            'gz': '压缩包' if zh else 'Archive',
            'zip': '压缩包' if zh else 'Archive',
            'tar': '压缩包' if zh else 'Archive',
            'tgz': '压缩包' if zh else 'Archive',
            'txt': '文本' if zh else 'Text',
            'out': '输出' if zh else 'Output',
            'err': '错误日志' if zh else 'Error log',
            'json': 'JSON',
            'xml': 'XML',
            'conf': '配置' if zh else 'Config',
            'cfg': '配置' if zh else 'Config',
            'properties': '配置' if zh else 'Config',
            'yml': '配置' if zh else 'Config',
            'yaml': '配置' if zh else 'Config',
            'sh': '脚本' if zh else 'Script',
            'py': '脚本' if zh else 'Script',
            'jar': 'JAR',
            'war': 'WAR',
        }
        return mapping.get(ext, f'.{ext}')

    def _fill_remote_tree(self, entries: list[dict]):
        """填充远端目录：图标 + 名称/类型/大小/时间（对齐文件库观感）。"""
        self.remote_tree.clear()
        zh = self.language == 'zh'
        # 文件夹在前，再按名称排序
        ordered = sorted(
            list(entries or []),
            key=lambda e: (
                0 if e.get('is_dir') else 1,
                str(e.get('name') or '').casefold(),
            ),
        )
        blank = QIcon()
        for entry in ordered:
            name = str(entry.get('name') or '')
            is_dir = bool(entry.get('is_dir'))
            ftype = self._remote_entry_type_label(entry)
            size_text = '' if is_dir else str(entry.get('size_text') or '--')
            mtime_text = str(entry.get('mtime_text') or '')
            item = QTreeWidgetItem([name, ftype, size_text, mtime_text])
            item.setIcon(0, self._remote_entry_icon(entry))
            # 仅名称列带图标，其它列清空装饰（与文件库一致）
            for col in range(1, 4):
                item.setIcon(col, blank)
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            for col in range(4):
                item.setTextAlignment(
                    col, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                )
            path = str(entry.get('path') or name)
            tip = (
                f"{'文件夹' if is_dir else '文件'}：{name}\n"
                f"类型：{ftype}\n"
                f"路径：{path}"
            )
            if size_text:
                tip += f'\n大小：{size_text}'
            if mtime_text:
                tip += f'\n修改：{mtime_text}'
            for col in range(4):
                item.setToolTip(col, tip)
            # 文件夹加粗，更易扫读
            if is_dir:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            item.setSizeHint(0, QSize(0, 28))
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator,
            )
            self.remote_tree.addTopLevelItem(item)
        # 不强制 ResizeToContents，保留用户拖拽的列宽；仅保证最小可读宽度
        try:
            if self.remote_tree.columnWidth(0) < 80:
                self.remote_tree.setColumnWidth(0, 140)
        except Exception:
            pass

    def _run_bg(self, fn, on_ok, busy_text: str = '', on_fail=None):
        if self._worker and self._worker.isRunning():
            # 刷新日志列表失败时不弹窗打扰
            if on_fail is None:
                show_warning(self, 'PengTools', '请等待当前操作完成' if self.language == 'zh' else 'Busy')
            return
        if busy_text:
            self.session_status.setText(busy_text)
        worker = _Worker(fn, self)
        self._worker = worker
        worker.ok.connect(on_ok)

        def _default_fail(msg):
            self._console_append(f'[ERROR] {msg}')
            show_warning(self, 'PengTools', msg)

        worker.fail.connect(on_fail if on_fail is not None else _default_fail)
        worker.finished.connect(lambda: setattr(self, '_worker', None))
        worker.start()

    def _refresh_remote_dir(self):
        if not self._client:
            return
        client = self._client
        cwd = self.path_edit.text().strip() or self._remote_cwd

        def job():
            return {'cwd': cwd, 'entries': list_remote_dir(client, cwd)}

        def ok(payload):
            self._remote_cwd = payload['cwd']
            self.path_edit.setText(self._remote_cwd)
            self._fill_remote_tree(payload['entries'])
            sess = self._current_session()
            if sess is not None:
                sess['remote_cwd'] = payload['cwd']
                sess['remote_entries'] = payload.get('entries') or []
            self._console_append(f'[ls] {self._remote_cwd}  ({len(payload["entries"])} 项)')
            self._update_session_status_label(sess)

        self._run_bg(job, ok, '正在列目录…' if self.language == 'zh' else 'Listing…')

    def _remote_goto_path(self):
        self._refresh_remote_dir()

    def _remote_go_up(self):
        self.path_edit.setText(parent_remote_path(self.path_edit.text().strip() or self._remote_cwd))
        self._refresh_remote_dir()

    def _remote_item_clicked(self, item, _col=0):
        """单击文件时预填路径到状态提示（不打断浏览）。"""
        entry = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(entry, dict):
            return
        path = str(entry.get('path') or '')
        if path and hasattr(self, 'remote_hint'):
            zh = self.language == 'zh'
            kind = '目录' if entry.get('is_dir') else '文件'
            self.remote_hint.setText(
                f'已选中{kind}：{path}' if zh else f'Selected: {path}'
            )

    def _remote_item_activated(self, item, _col=0):
        entry = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(entry, dict):
            return
        if entry.get('is_dir'):
            self.path_edit.setText(entry.get('path') or '')
            self._refresh_remote_dir()
        else:
            path = entry.get('path') or ''
            # 文件：目录填父路径，下拉选中该文件
            parent = path.rsplit('/', 1)[0] if '/' in path else path
            name = path.rsplit('/', 1)[-1]
            self.log_path_edit.setText(parent or path)
            self.export_log_path.setText(path)
            self._console_append(f'[选中文件] {path}')
            if hasattr(self, 'remote_hint'):
                self.remote_hint.setText(
                    f'已选中日志文件：{path}' if self.language == 'zh' else f'Log file: {path}'
                )
            # 先选中该文件，再后台刷新完整列表（保持 prefer）
            self._set_log_file_combo_items([{
                'name': name, 'path': path,
                'mtime_text': str(entry.get('mtime_text') or ''),
                'size_text': str(entry.get('size_text') or ''),
            }], prefer_path=path)
            self._refresh_log_file_combo()

    def _use_selected_as_log_path(self):
        item = self.remote_tree.currentItem()
        entry = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        prefer_file = ''
        if isinstance(entry, dict):
            path = entry.get('path') or ''
            if entry.get('is_dir'):
                # 绑定目录
                prefer_file = ''
            else:
                # 绑定到父目录，文件进下拉
                prefer_file = path
                path = path.rsplit('/', 1)[0] if '/' in path else path
            if entry.get('is_dir') and not path:
                path = self.path_edit.text().strip()
        else:
            path = self.path_edit.text().strip()
        if not path:
            show_warning(self, 'PengTools', '请先选择文件或目录' if self.language == 'zh' else 'Select a path')
            return
        self.log_path_edit.setText(path)
        self.export_log_path.setText(prefer_file or path)
        self._console_append(
            f'[日志目录] {path}' if not prefer_file else f'[日志文件] {prefer_file}'
        )
        if hasattr(self, 'remote_hint'):
            self.remote_hint.setText(
                (f'已绑定目录：{path}，请在「日志文件」下拉选择' if not prefer_file else f'已选用：{prefer_file}')
                if self.language == 'zh' else
                f'Bound: {prefer_file or path}'
            )
        self._refresh_log_file_combo()
        if prefer_file and hasattr(self, 'log_file_combo'):
            idx = self.log_file_combo.findData(prefer_file)
            if idx >= 0:
                self.log_file_combo.setCurrentIndex(idx)
            else:
                name = prefer_file.rsplit('/', 1)[-1]
                self.log_file_combo.insertItem(0, name, prefer_file)
                self.log_file_combo.setCurrentIndex(0)

    # ── 会话命令 / 截取（发到交互终端，实时刷） ─────────
    def _run_console_command(self):
        """兼容：把快捷命令行发到 PTY。"""
        if not self.terminal.shell_alive:
            show_warning(self, 'PengTools', '请先连接服务器' if self.language == 'zh' else 'Connect first')
            return
        cmd = self.cmd_edit.text().strip()
        if not cmd:
            return
        self.cmd_edit.clear()
        self.terminal.setFocus()
        self.terminal.send_command_line(cmd)

    def _session_keywords(self) -> tuple[str, list[str]]:
        raw = self.keyword_edit.text().strip()
        primary, extras = parse_keywords(raw)
        # 兼容旧「也包含」隐藏字段
        if hasattr(self, 'extra_edit') and self.extra_edit.isVisible():
            extras = list(dict.fromkeys([*extras, *split_extra_keywords(self.extra_edit.toPlainText())]))
        return primary, extras

    def _preview_on_console(self):
        try:
            cap = self._capture_settings()
            primary, extras = self._session_keywords()
            cmd = build_remote_grep_command(
                self._effective_log_path(),
                primary,
                extras,
                context_lines=int(cap.get('context_lines') or 20),
                case_insensitive=bool(cap.get('case_insensitive', True)),
            )
            self._console_append(f'[预览] {cmd}')
            term = self._current_terminal()
            if term is not None:
                term.setFocus()
        except OpsSshError as exc:
            show_warning(self, 'PengTools', str(exc))

    def _run_grep_on_session(self):
        term = self._current_terminal()
        if term is None or not getattr(term, 'shell_alive', False):
            show_warning(self, 'PengTools', '请先连接服务器' if self.language == 'zh' else 'Connect first')
            return
        try:
            cap = self._capture_settings()
            primary, extras = self._session_keywords()
            if hasattr(self, 'context_spin'):
                try:
                    cap['context_lines'] = int(self.context_spin.value())
                except Exception:
                    pass
            cmd = build_remote_grep_command(
                self._effective_log_path(),
                primary,
                extras,
                context_lines=int(cap.get('context_lines') or 20),
                case_insensitive=bool(cap.get('case_insensitive', True)),
            )
        except OpsSshError as exc:
            show_warning(self, 'PengTools', str(exc))
            return
        self.terminal.setFocus()
        # 直接打进 shell，输出实时回来
        self.terminal.send_command_line(cmd)

    def _run_tail_on_session(self):
        term = self._current_terminal()
        if term is None or not getattr(term, 'shell_alive', False):
            show_warning(self, 'PengTools', '请先连接服务器' if self.language == 'zh' else 'Connect first')
            return
        path = self._effective_log_path()
        if not path:
            show_warning(
                self, 'PengTools',
                '请填写日志目录并在「日志文件」中选择具体文件' if self.language == 'zh' else 'Select a log file',
            )
            return
        n = int(self._capture_settings().get('tail_lines') or 100)
        import shlex
        # 默认看尾部；若需要实时跟踪可在终端手输 tail -f
        cmd = f'tail -n {int(n)} -- {shlex.quote(path)}'
        term.setFocus()
        term.send_command_line(cmd)

    # ── 批量导出 ────────────────────────────────────────
    def _fill_export_tree(self):
        tree = self.export_server_list
        tree.blockSignals(True)
        tree.clear()
        names = category_name_map(self._categories)
        export_servers = sorted(
            self._servers,
            key=lambda s: (
                names.get(s.get('category_id') or UNCATEGORIZED_ID, ''),
                (s.get('name') or '').casefold(),
            ),
        )
        last_cid = object()
        cat_item = None
        for server in export_servers:
            cid = server.get('category_id') or UNCATEGORIZED_ID
            if cid != last_cid:
                cat_item = QTreeWidgetItem([f"—— {names.get(cid, DEFAULT_CATEGORY_NAME)} ——"])
                cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                cat_item.setData(0, Qt.ItemDataRole.UserRole, f'__cat__:{cid}')
                font = cat_item.font(0)
                font.setBold(True)
                cat_item.setFont(0, font)
                tree.addTopLevelItem(cat_item)
                last_cid = cid
            parent = cat_item if cat_item is not None else tree.invisibleRootItem()
            host = QTreeWidgetItem([
                self._server_label(server, with_category=False),
                str(server.get('host') or ''),
            ])
            host.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            host.setCheckState(0, Qt.CheckState.Checked if server.get('enabled', True) else Qt.CheckState.Unchecked)
            host.setData(0, Qt.ItemDataRole.UserRole, {'type': 'server', 'server_id': server.get('id')})
            host.setToolTip(0, self._server_label(server, with_category=True))
            host.setToolTip(1, str(server.get('host') or ''))
            if cat_item is not None:
                cat_item.addChild(host)
            else:
                tree.addTopLevelItem(host)
            services = server_services(server, only_enabled=False)
            if not services:
                p = primary_log_path(server)
                if p:
                    services = [{'id': 'default', 'name': '默认', 'log_path': p, 'enabled': True}]
            for svc in services:
                path = str(svc.get('log_path') or '').strip()
                svc_name = str(svc.get('name') or '服务')
                child = QTreeWidgetItem([svc_name, path or '（未配置路径）'])
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                on = bool(svc.get('enabled', True)) and bool(path)
                child.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                child.setData(0, Qt.ItemDataRole.UserRole, {
                    'type': 'service',
                    'server_id': server.get('id'),
                    'service_id': svc.get('id'),
                    'job_key': make_job_key(str(server.get('id') or ''), str(svc.get('id') or '')),
                    'log_path': path,
                })
                tip = f'{svc_name}\n{path}' if path else svc_name
                child.setToolTip(0, tip)
                child.setToolTip(1, path or '')
                if not path:
                    child.setDisabled(True)
                host.addChild(child)
            host.setExpanded(True)
            if cat_item is not None:
                cat_item.setExpanded(True)
        tree.blockSignals(False)
        try:
            # 给路径列足够宽度，过长时靠横向滚动查看
            if tree.columnWidth(0) < 140:
                tree.setColumnWidth(0, 160)
            if tree.columnWidth(1) < 160:
                tree.setColumnWidth(1, 240)
        except Exception:
            pass

    def _on_export_tree_item_changed(self, item, column):
        if column != 0 or item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get('type') != 'server':
            return
        # 服务器勾选 → 同步子服务
        state = item.checkState(0)
        tree = self.export_server_list
        tree.blockSignals(True)
        for i in range(item.childCount()):
            ch = item.child(i)
            if ch is None or not (ch.flags() & Qt.ItemFlag.ItemIsEnabled):
                continue
            if ch.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                ch.setCheckState(0, state if state != Qt.CheckState.PartiallyChecked else Qt.CheckState.Checked)
        tree.blockSignals(False)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        tree = self.export_server_list
        tree.blockSignals(True)

        def walk(node):
            for i in range(node.childCount()):
                ch = node.child(i)
                if ch.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    ch.setCheckState(0, state)
                walk(ch)

        walk(tree.invisibleRootItem())
        tree.blockSignals(False)

    def _selected_export_keys(self) -> set[str]:
        keys: set[str] = set()
        tree = self.export_server_list

        def walk(node):
            for i in range(node.childCount()):
                ch = node.child(i)
                data = ch.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get('type') == 'service':
                    if ch.checkState(0) == Qt.CheckState.Checked and data.get('job_key'):
                        keys.add(str(data['job_key']))
                walk(ch)

        walk(tree.invisibleRootItem())
        return keys

    def _selected_export_servers(self) -> list[dict]:
        keys = self._selected_export_keys()
        sids = {k.split('::', 1)[0] for k in keys if '::' in k}
        return [s for s in self._servers if s.get('id') in sids]

    def _browse_export_dir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            '选择导出目录' if self.language == 'zh' else 'Export folder',
            self.export_dir_edit.text().strip() or os.path.expanduser('~'),
        )
        if path:
            self.export_dir_edit.setText(path)

    def _open_export_dir(self):
        path = self.export_dir_edit.text().strip()
        # 优先打开最近一次成功导出所在的关键字子目录
        if hasattr(self, 'result_table'):
            for row in range(self.result_table.rowCount() - 1, -1, -1):
                item = self.result_table.item(row, 4) or self.result_table.item(row, 3)
                if not item:
                    continue
                lp = item.data(Qt.ItemDataRole.UserRole) or item.text()
                if lp and os.path.isfile(str(lp)):
                    path = os.path.dirname(str(lp))
                    break
                if lp and os.path.isdir(str(lp)):
                    path = str(lp)
                    break
        if not path or not os.path.isdir(path):
            show_warning(self, 'PengTools', '导出目录无效' if self.language == 'zh' else 'Invalid folder')
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(['explorer', path], shell=False)


    def _export_current_session(self):
        """会话模式：弹框选导出文件夹，再导出当前主机 + 日志文件 + 关键字。"""
        zh = self.language == 'zh'
        if self._running:
            return
        if not paramiko_available():
            show_error(self, 'PengTools', '未安装 paramiko' if zh else 'paramiko missing')
            return
        server = self._current_server()
        if not server:
            show_warning(self, 'PengTools', '请先选择服务器' if zh else 'Select a server')
            return
        if not decrypt_secret(server.get('password_token') or ''):
            show_warning(self, 'PengTools', '请先编辑服务器并保存密码' if zh else 'Save password first')
            return
        path = self._effective_log_path()
        if not path:
            show_warning(
                self, 'PengTools',
                '请填写日志目录并在「日志文件」中选择具体 .log' if zh else 'Select a log file',
            )
            return
        raw_kw = self.keyword_edit.text().strip()
        keyword, extras = parse_keywords(raw_kw)
        if not keyword:
            show_warning(self, 'PengTools', '请填写关键字（多个用逗号分隔）' if zh else 'Keyword required')
            return
        # 每次会话导出都弹框选目录（默认上次目录）
        start_dir = ''
        if hasattr(self, 'export_dir_edit'):
            start_dir = self.export_dir_edit.text().strip()
        if not start_dir:
            start_dir = str(load_log_settings().get('export_dir') or '').strip()
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = os.path.expanduser('~')
        path_pick = QFileDialog.getExistingDirectory(
            self,
            '选择导出文件夹' if zh else 'Choose export folder',
            start_dir,
        )
        if not path_pick:
            return
        export_dir = path_pick
        if hasattr(self, 'export_dir_edit'):
            self.export_dir_edit.setText(export_dir)
        svc_name = ''
        if hasattr(self, 'service_combo') and self.service_combo.currentText():
            svc_name = self.service_combo.currentText().strip()
        cap = self._capture_settings()
        if hasattr(self, 'context_spin'):
            try:
                cap['context_lines'] = int(self.context_spin.value())
            except Exception:
                pass
        context = int(cap.get('context_lines') or 20)
        timeout = int(cap.get('timeout_sec') or 30)
        case_i = bool(cap.get('case_insensitive', True))
        self._persist_settings_from_ui()
        self._running = True
        if hasattr(self, 'session_export_btn'):
            self.session_export_btn.setEnabled(False)
        if hasattr(self, 'export_btn'):
            self.export_btn.setEnabled(False)
        if hasattr(self, 'result_panel'):
            self.result_panel.show()
        if hasattr(self, 'result_table'):
            self.result_table.setRowCount(0)
        self.status_label.setText('正在导出当前日志…' if zh else 'Exporting current log…')
        bridge = self._bridge
        job = {
            'server': server,
            'server_id': server.get('id'),
            'service_id': 'session',
            'service_name': svc_name or 'session',
            'log_path': path,
            'job_key': make_job_key(str(server.get('id') or ''), 'session'),
        }

        def job_run():
            try:
                results = extract_logs_parallel(
                    jobs=[job],
                    keyword=keyword,
                    extra_keywords=extras,
                    context_lines=context,
                    case_insensitive=case_i,
                    export_dir=export_dir,
                    timeout_sec=timeout,
                    max_workers=1,
                    on_result=lambda r: bridge.result_ready.emit(r),
                )
                bridge.finished.emit(results)
            except Exception as exc:
                bridge.failed.emit(str(exc) or exc.__class__.__name__)

        self._executor.submit(job_run)

    def _start_export(self):
        zh = self.language == 'zh'
        if self._running:
            return
        if not paramiko_available():
            show_error(self, 'PengTools', '未安装 paramiko' if zh else 'paramiko missing')
            return
        selected_keys = self._selected_export_keys()
        servers = list(self._servers)
        if not selected_keys:
            show_warning(self, 'PengTools', '请至少勾选一个服务日志路径' if zh else 'Select service log paths')
            return
        jobs = build_export_jobs(
            servers,
            selected_keys=selected_keys if selected_keys else None,
            override_path='',
        )
        if not jobs:
            show_warning(self, 'PengTools', '没有可导出的路径（请检查服务配置）' if zh else 'No export jobs')
            return
        raw_kw = self.export_keyword.text().strip() or self.keyword_edit.text().strip()
        keyword, extras = parse_keywords(raw_kw)
        if not keyword:
            show_warning(self, 'PengTools', '请填写关键字（多个用逗号分隔）' if zh else 'Keyword required')
            return
        export_dir = self.export_dir_edit.text().strip()
        if not export_dir:
            show_warning(self, 'PengTools', '请选择导出目录' if zh else 'Export folder required')
            return
        for job in jobs:
            s = job.get('server') or {}
            if not decrypt_secret(s.get('password_token') or ''):
                show_warning(self, 'PengTools', f'「{s.get("name")}」密码无效，请先编辑保存密码')
                return
        self._persist_settings_from_ui()
        self._running = True
        self.export_btn.setEnabled(False)
        self.result_table.setRowCount(0)
        # 导出时展示结果区
        if hasattr(self, 'result_panel'):
            self.result_panel.show()
        self.status_label.setText(
            f'正在并行导出 {len(jobs)} 条路径…' if zh else f'Exporting {len(jobs)} paths…'
        )
        cap = self._capture_settings()
        if hasattr(self, 'export_context'):
            try:
                cap['context_lines'] = int(self.export_context.value())
            except Exception:
                pass
        context = int(cap.get('context_lines') or 20)
        workers = int(cap.get('max_workers') or 4)
        timeout = int(cap.get('timeout_sec') or 30)
        case_i = bool(cap.get('case_insensitive', True))
        bridge = self._bridge

        def job_run():
            try:
                results = extract_logs_parallel(
                    jobs=jobs,
                    keyword=keyword,
                    extra_keywords=extras,
                    context_lines=context,
                    case_insensitive=case_i,
                    export_dir=export_dir,
                    timeout_sec=timeout,
                    max_workers=workers,
                    on_result=lambda r: bridge.result_ready.emit(r),
                )
                bridge.finished.emit(results)
            except Exception as exc:
                bridge.failed.emit(str(exc) or exc.__class__.__name__)

        self._executor.submit(job_run)
        self._set_work_mode('export')

    def _on_result_row(self, result: dict):
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        name = str(result.get('server_name') or result.get('host') or '')
        ok = bool(result.get('ok'))
        status = ('成功' if ok else '失败') if self.language == 'zh' else ('OK' if ok else 'Fail')
        if ok and int(result.get('line_count') or 0) == 0:
            status = '成功·0命中' if self.language == 'zh' else 'OK·0 hits'
        msg = str(result.get('message') or '')
        if not ok and msg:
            status = f'{status}: {msg[:40]}'
        svc = str(result.get('service_name') or '')
        values = [
            name,
            svc,
            status,
            str(result.get('line_count') if ok else '-'),
            str(result.get('local_path') or msg),
        ]
        for col, cell in enumerate(values):
            item = QTableWidgetItem(cell)
            if col == 2:
                item.setForeground(Qt.GlobalColor.darkGreen if ok else Qt.GlobalColor.red)
            if col == 4 and result.get('local_path'):
                item.setData(Qt.ItemDataRole.UserRole, result.get('local_path'))
            self.result_table.setItem(row, col, item)
        # 同步到会话控制台摘要
        self._console_append(f"[导出] {name}/{svc}: {status}")

    def _on_export_finished(self, results: list):
        self._running = False
        if hasattr(self, 'export_btn'):
            self.export_btn.setEnabled(True)
        if hasattr(self, 'session_export_btn'):
            self.session_export_btn.setEnabled(True)
        ok_n = sum(1 for r in results if r.get('ok'))
        self.status_label.setText(
            f'完成：{ok_n}/{len(results)} 成功' if self.language == 'zh' else f'Done: {ok_n}/{len(results)}'
        )
        # 若有成功文件，状态栏提示关键字目录
        for r in results or []:
            lp = str(r.get('local_path') or '')
            if r.get('ok') and lp:
                parent = os.path.dirname(lp)
                self.status_label.setText(
                    (f'完成：{ok_n}/{len(results)} 成功 · 目录 {parent}'
                     if self.language == 'zh' else
                     f'Done: {ok_n}/{len(results)} · {parent}')
                )
                break

    def _on_export_failed(self, message: str):
        self._running = False
        if hasattr(self, 'export_btn'):
            self.export_btn.setEnabled(True)
        if hasattr(self, 'session_export_btn'):
            self.session_export_btn.setEnabled(True)
        self.status_label.setText(message)
        show_error(self, 'PengTools', message)

    def _open_selected_result(self):
        row = self.result_table.currentRow()
        if row < 0:
            return
        item = self.result_table.item(row, 4) or self.result_table.item(row, 3)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole) or item.text()
        if path and os.path.isfile(path):
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _on_theme_changed_for_terms(self, _theme_id=None):
        for sess in list(getattr(self, '_term_sessions', []) or []):
            w = sess.get('widget')
            if w is not None and hasattr(w, 'refresh_theme'):
                try:
                    w.refresh_theme()
                except Exception:
                    pass

    def closeEvent(self, event):
        try:
            from ui.theme_manager import ThemeManager
            ThemeManager.instance().remove_listener(self._on_theme_changed_for_terms)
        except Exception:
            pass
        for sess in list(getattr(self, '_term_sessions', []) or []):
            try:
                self._close_session_resources(sess)
            except Exception:
                pass
        super().closeEvent(event)
