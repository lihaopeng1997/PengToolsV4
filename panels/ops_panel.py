# -*- coding: utf-8 -*-
from PyQt6.QtCore import QStringListModel, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from tools.ops_commands import (
    CATEGORIES, COMMANDS, RISK_LABELS, build_command, command_text,
    contains_forbidden_delete, infer_risk, load_custom_commands,
    output_guide, save_custom_commands, search_commands,
)
from ui.confirm_dialog import confirm_action, show_error, show_warning
from ui.field_metrics import size_combo, size_line


class CustomCommandDialog(QDialog):
    def __init__(self, language='zh', parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setWindowTitle('新增自定义运维命令' if zh else 'Add custom operations command')
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        note = QLabel(
            '内置命令不可修改或删除；自定义命令也不允许包含 rm、rmdir、unlink、shred 或 find -delete。'
            if zh else
            'Built-in commands cannot be changed or deleted. Delete commands are not accepted.'
        )
        note.setObjectName('ops-safety-note')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.title_edit = QLineEdit()
        size_line(self.title_edit, 'std')
        self.command_edit = QPlainTextEdit()
        self.command_edit.setMaximumHeight(82)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setMaximumHeight(92)
        self.category_combo = QComboBox()
        size_combo(self.category_combo, 'md')
        for key, labels in CATEGORIES.items():
            if key != 'all':
                self.category_combo.addItem(labels[0 if zh else 1], key)
        self.risk_combo = QComboBox()
        size_combo(self.risk_combo, 'sm')
        for key in ('safe', 'caution', 'danger'):
            self.risk_combo.addItem(RISK_LABELS[key][0 if zh else 1], key)
        form.addRow('名称' if zh else 'Title', self.title_edit)
        form.addRow('Linux 命令' if zh else 'Linux command', self.command_edit)
        form.addRow('中文解释' if zh else 'Description', self.description_edit)
        form.addRow('分类' if zh else 'Category', self.category_combo)
        form.addRow('风险等级' if zh else 'Risk level', self.risk_combo)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_checked(self):
        zh = self.language == 'zh'
        command = self.command_edit.toPlainText().strip()
        if not self.title_edit.text().strip() or not command:
            show_warning(self, 'PengTools', '请填写名称和命令。' if zh else 'Enter a title and command.')
            return
        if contains_forbidden_delete(command):
            show_error(
                self, 'PengTools',
                '为避免误操作，命令库不允许新增文件删除类命令。'
                if zh else 'File deletion commands are not allowed.'
            )
            return
        self.accept()

    def command_data(self):
        title = self.title_edit.text().strip()
        description = self.description_edit.toPlainText().strip() or '用户自定义命令'
        command = self.command_edit.toPlainText().strip()
        selected_risk = self.risk_combo.currentData()
        risk = 'danger' if infer_risk(command) == 'danger' else selected_risk
        return {
            'command': command,
            'category': self.category_combo.currentData(),
            'title_zh': title,
            'description_zh': description,
            'title_en': title,
            'description_en': description,
            'template': command,
            'params': [],
            'risk': risk,
            'workflow': None,
            'tags': '用户自定义 custom',
            'builtin': False,
        }


class OpsPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._custom_commands = load_custom_commands()
        self._commands = []
        self._current_command = None
        self._param_edits = {}
        self._copy_feedback_duration = 1500
        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.timeout.connect(self._restore_copy_button_text)
        self._setup_ui()
        self.set_language(language)
        self._refresh_results()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('ops-search')
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh_results)
        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.activated.connect(self._apply_completion)
        self.search_edit.setCompleter(self._completer)
        header.addWidget(self.search_edit, 1)
        self.category_combo = QComboBox()
        size_combo(self.category_combo, 'md')
        self.category_combo.currentIndexChanged.connect(self._refresh_results)
        header.addWidget(self.category_combo)
        self.add_btn = QPushButton()
        self.add_btn.clicked.connect(self._add_custom_command)
        header.addWidget(self.add_btn)
        root.addLayout(header)

        self.safety_note = QLabel()
        self.safety_note.setObjectName('ops-safety-note')
        self.safety_note.setWordWrap(True)
        self.safety_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # 可关闭的首次安全横条
        safety_row = QHBoxLayout()
        safety_row.addWidget(self.safety_note, 1)
        self.safety_dismiss = QPushButton('知道了')
        self.safety_dismiss.setProperty('compactAction', True)
        self.safety_dismiss.clicked.connect(self._dismiss_safety)
        safety_row.addWidget(self.safety_dismiss)
        self._safety_host = QWidget()
        self._safety_host.setLayout(safety_row)
        root.addWidget(self._safety_host)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QFrame()
        left.setObjectName('ops-list-card')
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        top = QHBoxLayout()
        self.result_label = QLabel()
        self.result_label.setObjectName('section-title')
        top.addWidget(self.result_label)
        top.addStretch()
        self.result_count = QLabel()
        self.result_count.setObjectName('small-label')
        top.addWidget(self.result_count)
        left_layout.addLayout(top)
        self.command_list = QListWidget()
        self.command_list.setObjectName('ops-command-list')
        self.command_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.command_list.customContextMenuRequested.connect(self._show_command_menu)
        self.command_list.currentItemChanged.connect(self._show_command)
        left_layout.addWidget(self.command_list)
        splitter.addWidget(left)

        right_container = QWidget()
        right_container_layout = QVBoxLayout(right_container)
        right_container_layout.setContentsMargins(0, 0, 0, 0)
        right_container_layout.setSpacing(6)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setContentsMargins(12, 4, 8, 8)
        title_row = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setObjectName('ops-title')
        title_row.addWidget(self.title_label, 1)
        self.category_badge = QLabel()
        self.category_badge.setObjectName('ops-category-badge')
        title_row.addWidget(self.category_badge)
        self.risk_badge = QLabel()
        self.risk_badge.setObjectName('ops-risk-badge')
        title_row.addWidget(self.risk_badge)
        detail.addLayout(title_row)
        self.description = QLabel()
        self.description.setObjectName('ops-description')
        self.description.setWordWrap(True)
        detail.addWidget(self.description)
        self.warning = QLabel()
        self.warning.setObjectName('ops-warning')
        self.warning.setWordWrap(True)
        detail.addWidget(self.warning)

        # 三段：说明（description+warning）→ 命令 → 结果
        self.guide_title = QLabel()
        self.guide_title.hide()  # 去掉泛化「使用指南」标题
        self.param_frame = QFrame()
        self.param_frame.setObjectName('ops-param-card')
        self.param_form = QFormLayout(self.param_frame)
        self.param_form.setContentsMargins(14, 12, 14, 12)
        self.param_form.setSpacing(8)
        detail.addWidget(self.param_frame)

        preview_top = QHBoxLayout()
        self.preview_title = QLabel()
        self.preview_title.setObjectName('section-title')
        preview_top.addWidget(self.preview_title)
        preview_top.addStretch()
        self.generate_btn = QPushButton()
        self.generate_btn.clicked.connect(self._generate_preview)
        preview_top.addWidget(self.generate_btn)
        detail.addLayout(preview_top)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName('ops-preview')
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(125)
        detail.addWidget(self.preview)
        self.output_title = QLabel()
        self.output_title.setObjectName('section-title')
        detail.addWidget(self.output_title)
        self.output_explanation = QLabel()
        self.output_explanation.setObjectName('ops-output-guide')
        self.output_explanation.setWordWrap(True)
        self.output_explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.addWidget(self.output_explanation)
        detail.addStretch()
        right_scroll.setWidget(right)
        right_container_layout.addWidget(right_scroll, 1)

        actions = QHBoxLayout()
        self.custom_mark = QLabel()
        self.custom_mark.setObjectName('field-hint')
        actions.addWidget(self.custom_mark)
        actions.addStretch()
        self.delete_btn = QPushButton()
        self.delete_btn.setObjectName('ops-delete-custom')
        self.delete_btn.clicked.connect(self._delete_custom_command)
        actions.addWidget(self.delete_btn)
        self.copy_btn = QPushButton()
        self.copy_btn.setObjectName('primary-btn')
        self.copy_btn.clicked.connect(self._copy_command)
        actions.addWidget(self.copy_btn)
        right_container_layout.addLayout(actions)
        splitter.addWidget(right_container)
        splitter.setSizes([350, 650])
        root.addWidget(splitter, 1)

    def _dismiss_safety(self):
        if hasattr(self, '_safety_host'):
            self._safety_host.hide()

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible, editor_min_height
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        for name in ('preview_edit', 'output_edit', 'command_list'):
            w = getattr(self, name, None)
            if w is not None and hasattr(w, 'setMinimumHeight'):
                try:
                    w.setMinimumHeight(max(120, editor_min_height() // 2) if mode in ('compact', 'narrow') else 0)
                except Exception:
                    pass

    def set_language(self, language):
        self.language = language
        self._copy_feedback_timer.stop()
        zh = language == 'zh'
        current_category = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for key, labels in CATEGORIES.items():
            self.category_combo.addItem(labels[0 if zh else 1], key)
        index = self.category_combo.findData(current_category or 'all')
        self.category_combo.setCurrentIndex(max(index, 0))
        self.category_combo.blockSignals(False)
        self.search_edit.setPlaceholderText(
            '模糊搜索：ps -ef、日志截取、端口、CPU、容器…'
            if zh else 'Fuzzy search: ps -ef, log extract, port, CPU, container…'
        )
        self.add_btn.setText('新增我的命令' if zh else 'Add my command')
        self.safety_note.setText(
            '安全边界：仅生成和复制命令，不连接服务器、不自动执行；不收录删除命令。修改服务、权限或进程状态的命令会在复制前强制确认。'
            if zh else
            'Safety: commands are generated and copied only. No server execution or delete commands. State-changing commands require confirmation.'
        )
        self.result_label.setText('命令与场景' if zh else 'Commands & scenarios')
        self.guide_title.setText('')
        self.preview_title.setText('命令' if zh else 'Command')
        self.output_title.setText('结果说明' if zh else 'Result')
        if hasattr(self, 'safety_dismiss'):
            self.safety_dismiss.setText('知道了' if zh else 'Got it')
        self.generate_btn.setText('重新生成' if zh else 'Regenerate')
        self.copy_btn.setText('复制命令' if zh else 'Copy command')
        self.delete_btn.setText('删除我的命令' if zh else 'Delete my command')
        self._refresh_results()

    def _all_commands(self):
        builtins = [dict(item, builtin=True) for item in COMMANDS]
        return builtins + self._custom_commands

    def _apply_completion(self, text):
        self.search_edit.setText(text.split('  ·  ', 1)[0].strip())

    def _refresh_results(self):
        if not hasattr(self, 'command_list'):
            return
        category = self.category_combo.currentData() or 'all'
        self._commands = search_commands(
            self.search_edit.text(), category, commands=self._all_commands()
        )
        self.command_list.clear()
        zh = self.language == 'zh'
        query = self.search_edit.text().strip()
        from tools.list_pin import decorate_title, is_pinned
        from tools.pinyin_search import highlight_terms, match_snippet
        from ui.search_highlight import focus_list_item, paint_list_item
        first_hit = None
        for command in self._commands:
            title = command['title_zh' if zh else 'title_en']
            display_command = command['command'].replace('\n', ' ')
            if len(display_command) > 31:
                display_command = display_command[:28] + '…'
            summary = title if len(title) <= 20 else title[:19] + '…'
            pinned = is_pinned(command)
            if query:
                display_command = highlight_terms(display_command, query)
                summary = highlight_terms(summary, query)
            label = decorate_title(f'{display_command}\n{summary}', pinned)
            item = QListWidgetItem(label)
            full_description = command['description_zh' if zh else 'description_en']
            source = '用户自定义' if not command.get('builtin', True) else '内置只读'
            pin_tip = ('已置顶\n' if zh else 'Pinned\n') if pinned else ''
            hit_tip = ''
            if query:
                sn = match_snippet(f'{command.get("command","")}\n{title}\n{full_description}', query)
                if sn:
                    hit_tip = (f'命中：{sn}\n\n' if zh else f'Match: {sn}\n\n')
            item.setToolTip(
                f"{pin_tip}{hit_tip}{command['command']}\n\n{title}\n{full_description}\n\n"
                f"{source if zh else ('Custom' if source == '用户自定义' else 'Built-in read-only')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, command)
            matched = bool(query)
            paint_list_item(item, matched=matched, current=matched and first_hit is None)
            self.command_list.addItem(item)
            if matched and first_hit is None:
                first_hit = item
        if query:
            self.result_count.setText(
                f'命中 {len(self._commands)} 条' if zh else f'{len(self._commands)} match(es)'
            )
        else:
            self.result_count.setText(
                f'{len(self._commands)} 条' if zh else f'{len(self._commands)} result(s)'
            )
        suggestions = [command_text(item, self.language) for item in self._commands[:15]]
        self._completion_model.setStringList(suggestions)
        if self.command_list.count():
            if first_hit is not None:
                focus_list_item(self.command_list, first_hit)
            else:
                self.command_list.setCurrentRow(0)
            # 详情区也高亮命令文本
            self._highlight_command_detail()
        else:
            self._clear_detail()

    def _clear_detail(self):
        self._current_command = None
        self._clear_params()
        self.title_label.setText('未找到命令' if self.language == 'zh' else 'No command found')
        self.description.clear()
        self.category_badge.clear()
        self.risk_badge.clear()
        self.warning.hide()
        self.preview.clear()
        self.output_explanation.clear()
        self.custom_mark.clear()
        self.copy_btn.setEnabled(False)
        self.delete_btn.hide()

    def _clear_params(self):
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self._param_edits.clear()

    def _show_command(self, current, _previous=None):
        self._copy_feedback_timer.stop()
        self._restore_copy_button_text()
        if current is None:
            self._clear_detail()
            return
        command = current.data(Qt.ItemDataRole.UserRole)
        self._current_command = command
        zh = self.language == 'zh'
        self.title_label.setText(command['title_zh' if zh else 'title_en'])
        self.description.setText(command['description_zh' if zh else 'description_en'])
        self.output_explanation.setText(output_guide(command, self.language))
        self.category_badge.setText(CATEGORIES[command['category']][0 if zh else 1])
        risk = command.get('risk', 'safe')
        self.risk_badge.setProperty('risk', risk)
        self.risk_badge.setText(RISK_LABELS[risk][0 if zh else 1])
        self.risk_badge.style().unpolish(self.risk_badge)
        self.risk_badge.style().polish(self.risk_badge)
        if risk == 'danger':
            self.warning.setText(
                '强提示：此命令会改变服务器状态。复制前必须再次确认目标、影响范围、审批和回滚方案。'
                if zh else 'Warning: this command changes server state. Verify target, impact, approval and rollback before copying.'
            )
            self.warning.show()
        elif risk == 'caution':
            self.warning.setText(
                '注意：该命令可能产生额外 IO、性能影响、网络访问或输出文件，请先确认环境。'
                if zh else 'Caution: this may cause IO, performance impact, network access or an output file.'
            )
            self.warning.show()
        else:
            self.warning.hide()
        self._clear_params()
        for param in command.get('params', []):
            edit = QLineEdit(str(param.get('default', '')))
            size_line(edit, 'std')
            edit.setPlaceholderText(param.get('placeholder', ''))
            edit.textChanged.connect(self._generate_preview)
            label = param['label_zh' if zh else 'label_en']
            self.param_form.addRow(label, edit)
            self._param_edits[param['name']] = edit
        if not command.get('params'):
            note = QLabel('此命令无需参数，可直接复制。' if zh else 'No parameters required; ready to copy.')
            note.setObjectName('field-hint')
            self.param_form.addRow(note)
        is_custom = not command.get('builtin', True)
        self.custom_mark.setText(
            ('用户自定义命令 · 可删除' if zh else 'Custom command · deletable')
            if is_custom else ('内置命令 · 只读不可删除' if zh else 'Built-in command · read-only')
        )
        self.delete_btn.setVisible(is_custom)
        self._generate_preview()
        self._highlight_command_detail()

    def _highlight_command_detail(self):
        """搜索时在标题/说明/预览中标出命中并滚动到预览首处。"""
        query = self.search_edit.text().strip() if hasattr(self, 'search_edit') else ''
        from tools.pinyin_search import highlight_terms
        from ui.search_highlight import apply_text_highlights, clear_text_highlights
        cmd = getattr(self, '_current_command', None)
        if not cmd:
            clear_text_highlights(self.preview)
            if hasattr(self, 'output_explanation'):
                clear_text_highlights(self.output_explanation)
            return
        zh = self.language == 'zh'
        title = cmd['title_zh' if zh else 'title_en']
        desc = cmd['description_zh' if zh else 'description_en']
        if query:
            self.title_label.setText(highlight_terms(title, query))
            self.description.setText(highlight_terms(desc, query))
            apply_text_highlights(self.preview, query, select_first=True)
            if hasattr(self, 'output_explanation'):
                apply_text_highlights(self.output_explanation, query, select_first=False)
        else:
            self.title_label.setText(title)
            self.description.setText(desc)
            clear_text_highlights(self.preview)
            if hasattr(self, 'output_explanation'):
                clear_text_highlights(self.output_explanation)

    def _values(self):
        return {name: edit.text() for name, edit in self._param_edits.items()}

    def _generate_preview(self):
        if not self._current_command:
            return
        try:
            text = build_command(self._current_command, self._values())
            if contains_forbidden_delete(text):
                raise ValueError('生成结果包含被禁止的删除命令')
            self.preview.setPlainText(text)
            self.copy_btn.setEnabled(True)
        except ValueError as exc:
            self.preview.setPlainText(str(exc))
            self.copy_btn.setEnabled(False)
        # 参数变更后保持搜索高亮
        if getattr(self, 'search_edit', None) and self.search_edit.text().strip():
            from ui.search_highlight import apply_text_highlights
            apply_text_highlights(self.preview, self.search_edit.text().strip(), select_first=True)

    def _copy_command(self):
        text = self.preview.toPlainText().strip()
        if not text or not self.copy_btn.isEnabled():
            return
        if self._current_command.get('risk') == 'danger':
            zh = self.language == 'zh'
            if not confirm_action(
                self,
                'PengTools · ' + ('高风险确认' if zh else 'Risk confirmation'),
                ('该命令会修改服务器状态，复制不代表可以直接执行。\n\n请确认：\n1. 目标主机和对象无误；\n2. 已评估业务影响；\n3. 已获得变更授权；\n4. 已准备回滚方案。\n\n仍要复制吗？'
                 if zh else
                 'This command changes server state. Confirm the target, impact, authorization and rollback plan. Copy anyway?'),
                confirm_text='仍要复制' if zh else 'Copy anyway',
                danger=True,
            ):
                return
        QApplication.clipboard().setText(text)
        # 立即反馈「已复制」，延时后恢复文案
        self.copy_btn.setText('已复制' if self.language == 'zh' else 'Copied')
        self._copy_feedback_timer.stop()
        self._copy_feedback_timer.start(self._copy_feedback_duration)

    def _restore_copy_button_text(self):
        self.copy_btn.setText('复制命令' if self.language == 'zh' else 'Copy command')

    def set_copy_feedback_duration(self, milliseconds):
        self._copy_feedback_duration = max(500, min(5000, int(milliseconds)))

    def _show_command_menu(self, point):
        item = self.command_list.itemAt(point)
        if not item:
            return
        command = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(command, dict):
            return
        from tools.list_pin import (
            is_pinned, ops_command_pin_id, pin_action_label, set_namespace_pinned,
        )
        menu = QMenu(self)
        pin_id = ops_command_pin_id(command)
        pinned = is_pinned(command)
        act = menu.addAction(pin_action_label(pinned, self.language))
        act.triggered.connect(
            lambda _=False, pid=pin_id, p=pinned: self._toggle_command_pin(pid, p)
        )
        if not command.get('builtin', True):
            menu.addSeparator()
            menu.addAction(
                '删除我的命令' if self.language == 'zh' else 'Delete my command',
                self._delete_custom_command,
            )
        menu.exec(self.command_list.viewport().mapToGlobal(point))

    def _toggle_command_pin(self, pin_id, currently_pinned):
        from tools.list_pin import set_namespace_pinned
        if not pin_id:
            return
        set_namespace_pinned('ops_command', pin_id, not currently_pinned)
        current = None
        if self.command_list.currentItem():
            current = self.command_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self._refresh_results()
        if current:
            for row in range(self.command_list.count()):
                item = self.command_list.item(row)
                data = item.data(Qt.ItemDataRole.UserRole) or {}
                if data.get('command') == current.get('command') and data.get('title_zh') == current.get('title_zh'):
                    self.command_list.setCurrentRow(row)
                    break

    def _add_custom_command(self):
        dialog = CustomCommandDialog(self.language, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._custom_commands.append(dialog.command_data())
        save_custom_commands(self._custom_commands)
        self.search_edit.clear()
        self.category_combo.setCurrentIndex(0)
        self._refresh_results()
        for row in range(self.command_list.count()):
            item = self.command_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole).get('command') == dialog.command_data()['command']:
                self.command_list.setCurrentRow(row)
                break

    def _delete_custom_command(self):
        command = self._current_command
        if not command or command.get('builtin', True):
            return
        zh = self.language == 'zh'
        if not confirm_action(
            self, '删除自定义命令',
            f"{'仅删除这条用户自定义命令，内置命令不会受影响。\n\n待删除：' if zh else 'Delete this custom command?\n\n'}{command['title_zh' if zh else 'title_en']}?",
        ):
            return
        self._custom_commands = [item for item in self._custom_commands if item is not command]
        save_custom_commands(self._custom_commands)
        self._refresh_results()
