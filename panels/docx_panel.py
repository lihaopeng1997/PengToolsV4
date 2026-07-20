# -*- coding: utf-8 -*-
import contextlib
import io
import os
import tempfile

from PyQt6.QtCore import QDate, QFileInfo, QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QDateEdit, QFileDialog, QFileIconProvider,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tools.docx_updater import (
    deduplicate_sql, detect_existing_changes, filter_docx_structure_sql, parse_sql, process,
    refined_docx_path,
)
from tools.docx_template_registry import match_document_template, supported_system_names
from tools.sql_tool import read_file_auto_encoding, validate_oracle_sql_detailed
from tools.svn_workspace import (
    SvnError, checkout, safe_folder_name, update_working_copy, validate_svn_url, working_copy_info,
)
from ui.aurora_progress import AuroraProgress
from ui.confirm_dialog import confirm_action, show_error, show_info, show_success, show_warning
from ui.field_metrics import size_compact_button, size_date, size_line

_FILE_ICON_PROVIDER = QFileIconProvider()


class DocxUpdateWorker(QThread):
    completed = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def __init__(self, input_docx, sql_path, author, output_path, update_date):
        super().__init__()
        self.input_docx = input_docx
        self.sql_path = sql_path
        self.author = author
        self.output_path = output_path
        self.update_date = update_date

    def run(self):
        capture = io.StringIO()
        try:
            with contextlib.redirect_stdout(capture):
                report = process(
                    self.input_docx, self.sql_path, self.author,
                    self.output_path, backup=False, update_date=self.update_date,
                )
            self.completed.emit(report, capture.getvalue())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if os.path.exists(self.sql_path):
                os.unlink(self.sql_path)


class DocxUpdatePanel(QWidget):
    task_completed = pyqtSignal()

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._sql_paths = []
        self._worker = None
        self._template_profile = None
        self._folder_docs = []
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.file_group = QGroupBox()
        form = QFormLayout(self.file_group)
        form.setSpacing(8)

        self.folder_path = QLineEdit()
        size_line(self.folder_path, 'path')
        self.folder_browse = QPushButton()
        size_compact_button(self.folder_browse)
        self.folder_browse.clicked.connect(self._choose_folder)
        self.folder_refresh = QPushButton()
        size_compact_button(self.folder_refresh)
        self.folder_refresh.clicked.connect(self._refresh_folder_docs)
        self.folder_open = QPushButton()
        size_compact_button(self.folder_open)
        self.folder_open.clicked.connect(lambda: self._open_path(self.folder_path.text().strip()))
        self.folder_row_label = QLabel()
        form.addRow(self.folder_row_label, self._multi_button_row(
            self.folder_path, self.folder_browse, self.folder_refresh, self.folder_open
        ))

        self.doc_list = QListWidget()
        self.doc_list.setObjectName('docx-doc-list')
        self.doc_list.setMinimumHeight(118)
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.doc_list.itemSelectionChanged.connect(self._on_doc_selected)
        self.doc_list.itemDoubleClicked.connect(self._open_list_item)
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._show_doc_list_menu)
        self.doc_list_label = QLabel()
        form.addRow(self.doc_list_label, self.doc_list)

        self.docx_path = QLineEdit()
        size_line(self.docx_path, 'path')
        self.docx_browse = QPushButton()
        size_compact_button(self.docx_browse)
        self.docx_browse.clicked.connect(self._choose_docx)
        self.docx_path.textChanged.connect(self._on_docx_path_changed)
        self.docx_row_label = QLabel()
        form.addRow(self.docx_row_label, self._path_row(self.docx_path, self.docx_browse))

        self.template_status = QLabel()
        self.template_status.setWordWrap(True)
        self.template_status.setObjectName('docx-template-status')
        self.template_row_label = QLabel()
        form.addRow(self.template_row_label, self.template_status)

        self.svn_url = QLineEdit()
        size_line(self.svn_url, 'path')
        self.svn_pull_btn = QPushButton()
        size_compact_button(self.svn_pull_btn)
        self.svn_pull_btn.clicked.connect(self._pull_svn_docs)
        self.svn_row_label = QLabel()
        form.addRow(self.svn_row_label, self._path_row(self.svn_url, self.svn_pull_btn))

        self.output_dir = QLineEdit()
        size_line(self.output_dir, 'path')
        self.output_dir_browse = QPushButton()
        size_compact_button(self.output_dir_browse)
        self.output_dir_browse.clicked.connect(self._choose_output_dir)
        self.output_dir_open = QPushButton()
        size_compact_button(self.output_dir_open)
        self.output_dir_open.clicked.connect(lambda: self._open_path(self.output_dir.text().strip()))
        self.output_dir.textChanged.connect(self._sync_output_path)
        self.output_dir_label = QLabel()
        form.addRow(self.output_dir_label, self._multi_button_row(
            self.output_dir, self.output_dir_browse, self.output_dir_open
        ))

        self.output_path = QLineEdit()
        size_line(self.output_path, 'path')
        self.output_path.setReadOnly(True)
        self.output_row_label = QLabel()
        form.addRow(self.output_row_label, self.output_path)

        self.author = QLineEdit('System')
        size_line(self.author, 'std')
        self.author_row_label = QLabel()
        form.addRow(self.author_row_label, self.author)

        self.update_date = QDateEdit(QDate.currentDate())
        self.update_date.setObjectName('docx-date')
        self.update_date.setDisplayFormat('yyyy-MM-dd')
        size_date(self.update_date)
        calendar = self.update_date.calendarWidget()
        calendar.setObjectName('docx-calendar')
        calendar.setGridVisible(False)
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.date_card = QFrame()
        self.date_card.setObjectName('docx-date-card')
        date_layout = QHBoxLayout(self.date_card)
        date_layout.setContentsMargins(8, 6, 8, 6)
        date_layout.setSpacing(8)
        self.date_badge = QLabel('DATE')
        self.date_badge.setObjectName('docx-date-badge')
        date_layout.addWidget(self.date_badge)
        date_layout.addWidget(self.update_date, 1)
        self.today_btn = QPushButton()
        self.today_btn.setObjectName('docx-date-today')
        self.today_btn.clicked.connect(lambda: self.update_date.setDate(QDate.currentDate()))
        date_layout.addWidget(self.today_btn)
        self.date_row_label = QLabel()
        form.addRow(self.date_row_label, self.date_card)
        layout.addWidget(self.file_group)

        mid = QSplitter(Qt.Orientation.Horizontal)
        mid.setChildrenCollapsible(False)

        browser_box = QWidget()
        browser_layout = QVBoxLayout(browser_box)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_top = QHBoxLayout()
        self.browser_label = QLabel()
        browser_top.addWidget(self.browser_label)
        browser_top.addStretch()
        self.browser_refresh = QPushButton()
        self.browser_refresh.setProperty('compactAction', True)
        self.browser_refresh.clicked.connect(self._refresh_output_browser)
        browser_top.addWidget(self.browser_refresh)
        self.browser_open_folder = QPushButton()
        self.browser_open_folder.setProperty('compactAction', True)
        self.browser_open_folder.clicked.connect(lambda: self._open_path(self._browser_root()))
        browser_top.addWidget(self.browser_open_folder)
        browser_layout.addLayout(browser_top)
        self.output_browser = QTreeWidget()
        self.output_browser.setObjectName('docx-output-browser')
        self.output_browser.setHeaderLabels(('名称', '类型', '大小'))
        self.output_browser.setRootIsDecorated(True)
        self.output_browser.setIndentation(16)
        self.output_browser.setAlternatingRowColors(True)
        self.output_browser.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.output_browser.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.output_browser.setColumnWidth(1, 110)
        self.output_browser.setColumnWidth(2, 80)
        self.output_browser.setExpandsOnDoubleClick(False)
        self.output_browser.itemDoubleClicked.connect(self._open_browser_item)
        self.output_browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.output_browser.customContextMenuRequested.connect(self._show_browser_menu)
        browser_layout.addWidget(self.output_browser, 1)
        mid.addWidget(browser_box)

        work_box = QWidget()
        work_layout = QVBoxLayout(work_box)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_split = QSplitter(Qt.Orientation.Vertical)
        input_box = QWidget()
        input_layout = QVBoxLayout(input_box)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_top = QHBoxLayout()
        self.sql_label = QLabel()
        input_top.addWidget(self.sql_label)
        input_top.addStretch()
        self.load_sql_btn = QPushButton()
        self.load_sql_btn.clicked.connect(self._load_sql)
        input_top.addWidget(self.load_sql_btn)
        self.preview_btn = QPushButton()
        self.preview_btn.clicked.connect(self._preview)
        input_top.addWidget(self.preview_btn)
        input_layout.addLayout(input_top)
        self.sql_editor = QPlainTextEdit()
        self.sql_editor.setPlaceholderText('CREATE TABLE ...;\nALTER TABLE ... ADD (...);')
        input_layout.addWidget(self.sql_editor)
        work_split.addWidget(input_box)

        output_box = QWidget()
        output_layout = QVBoxLayout(output_box)
        output_layout.setContentsMargins(0, 0, 0, 0)
        # 日志默认折叠为状态条，运行/失败时展开
        log_head = QHBoxLayout()
        self.log_toggle = QPushButton('日志 ▸')
        self.log_toggle.setCheckable(True)
        self.log_toggle.setProperty('compactAction', True)
        self.log_toggle.toggled.connect(self._toggle_log)
        log_head.addWidget(self.log_toggle)
        self.log_label = QLabel()
        self.log_label.setObjectName('small-label')
        log_head.addWidget(self.log_label, 1)
        output_layout.addLayout(log_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(32)
        self.log.hide()
        output_layout.addWidget(self.log)
        work_split.addWidget(output_box)
        work_split.setSizes([420, 80])
        work_layout.addWidget(work_split)
        mid.addWidget(work_box)
        mid.setSizes([340, 720])
        layout.addWidget(mid, 1)

        bottom = QHBoxLayout()
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        bottom.addWidget(self.hint, 1)
        self.update_btn = QPushButton()
        self.update_btn.setObjectName('primary-btn')
        self.update_btn.clicked.connect(self._update_document)
        bottom.addWidget(self.update_btn)
        layout.addLayout(bottom)
        # 浮层 Loading：不进 layout，避免生成文档时底栏跳动
        self.progress = AuroraProgress(self)

    @staticmethod
    def _path_row(line_edit, button):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(line_edit, 1)
        row.addWidget(button)
        return widget

    @staticmethod
    def _multi_button_row(line_edit, *buttons):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(line_edit, 1)
        for button in buttons:
            button.setProperty('compactAction', True)
            row.addWidget(button)
        return widget

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'progress'):
            self.progress.place_overlay()

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.file_group.setTitle('① 选文档  ·  ② 拉 SVN（可选）  ·  ③ 填 SQL 更新' if zh else '1 Pick doc · 2 Optional SVN · 3 Update with SQL')
        self.folder_row_label.setText('文档目录' if zh else 'Folder')
        self.doc_list_label.setText('点选文档' if zh else 'Pick document')
        self.docx_row_label.setText('当前文档' if zh else 'Current doc')
        self.output_dir_label.setText('输出目录' if zh else 'Output dir')
        self.output_row_label.setText('生成结果' if zh else 'Output file')
        self.svn_row_label.setText('SVN（可选）' if zh else 'SVN (optional)')
        self.template_row_label.setText('模板识别' if zh else 'Template')
        self.author_row_label.setText('作者' if zh else 'Author')
        self.date_row_label.setText('日期' if zh else 'Date')
        self.date_badge.setText('日' if zh else 'D')
        self.today_btn.setText('今天' if zh else 'Today')
        self.folder_browse.setText('选择' if zh else 'Browse')
        self.folder_refresh.setText('刷新' if zh else 'Refresh')
        self.folder_open.setText('打开目录' if zh else 'Open')
        self.docx_browse.setText('浏览' if zh else 'Browse')
        self.output_dir_browse.setText('选择' if zh else 'Browse')
        self.output_dir_open.setText('打开' if zh else 'Open')
        self.svn_pull_btn.setText('一键拉取' if zh else 'Pull')
        out_name = os.path.basename(self.output_dir.text().strip() or '') or ('输出' if zh else 'Output')
        self.browser_label.setText(out_name)
        self.browser_label.setToolTip(
            '双击文件夹展开，双击文件打开' if zh else 'Double-click folder to expand, file to open'
        )
        self.browser_refresh.setText('刷新' if zh else 'Refresh')
        self.browser_open_folder.setText('打开目录' if zh else 'Open dir')
        self.update_date.setToolTip('写入版本历史的日期' if zh else 'Date written into revision history')
        self.folder_path.setPlaceholderText('含接口文档的文件夹' if zh else 'Folder with interface docs')
        self.docx_path.setPlaceholderText('点上方列表即可' if zh else 'Click a document above')
        self.output_dir.setPlaceholderText('不填则输出到原文档同目录' if zh else 'Default: same folder as source')
        self.output_path.setPlaceholderText('自动生成' if zh else 'Auto')
        self.svn_url.setPlaceholderText('可选：svn://... 拉取到输出目录' if zh else 'Optional svn://... into output dir')
        self.author.setPlaceholderText('作者' if zh else 'Author')
        self.sql_label.setText('SQL' if zh else 'SQL')
        self.load_sql_btn.setText('上传 SQL' if zh else 'Load SQL')
        self.preview_btn.setText('预检' if zh else 'Preview')
        self.log_label.setText('' if zh else '')
        if hasattr(self, 'log_toggle'):
            self.log_toggle.setText(
                ('日志 ▾' if self.log_toggle.isChecked() else '日志 ▸') if zh else
                ('Log ▾' if self.log_toggle.isChecked() else 'Log ▸')
            )
        self.hint.setText('')
        self.hint.hide()
        self.hint.setToolTip(
            '选目录点文档 →（可选）SVN 拉取 → 粘贴 SQL 更新' if zh else
            'Pick doc → optional SVN → paste SQL and update'
        )
        self.update_btn.setText('一键更新文档' if zh else 'Update document')
        self._refresh_template_match()
        self._refresh_folder_docs()
        self._refresh_output_browser()

    def _organized_output_path(self, source):
        source = os.path.abspath(source) if source else ''
        if not source:
            return ''
        organized = refined_docx_path(source)
        output_dir = self.output_dir.text().strip()
        if output_dir:
            return os.path.join(os.path.abspath(output_dir), os.path.basename(organized))
        return organized

    def _sync_output_path(self):
        source = self.docx_path.text().strip()
        self.output_path.setText(self._organized_output_path(source) if source else '')

    def _on_docx_path_changed(self, _text=None):
        self._sync_output_path()
        self._refresh_template_match()

    def _toggle_log(self, checked):
        self.log.setVisible(bool(checked))
        if checked:
            self.log.setMaximumHeight(16777215)
            self.log.setMinimumHeight(80)
        else:
            self.log.setMaximumHeight(32)
        zh = self.language == 'zh'
        self.log_toggle.setText(
            ('日志 ▾' if checked else '日志 ▸') if zh else
            ('Log ▾' if checked else 'Log ▸')
        )

    def _expand_log(self):
        if hasattr(self, 'log_toggle') and not self.log_toggle.isChecked():
            self.log_toggle.setChecked(True)

    def _refresh_template_match(self):
        path = self.docx_path.text().strip()
        self._template_profile = match_document_template(path) if path else None
        if not path:
            self.template_status.clear()
            self.template_status.hide()
            self.template_status.setProperty('matched', False)
            if hasattr(self, 'template_row_label'):
                self.template_row_label.hide()
            return
        if self._template_profile:
            # 正常匹配不占行；详情放 tooltip
            profile = self._template_profile
            confidence = '精确匹配' if profile['confidence'] == 'exact' else '兼容匹配'
            tip = (
                f"{profile['system']} · {confidence}\n最新模板：{profile['template']}"
                if self.language == 'zh' else
                f"{profile['system']} · {profile['confidence']} match\nLatest: {profile['template']}"
            )
            self.template_status.clear()
            self.template_status.hide()
            self.template_status.setToolTip(tip)
            self.template_status.setProperty('matched', True)
            if hasattr(self, 'template_row_label'):
                self.template_row_label.hide()
        else:
            names = '、'.join(supported_system_names())
            self.template_status.setText(
                f'未匹配到系统模板，将阻止写入。支持：{names}' if self.language == 'zh'
                else 'No system template matched. Writing is blocked; rename or provide a supported template.'
            )
            self.template_status.show()
            self.template_status.setProperty('matched', False)
            if hasattr(self, 'template_row_label'):
                self.template_row_label.show()
        self.template_status.style().unpolish(self.template_status)
        self.template_status.style().polish(self.template_status)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择接口文档文件夹' if self.language == 'zh' else 'Choose document folder', self.folder_path.text().strip())
        if path:
            self.folder_path.setText(os.path.abspath(path))
            if not self.output_dir.text().strip():
                self.output_dir.setText(os.path.abspath(path))
            self._refresh_folder_docs()
            self._refresh_output_browser()

    def _choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, '选择输出目录' if self.language == 'zh' else 'Choose output directory', self.output_dir.text().strip())
        if path:
            self.output_dir.setText(os.path.abspath(path))
            self._refresh_output_browser()

    def _choose_docx(self):
        start = self.folder_path.text().strip() or self.output_dir.text().strip()
        path, _ = QFileDialog.getOpenFileName(self, '选择接口文档' if self.language == 'zh' else 'Choose document', start, 'Word (*.docx)')
        if path:
            path = os.path.abspath(path)
            self.docx_path.setText(path)
            folder = os.path.dirname(path)
            self.folder_path.setText(folder)
            if not self.output_dir.text().strip():
                self.output_dir.setText(folder)
            self._refresh_folder_docs()
            self._select_doc_in_list(path)
            self._refresh_output_browser()

    def _list_docx_files(self, folder):
        if not folder or not os.path.isdir(folder):
            return []
        files = []
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name.lower().endswith('.docx') and not name.startswith('~$'):
                files.append(path)
        return files

    def _refresh_folder_docs(self):
        folder = self.folder_path.text().strip()
        current = self.docx_path.text().strip()
        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        self._folder_docs = self._list_docx_files(folder)
        for path in self._folder_docs:
            item = QListWidgetItem(_FILE_ICON_PROVIDER.icon(QFileInfo(path)), os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.doc_list.addItem(item)
        self.doc_list.blockSignals(False)
        if current:
            self._select_doc_in_list(current)
        elif self._folder_docs:
            self.doc_list.setCurrentRow(0)

    def _select_doc_in_list(self, path):
        path = os.path.normcase(os.path.abspath(path))
        for index in range(self.doc_list.count()):
            item = self.doc_list.item(index)
            if os.path.normcase(os.path.abspath(item.data(Qt.ItemDataRole.UserRole))) == path:
                self.doc_list.setCurrentItem(item)
                return

    def _on_doc_selected(self):
        item = self.doc_list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            self.docx_path.setText(path)

    def _open_list_item(self, item):
        """列表双击：选中为当前文档并打开文件。"""
        path = item.data(Qt.ItemDataRole.UserRole) if item else ''
        if path and os.path.isfile(path):
            self.docx_path.setText(path)
            self._open_path(path)

    def _show_doc_list_menu(self, point):
        item = self.doc_list.itemAt(point)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        use_action = menu.addAction('设为当前文档' if self.language == 'zh' else 'Use as current')
        use_action.triggered.connect(lambda: self.docx_path.setText(path))
        open_doc = menu.addAction('打开文档' if self.language == 'zh' else 'Open document')
        open_doc.triggered.connect(lambda: self._open_path(path))
        open_folder = menu.addAction('打开所在文件夹' if self.language == 'zh' else 'Open containing folder')
        open_folder.triggered.connect(lambda: self._open_path(os.path.dirname(path)))
        menu.exec(self.doc_list.viewport().mapToGlobal(point))

    def _browser_root(self):
        for candidate in (self.output_dir.text().strip(), self.folder_path.text().strip()):
            if candidate and os.path.isdir(candidate):
                return os.path.abspath(candidate)
        return ''

    def _refresh_output_browser(self):
        self.output_browser.clear()
        root = self._browser_root()
        if not root:
            return
        nodes = {'': self.output_browser.invisibleRootItem()}
        for current, directories, files in os.walk(root):
            directories[:] = [name for name in sorted(directories) if name != '.svn']
            relative_dir = os.path.relpath(current, root)
            parent_key = '' if relative_dir == '.' else relative_dir
            parent = nodes.get(parent_key, self.output_browser.invisibleRootItem())
            for name in directories:
                path = os.path.join(current, name)
                rel = os.path.relpath(path, root)
                item = QTreeWidgetItem(parent, (name, '文件夹', '--'))
                item.setIcon(0, _FILE_ICON_PROVIDER.icon(QFileInfo(path)))
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                item.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
                nodes[rel] = item
            for name in sorted(files):
                if name.startswith('~$'):
                    continue
                path = os.path.join(current, name)
                ext = os.path.splitext(name)[1].lower()
                kind = {
                    '.docx': 'Word 文档', '.doc': 'Word 文档', '.sql': 'SQL 脚本',
                    '.xlsx': 'Excel', '.txt': '文本', '.md': 'Markdown',
                }.get(ext, '文件')
                try:
                    size = f'{os.path.getsize(path) / 1024:.1f} KB'
                except OSError:
                    size = '--'
                item = QTreeWidgetItem(parent, (name, kind, size))
                item.setIcon(0, _FILE_ICON_PROVIDER.icon(QFileInfo(path)))
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
                item.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
        self.output_browser.expandToDepth(0)

    def _open_browser_item(self, item, _column):
        """双击：文件夹只展开/折叠；文件才打开。"""
        if not item:
            return
        is_dir = bool(item.data(0, Qt.ItemDataRole.UserRole + 1))
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if is_dir or (path and os.path.isdir(path)):
            item.setExpanded(not item.isExpanded())
            return
        if path and os.path.isfile(path):
            self._open_path(path)

    def _show_browser_menu(self, point):
        item = self.output_browser.itemAt(point)
        menu = QMenu(self)
        if item:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            is_dir = bool(item.data(0, Qt.ItemDataRole.UserRole + 1))
            if is_dir:
                menu.addAction('展开/折叠' if self.language == 'zh' else 'Expand/Collapse', lambda: item.setExpanded(not item.isExpanded()))
                menu.addAction('打开目录' if self.language == 'zh' else 'Open folder', lambda: self._open_path(path))
            else:
                menu.addAction('打开文档' if self.language == 'zh' else 'Open document', lambda: self._open_path(path))
                menu.addAction('打开所在文件夹' if self.language == 'zh' else 'Open containing folder', lambda: self._open_path(os.path.dirname(path)))
            if path and path.lower().endswith('.docx'):
                menu.addAction('设为当前接口文档' if self.language == 'zh' else 'Use as current document', lambda: self.docx_path.setText(path))
        root = self._browser_root()
        if root:
            menu.addSeparator()
            menu.addAction('打开工作目录' if self.language == 'zh' else 'Open working directory', lambda: self._open_path(root))
        if menu.actions():
            menu.exec(self.output_browser.viewport().mapToGlobal(point))

    @staticmethod
    def _open_path(path):
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @staticmethod
    def _is_working_copy(path):
        try:
            working_copy_info(path)
            return True
        except Exception:
            return False

    def _pull_svn_docs(self):
        url = self.svn_url.text().strip()
        target = self.output_dir.text().strip()
        zh = self.language == 'zh'
        if not url:
            show_warning(self, '接口文档更新' if zh else 'Interface docs', '请填写 SVN 路径。' if zh else 'Enter an SVN URL.')
            return
        try:
            url = validate_svn_url(url)
        except ValueError as exc:
            show_warning(self, '接口文档更新' if zh else 'Interface docs', str(exc))
            return
        if not target:
            target = QFileDialog.getExistingDirectory(self, '选择 SVN 检出输出目录' if zh else 'Choose checkout directory')
            if not target:
                return
            self.output_dir.setText(os.path.abspath(target))
            target = self.output_dir.text().strip()
        target = os.path.abspath(target)
        os.makedirs(target, exist_ok=True)
        try:
            if self._is_working_copy(target):
                result = update_working_copy(target)
                message = result.get('output') or ('SVN 更新完成' if zh else 'SVN update finished')
            else:
                names = [name for name in os.listdir(target) if name != '.svn']
                checkout_target = target
                if names:
                    checkout_target = os.path.join(target, safe_folder_name(url.rstrip('/').rsplit('/', 1)[-1]))
                    if os.path.exists(checkout_target) and os.listdir(checkout_target) and not self._is_working_copy(checkout_target):
                        show_warning(
                            self, '接口文档更新' if zh else 'Interface docs',
                            (f'输出目录非空，且无法安全检出。请清空目录或换一个空目录。\n{checkout_target}' if zh else
                             f'Cannot safely checkout into a non-empty directory:\n{checkout_target}'),
                        )
                        return
                    if self._is_working_copy(checkout_target):
                        result = update_working_copy(checkout_target)
                        message = result.get('output') or ('SVN 更新完成' if zh else 'SVN update finished')
                        target = checkout_target
                    else:
                        result = checkout(url, checkout_target)
                        message = (result.get('output') or ('SVN 检出完成' if zh else 'SVN checkout finished'))
                        message = f'{message}\n\n检出目录：{checkout_target}'
                        target = checkout_target
                else:
                    result = checkout(url, checkout_target)
                    message = result.get('output') or ('SVN 检出完成' if zh else 'SVN checkout finished')
        except (SvnError, ValueError, OSError) as exc:
            show_error(self, '接口文档更新' if zh else 'Interface docs', str(exc))
            return
        self.output_dir.setText(target)
        self.folder_path.setText(target)
        self._refresh_folder_docs()
        self._refresh_output_browser()
        self._expand_log()
        self.log.setPlainText(message)
        show_info(self, '接口文档更新' if zh else 'Interface docs', message if len(message) < 800 else message[:800] + '…')

    def _load_sql(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'SQL', '', 'SQL (*.sql *.txt);;All files (*.*)')
        if not paths:
            return
        blocks = []
        for path in paths:
            try:
                blocks.append(f'-- 来源文件: {os.path.basename(path)}\n{read_file_auto_encoding(path).strip()}')
                self._sql_paths.append(path)
            except OSError as exc:
                show_error(self, 'PengTools', str(exc))
                return
        current = self.sql_editor.toPlainText().strip()
        self.sql_editor.setPlainText('\n\n'.join(([current] if current else []) + blocks))
        self.log.setPlainText(('已加载：\n' if self.language == 'zh' else 'Loaded:\n') + '\n'.join(paths))

    def _processing_docx_path(self):
        selected = self.docx_path.text().strip()
        organized = self.output_path.text().strip() or self._organized_output_path(selected)
        return organized if organized and os.path.isfile(organized) else selected

    @staticmethod
    def _parsed_lines(parsed):
        lines = []
        lines.extend('+ TABLE  ' + item['table_name'] for item in parsed['new_tables'])
        lines.extend('+ FIELD  ' + item['table_name'] + ': ' + ', '.join(c['name'] for c in item['columns']) for item in parsed['alter_adds'])
        lines.extend('~ FIELD  ' + item['table_name'] + ': ' + ', '.join(c['name'] for c in item['columns']) for item in parsed['alter_modifies'])
        lines.extend('* COMMENT ' + item['table_name'] + '.' + item['col_name'] for item in parsed['col_comments'])
        lines.extend('* COMMENT ' + item['table_name'] for item in parsed['table_comments'])
        return lines

    def _preview(self):
        sql = self.sql_editor.toPlainText()
        _, duplicates = deduplicate_sql(sql)
        lines = self._parsed_lines(parse_sql(sql))
        docx_path = self._processing_docx_path() if self.docx_path.text().strip() else ''
        existing = detect_existing_changes(docx_path, sql) if docx_path and os.path.isfile(docx_path) else []
        if duplicates:
            lines.append(f'\n! 重复 SQL: {len(duplicates)}')
            lines.extend('  - ' + statement.replace('\n', ' ')[:160] for statement in duplicates)
        if existing:
            lines.append(f'\n! 文档已存在内容: {len(existing)}')
            lines.extend(f"  - {item['table']}{'.' + item['field'] if item['field'] else ''}: {item['detail']}" for item in existing)
        self.log.setPlainText('\n'.join(lines) if lines else ('未识别到支持的 SQL' if self.language == 'zh' else 'No supported SQL found'))

    def _confirm_sql(self, sql, input_docx):
        unique_sql, duplicates = deduplicate_sql(sql)
        if duplicates:
            if not confirm_action(
                self, 'PengTools',
                (f'检测到 {len(duplicates)} 条重复 SQL。是否去重后继续？写入时仍会安全跳过文档内已有重复。'
                 if self.language == 'zh' else
                 f'{len(duplicates)} duplicate SQL statement(s) found. Deduplicate and continue?'),
                confirm_text='去重并继续' if self.language == 'zh' else 'Deduplicate',
                danger=False,
            ):
                return None
            sql = unique_sql

        structure_sql, rejected = filter_docx_structure_sql(sql)
        if rejected:
            preview = '\n'.join('- ' + item.replace('\n', ' ')[:150] for item in rejected[:8])
            if len(rejected) > 8:
                preview += f'\n... 另有 {len(rejected) - 8} 条'
            if not confirm_action(
                self, 'PengTools · 接口文档 SQL',
                (f'接口结构文档只支持 CREATE TABLE、ALTER TABLE ADD/MODIFY 和 COMMENT ON。\n'
                 f'检测到 {len(rejected)} 条非结构 DDL 或不支持的 SQL：\n{preview}\n\n'
                 '过滤这些语句并继续？'
                 if self.language == 'zh' else
                 f'The document updater supports CREATE TABLE, ALTER TABLE ADD/MODIFY and COMMENT ON only.\n'
                 f'{len(rejected)} unsupported statement(s):\n{preview}\n\nFilter and continue?'),
                confirm_text='过滤并继续' if self.language == 'zh' else 'Filter & continue',
                danger=False,
            ):
                return None
            sql = structure_sql
        if not sql.strip():
            show_warning(
                self, 'PengTools · 接口文档 SQL',
                '过滤后没有可用于更新接口文档的结构 DDL。' if self.language == 'zh'
                else 'No supported structure DDL remains after filtering.',
            )
            return None

        syntax_issues = validate_oracle_sql_detailed(sql)
        syntax_errors = [item for item in syntax_issues if item['severity'] == 'error']
        if syntax_errors:
            zh = self.language == 'zh'
            preview = '\n'.join(
                f"- SQL {item['statement']}: {item['message_zh' if zh else 'message_en']}"
                for item in syntax_errors[:8]
            )
            if not confirm_action(
                self, 'PengTools · 接口文档 SQL',
                (f'轻量语法检查发现 {len(syntax_errors)} 个问题：\n{preview}\n\n仍要继续尝试解析吗？'
                 if zh else f'Lightweight validation found {len(syntax_errors)} issue(s):\n{preview}\n\nTry parsing anyway?'),
                confirm_text='仍要继续' if zh else 'Continue',
                danger=False,
            ):
                return None

        existing = detect_existing_changes(input_docx, sql)
        if existing:
            preview = '\n'.join(
                f"- {item['table']}{'.' + item['field'] if item['field'] else ''}: {item['detail']}"
                for item in existing[:12]
            )
            if len(existing) > 12:
                preview += f'\n... 另有 {len(existing) - 12} 项'
            if not confirm_action(
                self, 'PengTools',
                (f'检测到 {len(existing)} 项表/字段/说明已存在：\n{preview}\n\n跳过已存在项并继续处理其余新增内容？'
                 if self.language == 'zh' else
                 f'{len(existing)} table/field/comment item(s) already exist:\n{preview}\n\nSkip them and continue?'),
                confirm_text='跳过并继续' if self.language == 'zh' else 'Skip & continue',
                danger=False,
            ):
                return None
        return sql

    def _update_document(self):
        selected_docx = self.docx_path.text().strip()
        output_path = self.output_path.text().strip() or self._organized_output_path(selected_docx)
        sql = self.sql_editor.toPlainText().strip()
        if not selected_docx or not os.path.isfile(selected_docx) or not sql:
            show_warning(self, 'PengTools', '请选择接口文档并输入 SQL。' if self.language == 'zh' else 'Select a document and enter SQL.')
            return
        self._refresh_template_match()
        if not self._template_profile:
            show_warning(
                self, 'PengTools · 接口文档更新',
                ('无法根据文档名称匹配系统模板，已停止写入。\n请使用对应系统的结构文档原始名称，或补充该系统模板后再处理。'
                 if self.language == 'zh' else
                 'No system template matches this filename, so writing was stopped. Use the original system document name or add its template first.'),
            )
            return
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        self.output_path.setText(output_path)
        input_docx = output_path if os.path.isfile(output_path) else selected_docx
        sql = self._confirm_sql(sql, input_docx)
        if sql is None:
            return

        try:
            with tempfile.NamedTemporaryFile('w', suffix='.sql', encoding='utf-8', delete=False) as stream:
                stream.write(sql)
                temp_path = stream.name
        except Exception as exc:
            self.log.setPlainText(str(exc))
            show_error(self, 'PengTools', str(exc))
            return

        self.update_btn.setEnabled(False)
        self.progress.start_busy('正在解析 SQL 与重组接口文档…' if self.language == 'zh' else 'Parsing SQL and reorganizing document…')
        self._worker = DocxUpdateWorker(
            input_docx, temp_path, self.author.text().strip() or 'System', output_path,
            self.update_date.date().toString('yyyy-MM-dd'),
        )
        self._worker.completed.connect(self._on_update_completed)
        self._worker.failed.connect(self._on_update_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_update_completed(self, report, log_text):
        output_path = report['save_path']
        self.log.setPlainText(log_text)
        if not output_path or not os.path.isfile(output_path):
            self._on_update_failed('没有产生输出文件' if self.language == 'zh' else 'No output file was created')
            return
        self.sql_editor.clear()
        self._sql_paths.clear()
        self.progress.finish('接口文档已生成' if self.language == 'zh' else 'Interface document generated')
        self.task_completed.emit()
        self._refresh_folder_docs()
        self._refresh_output_browser()
        show_success(
            self, '接口文档更新完成' if self.language == 'zh' else 'Interface document updated',
            (f'新增/修改 {len(report["changes"])} 项，跳过 {len(report["skipped"])} 项。\n\n请务必自行检查表结构、字段说明和版本记录后再提交。\n{output_path}'
             if self.language == 'zh' else
             f'Changed {len(report["changes"])} item(s), skipped {len(report["skipped"])}.\n\nPlease manually check table structures, field descriptions, and version history before submission.\n{output_path}')
        )

    def _on_update_failed(self, message):
        self._expand_log()
        self.log.setPlainText(message)
        self.progress.fail('处理失败，请检查输入' if self.language == 'zh' else 'Processing failed; check the input')
        show_error(self, 'PengTools', message)

    def _on_worker_finished(self):
        self.update_btn.setEnabled(True)
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
