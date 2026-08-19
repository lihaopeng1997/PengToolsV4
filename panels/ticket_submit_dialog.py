# -*- coding: utf-8 -*-
"""一键提签弹窗与提签族配置。"""

from __future__ import annotations

import datetime
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
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


ENV_LABELS = {
    'SIT': '系统测试（SIT）',
    'INT': '集成测试（INT）',
    'UAT': '用户测试（UAT）',
}


def _hint_label(text):
    lab = QLabel(text)
    lab.setObjectName('field-hint')
    lab.setWordWrap(True)
    return lab


def _field_block(title, hint, widget):
    host = QWidget()
    box = QVBoxLayout(host)
    box.setContentsMargins(0, 0, 0, 8)
    box.setSpacing(3)
    caption = QLabel(title)
    caption.setObjectName('section-title')
    box.addWidget(caption)
    if hint:
        box.addWidget(_hint_label(hint))
    box.addWidget(widget)
    return host


class TicketSubmitConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('配置签库地址')
        self.resize(760, 620)
        self._profiles = [normalize_ticket_profile(item) for item in load_ticket_profiles()]
        root = QVBoxLayout(self)
        root.addWidget(_hint_label(
            '左边选一套签（比如客户信息平台、车险共享中心），右边填这套签在各环境的 SVN 目录。'
            '没填 SVN 的环境，一键提签时选不了。'
        ))
        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel('签文档所属系统'))
        self.list = QListWidget()
        self.list.setMinimumWidth(160)
        self.list.currentRowChanged.connect(self._show_profile)
        left.addWidget(self.list, 1)
        body.addLayout(left, 1)
        form_host = QWidget()
        form = QVBoxLayout(form_host)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(2)
        self.name_edit = QLineEdit()
        size_line(self.name_edit, 'std')
        self.name_edit.setPlaceholderText('例如：客户信息平台、车险共享中心')
        form.addWidget(_field_block('显示名称', '出现在左边列表和提签下拉里。', self.name_edit))
        self.code_edit = QLineEdit()
        size_line(self.code_edit, 'std')
        self.code_edit.setPlaceholderText('例如：ECIF 或 prpcar')
        form.addWidget(_field_block(
            '签文件夹里的系统代号',
            '会出现在签目录名里。例：INT_INT_ECIF_2026081910A-李浩鹏 里的 ECIF。',
            self.code_edit,
        ))
        self.owner_edit = QLineEdit()
        size_line(self.owner_edit, 'std')
        self.owner_edit.setPlaceholderText('例如：李浩鹏')
        form.addWidget(_field_block('默认责任人', '提签时自动填到签文档「责任人」。', self.owner_edit))
        sys_host = QWidget()
        sys_box = QVBoxLayout(sys_host)
        sys_box.setContentsMargins(0, 0, 0, 0)
        sys_box.setSpacing(4)
        self.system_boxes = []
        for system in load_systems():
            box = QCheckBox(system['name'])
            self.system_boxes.append(box)
            sys_box.addWidget(box)
        form.addWidget(_field_block(
            '这份签要带哪些需求',
            '勾选后，点「一键提签」只会列出这些系统的需求。可多选。',
            sys_host,
        ))
        form.addWidget(_hint_label('下面按环境分别填签库。SIT=系统测试，INT=集成测试，UAT=用户测试。'))
        self.env_edits = {}
        for env in TICKET_ENVS:
            url = QLineEdit()
            size_line(url, 'path')
            url.setPlaceholderText(f'粘贴 {ENV_LABELS[env]} 签所在的 SVN 目录，没有就留空')
            host = QLineEdit()
            size_line(host, 'std')
            host.setPlaceholderText('填到签上的升级环境地址，例如 10.128.24.72')
            env_host = QWidget()
            env_box = QVBoxLayout(env_host)
            env_box.setContentsMargins(0, 0, 0, 0)
            env_box.setSpacing(4)
            env_box.addWidget(url)
            env_box.addWidget(host)
            form.addWidget(_field_block(
                f'{ENV_LABELS[env]} 的签库',
                '第一行：签文件夹所在的 SVN 地址。第二行：写进签文档的机器地址，可空。',
                env_host,
            ))
            self.env_edits[env] = (url, host)
        self.seed_edit = QLineEdit()
        size_line(self.seed_edit, 'path')
        self.seed_edit.setPlaceholderText('一般不用填。只有这个环境 SVN 上还没有任何历史签时才需要')
        seed_btn = QPushButton('选择本机文件')
        size_compact_button(seed_btn)
        seed_btn.clicked.connect(self._pick_seed)
        seed_row = QWidget()
        seed_l = QHBoxLayout(seed_row)
        seed_l.setContentsMargins(0, 0, 0, 0)
        seed_l.addWidget(self.seed_edit, 1)
        seed_l.addWidget(seed_btn)
        form.addWidget(_field_block(
            '本机模板（可选）',
            'SVN 上已有历史签时，软件会自动复制最新一份，不用选这个。',
            seed_row,
        ))
        form.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(form_host)
        body.addWidget(scroll, 3)
        root.addLayout(body, 1)
        tools = QHBoxLayout()
        add_btn = QPushButton('新增一套签')
        del_btn = QPushButton('删除这套')
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
            'name': '新系统签',
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
        self.resize(560 if compact else 680, 500 if compact else 620)
        self._all_requirements = list(requirements or [])
        self._compact = bool(compact)
        root = QVBoxLayout(self)
        root.addWidget(_hint_label(
            '选好签和需求后点「确认提签」。软件会复制该环境最新一份签，把需求写进去，再提交到 SVN。'
        ))
        form = QVBoxLayout()
        form.setSpacing(2)
        self.profile_combo = QComboBox()
        size_pick_combo(self.profile_combo)
        form.addWidget(_field_block('提到哪套签', '客户信息平台、车险共享中心等。没有的话先点下面「配置签库地址」。', self.profile_combo))
        self.env_combo = QComboBox()
        for env in TICKET_ENVS:
            self.env_combo.addItem(ENV_LABELS[env], env)
        size_enum_combo(self.env_combo)
        form.addWidget(_field_block('提到哪个环境', '系统测试 SIT / 集成测试 INT / 用户测试 UAT。', self.env_combo))
        self.slot_combo = QComboBox()
        self.slot_combo.addItem('上午 10 点', '10')
        self.slot_combo.addItem('下午 15 点', '15')
        self.slot_combo.addItem('11 点', '11')
        self.slot_combo.addItem('19 点', '19')
        size_enum_combo(self.slot_combo)
        self.slot_combo.setCurrentIndex(0 if default_slot() == '10' else 1)
        form.addWidget(_field_block('几点的签', '上午默认 10 点，下午默认 15 点。会写进文件夹名字。', self.slot_combo))
        self.owner_edit = QLineEdit()
        size_line(self.owner_edit, 'std')
        self.owner_edit.setPlaceholderText('写在签上和文件夹名后面的人，例如 李浩鹏')
        form.addWidget(_field_block('责任人', '签文档「责任人」和目录名末尾。', self.owner_edit))
        self.host_edit = QLineEdit()
        size_line(self.host_edit, 'std')
        self.host_edit.setPlaceholderText('写进签里的升级环境地址，例如 10.128.24.72')
        self.program_edit = QLineEdit()
        size_line(self.program_edit, 'std')
        self.program_edit.setPlaceholderText('例如：后端 或 ecif-service，可空')
        self.remark_edit = QLineEdit()
        size_line(self.remark_edit, 'std')
        self.remark_edit.setPlaceholderText('签上的备注，没有就空着')
        self.jar_check = QCheckBox('这次升级带 jar 包')
        if not compact:
            form.addWidget(_field_block('升级环境地址', '填到签文档「升级环境地址」那一格。', self.host_edit))
            form.addWidget(_field_block('程序清单', '填到签上「修改的程序清单」，如 后端、前端、jar 名。', self.program_edit))
            form.addWidget(_field_block('备注', '', self.remark_edit))
            form.addWidget(self.jar_check)
        else:
            self.host_edit.hide()
            self.program_edit.hide()
            self.remark_edit.hide()
            self.jar_check.hide()
        root.addLayout(form)
        self.preview = QLabel()
        self.preview.setObjectName('field-hint')
        self.preview.setWordWrap(True)
        root.addWidget(self.preview)
        root.addWidget(QLabel('勾选要写进这份签的需求（可多条）'))
        self.req_list = QListWidget()
        self.req_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        root.addWidget(self.req_list, 1)
        cfg = QPushButton('配置签库地址…')
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
        self.env_combo.currentIndexChanged.connect(self._on_env_changed)
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

    def _on_env_changed(self):
        profile = self._current_profile()
        if profile:
            env = self.env_combo.currentData()
            host = ((profile.get('envs') or {}).get(env) or {}).get('host') or ''
            self.host_edit.setText(host)
        self._refresh_preview()

    def _refresh_preview(self):
        profile = self._current_profile()
        if not profile:
            self.preview.setText('还没有配置任何签。请先点「配置签库地址」。')
            return
        env = self.env_combo.currentData() or 'INT'
        svn_url = ((profile.get('envs') or {}).get(env) or {}).get('svn_url') or ''
        folder = next_ticket_folder(
            [], env, profile.get('folder_code'), self.owner_edit.text().strip() or profile.get('owner_default'),
            slot=self.slot_combo.currentData(),
        )
        env_name = ENV_LABELS.get(env, env)
        if not svn_url:
            self.preview.setText(
                f'将生成文件夹：{folder}\n'
                f'{env_name} 还没有填签库 SVN，请先点「配置签库地址」。'
            )
        else:
            self.preview.setText(
                f'将生成文件夹：{folder}\n'
                f'提交到：{svn_url.rstrip("/")}/{datetime.date.today().year}/\n'
                '软件会复制该环境最新一份签当模板，再改需求后提交。'
            )

    def _open_config(self):
        dialog = TicketSubmitConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_profiles()
            self._reload_requirements()

    def _submit(self):
        profile = self._current_profile()
        if not profile:
            show_warning(self, '一键提签', '请先点「配置签库地址」，把这套签的 SVN 填上。')
            return
        reqs = self._checked_requirements()
        if not reqs:
            show_warning(self, '一键提签', '请至少勾选一条需求。')
            return
        env = self.env_combo.currentData()
        if not ((profile.get('envs') or {}).get(env) or {}).get('svn_url'):
            show_warning(self, '一键提签', f'{ENV_LABELS.get(env, env)} 还没有填签库 SVN，请先配置。')
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
