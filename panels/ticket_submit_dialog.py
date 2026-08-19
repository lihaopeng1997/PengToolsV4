# -*- coding: utf-8 -*-
"""一键提签弹窗与提签族配置。"""

from __future__ import annotations

import datetime
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from config import load_systems
from tools.requirements import requirement_identity
from tools.ticket_submit import (
    TICKET_ENVS, default_slot, load_ticket_profiles, next_ticket_folder,
    normalize_ticket_profile, requirement_candidates_for_profile,
    save_ticket_profiles, submit_ticket,
)
from ui.confirm_dialog import show_error, show_info, show_success, show_warning
from ui.design_system import apply_button
from ui.dialog_buttons import localize_button_box
from ui.field_metrics import size_compact_button, size_enum_combo, size_line, size_pick_combo


class TicketSubmitConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('配置提签 SVN')
        self.resize(640, 520)
        self._profiles = [normalize_ticket_profile(item) for item in load_ticket_profiles()]
        root = QVBoxLayout(self)
        hint = QLabel('按「提签族」配置各环境签库 SVN。未填地址的环境不能提交。空库可指定一份本机种子 xls。')
        hint.setObjectName('field-hint')
        hint.setWordWrap(True)
        root.addWidget(hint)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_profile)
        body.addWidget(self.list, 1)
        form_host = QWidget()
        form = QFormLayout(form_host)
        self.name_edit = QLineEdit()
        size_line(self.name_edit, 'std')
        self.code_edit = QLineEdit()
        size_line(self.code_edit, 'std')
        self.owner_edit = QLineEdit()
        size_line(self.owner_edit, 'std')
        self.seed_edit = QLineEdit()
        size_line(self.seed_edit, 'path')
        seed_btn = QPushButton('选择')
        size_compact_button(seed_btn)
        seed_btn.clicked.connect(self._pick_seed)
        seed_row = QHBoxLayout()
        seed_row.addWidget(self.seed_edit, 1)
        seed_row.addWidget(seed_btn)
        form.addRow('名称', self.name_edit)
        form.addRow('目录代号', self.code_edit)
        form.addRow('默认责任人', self.owner_edit)
        form.addRow('种子 xls', seed_row)
        self.system_boxes = []
        sys_box = QVBoxLayout()
        for system in load_systems():
            box = QCheckBox(system['name'])
            self.system_boxes.append(box)
            sys_box.addWidget(box)
        form.addRow('关联台账系统', sys_box)
        self.env_edits = {}
        for env in TICKET_ENVS:
            url = QLineEdit()
            size_line(url, 'path')
            url.setPlaceholderText(f'{env} 签库 SVN，可空')
            host = QLineEdit()
            size_line(host, 'std')
            host.setPlaceholderText('默认升级环境地址，可空')
            form.addRow(f'{env} SVN', url)
            form.addRow(f'{env} 地址', host)
            self.env_edits[env] = (url, host)
        body.addWidget(form_host, 2)
        root.addLayout(body, 1)
        tools = QHBoxLayout()
        add_btn = QPushButton('新增提签族')
        del_btn = QPushButton('删除')
        size_compact_button(add_btn)
        size_compact_button(del_btn)
        add_btn.clicked.connect(self._add_profile)
        del_btn.clicked.connect(self._delete_profile)
        tools.addWidget(add_btn)
        tools.addWidget(del_btn)
        tools.addStretch(1)
        root.addLayout(tools)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        localize_button_box(buttons, 'zh')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._save)
        root.addWidget(buttons)
        self._last_row = -1
        self._refresh_list(0)

    def _refresh_list(self, row=0):
        self.list.blockSignals(True)
        self.list.clear()
        for profile in self._profiles:
            item = QListWidgetItem(profile.get('name') or profile.get('id'))
            item.setData(Qt.ItemDataRole.UserRole, profile.get('id'))
            self.list.addItem(item)
        if self._profiles:
            target = max(0, min(row, len(self._profiles) - 1))
            self.list.setCurrentRow(target)
            self.list.blockSignals(False)
            self._show_profile(target)
        else:
            self.list.blockSignals(False)
            self._show_profile(-1)

    def _current_index(self):
        return self.list.currentRow()

    def _collect_form(self, index=None):
        if index is None:
            index = getattr(self, '_last_row', -1)
        if index < 0 or index >= len(self._profiles):
            return
        profile = dict(self._profiles[index])
        profile['name'] = self.name_edit.text().strip()
        profile['folder_code'] = self.code_edit.text().strip()
        profile['owner_default'] = self.owner_edit.text().strip()
        profile['seed_xls'] = self.seed_edit.text().strip()
        profile['source_systems'] = [box.text() for box in self.system_boxes if box.isChecked()]
        for env, (url, host) in self.env_edits.items():
            profile.setdefault('envs', {})[env] = {
                'svn_url': url.text().strip(),
                'host': host.text().strip(),
            }
        self._profiles[index] = normalize_ticket_profile(profile)
        item = self.list.item(index)
        if item is not None:
            item.setText(self._profiles[index].get('name') or self._profiles[index].get('id'))
            item.setData(Qt.ItemDataRole.UserRole, self._profiles[index].get('id'))

    def _show_profile(self, row):
        last = getattr(self, '_last_row', -1)
        if last != row and 0 <= last < len(self._profiles):
            self._collect_form(last)
        self._last_row = row
        if row < 0 or row >= len(self._profiles):
            return
        profile = self._profiles[row]
        self.name_edit.blockSignals(True)
        self.code_edit.blockSignals(True)
        self.owner_edit.blockSignals(True)
        self.seed_edit.blockSignals(True)
        self.name_edit.setText(profile.get('name', ''))
        self.code_edit.setText(profile.get('folder_code', ''))
        self.owner_edit.setText(profile.get('owner_default', ''))
        self.seed_edit.setText(profile.get('seed_xls', ''))
        selected = set(profile.get('source_systems') or [])
        for box in self.system_boxes:
            box.blockSignals(True)
            box.setChecked(box.text() in selected)
            box.blockSignals(False)
        for env, (url, host) in self.env_edits.items():
            block = (profile.get('envs') or {}).get(env) or {}
            url.blockSignals(True)
            host.blockSignals(True)
            url.setText(block.get('svn_url', ''))
            host.setText(block.get('host', ''))
            url.blockSignals(False)
            host.blockSignals(False)
        self.name_edit.blockSignals(False)
        self.code_edit.blockSignals(False)
        self.owner_edit.blockSignals(False)
        self.seed_edit.blockSignals(False)

    def _add_profile(self):
        self._collect_form(self._last_row)
        self._profiles.append(normalize_ticket_profile({
            'name': '新提签族',
            'folder_code': 'SYS',
        }))
        self._refresh_list(len(self._profiles) - 1)

    def _delete_profile(self):
        index = self._current_index()
        if index < 0:
            return
        del self._profiles[index]
        self._last_row = -1
        self._refresh_list(min(index, len(self._profiles) - 1))

    def _pick_seed(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择种子签文档', '', 'Excel (*.xls *.xlsx)')
        if path:
            self.seed_edit.setText(path)

    def _save(self):
        self._collect_form()
        save_ticket_profiles(self._profiles)
        self.accept()


class TicketSubmitDialog(QDialog):
    def __init__(self, requirements, selected_ids=None, parent=None, compact=False):
        super().__init__(parent)
        self.setWindowTitle('一键提签')
        self.resize(560 if compact else 640, 480 if compact else 560)
        self._all_requirements = list(requirements or [])
        self._compact = bool(compact)
        root = QVBoxLayout(self)
        hint = QLabel('会从该环境 SVN 拉最新一份签当模板，填入所选需求后提交新文件夹。内网 SVN 以你本机为准。')
        hint.setObjectName('field-hint')
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        self.profile_combo = QComboBox()
        size_pick_combo(self.profile_combo)
        self.env_combo = QComboBox()
        size_enum_combo(self.env_combo)
        for env in TICKET_ENVS:
            self.env_combo.addItem(env, env)
        self.slot_combo = QComboBox()
        size_enum_combo(self.slot_combo)
        for slot in ('10', '15', '11', '19'):
            self.slot_combo.addItem(f'{slot} 点', slot)
        self.slot_combo.setCurrentIndex(0 if default_slot() == '10' else 1)
        self.owner_edit = QLineEdit()
        size_line(self.owner_edit, 'std')
        self.host_edit = QLineEdit()
        size_line(self.host_edit, 'std')
        form.addRow('提签族', self.profile_combo)
        form.addRow('环境', self.env_combo)
        form.addRow('场次', self.slot_combo)
        form.addRow('责任人', self.owner_edit)
        if not compact:
            form.addRow('升级环境地址', self.host_edit)
            self.program_edit = QLineEdit()
            size_line(self.program_edit, 'std')
            self.program_edit.setPlaceholderText('程序清单，可空')
            self.remark_edit = QLineEdit()
            size_line(self.remark_edit, 'std')
            self.jar_check = QCheckBox('有 jar 包')
            form.addRow('程序清单', self.program_edit)
            form.addRow('备注', self.remark_edit)
            form.addRow('', self.jar_check)
        else:
            self.program_edit = QLineEdit()
            self.remark_edit = QLineEdit()
            self.jar_check = QCheckBox()
            self.host_edit.hide()
        root.addLayout(form)
        self.preview = QLabel()
        self.preview.setObjectName('field-hint')
        self.preview.setWordWrap(True)
        root.addWidget(self.preview)
        self.req_list = QListWidget()
        self.req_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        root.addWidget(self.req_list, 1)
        cfg = QPushButton('配置提签 SVN…')
        apply_button(cfg, 'secondary', compact=True)
        cfg.clicked.connect(self._open_config)
        root.addWidget(cfg, 0, Qt.AlignmentFlag.AlignLeft)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        localize_button_box(buttons, 'zh', Ok='确认提签')
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        ok.setDefault(False)
        ok.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._submit)
        root.addWidget(buttons)
        self.profile_combo.currentIndexChanged.connect(self._reload_requirements)
        self.env_combo.currentIndexChanged.connect(self._refresh_preview)
        self.slot_combo.currentIndexChanged.connect(self._refresh_preview)
        self.owner_edit.textChanged.connect(lambda _t: self._refresh_preview())
        self._selected_ids = set(selected_ids or [])
        self._load_profiles()
        self._reload_requirements()

    def _load_profiles(self):
        self._profiles = load_ticket_profiles()
        current = self.profile_combo.currentData()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self._profiles:
            self.profile_combo.addItem(profile.get('name') or profile.get('id'), profile.get('id'))
        index = self.profile_combo.findData(current)
        self.profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.profile_combo.blockSignals(False)

    def _current_profile(self):
        wanted = self.profile_combo.currentData()
        return next((item for item in self._profiles if item.get('id') == wanted), None)

    def _reload_requirements(self):
        profile = self._current_profile()
        if profile:
            self.owner_edit.setText(profile.get('owner_default') or '')
            env = self.env_combo.currentData()
            host = ((profile.get('envs') or {}).get(env) or {}).get('host') or ''
            self.host_edit.setText(host)
        items = requirement_candidates_for_profile(self._all_requirements, profile or {})
        self.req_list.clear()
        for req in items:
            label = ' '.join(part for part in (req.get('code'), req.get('title')) if part) or '未命名'
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = not self._selected_ids or requirement_identity(req) in self._selected_ids
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, req)
            self.req_list.addItem(item)
        self._refresh_preview()

    def _checked_requirements(self):
        result = []
        for index in range(self.req_list.count()):
            item = self.req_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _refresh_preview(self):
        profile = self._current_profile()
        if not profile:
            self.preview.setText('还没有提签族，请先配置。')
            return
        env = self.env_combo.currentData() or 'INT'
        svn_url = ((profile.get('envs') or {}).get(env) or {}).get('svn_url') or ''
        folder = next_ticket_folder(
            [], env, profile.get('folder_code'), self.owner_edit.text().strip() or profile.get('owner_default'),
            slot=self.slot_combo.currentData(),
        )
        if not svn_url:
            self.preview.setText(f'将生成 {folder}\n当前环境未配置 SVN，不能提交。')
        else:
            self.preview.setText(f'将生成 {folder}\n提交到：{svn_url.rstrip("/")}/{datetime.date.today().year}/')

    def _open_config(self):
        dialog = TicketSubmitConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_profiles()
            self._reload_requirements()

    def _submit(self):
        profile = self._current_profile()
        if not profile:
            show_warning(self, '一键提签', '请先配置提签族。')
            return
        reqs = self._checked_requirements()
        if not reqs:
            show_warning(self, '一键提签', '请至少勾选一条需求。')
            return
        env = self.env_combo.currentData()
        if not ((profile.get('envs') or {}).get(env) or {}).get('svn_url'):
            show_warning(self, '一键提签', '这个环境还没有配置提签 SVN。')
            return
        try:
            result = submit_ticket(
                profile, env, reqs,
                owner=self.owner_edit.text().strip(),
                slot=self.slot_combo.currentData(),
                host=self.host_edit.text().strip(),
                program_list=self.program_edit.text().strip(),
                remark=self.remark_edit.text().strip(),
                has_jar=self.jar_check.isChecked(),
            )
        except Exception as exc:
            show_error(self, '提签失败', str(exc))
            return
        show_success(
            self, '提签已提交',
            f"目录：{result.get('folder')}\nSVN：{result.get('url')}\n\n请在内网核对提交结果。",
        )
        self.accept()


def open_ticket_submit_dialog(requirements, selected_ids=None, parent=None, compact=False):
    dialog = TicketSubmitDialog(requirements, selected_ids=selected_ids, parent=parent, compact=compact)
    return dialog.exec()
