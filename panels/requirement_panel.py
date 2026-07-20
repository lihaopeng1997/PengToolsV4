# -*- coding: utf-8 -*-
import datetime
import copy
import os
import re

from PyQt6.QtCore import QDate, QEvent, QFileInfo, QItemSelectionModel, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
    QHeaderView, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

_FILE_ICON_PROVIDER = QFileIconProvider()

from config import SVN_WORKSPACE_DIR, load_requirement_ui, load_systems, save_requirement_ui
from tools.personal_knowledge import (
    export_word_entry, export_workbook_entry, extract_document_entries,
    read_text_file,
)
from tools.requirements import (
    CATEGORIES, FLAG_DEFS, PRIORITIES, STATUSES, active_flags, apply_auto_inference,
    classify_requirement, flag_is_active, flag_status_text, load_requirements,
    merge_working_copies, merged_sql, normalize_flag_done, normalize_requirement,
    requirement_from_text, requirement_from_working_copy, requirement_search_text,
    save_requirements,
)
from tools.svn_workspace import (
    SvnError, add_existing_files, add_text_file, checkout, commit_working_copy, lock_file, month_end_date,
    safe_folder_name, scan_working_copies, svn_status, update_many,
    unlock_file, validate_svn_url, working_copy_info, workspace_files,
)
from ui.confirm_dialog import (
    confirm_action, offer_next_steps, show_error, show_info, show_success, show_warning,
)
from ui.aurora_progress import AuroraProgress
from ui.field_metrics import size_combo, size_compact_button, size_date, size_line


IS_DIR_ROLE = int(Qt.ItemDataRole.UserRole) + 1
GROUP_MONTH_ROLE = int(Qt.ItemDataRole.UserRole) + 2


def format_online_month_label(month):
    """把 online_month 规范成“2026年6月”这类完整中文月份标题。"""
    text = str(month or '').strip()
    if not text or text == '未分月':
        return '未分月'
    match = re.fullmatch(r'(20\d{2})[-/.](0?[1-9]|1[0-2])', text)
    if match:
        return f'{match.group(1)}年{int(match.group(2))}月'
    match = re.fullmatch(r'(20\d{2})年(0?[1-9]|1[0-2])月?', text)
    if match:
        return f'{match.group(1)}年{int(match.group(2))}月'
    match = re.fullmatch(r'(20\d{2})(0[1-9]|1[0-2])', text)
    if match:
        return f'{match.group(1)}年{int(match.group(2))}月'
    return text


class RequirementTree(QTreeWidget):
    requirementsMoved = pyqtSignal(list, str)

    def dropEvent(self, event):
        target = self.itemAt(event.position().toPoint())
        group = target if target and target.data(0, GROUP_MONTH_ROLE) is not None else (target.parent() if target else None)
        month = group.data(0, GROUP_MONTH_ROLE) if group else None
        requirement_ids = [
            item.data(0, Qt.ItemDataRole.UserRole).get('id')
            for item in self.selectedItems()
            if isinstance(item.data(0, Qt.ItemDataRole.UserRole), dict)
        ]
        if month is None or not requirement_ids:
            event.ignore()
            return
        QTimer.singleShot(0, lambda ids=list(requirement_ids), target_month=month: self.requirementsMoved.emit(ids, target_month))
        event.ignore()


def parse_year_month(value, fallback=None):
    """解析 yyyy-MM / yyyy-M / 2026年7月 等，返回 (year, month) 或 fallback。"""
    text = str(value or '').strip()
    fallback = fallback or QDate.currentDate()
    match = re.fullmatch(r'(20\d{2})[-/.](0?[1-9]|1[0-2])', text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r'(20\d{2})年(0?[1-9]|1[0-2])月?', text)
    if match:
        return int(match.group(1)), int(match.group(2))
    date = QDate.fromString(text + '-01', 'yyyy-MM-dd')
    if date.isValid():
        return date.year(), date.month()
    date = QDate.fromString(text, 'yyyy-MM-dd')
    if date.isValid():
        return date.year(), date.month()
    return fallback.year(), fallback.month()


class MonthPickerDialog(QDialog):
    """只选年月，不出现日期格子。"""

    def __init__(self, year=None, month=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择月份')
        self.setModal(True)
        today = QDate.currentDate()
        year = int(year or today.year())
        month = int(month or today.month())
        root = QVBoxLayout(self)
        root.setSpacing(10)
        tip = QLabel('上线归档只需要年月，无需选择具体日期。')
        tip.setObjectName('small-label')
        tip.setWordWrap(True)
        root.addWidget(tip)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel('年'))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2000, 2099)
        self.year_spin.setValue(max(2000, min(2099, year)))
        self.year_spin.setFixedWidth(100)
        row.addWidget(self.year_spin)
        row.addWidget(QLabel('月'))
        self.month_combo = QComboBox()
        size_combo(self.month_combo, 'sm')
        for value in range(1, 13):
            self.month_combo.addItem(f'{value:02d} 月', value)
        self.month_combo.setCurrentIndex(max(0, min(11, month - 1)))
        row.addWidget(self.month_combo)
        row.addStretch()
        root.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确定')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def month_text(self):
        return f'{self.year_spin.value():04d}-{int(self.month_combo.currentData()):02d}'


class MonthSelect(QWidget):
    """内联年月下拉：只选月，不能选日。"""

    def __init__(self, value='', parent=None):
        super().__init__(parent)
        year, month = parse_year_month(value)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2000, 2099)
        self.year_spin.setValue(year)
        self.year_spin.setFixedWidth(92)
        self.year_spin.setFixedHeight(34)
        self.year_spin.setSuffix(' 年')
        self.month_combo = QComboBox()
        size_combo(self.month_combo, 'sm')
        for value in range(1, 13):
            self.month_combo.addItem(f'{value:02d} 月', value)
        self.month_combo.setCurrentIndex(max(0, min(11, month - 1)))
        layout.addWidget(self.year_spin)
        layout.addWidget(self.month_combo)

    def month_text(self):
        return f'{self.year_spin.value():04d}-{int(self.month_combo.currentData()):02d}'

    def set_month_text(self, value):
        year, month = parse_year_month(value)
        self.year_spin.setValue(year)
        self.month_combo.setCurrentIndex(max(0, min(11, month - 1)))

    def date(self):
        """兼容旧 QDateEdit 调用：返回当月 1 日。"""
        return QDate(self.year_spin.value(), int(self.month_combo.currentData()), 1)


class DateInput(QWidget):
    def __init__(self, value='', month_only=False, parent=None):
        super().__init__(parent)
        self.month_only = month_only
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.edit = QLineEdit(value)
        self.edit.setObjectName('date-input-edit')
        size_date(self.edit, month=month_only)
        self.edit.setPlaceholderText('yyyy-MM，可留空' if month_only else 'yyyy-MM-dd，可留空')
        self.button = QPushButton('选月份' if month_only else '选日期')
        size_compact_button(self.button)
        self.button.clicked.connect(self._choose_date)
        layout.addWidget(self.edit, 1); layout.addWidget(self.button)

    def text(self):
        return self.edit.text().strip()

    def is_valid(self):
        value = self.text()
        if not value:
            return True
        if self.month_only:
            # 允许 2026-07 / 2026-7 / 2026/07 / 2026年7月
            if re.fullmatch(r'20\d{2}[-/.](0?[1-9]|1[0-2])', value):
                return True
            if re.fullmatch(r'20\d{2}年(0?[1-9]|1[0-2])月?', value):
                return True
            date = QDate.fromString(value + '-01', 'yyyy-MM-dd')
            return date.isValid()
        for fmt in ('yyyy-MM-dd', 'yyyy/MM/dd', 'yyyy.MM.dd', 'yyyyMMdd'):
            if QDate.fromString(value, fmt).isValid():
                return True
        return False

    def _choose_date(self):
        if self.month_only:
            year, month = parse_year_month(self.text())
            dialog = MonthPickerDialog(year, month, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.edit.setText(dialog.month_text())
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('选择日期')
        layout = QVBoxLayout(dialog)
        calendar = QCalendarWidget()
        current = QDate.fromString(self.text(), 'yyyy-MM-dd')
        calendar.setSelectedDate(current if current.isValid() else QDate.currentDate())
        layout.addWidget(calendar)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        calendar.activated.connect(lambda _date: dialog.accept())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.edit.setText(calendar.selectedDate().toString('yyyy-MM-dd'))


def _document_entries_with_password(path, parent):
    try:
        return extract_document_entries(path)
    except PermissionError as exc:
        if str(exc) != 'PASSWORD_REQUIRED':
            raise
        password, accepted = QInputDialog.getText(
            parent, '工作簿密码', f'请输入 {os.path.basename(path)} 的打开密码：',
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            raise ValueError('已取消密码输入')
        return extract_document_entries(path, password)


def _entry_text(entry):
    if entry.get('content_type') == 'workbook_sheet':
        return '\n'.join('\t'.join(map(str, row)) for row in entry.get('rows', []))
    return str(entry.get('content', ''))


class RequirementAttachmentDialog(QDialog):
    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self._entry = copy.deepcopy(entry)
        self._hidden_rows = set(); self._hidden_columns = set(); self._highlights = []
        self.setWindowTitle(f"附件编辑器 · {entry.get('file_type', '文档')}")
        self.resize(1050, 720)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"{entry.get('file_type', '文档')} · {entry.get('name') or entry.get('source', '未命名')}"))
        content_type = entry.get('content_type', 'text_document')
        if content_type == 'workbook_sheet':
            self._setup_excel(root)
        elif content_type == 'word_document':
            self.editor = QTextEdit(); self.editor.setHtml(entry.get('document_html') or entry.get('content', '')); root.addWidget(self.editor, 1)
            export_btn = QPushButton('导出 DOCX'); export_btn.clicked.connect(self._export_word); root.addWidget(export_btn)
        else:
            self.editor = QPlainTextEdit(entry.get('content', '')); root.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存附件修改')
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    @staticmethod
    def _column_name(index):
        value, result = index + 1, ''
        while value:
            value, remainder = divmod(value - 1, 26); result = chr(65 + remainder) + result
        return result

    def _setup_excel(self, root):
        search_row = QHBoxLayout(); search_row.addWidget(QLabel('实时定位'))
        self.search = QLineEdit(); self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._filter_excel)
        search_row.addWidget(self.search, 1); root.addLayout(search_row)
        rows = self._entry.get('rows', []); columns = self._entry.get('column_count') or max((len(row) for row in rows), default=0)
        self.table = QTableWidget(len(rows), columns); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setHorizontalHeaderLabels([self._column_name(index) for index in range(columns)])
        self.table.setVerticalHeaderLabels([str(index + 1) for index in range(len(rows))])
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                if value:
                    self.table.setItem(row_index, column_index, QTableWidgetItem(str(value)))
        for index, width in enumerate(self._entry.get('column_widths', [])):
            self.table.setColumnWidth(index, min(max(int(float(width) * 7 + 12), 48), 420))
        self.table.cellDoubleClicked.connect(self._copy_cell); root.addWidget(self.table, 1)
        row1 = QHBoxLayout()
        actions = (
            ('修改单元格', self._edit_cell), ('复制整行', self._copy_row),
            ('复制当前展示', self._copy_visible), ('复制整表', self._copy_all),
        )
        for label, slot in actions:
            button = QPushButton(label); button.clicked.connect(slot); row1.addWidget(button)
        row1.addStretch(); root.addLayout(row1)
        row2 = QHBoxLayout()
        actions = (
            ('隐藏选中行', self._hide_rows), ('隐藏当前列', self._hide_column), ('恢复全部行列', self._restore),
            ('导出当前展示', lambda: self._export_excel(True)), ('导出整表', lambda: self._export_excel(False)),
        )
        for label, slot in actions:
            button = QPushButton(label); button.clicked.connect(slot); row2.addWidget(button)
        row2.addStretch(); root.addLayout(row2)

    def _table_text(self, rows, columns):
        return '\n'.join('\t'.join(self.table.item(r, c).text() if self.table.item(r, c) else '' for c in columns) for r in rows)

    def _copy_cell(self, row, column):
        item = self.table.item(row, column)
        if item: QApplication.clipboard().setText(item.text())

    def _copy_row(self):
        row = self.table.currentRow()
        if row >= 0: QApplication.clipboard().setText(self._table_text([row], range(self.table.columnCount())))

    def _copy_visible(self):
        rows = [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]
        columns = [c for c in range(self.table.columnCount()) if not self.table.isColumnHidden(c)]
        QApplication.clipboard().setText(self._table_text(rows, columns))

    def _copy_all(self):
        QApplication.clipboard().setText(self._table_text(range(self.table.rowCount()), range(self.table.columnCount())))

    def _edit_cell(self):
        row, column = self.table.currentRow(), self.table.currentColumn()
        if row < 0 or column < 0: return
        item = self.table.item(row, column); old = item.text() if item else ''
        value, accepted = QInputDialog.getMultiLineText(self, '修改单元格', f'{self._column_name(column)}{row + 1}：', old)
        if accepted:
            if item is None: item = QTableWidgetItem(); self.table.setItem(row, column, item)
            item.setText(value)

    def _hide_rows(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not rows and self.table.currentRow() >= 0: rows = {self.table.currentRow()}
        self._hidden_rows.update(rows); self._filter_excel()

    def _hide_column(self):
        if self.table.currentColumn() >= 0:
            self._hidden_columns.add(self.table.currentColumn()); self.table.setColumnHidden(self.table.currentColumn(), True)

    def _restore(self):
        self._hidden_rows.clear(); self._hidden_columns.clear()
        for column in range(self.table.columnCount()): self.table.setColumnHidden(column, False)
        self._filter_excel()

    def _filter_excel(self):
        for item, brush in self._highlights: item.setBackground(brush)
        self._highlights = []; terms = [term.casefold() for term in self.search.text().split() if term]
        first = None
        for row in range(self.table.rowCount()):
            text = '\t'.join(self.table.item(row, c).text() if self.table.item(row, c) else '' for c in range(self.table.columnCount())).casefold()
            match = not terms or all(term in text for term in terms)
            self.table.setRowHidden(row, row in self._hidden_rows or not match)
            if terms and match:
                for column in range(self.table.columnCount()):
                    item = self.table.item(row, column)
                    if item and any(term in item.text().casefold() for term in terms):
                        self._highlights.append((item, QBrush(item.background()))); item.setBackground(QColor('#FFF19C')); first = first or item
        if first:
            self.table.setCurrentItem(first); self.table.scrollToItem(first, QAbstractItemView.ScrollHint.PositionAtCenter); self.table.clearSelection()

    def _sync_excel(self):
        self._entry['rows'] = [[self.table.item(r, c).text() if self.table.item(r, c) else '' for c in range(self.table.columnCount())] for r in range(self.table.rowCount())]
        self._entry['row_count'] = self.table.rowCount(); self._entry['column_count'] = self.table.columnCount()

    def _export_excel(self, visible):
        self._sync_excel(); path, _ = QFileDialog.getSaveFileName(self, '导出 Excel', '', 'Excel 工作簿 (*.xlsx)')
        if not path: return
        if not path.lower().endswith('.xlsx'): path += '.xlsx'
        rows = [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)] if visible else None
        columns = [c for c in range(self.table.columnCount()) if not self.table.isColumnHidden(c)] if visible else None
        export_workbook_entry(self._entry, path, rows, columns)

    def _export_word(self):
        entry = copy.deepcopy(self._entry); entry['content'] = self.editor.toPlainText(); entry['document_html'] = self.editor.toHtml()
        path, _ = QFileDialog.getSaveFileName(self, '导出 Word', '', 'Word 文档 (*.docx)')
        if path:
            if not path.lower().endswith('.docx'): path += '.docx'
            export_word_entry(entry, path)

    def _accept(self):
        if self._entry.get('content_type') == 'workbook_sheet':
            self._sync_excel()
        else:
            self._entry['content'] = self.editor.toPlainText()
            if self._entry.get('content_type') == 'word_document': self._entry['document_html'] = self.editor.toHtml()
        self._entry['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds'); self.accept()

    def entry(self):
        return self._entry


class SvnWorker(QThread):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, function, *arguments, parent=None):
        super().__init__(parent)
        self.function = function
        self.arguments = arguments

    def run(self):
        try:
            self.result_ready.emit(self.function(*self.arguments))
        except Exception as exc:
            self.failed.emit(str(exc))


class SvnCheckoutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('检出需求代码')
        self.resize(720, 380)
        root = QVBoxLayout(self)
        note = QLabel('填写 SVN 地址，选择类型与上线月份后检出到本机。使用本机已缓存认证，不保存密码。')
        note.setObjectName('ops-safety-note'); note.setWordWrap(True); root.addWidget(note)
        form = QFormLayout()
        self.url_edit = QLineEdit(); self.url_edit.setPlaceholderText('svn://... 或 https://...')
        size_line(self.url_edit, 'path')
        self.title_edit = QLineEdit(); self.title_edit.setPlaceholderText('可留空，默认取路径最后一级')
        size_line(self.title_edit, 'std')
        self.kind_combo = QComboBox(); self.kind_combo.addItems(('需求', 'BUG'))
        size_combo(self.kind_combo, 'sm')
        self.month_enabled = QCheckBox('按上线月份归档'); self.month_enabled.setChecked(True)
        month_row = QHBoxLayout(); month_row.setSpacing(8); month_row.addWidget(self.month_enabled)
        # 只选年月，不用带「日」的日历
        self.month_edit = MonthSelect()
        month_row.addWidget(self.month_edit); month_row.addStretch()
        folder_row = QHBoxLayout()
        self.root_edit = QLineEdit(SVN_WORKSPACE_DIR); size_line(self.root_edit, 'path'); folder_row.addWidget(self.root_edit, 1)
        browse = QPushButton('浏览'); size_compact_button(browse)
        browse.clicked.connect(self._browse); folder_row.addWidget(browse)
        form.addRow('代码地址', self.url_edit); form.addRow('显示名称', self.title_edit)
        form.addRow('记录类型', self.kind_combo); form.addRow('上线月份', month_row); form.addRow('本机目录', folder_row)
        root.addLayout(form); root.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始检出')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self._accept_checked); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.kind_combo.currentTextChanged.connect(lambda value: self.month_enabled.setChecked(value != 'BUG'))

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, '选择本机存放目录', self.root_edit.text())
        if folder: self.root_edit.setText(folder)

    def _accept_checked(self):
        try:
            validate_svn_url(self.url_edit.text())
        except ValueError as exc:
            show_warning(self, 'SVN 地址', str(exc)); return
        if not self.root_edit.text().strip():
            show_warning(self, '本地工作区', '请选择本地工作区目录。'); return
        self.accept()

    def values(self):
        url = validate_svn_url(self.url_edit.text())
        title = self.title_edit.text().strip() or safe_folder_name(url.rsplit('/', 1)[-1])
        month = self.month_edit.month_text() if self.month_enabled.isChecked() else ''
        kind = self.kind_combo.currentText()
        group = month or 'BUG'
        target = os.path.join(self.root_edit.text().strip(), group, kind, safe_folder_name(title))
        return {'url': url, 'title': title, 'record_kind': kind, 'online_month': month, 'target': target}


class RequirementDialog(QDialog):
    def __init__(self, requirement=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('编辑需求' if requirement else '新增需求')
        self.resize(850, 760)
        base = requirement or {}
        self._sql_parts = [dict(item) for item in base.get('sql_parts', [])]
        self._source_files = [dict(item) for item in base.get('source_files', [])]

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)

        form = QGridLayout()
        self.code_edit = QLineEdit(base.get('code', ''))
        size_line(self.code_edit, 'std')
        self.title_edit = QLineEdit(base.get('title', ''))
        size_line(self.title_edit, 'std')
        self.kind_combo = QComboBox(); self.kind_combo.addItems(('需求', 'BUG')); size_combo(self.kind_combo, 'sm')
        self.category_combo = QComboBox(); self.category_combo.addItems(CATEGORIES); size_combo(self.category_combo, 'md')
        self.status_combo = QComboBox(); self.status_combo.addItems(STATUSES); size_combo(self.status_combo, 'sm')
        self.priority_combo = QComboBox(); self.priority_combo.addItems(PRIORITIES); size_combo(self.priority_combo, 'sm')
        self.system_edit = QComboBox()
        size_combo(self.system_edit, 'md')
        self.system_edit.addItem('请选择系统', '')
        for system in load_systems():
            self.system_edit.addItem(system['name'], system['name'])
        # 打开对话框时对空字段做一次自动推断（不覆盖已有值）
        inferred = apply_auto_inference(dict(base), systems=load_systems(), only_empty=True)
        current_system = inferred.get('system', '') or base.get('system', '')
        system_index = self.system_edit.findData(current_system)
        if current_system and system_index < 0:
            self.system_edit.addItem(f'{current_system}（旧配置）', current_system)
            system_index = self.system_edit.count() - 1
        self.system_edit.setCurrentIndex(max(0, system_index))
        self.owner_edit = QLineEdit(base.get('owner', ''))
        size_line(self.owner_edit, 'std')
        self.svn_url_edit = QLineEdit(base.get('svn_url', ''))
        size_line(self.svn_url_edit, 'path')
        self.svn_url_edit.setPlaceholderText('可留空；有开发分支时填写，例如 svn://服务器/.../DEV_prpcar_20260715-REQ-...')
        self.local_path_edit = QLineEdit(base.get('local_path', ''))
        size_line(self.local_path_edit, 'path')
        self.local_path_edit.setPlaceholderText('可绑定本机已有的 SVN 工作副本或需求资料目录')
        bind_folder_btn = QPushButton('绑定目录'); size_compact_button(bind_folder_btn); bind_folder_btn.clicked.connect(self._bind_local_folder)
        local_path_row = QHBoxLayout(); local_path_row.addWidget(self.local_path_edit, 1); local_path_row.addWidget(bind_folder_btn)
        self.planned_date = DateInput(inferred.get('planned_online_date') or base.get('planned_online_date', ''))
        self.actual_date = DateInput(base.get('actual_online_date', ''))
        self.online_month = DateInput(inferred.get('online_month') or base.get('online_month', ''), month_only=True)
        for combo, value in (
            (self.kind_combo, inferred.get('record_kind') or base.get('record_kind', '需求')),
            (self.category_combo, inferred.get('category') or base.get('category', '功能需求')),
            (self.status_combo, base.get('status', '待分析')),
            (self.priority_combo, base.get('priority', '普通')),
        ):
            combo.setCurrentIndex(max(0, combo.findText(value)))
        form.addWidget(QLabel('需求编号'), 0, 0); form.addWidget(self.code_edit, 0, 1)
        form.addWidget(QLabel('需求标题'), 0, 2); form.addWidget(self.title_edit, 0, 3)
        form.addWidget(QLabel('类型'), 1, 0); form.addWidget(self.kind_combo, 1, 1)
        form.addWidget(QLabel('自动分类'), 1, 2); form.addWidget(self.category_combo, 1, 3)
        form.addWidget(QLabel('当前状态'), 2, 0); form.addWidget(self.status_combo, 2, 1)
        form.addWidget(QLabel('优先级'), 2, 2); form.addWidget(self.priority_combo, 2, 3)
        form.addWidget(QLabel('所属系统'), 3, 0); form.addWidget(self.system_edit, 3, 1)
        form.addWidget(QLabel('负责人'), 3, 2); form.addWidget(self.owner_edit, 3, 3)
        form.addWidget(QLabel('开发分支 SVN 地址'), 4, 0); form.addWidget(self.svn_url_edit, 4, 1, 1, 3)
        form.addWidget(QLabel('绑定本地目录'), 5, 0); form.addLayout(local_path_row, 5, 1, 1, 3)
        form.addWidget(QLabel('上线月份'), 6, 0); form.addWidget(self.online_month, 6, 1)
        form.addWidget(QLabel('计划上线'), 6, 2); form.addWidget(self.planned_date, 6, 3)
        form.addWidget(QLabel('实际上线'), 7, 2); form.addWidget(self.actual_date, 7, 3)
        layout.addLayout(form)

        flag_card = QFrame()
        flag_card.setObjectName('flag-check-card')
        flag_wrap = QVBoxLayout(flag_card)
        flag_wrap.setContentsMargins(12, 10, 12, 10)
        flag_wrap.setSpacing(8)
        flag_title = QLabel('升级标记（勾选后可在右侧点击切换完成状态）')
        flag_title.setObjectName('section-title')
        flag_wrap.addWidget(flag_title)
        flags = QGridLayout()
        flags.setHorizontalSpacing(14)
        flags.setVerticalSpacing(8)
        self.has_sql = QCheckBox('包含 SQL')
        self.peripheral = QCheckBox('需通知周边系统升级')
        self.temporary = QCheckBox('临时升级')
        self.interface_update = QCheckBox('需整理接口文档')
        self.has_sql.setChecked(bool(inferred.get('has_sql') or base.get('has_sql') or self._sql_parts))
        self.peripheral.setChecked(bool(inferred.get('needs_peripheral_upgrade') or base.get('needs_peripheral_upgrade')))
        self.temporary.setChecked(bool(inferred.get('temporary_upgrade') or base.get('temporary_upgrade')))
        self.interface_update.setChecked(bool(inferred.get('needs_interface_update') or base.get('needs_interface_update')))
        for index, widget in enumerate((self.has_sql, self.peripheral, self.temporary, self.interface_update)):
            widget.setObjectName('flag-check')
            widget.setMinimumHeight(28)
            flags.addWidget(widget, index // 2, index % 2)
        flag_wrap.addLayout(flags)
        layout.addWidget(flag_card)

        description_top = QHBoxLayout()
        description_top.addWidget(QLabel('需求说明 / 可直接粘贴全文'))
        description_top.addStretch()
        classify_btn = QPushButton('按内容重新分类')
        classify_btn.clicked.connect(self._classify)
        description_top.addWidget(classify_btn)
        layout.addLayout(description_top)
        self.description_edit = QPlainTextEdit(base.get('description', ''))
        self.description_edit.setMinimumHeight(150)
        layout.addWidget(self.description_edit)

        source_top = QHBoxLayout()
        source_top.addWidget(QLabel('需求相关文档'))
        source_top.addStretch()
        source_btn = QPushButton('上传需求文档')
        source_btn.clicked.connect(self._load_documents)
        source_top.addWidget(source_btn)
        source_edit_btn = QPushButton('编辑选中附件'); source_edit_btn.clicked.connect(self._edit_source_part)
        source_top.addWidget(source_edit_btn)
        layout.addLayout(source_top)
        self.source_list = QListWidget(); self.source_list.setMaximumHeight(95)
        self.source_list.itemDoubleClicked.connect(lambda _item: self._edit_source_part())
        layout.addWidget(self.source_list)

        sql_top = QHBoxLayout()
        sql_top.addWidget(QLabel('需求相关 SQL'))
        sql_top.addStretch()
        load_btn = QPushButton('上传多个 SQL')
        load_btn.clicked.connect(self._load_sql)
        sql_top.addWidget(load_btn)
        layout.addLayout(sql_top)
        self.sql_list = QListWidget(); self.sql_list.setMaximumHeight(105)
        layout.addWidget(self.sql_list)
        self.sql_paste = QPlainTextEdit()
        self.sql_paste.setPlaceholderText('也可以直接粘贴需求 SQL；保存后可一键发送到 SQL 整理和接口 DOCX。')
        self.sql_paste.setMaximumHeight(95)
        layout.addWidget(self.sql_paste)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存需求')
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_lists()

    def _bind_local_folder(self):
        path = QFileDialog.getExistingDirectory(self, '绑定需求的 SVN 工作副本或资料目录', self.local_path_edit.text())
        if not path:
            return
        self.local_path_edit.setText(path)
        if os.path.isdir(os.path.join(path, '.svn')):
            try:
                info = working_copy_info(path)
                if info.get('svn_url'):
                    self.svn_url_edit.setText(info['svn_url'])
            except SvnError as exc:
                show_warning(self, '绑定 SVN 目录', f'目录已绑定，但 SVN 信息读取失败：\n{exc}')

    def _load_documents(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择需求相关文档', '',
            '需求文档 (*.docx *.xlsx *.txt *.md *.json *.xml *.yaml *.yml *.csv)'
        )
        for path in paths:
            try:
                entries = _document_entries_with_password(path, self)
                for entry in entries:
                    entry['name'] = entry.get('source') or os.path.basename(path)
                    self._source_files.append(entry)
                content = '\n'.join(_entry_text(entry) for entry in entries)
                if not self.description_edit.toPlainText().strip():
                    self.description_edit.setPlainText(content)
            except (OSError, ValueError) as exc:
                show_warning(self, '需求文档', f'{os.path.basename(path)} 读取失败：{exc}')
        self._refresh_lists()
        self._classify()

    def _load_sql(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '选择需求 SQL', '', 'SQL 文件 (*.sql *.txt)')
        for path in paths:
            try:
                self._sql_parts.append({'name': os.path.basename(path), 'content': read_text_file(path)})
            except OSError as exc:
                show_warning(self, '需求 SQL', f'{os.path.basename(path)} 读取失败：{exc}')
        if paths:
            self.has_sql.setChecked(True)
        self._refresh_lists()

    def _refresh_lists(self):
        self.source_list.clear()
        for part in self._source_files:
            file_type = part.get('file_type') or '文本'
            size = f"{part.get('row_count', 0)} 行 × {part.get('column_count', 0)} 列" if part.get('content_type') == 'workbook_sheet' else f"{len(part.get('content', ''))} 字符"
            self.source_list.addItem(f"[{file_type}] {part.get('name')} · {size}")
        self.sql_list.clear()
        for part in self._sql_parts:
            item = QListWidgetItem(f"{part.get('name')} · {len(part.get('content', ''))} 字符")
            item.setToolTip(part.get('content', '')[:1000])
            self.sql_list.addItem(item)

    def _edit_source_part(self):
        row = self.source_list.currentRow()
        if row < 0 or row >= len(self._source_files):
            show_info(self, '附件编辑器', '请先选择一个附件。'); return
        dialog = RequirementAttachmentDialog(self._source_files[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._source_files[row] = dialog.entry(); self._refresh_lists()

    def _classify(self):
        content = self.description_edit.toPlainText()
        seed = {
            'title': self.title_edit.text(),
            'code': self.code_edit.text(),
            'description': content,
            'svn_url': self.svn_url_edit.text(),
            'local_path': self.local_path_edit.text(),
            'system': self.system_edit.currentData() or '',
            'sql_parts': list(self._sql_parts),
            'source_files': list(self._source_files),
            'has_sql': self.has_sql.isChecked(),
            'needs_peripheral_upgrade': self.peripheral.isChecked(),
            'temporary_upgrade': self.temporary.isChecked(),
            'needs_interface_update': self.interface_update.isChecked(),
            'online_month': self.online_month.text(),
            'category': self.category_combo.currentText(),
        }
        inferred = apply_auto_inference(seed, systems=load_systems(), only_empty=True)
        self.category_combo.setCurrentIndex(max(0, self.category_combo.findText(
            inferred.get('category') or classify_requirement(content)
        )))
        if not self.system_edit.currentData() and inferred.get('system'):
            index = self.system_edit.findData(inferred['system'])
            if index >= 0:
                self.system_edit.setCurrentIndex(index)
        if not self.online_month.text().strip() and inferred.get('online_month'):
            self.online_month.edit.setText(inferred['online_month'])
        if inferred.get('has_sql') or self._sql_parts:
            self.has_sql.setChecked(True)
        if inferred.get('needs_peripheral_upgrade'):
            self.peripheral.setChecked(True)
        if inferred.get('temporary_upgrade'):
            self.temporary.setChecked(True)
        if inferred.get('needs_interface_update'):
            self.interface_update.setChecked(True)

    def _normalize_month_text(self, value):
        text = str(value or '').strip()
        match = re.fullmatch(r'(20\d{2})[-/.](0?[1-9]|1[0-2])', text)
        if match:
            return f'{match.group(1)}-{int(match.group(2)):02d}'
        match = re.fullmatch(r'(20\d{2})年(0?[1-9]|1[0-2])月?', text)
        if match:
            return f'{match.group(1)}-{int(match.group(2)):02d}'
        return text

    def _normalize_date_text(self, value):
        text = str(value or '').strip()
        if not text:
            return ''
        for fmt in ('yyyy-MM-dd', 'yyyy/MM/dd', 'yyyy.MM.dd', 'yyyyMMdd'):
            date = QDate.fromString(text, fmt)
            if date.isValid():
                return date.toString('yyyy-MM-dd')
        return text

    def _accept_checked(self):
        if not self.title_edit.text().strip():
            show_warning(self, '需求工作台', '请填写需求标题。')
            return
        local_path = self.local_path_edit.text().strip()
        # 目录失效不应阻止保存台账字段（标题/勾选/日期等）
        if local_path and not os.path.isdir(local_path):
            if not confirm_action(
                self, '绑定本地目录',
                f'本地目录不存在：\n{local_path}\n\n是否清空目录绑定并继续保存其他字段？',
                confirm_text='清空并继续',
                danger=False,
            ):
                self.local_path_edit.setFocus()
                return
            self.local_path_edit.clear()
        for field, label in ((self.online_month, '上线月份'), (self.planned_date, '计划上线'), (self.actual_date, '实际上线')):
            if not field.is_valid():
                tip = '选月份' if getattr(field, 'month_only', False) else '选日期'
                show_warning(self, label, f'{label}格式不正确，请按输入框提示手动录入，或点击“{tip}”。')
                field.edit.setFocus(); return
        pasted = self.sql_paste.toPlainText().strip()
        if pasted:
            self._sql_parts.append({'name': '直接粘贴.sql', 'content': pasted})
            self.has_sql.setChecked(True)
        self.accept()

    def values(self):
        online_month = self._normalize_month_text(self.online_month.text())
        planned_date = self._normalize_date_text(self.planned_date.text()) or month_end_date(online_month)
        actual_date = self._normalize_date_text(self.actual_date.text())
        local_path = self.local_path_edit.text().strip()
        return {
            'code': self.code_edit.text().strip(), 'title': self.title_edit.text().strip(),
            'record_kind': self.kind_combo.currentText(),
            'description': self.description_edit.toPlainText().strip(),
            'category': self.category_combo.currentText(), 'status': self.status_combo.currentText(),
            'priority': self.priority_combo.currentText(), 'system': self.system_edit.currentData() or '',
            'owner': self.owner_edit.text().strip(),
            'svn_url': self.svn_url_edit.text().strip(),
            'local_path': local_path,
            'workspace_kind': (
                'folder' if local_path and not os.path.isdir(os.path.join(local_path, '.svn'))
                else 'svn'
            ),
            'planned_online_date': planned_date,
            'actual_online_date': actual_date,
            'online_month': online_month,
            'has_sql': bool(self.has_sql.isChecked() or self._sql_parts),
            'needs_peripheral_upgrade': bool(self.peripheral.isChecked()),
            'temporary_upgrade': bool(self.temporary.isChecked()),
            'needs_interface_update': bool(self.interface_update.isChecked()),
            'sql_parts': list(self._sql_parts),
            'source_files': list(self._source_files),
        }


class RequirementPanel(QWidget):
    send_to_sql = pyqtSignal(str, str)
    send_to_docx = pyqtSignal(str, str)
    add_to_daily = pyqtSignal(dict)
    open_system_config = pyqtSignal()
    open_release_prep = pyqtSignal(object)

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._systems = load_systems()
        self._requirements = load_requirements()
        self._current = None
        self._active_worker = None
        self._pending_file_refresh = False
        self._task_failed = False
        self._task_shows_loading = False
        self._setup_ui(); self.set_language(language); self._refresh()

    def _setup_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        # 顶栏操作区
        toolbar_card = QFrame()
        toolbar_card.setObjectName('req-toolbar-card')
        toolbar_layout = QVBoxLayout(toolbar_card)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.scan_btn = QPushButton('扫描目录'); self.scan_btn.clicked.connect(self._scan_folder)
        self.checkout_btn = QPushButton('检出代码'); self.checkout_btn.clicked.connect(self._checkout_svn)
        self.update_all_btn = QPushButton('更新全部'); self.update_all_btn.clicked.connect(self._update_all)
        self.system_config_btn = QPushButton('系统配置'); self.system_config_btn.clicked.connect(self.open_system_config.emit)
        self.bug_btn = QPushButton('登记缺陷'); self.bug_btn.clicked.connect(self._paste_bug)
        self.import_btn = QPushButton('导入资料'); self.import_btn.clicked.connect(self._import_requirement)
        self.add_btn = QPushButton('新建需求'); self.add_btn.setObjectName('primary-btn'); self.add_btn.clicked.connect(self._add_requirement)
        for button in (self.scan_btn, self.checkout_btn, self.update_all_btn, self.bug_btn, self.import_btn, self.system_config_btn, self.add_btn):
            button.setProperty('compactAction', True)
            button.setMinimumHeight(32)
            header.addWidget(button)
        header.addStretch()
        toolbar_layout.addLayout(header)
        self.svn_activity = QLabel('流程：扫描/检出 → 左侧选需求 → 右侧看详情与文件。双击文件夹展开，双击文件打开；点击标记切换红/绿点。')
        self.svn_activity.setObjectName('path-note'); self.svn_activity.setWordWrap(True)
        toolbar_layout.addWidget(self.svn_activity)
        root.addWidget(toolbar_card)
        self.loading = AuroraProgress(self)

        filter_card = QFrame()
        filter_card.setObjectName('req-filter-card')
        filters = QHBoxLayout(filter_card)
        filters.setContentsMargins(12, 9, 12, 9)
        filters.setSpacing(8)
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText('全文搜索：编号、标题、说明、SQL、系统……')
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True); self.search_edit.textChanged.connect(self._refresh)
        self.status_filter = QComboBox(); self.status_filter.addItems(('全部状态',) + STATUSES); self.status_filter.currentIndexChanged.connect(self._refresh)
        self.kind_filter = QComboBox(); self.kind_filter.addItems(('全部类型', '需求', 'BUG')); self.kind_filter.currentIndexChanged.connect(self._refresh)
        self.system_filter = QComboBox(); self._fill_system_filter(); self.system_filter.currentIndexChanged.connect(self._refresh)
        size_combo(self.system_filter, 'md'); size_combo(self.kind_filter, 'sm'); size_combo(self.status_filter, 'sm')
        filters.addWidget(self.search_edit, 1); filters.addWidget(self.system_filter); filters.addWidget(self.kind_filter); filters.addWidget(self.status_filter)
        root.addWidget(filter_card)

        self.detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.detail_splitter.setObjectName('requirement-splitter')
        self.detail_splitter.setHandleWidth(10)
        self.detail_splitter.setChildrenCollapsible(False)
        left = QFrame(); left.setObjectName('req-tree-card'); left_layout = QVBoxLayout(left)
        left.setMinimumWidth(200)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(7)
        tree_head = QHBoxLayout()
        tree_head.setSpacing(6)
        tree_title = QLabel('需求列表')
        tree_title.setObjectName('zone-title')
        tree_head.addWidget(tree_title)
        tree_head.addStretch()
        left_layout.addLayout(tree_head)
        # 两行工具条：窄侧栏时也能看到「删除」，避免被展开/折叠按钮挤掉
        tree_tools = QVBoxLayout()
        tree_tools.setContentsMargins(0, 0, 0, 0)
        tree_tools.setSpacing(4)
        tree_row1 = QHBoxLayout()
        tree_row1.setSpacing(6)
        self.select_all_check = QCheckBox('全选')
        self.select_all_check.stateChanged.connect(self._select_all_requirements)
        self.batch_delete_btn = QPushButton('删除')
        self.batch_delete_btn.setObjectName('ops-delete-custom')
        self.batch_delete_btn.setProperty('compactAction', True)
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.setMinimumWidth(64)
        self.batch_delete_btn.setToolTip('删除左侧树中当前选中的需求/BUG（支持全选后批量删除；也可按 Delete）')
        self.batch_delete_btn.clicked.connect(self._delete_requirement)
        tree_row1.addWidget(self.select_all_check)
        tree_row1.addWidget(self.batch_delete_btn)
        tree_row1.addStretch()
        tree_row2 = QHBoxLayout()
        tree_row2.setSpacing(6)
        self.expand_tree_btn = QPushButton('全部展开')
        self.expand_tree_btn.setProperty('compactAction', True)
        self.expand_tree_btn.clicked.connect(lambda: self.requirement_list.expandAll())
        self.collapse_tree_btn = QPushButton('全部折叠')
        self.collapse_tree_btn.setProperty('compactAction', True)
        self.collapse_tree_btn.clicked.connect(lambda: self.requirement_list.collapseAll())
        tree_row2.addWidget(self.expand_tree_btn)
        tree_row2.addWidget(self.collapse_tree_btn)
        tree_row2.addStretch()
        tree_tools.addLayout(tree_row1)
        tree_tools.addLayout(tree_row2)
        left_layout.addLayout(tree_tools)
        self.requirement_list = RequirementTree(); self.requirement_list.setObjectName('requirement-tree')
        self.requirement_list.setHeaderHidden(True)
        self.requirement_list.setColumnCount(2)
        self.requirement_list.setRootIsDecorated(True)
        self.requirement_list.setItemsExpandable(True)
        self.requirement_list.setExpandsOnDoubleClick(True)
        self.requirement_list.setIndentation(18)
        self.requirement_list.setWordWrap(True)
        self.requirement_list.setUniformRowHeights(False)
        self.requirement_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.requirement_list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.requirement_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.requirement_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree_header = self.requirement_list.header()
        tree_header.setStretchLastSection(True)
        tree_header.setMinimumSectionSize(72)
        tree_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.requirement_list.setColumnWidth(0, 200)
        self.requirement_list.setDragEnabled(True); self.requirement_list.setAcceptDrops(True)
        self.requirement_list.setDropIndicatorShown(True); self.requirement_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.requirement_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.requirement_list.customContextMenuRequested.connect(self._show_requirement_menu)
        self.requirement_list.requirementsMoved.connect(self._move_requirements)
        self.requirement_list.currentItemChanged.connect(self._show_requirement)
        self.requirement_list.itemSelectionChanged.connect(self._on_requirement_selection_changed)
        self.requirement_list.itemClicked.connect(self._on_requirement_item_clicked)
        # Delete / Backspace 批量删除选中项
        self.requirement_list.installEventFilter(self)
        left_layout.addWidget(self.requirement_list, 1)
        self.detail_splitter.addWidget(left)

        right = QWidget(); detail = QVBoxLayout(right)
        right.setMinimumWidth(360)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(8)

        # 需求信息：只展示 类型 / 状态 / 系统 / 上线时间
        self.detail_card = QFrame(); self.detail_card.setObjectName('detail-summary-card')
        self.detail_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        card = QVBoxLayout(self.detail_card)
        card.setContentsMargins(12, 10, 12, 10)
        card.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(8)
        detail_zone = QLabel('需求详情')
        detail_zone.setObjectName('zone-title')
        head.addWidget(detail_zone)
        self.detail_title = QLabel('请选择需求')
        self.detail_title.setObjectName('section-title')
        self.detail_title.setWordWrap(False)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head.addWidget(self.detail_title, 1)
        self.edit_btn = QPushButton('编辑')
        self.edit_btn.setProperty('compactAction', True)
        self.edit_btn.setMaximumHeight(28)
        self.edit_btn.clicked.connect(self._edit_requirement)
        head.addWidget(self.edit_btn)
        card.addLayout(head)

        self.detail_grid = QGridLayout()
        self.detail_grid.setContentsMargins(0, 0, 0, 0)
        self.detail_grid.setHorizontalSpacing(16)
        self.detail_grid.setVerticalSpacing(6)
        self._detail_fields = {}
        for index, (key, label) in enumerate((
            ('kind', '类型'),
            ('status', '当前状态'),
            ('system', '所属系统'),
            ('online', '上线时间'),
        )):
            row, col = divmod(index, 2)
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(2)
            caption = QLabel(label)
            caption.setObjectName('field-caption')
            value = QLabel('—')
            value.setObjectName('field-value')
            value.setWordWrap(False)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cell.addWidget(caption)
            cell.addWidget(value)
            self.detail_grid.addLayout(cell, row, col)
            self._detail_fields[key] = value
        card.addLayout(self.detail_grid)

        # 兼容旧引用
        self.detail_tags = QLabel(); self.detail_tags.hide()
        self.desc_preview = QLabel(); self.desc_preview.hide()
        self.flags = QLabel(); self.flags.hide()
        self.meta = QLabel(); self.meta.hide()

        # 任务标记：可点击短 chip
        self.flag_chips = QWidget()
        self.flag_chips_layout = QHBoxLayout(self.flag_chips)
        self.flag_chips_layout.setContentsMargins(0, 2, 0, 0)
        self.flag_chips_layout.setSpacing(6)
        self._flag_buttons = {}
        for key, short, full in FLAG_DEFS:
            btn = QPushButton(short)
            btn.setObjectName('flag-chip')
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMaximumHeight(26)
            btn.setToolTip(f'{full} · 点击切换红/绿')
            btn.clicked.connect(lambda _checked=False, flag_key=key: self._on_flag_chip_clicked(flag_key))
            self._flag_buttons[key] = btn
            self.flag_chips_layout.addWidget(btn)
            btn.hide()
        self.flag_chips_layout.addStretch()
        card.addWidget(self.flag_chips)
        detail.addWidget(self.detail_card, 0)

        self.file_sql_splitter = QSplitter(Qt.Orientation.Vertical)
        self.file_sql_splitter.setObjectName('requirement-content-splitter')
        self.file_sql_splitter.setHandleWidth(8)
        self.file_sql_splitter.setChildrenCollapsible(False)

        # 主区域：文件浏览
        file_section = QFrame()
        file_section.setObjectName('req-file-card')
        file_layout = QVBoxLayout(file_section)
        file_layout.setContentsMargins(12, 10, 12, 10)
        file_layout.setSpacing(7)
        file_head = QHBoxLayout(); file_head.setSpacing(6)
        file_title = QLabel('文件浏览'); file_title.setObjectName('zone-title')
        file_head.addWidget(file_title)
        self.svn_meta = QLabel()
        self.svn_meta.setObjectName('svn-workspace-meta')
        self.svn_meta.setWordWrap(False)
        self.svn_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_head.addWidget(self.svn_meta, 1)
        file_layout.addLayout(file_head)

        self.file_tree = QTreeWidget(); self.file_tree.setObjectName('requirement-file-tree')
        self.file_tree.setHeaderLabels(('名称', '修改时间', '类型', '大小'))
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setRootIsDecorated(True)
        self.file_tree.setItemsExpandable(True)
        self.file_tree.setExpandsOnDoubleClick(False)
        self.file_tree.setIndentation(16)
        self.file_tree.setUniformRowHeights(True)
        self.file_tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.file_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.file_tree.setMinimumHeight(180)
        file_header = self.file_tree.header()
        file_header.setObjectName('requirement-file-header')
        file_header.setSectionsClickable(True)
        file_header.setHighlightSections(False)
        file_header.setSectionsMovable(True)
        file_header.setStretchLastSection(False)
        file_header.setMinimumSectionSize(56)
        file_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        file_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        file_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        file_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        file_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.file_tree.setColumnWidth(1, 145)
        self.file_tree.setColumnWidth(2, 110)
        self.file_tree.setColumnWidth(3, 90)
        for index in range(self.file_tree.columnCount()):
            self.file_tree.headerItem().setToolTip(index, '拖动标题列分隔线可调列宽 · 双击文件夹展开 · 双击文件打开 · 右键打开目录')
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._show_file_menu)
        self.file_tree.itemDoubleClicked.connect(self._open_tree_item)
        self.file_tree.currentItemChanged.connect(self._update_lock_buttons)
        file_layout.addWidget(self.file_tree, 1)

        # 文件操作：紧凑 2×4，不再撑高
        action_card = QFrame(); action_card.setObjectName('detail-action-card')
        action_card_layout = QVBoxLayout(action_card)
        action_card_layout.setContentsMargins(8, 6, 8, 6)
        action_card_layout.setSpacing(4)
        self.svn_actions = QGridLayout()
        self.svn_actions.setHorizontalSpacing(4)
        self.svn_actions.setVerticalSpacing(4)
        self.open_folder_btn = QPushButton('打开目录'); self.open_folder_btn.clicked.connect(self._open_folder)
        self.refresh_svn_btn = QPushButton('刷新列表'); self.refresh_svn_btn.clicked.connect(self._refresh_file_tree)
        self.update_current_btn = QPushButton('更新代码'); self.update_current_btn.clicked.connect(self._update_current)
        self.add_file_btn = QPushButton('添加文件'); self.add_file_btn.clicked.connect(self._add_existing_files)
        self.new_text_btn = QPushButton('新建文本'); self.new_text_btn.clicked.connect(self._add_text_file)
        self.lock_file_btn = QPushButton('锁定文件'); self.lock_file_btn.clicked.connect(self._lock_selected_file)
        self.unlock_file_btn = QPushButton('解锁文件'); self.unlock_file_btn.clicked.connect(self._unlock_selected_file)
        self.commit_btn = QPushButton('提交变更'); self.commit_btn.setObjectName('primary-btn'); self.commit_btn.clicked.connect(self._commit_svn)
        flat_buttons = (
            self.open_folder_btn, self.refresh_svn_btn, self.update_current_btn, self.add_file_btn,
            self.new_text_btn, self.lock_file_btn, self.unlock_file_btn, self.commit_btn,
        )
        for index, button in enumerate(flat_buttons):
            button.setProperty('compactAction', True)
            button.setMinimumHeight(26)
            button.setMaximumHeight(28)
            self.svn_actions.addWidget(button, index // 4, index % 4)
        for column in range(4):
            self.svn_actions.setColumnStretch(column, 1)
        action_card_layout.addLayout(self.svn_actions)
        file_layout.addWidget(action_card, 0)

        # 底部：SQL 预览 + 业务操作（默认高度较小）
        sql_section = QFrame()
        sql_section.setObjectName('req-sql-card')
        sql_section.setMinimumHeight(120)
        sql_layout = QVBoxLayout(sql_section)
        sql_layout.setContentsMargins(12, 10, 12, 10)
        sql_layout.setSpacing(7)
        sql_head = QHBoxLayout(); sql_head.setSpacing(6)
        sql_title = QLabel('关联 SQL'); sql_title.setObjectName('zone-title')
        sql_head.addWidget(sql_title)
        sql_head.addStretch()
        self.delete_btn = QPushButton('删除'); self.delete_btn.setObjectName('ops-delete-custom'); self.delete_btn.clicked.connect(self._delete_requirement)
        self.daily_btn = QPushButton('日报'); self.daily_btn.clicked.connect(self._send_daily)
        self.docx_btn = QPushButton('接口'); self.docx_btn.clicked.connect(self._send_docx)
        self.sql_btn = QPushButton('整理 SQL'); self.sql_btn.setObjectName('primary-btn'); self.sql_btn.clicked.connect(self._send_sql)
        for button in (self.delete_btn, self.daily_btn, self.docx_btn, self.sql_btn):
            button.setProperty('compactAction', True)
            button.setMinimumHeight(26)
            button.setMaximumHeight(28)
            sql_head.addWidget(button)
        sql_layout.addLayout(sql_head)
        self.sql_preview = QPlainTextEdit()
        self.sql_preview.setReadOnly(True)
        self.sql_preview.setObjectName('ops-preview')
        self.sql_preview.setMaximumBlockCount(0)
        self.sql_preview.setMinimumHeight(56)
        sql_layout.addWidget(self.sql_preview, 1)
        # 兼容旧 actions_card 引用（布局已并入 sql_head）
        self.actions_card = QFrame(); self.actions_card.hide()

        self.file_sql_splitter.addWidget(file_section)
        self.file_sql_splitter.addWidget(sql_section)
        self.file_sql_splitter.setStretchFactor(0, 4)
        self.file_sql_splitter.setStretchFactor(1, 1)
        requirement_ui = load_requirement_ui()
        sizes = requirement_ui['content_splitter_sizes']
        # 若历史配置把文件区压得过小，则回落到以文件为主
        if sizes[0] < sizes[1] * 1.5:
            sizes = [520, 140]
        self.file_sql_splitter.setSizes(sizes)
        detail.addWidget(self.file_sql_splitter, 1)
        self.detail_splitter.addWidget(right)
        self.detail_splitter.setSizes(requirement_ui['splitter_sizes'])
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(250)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self.detail_splitter.splitterMoved.connect(lambda _position, _index: self._splitter_save_timer.start())
        self.file_sql_splitter.splitterMoved.connect(lambda _position, _index: self._splitter_save_timer.start())
        root.addWidget(self.detail_splitter, 1)

    def _save_splitter_sizes(self):
        save_requirement_ui({
            'splitter_sizes': self.detail_splitter.sizes(),
            'content_splitter_sizes': self.file_sql_splitter.sizes(),
        })

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading'):
            width = min(540, max(300, self.width() - 48))
            self.loading.setFixedWidth(width)
            self.loading.move(max(24, (self.width() - width) // 2), 64)
            self.loading.raise_()

    def set_language(self, language):
        self.language = language

    def _fill_system_filter(self):
        current = self.system_filter.currentData() if hasattr(self, 'system_filter') else ''
        if hasattr(self, 'system_filter'):
            self.system_filter.clear()
        else:
            return
        self.system_filter.addItem('全部系统', '')
        for system in self._systems:
            self.system_filter.addItem(system['name'], system['name'])
        self.system_filter.setCurrentIndex(max(0, self.system_filter.findData(current)))

    def refresh_systems(self):
        self._systems = load_systems()
        self._fill_system_filter()
        self._refresh()

    def _refresh(self):
        if not hasattr(self, 'requirement_list'): return
        query = self.search_edit.text().strip().casefold()
        current_id = self._current.get('id') if self._current else None
        status = self.status_filter.currentText(); kind = self.kind_filter.currentText(); system = self.system_filter.currentData()
        self.requirement_list.clear()
        visible = []
        for requirement in self._requirements:
            if query and query not in requirement_search_text(requirement): continue
            if kind != '全部类型' and requirement.get('record_kind', '需求') != kind: continue
            if status != '全部状态' and requirement.get('status', '待分析') != status: continue
            if system and requirement.get('system', '') != system: continue
            visible.append(requirement)
        visible.sort(
            key=lambda item: (item.get('online_month', ''), item.get('source_modified_at') or item.get('updated_at', ''), item.get('title', '')),
            reverse=True,
        )
        month_counts = {}
        for requirement in visible:
            month = requirement.get('online_month') or '未分月'
            month_counts[month] = month_counts.get(month, 0) + 1
        selected_item = None
        first_item = None
        groups = {}
        for requirement in visible:
            month = requirement.get('online_month') or '未分月'
            if month not in groups:
                label = format_online_month_label(month)
                header_text = f'{label}  ·  {month_counts[month]} 项'
                header = QTreeWidgetItem()
                header.setText(0, header_text)
                header.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
                header.setData(0, GROUP_MONTH_ROLE, '' if month == '未分月' else month)
                header.setToolTip(0, f'{header_text}\n点击左侧三角可展开/折叠该月')
                header.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                    | Qt.ItemFlag.ItemIsAutoTristate
                )
                font = header.font(0); font.setBold(True); font.setPointSize(max(font.pointSize() + 1, 11)); header.setFont(0, font)
                header.setForeground(0, QColor('#1E2A44'))
                header.setBackground(0, QColor('#F0F3FA')); header.setBackground(1, QColor('#F0F3FA'))
                self.requirement_list.addTopLevelItem(header)
                # 必须先入树再跨列，否则月份标题会被挤在需求号窄列中
                header.setFirstColumnSpanned(True)
                header.setExpanded(True)
                header.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                groups[month] = header
            requirement = normalize_requirement(requirement)
            count = len(requirement.get('sql_parts', []))
            modified = requirement.get('source_modified_at') or requirement.get('updated_at', '')
            modified_text = modified[:16].replace('T', ' ') if modified else '未知'
            file_count = requirement.get('file_count', 0)
            badge_text = flag_status_text(requirement)
            item = QTreeWidgetItem(groups[month])
            item.setFirstColumnSpanned(False)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
            title = requirement.get('title') or os.path.basename(requirement.get('local_path', '').rstrip(os.sep)) or requirement.get('svn_url', '').rstrip('/').rsplit('/', 1)[-1] or '未命名'
            code = str(requirement.get('code') or '').strip() or '未编号'
            item.setText(0, code)
            item.setText(1, f"{title}\n{badge_text}  ·  {file_count}文件 · {modified_text}")
            item.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setTextAlignment(1, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setData(0, Qt.ItemDataRole.UserRole, requirement)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            code_font = item.font(0); code_font.setBold(True); item.setFont(0, code_font)
            # 搜索命中时用更醒目的颜色提示编号/标题（轻量高亮，不改树结构）
            hit = bool(query) and (
                query in code.casefold() or query in title.casefold() or query in requirement_search_text(requirement)
            )
            item.setForeground(0, QColor('#C45C12' if hit and query in code.casefold() else '#4058C8'))
            item.setForeground(1, QColor('#C45C12' if hit else '#4A5872'))
            if hit:
                title_font = item.font(1); title_font.setBold(True); item.setFont(1, title_font)
            flag_tip = []
            done = normalize_flag_done(requirement)
            for key, short, full in active_flags(requirement):
                flag_tip.append(f"{'✅' if done.get(key) else '⏳'} {full}")
            tip_flags = '\n'.join(flag_tip) if flag_tip else '无升级标记'
            item.setToolTip(0, f"{requirement.get('record_kind', '需求')}：{code}")
            item.setToolTip(1, f"{title}\n状态：{requirement.get('status', '待分析')} · SQL：{count} 个 · 文件：{file_count} 个\n{tip_flags}\n\n右侧详情卡片可点击标记切换红/绿点")
            first_item = first_item or item
            if requirement.get('id') == current_id: selected_item = item
        self.requirement_list.expandAll()
        self._fit_requirement_code_column()
        # 有搜索词时优先定位第一条匹配；无搜索时尽量保持当前选中
        pick = first_item if query else (selected_item or first_item)
        if pick:
            self.requirement_list.setCurrentItem(pick)
            parent = pick.parent()
            if parent is not None:
                parent.setExpanded(True)
            self.requirement_list.scrollToItem(pick)
        else:
            self._show_requirement(None)
        self._on_requirement_selection_changed()

    def _fit_requirement_code_column(self):
        """按当前需求号内容自适应第 0 列，避免编号被裁切。"""
        if not hasattr(self, 'requirement_list'):
            return
        self.requirement_list.resizeColumnToContents(0)
        width = self.requirement_list.columnWidth(0) + 18
        self.requirement_list.setColumnWidth(0, max(180, min(width, 360)))

    def _selected_requirements(self):
        selected = []
        seen = set()
        for item in self.requirement_list.selectedItems():
            requirement = item.data(0, Qt.ItemDataRole.UserRole)
            requirement_id = requirement.get('id') if isinstance(requirement, dict) else None
            if requirement_id and requirement_id not in seen:
                selected.append(requirement); seen.add(requirement_id)
        if not selected and self._current:
            selected.append(self._current)
        return selected

    def _select_all_requirements(self, state):
        if not hasattr(self, 'requirement_list'):
            return
        # Qt6: state 可能是 CheckState 枚举或 int
        checked = state == Qt.CheckState.Checked or state == Qt.CheckState.Checked.value
        self.requirement_list.blockSignals(True)
        first_leaf = None
        for group_index in range(self.requirement_list.topLevelItemCount()):
            group = self.requirement_list.topLevelItem(group_index)
            group.setSelected(False)
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                if checked and first_leaf is None:
                    first_leaf = child
                child.setSelected(checked)
        # 切当前项时不要默认 ClearAndSelect，否则会把全选冲掉只剩一项
        if checked and first_leaf is not None:
            self.requirement_list.setCurrentItem(
                first_leaf,
                QItemSelectionModel.SelectionFlag.Current | QItemSelectionModel.SelectionFlag.Select,
            )
            for group_index in range(self.requirement_list.topLevelItemCount()):
                group = self.requirement_list.topLevelItem(group_index)
                for child_index in range(group.childCount()):
                    group.child(child_index).setSelected(True)
        elif not checked:
            self.requirement_list.clearSelection()
        self.requirement_list.blockSignals(False)
        self._on_requirement_selection_changed()

    def _on_requirement_selection_changed(self):
        self._sync_select_all_check()
        self._update_batch_delete_button()

    def _sync_select_all_check(self):
        if not hasattr(self, 'select_all_check'):
            return
        leaves = [
            self.requirement_list.topLevelItem(group_index).child(child_index)
            for group_index in range(self.requirement_list.topLevelItemCount())
            for child_index in range(self.requirement_list.topLevelItem(group_index).childCount())
        ]
        self.select_all_check.blockSignals(True)
        self.select_all_check.setChecked(bool(leaves) and all(item.isSelected() for item in leaves))
        self.select_all_check.blockSignals(False)

    def _update_batch_delete_button(self):
        if not hasattr(self, 'batch_delete_btn'):
            return
        count = len(self._selected_requirements())
        self.batch_delete_btn.setEnabled(count > 0)
        if count <= 1:
            self.batch_delete_btn.setText('删除')
        else:
            self.batch_delete_btn.setText(f'删除({count})')

    def _show_requirement_menu(self, point):
        item = self.requirement_list.itemAt(point)
        if not item:
            return
        requirement = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if not isinstance(requirement, dict):
            toggle = menu.addAction('折叠分类' if item.isExpanded() else '展开分类')
            toggle.triggered.connect(lambda: item.setExpanded(not item.isExpanded()))
            menu.exec(self.requirement_list.viewport().mapToGlobal(point)); return
        if not item.isSelected():
            self.requirement_list.clearSelection(); item.setSelected(True)
        self.requirement_list.setCurrentItem(item)
        rename_action = menu.addAction('修改标题'); rename_action.triggered.connect(self._rename_requirement_title)
        edit_action = menu.addAction('编辑完整信息'); edit_action.triggered.connect(self._edit_requirement)
        flags = active_flags(requirement)
        if flags:
            status_menu = menu.addMenu('任务红绿点')
            done = normalize_flag_done(requirement)
            for key, short, full in flags:
                action = status_menu.addAction(
                    f"{'🟢 完成' if done.get(key) else '🔴 待办'} · {full}"
                )
                action.triggered.connect(
                    lambda _checked=False, req=requirement, flag_key=key: self._toggle_flag_done(req, flag_key)
                )
            status_menu.addSeparator()
            status_menu.addAction('全部标完成', lambda req=requirement: self._set_all_flags_done(req, True))
            status_menu.addAction('全部标待办', lambda req=requirement: self._set_all_flags_done(req, False))
        if requirement.get('local_path') and os.path.isdir(requirement['local_path']):
            open_action = menu.addAction('打开文件夹')
            open_action.triggered.connect(lambda: self._open_requirement_folder(requirement))
        menu.addSeparator()
        delete_action = menu.addAction(f"删除选中的 {len(self._selected_requirements())} 项")
        delete_action.triggered.connect(self._delete_requirement)
        menu.exec(self.requirement_list.viewport().mapToGlobal(point))

    def _on_requirement_item_clicked(self, item, column):
        """左侧树单击仅选中；颜色切换请点右侧对应标记按钮。"""
        return

    def _on_flag_chip_clicked(self, flag_key):
        """点哪个标记就切换哪个：红↔绿。"""
        if not self._current:
            return
        if not flag_is_active(self._current, flag_key):
            return
        self._toggle_flag_done(self._current, flag_key)

    def _toggle_flag_done(self, requirement, flag_key, force_done=None):
        if not isinstance(requirement, dict):
            return
        target = next((item for item in self._requirements if item.get('id') == requirement.get('id')), requirement)
        if not flag_is_active(target, flag_key):
            return
        done = normalize_flag_done(target)
        done[flag_key] = (not bool(done.get(flag_key))) if force_done is None else bool(force_done)
        target['flag_done'] = done
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(self._requirements)
        self._current = target
        self._refresh()

    def _set_all_flags_done(self, requirement, done_value):
        target = next((item for item in self._requirements if item.get('id') == requirement.get('id')), requirement)
        done = normalize_flag_done(target)
        for key, _short, _full in active_flags(target):
            done[key] = bool(done_value)
        target['flag_done'] = done
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(self._requirements)
        self._current = target
        self._refresh()

    def _rename_requirement_title(self):
        if not self._current:
            return
        title, accepted = QInputDialog.getText(self, '修改需求标题', '标题：', text=self._current.get('title', ''))
        if not accepted or not title.strip():
            return
        self._current['title'] = title.strip()
        self._current['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(self._requirements); self._refresh()

    def _move_requirements(self, requirement_ids, month):
        now = datetime.datetime.now().isoformat(timespec='seconds')
        changed = False
        for requirement in self._requirements:
            if requirement.get('id') in requirement_ids and requirement.get('online_month', '') != month:
                requirement['online_month'] = month
                if month and not requirement.get('planned_online_date'):
                    requirement['planned_online_date'] = month_end_date(month)
                requirement['updated_at'] = now; changed = True
        if changed:
            save_requirements(self._requirements); self._refresh()

    @staticmethod
    def _open_requirement_folder(requirement):
        path = requirement.get('local_path', '') if requirement else ''
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _show_requirement(self, current, _previous=None, refresh_files=True):
        self._current = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        for button in (self.delete_btn, self.edit_btn, self.daily_btn, self.docx_btn, self.sql_btn):
            button.setEnabled(bool(self._current))
        has_path = bool(self._current and self._current.get('local_path'))
        is_svn = bool(has_path and self._current.get('workspace_kind', 'svn') == 'svn')
        self.open_folder_btn.setEnabled(has_path)
        self.refresh_svn_btn.setEnabled(has_path)
        for button in (self.update_current_btn, self.add_file_btn, self.new_text_btn, self.commit_btn):
            button.setEnabled(is_svn)
        self._update_lock_buttons()
        if not self._current:
            self.detail_title.setText('请选择左侧需求')
            self.detail_title.setToolTip('')
            for value in self._detail_fields.values():
                value.setText('—')
                value.setToolTip('')
            for btn in self._flag_buttons.values():
                btn.setVisible(False)
            self.sql_preview.clear()
            self.svn_meta.clear()
            self.file_tree.clear()
            return

        requirement = normalize_requirement(self._current)
        self._current = next((item for item in self._requirements if item.get('id') == requirement.get('id')), requirement)
        title = requirement.get('title') or '未命名需求'
        code = requirement.get('code') or '无编号'
        kind = requirement.get('record_kind', '需求')
        system = requirement.get('system') or '未选系统'
        status = requirement.get('status') or '待分析'
        month = requirement.get('online_month') or ''
        planned = requirement.get('planned_online_date') or ''
        actual = requirement.get('actual_online_date') or ''
        # 上线时间：优先实际上线，其次计划上线，再次上线月份
        if actual:
            online_text = actual
        elif planned:
            online_text = planned
        elif month:
            online_text = format_online_month_label(month)
        else:
            online_text = '未定'
        self.detail_title.setText(title)
        self.detail_title.setToolTip(f'{title}\n编号：{code}\n完整信息请点「编辑」')
        field_map = {
            'kind': kind,
            'status': status,
            'system': system,
            'online': online_text,
        }
        for key, value in field_map.items():
            self._detail_fields[key].setText(str(value))
            self._detail_fields[key].setToolTip(str(value))

        # 任务标记：只显示已勾选项的可点击短 chip
        done = normalize_flag_done(requirement)
        active = {key for key, _s, _f in active_flags(requirement)}
        for key, short, full in FLAG_DEFS:
            btn = self._flag_buttons[key]
            if key not in active:
                btn.setVisible(False)
                continue
            btn.setVisible(True)
            is_done = bool(done.get(key))
            btn.setText(f"{'🟢' if is_done else '🔴'}{short}")
            btn.setProperty('flagDone', 'true' if is_done else 'false')
            btn.style().unpolish(btn); btn.style().polish(btn)
            btn.setToolTip(f'{full}\n点击切换：待办(红) ↔ 完成(绿)')

        local_path = requirement.get('local_path') or ''
        if requirement.get('workspace_kind') == 'folder':
            modified = (requirement.get('source_modified_at') or '未知')[:16].replace('T', ' ')
            self.svn_meta.setText(f'{local_path or "未识别"}  ·  {requirement.get("file_count", 0)} 文件  ·  {modified}')
        elif local_path or requirement.get('svn_url'):
            rev = requirement.get('svn_revision') or '未知'
            self.svn_meta.setText(f'{local_path or "未检出"}  ·  r{rev}')
        else:
            self.svn_meta.setText('尚未关联代码目录')
        self.svn_meta.setToolTip(
            f"分支：{requirement.get('svn_url') or '无'}\n本地：{local_path or '无'}"
        )
        if refresh_files:
            self._refresh_file_tree()
        sql = merged_sql(requirement)
        self.sql_preview.setPlainText(sql or '尚未关联 SQL')
        self.docx_btn.setEnabled(bool(sql))
        self.sql_btn.setEnabled(bool(sql))

    def _set_busy(self, message=''):
        busy = bool(message)
        self.svn_activity.setText(message or 'SVN 操作完成。')
        widgets = (
            self.scan_btn, self.checkout_btn, self.update_all_btn, self.system_config_btn,
            self.bug_btn, self.import_btn, self.add_btn, self.search_edit, self.system_filter,
            self.kind_filter, self.status_filter, self.requirement_list, self.select_all_check,
            self.batch_delete_btn, self.expand_tree_btn, self.collapse_tree_btn, self.file_tree,
            self.open_folder_btn, self.refresh_svn_btn,
            self.update_current_btn, self.add_file_btn, self.new_text_btn, self.lock_file_btn,
            self.unlock_file_btn, self.commit_btn, self.delete_btn, self.edit_btn,
            self.daily_btn, self.docx_btn, self.sql_btn,
        )
        for widget in widgets:
            widget.setEnabled(not busy)
        if not busy:
            self._update_batch_delete_button()
        if busy:
            self.loading.start_busy(message)
        else:
            self._show_requirement(self.requirement_list.currentItem(), refresh_files=False)

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'requirement_list', None) and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Delete) or event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self._selected_requirements():
                    self._delete_requirement()
                    return True
        return super().eventFilter(watched, event)

    def _start_task(self, message, function, arguments, success, show_loading=True):
        if self._active_worker is not None:
            show_info(self, 'SVN 正在处理', '请等待当前 SVN 操作完成。')
            return
        self._task_failed = False
        self._task_shows_loading = show_loading
        if show_loading:
            self._set_busy(message)
        worker = SvnWorker(function, *arguments, parent=self)
        self._active_worker = worker
        worker.result_ready.connect(lambda result: self._task_succeeded(result, success))
        worker.failed.connect(self._task_failed_with_error)
        worker.finished.connect(lambda: self._task_finished(worker))
        worker.start()

    def _task_succeeded(self, result, success):
        if self._task_shows_loading:
            self.loading.finish('处理完成')
        success(result)

    def _task_failed_with_error(self, error):
        self._task_failed = True
        self.svn_activity.setText('SVN 操作失败；本地台账未丢失。')
        if self._task_shows_loading:
            self.loading.fail('处理失败：' + error[:120])
            QTimer.singleShot(4000, self.loading.hide)
        show_warning(self, 'SVN 操作失败', error)

    def _task_finished(self, worker):
        if worker is not self._active_worker:
            worker.deleteLater()
            return
        self._active_worker = None
        if self._task_shows_loading:
            self._set_busy('')
        if self._task_failed:
            self.svn_activity.setText('SVN 操作失败；本地台账未丢失。')
        if worker:
            worker.deleteLater()
        if self._pending_file_refresh:
            self._pending_file_refresh = False
            QTimer.singleShot(0, self._refresh_file_tree)

    def _scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择按月份整理的需求根目录')
        if folder:
            self._start_task('正在扫描本地需求文件夹和 SVN 工作副本……', scan_working_copies, (folder,), self._scan_finished)

    def _scan_finished(self, copies):
        self._requirements, added, updated = merge_working_copies(self._requirements, copies)
        # 扫描新增条目补全系统/月份等空字段（只填空，不覆盖已有）
        self._requirements = [
            apply_auto_inference(item, systems=self._systems, only_empty=True)
            for item in self._requirements
        ]
        save_requirements(self._requirements); self._refresh()
        missing = sum(not item.get('online_month') for item in copies)
        errors = sum(bool(item.get('error')) for item in copies)
        message = f'识别到 {len(copies)} 个需求目录；新增 {added} 条，更新 {updated} 条。\n已按月份分组，同月按文件最新修改时间排序。'
        if missing: message += f'\n{missing} 条未从目录名识别出月份，可在“编辑”中补充。'
        if errors: message += f'\n{errors} 条 SVN 信息读取失败，已保留本地路径和错误信息。'
        show_success(self, '需求文件夹扫描完成', message)
        if added or updated:
            focus = self._current or (self._requirements[0] if self._requirements else None)
            if focus:
                self._offer_next_steps(focus, context='scan')

    def _checkout_svn(self):
        dialog = SvnCheckoutDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        values = dialog.values()
        self._pending_checkout = values
        self._start_task('正在从 SVN 拉取，请保持内网连接……', checkout, (values['url'], values['target']), self._checkout_finished)

    def _checkout_finished(self, info):
        values = self._pending_checkout
        info.update({
            'record_kind': values['record_kind'], 'online_month': values['online_month'],
            'svn_status': svn_status(info['local_path'])['text'],
        })
        item = requirement_from_working_copy(info)
        item['title'] = values['title']
        item['record_kind'] = values['record_kind']
        if values['record_kind'] == 'BUG': item['category'] = '缺陷优化'
        self._requirements.append(item); self._current = item
        save_requirements(self._requirements); self._refresh()
        show_success(self, 'SVN 拉取完成', f"已拉取到：\n{info['local_path']}")

    def _update_all(self):
        paths = list(dict.fromkeys(item.get('local_path') for item in self._requirements if item.get('local_path') and os.path.isdir(item.get('local_path'))))
        if not paths:
            show_info(self, '更新全部 SVN', '当前台账没有可更新的本地 SVN 工作副本。'); return
        if not confirm_action(
            self, '更新全部 SVN',
            f'确定依次更新 {len(paths)} 个工作副本吗？\n\n仅执行 SVN update，不会提交或删除本地文件。',
            confirm_text='开始更新',
            danger=False,
        ):
            return
        self._start_task('正在逐个更新全部 SVN 工作副本……', update_many, (paths,), self._update_all_finished)

    def _update_all_finished(self, results):
        successful = [item for item in results if item.get('ok')]
        failed = [item for item in results if not item.get('ok')]
        self._requirements, _added, _updated = merge_working_copies(self._requirements, successful)
        save_requirements(self._requirements); self._refresh()
        message = f'更新成功 {len(successful)} 个，失败 {len(failed)} 个。'
        if failed:
            message += '\n\n失败明细：\n' + '\n'.join(f"{item.get('path')}：{item.get('error')}" for item in failed[:8])
        show_success(self, '全部 SVN 更新完成', message)

    def _paste_bug(self):
        initial = QApplication.clipboard().text().strip()
        content, accepted = QInputDialog.getMultiLineText(self, '粘贴 BUG 内容', '直接粘贴 BUG 描述，软件会自动提取编号、标题并归类：', initial)
        if not accepted or not content.strip(): return
        candidate = requirement_from_text(content, '直接粘贴BUG', systems=self._systems)
        candidate.update({'record_kind': 'BUG', 'category': '缺陷优化'})
        svn_url, accepted = QInputDialog.getText(
            self, 'BUG 开发分支',
            '可粘贴这个 BUG 的开发分支 SVN 地址；没有可直接留空：',
        )
        if not accepted:
            return
        candidate['svn_url'] = svn_url.strip()
        apply_auto_inference(candidate, systems=self._systems, only_empty=True)
        self._requirements.append(candidate); self._current = candidate
        save_requirements(self._requirements); self._refresh()
        self._offer_next_steps(candidate, context='bug')

    def _current_path(self):
        path = self._current.get('local_path', '') if self._current else ''
        return path if path and os.path.isdir(path) else ''

    def _refresh_file_tree(self):
        self.file_tree.clear()
        path = self._current_path()
        if not path: return
        if self._active_worker is not None:
            self._pending_file_refresh = True
            return
        self._file_tree_path = path
        self._start_task('正在读取需求文件夹完整内容……', workspace_files, (path,), self._file_tree_loaded, show_loading=False)

    def _file_tree_loaded(self, entries):
        if getattr(self, '_file_tree_path', '') != self._current_path():
            return
        nodes = {'': self.file_tree.invisibleRootItem()}
        for entry in entries:
            relative = entry['relative_path']
            parent_key = os.path.dirname(relative)
            parent = nodes.get(parent_key, self.file_tree.invisibleRootItem())
            locked = relative in (self._current.get('svn_locks', {}) if self._current else {})
            name = os.path.basename(relative)
            display_name = f'🔒 {name}' if locked else name
            item = QTreeWidgetItem(parent, (
                display_name, entry.get('modified_at', ''),
                entry.get('file_type', '文件夹' if entry['is_dir'] else '文件'), entry.get('size', ''),
            ))
            item.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setTextAlignment(1, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setTextAlignment(2, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setTextAlignment(3, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setIcon(0, _FILE_ICON_PROVIDER.icon(QFileInfo(entry['path'])))
            item.setData(0, Qt.ItemDataRole.UserRole, entry['path'])
            item.setData(0, IS_DIR_ROLE, entry['is_dir'])
            item.setToolTip(0, relative)
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
                if entry['is_dir'] else QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator
            )
            if locked:
                font = item.font(0); font.setBold(True); item.setFont(0, font); item.setForeground(0, QColor('#B24A24'))
            if entry['is_dir']: nodes[relative] = item
        self.file_tree.expandAll()
        self._update_lock_buttons()

    def _selected_svn_file(self):
        item = self.file_tree.currentItem()
        if not item or item.data(0, IS_DIR_ROLE):
            return ''
        path = item.data(0, Qt.ItemDataRole.UserRole)
        return path if path and os.path.isfile(path) else ''

    def _update_lock_buttons(self, *_args):
        path = self._selected_svn_file() if hasattr(self, 'file_tree') else ''
        relative = os.path.relpath(path, self._current_path()) if path and self._current_path() else ''
        locked = bool(relative and self._current and relative in self._current.get('svn_locks', {}))
        is_svn = bool(self._current and self._current.get('workspace_kind', 'svn') == 'svn')
        if hasattr(self, 'lock_file_btn'):
            self.lock_file_btn.setEnabled(is_svn and bool(path) and not locked)
            self.unlock_file_btn.setEnabled(is_svn and bool(path) and locked)

    def _lock_selected_file(self):
        path = self._selected_svn_file()
        if not path:
            show_info(self, 'SVN 文件锁', '请先在文件树中选择一个文件。'); return
        message, accepted = QInputDialog.getText(self, '锁定 SVN 文件', '锁定说明：', text='PengTools 开发锁定')
        if not accepted: return
        self._pending_lock_path = path
        self._start_task('正在锁定选中的 SVN 文件……', lock_file, (path, message), self._lock_finished)

    def _lock_finished(self, result):
        if not self._current: return
        relative = os.path.relpath(result['path'], self._current_path())
        self._current.setdefault('svn_locks', {})[relative] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(self._requirements); self._refresh_file_tree()
        show_success(self, 'SVN 文件已锁定', result.get('output') or '其他人将无法提交该文件，直到你解锁。')

    def _unlock_selected_file(self):
        path = self._selected_svn_file()
        if not path:
            show_info(self, 'SVN 文件锁', '请先在文件树中选择一个已锁定文件。'); return
        self._start_task('正在解锁选中的 SVN 文件……', unlock_file, (path,), self._unlock_finished)

    def _unlock_finished(self, result):
        if not self._current: return
        relative = os.path.relpath(result['path'], self._current_path())
        self._current.setdefault('svn_locks', {}).pop(relative, None)
        save_requirements(self._requirements); self._refresh_file_tree()
        show_success(self, 'SVN 文件已解锁', result.get('output') or '该文件已允许其他人提交。')

    def _open_tree_item(self, item, _column):
        """双击：文件夹只展开/折叠树；文件才用系统打开。"""
        if not item:
            return
        is_dir = bool(item.data(0, IS_DIR_ROLE))
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if is_dir or (path and os.path.isdir(path)):
            item.setExpanded(not item.isExpanded())
            return
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _show_file_menu(self, point):
        item = self.file_tree.itemAt(point)
        if not item:
            return
        self.file_tree.setCurrentItem(item)
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(self)
        is_dir = bool(item.data(0, IS_DIR_ROLE)) or os.path.isdir(path)
        if is_dir:
            expand_action = menu.addAction('展开/折叠')
            expand_action.triggered.connect(lambda: item.setExpanded(not item.isExpanded()))
            open_action = menu.addAction('打开目录')
            open_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        else:
            open_action = menu.addAction('打开文件')
            open_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            folder_action = menu.addAction('打开所在文件夹')
            folder_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
            menu.addSeparator()
            if self.lock_file_btn.isEnabled():
                menu.addAction('锁定文件', self._lock_selected_file)
            if self.unlock_file_btn.isEnabled():
                menu.addAction('解锁文件', self._unlock_selected_file)
        menu.exec(self.file_tree.viewport().mapToGlobal(point))

    def _open_folder(self):
        self._open_requirement_folder(self._current)

    @staticmethod
    def _inspect_working_copy(path):
        info = working_copy_info(path); info['svn_status'] = svn_status(path)['text']; return info

    def _refresh_current_svn(self):
        path = self._current_path()
        if path: self._start_task('正在刷新当前 SVN 状态……', self._inspect_working_copy, (path,), self._refresh_current_finished)

    def _refresh_current_finished(self, info):
        if not self._current: return
        self._current.update({key: info.get(key, '') for key in ('svn_url', 'svn_revision', 'svn_status')})
        self._current['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(self._requirements); self._refresh()

    def _update_current(self):
        path = self._current_path()
        if not path: return
        self._start_task('正在更新当前 SVN 工作副本……', update_many, ([path],), self._update_current_finished)

    def _update_current_finished(self, results):
        if results and results[0].get('ok'):
            self._refresh_current_finished(results[0]); show_success(self, 'SVN 更新', '当前工作副本已更新。')
        elif results:
            show_warning(self, 'SVN 更新失败', results[0].get('error', '未知错误'))

    def _add_existing_files(self):
        path = self._current_path()
        if not path: return
        paths, _ = QFileDialog.getOpenFileNames(self, '选择要复制并加入 SVN 的文件')
        if not paths: return
        folder, accepted = QInputDialog.getText(self, '目标子目录', '工作副本内的目标子目录，可留空：')
        if not accepted: return
        self._start_task('正在复制文件并执行 SVN add……', add_existing_files, (path, paths, folder), self._files_added)

    def _add_text_file(self):
        path = self._current_path()
        if not path: return
        relative, accepted = QInputDialog.getText(self, '新增文本文件', '工作副本内相对路径，例如 文档/说明.md：')
        if not accepted or not relative.strip(): return
        content, accepted = QInputDialog.getMultiLineText(self, '文件内容', '输入文件内容：')
        if not accepted: return
        self._start_task('正在创建文件并执行 SVN add……', add_text_file, (path, relative, content), self._files_added)

    def _files_added(self, result):
        self._refresh_file_tree()
        count = len(result) if isinstance(result, list) else 1
        show_success(self, 'SVN 新增完成', f'已新增并加入版本控制：{count} 个文件。提交前仍可继续修改。')

    def _commit_svn(self):
        path = self._current_path()
        if not path: return
        try:
            status = svn_status(path)
        except SvnError as exc:
            show_warning(self, 'SVN 状态', str(exc)); return
        if status['clean']:
            show_info(self, '提交 SVN', '当前工作副本没有可提交的改动。'); return
        message, accepted = QInputDialog.getText(self, '提交 SVN', '提交说明：')
        if not accepted or not message.strip(): return
        preview = status['text'][:4000]
        if not confirm_action(
            self, '确认提交 SVN',
            f'将提交当前需求工作副本的全部改动：\n\n{preview}\n\n提交说明：{message}',
            confirm_text='确认提交',
            danger=True,
        ):
            return
        self._start_task('正在提交 SVN，请勿关闭软件……', commit_working_copy, (path, message), self._commit_finished)

    def _commit_finished(self, info):
        self._refresh_current_finished(info)
        show_success(self, 'SVN 提交完成', info.get('output', '提交成功。'))

    def _save_dialog(self, requirement=None, offer_next=True, is_new=False, offer_daily=None):
        # offer_daily 为旧参数别名：True/False 映射到是否弹出下一步建议
        if offer_daily is not None:
            offer_next = bool(offer_daily)
        dialog = RequirementDialog(requirement, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        now = datetime.datetime.now().isoformat(timespec='seconds')
        values = dialog.values()
        if not values.get('title'):
            show_warning(self, '需求工作台', '标题为空，未保存。')
            return None

        # 统一落到列表里的可变 dict，避免树节点 UserRole 拷贝导致“看起来保存了实际没写回”
        req_id = None
        if isinstance(requirement, dict):
            req_id = requirement.get('id')
        if not req_id:
            req_id = requirement_from_text('', '', systems=self._systems).get('id')
            is_new = True

        index = next((i for i, item in enumerate(self._requirements) if item.get('id') == req_id), -1)
        if index < 0:
            target = {'id': req_id, 'created_at': now}
            self._requirements.append(target)
            index = len(self._requirements) - 1
            is_new = True
        else:
            target = self._requirements[index]

        old_done = dict(target.get('flag_done') or {})
        # 原地更新，保持列表引用稳定
        target.update(values)
        target['id'] = req_id
        target['has_sql'] = bool(values.get('has_sql') or values.get('sql_parts'))
        target['needs_peripheral_upgrade'] = bool(values.get('needs_peripheral_upgrade'))
        target['needs_interface_update'] = bool(values.get('needs_interface_update'))
        target['temporary_upgrade'] = bool(values.get('temporary_upgrade'))
        target['sql_parts'] = list(values.get('sql_parts') or [])
        target['source_files'] = list(values.get('source_files') or [])
        target['flag_done'] = {
            key: bool(old_done.get(key)) if flag_is_active(target, key) else False
            for key, _short, _full in FLAG_DEFS
        }
        target['updated_at'] = now
        if is_new:
            target.setdefault('created_at', now)

        normalized = apply_auto_inference(normalize_requirement(target), systems=self._systems, only_empty=True)
        target.clear()
        target.update(normalized)
        self._requirements[index] = target

        try:
            save_requirements(self._requirements)
        except OSError as exc:
            show_error(self, '保存失败', f'无法写入 requirements.json：\n{exc}')
            return None

        self._current = target
        self._refresh()
        self.svn_activity.setText(
            f'已保存：{target.get("code") or "无编号"} · {target.get("title") or "未命名"}'
            f' · 标记 {flag_status_text(target)}'
        )
        if offer_next:
            self._offer_next_steps(target, context='save' if not is_new else 'import')
        return target

    def _offer_next_steps(self, requirement, context='save'):
        """保存/导入/扫描/粘贴 BUG 后的统一下一步建议（单次弹窗，默认推荐日报）。"""
        if not requirement:
            return
        has_sql = bool(requirement.get('has_sql') or requirement.get('sql_parts'))
        has_path = bool(requirement.get('local_path') and os.path.isdir(str(requirement.get('local_path'))))
        name = ' '.join(part for part in (requirement.get('code'), requirement.get('title')) if part) or '当前需求'
        titles = {
            'save': '已保存',
            'import': '已导入并保存',
            'bug': 'BUG 已归档',
            'scan': '扫描完成',
        }
        title = titles.get(context, '下一步')
        actions = [
            ('daily', '加入今日日报', True),
            ('release', '进入升级准备', False),
        ]
        if has_sql:
            actions.append(('sql', '发送 SQL', False))
        if has_path:
            actions.append(('folder', '打开目录', False))
        recommended = 'daily'
        if context == 'scan':
            recommended = 'release'
            message = f'{name}\n\n推荐：进入升级准备核对候选；也可把当前项加入今日日报。'
        elif has_sql:
            message = f'{name}\n\n推荐：加入今日日报。已检测到 SQL，也可发送到升级准备/SQL 整理。'
        else:
            message = f'{name}\n\n推荐：加入今日日报（不覆盖你已写内容）。'
        action = offer_next_steps(self, title, message, actions, recommended=recommended)
        if action == 'daily':
            self.add_to_daily.emit(requirement)
        elif action == 'release':
            self.open_release_prep.emit(requirement)
        elif action == 'sql':
            self.send_to_sql.emit(requirement.get('title', '需求 SQL'), merged_sql(requirement))
        elif action == 'folder':
            self._open_requirement_folder(requirement)

    def _import_requirement(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '导入需求文档', '', '需求文档 (*.docx *.xlsx *.txt *.md *.json *.xml *.yaml *.yml *.csv)'
        )
        if not path: return
        self.loading.start_busy('正在导入并整理需求文档……'); self.loading.raise_(); QApplication.processEvents()
        try:
            entries = _document_entries_with_password(path, self)
            for entry in entries: entry['name'] = entry.get('source') or os.path.basename(path)
            content = '\n'.join(_entry_text(entry) for entry in entries)
        except (OSError, ValueError) as exc:
            self.loading.fail('导入失败'); QTimer.singleShot(2500, self.loading.hide)
            show_warning(self, '导入需求', f'{os.path.basename(path)} 读取失败：{exc}'); return
        self.loading.finish('导入完成')
        candidate = requirement_from_text(content, os.path.basename(path), systems=self._systems)
        candidate['source_files'] = entries
        self._save_dialog(candidate, is_new=True, offer_next=True)

    def _add_requirement(self): self._save_dialog(is_new=True, offer_next=True)
    def _edit_requirement(self):
        if self._current: self._save_dialog(self._current, offer_next=False)

    def _delete_requirement(self):
        selected = self._selected_requirements()
        if not selected:
            show_info(self, '删除需求', '请先在左侧树勾选或选中要删除的需求/BUG。')
            return
        names = '\n'.join(f"• {item.get('code') or '无编号'}  {item.get('title') or '未命名'}" for item in selected[:8])
        if len(selected) > 8:
            names += f"\n• 另外 {len(selected) - 8} 项……"
        if not confirm_action(
            self,
            f'删除选中的 {len(selected)} 项',
            f"即将删除以下需求/BUG：\n\n{names}\n\n仅移除台账记录，不会删除本地 SVN 工作副本或资料目录。\n删除后无法恢复，是否继续？",
        ):
            return
        targets = {item.get('id') for item in selected}
        count = len(selected)
        self._requirements = [item for item in self._requirements if item.get('id') not in targets]
        self._current = None
        if hasattr(self, 'select_all_check'):
            self.select_all_check.blockSignals(True)
            self.select_all_check.setChecked(False)
            self.select_all_check.blockSignals(False)
        save_requirements(self._requirements)
        self._refresh()
        # 不二次弹窗打断：确认删除后状态栏轻提示即可
        parent = self.window()
        if parent and hasattr(parent, 'status_bar'):
            parent.status_bar.showMessage(f'已从需求台账删除 {count} 项', 4000)

    def _send_sql(self):
        if self._current: self.send_to_sql.emit(self._current.get('title', '需求 SQL'), merged_sql(self._current))
    def _send_docx(self):
        if self._current: self.send_to_docx.emit(self._current.get('title', '需求 SQL'), merged_sql(self._current))
    def _send_daily(self):
        if self._current: self.add_to_daily.emit(self._current)
