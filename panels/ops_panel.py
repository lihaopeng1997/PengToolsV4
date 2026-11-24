# -*- coding: utf-8 -*-
import re

from PyQt6.QtCore import QStringListModel, Qt, QThread, QTimer, pyqtSignal
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
from ui.dialog_buttons import clamp_dialog_geometry, localize_button_box
from ui.field_metrics import apply_form, size_combo, size_line
from ui.guided_tour import make_guide_button, start_guided_tour
from ui.motion import play_dialog_enter
from ui.splitter_prefs import install_splitter_prefs


class _OpsCommandAiWorker(QThread):
    """后台调用已配置的内网模型，用于生成只读排查命令或解释当前命令。"""

    finished_ok = pyqtSignal(str, str)  # (mode, content)
    failed = pyqtSignal(str, str)  # (mode, error)

    def __init__(self, mode: str, prompt: str, model_id: str, language: str = 'zh', parent=None):
        super().__init__(parent)
        self._mode = mode
        self._prompt = prompt
        self._model_id = model_id
        self._language = language

    def run(self):
        try:
            from tools.intranet_llm import chat_completions
            zh = self._language == 'zh'
            if self._mode == 'generate':
                system_msg = (
                    '你是资深 Linux 运维与 Java 线上排查专家。请根据用户描述给出最精准的一条安全只读 Linux 命令。'
                    '严禁输出任何删除/破坏性命令（如 rm、rmdir、unlink、shred、find -delete、mkfs、dd）。'
                    '请按以下格式简洁输出：\n'
                    '```bash\n<单行或可直接执行的命令>\n```\n'
                    '参数说明：简短列出需替换的占位符（如 PID、端口、日志路径）。\n'
                    '排查思路：1-2 句说明输出怎么看。'
                    if zh else
                    'You are a senior Linux & JVM operations engineer. Return ONE safe, read-only Linux command inside ```bash ... ``` '
                    '(never output rm/rmdir/unlink/shred/find -delete), followed by concise parameter and output tips.'
                )
            else:
                system_msg = (
                    '你是资深 Linux 运维助手。请用通俗中文分点解释用户给出的 Linux 命令：'
                    '1）每个参数和管道段的作用；2）典型输出怎么看；3）在生产环境执行时的安全注意事项（是否只读、有无性能影响）。'
                    '回答控制在 8 行以内，简洁实用。'
                    if zh else
                    'Explain the given Linux command concisely: flags/pipes, how to read output, and production safety notes.'
                )
            messages = [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': self._prompt},
            ]
            text = chat_completions(messages, model_config_id=self._model_id or None)
            self.finished_ok.emit(self._mode, str(text or '').strip())
        except Exception as exc:
            self.failed.emit(self._mode, str(exc))


class CustomCommandDialog(QDialog):
    def __init__(self, language='zh', parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setWindowTitle('新增自定义运维命令' if zh else 'Add custom operations command')
        clamp_dialog_geometry(self, 640, 520, min_width=540, min_height=400)
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
        apply_form(form)
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
        localize_button_box(buttons, self.language)
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

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
    send_to_ops_log = pyqtSignal(str)
    open_ai_log_workspace = pyqtSignal(str)

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._custom_commands = load_custom_commands()
        self._commands = []
        self._current_command = None
        self._param_edits = {}
        self._ai_worker = None
        self._ai_extracted_cmd = ''
        self._copy_feedback_duration = 1500
        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.timeout.connect(self._restore_copy_button_text)
        self._setup_ui()
        self.set_language(language)
        self._reload_ai_models()
        self._refresh_results()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        try:
            from ui.page_chrome import make_page_header
            page_header, self.page_title, self.page_subtitle = make_page_header(
                '命令库',
                '仅生成与复制命令，不连接服务器、不自动执行',
                'operations',
            )
            root.addWidget(page_header)
        except Exception:
            self.page_title = None
            self.page_subtitle = None

        header = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('ops-search')
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(220)
        self._search_debounce.timeout.connect(self._refresh_results)
        self.search_edit.textChanged.connect(lambda: self._search_debounce.start())
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
        try:
            from ui.design_system import apply_button
            apply_button(self.add_btn, 'secondary', compact=True)
        except Exception:
            pass
        header.addWidget(self.add_btn)
        self.guide_btn = make_guide_button(self._start_guided_tour, self.language, self)
        header.addWidget(self.guide_btn)
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
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)
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
        right_container_layout.setSpacing(8)
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
        apply_form(self.param_form)
        self.param_form.setContentsMargins(14, 12, 14, 12)
        detail.addWidget(self.param_frame)

        preview_top = QHBoxLayout()
        self.preview_title = QLabel()
        self.preview_title.setObjectName('zone-title')
        preview_top.addWidget(self.preview_title)
        preview_top.addStretch()
        self.generate_btn = QPushButton()
        self.generate_btn.clicked.connect(self._generate_preview)
        try:
            from ui.design_system import apply_button
            apply_button(self.generate_btn, 'secondary', compact=True)
        except Exception:
            pass
        preview_top.addWidget(self.generate_btn)
        detail.addLayout(preview_top)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName('ops-preview')
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(240)
        detail.addWidget(self.preview)
        self.output_title = QLabel()
        self.output_title.setObjectName('zone-title')
        detail.addWidget(self.output_title)
        self.output_explanation = QLabel()
        self.output_explanation.setObjectName('ops-output-guide')
        self.output_explanation.setWordWrap(True)
        self.output_explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.addWidget(self.output_explanation)

        # AI 命令助手卡片（按需调用内网模型生成只读排查命令或解释当前命令，与日志排查联动）
        self.ai_card = QFrame()
        self.ai_card.setObjectName('ops-ai-card')
        self.ai_card.setStyleSheet(
            'QFrame#ops-ai-card { background: #F7F5FD; border: 1px solid #DFD9F0; border-radius: 12px; }'
        )
        ai_l = QVBoxLayout(self.ai_card)
        ai_l.setContentsMargins(12, 10, 12, 10)
        ai_l.setSpacing(8)
        ai_head = QHBoxLayout()
        ai_head.setSpacing(8)
        self.ai_card_title = QLabel('AI 命令助手')
        self.ai_card_title.setStyleSheet('color: #262438; font-weight: 700; font-size: 13px; background: transparent;')
        ai_head.addWidget(self.ai_card_title)
        self.ai_model_combo = QComboBox()
        size_combo(self.ai_model_combo, 'md')
        self.ai_model_combo.setMinimumWidth(160)
        ai_head.addWidget(self.ai_model_combo)
        self.ai_refresh_models_btn = QPushButton('刷新模型')
        self.ai_refresh_models_btn.clicked.connect(self._reload_ai_models)
        ai_head.addWidget(self.ai_refresh_models_btn)
        ai_head.addStretch(1)
        self.ai_explain_btn = QPushButton('AI 解释当前命令')
        self.ai_explain_btn.clicked.connect(self._ask_ai_explain)
        ai_head.addWidget(self.ai_explain_btn)
        ai_l.addLayout(ai_head)

        ai_input_row = QHBoxLayout()
        ai_input_row.setSpacing(6)
        self.ai_prompt_edit = QLineEdit()
        size_line(self.ai_prompt_edit, 'std')
        self.ai_prompt_edit.setPlaceholderText('用白话描述想查什么，如：查 8080 端口占用并看最近 100 行 ERROR…')
        self.ai_prompt_edit.returnPressed.connect(self._ask_ai_generate)
        ai_input_row.addWidget(self.ai_prompt_edit, 1)
        self.ai_gen_btn = QPushButton('AI 生成命令')
        self.ai_gen_btn.clicked.connect(self._ask_ai_generate)
        ai_input_row.addWidget(self.ai_gen_btn)
        ai_l.addLayout(ai_input_row)

        self.ai_status_lbl = QLabel('')
        self.ai_status_lbl.setObjectName('field-hint')
        self.ai_status_lbl.setWordWrap(True)
        self.ai_status_lbl.hide()
        ai_l.addWidget(self.ai_status_lbl)

        self.ai_result_box = QPlainTextEdit()
        self.ai_result_box.setReadOnly(True)
        self.ai_result_box.setMaximumHeight(160)
        self.ai_result_box.hide()
        ai_l.addWidget(self.ai_result_box)

        ai_apply_row = QHBoxLayout()
        ai_apply_row.setSpacing(6)
        self.ai_use_btn = QPushButton('填入命令预览区')
        self.ai_use_btn.clicked.connect(self._apply_ai_command_to_preview)
        self.ai_use_btn.hide()
        ai_apply_row.addWidget(self.ai_use_btn)
        self.ai_send_log_btn = QPushButton('直接送到日志排查终端')
        self.ai_send_log_btn.clicked.connect(self._send_ai_command_to_log)
        self.ai_send_log_btn.hide()
        ai_apply_row.addWidget(self.ai_send_log_btn)
        ai_apply_row.addStretch(1)
        ai_l.addLayout(ai_apply_row)
        try:
            from ui.design_system import apply_button
            apply_button(self.ai_refresh_models_btn, 'ghost', compact=True)
            apply_button(self.ai_explain_btn, 'secondary', compact=True)
            apply_button(self.ai_gen_btn, 'primary', compact=True)
            apply_button(self.ai_use_btn, 'secondary', compact=True)
            apply_button(self.ai_send_log_btn, 'secondary', compact=True, icon='terminal', icon_size=15)
        except Exception:
            pass
        detail.addWidget(self.ai_card)
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
        self.open_ai_log_btn = QPushButton('AI 日志排查')
        self.open_ai_log_btn.clicked.connect(self._open_in_ai_log)
        actions.addWidget(self.open_ai_log_btn)
        self.send_log_btn = QPushButton('送到日志排查终端')
        self.send_log_btn.clicked.connect(self._send_to_ops_log)
        actions.addWidget(self.send_log_btn)
        self.copy_btn = QPushButton()
        self.copy_btn.setObjectName('primary-btn')
        self.copy_btn.clicked.connect(self._copy_command)
        try:
            from ui.design_system import apply_button
            apply_button(self.delete_btn, 'danger', compact=True)
            apply_button(self.open_ai_log_btn, 'ghost', compact=True)
            apply_button(self.send_log_btn, 'secondary', compact=True, icon='terminal', icon_size=15)
            apply_button(self.copy_btn, 'primary', compact=True)
        except Exception:
            pass
        actions.addWidget(self.copy_btn)
        right_container_layout.addLayout(actions)
        splitter.addWidget(right_container)
        splitter.setHandleWidth(16)
        splitter.setProperty('prismGutter', True)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        install_splitter_prefs(
            splitter,
            defaults=[280, 864],
            defaults_for_extent=lambda extent: [280, max(360, extent - 280)],
            page_id='ops-commands',
            tab_id='list-detail',
            min_sizes=[220, 360],
            accessible_name='运维命令列表/详情分隔',
        )
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
        if getattr(self, 'page_title', None) is not None:
            self.page_title.setText('命令库' if zh else 'Command Library')
        if getattr(self, 'page_subtitle', None) is not None:
            self.page_subtitle.setText(
                '生成与复制命令 · 支持 AI 生成/解释与一键填入日志排查终端（不自动执行）'
                if zh else
                'Generate & copy commands · AI helper & fill into Log Investigation terminal (no auto-run)'
            )
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
        self.add_btn.setToolTip('新增我的命令' if zh else 'Add my command')
        self.safety_note.setText(
            '安全边界：仅生成和复制命令，不连接服务器、不自动执行；不收录删除命令。修改服务、权限或进程状态的命令会在复制前强制确认。'
            if zh else
            'Safety: commands are generated and copied only. No server execution or delete commands. State-changing commands require confirmation.'
        )
        self.result_label.setText('命令与场景' if zh else 'Commands & scenarios')
        self.guide_title.setText('')
        self.preview_title.setText('命令' if zh else 'Command')
        self.output_title.setText('结果' if zh else 'Result')
        if hasattr(self, 'safety_dismiss'):
            self.safety_dismiss.setText('知道了' if zh else 'Got it')
        self.generate_btn.setText('重新生成' if zh else 'Regenerate')
        self.generate_btn.setToolTip('重新生成' if zh else 'Regenerate')
        self.copy_btn.setText('复制命令' if zh else 'Copy command')
        self.copy_btn.setToolTip('复制命令' if zh else 'Copy command')
        if hasattr(self, 'send_log_btn'):
            self.send_log_btn.setText('送到日志排查终端' if zh else 'Send to SSH terminal')
            self.send_log_btn.setToolTip(
                '跳转到「日志排查」并把命令填入终端输入框（不会自动执行，需你确认后回车运行）'
                if zh else
                'Jump to Log Investigation and fill this command into the SSH input bar (does not auto-run)'
            )
        if hasattr(self, 'open_ai_log_btn'):
            self.open_ai_log_btn.setText('AI 日志排查' if zh else 'AI Log Inspect')
            self.open_ai_log_btn.setToolTip(
                '带着当前命令场景跳转到「日志排查 → AI 日志与系统排查」继续分析'
                if zh else
                'Open AI Log Investigation with this command context'
            )
        if hasattr(self, 'ai_card_title'):
            self.ai_card_title.setText('AI 命令助手' if zh else 'AI Command Helper')
            self.ai_explain_btn.setText('AI 解释当前命令' if zh else 'Explain current')
            self.ai_gen_btn.setText('AI 生成命令' if zh else 'Generate')
            self.ai_refresh_models_btn.setText('刷新模型' if zh else 'Refresh')
            self.ai_use_btn.setText('填入命令预览区' if zh else 'Apply to preview')
            self.ai_send_log_btn.setText('直接送到日志排查终端' if zh else 'Send to SSH terminal')
            self.ai_prompt_edit.setPlaceholderText(
                '用白话描述想查什么，如：查 8080 端口占用、按分钟统计 ERROR 峰值、定位 Java 死锁…'
                if zh else
                'Describe what to inspect, e.g. check port 8080, count ERROR per minute, Java deadlock…'
            )
        if hasattr(self, 'guide_btn'):
            self.guide_btn.setText('引导' if zh else 'Guide')
        self.delete_btn.setText('删除我的命令' if zh else 'Delete my command')
        self.delete_btn.setToolTip('删除我的命令' if zh else 'Delete my command')
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
        first_hit = None
        for command in self._commands:
            title = command['title_zh' if zh else 'title_en']
            display_command = command['command'].replace('\n', ' ')
            if len(display_command) > 31:
                display_command = display_command[:28] + '…'
            summary = title if len(title) <= 20 else title[:19] + '…'
            pinned = is_pinned(command)
            # 搜索仅过滤：列表文案与未搜索时一致
            label = decorate_title(f'{display_command}\n{summary}', pinned)
            item = QListWidgetItem(label)
            full_description = command['description_zh' if zh else 'description_en']
            source = '用户自定义' if not command.get('builtin', True) else '内置只读'
            pin_tip = ('已置顶\n' if zh else 'Pinned\n') if pinned else ''
            item.setToolTip(
                f"{pin_tip}{command['command']}\n\n{title}\n{full_description}\n\n"
                f"{source if zh else ('Custom' if source == '用户自定义' else 'Built-in read-only')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.command_list.addItem(item)
            if query and first_hit is None:
                first_hit = item
        self.result_count.setText(
            f'{len(self._commands)} 条' if zh else f'{len(self._commands)} result(s)'
        )
        suggestions = [command_text(item, self.language) for item in self._commands[:15]]
        self._completion_model.setStringList(suggestions)
        if self.command_list.count():
            if first_hit is not None:
                self.command_list.setCurrentItem(first_hit)
            else:
                self.command_list.setCurrentRow(0)
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
        """恢复标题/说明纯文案；预览不做搜索高亮（列表搜索仅过滤）。"""
        from ui.search_highlight import clear_text_highlights
        cmd = getattr(self, '_current_command', None)
        if not cmd:
            clear_text_highlights(self.preview)
            if hasattr(self, 'output_explanation'):
                clear_text_highlights(self.output_explanation)
            return
        zh = self.language == 'zh'
        self.title_label.setText(cmd['title_zh' if zh else 'title_en'])
        self.description.setText(cmd['description_zh' if zh else 'description_en'])
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
            if hasattr(self, 'send_log_btn'):
                self.send_log_btn.setEnabled(True)
        except ValueError as exc:
            self.preview.setPlainText(str(exc))
            self.copy_btn.setEnabled(False)
            if hasattr(self, 'send_log_btn'):
                self.send_log_btn.setEnabled(False)

    def _confirm_danger_if_needed(self, text: str, action_zh: str, action_en: str) -> bool:
        risk = self._current_command.get('risk') if self._current_command else infer_risk(text)
        if risk != 'danger' and infer_risk(text) != 'danger':
            return True
        zh = self.language == 'zh'
        return bool(confirm_action(
            self,
            'PengTools · ' + ('高风险确认' if zh else 'Risk confirmation'),
            (f'该命令会修改服务器状态，{action_zh}前请确认：\n\n1. 目标主机和对象无误；\n2. 已评估业务影响；\n3. 已获得变更授权与回滚方案。\n\n仍要继续吗？'
             if zh else
             f'This command changes server state. Confirm target, impact, authorization and rollback before {action_en}. Continue?'),
            confirm_text=f'仍要{action_zh}' if zh else f'{action_en.title()} anyway',
            danger=True,
        ))

    def _send_to_ops_log(self, override_text: str | None = None):
        text = (override_text if override_text is not None else self.preview.toPlainText()).strip()
        if not text:
            return
        if contains_forbidden_delete(text):
            show_error(self, 'PengTools', '安全边界：禁止将删除类命令发送到终端。' if self.language == 'zh' else 'Delete commands cannot be sent to terminal.')
            return
        if not self._confirm_danger_if_needed(text, '发送到终端输入框', 'sending to terminal'):
            return
        self.send_to_ops_log.emit(text)

    def _open_in_ai_log(self):
        zh = self.language == 'zh'
        cmd = self.preview.toPlainText().strip()
        title = ''
        if self._current_command:
            title = self._current_command.get('title_zh' if zh else 'title_en', '')
        prompt = ''
        if cmd:
            prompt = (
                f'请结合当前服务器/日志排查场景，分析这条命令相关的故障排查思路（{title}）：\n{cmd}'
                if zh else
                f'Analyze troubleshooting steps for this command ({title}):\n{cmd}'
            )
        self.open_ai_log_workspace.emit(prompt)

    def _reload_ai_models(self):
        if not hasattr(self, 'ai_model_combo'):
            return
        zh = self.language == 'zh'
        prev = self.ai_model_combo.currentData()
        self.ai_model_combo.blockSignals(True)
        self.ai_model_combo.clear()
        try:
            from tools.intranet_llm import list_enabled_items
            items = list_enabled_items() or []
        except Exception:
            items = []
        if not items:
            self.ai_model_combo.addItem('未配置内网模型（可在设置中启用）' if zh else 'No enabled intranet model', '')
        else:
            for item in items:
                name = str(item.get('name') or item.get('model') or item.get('id') or '')
                self.ai_model_combo.addItem(name, str(item.get('id') or ''))
            if prev:
                idx = self.ai_model_combo.findData(prev)
                if idx >= 0:
                    self.ai_model_combo.setCurrentIndex(idx)
        self.ai_model_combo.blockSignals(False)

    def _set_ai_busy(self, busy: bool, status_text: str = ''):
        for btn in (self.ai_gen_btn, self.ai_explain_btn):
            btn.setEnabled(not busy)
        if status_text:
            self.ai_status_lbl.setText(status_text)
            self.ai_status_lbl.show()
        elif not busy:
            self.ai_status_lbl.hide()

    def _start_ai_task(self, mode: str, prompt: str):
        zh = self.language == 'zh'
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        model_id = str(self.ai_model_combo.currentData() or '').strip()
        if not model_id:
            self._reload_ai_models()
            model_id = str(self.ai_model_combo.currentData() or '').strip()
        if not model_id:
            self.ai_status_lbl.setText(
                '尚未启用内网模型：请先到「设置 → 模型配置」添加并启用内网模型；内置命令和「送到日志排查终端」仍可直接使用。'
                if zh else
                'No enabled intranet model configured in Settings; built-in commands and terminal send still work.'
            )
            self.ai_status_lbl.show()
            return
        self._ai_extracted_cmd = ''
        self.ai_use_btn.hide()
        self.ai_send_log_btn.hide()
        self._set_ai_busy(True, '正在请求内网模型生成安全命令…' if (zh and mode == 'generate') else ('正在请求内网模型解释当前命令…' if zh else 'Contacting intranet LLM…'))
        worker = _OpsCommandAiWorker(mode, prompt, model_id, self.language, self)
        worker.finished_ok.connect(self._on_ai_finished)
        worker.failed.connect(self._on_ai_failed)
        worker.finished.connect(worker.deleteLater)
        self._ai_worker = worker
        worker.start()

    def _ask_ai_generate(self):
        zh = self.language == 'zh'
        desc = self.ai_prompt_edit.text().strip()
        if not desc:
            self.ai_status_lbl.setText('请先输入想排查的场景，例如：查看 8080 端口占用或定位 Java 死锁。' if zh else 'Enter what you want to inspect first.')
            self.ai_status_lbl.show()
            self.ai_prompt_edit.setFocus()
            return
        self._start_ai_task('generate', desc)

    def _ask_ai_explain(self):
        zh = self.language == 'zh'
        cmd = self.preview.toPlainText().strip()
        if not cmd:
            self.ai_status_lbl.setText('当前命令预览区为空，请先在左侧选择一条命令。' if zh else 'Select a command first.')
            self.ai_status_lbl.show()
            return
        self._start_ai_task('explain', cmd)

    @staticmethod
    def _extract_bash_command(text: str) -> str:
        m = re.search(r'```(?:bash|sh|shell)?\s*\n(.*?)```', text, re.S | re.I)
        if m:
            lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip() and not ln.strip().startswith('#')]
            return '\n'.join(lines).strip()
        return ''

    def _on_ai_finished(self, mode: str, content: str):
        zh = self.language == 'zh'
        self._ai_worker = None
        self._set_ai_busy(False)
        self.ai_result_box.setPlainText(content or ('模型未返回有效内容。' if zh else 'Empty response.'))
        self.ai_result_box.show()
        if mode == 'generate':
            extracted = self._extract_bash_command(content)
            if extracted and contains_forbidden_delete(extracted):
                self.ai_status_lbl.setText('已拦截：模型输出包含被禁止的文件删除类命令，未填入预览区。' if zh else 'Blocked: generated command contains forbidden deletion.')
                self.ai_status_lbl.show()
                return
            if extracted:
                self._ai_extracted_cmd = extracted
                self.ai_use_btn.show()
                self.ai_send_log_btn.show()
                self.ai_status_lbl.setText('已生成只读排查命令：可一键填入预览区，或直接送到日志排查终端（不会自动执行）。' if zh else 'Generated safe command: apply to preview or send to SSH terminal.')
                self.ai_status_lbl.show()

    def _on_ai_failed(self, _mode: str, err: str):
        self._ai_worker = None
        self._set_ai_busy(False, f'AI 请求失败：{err}' if self.language == 'zh' else f'AI request failed: {err}')

    def _apply_ai_command_to_preview(self):
        if not self._ai_extracted_cmd or contains_forbidden_delete(self._ai_extracted_cmd):
            return
        self.preview.setPlainText(self._ai_extracted_cmd)
        self.copy_btn.setEnabled(True)
        self.send_log_btn.setEnabled(True)

    def _send_ai_command_to_log(self):
        if not self._ai_extracted_cmd:
            return
        self._send_to_ops_log(self._ai_extracted_cmd)

    def _start_guided_tour(self):
        zh = self.language == 'zh'
        steps = [
            (
                self.search_edit,
                '按场景或命令名检索' if zh else 'Search by scenario or command',
                '直接搜「Java 死锁」「幽灵文件」「HTTP 耗时」「端口」或命令名，右侧可按分类筛选（含新增 Java / JVM 排查）。'
                if zh else
                'Search by symptom or command name, and filter by category including Java/JVM.',
            ),
            (
                self.param_frame,
                '填空生成安全参数化命令' if zh else 'Fill parameters safely',
                '只需改 PID、端口或日志路径，下方实时生成带引号转义的安全命令，不会误拼接删除指令。'
                if zh else
                'Edit PID, port, or log path to render shell-safe commands automatically.',
            ),
            (
                self.ai_card,
                'AI 定制命令与白话解释' if zh else 'AI generate & explain',
                '找不到现成模板？输入白话让内网模型生成只读排查命令，或点「AI 解释当前命令」看懂每个参数含义。'
                if zh else
                'Ask your configured intranet LLM to craft a safe command or explain the selected one.',
            ),
            (
                self.send_log_btn,
                '一键送到日志排查终端' if zh else 'Send to Log Investigation terminal',
                '点「送到日志排查终端」自动跳到 SSH 终端并填好命令（绝不自动执行），确认主机和参数后回车即可运行。'
                if zh else
                'Jumps to Log Investigation and fills the SSH input bar without auto-running.',
            ),
        ]
        start_guided_tour(self, steps, module_title='命令库' if zh else 'Command Library', language=self.language)

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

    @staticmethod
    def _same_custom_command(left: dict, right: dict) -> bool:
        if left is right:
            return True
        lid = str(left.get('id') or '').strip()
        rid = str(right.get('id') or '').strip()
        if lid and rid:
            return lid == rid
        return (
            str(left.get('command') or '') == str(right.get('command') or '')
            and str(left.get('title_zh') or '') == str(right.get('title_zh') or '')
            and str(left.get('category') or '') == str(right.get('category') or '')
        )

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
        removed = False
        remaining = []
        for item in self._custom_commands:
            if not removed and self._same_custom_command(item, command):
                removed = True
                continue
            remaining.append(item)
        if not removed:
            show_warning(self, '删除自定义命令', '未找到对应的自定义命令。' if zh else 'Custom command not found.')
            return
        self._custom_commands = remaining
        save_custom_commands(self._custom_commands)
        self._current_command = None
        self._refresh_results()
