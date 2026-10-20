# -*- coding: utf-8 -*-
import datetime
import copy
import os
import re

from PyQt6.QtCore import QDate, QEvent, QFileInfo, QItemSelectionModel, QRect, QSignalBlocker, QSize, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFontMetrics, QKeySequence, QTextOption
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QHeaderView, QScrollArea, QSizePolicy, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

try:
    from PyQt6.sip import isdeleted as _sip_isdeleted
except ImportError:
    try:
        import sip
        _sip_isdeleted = sip.isdeleted
    except ImportError:
        _sip_isdeleted = None


def _is_alive(obj) -> bool:
    if obj is None:
        return False
    if _sip_isdeleted is not None:
        try:
            if _sip_isdeleted(obj):
                return False
        except Exception:
            return False
    return True


# Running QThreads must outlive a RequirementPanel that is removed or deleted.
# The worker drops itself from this registry after it finishes naturally.
_ACTIVE_SVN_WORKERS = set()


class _ElideTextDelegate(QStyledItemDelegate):
    """文件库：单行省略号；选中态强制高对比字色，避免 setForeground 盖住。"""

    def __init__(self, parent=None, row_height: int = 24):
        super().__init__(parent)
        self._row_height = row_height

    def _theme_colors(self):
        from ui.selection_delegate import theme_select_colors
        return theme_select_colors()

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.textElideMode = Qt.TextElideMode.ElideRight
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if not selected:
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            if fg is not None:
                color = fg.color() if hasattr(fg, 'color') else QColor(fg)
                if color.isValid():
                    opt.foregroundBrush = QBrush(color)
                    opt.palette.setColor(opt.palette.ColorRole.Text, color)
                    opt.palette.setColor(opt.palette.ColorRole.WindowText, color)
        if selected:
            fill, accent, _text = self._theme_colors()
            opt.backgroundBrush = fill
            opt.foregroundBrush = QBrush(accent)
            opt.palette.setColor(opt.palette.ColorRole.Text, accent)
            opt.palette.setColor(opt.palette.ColorRole.HighlightedText, accent)
            opt.palette.setColor(opt.palette.ColorRole.WindowText, accent)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        fm = QFontMetrics(option.font)
        return QSize(hint.width(), max(self._row_height, fm.height() + 4))


class _WrapTextDelegate(QStyledItemDelegate):
    """树/列表名称列：完整换行展示，禁止半截省略；选中态强制高对比字色。"""

    def __init__(self, parent=None, min_height: int = 28, max_lines: int = 3):
        super().__init__(parent)
        self._min_height = min_height
        self._max_lines = max_lines

    def _theme_colors(self):
        from ui.selection_delegate import theme_select_colors
        return theme_select_colors()

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideNone
        text = index.data(Qt.ItemDataRole.DisplayRole) or ''
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        fill, accent, text_strong = self._theme_colors()
        if selected:
            painter.save()
            painter.fillRect(option.rect.adjusted(1, 1, -1, -1), fill)
            painter.restore()
        else:
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        if not text:
            return
        # 图标区
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        left = option.rect.left() + 4
        if icon is not None and hasattr(icon, 'isNull') and not icon.isNull():
            icon_size = option.decorationSize if option.decorationSize.isValid() else QSize(16, 16)
            icon_rect = QRect(left, option.rect.top() + 4, icon_size.width(), icon_size.height())
            icon.paint(painter, icon_rect)
            left = icon_rect.right() + 6
        text_rect = QRect(left, option.rect.top() + 2, option.rect.right() - left - 4, option.rect.height() - 4)
        painter.save()
        painter.setPen(accent if selected else text_strong)
        # 多行完整绘制
        fm = QFontMetrics(option.font)
        line_h = fm.lineSpacing()
        y = text_rect.top()
        remaining = str(text)
        lines = 0
        while remaining and lines < self._max_lines:
            n = len(remaining)
            lo, hi = 1, n
            best = 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if fm.horizontalAdvance(remaining[:mid]) <= max(8, text_rect.width()):
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            chunk = remaining[:best]
            remaining = remaining[best:]
            painter.drawText(text_rect.left(), y + fm.ascent(), chunk)
            y += line_h
            lines += 1
            if remaining and lines >= self._max_lines:
                break
        painter.restore()

    def sizeHint(self, option, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
        width = option.rect.width() if option.rect.width() > 40 else 200
        fm = QFontMetrics(option.font)
        if not text:
            return QSize(width, self._min_height)
        # 估算行数
        line_w = max(40, width - 28)
        total = 0
        line = ''
        lines = 1
        for ch in text:
            if fm.horizontalAdvance(line + ch) > line_w:
                lines += 1
                line = ch
            else:
                line += ch
            if lines >= self._max_lines:
                break
        h = max(self._min_height, lines * fm.lineSpacing() + 8)
        return QSize(width, h)

_FILE_ICON_PROVIDER = QFileIconProvider()

from config import (
    REQUIREMENTS_FILE, SVN_WORKSPACE_DIR, SYSTEMS_FILE,
    load_last_choices, load_requirement_ui, load_systems, save_requirement_ui, update_last_choices,
)
from tools.personal_knowledge import (
    export_word_entry, export_workbook_entry, extract_document_entries,
    read_text_file,
)
from tools.requirements import (
    CATEGORIES, FLAG_DEFS, FLAG_CHIP_LABELS, PRIORITIES, STATUSES, active_flags, apply_auto_inference,
    binding_for, classify_requirement, clear_workspace_binding, flag_chip_text, flag_is_active, flag_status_text,
    load_requirements, merge_working_copies, merged_sql, normalize_flag_done,
    normalize_requirement, normalize_test_points, requirement_from_text, requirement_from_working_copy,
    requirement_matches_system, requirement_search_text, requirement_systems,
    save_requirements, sync_system_fields, systems_display_text,
)
from panels.test_points_editor import TestPointsEditor
from tools.svn_workspace import (
    SVN_DIRTY_STATUSES, SvnError, add_existing_files, add_text_file, changed_paths, checkout, commit_paths,
    commit_working_copy, lock_file, lock_files, month_end_date, revert_paths,
    safe_folder_name, scan_working_copies, svn_status, update_many,
    unlock_file, unlock_files, validate_svn_url, working_copy_info, workspace_files,
    workspace_snapshot, working_copy_locks,
)
from ui.confirm_dialog import (
    confirm_action, offer_next_steps, show_error, show_info, show_success, show_warning,
)
from ui.aurora_progress import AuroraProgress
from ui.dialog_buttons import clamp_dialog_geometry
from ui.field_metrics import CompactStepper, apply_form, size_combo, size_compact_button, size_date, size_line, size_pick_combo
from ui.motion import play_dialog_enter


IS_DIR_ROLE = int(Qt.ItemDataRole.UserRole) + 1
GROUP_MONTH_ROLE = int(Qt.ItemDataRole.UserRole) + 2
SVN_STATUS_LABELS = {
    'normal': '已提交',
    'modified': '已修改未提交',
    'added': '已添加未提交',
    'deleted': '已删除未提交',
    'replaced': '已替换未提交',
    'missing': '工作副本缺失',
    'unversioned': '未纳入版本库',
    'conflicted': '冲突',
    'obstructed': '路径冲突',
    'incomplete': '不完整',
}


def _svn_status_color(code: str) -> QColor:
    try:
        from ui.theme_manager import ThemeManager
        pal = ThemeManager.instance().palette()
    except Exception:
        pal = {}
    if str(code or '') in SVN_DIRTY_STATUSES:
        return QColor(pal.get('DANGER', '#B85C5C'))
    return QColor(pal.get('SUCCESS', '#3E7A5C'))


def normalize_content_splitter_sizes(stored=None, total_h: int = 800, top_h: int = 160) -> list[int]:
    """配置兼容：上区按内容高度，其余给文件库（不再使用固定比例）。"""
    total_h = max(int(total_h or 800), 400)
    # 优先用显式 top_h；stored 仅作兜底，且夹在合理紧凑区间
    top = int(top_h or 0)
    if top <= 0 and isinstance(stored, (list, tuple)) and len(stored) >= 1:
        try:
            top = int(stored[0] or 0)
        except (TypeError, ValueError):
            top = 0
    if top <= 0:
        top = 160
    top = max(96, min(top, min(280, total_h // 2)))
    bottom = max(200, total_h - top)
    return [top, bottom]


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
        def _safe_emit_moved():
            if _is_alive(self):
                self.requirementsMoved.emit(list(requirement_ids), month)
        QTimer.singleShot(0, _safe_emit_moved)
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
        clamp_dialog_geometry(self, 380, 220, min_width=320, min_height=180)
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
        self.year_spin = CompactStepper(2000, 2099, max(2000, min(2099, year)), edit_width=52)
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
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, 'zh')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

    def month_text(self):
        return f'{self.year_spin.value():04d}-{int(self.month_combo.currentData()):02d}'


class MonthSelect(QWidget):
    """内联年月下拉：只选月，不能选日。"""

    def __init__(self, value='', parent=None):
        super().__init__(parent)
        year, month = parse_year_month(value)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.year_spin = CompactStepper(2000, 2099, year, edit_width=52, suffix=' 年')
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
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        self.edit = QLineEdit(value)
        self.edit.setObjectName('date-input-edit')
        self.edit.setProperty('editableField', True)
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
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, 'zh')
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
        clamp_dialog_geometry(self, 860, 700, min_width=640, min_height=500)
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
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, 'zh', Save='保存附件修改')
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

    @staticmethod
    def _column_name(index):
        value, result = index + 1, ''
        while value:
            value, remainder = divmod(value - 1, 26); result = chr(65 + remainder) + result
        return result

    def _setup_excel(self, root):
        search_row = QHBoxLayout(); search_row.addWidget(QLabel('实时定位'))
        self.search = QLineEdit(); self.search.setProperty('editableField', True); self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._filter_excel)
        search_row.addWidget(self.search, 1); root.addLayout(search_row)
        rows = self._entry.get('rows', []); columns = self._entry.get('column_count') or max((len(row) for row in rows), default=0)
        self.table = QTableWidget(len(rows), columns); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        try:
            from ui.design_system import apply_list_header
            apply_list_header(self.table.horizontalHeader())
        except Exception:
            pass
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
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
                        self._highlights.append((item, QBrush(item.background())))
                        try:
                            from ui.theme_manager import ThemeManager
                            sc = QColor(ThemeManager.instance().token('SEARCH_MATCH'))
                        except Exception:
                            sc = QColor('#FFF19C')
                        item.setBackground(sc); first = first or item
        if first:
            self.table.setCurrentItem(first); self.table.scrollToItem(first, QAbstractItemView.ScrollHint.PositionAtCenter); self.table.clearSelection()

    def _sync_excel(self):
        self._entry['rows'] = [[self.table.item(r, c).text() if self.table.item(r, c) else '' for c in range(self.table.columnCount())] for r in range(self.table.rowCount())]
        self._entry['row_count'] = self.table.rowCount(); self._entry['column_count'] = self.table.columnCount()

    def _export_excel(self, visible):
        from tools.dialog_paths import get_dialog_save_path, remember_dialog_path
        start = get_dialog_save_path('requirement_export', 'requirement.xlsx')
        self._sync_excel()
        path, _ = QFileDialog.getSaveFileName(self, '导出 Excel', start, 'Excel 工作簿 (*.xlsx)')
        if not path: return
        remember_dialog_path('requirement_export', path)
        if not path.lower().endswith('.xlsx'): path += '.xlsx'
        rows = [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)] if visible else None
        columns = [c for c in range(self.table.columnCount()) if not self.table.isColumnHidden(c)] if visible else None
        export_workbook_entry(self._entry, path, rows, columns)

    def _export_word(self):
        from tools.dialog_paths import get_dialog_save_path, remember_dialog_path
        entry = copy.deepcopy(self._entry); entry['content'] = self.editor.toPlainText(); entry['document_html'] = self.editor.toHtml()
        start = get_dialog_save_path('requirement_export', 'requirement.docx')
        path, _ = QFileDialog.getSaveFileName(self, '导出 Word', start, 'Word 文档 (*.docx)')
        if path:
            remember_dialog_path('requirement_export', path)
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

    def release_later(self):
        self.function = None
        self.arguments = ()
        self.success_callback = None
        _ACTIVE_SVN_WORKERS.discard(self)
        self.deleteLater()


class SvnCheckoutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('检出需求代码')
        clamp_dialog_geometry(self, 720, 380, min_width=520, min_height=320)
        root = QVBoxLayout(self)
        note = QLabel('填写 SVN 地址，选择类型与上线月份后检出到本机。使用本机已缓存认证，不保存密码。')
        note.setObjectName('ops-safety-note'); note.setWordWrap(True); root.addWidget(note)
        form = QFormLayout()
        apply_form(form)
        self.url_edit = QLineEdit(); self.url_edit.setProperty('editableField', True); self.url_edit.setPlaceholderText('svn://... 或 https://...')
        size_line(self.url_edit, 'path')
        self.title_edit = QLineEdit(); self.title_edit.setProperty('editableField', True); self.title_edit.setPlaceholderText('可留空，默认取路径最后一级')
        size_line(self.title_edit, 'std')
        self.kind_combo = QComboBox(); self.kind_combo.addItems(('需求', 'BUG'))
        size_combo(self.kind_combo, 'sm')
        self.month_enabled = QCheckBox('按上线月份归档'); self.month_enabled.setChecked(True)
        month_row = QHBoxLayout(); month_row.setSpacing(8); month_row.addWidget(self.month_enabled)
        # 只选年月，不用带「日」的日历
        self.month_edit = MonthSelect()
        month_row.addWidget(self.month_edit); month_row.addStretch()
        folder_row = QHBoxLayout()
        self.root_edit = QLineEdit(SVN_WORKSPACE_DIR); self.root_edit.setProperty('editableField', True); size_line(self.root_edit, 'path'); folder_row.addWidget(self.root_edit, 1)
        browse = QPushButton('浏览'); size_compact_button(browse)
        browse.clicked.connect(self._browse); folder_row.addWidget(browse)
        form.addRow('代码地址', self.url_edit); form.addRow('显示名称', self.title_edit)
        form.addRow('记录类型', self.kind_combo); form.addRow('上线月份', month_row); form.addRow('本机目录', folder_row)
        root.addLayout(form); root.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, 'zh', Ok='开始检出')
        buttons.accepted.connect(self._accept_checked); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.kind_combo.currentTextChanged.connect(lambda value: self.month_enabled.setChecked(value != 'BUG'))

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

    def _browse(self):
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_local_root', self.root_edit.text())
        folder = QFileDialog.getExistingDirectory(self, '选择本机存放目录', start)
        if folder:
            remember_dialog_path('requirement_local_root', folder, is_directory=True)
            self.root_edit.setText(folder)

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


class _SystemPickWidget(QWidget):
    """已配置系统的勾选列表。"""

    changed = pyqtSignal()

    def __init__(self, options, selected=None, parent=None):
        super().__init__(parent)
        self._boxes = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        selected = list(selected or [])
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(12)
        for name in options:
            box = QCheckBox(name)
            box.setChecked(name in selected)
            box.toggled.connect(self.changed.emit)
            self._boxes.append(box)
            wrap.addWidget(box)
        wrap.addStretch(1)
        layout.addLayout(wrap)

    def selected_names(self):
        return [box.text() for box in self._boxes if box.isChecked()]

    def set_selected_names(self, names):
        wanted = set(names or [])
        for box in self._boxes:
            box.blockSignals(True)
            box.setChecked(box.text() in wanted)
            box.blockSignals(False)
        self.changed.emit()


class RequirementDialog(QDialog):
    def __init__(self, requirement=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('编辑需求' if requirement else '新增需求')
        clamp_dialog_geometry(self, 1000, 760, min_width=800, min_height=600)
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
        self.code_edit.setProperty('editableField', True)
        size_line(self.code_edit, 'std')
        self.title_edit = QLineEdit(base.get('title', ''))
        self.title_edit.setProperty('editableField', True)
        size_line(self.title_edit, 'std')
        self.kind_combo = QComboBox(); self.kind_combo.addItems(('需求', 'BUG')); size_combo(self.kind_combo, 'sm')
        self.category_combo = QComboBox(); self.category_combo.addItems(CATEGORIES); size_combo(self.category_combo, 'md')
        self.status_combo = QComboBox(); self.status_combo.addItems(STATUSES); size_combo(self.status_combo, 'sm')
        self.priority_combo = QComboBox(); self.priority_combo.addItems(PRIORITIES); size_combo(self.priority_combo, 'sm')
        configured_systems = load_systems()
        inferred = apply_auto_inference(dict(base), systems=configured_systems, only_empty=True)
        option_names = [system['name'] for system in configured_systems]
        selected_systems = requirement_systems(inferred) or requirement_systems(base)
        for name in selected_systems:
            if name and name not in option_names:
                option_names.append(name)
        self.system_pick = _SystemPickWidget(option_names, selected_systems)
        self.system_pick.changed.connect(self._rebuild_system_bindings)
        self.owner_edit = QLineEdit(base.get('owner', ''))
        self.owner_edit.setProperty('editableField', True)
        size_line(self.owner_edit, 'std')
        self._binding_cache = {}
        for name in selected_systems:
            bound = binding_for(inferred if inferred.get('system_bindings') else base, name)
            if not bound.get('svn_url') and not bound.get('dev_local_path') and name == (selected_systems[0] if selected_systems else ''):
                bound = {
                    'svn_url': str(base.get('svn_url') or inferred.get('svn_url') or '').strip(),
                    'dev_local_path': str(base.get('dev_local_path') or inferred.get('dev_local_path') or '').strip(),
                }
            self._binding_cache[name] = bound
        self.svn_url_edit = QLineEdit(base.get('svn_url', ''))
        self.svn_url_edit.setProperty('editableField', True)
        size_line(self.svn_url_edit, 'path')
        self.svn_url_edit.setPlaceholderText('未配置则留空；有开发分支时再填，例如 svn://服务器/.../DEV_prpcar_...')
        self.dev_local_path_edit = QLineEdit(base.get('dev_local_path', ''))
        self.dev_local_path_edit.setProperty('editableField', True)
        size_line(self.dev_local_path_edit, 'path')
        self.dev_local_path_edit.setPlaceholderText('本机开发工程目录，配置后可在需求列表右键打开')
        self._standalone_dev_btn = QPushButton('选择目录')
        size_compact_button(self._standalone_dev_btn)
        self._standalone_dev_btn.clicked.connect(self._pick_dev_local_path)
        self._standalone_dev_row = QHBoxLayout()
        self._standalone_dev_row.addWidget(self.dev_local_path_edit, 1)
        self._standalone_dev_row.addWidget(self._standalone_dev_btn)
        self._standalone_dev_clear = QPushButton('清除')
        size_compact_button(self._standalone_dev_clear)
        self._standalone_dev_clear.clicked.connect(self.dev_local_path_edit.clear)
        self._standalone_dev_row.addWidget(self._standalone_dev_clear)
        self.local_path_edit = QLineEdit(base.get('local_path', ''))
        self.local_path_edit.setProperty('editableField', True)
        size_line(self.local_path_edit, 'path')
        self.local_path_edit.setPlaceholderText('可绑定本机已有的 SVN 工作副本或需求资料目录')
        bind_folder_btn = QPushButton('绑定目录'); size_compact_button(bind_folder_btn); bind_folder_btn.clicked.connect(self._bind_local_folder)
        clear_folder_btn = QPushButton('清除'); size_compact_button(clear_folder_btn); clear_folder_btn.clicked.connect(self._clear_local_folder)
        local_path_row = QHBoxLayout(); local_path_row.addWidget(self.local_path_edit, 1); local_path_row.addWidget(bind_folder_btn); local_path_row.addWidget(clear_folder_btn)
        self.system_bindings_host = QWidget()
        self.system_bindings_layout = QVBoxLayout(self.system_bindings_host)
        self.system_bindings_layout.setContentsMargins(0, 0, 0, 0)
        self.system_bindings_layout.setSpacing(6)
        self._binding_rows = {}
        self.actual_date = DateInput(base.get('actual_release_date') or base.get('actual_online_date', ''))
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
        form.addWidget(QLabel('负责人'), 3, 0); form.addWidget(self.owner_edit, 3, 1, 1, 3)
        form.addWidget(QLabel('所属系统'), 4, 0); form.addWidget(self.system_pick, 4, 1, 1, 3)
        form.addWidget(self.system_bindings_host, 5, 0, 1, 4)
        self.svn_label = QLabel('开发分支 SVN 地址')
        self.dev_label = QLabel('本地开发项目地址')
        form.addWidget(self.svn_label, 6, 0); form.addWidget(self.svn_url_edit, 6, 1, 1, 3)
        form.addWidget(self.dev_label, 7, 0); form.addLayout(self._standalone_dev_row, 7, 1, 1, 3)
        form.addWidget(QLabel('绑定本地目录'), 8, 0); form.addLayout(local_path_row, 8, 1, 1, 3)
        form.addWidget(QLabel('实际上线'), 9, 0); form.addWidget(self.actual_date, 9, 1, 1, 3)
        self._rebuild_system_bindings()
        layout.addLayout(form)

        flag_card = QFrame()
        flag_card.setObjectName('flag-check-card')
        flag_wrap = QVBoxLayout(flag_card)
        flag_wrap.setContentsMargins(12, 10, 12, 10)
        flag_wrap.setSpacing(8)
        flag_title = QLabel('上线事项')
        flag_title.setObjectName('section-title')
        flag_wrap.addWidget(flag_title)
        flag_hint = QLabel('勾选本需求/BUG需要处理的事项；保存后可在详情中标记是否完成。')
        flag_hint.setObjectName('field-hint')
        flag_hint.setWordWrap(True)
        flag_wrap.addWidget(flag_hint)
        flags = QGridLayout()
        flags.setHorizontalSpacing(14)
        flags.setVerticalSpacing(8)
        self.has_sql = QCheckBox('涉及 SQL')
        self.peripheral = QCheckBox('通知周边系统')
        self.temporary = QCheckBox('临时/紧急升级')
        self.interface_update = QCheckBox('更新接口文档')
        # 已有键（含用户显式 False）优先；仅缺失键才用推断结果
        def _flag_checked(key, extra=False):
            if key in base:
                return bool(base.get(key) or extra)
            return bool(inferred.get(key) or extra)

        self.has_sql.setChecked(_flag_checked('has_sql', bool(self._sql_parts)))
        self.peripheral.setChecked(_flag_checked('needs_peripheral_upgrade'))
        self.temporary.setChecked(_flag_checked('temporary_upgrade'))
        self.interface_update.setChecked(_flag_checked('needs_interface_update'))
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
        self.test_points_editor = TestPointsEditor(
            base.get('test_points'),
            description=base.get('description', ''),
            persist_callback=None,
            compact=True,
            auto_seed=False,
            parent=self,
        )
        layout.addWidget(self.test_points_editor)
        self.description_edit.textChanged.connect(self._sync_test_points_description)

        source_top = QHBoxLayout()
        source_top.addWidget(QLabel('需求相关文档'))
        source_top.addStretch()
        source_btn = QPushButton('上传需求文档')
        source_btn.clicked.connect(self._load_documents)
        source_top.addWidget(source_btn)
        source_edit_btn = QPushButton('编辑选中附件'); source_edit_btn.clicked.connect(self._edit_source_part)
        source_top.addWidget(source_edit_btn)
        source_del_btn = QPushButton('删除选中文档')
        source_del_btn.setObjectName('btn-danger')
        source_del_btn.setToolTip('从本条需求中移除选中文档，不删除电脑上的原文件。可用 Delete 键。')
        source_del_btn.clicked.connect(self._delete_source_parts)
        source_top.addWidget(source_del_btn)
        layout.addLayout(source_top)
        self.source_list = QListWidget(); self.source_list.setMaximumHeight(95)
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.itemDoubleClicked.connect(lambda _item: self._edit_source_part())
        self.source_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.source_list.customContextMenuRequested.connect(self._show_source_menu)
        self.source_list.installEventFilter(self)
        layout.addWidget(self.source_list)

        sql_top = QHBoxLayout()
        sql_top.addWidget(QLabel('需求相关 SQL'))
        sql_top.addStretch()
        load_btn = QPushButton('上传多个 SQL')
        load_btn.clicked.connect(self._load_sql)
        sql_top.addWidget(load_btn)
        sql_del_btn = QPushButton('删除选中 SQL')
        sql_del_btn.setObjectName('btn-danger')
        sql_del_btn.setToolTip('从本条需求中移除选中 SQL，不删除电脑上的原文件。可用 Delete 键。')
        sql_del_btn.clicked.connect(self._delete_sql_parts)
        sql_top.addWidget(sql_del_btn)
        layout.addLayout(sql_top)
        self.sql_list = QListWidget(); self.sql_list.setMaximumHeight(105)
        self.sql_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.sql_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sql_list.customContextMenuRequested.connect(self._show_sql_menu)
        self.sql_list.installEventFilter(self)
        self.sql_list.currentRowChanged.connect(self._sync_sql_system_combo)
        layout.addWidget(self.sql_list)
        sql_assign = QHBoxLayout()
        sql_assign.addWidget(QLabel('选中 SQL 归属'))
        self.sql_system_combo = QComboBox()
        size_combo(self.sql_system_combo, 'md')
        self.sql_system_combo.currentIndexChanged.connect(self._apply_sql_system)
        sql_assign.addWidget(self.sql_system_combo)
        sql_assign.addStretch(1)
        layout.addLayout(sql_assign)
        self.sql_paste = QPlainTextEdit()
        self.sql_paste.setPlaceholderText('也可以直接粘贴需求 SQL；保存后可一键发送到发版联动和接口 DOCX。')
        self.sql_paste.setMaximumHeight(95)
        layout.addWidget(self.sql_paste)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        from ui.dialog_buttons import localize_button_box
        localize_button_box(buttons, 'zh', Save='保存需求')
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_lists()

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

    def selected_systems(self):
        return self.system_pick.selected_names()

    def set_selected_systems(self, names):
        self.system_pick.set_selected_names(names)

    def _cache_binding_rows(self):
        for name, widgets in self._binding_rows.items():
            self._binding_cache[name] = {
                'svn_url': widgets['svn'].text().strip(),
                'dev_local_path': widgets['dev'].text().strip(),
            }

    def _rebuild_system_bindings(self):
        self._cache_binding_rows()
        while self.system_bindings_layout.count():
            item = self.system_bindings_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # 先隐藏：deleteLater 前不再以默认尺寸闪现
                widget.deleteLater()
        self._binding_rows = {}
        names = self.selected_systems()
        has_systems = bool(names)
        if names and not self._binding_rows:
            first = names[0]
            seeded = self._binding_cache.get(first) or {}
            if not str(seeded.get('svn_url') or '').strip():
                seeded['svn_url'] = self.svn_url_edit.text().strip()
            if not str(seeded.get('dev_local_path') or '').strip():
                seeded['dev_local_path'] = self.dev_local_path_edit.text().strip()
            self._binding_cache[first] = seeded
        self.svn_label.setVisible(not has_systems)
        self.svn_url_edit.setVisible(not has_systems)
        self.dev_label.setVisible(not has_systems)
        self.dev_local_path_edit.setVisible(not has_systems)
        self._standalone_dev_btn.setVisible(not has_systems)
        if hasattr(self, '_standalone_dev_clear'):
            self._standalone_dev_clear.setVisible(not has_systems)
        for name in names:
            cached = self._binding_cache.get(name) or {'svn_url': '', 'dev_local_path': ''}
            row = QWidget()
            box = QVBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 4)
            box.setSpacing(4)
            title = QLabel(f'{name} 的开发绑定')
            title.setObjectName('field-hint')
            box.addWidget(title)
            svn_edit = QLineEdit(cached.get('svn_url', ''))
            svn_edit.setProperty('editableField', True)
            size_line(svn_edit, 'path')
            svn_edit.setPlaceholderText('未配置则留空')
            box.addWidget(svn_edit)
            dev_row = QHBoxLayout()
            dev_edit = QLineEdit(cached.get('dev_local_path', ''))
            dev_edit.setProperty('editableField', True)
            size_line(dev_edit, 'path')
            dev_edit.setPlaceholderText('本机开发工程目录')
            pick_btn = QPushButton('选择目录')
            size_compact_button(pick_btn)
            pick_btn.clicked.connect(lambda _=False, editor=dev_edit: self._pick_path_into(editor))
            clear_btn = QPushButton('清除')
            size_compact_button(clear_btn)
            clear_btn.clicked.connect(dev_edit.clear)
            dev_row.addWidget(dev_edit, 1)
            dev_row.addWidget(pick_btn)
            dev_row.addWidget(clear_btn)
            box.addLayout(dev_row)
            self.system_bindings_layout.addWidget(row)
            self._binding_rows[name] = {'svn': svn_edit, 'dev': dev_edit}
        if hasattr(self, 'sql_system_combo'):
            self._refresh_sql_system_combo()

    def _pick_path_into(self, editor):
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_project_dir', editor.text().strip() or self.local_path_edit.text().strip())
        path = QFileDialog.getExistingDirectory(self, '选择本地开发项目目录', start)
        if path:
            remember_dialog_path('requirement_project_dir', path, is_directory=True)
            editor.setText(path)

    def _pick_dev_local_path(self):
        self._pick_path_into(self.dev_local_path_edit)

    def _current_binding_map(self):
        self._cache_binding_rows()
        names = self.selected_systems()
        result = {}
        for name in names:
            cached = self._binding_cache.get(name) or {}
            result[name] = {
                'svn_url': str(cached.get('svn_url') or '').strip(),
                'dev_local_path': str(cached.get('dev_local_path') or '').strip(),
            }
        return result

    def _refresh_sql_system_combo(self):
        if not hasattr(self, 'sql_system_combo'):
            return
        current = self.sql_system_combo.currentData()
        self.sql_system_combo.blockSignals(True)
        self.sql_system_combo.clear()
        self.sql_system_combo.addItem('未指定', '')
        for name in self.selected_systems():
            self.sql_system_combo.addItem(name, name)
        index = self.sql_system_combo.findData(current)
        self.sql_system_combo.setCurrentIndex(index if index >= 0 else 0)
        self.sql_system_combo.blockSignals(False)
        self._sync_sql_system_combo()

    def _sync_sql_system_combo(self, _row=None):
        if not hasattr(self, 'sql_system_combo'):
            return
        row = self.sql_list.currentRow()
        self.sql_system_combo.blockSignals(True)
        if 0 <= row < len(self._sql_parts):
            assigned = str(self._sql_parts[row].get('system') or '').strip()
            index = self.sql_system_combo.findData(assigned)
            self.sql_system_combo.setCurrentIndex(index if index >= 0 else 0)
        self.sql_system_combo.blockSignals(False)

    def _apply_sql_system(self):
        row = self.sql_list.currentRow()
        if row < 0 or row >= len(self._sql_parts):
            return
        self._sql_parts[row]['system'] = self.sql_system_combo.currentData() or ''
        self._refresh_lists('sql')
        self.sql_list.setCurrentRow(row)

    def _clear_local_folder(self):
        self.local_path_edit.clear()

    def _bind_local_folder(self):
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_svn_wc', self.local_path_edit.text())
        path = QFileDialog.getExistingDirectory(self, '绑定需求的 SVN 工作副本或资料目录', start)
        if not path:
            return
        remember_dialog_path('requirement_svn_wc', path, is_directory=True)
        self.local_path_edit.setText(path)
        if os.path.isdir(os.path.join(path, '.svn')):
            try:
                info = working_copy_info(path)
                # 仅当用户尚未填写开发分支时才回填；未配置则保持空
                if info.get('svn_url') and not self.svn_url_edit.text().strip():
                    self.svn_url_edit.setText(info['svn_url'])
            except SvnError as exc:
                show_warning(self, '绑定 SVN 目录', f'目录已绑定，但 SVN 信息读取失败：\n{exc}')

    def _load_documents(self):
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_doc_attach')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择需求相关文档', start,
            '需求文档 (*.docx *.xlsx *.txt *.md *.json *.xml *.yaml *.yml *.csv)'
        )
        if not paths:
            return
        remember_dialog_path('requirement_doc_attach', paths)
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
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_sql_attach')
        paths, _ = QFileDialog.getOpenFileNames(self, '选择需求 SQL', start, 'SQL 文件 (*.sql *.txt)')
        if not paths:
            return
        remember_dialog_path('requirement_sql_attach', paths)
        for path in paths:
            try:
                self._sql_parts.append({'name': os.path.basename(path), 'content': read_text_file(path)})
            except OSError as exc:
                show_warning(self, '需求 SQL', f'{os.path.basename(path)} 读取失败：{exc}')
        if paths:
            self.has_sql.setChecked(True)
        self._refresh_lists()

    def _refresh_lists(self, which=None):
        if which in (None, 'source'):
            self.source_list.clear()
            for part in self._source_files:
                file_type = part.get('file_type') or '文本'
                size = f"{part.get('row_count', 0)} 行 × {part.get('column_count', 0)} 列" if part.get('content_type') == 'workbook_sheet' else f"{len(part.get('content', ''))} 字符"
                self.source_list.addItem(f"[{file_type}] {part.get('name')} · {size}")
        if which in (None, 'sql'):
            self.sql_list.clear()
            for part in self._sql_parts:
                assigned = str(part.get('system') or '').strip() or '未指定'
                item = QListWidgetItem(f"[{assigned}] {part.get('name')} · {len(part.get('content', ''))} 字符")
                item.setToolTip(part.get('content', '')[:1000])
                self.sql_list.addItem(item)
            if hasattr(self, 'sql_system_combo'):
                self._refresh_sql_system_combo()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if obj is self.source_list:
                self._delete_source_parts()
                return True
            if obj is self.sql_list:
                self._delete_sql_parts()
                return True
        return super().eventFilter(obj, event)

    def _selected_list_rows(self, list_widget):
        rows = sorted({index.row() for index in list_widget.selectedIndexes()})
        if rows:
            return rows
        current = list_widget.currentRow()
        return [current] if current >= 0 else []

    def _show_source_menu(self, point):
        item = self.source_list.itemAt(point)
        if item and not item.isSelected():
            self.source_list.clearSelection()
            item.setSelected(True)
            self.source_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('编辑选中附件', self._edit_source_part)
        menu.addAction('删除选中文档', self._delete_source_parts)
        menu.exec(self.source_list.mapToGlobal(point))

    def _show_sql_menu(self, point):
        item = self.sql_list.itemAt(point)
        if item and not item.isSelected():
            self.sql_list.clearSelection()
            item.setSelected(True)
            self.sql_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('删除选中 SQL', self._delete_sql_parts)
        menu.exec(self.sql_list.mapToGlobal(point))

    def _delete_source_parts(self):
        rows = self._selected_list_rows(self.source_list)
        if not rows:
            show_info(self, '删除文档', '请先选择要删除的文档。')
            return
        names = '\n'.join(f"• {self._source_files[row].get('name') or '未命名'}" for row in rows[:8])
        if len(rows) > 8:
            names += f'\n• 另外 {len(rows) - 8} 项……'
        if not confirm_action(
            self,
            f'删除选中的 {len(rows)} 个文档',
            f'即将从本条需求中移除：\n\n{names}\n\n只移除需求里的附件记录，不会删除电脑上的原文件。\n保存需求后生效。',
            confirm_text=f'删除 {len(rows)} 个文档',
            danger=True,
        ):
            return
        for row in reversed(rows):
            del self._source_files[row]
        self._refresh_lists('source')

    def _delete_sql_parts(self):
        rows = self._selected_list_rows(self.sql_list)
        if not rows:
            show_info(self, '删除 SQL', '请先选择要删除的 SQL。')
            return
        names = '\n'.join(f"• {self._sql_parts[row].get('name') or '未命名'}" for row in rows[:8])
        if len(rows) > 8:
            names += f'\n• 另外 {len(rows) - 8} 项……'
        if not confirm_action(
            self,
            f'删除选中的 {len(rows)} 条 SQL',
            f'即将从本条需求中移除：\n\n{names}\n\n只移除需求里的 SQL 记录，不会删除电脑上的原文件。\n保存需求后生效。',
            confirm_text=f'删除 {len(rows)} 条 SQL',
            danger=True,
        ):
            return
        for row in reversed(rows):
            del self._sql_parts[row]
        self._refresh_lists('sql')

    def _edit_source_part(self):
        row = self.source_list.currentRow()
        if row < 0 or row >= len(self._source_files):
            show_info(self, '附件编辑器', '请先选择一个附件。'); return
        dialog = RequirementAttachmentDialog(self._source_files[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._source_files[row] = dialog.entry(); self._refresh_lists('source')

    def _sync_test_points_description(self):
        if hasattr(self, 'test_points_editor'):
            self.test_points_editor.set_description(self.description_edit.toPlainText())

    def _classify(self):
        content = self.description_edit.toPlainText()
        seed = {
            'title': self.title_edit.text(),
            'code': self.code_edit.text(),
            'description': content,
            'svn_url': self.svn_url_edit.text(),
            'local_path': self.local_path_edit.text(),
            'system': (self.selected_systems() or [''])[0],
            'systems': self.selected_systems(),
            'sql_parts': list(self._sql_parts),
            'source_files': list(self._source_files),
            'has_sql': self.has_sql.isChecked(),
            'needs_peripheral_upgrade': self.peripheral.isChecked(),
            'temporary_upgrade': self.temporary.isChecked(),
            'needs_interface_update': self.interface_update.isChecked(),
            'category': self.category_combo.currentText(),
        }
        inferred = apply_auto_inference(seed, systems=load_systems(), only_empty=True)
        self.category_combo.setCurrentIndex(max(0, self.category_combo.findText(
            inferred.get('category') or classify_requirement(content)
        )))
        if not self.selected_systems() and requirement_systems(inferred):
            self.set_selected_systems(requirement_systems(inferred))
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
        bindings = self._current_binding_map()
        for name, bound in bindings.items():
            dev_path = bound.get('dev_local_path') or ''
            if dev_path and not os.path.isdir(dev_path):
                if not confirm_action(
                    self, '本地开发项目地址',
                    f'{name} 的本地开发目录不存在：\n{dev_path}\n\n是否清空该地址并继续保存其他字段？',
                    confirm_text='清空并继续',
                    danger=False,
                ):
                    return
                bound['dev_local_path'] = ''
                self._binding_cache[name] = bound
                widgets = self._binding_rows.get(name)
                if widgets:
                    widgets['dev'].clear()
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
        dev_path = self.dev_local_path_edit.text().strip()
        if dev_path and not os.path.isdir(dev_path):
            if not confirm_action(
                self, '本地开发项目地址',
                f'本地开发目录不存在：\n{dev_path}\n\n是否清空该地址并继续保存其他字段？',
                confirm_text='清空并继续',
                danger=False,
            ):
                self.dev_local_path_edit.setFocus()
                return
            self.dev_local_path_edit.clear()
        if not self.actual_date.is_valid():
            show_warning(self, '实际上线', '实际上线日期格式不正确，请按输入框提示手动录入，或点击“选日期”。')
            self.actual_date.edit.setFocus(); return
        pasted = self.sql_paste.toPlainText().strip()
        if pasted:
            self._sql_parts.append({'name': '直接粘贴.sql', 'content': pasted})
            self.has_sql.setChecked(True)
        self.accept()

    def values(self):
        actual_date = self._normalize_date_text(self.actual_date.text())
        online_month = actual_date[:7] if actual_date else ''
        local_path = self.local_path_edit.text().strip()
        names = self.selected_systems()
        bindings = self._current_binding_map()
        if names:
            primary = bindings.get(names[0]) or {}
            # 已选系统时以各系统绑定为准，空字符串表示用户主动清空，不得用隐藏的独立栏回填
            svn_url = str(primary.get('svn_url') or '').strip()
            dev_local_path = str(primary.get('dev_local_path') or '').strip()
        else:
            svn_url = self.svn_url_edit.text().strip()
            dev_local_path = self.dev_local_path_edit.text().strip()
        payload = {
            'code': self.code_edit.text().strip(), 'title': self.title_edit.text().strip(),
            'record_kind': self.kind_combo.currentText(),
            'description': self.description_edit.toPlainText().strip(),
            'test_points': self.test_points_editor.points() if hasattr(self, 'test_points_editor') else [],
            'category': self.category_combo.currentText(), 'status': self.status_combo.currentText(),
            'priority': self.priority_combo.currentText(),
            'system': names[0] if names else '',
            'systems': names,
            'system_bindings': bindings,
            'owner': self.owner_edit.text().strip(),
            'svn_url': svn_url,
            'dev_local_path': dev_local_path,
            'local_path': local_path,
            'workspace_kind': (
                'folder' if local_path and not os.path.isdir(os.path.join(local_path, '.svn'))
                else ('svn' if local_path else '')
            ),
            'actual_release_date': actual_date,
            'actual_online_date': actual_date,
            'online_month': online_month,
            'has_sql': bool(self.has_sql.isChecked() or self._sql_parts),
            'needs_peripheral_upgrade': bool(self.peripheral.isChecked()),
            'temporary_upgrade': bool(self.temporary.isChecked()),
            'needs_interface_update': bool(self.interface_update.isChecked()),
            'sql_parts': list(self._sql_parts),
            'source_files': list(self._source_files),
        }
        if not local_path:
            clear_workspace_binding(payload)
        return sync_system_fields(payload)


class RequirementPanel(QWidget):
    requirement_saved = pyqtSignal(object)
    requirements_changed = pyqtSignal()  # 删除/扫描/置顶等批量变更后刷新工作台
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
        self._pending_scan_folder = None
        self._pending_scan_dispatch_scheduled = False
        self._task_failed = False
        self._task_shows_loading = False
        self._file_entries_cache = []
        self._file_sort_column = 0
        self._file_sort_order = Qt.SortOrder.AscendingOrder
        self._search_expand_snapshot = None
        self._sources_stamp = None
        self._setup_ui(); self.set_language(language); self._refresh()
        self._clamp_file_library_action_heights()
        self._sources_stamp = self._current_sources_stamp()

    def _sync_toolbar_more_menu(self):
        """同步更多菜单中的各项操作状态。"""
        has_active_worker = self._active_worker is not None
        for name in ('_action_bug', '_action_scan', '_action_checkout'):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(not has_active_worker)
        if hasattr(self, '_action_import'):
            self._action_import.setEnabled(not has_active_worker)
        if hasattr(self, '_action_syscfg'):
            self._action_syscfg.setEnabled(True)

    def _setup_ui(self):
        from ui.layout_metrics import REQ_LEFT_MIN, REQ_RIGHT_MIN, SPACING_PAGE, TABLE_ROW_H
        from panels.requirement_views import StageBar
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self._page_root_layout = root

        view_prefs = (load_last_choices().get('requirement_view') or {})
        self._group_mode = 'month' if view_prefs.get('group') == 'month' else 'status'
        self._status_group_filter = ''

        # 唯一工具条：搜索与筛选 | 拉取 / 新建 / 提签
        toolbar_card = QFrame()
        toolbar_card.setObjectName('page-toolbar')
        toolbar_card.setProperty('card', True)
        toolbar_layout = QHBoxLayout(toolbar_card)
        toolbar_layout.setContentsMargins(12, 8, 12, 8)
        toolbar_layout.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setProperty('editableField', True)
        self.search_edit.setPlaceholderText('搜编号、标题、系统（支持拼音/首字母）')
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True)
        self._req_search_timer = QTimer(self)
        self._req_search_timer.setSingleShot(True)
        self._req_search_timer.setInterval(150)
        self._req_search_timer.timeout.connect(self._refresh)
        self.search_edit.textChanged.connect(lambda *_: self._req_search_timer.start())
        toolbar_layout.addWidget(self.search_edit, 1)

        self.month_filter = QComboBox(); self.month_filter.setObjectName('req-month-filter')
        self.month_filter.setToolTip('按上线月份筛选')
        self.month_filter.currentIndexChanged.connect(self._on_filter_changed)
        self.system_filter = QComboBox(); self._fill_system_filter(); self.system_filter.currentIndexChanged.connect(self._on_filter_changed)
        self.kind_filter = QComboBox(); self.kind_filter.addItems(('全部类型', '需求', 'BUG')); self.kind_filter.currentIndexChanged.connect(self._on_filter_changed)
        # 精确状态筛选只作为定位/兼容状态保留；可见入口是下方状态统计
        self.status_filter = QComboBox(self); self.status_filter.addItems(('全部状态',) + STATUSES); self.status_filter.currentIndexChanged.connect(self._on_filter_changed)
        self.status_filter.hide()
        size_combo(self.month_filter, 'sm'); size_combo(self.system_filter, 'md'); size_combo(self.kind_filter, 'sm'); size_combo(self.status_filter, 'sm')
        # 筛选只占必要宽度，把空间留给搜索框
        for combo, width in ((self.month_filter, 144), (self.system_filter, 156), (self.kind_filter, 134)):
            size_pick_combo(combo, width)
        toolbar_layout.addWidget(self.month_filter)
        toolbar_layout.addWidget(self.system_filter)
        toolbar_layout.addWidget(self.kind_filter)

        self.update_all_btn = QPushButton('拉取最新', toolbar_card)
        self.update_all_btn.setToolTip('依次 SVN update 台账里全部已绑定的需求目录，并刷新文件数')
        self.update_all_btn.clicked.connect(self._update_all)
        self.add_btn = QPushButton('新建需求', toolbar_card)
        self.add_btn.setObjectName('primary-btn')
        self.add_btn.clicked.connect(self._add_requirement)
        self.toolbar_more_btn = QPushButton('更多', toolbar_card)
        self.toolbar_more_btn.setToolTip('登记缺陷、扫描目录、检出代码、导入资料、系统配置')
        self.toolbar_more_menu = QMenu(self.toolbar_more_btn)
        self._action_bug = self.toolbar_more_menu.addAction('登记缺陷（粘贴 BUG）', self._paste_bug)
        self._action_scan = self.toolbar_more_menu.addAction('扫描需求目录…', self._scan_folder)
        self._action_checkout = self.toolbar_more_menu.addAction('检出代码…', self._checkout_svn)
        self._action_import = self.toolbar_more_menu.addAction('导入资料…', self._import_requirement)
        self.toolbar_more_menu.addSeparator()
        self._action_syscfg = self.toolbar_more_menu.addAction('系统配置', self.open_system_config.emit)
        self.toolbar_more_menu.aboutToShow.connect(self._sync_toolbar_more_menu)
        self.toolbar_more_btn.setMenu(self.toolbar_more_menu)
        self.ticket_btn = QPushButton('一键提签', toolbar_card)
        self.ticket_btn.setToolTip('把选中的需求写进环境签文档，并提交到签库 SVN')
        self.ticket_btn.clicked.connect(self._open_ticket_submit)
        for button in (self.update_all_btn, self.toolbar_more_btn, self.ticket_btn, self.add_btn):
            size_compact_button(button)
            toolbar_layout.addWidget(button)
        try:
            from ui.icons import apply_icon
            apply_icon(self.update_all_btn, 'refresh', 16)
        except Exception:
            pass
        root.addWidget(toolbar_card)
        self.loading = AuroraProgress(self)

        # 进度统计：既是概览，也是状态筛选入口
        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        strip.setSpacing(6)
        self.status_strip_layout = QHBoxLayout()
        self.status_strip_layout.setSpacing(6)
        self._status_chips = {}
        strip.addLayout(self.status_strip_layout)
        strip.addStretch(1)
        # 仅在扫描/SVN 任务运行时显示
        self.svn_activity = QLabel('')
        self.svn_activity.setObjectName('small-label')
        self.svn_activity.hide()
        strip.addWidget(self.svn_activity)
        self.group_status_btn = QPushButton('按状态')
        self.group_month_btn = QPushButton('按月份')
        for button, mode in ((self.group_status_btn, 'status'), (self.group_month_btn, 'month')):
            button.setObjectName('req-group-toggle')
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=mode: self._set_group_mode(value))
            strip.addWidget(button)
        self._sync_group_buttons()
        root.addLayout(strip)

        self.detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.detail_splitter.setObjectName('requirement-splitter')
        self.detail_splitter.setHandleWidth(16)
        self.detail_splitter.setProperty('prismGutter', True)
        self.detail_splitter.setChildrenCollapsible(False)
        self.detail_splitter.setOpaqueResize(True)
        left = QFrame(); left.setObjectName('req-tree-card'); left_layout = QVBoxLayout(left)
        left.setMinimumWidth(REQ_LEFT_MIN)
        left_layout.setContentsMargins(6, 6, 6, 6)
        left_layout.setSpacing(6)
        # 多选时才出现的批量操作条
        self.selection_bar = QFrame()
        self.selection_bar.setObjectName('req-selection-bar')
        selection_layout = QHBoxLayout(self.selection_bar)
        selection_layout.setContentsMargins(8, 4, 8, 4)
        selection_layout.setSpacing(8)
        self.selection_label = QLabel('')
        self.selection_label.setObjectName('small-label')
        selection_layout.addWidget(self.selection_label, 1)
        self.batch_delete_btn = QPushButton('删除')
        self.batch_delete_btn.setObjectName('ops-delete-custom')
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.setToolTip('删除选中的需求/BUG（也可按 Delete）')
        self.batch_delete_btn.clicked.connect(self._delete_requirement)
        size_compact_button(self.batch_delete_btn)
        selection_layout.addWidget(self.batch_delete_btn)
        self.selection_bar.hide()
        left_layout.addWidget(self.selection_bar)
        # 兼容旧计数引用：数字已在状态统计里展示
        self.tree_count_label = QLabel('')
        self.tree_count_label.hide()

        self.requirement_list = RequirementTree(); self.requirement_list.setObjectName('requirement-tree')
        self.requirement_list.setHeaderHidden(True)
        self._tree_expand_state = {}
        self._search_expand_snapshot = None
        self.requirement_list.setColumnCount(4)
        self.requirement_list.setRootIsDecorated(True)
        self.requirement_list.setItemsExpandable(True)
        self.requirement_list.setExpandsOnDoubleClick(True)
        self.requirement_list.setIndentation(12)
        self.requirement_list.setUniformRowHeights(True)
        self.requirement_list.setWordWrap(False)
        self.requirement_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.requirement_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.requirement_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree_header = self.requirement_list.header()
        tree_header.setStretchLastSection(False)
        tree_header.setMinimumSectionSize(24)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        tree_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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
        right.setMinimumWidth(REQ_RIGHT_MIN)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(8)

        # 需求摘要：编号/系统/上线 → 标题 → 进度 → 快捷动作 → 完成标记
        self.detail_card = QFrame()
        self.detail_card.setObjectName('detail-summary-card')
        self.detail_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card = QVBoxLayout(self.detail_card)
        card.setContentsMargins(14, 10, 14, 12)
        card.setSpacing(6)
        head = QHBoxLayout(); head.setSpacing(8)
        self.detail_eyebrow = QLabel('')
        self.detail_eyebrow.setObjectName('field-caption')
        self.detail_eyebrow.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head.addWidget(self.detail_eyebrow, 1)
        self.status_combo = QComboBox()
        self.status_combo.setObjectName('req-status-combo')
        self.status_combo.addItems(STATUSES)
        self.status_combo.setToolTip('直接修改进度状态，立即保存')
        self.status_combo.currentTextChanged.connect(self._on_status_combo_changed)
        size_pick_combo(self.status_combo, 132)
        head.addWidget(self.status_combo)
        self.edit_btn = QPushButton('编辑')
        self.edit_btn.setProperty('compactAction', True)
        self.edit_btn.setToolTip('编辑完整信息：系统、日期、SVN、附件、SQL')
        self.edit_btn.clicked.connect(self._edit_requirement)
        size_compact_button(self.edit_btn)
        head.addWidget(self.edit_btn)
        card.addLayout(head)
        self.detail_title = QLabel('请选择左侧需求')
        self.detail_title.setObjectName('section-title')
        self.detail_title.setWordWrap(True)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.addWidget(self.detail_title)
        self.stage_bar = StageBar()
        card.addWidget(self.stage_bar)
        self.stage_caption = QLabel('')
        self.stage_caption.setObjectName('field-caption')
        self.stage_caption.setTextFormat(Qt.TextFormat.RichText)
        card.addWidget(self.stage_caption)
        self.stage_caption.hide()  # 步骤条已显示阶段名与细状态

        # 快捷动作：日常最常用的四个，复用既有处理函数
        quick = QHBoxLayout(); quick.setSpacing(6)
        self.quick_actions_layout = quick
        self.open_dev_btn = QPushButton('开发目录')
        self.open_dev_btn.setToolTip('打开本地开发工程目录')
        self.open_dev_btn.clicked.connect(lambda: self._open_dev_project_folder(self._current))
        self.quick_update_btn = QPushButton('SVN 更新')
        self.quick_update_btn.setToolTip('更新当前需求的资料工作副本')
        self.quick_update_btn.clicked.connect(self._update_current)
        self.quick_commit_btn = QPushButton('提交')
        self.quick_commit_btn.setToolTip('选中了文件就提交选中项，否则提交这条需求目录的全部改动')
        self.quick_commit_btn.clicked.connect(self._commit_current_changes)
        self.daily_btn = QPushButton('写日报'); self.daily_btn.clicked.connect(self._send_daily)
        self.quick_buttons = {
            'open_dev_btn': self.open_dev_btn,
            'quick_update_btn': self.quick_update_btn,
            'quick_commit_btn': self.quick_commit_btn,
            'daily_btn': self.daily_btn,
        }
        try:
            from ui.icons import apply_icon
            apply_icon(self.open_dev_btn, 'folder-open', 16)
            apply_icon(self.quick_commit_btn, 'export', 16)
        except Exception:
            pass
        for button in self.quick_buttons.values():
            size_compact_button(button)
            quick.addWidget(button)
        self.detail_more_btn = QPushButton('更多')
        size_compact_button(self.detail_more_btn)
        self.detail_more_menu = QMenu(self.detail_more_btn)
        self._action_open_folder = self.detail_more_menu.addAction('打开需求资料目录', self._open_folder)
        self._action_open_dev = self.detail_more_menu.addAction('打开本地开发目录', lambda: self._open_dev_project_folder(self._current))
        self._action_daily = self.detail_more_menu.addAction('写入日报', self._send_daily)
        self._action_release = self.detail_more_menu.addAction('准备本次升级', lambda: self.open_release_prep.emit(self._current or {}))
        self.detail_more_menu.addSeparator()
        self._action_rename = self.detail_more_menu.addAction('修改标题', self._rename_requirement_title)
        self._action_delete = self.detail_more_menu.addAction('删除需求', self._delete_requirement)
        self.detail_more_btn.setMenu(self.detail_more_menu)
        quick.addWidget(self.detail_more_btn)
        quick.addStretch(1)
        card.addLayout(quick)

        # 兼容旧引用
        self._detail_fields = {}
        self._detail_captions = {}
        self.detail_scroll = None
        self.detail_body = self.detail_card

        # 完成标记：最多四个单行；禁止 setFixedHeight 压扁，否则 1px 下边框会被裁掉
        self.flag_section = QWidget()
        self.flag_section.setObjectName('flag-section')
        self.flag_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        flag_section_layout = QHBoxLayout(self.flag_section)
        flag_section_layout.setContentsMargins(0, 2, 0, 2)
        flag_section_layout.setSpacing(8)
        self.flag_section_caption = QLabel('上线事项')
        self.flag_section_caption.setObjectName('flag-section-caption')
        flag_section_layout.addWidget(self.flag_section_caption)
        self.flag_chips = QWidget()
        self.flag_chips.setObjectName('flag-chips-host')
        self.flag_chips.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.flag_chips_layout = QHBoxLayout(self.flag_chips)
        self.flag_chips_layout.setContentsMargins(1, 2, 1, 4)
        self.flag_chips_layout.setSpacing(8)
        self.flag_chips_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._flag_buttons = {}
        self._layouting_flags = False
        self._showing_requirement = False
        self._refreshing_tree = False
        for key, short, full in FLAG_DEFS:
            btn = QPushButton(short)
            btn.setObjectName('flag-chip')
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(30)
            btn.setMaximumHeight(34)
            btn.setMinimumWidth(72)
            btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            btn.setToolTip(f'{full} · 点击切换完成状态')
            btn.clicked.connect(lambda _checked=False, flag_key=key: self._on_flag_chip_clicked(flag_key))
            self._flag_buttons[key] = btn
            self.flag_chips_layout.addWidget(btn, 0)
            btn.hide()
        self.flag_chips_layout.addStretch(1)
        flag_section_layout.addWidget(self.flag_chips, 1)
        self.flag_section.hide()
        card.addWidget(self.flag_section, 0)
        self.detail_card.installEventFilter(self)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName('module-tabs')
        self.detail_tabs.setDocumentMode(True)
        self.detail_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail_tabs.setMinimumHeight(240)

        # —— 测试点 ——
        test_section = QFrame()
        test_section.setObjectName('req-test-points-card')
        test_layout = QVBoxLayout(test_section)
        test_layout.setContentsMargins(12, 10, 12, 10)
        test_layout.setSpacing(8)
        self.test_points_editor = TestPointsEditor(
            persist_callback=self._persist_test_points,
            compact=False,
            auto_seed=True,
            parent=self,
        )
        test_layout.addWidget(self.test_points_editor, 1)
        self._test_tab = test_section

        # —— 文件库 ——
        file_section = QFrame()
        file_section.setObjectName('req-file-card')
        file_layout = QVBoxLayout(file_section)
        file_layout.setContentsMargins(12, 10, 12, 10)
        file_layout.setSpacing(8)
        file_head = QHBoxLayout(); file_head.setSpacing(6)
        self.file_lib_title = QLabel('文件库'); self.file_lib_title.hide()
        self.file_lib_subtitle = QLabel(''); self.file_lib_subtitle.hide()

        self.open_folder_btn = QPushButton('打开目录')
        self.open_folder_btn.clicked.connect(self._open_folder)
        self.refresh_svn_btn = QPushButton('刷新')
        self.refresh_svn_btn.clicked.connect(self._refresh_file_tree)
        self.update_current_btn = QPushButton('更新')
        self.update_current_btn.clicked.connect(self._update_current)
        self.commit_btn = QPushButton('提交')
        self.commit_btn.setObjectName('primary-btn')
        self.commit_btn.clicked.connect(self._commit_svn)
        try:
            from ui.icons import apply_icon
            apply_icon(self.open_folder_btn, 'folder-open', 16)
            apply_icon(self.refresh_svn_btn, 'refresh', 16)
            apply_icon(self.commit_btn, 'export', 16)
        except Exception:
            pass
        # 低频文件操作只放菜单和右键，不常驻工具条
        self.add_file_btn = QPushButton('添加文件', self); self.add_file_btn.clicked.connect(self._add_existing_files); self.add_file_btn.hide()
        self.new_text_btn = QPushButton('新建文本', self); self.new_text_btn.clicked.connect(self._add_text_file); self.new_text_btn.hide()
        self.lock_file_btn = QPushButton('锁定', self); self.lock_file_btn.clicked.connect(self._lock_selected_file); self.lock_file_btn.hide()
        self.unlock_file_btn = QPushButton('解锁', self); self.unlock_file_btn.clicked.connect(self._unlock_selected_file); self.unlock_file_btn.hide()
        self.revert_btn = QPushButton('回滚', self); self.revert_btn.clicked.connect(self._revert_svn_paths); self.revert_btn.hide()
        self.file_library_action_buttons = (
            self.open_folder_btn, self.refresh_svn_btn, self.update_current_btn, self.commit_btn,
        )
        for button in self.file_library_action_buttons:
            size_compact_button(button)
            file_head.addWidget(button)
        self.file_more_btn = QPushButton('更多')
        size_compact_button(self.file_more_btn)
        self.file_more_menu = QMenu(self.file_more_btn)
        self._action_add_file = self.file_more_menu.addAction('添加文件…', self._add_existing_files)
        self._action_new_text = self.file_more_menu.addAction('新建文本…', self._add_text_file)
        self.file_more_menu.addSeparator()
        self._action_lock = self.file_more_menu.addAction('锁定选中文件', self._lock_selected_file)
        self._action_unlock = self.file_more_menu.addAction('解锁选中文件', self._unlock_selected_file)
        self._action_revert = self.file_more_menu.addAction('回滚选中改动', self._revert_svn_paths)
        self.file_more_menu.aboutToShow.connect(self._sync_file_more_menu)
        self.file_more_btn.setMenu(self.file_more_menu)
        file_head.addWidget(self.file_more_btn)
        file_head.addStretch(1)
        self.svn_meta = QLabel()
        self.svn_meta.setObjectName('svn-workspace-meta')
        self.svn_meta.setWordWrap(False)
        self.svn_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_head.addWidget(self.svn_meta)
        file_layout.addLayout(file_head)

        file_tools = QHBoxLayout()
        file_tools.setSpacing(8)
        self.file_search_edit = QLineEdit()
        self.file_search_edit.setProperty('editableField', True)
        self.file_search_edit.setClearButtonEnabled(True)
        self.file_search_edit.setPlaceholderText('搜文件名、路径、类型（支持拼音）')
        size_line(self.file_search_edit, 'search')
        self._file_search_timer = QTimer(self)
        self._file_search_timer.setSingleShot(True)
        self._file_search_timer.setInterval(150)
        self._file_search_timer.timeout.connect(self._filter_file_tree_local)
        self.file_search_edit.textChanged.connect(lambda *_: self._file_search_timer.start())
        file_tools.addWidget(self.file_search_edit, 1)
        self.file_count_label = QLabel('')
        self.file_count_label.setObjectName('small-label')
        file_tools.addWidget(self.file_count_label)
        file_layout.addLayout(file_tools)

        self.file_tree = QTreeWidget(); self.file_tree.setObjectName('requirement-file-tree')
        self.file_tree.setHeaderLabels(('名称', '类型', '修改时间', '大小', '路径'))
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setRootIsDecorated(True)
        self.file_tree.setItemsExpandable(True)
        self.file_tree.setExpandsOnDoubleClick(False)
        self.file_tree.setIndentation(16)
        self.file_tree.setUniformRowHeights(True)
        self.file_tree.setWordWrap(False)
        self.file_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.file_tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.file_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.file_tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.file_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_tree.setMinimumHeight(200)
        # 外部文件拖入工作副本
        self.file_tree.setAcceptDrops(True)
        self.file_tree.setDragEnabled(False)
        self.file_tree.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.file_tree.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.file_tree.viewport().setAcceptDrops(True)
        self.file_tree.viewport().installEventFilter(self)
        self.file_tree.installEventFilter(self)
        self._file_row_delegate = _ElideTextDelegate(self.file_tree, row_height=TABLE_ROW_H)
        self.file_tree.setItemDelegate(self._file_row_delegate)
        self._file_sort_column = 0
        self._file_sort_order = Qt.SortOrder.AscendingOrder
        self._file_entries_cache = []
        file_header = self.file_tree.header()
        file_header.setObjectName('requirement-file-header')
        file_header.setSectionsClickable(True)
        try:
            from ui.design_system import apply_list_header
            apply_list_header(file_header)
        except Exception:
            file_header.setHighlightSections(False)
        file_header.setSectionsMovable(True)
        file_header.setStretchLastSection(False)
        file_header.setMinimumSectionSize(40)
        file_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col in range(5):
            file_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        file_header.sectionClicked.connect(self._on_file_header_clicked)
        self.file_tree.setColumnWidth(0, 220)
        self.file_tree.setColumnWidth(1, 72)
        self.file_tree.setColumnWidth(2, 124)
        self.file_tree.setColumnWidth(3, 56)
        self.file_tree.setColumnWidth(4, 148)
        for index in range(self.file_tree.columnCount()):
            self.file_tree.headerItem().setToolTip(index, '可拖入本地文件 · 双击打开 · 右键更多操作')
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._show_file_menu)
        self.file_tree.itemDoubleClicked.connect(self._open_tree_item)
        self.file_tree.currentItemChanged.connect(self._update_vcs_action_state)
        self.file_tree.itemSelectionChanged.connect(self._update_vcs_action_state)
        file_layout.addWidget(self.file_tree, 1)
        self._file_tab = file_section

        # —— SQL 与联动 ——
        sql_section = QFrame()
        sql_section.setObjectName('req-sql-card')
        sql_layout = QVBoxLayout(sql_section)
        sql_layout.setContentsMargins(12, 10, 12, 10)
        sql_layout.setSpacing(8)
        sql_head = QHBoxLayout(); sql_head.setSpacing(8)
        self.sql_btn = QPushButton('打开发版联动'); self.sql_btn.clicked.connect(self._send_sql)
        self.docx_btn = QPushButton('更新接口文档'); self.docx_btn.clicked.connect(self._send_docx)
        self.release_link_btn = QPushButton('准备本次升级')
        self.release_link_btn.clicked.connect(lambda: self.open_release_prep.emit(self._current or {}))
        for button in (self.sql_btn, self.docx_btn, self.release_link_btn):
            size_compact_button(button)
            sql_head.addWidget(button)
        sql_head.addStretch(1)
        sql_layout.addLayout(sql_head)
        self.sql_preview = QPlainTextEdit()
        self.sql_preview.setProperty('readOnlyField', True)
        self.sql_preview.setReadOnly(True)
        self.sql_preview.setObjectName('ops-preview')
        self.sql_preview.setMaximumBlockCount(0)
        self.sql_preview.setMinimumHeight(56)
        self.sql_preview.setPlaceholderText('暂无 SQL 脚本 · 在「编辑」里上传或粘贴')
        sql_layout.addWidget(self.sql_preview, 1)
        self.sql_empty = QLabel('暂无 SQL 脚本 · 在「编辑」里上传或粘贴')
        self.sql_empty.setObjectName('field-hint')
        self.sql_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sql_empty.hide()
        sql_layout.addWidget(self.sql_empty)
        self._sql_tab = sql_section

        self.actions_card = QFrame(); self.actions_card.hide()

        detail.addWidget(self.detail_card, 0)
        detail.addWidget(self.detail_tabs, 1)
        self.file_sql_splitter = None
        self.detail_splitter.addWidget(right)
        self._arrange_detail_tabs()

        from ui.layout_metrics import REQ_LEFT_DEFAULT, REQ_LEFT_MIN, REQ_RIGHT_MIN
        requirement_ui = load_requirement_ui()
        sizes = requirement_ui.get('splitter_sizes') or [REQ_LEFT_DEFAULT, 820]
        if len(sizes) >= 2 and sizes[0] < REQ_LEFT_MIN:
            sizes = [REQ_LEFT_DEFAULT, max(REQ_RIGHT_MIN, sizes[1])]
        try:
            from ui.splitter_prefs import install_splitter_prefs, layout_bucket
            install_splitter_prefs(
                self.detail_splitter,
                defaults=[REQ_LEFT_DEFAULT, 820],
                saved=sizes,
                page_id='requirement',
                tab_id='tree-detail',
                bucket=layout_bucket('standard'),
                min_sizes=[REQ_LEFT_MIN, REQ_RIGHT_MIN],
                accessible_name='需求管理列表/详情分隔',
                on_changed=lambda _sizes: self._save_splitter_sizes(),
                double_click_reset=False,
            )
        except Exception:
            self.detail_splitter.setSizes(sizes)
            self._splitter_save_timer = QTimer(self)
            self._splitter_save_timer.setSingleShot(True)
            self._splitter_save_timer.setInterval(250)
            self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
            self.detail_splitter.splitterMoved.connect(
                lambda _position, _index: self._splitter_save_timer.start()
            )
        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setObjectName('requirement-workspace-scroll')
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setWidget(self.detail_splitter)
        root.addWidget(self.workspace_scroll, 1)
        self._fill_month_filter()
        self._restore_last_filters()

    # ---- 视角 / 分组 / 状态统计 ----

    def _save_view_prefs(self):
        update_last_choices(requirement_view={
            'group': self._group_mode,
            'month': self.month_filter.currentData() or '',
        })

    def _arrange_detail_tabs(self):
        tabs = self.detail_tabs
        while tabs.count():
            tabs.removeTab(0)
        for widget, label in ((self._file_tab, '文件库'), (self._test_tab, '测试点'), (self._sql_tab, 'SQL / 联动')):
            tabs.addTab(widget, label)
        tabs.setCurrentIndex(0)

    def _select_detail_tab(self, section, *, focus_input=False):
        widget = {'test_points': self._test_tab, 'files': self._file_tab, 'sql': self._sql_tab}.get(section)
        index = self.detail_tabs.indexOf(widget) if widget is not None else -1
        if index >= 0:
            self.detail_tabs.setCurrentIndex(index)
        if focus_input and section == 'test_points' and _is_alive(self.test_points_editor):
            add_edit = getattr(self.test_points_editor, 'add_edit', None)
            if add_edit is not None and _is_alive(add_edit):
                add_edit.setFocus()

    def _set_group_mode(self, mode):
        mode = 'month' if mode == 'month' else 'status'
        if mode != self._group_mode:
            self._group_mode = mode
            self._tree_expand_state = {}
            self._save_view_prefs()
            self._refresh()
        self._sync_group_buttons()

    def _sync_group_buttons(self):
        self.group_status_btn.setChecked(self._group_mode == 'status')
        self.group_month_btn.setChecked(self._group_mode == 'month')

    def _fill_month_filter(self):
        current = self.month_filter.currentData() if self.month_filter.count() else None
        if current is None:
            current = (load_last_choices().get('requirement_view') or {}).get('month') or ''
        months = sorted({str(item.get('online_month') or '').strip() for item in self._requirements} - {''}, reverse=True)
        self.month_filter.blockSignals(True)
        try:
            self.month_filter.clear()
            self.month_filter.addItem('全部月份', '')
            for month in months:
                self.month_filter.addItem(format_online_month_label(month), month)
            if any(not str(item.get('online_month') or '').strip() for item in self._requirements):
                self.month_filter.addItem('未分月', '__none__')
            index = self.month_filter.findData(current)
            self.month_filter.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.month_filter.blockSignals(False)

    def _on_status_chip_clicked(self, group_key):
        self._status_group_filter = '' if group_key == self._status_group_filter else group_key
        self._refresh()

    def _rebuild_status_strip(self, rows):
        """rows: 已按月份/系统/类型/搜索过滤、未按状态过滤的需求。"""
        from panels.requirement_views import STATUS_GROUP_LABELS, STATUS_GROUP_ORDER, status_group
        counts = {key: 0 for key in STATUS_GROUP_ORDER}
        for item in rows:
            counts[status_group(item.get('status'))] += 1
        layout = self.status_strip_layout
        while layout.count():
            child = layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.hide()  # 先隐藏：deleteLater 前不再以默认尺寸闪现
                widget.deleteLater()
        self._status_chips = {}
        entries = [('', f'全部 {len(rows)}')]
        entries.extend((key, f'{STATUS_GROUP_LABELS[key]} {counts[key]}') for key in STATUS_GROUP_ORDER if counts[key] or key == self._status_group_filter)
        for key, text in entries:
            chip = QPushButton(text)
            chip.setObjectName('req-stat-chip')
            chip.setCheckable(True)
            chip.setChecked(key == self._status_group_filter)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setProperty('tone', key or 'all')
            chip.setToolTip('点击只看该状态，再点一次取消' if key else '显示全部状态')
            chip.clicked.connect(lambda _checked=False, value=key: self._on_status_chip_clicked(value))
            layout.addWidget(chip)
            self._status_chips[key] = chip

    def _on_status_combo_changed(self, status):
        if getattr(self, '_showing_requirement', False) or not isinstance(self._current, dict):
            return
        if not status or status == (self._current.get('status') or '待分析'):
            return
        self._set_requirement_status([self._current.get('id')], status)

    def _set_requirement_status(self, requirement_ids, status):
        ids = {str(value) for value in (requirement_ids or []) if value}
        if not ids or status not in STATUSES:
            return False
        candidate = copy.deepcopy(self._requirements)
        now = datetime.datetime.now().isoformat(timespec='seconds')
        changed = False
        for item in candidate:
            if isinstance(item, dict) and item.get('id') in ids and item.get('status') != status:
                item['status'] = status
                item['updated_at'] = now
                changed = True
        if not changed:
            return False
        if not self._save_requirements_candidate(candidate, error_message='无法写入进度状态'):
            self._refresh()
            return False
        if isinstance(self._current, dict):
            self._current = next((item for item in self._requirements if item.get('id') == self._current.get('id')), None)
        self._refresh()
        self.requirement_saved.emit(self._current or {})
        self.requirements_changed.emit()
        return True

    def _content_stack_sizes(self):
        """配置兼容字段：上区实际高度 + 估算剩余。"""
        top_h = 0
        if hasattr(self, 'detail_card') and self.detail_card is not None:
            top_h = self.detail_card.height() or self.detail_card.sizeHint().height()
        parent_h = 0
        if self.detail_card is not None and self.detail_card.parentWidget() is not None:
            parent_h = self.detail_card.parentWidget().height()
        return normalize_content_splitter_sizes(top_h=top_h or 160, total_h=parent_h or 800)

    def _save_splitter_sizes(self):
        save_requirement_ui({
            'splitter_sizes': self.detail_splitter.sizes(),
            'content_splitter_sizes': self._content_stack_sizes(),
        })

    def _sync_file_more_menu(self):
        """同步文件库 More 菜单中 Actions 的启用与可见状态。"""
        if hasattr(self, '_action_new_text') and hasattr(self, 'new_text_btn'):
            self._action_new_text.setEnabled(self.new_text_btn.isEnabled())
            self._action_new_text.setVisible(not self.new_text_btn.isVisible())
        if hasattr(self, '_action_lock') and hasattr(self, 'lock_file_btn'):
            self._action_lock.setEnabled(self.lock_file_btn.isEnabled())
            self._action_lock.setVisible(not self.lock_file_btn.isVisible())
        if hasattr(self, '_action_unlock') and hasattr(self, 'unlock_file_btn'):
            self._action_unlock.setEnabled(self.unlock_file_btn.isEnabled())
            self._action_unlock.setVisible(not self.unlock_file_btn.isVisible())
        if hasattr(self, '_action_revert') and hasattr(self, 'revert_btn'):
            self._action_revert.setEnabled(self.revert_btn.isEnabled())
            self._action_revert.setVisible(not self.revert_btn.isVisible())

    def _clamp_file_library_action_heights(self):
        """文件库操作栏已升级为响应式 Action Bar（高频直显+低频 More 菜单），无需滚动条高度夹紧。"""
        pass

    def apply_default_splitter_sizes(self):
        """套用 requirement_ui 默认/已复位的左右分栏尺寸。"""
        if not hasattr(self, 'detail_splitter') or self.detail_splitter is None:
            return
        from ui.layout_metrics import REQ_LEFT_DEFAULT, REQ_LEFT_MIN, REQ_RIGHT_MIN
        requirement_ui = load_requirement_ui()
        sizes = requirement_ui.get('splitter_sizes') or [REQ_LEFT_DEFAULT, 780]
        if len(sizes) >= 2 and sizes[0] < REQ_LEFT_MIN:
            sizes = [REQ_LEFT_DEFAULT, max(REQ_RIGHT_MIN, sizes[1])]
        self.detail_splitter.setSizes(sizes)

    def apply_layout_mode(self, mode, low_height=False):
        """响应主窗口断点：只调整左右栏宽度和次要筛选的显隐。"""
        self._layout_mode = mode
        from ui.layout_metrics import REQ_LEFT_MAX, REQ_LEFT_MIN, REQ_RIGHT_MIN
        from ui.responsive import page_spacing_for_mode
        if hasattr(self, '_page_root_layout') and self._page_root_layout is not None:
            self._page_root_layout.setSpacing(min(8, page_spacing_for_mode(mode, low_height)))
        if not hasattr(self, 'detail_splitter'):
            return
        self.detail_splitter.setChildrenCollapsible(False)
        self.detail_splitter.setStretchFactor(0, 0)
        self.detail_splitter.setStretchFactor(1, 1)
        left_pane = self.detail_splitter.widget(0)
        right_pane = self.detail_splitter.widget(1)
        if mode == 'wide':
            left_min, right_min, left_def = REQ_LEFT_MIN, REQ_RIGHT_MIN, 460
        elif mode == 'compact':
            left_min, right_min, left_def = 280, 460, 340
        elif mode == 'narrow':
            left_min, right_min, left_def = 250, 360, 300
        else:  # standard
            left_min, right_min, left_def = 320, REQ_RIGHT_MIN, 420
        if left_pane is not None:
            left_pane.setMinimumWidth(left_min)
            left_pane.setMaximumWidth(REQ_LEFT_MAX if mode in ('wide', 'standard') else 16777215)
        if right_pane is not None:
            right_pane.setMinimumWidth(right_min)
        from ui.splitter_prefs import install_splitter_prefs, layout_bucket
        install_splitter_prefs(
            self.detail_splitter,
            defaults=[left_def, 820],
            page_id='requirement',
            tab_id='tree-detail',
            bucket=layout_bucket(mode),
            min_sizes=[left_min, right_min],
            accessible_name='需求管理列表/详情分隔',
            on_changed=lambda _sizes: self._save_splitter_sizes(),
            double_click_reset=False,
        )
        # 窄屏把系统/类型筛选收起，搜索与月份始终保留
        narrow = mode in ('compact', 'narrow')
        if hasattr(self, 'kind_filter'):
            self.kind_filter.setVisible(not narrow)
        if hasattr(self, 'system_filter'):
            self.system_filter.setVisible(mode != 'narrow')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading'):
            self.loading.place_overlay()
        # 标记按钮已常驻单行布局，resize 不再反复拆装，避免重入闪退

    @staticmethod
    def _tree_item_alive(item) -> bool:
        """PyQt 树节点在 clear/重建后可能变成已删除的 sip 包装。"""
        if item is None:
            return False
        try:
            from PyQt6 import sip
            if sip.isdeleted(item):
                return False
        except Exception:
            pass
        try:
            item.data(0, Qt.ItemDataRole.UserRole)
            return True
        except RuntimeError:
            return False

    def _layout_flag_chips(self):
        """完成标记最多 4 个，始终单行；仅同步 section 显隐，不拆装布局。"""
        if not hasattr(self, 'flag_chips_layout') or getattr(self, '_layouting_flags', False):
            return
        self._layouting_flags = True
        try:
            has_visible = any(not btn.isHidden() for btn in self._flag_buttons.values())
            if hasattr(self, 'flag_section'):
                self.flag_section.setVisible(has_visible)
        finally:
            self._layouting_flags = False

    def set_language(self, language):
        self.language = language

    def _restore_last_filters(self):
        # 精确状态筛选不跨会话恢复：它没有常驻控件，恢复后会让需求“凭空消失”
        last = (load_last_choices().get('requirement_filters') or {})
        system = last.get('system')
        if system:
            index = self.system_filter.findData(system)
            if index >= 0:
                self.system_filter.blockSignals(True)
                self.system_filter.setCurrentIndex(index)
                self.system_filter.blockSignals(False)
        kind = last.get('kind')
        if kind:
            index = self.kind_filter.findText(kind)
            if index >= 0:
                self.kind_filter.blockSignals(True)
                self.kind_filter.setCurrentIndex(index)
                self.kind_filter.blockSignals(False)

    def _on_filter_changed(self):
        update_last_choices(requirement_filters={
            'system': self.system_filter.currentData() or '',
            'kind': self.kind_filter.currentText(),
        })
        if hasattr(self, 'month_filter') and hasattr(self, 'group_month_btn'):
            self._save_view_prefs()
        self._refresh()

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

    @staticmethod
    def _file_mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _current_sources_stamp(self):
        return (self._file_mtime(SYSTEMS_FILE), self._file_mtime(REQUIREMENTS_FILE))

    def refresh_systems(self, force=False):
        stamp = self._current_sources_stamp()
        if not force and self._sources_stamp == stamp:
            return
        self._systems = load_systems()
        self._fill_system_filter()
        self._refresh()
        self._sources_stamp = stamp

    def refresh_theme(self):
        """重建需求树中按主题写入的月份、状态与搜索刷色，保留筛选和当前选择。"""
        self._refresh()

    def focus_requirement(self, requirement_or_id):
        """首页等入口：定位并选中指定需求。"""
        target_id = requirement_or_id
        if isinstance(requirement_or_id, dict):
            target_id = requirement_or_id.get('id')
        if not target_id:
            return False
        self._current = next((item for item in self._requirements if item.get('id') == target_id), None)
        if self._current is None:
            # 可能缓存未刷新
            self._requirements = load_requirements()
            self._current = next((item for item in self._requirements if item.get('id') == target_id), None)
        self._refresh()
        return self._current is not None

    def focus_requirement_section(self, req_id: str, section: str) -> bool:
        """定位指定需求并聚焦到指定模块（仅 completion / test_points）。"""
        if not isinstance(req_id, str) or not isinstance(section, str):
            return False
        target_id = req_id.strip()
        if not target_id or target_id.lower().startswith('demo-'):
            return False
        if section not in ('completion', 'test_points'):
            return False

        # 精确ID先在内存查，找不到可load_requirements一次但只有找到才替换
        target = next((item for item in self._requirements if isinstance(item, dict) and item.get('id') == target_id), None)
        if target is None:
            reloaded = load_requirements()
            found = next((item for item in reloaded if isinstance(item, dict) and item.get('id') == target_id), None)
            if found is not None:
                self._requirements = reloaded
                target = found

        if target is None:
            return False

        # 有效ID导航时阻断信号清空search和system/kind/status过滤
        if hasattr(self, 'search_edit') and self.search_edit is not None:
            was = self.search_edit.signalsBlocked() if hasattr(self.search_edit, 'signalsBlocked') else False
            self.search_edit.blockSignals(True)
            try:
                self.search_edit.clear()
            finally:
                self.search_edit.blockSignals(was)
        if hasattr(self, '_req_search_timer') and self._req_search_timer.isActive():
            self._req_search_timer.stop()

        if hasattr(self, 'system_filter') and self.system_filter is not None:
            was = self.system_filter.signalsBlocked() if hasattr(self.system_filter, 'signalsBlocked') else False
            self.system_filter.blockSignals(True)
            try:
                if self.system_filter.count() > 0:
                    self.system_filter.setCurrentIndex(0)
            finally:
                self.system_filter.blockSignals(was)

        if hasattr(self, 'kind_filter') and self.kind_filter is not None:
            was = self.kind_filter.signalsBlocked() if hasattr(self.kind_filter, 'signalsBlocked') else False
            self.kind_filter.blockSignals(True)
            try:
                if self.kind_filter.count() > 0:
                    self.kind_filter.setCurrentIndex(0)
            finally:
                self.kind_filter.blockSignals(was)

        if hasattr(self, 'status_filter') and self.status_filter is not None:
            was = self.status_filter.signalsBlocked() if hasattr(self.status_filter, 'signalsBlocked') else False
            self.status_filter.blockSignals(True)
            try:
                if self.status_filter.count() > 0:
                    self.status_filter.setCurrentIndex(0)
            finally:
                self.status_filter.blockSignals(was)

        # 设置_current→_refresh并校验树 currentItem 实际ID，不能被旧search/filters覆盖
        self._current = target
        self._refresh()

        cur_item = self.requirement_list.currentItem() if hasattr(self, 'requirement_list') else None
        cur_data = cur_item.data(0, Qt.ItemDataRole.UserRole) if (cur_item and self._tree_item_alive(cur_item)) else None
        if not isinstance(cur_data, dict) or cur_data.get('id') != target_id:
            def _find_item(root_item):
                for i in range(root_item.childCount()):
                    child = root_item.child(i)
                    if not self._tree_item_alive(child):
                        continue
                    d = child.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(d, dict) and d.get('id') == target_id:
                        return child
                    sub = _find_item(child)
                    if sub is not None:
                        return sub
                return None

            found_item = None
            if hasattr(self, 'requirement_list'):
                for i in range(self.requirement_list.topLevelItemCount()):
                    top = self.requirement_list.topLevelItem(i)
                    if not self._tree_item_alive(top):
                        continue
                    d = top.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(d, dict) and d.get('id') == target_id:
                        found_item = top
                        break
                    found_item = _find_item(top)
                    if found_item is not None:
                        break

            if found_item and self._tree_item_alive(found_item):
                was = self.requirement_list.signalsBlocked() if hasattr(self.requirement_list, 'signalsBlocked') else False
                self.requirement_list.blockSignals(True)
                try:
                    from ui.search_highlight import focus_tree_item
                    focus_tree_item(self.requirement_list, found_item)
                finally:
                    self.requirement_list.blockSignals(was)
                self._show_requirement(found_item, refresh_files=True)
            else:
                return False

        final_item = self.requirement_list.currentItem() if hasattr(self, 'requirement_list') else None
        final_data = final_item.data(0, Qt.ItemDataRole.UserRole) if (final_item and self._tree_item_alive(final_item)) else None
        if not isinstance(final_data, dict) or final_data.get('id') != target_id:
            return False

        # 代际标识更新，防止迟到的旧 timer 弹窗
        self._focus_request_token = getattr(self, '_focus_request_token', 0) + 1
        request_token = self._focus_request_token

        if section == 'completion':
            def _open_completion_dialog():
                if not _is_alive(self):
                    return
                if not self.isVisible():
                    return
                if getattr(self, '_focus_request_token', None) != request_token:
                    return
                if not isinstance(self._current, dict) or self._current.get('id') != target_id:
                    return
                self._save_dialog(self._current, offer_next=False, focus_section='completion')

            QTimer.singleShot(0, _open_completion_dialog)
            return True

        elif section == 'test_points':
            self._select_detail_tab('test_points', focus_input=True)

            def _refocus_test_points():
                if not _is_alive(self):
                    return
                if not self.isVisible():
                    return
                if getattr(self, '_focus_request_token', None) != request_token:
                    return
                if not isinstance(self._current, dict) or self._current.get('id') != target_id:
                    return
                self._select_detail_tab('test_points', focus_input=True)

            QTimer.singleShot(0, _refocus_test_points)
            return True

        return False

    def reload_requirements(self):
        """外部（如工作台标记上线）写过台账后，重新加载并刷新树。"""
        current_id = self._current.get('id') if isinstance(self._current, dict) else None
        self._requirements = load_requirements()
        if current_id:
            self._current = next((item for item in self._requirements if item.get('id') == current_id), None)
        self._refresh()
        self._sources_stamp = self._current_sources_stamp()

    def _refresh(self):
        if not hasattr(self, 'requirement_list'):
            return
        if getattr(self, '_refreshing_tree', False):
            return
        self._refreshing_tree = True
        self.requirement_list.setUpdatesEnabled(False)
        try:
            self._refresh_impl()
        finally:
            self.requirement_list.setUpdatesEnabled(True)
            self._refreshing_tree = False
            self._sources_stamp = self._current_sources_stamp()

    def _refresh_impl(self):
        from tools.pinyin_search import match_query
        from tools.list_pin import is_pinned, pinned_at_rank
        from panels.requirement_views import (
            COLLAPSED_GROUPS, STATUS_GROUP_LABELS, STATUS_GROUP_ORDER, status_group,
        )
        query = self.search_edit.text().strip()
        current_id = self._current.get('id') if self._current else None
        status = self.status_filter.currentText(); kind = self.kind_filter.currentText(); system = self.system_filter.currentData()
        month_filter = self.month_filter.currentData() if hasattr(self, 'month_filter') else ''
        # 完整重建树前记录用户当前浏览上下文。搜索有独立快照，以便清空搜索后恢复搜索前状态。
        refresh_expand_snapshot = self._capture_tree_expand_state()
        if query and self._search_expand_snapshot is None:
            self._search_expand_snapshot = refresh_expand_snapshot
        # 阻断 clear 触发的 currentItemChanged，避免访问已删除节点闪退
        self.requirement_list.blockSignals(True)
        try:
            self.requirement_list.clear()
        finally:
            self.requirement_list.blockSignals(False)
        scoped = []
        for requirement in self._requirements:
            if query and not match_query(requirement_search_text(requirement), query):
                continue
            if kind != '全部类型' and requirement.get('record_kind', '需求') != kind: continue
            if status != '全部状态' and requirement.get('status', '待分析') != status: continue
            if system and not requirement_matches_system(requirement, system): continue
            item_month = str(requirement.get('online_month') or '').strip()
            if month_filter == '__none__' and item_month: continue
            if month_filter and month_filter != '__none__' and item_month != month_filter: continue
            scoped.append(requirement)
        self._rebuild_status_strip(scoped)
        group_filter = getattr(self, '_status_group_filter', '')
        visible = [item for item in scoped if not group_filter or status_group(item.get('status')) == group_filter]
        total_all = len(self._requirements)
        self.tree_count_label.setText(f'{len(visible)}/{total_all}' if len(visible) != total_all else f'{total_all} 条')

        def _recent_key(item):
            return (
                item.get('online_month', ''),
                item.get('source_modified_at') or item.get('updated_at', ''),
                item.get('title', ''),
            )

        pinned_items = sorted(
            (r for r in visible if is_pinned(r)),
            key=lambda item: (pinned_at_rank(item), item.get('source_modified_at') or item.get('updated_at', '')),
            reverse=True,
        )
        plain_items = sorted((r for r in visible if not is_pinned(r)), key=_recent_key, reverse=True)
        selected_item = None
        first_item = None
        groups = {}

        def _add_group_header(key, header_text, group_role):
            header = QTreeWidgetItem()
            header.setText(0, header_text)
            header.setData(0, GROUP_MONTH_ROLE, group_role)
            header.setToolTip(0, f'{header_text}\n点击展开/折叠；把需求拖到这里可{"改上线月份" if self._group_mode == "month" else "改状态"}')
            header.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            font = header.font(0); font.setBold(True); header.setFont(0, font)
            try:
                from ui.theme_manager import ThemeManager
                header.setForeground(0, QColor(ThemeManager.instance().token('TEXT_MUTED')))
            except Exception:
                header.setForeground(0, QColor('#615D73'))
            self.requirement_list.addTopLevelItem(header)
            header.setFirstColumnSpanned(True)
            header.setExpanded(True)
            header.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            groups[key] = header
            return header

        def _add_requirement_item(parent, requirement):
            nonlocal first_item, selected_item
            requirement = normalize_requirement(requirement)
            item = QTreeWidgetItem(parent)
            item.setData(0, Qt.ItemDataRole.UserRole, requirement)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
            self._fill_requirement_row(item, requirement)
            first_item = first_item or item
            if requirement.get('id') == current_id:
                selected_item = item
            return item

        if pinned_items:
            pin_header = _add_group_header('__pinned__', f'已置顶 · {len(pinned_items)}', '__pinned__')
            for requirement in pinned_items:
                _add_requirement_item(pin_header, requirement)
        if self._group_mode == 'month':
            month_counts = {}
            for requirement in plain_items:
                month = requirement.get('online_month') or '未分月'
                month_counts[month] = month_counts.get(month, 0) + 1
            for requirement in plain_items:
                month = requirement.get('online_month') or '未分月'
                if month not in groups:
                    _add_group_header(month, f'{format_online_month_label(month)} · {month_counts[month]}', '' if month == '未分月' else month)
                _add_requirement_item(groups[month], requirement)
        else:
            buckets = {key: [] for key in STATUS_GROUP_ORDER}
            for requirement in plain_items:
                buckets[status_group(requirement.get('status'))].append(requirement)
            for key in STATUS_GROUP_ORDER:
                if not buckets[key]:
                    continue
                header = _add_group_header(key, f'{STATUS_GROUP_LABELS[key]} · {len(buckets[key])}', f'status:{key}')
                for requirement in buckets[key]:
                    _add_requirement_item(header, requirement)

        if query:
            self.requirement_list.expandAll()
        elif self._search_expand_snapshot is not None:
            self._restore_tree_expand_state(self._search_expand_snapshot)
            self._search_expand_snapshot = None
        elif refresh_expand_snapshot and set(refresh_expand_snapshot) & {
            str(header.data(0, GROUP_MONTH_ROLE)) for header in groups.values()
        }:
            self._restore_tree_expand_state(refresh_expand_snapshot)
        else:
            self.requirement_list.expandAll()
            if self._group_mode == 'status' and not group_filter:
                for key in COLLAPSED_GROUPS:
                    header = groups.get(key)
                    if header is not None:
                        header.setExpanded(False)
        self._fit_requirement_code_column()
        # 有搜索词时优先定位第一条匹配；无搜索时尽量保持当前选中
        from ui.search_highlight import focus_tree_item
        pick = first_item if query else (selected_item or first_item)
        if pick and self._tree_item_alive(pick):
            parent = pick.parent()
            if parent is not None and not parent.isExpanded() and pick is selected_item:
                parent.setExpanded(True)
            self.requirement_list.blockSignals(True)
            try:
                focus_tree_item(self.requirement_list, pick)
            finally:
                self.requirement_list.blockSignals(False)
            # 整树重建后需要重新加载文件库；信号已阻断，此处显式刷新
            self._show_requirement(pick, refresh_files=True)
        else:
            self._show_requirement(None)
        self._on_requirement_selection_changed()

    def _fill_requirement_row(self, item, requirement):
        """单行：编号 | 标题 | 状态 | 测试点；细节放 tooltip。"""
        from tools.list_pin import is_pinned
        from tools.requirements import test_points_progress
        from panels.requirement_views import status_tone
        count = len(requirement.get('sql_parts', []))
        file_count = requirement.get('file_count', 0)
        title = (
            requirement.get('title')
            or os.path.basename(requirement.get('local_path', '').rstrip(os.sep))
            or requirement.get('svn_url', '').rstrip('/').rsplit('/', 1)[-1]
            or '未命名'
        )
        code = str(requirement.get('code') or '').strip() or '未编号'
        status_text = requirement.get('status') or '待分析'
        done, total = test_points_progress(requirement.get('test_points'))
        is_bug = requirement.get('record_kind') == 'BUG'
        item.setText(0, code)
        item.setText(1, title)
        item.setText(2, status_text)
        item.setText(3, f'{done}/{total}' if total else '')
        try:
            from ui.theme_manager import ThemeManager
            manager = ThemeManager.instance()
            code_c = QColor(manager.token('DANGER' if is_bug else 'PRIMARY'))
            tone_c = QColor(manager.token(status_tone(status_text)))
            mute_c = QColor(manager.token('TEXT_MUTED'))
        except Exception:
            code_c, tone_c, mute_c = QColor('#B85C5C' if is_bug else '#6C58D9'), QColor('#615D73'), QColor('#615D73')
        item.setForeground(0, code_c)
        item.setForeground(2, tone_c)
        item.setForeground(3, mute_c)
        code_font = item.font(0); code_font.setBold(True); item.setFont(0, code_font)
        if is_pinned(requirement):
            title_font = item.font(1); title_font.setBold(True); item.setFont(1, title_font)
        done_flags = normalize_flag_done(requirement)
        flag_lines = [f"{full} · {'已完成' if done_flags.get(key) else '待完成'}" for key, _short, full in active_flags(requirement)]
        modified = requirement.get('source_modified_at') or requirement.get('updated_at', '')
        modified_text = modified[:16].replace('T', ' ') if modified else '未知'
        full_tip = (
            f"{requirement.get('record_kind', '需求')}：{code}\n"
            f"{title}\n"
            f"{'置顶：是' + chr(10) if is_pinned(requirement) else ''}"
            f"进度：{status_text} · 测试点：{done}/{total} · SQL：{count} 个 · 文件：{file_count} 个\n"
            f"{chr(10).join(flag_lines) if flag_lines else '无上线事项'}\n"
            f"系统：{systems_display_text(requirement, empty='—')}\n"
            f"上线：{requirement.get('actual_release_date') or format_online_month_label(requirement.get('online_month') or '') or '未安排'}\n"
            f"更新：{modified_text}\n"
            f"路径：{requirement.get('local_path') or requirement.get('svn_url') or '—'}\n"
            f"开发目录：{requirement.get('dev_local_path') or '—'}"
        )
        for column in range(4):
            item.setToolTip(column, full_tip)
        item.setSizeHint(0, QSize(0, 32))

    def _capture_tree_expand_state(self) -> dict:
        state = {}
        if not hasattr(self, 'requirement_list'):
            return state
        for i in range(self.requirement_list.topLevelItemCount()):
            group = self.requirement_list.topLevelItem(i)
            key = group.data(0, GROUP_MONTH_ROLE)
            state[str(key if key is not None else group.text(0))] = bool(group.isExpanded())
        return state

    def _restore_tree_expand_state(self, state: dict):
        if not state or not hasattr(self, 'requirement_list'):
            return
        for i in range(self.requirement_list.topLevelItemCount()):
            group = self.requirement_list.topLevelItem(i)
            key = str(group.data(0, GROUP_MONTH_ROLE) if group.data(0, GROUP_MONTH_ROLE) is not None else group.text(0))
            if key in state:
                group.setExpanded(bool(state[key]))

    def _fit_requirement_code_column(self):
        """按当前需求号内容自适应第 0 列，避免编号被裁切。"""
        if not hasattr(self, 'requirement_list'):
            return
        metrics = QFontMetrics(self.requirement_list.font())
        widest = 0
        for group_index in range(self.requirement_list.topLevelItemCount()):
            group = self.requirement_list.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                widest = max(widest, metrics.horizontalAdvance(group.child(child_index).text(0)))
        width = widest + self.requirement_list.indentation() * 2 + 28
        self.requirement_list.setColumnWidth(0, max(96, min(width, 200)))

    def _open_ticket_submit(self):
        from tools.requirements import requirement_identity
        from panels.ticket_submit_dialog import open_ticket_submit_dialog
        selected_ids = [
            requirement_identity(item)
            for item in self._selected_requirements()
            if requirement_identity(item)
        ]
        open_ticket_submit_dialog(self._requirements, selected_ids=selected_ids, parent=self)

    def _selected_requirements(self):
        selected = []
        seen = set()
        try:
            items = list(self.requirement_list.selectedItems())
        except RuntimeError:
            items = []
        for item in items:
            if not self._tree_item_alive(item):
                continue
            try:
                requirement = item.data(0, Qt.ItemDataRole.UserRole)
            except RuntimeError:
                continue
            requirement_id = requirement.get('id') if isinstance(requirement, dict) else None
            if requirement_id and requirement_id not in seen:
                selected.append(requirement); seen.add(requirement_id)
        if not selected and self._current:
            selected.append(self._current)
        return selected

    def _on_requirement_selection_changed(self):
        self._update_batch_delete_button()

    def _update_batch_delete_button(self):
        if not hasattr(self, 'batch_delete_btn'):
            return
        count = len(self._selected_requirements())
        self.batch_delete_btn.setEnabled(count > 0)
        self.batch_delete_btn.setText('删除' if count <= 1 else f'删除({count})')
        if hasattr(self, 'selection_bar'):
            self.selection_label.setText(f'已选 {count} 项 · 可一键提签或批量删除')
            self.selection_bar.setVisible(count > 1)

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
        from tools.list_pin import is_pinned, pin_action_label
        pin_action = menu.addAction(pin_action_label(is_pinned(requirement), self.language if hasattr(self, 'language') else 'zh'))
        pin_action.triggered.connect(lambda _=False, req=requirement: self._toggle_requirement_pin(req))
        flags = active_flags(requirement)
        if flags:
            status_menu = menu.addMenu('完成标记')
            done = normalize_flag_done(requirement)
            for key, short, full in flags:
                action = status_menu.addAction(
                    f"{'已完成' if done.get(key) else '待完成'} · {full}"
                )
                action.triggered.connect(
                    lambda _checked=False, req=requirement, flag_key=key: self._toggle_flag_done(req, flag_key)
                )
            status_menu.addSeparator()
            status_menu.addAction('全部标为已完成', lambda req=requirement: self._set_all_flags_done(req, True))
            status_menu.addAction('全部标为待完成', lambda req=requirement: self._set_all_flags_done(req, False))
        if requirement.get('local_path') and os.path.isdir(requirement['local_path']):
            open_action = menu.addAction('打开文件夹')
            open_action.triggered.connect(lambda: self._open_requirement_folder(requirement))
        dev_targets = []
        for name in requirement_systems(requirement):
            path = binding_for(requirement, name).get('dev_local_path')
            if path:
                dev_targets.append((name, path))
        if not dev_targets:
            fallback = str(requirement.get('dev_local_path') or '').strip()
            if fallback:
                dev_targets.append(('', fallback))
        if len(dev_targets) == 1:
            open_dev = menu.addAction('打开本地开发地址')
            open_dev.triggered.connect(
                lambda _=False, req=requirement, name=dev_targets[0][0]: self._open_dev_project_folder(req, name or None)
            )
        elif len(dev_targets) > 1:
            sub = menu.addMenu('打开本地开发地址')
            for name, _path in dev_targets:
                sub.addAction(
                    name,
                    lambda _=False, req=requirement, system=name: self._open_dev_project_folder(req, system),
                )
        menu.addSeparator()
        delete_action = menu.addAction(f"删除选中的 {len(self._selected_requirements())} 项")
        delete_action.triggered.connect(self._delete_requirement)
        menu.exec(self.requirement_list.viewport().mapToGlobal(point))

    def _toggle_requirement_pin(self, requirement):
        from tools.list_pin import is_pinned, set_pinned_fields
        if not isinstance(requirement, dict):
            return
        target = next((item for item in self._requirements if item.get('id') == requirement.get('id')), None)
        if target is None:
            return
        set_pinned_fields(target, not is_pinned(target))
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(self._requirements)
        self._current = target
        self._refresh()
        self.requirements_changed.emit()

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

    def _save_requirements_candidate(self, candidate, error_title='保存失败', error_message='无法写入 requirements.json') -> bool:
        """尝试保存需求候选列表；成功时提交 self._requirements，失败时弹窗报错并保留原状态。"""
        try:
            save_requirements(candidate)
        except Exception as exc:
            show_error(self, error_title, f'{error_message}：\n{exc}')
            return False
        self._requirements = candidate
        return True

    def _persist_test_points(self, points):
        if not isinstance(self._current, dict):
            return False
        req_id = self._current.get('id')
        if not req_id:
            return False
        orig_target = next(
            (item for item in self._requirements if isinstance(item, dict) and item.get('id') == req_id),
            None,
        )
        if orig_target is None:
            return False
        orig_points = copy.deepcopy(orig_target.get('test_points'))
        orig_desc = orig_target.get('description') or ''

        candidate = copy.deepcopy(self._requirements)
        target = next(
            (item for item in candidate if isinstance(item, dict) and item.get('id') == req_id),
            None,
        )
        if target is None:
            return False
        target['test_points'] = normalize_test_points(points)
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        if not self._save_requirements_candidate(candidate, error_message='无法写入测试点'):
            if hasattr(self, 'test_points_editor') and _is_alive(self.test_points_editor):
                editor = self.test_points_editor
                blocker = None
                was_blocked = editor.signalsBlocked() if hasattr(editor, 'signalsBlocked') else False
                try:
                    blocker = QSignalBlocker(editor)
                except Exception:
                    blocker = None
                    if hasattr(editor, 'blockSignals'):
                        editor.blockSignals(True)
                try:
                    editor.set_source(
                        points=orig_points,
                        description=orig_desc,
                        pending_seed=False,
                    )
                finally:
                    if blocker is not None:
                        del blocker
                    elif hasattr(editor, 'blockSignals'):
                        try:
                            editor.blockSignals(was_blocked)
                        except Exception:
                            pass
            return False

        self._current = target
        self.requirements_changed.emit()
        return True

    def _toggle_flag_done(self, requirement, flag_key, force_done=None):
        if not isinstance(requirement, dict):
            return False
        req_id = requirement.get('id')
        if not req_id:
            return False
        candidate = copy.deepcopy(self._requirements)
        target = next(
            (item for item in candidate if isinstance(item, dict) and item.get('id') == req_id),
            None,
        )
        if target is None:
            return False
        if not flag_is_active(target, flag_key):
            return False
        done = normalize_flag_done(target)
        done[flag_key] = (not bool(done.get(flag_key))) if force_done is None else bool(force_done)
        target['flag_done'] = done
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        if not self._save_requirements_candidate(candidate, error_message='无法写入完成标记'):
            return False
        self._current = target
        # 轻量刷新：只改标记与左侧徽章，避免整树 clear 导致点击闪退
        self._apply_flag_ui(target)
        self._patch_requirement_tree_item(target)
        # 通知工作台刷新最近需求（不重建树；月份选择由 dashboard 自行保留）
        self.requirements_changed.emit()
        return True

    def _set_all_flags_done(self, requirement, done_value):
        if not isinstance(requirement, dict):
            return False
        req_id = requirement.get('id')
        if not req_id:
            return False
        candidate = copy.deepcopy(self._requirements)
        target = next(
            (item for item in candidate if isinstance(item, dict) and item.get('id') == req_id),
            None,
        )
        if target is None:
            return False
        done = normalize_flag_done(target)
        for key, _short, _full in active_flags(target):
            done[key] = bool(done_value)
        target['flag_done'] = done
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        if not self._save_requirements_candidate(candidate, error_message='无法写入完成标记'):
            return False
        self._current = target
        self._apply_flag_ui(target)
        self._patch_requirement_tree_item(target)
        self.requirements_changed.emit()
        return True

    def _apply_flag_ui(self, requirement):
        """同步右侧完成标记按钮状态（不重建树）。"""
        if not requirement or not hasattr(self, '_flag_buttons'):
            return
        done = normalize_flag_done(requirement)
        active = {key for key, _s, _f in active_flags(requirement)}
        for key, short, full in FLAG_DEFS:
            btn = self._flag_buttons.get(key)
            if btn is None:
                continue
            if key not in active:
                btn.hide()
                continue
            is_done = bool(done.get(key))
            btn.setText(flag_chip_text(key, is_done))
            btn.setProperty('flagDone', 'true' if is_done else 'false')
            try:
                style = btn.style()
                if style is not None:
                    style.unpolish(btn)
                    style.polish(btn)
            except RuntimeError:
                pass
            btn.setToolTip(f'{full}\n点击切换完成状态：待完成 ↔ 已完成')
            btn.show()
        self.flag_section.setVisible(bool(active))
        self._layout_flag_chips()

    def _patch_requirement_tree_item(self, requirement):
        """就地更新左侧行文案与 UserRole，避免 clear 重建。"""
        if not requirement or not hasattr(self, 'requirement_list'):
            return
        rid = requirement.get('id')
        if not rid:
            return
        try:
            root_count = self.requirement_list.topLevelItemCount()
        except RuntimeError:
            return
        for i in range(root_count):
            group = self.requirement_list.topLevelItem(i)
            if not self._tree_item_alive(group):
                continue
            for j in range(group.childCount()):
                item = group.child(j)
                if not self._tree_item_alive(item):
                    continue
                try:
                    data = item.data(0, Qt.ItemDataRole.UserRole)
                except RuntimeError:
                    continue
                if not isinstance(data, dict) or data.get('id') != rid:
                    continue
                requirement = normalize_requirement(requirement)
                try:
                    item.setData(0, Qt.ItemDataRole.UserRole, requirement)
                    self._fill_requirement_row(item, requirement)
                except RuntimeError:
                    return
                return

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
        from tools.requirements import update_release_date_month
        target = str(month or '')
        if target == '__pinned__':
            return
        if target.startswith('status:'):
            from panels.requirement_views import GROUP_TARGET_STATUS, status_group
            group_key = target.split(':', 1)[1]
            status = GROUP_TARGET_STATUS.get(group_key)
            if status:
                wanted = {r if isinstance(r, str) else str((r or {}).get('id') or '') for r in (requirement_ids or [])}
                # 已在目标分组内的细状态（如“集成测试”）不被拖放覆盖
                ids = [
                    item.get('id') for item in self._requirements
                    if item.get('id') in wanted and status_group(item.get('status')) != group_key
                ]
                self._set_requirement_status(ids, status)
            return
        now = datetime.datetime.now().isoformat(timespec='seconds')
        changed = False
        target_month = str(month or '').strip()
        ids = {r if isinstance(r, str) else str((r or {}).get('id') or '') for r in (requirement_ids or [])}
        for requirement in self._requirements:
            if requirement.get('id') in ids:
                cur_date = str(requirement.get('actual_release_date') or requirement.get('actual_online_date') or '').strip()
                if target_month:
                    new_date = update_release_date_month(cur_date, target_month)
                else:
                    new_date = ''
                old_month = cur_date[:7] if cur_date else str(requirement.get('online_month') or '').strip()
                if new_date != cur_date or old_month != target_month:
                    requirement['actual_release_date'] = new_date
                    requirement['actual_online_date'] = new_date
                    requirement['online_month'] = new_date[:7] if new_date else ''
                    requirement['updated_at'] = now
                    changed = True
        if changed:
            save_requirements(self._requirements)
            self._refresh()
            self.requirements_changed.emit()

    @staticmethod
    def _open_requirement_folder(requirement):
        path = requirement.get('local_path', '') if requirement else ''
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_dev_project_folder(self, requirement, system_name=None):
        requirement = requirement or {}
        if system_name:
            path = binding_for(requirement, system_name).get('dev_local_path') or ''
        else:
            names = requirement_systems(requirement)
            if len(names) == 1:
                path = binding_for(requirement, names[0]).get('dev_local_path') or ''
            else:
                path = str(requirement.get('dev_local_path') or '').strip()
        path = str(path or '').strip()
        if not path:
            show_info(self, '本地开发地址', '这条需求还没有配置本地开发项目地址。')
            return
        if not os.path.isdir(path):
            show_warning(self, '本地开发地址', f'目录不存在：\n{path}')
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _show_requirement(self, current, _previous=None, refresh_files=True):
        # currentItemChanged 在树重建时可能传入已销毁节点
        if getattr(self, '_showing_requirement', False):
            return
        if current is not None and not self._tree_item_alive(current):
            current = None
        self._showing_requirement = True
        try:
            self._show_requirement_impl(current, refresh_files=refresh_files)
        except RuntimeError:
            # sip 包装已删除等瞬时错误：忽略，避免闪退
            return
        finally:
            self._showing_requirement = False

    def _show_requirement_impl(self, current, refresh_files=True):
        from panels.requirement_views import STAGES, stage_index
        try:
            self._current = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        except RuntimeError:
            self._current = None
        if self._current is not None and not isinstance(self._current, dict):
            self._current = None
        has_current = bool(self._current)
        for widget in (self.edit_btn, self.daily_btn, self.status_combo, self.detail_more_btn):
            widget.setEnabled(has_current)
        has_path = bool(self._current and self._current.get('local_path'))
        is_svn = bool(has_path and self._current.get('workspace_kind', 'svn') == 'svn')
        self.open_folder_btn.setEnabled(has_path)
        self.refresh_svn_btn.setEnabled(has_path)
        for button in (self.update_current_btn, self.quick_update_btn, self.quick_commit_btn, self.add_file_btn, self.new_text_btn):
            button.setEnabled(is_svn)
        self._action_open_folder.setEnabled(has_path)
        self._update_vcs_action_state()
        if not self._current:
            self.detail_title.setText('请选择左侧需求')
            self.detail_title.setToolTip('')
            self.detail_eyebrow.setText('')
            self.stage_bar.set_status('')
            self.stage_caption.setText('')
            self.open_dev_btn.setEnabled(False)
            for btn in self._flag_buttons.values():
                btn.hide()
            self.flag_section.hide()
            self.sql_preview.clear()
            self.sql_empty.show()
            for button in (self.sql_btn, self.docx_btn, self.release_link_btn):
                button.setEnabled(False)
            self.svn_meta.clear()
            self.file_tree.blockSignals(True)
            try:
                self.file_tree.clear()
            finally:
                self.file_tree.blockSignals(False)
            self.test_points_editor.setEnabled(False)
            self.test_points_editor.set_source(points=[], description='')
            return

        requirement = normalize_requirement(self._current)
        self._current = next((item for item in self._requirements if item.get('id') == requirement.get('id')), requirement)
        title = requirement.get('title') or '未命名需求'
        code = requirement.get('code') or '无编号'
        kind = requirement.get('record_kind', '需求')
        system = systems_display_text(requirement, empty='未选系统')
        status = requirement.get('status') or '待分析'
        month = requirement.get('online_month') or ''
        actual = requirement.get('actual_release_date') or requirement.get('actual_online_date') or ''
        online_text = f'上线 {actual}' if actual else (f'{format_online_month_label(month)}上线' if month else '未安排上线')
        self.detail_eyebrow.setText(f'{code}  ·  {kind}  ·  {system}  ·  {online_text}')
        self.detail_title.setText(title)
        self.detail_title.setToolTip(f'{title}\n编号：{code}\n完整信息请点「编辑」')
        self.status_combo.blockSignals(True)
        try:
            index = self.status_combo.findText(status)
            self.status_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.status_combo.blockSignals(False)
        self.stage_bar.set_status(status)
        current_stage = stage_index(status)
        parts = []
        for index, (_key, label, _members) in enumerate(STAGES):
            parts.append(f'<b>{label}</b>' if index == current_stage else label)
        self.stage_caption.setText('  ·  '.join(parts) + ('  （暂停）' if current_stage < 0 else ''))

        dev_path = str(requirement.get('dev_local_path') or '').strip() or any(
            binding_for(requirement, name).get('dev_local_path') for name in requirement_systems(requirement)
        )
        self.open_dev_btn.setEnabled(bool(dev_path))
        self.open_dev_btn.setToolTip('打开本地开发工程目录' if dev_path else '还没有配置本地开发目录，可在「编辑」里设置')
        self._action_open_dev.setEnabled(bool(dev_path))

        self._apply_flag_ui(requirement)

        local_path = requirement.get('local_path') or ''
        if requirement.get('workspace_kind') == 'folder':
            self.svn_meta.setText(f'{requirement.get("file_count", 0)} 个文件')
        elif local_path or requirement.get('svn_url'):
            rev = str(requirement.get('svn_revision') or '').strip()
            if not local_path:
                self.svn_meta.setText('未检出')
            else:
                self.svn_meta.setText(f'版本 r{rev}' if rev else '版本未知')
        else:
            self.svn_meta.setText('未关联资料目录')
        self.svn_meta.setToolTip(
            f"分支：{requirement.get('svn_url') or '无'}\n"
            f"资料：{local_path or '无'}\n"
            f"开发：{requirement.get('dev_local_path') or '无'}"
        )
        if refresh_files:
            self._refresh_file_tree()
        sql = merged_sql(requirement)
        has_sql = bool(sql and sql.strip())
        self.sql_preview.setPlainText(sql if has_sql else '')
        self.sql_empty.setVisible(not has_sql)
        self.sql_preview.setVisible(has_sql)
        try:
            from ui.search_highlight import clear_text_highlights
            clear_text_highlights(self.sql_preview)
        except Exception:
            pass
        self.docx_btn.setEnabled(has_sql)
        self.sql_btn.setEnabled(has_sql)
        self.release_link_btn.setEnabled(True)
        self.test_points_editor.setEnabled(True)
        self.test_points_editor.set_source(
            points=requirement.get('test_points'),
            description=requirement.get('description') or '',
        )

    def _set_busy(self, message=''):
        busy = bool(message)
        if message:
            self.svn_activity.setText(message)
            self.svn_activity.show()
        else:
            self.svn_activity.setText('')
            self.svn_activity.hide()
        widgets = (
            self.update_all_btn, self.toolbar_more_btn, self.add_btn, self.search_edit, self.month_filter,
            self.system_filter, self.kind_filter, self.status_filter, self.requirement_list,
            self.batch_delete_btn, self.ticket_btn, self.file_tree,
            self.open_folder_btn, self.refresh_svn_btn,
            self.update_current_btn, self.add_file_btn, self.new_text_btn, self.lock_file_btn,
            self.unlock_file_btn, self.revert_btn, self.commit_btn, self.file_more_btn,
            self.edit_btn, self.status_combo, self.detail_more_btn, self.docx_btn, self.sql_btn,
            *self.quick_buttons.values(),
        )
        for widget in widgets:
            widget.setEnabled(not busy)
        for action_name in ('_action_bug', '_action_scan', '_action_checkout', '_action_import', '_action_syscfg'):
            action = getattr(self, action_name, None)
            if action is not None:
                action.setEnabled(not busy)
        if not busy:
            self._update_batch_delete_button()
        if busy:
            token = self.loading.start_busy(message)
            self._task_token = token
            return token
        else:
            current = self.requirement_list.currentItem()
            if not self._tree_item_alive(current):
                current = None
            self._show_requirement(current, refresh_files=False)
            return getattr(self, '_task_token', None)

    def on_panel_deactivated(self):
        """离开需求管理面板时清理 loading 浮层。"""
        self._focus_request_token = getattr(self, '_focus_request_token', 0) + 1
        if hasattr(self, 'loading') and self.loading is not None:
            self.loading.hide_now()

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'requirement_list', None) and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Delete) or event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self._selected_requirements():
                    self._delete_requirement()
                    return True
        # 文件库：拖入本地文件
        tree = getattr(self, 'file_tree', None)
        if tree is not None and watched in (tree, tree.viewport()):
            et = event.type()
            if et in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                md = event.mimeData() if hasattr(event, 'mimeData') else None
                if md and md.hasUrls():
                    paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
                    if any(os.path.isfile(p) for p in paths):
                        event.acceptProposedAction()
                        return True
            if et == QEvent.Type.Drop:
                md = event.mimeData() if hasattr(event, 'mimeData') else None
                if md and md.hasUrls():
                    paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
                    if paths:
                        self._drop_files_into_workspace(paths)
                        event.acceptProposedAction()
                        return True
        return super().eventFilter(watched, event)

    def _drop_files_into_workspace(self, paths: list[str]):
        """拖入文件：复制到当前需求工作副本并尝试 svn add。"""
        root = self._current_path()
        if not root or not os.path.isdir(root):
            show_warning(self, '文件库', '请先选择有本机目录的需求，再拖入文件。')
            return
        files = [p for p in (paths or []) if p and os.path.isfile(p)]
        if not files:
            show_warning(self, '文件库', '没有可添加的文件。')
            return
        # 若落在某文件夹行上，写入该相对目录
        folder = ''
        try:
            item = self.file_tree.currentItem()
            if item is not None and bool(item.data(0, IS_DIR_ROLE)):
                folder = item.text(4) or ''
        except Exception:
            folder = ''
        self._start_task(
            '正在复制拖入的文件并执行 SVN add……',
            add_existing_files,
            (root, files, folder),
            self._files_added,
        )

    def _start_task(self, message, function, arguments, success, show_loading=True, finish_label=None):
        """后台任务。

        show_loading=True 仅用于长任务（拉取/更新/锁定/提交/扫描/add 等）。
        刷新文件树、状态探测等静默任务传 False，不打断浏览。
        finish_label：成功时 Loading 语义文案；缺省从 message 推导。
        """
        if self._active_worker is not None:
            show_info(self, 'SVN 正在处理', '请等待当前 SVN 操作完成。')
            return
        self._task_failed = False
        self._task_shows_loading = show_loading
        self._task_finish_label = finish_label or self._finish_label_from_busy(message)
        token = None
        if show_loading:
            token = self._set_busy(message)
        else:
            # 静默任务只短暂显示状态，不锁整页、不弹 Loading
            self.svn_activity.setText(message)
            self.svn_activity.setVisible(bool(message))
        worker = SvnWorker(function, *arguments)
        worker.token = token
        worker.success_callback = success
        # 每个任务自带 Loading/失败状态：成功回调里的弹窗会转事件循环，期间启动的
        # 静默刷新会改写面板级 _task_* 字段，不能再用它们决定收起哪个浮层。
        worker.shows_loading = show_loading
        worker.finish_label = self._task_finish_label
        worker.task_failed = False
        self._active_worker = worker
        _ACTIVE_SVN_WORKERS.add(worker)
        worker.result_ready.connect(self._task_succeeded)
        worker.failed.connect(self._task_failed_with_error)
        worker.finished.connect(self._task_finished)
        worker.finished.connect(worker.release_later)
        worker.start()

    @staticmethod
    def _finish_label_from_busy(message):
        text = (message or '').strip()
        mapping = (
            ('扫描', '扫描完成'),
            ('拉取', '拉取完成'),
            ('检出', '检出完成'),
            ('更新全部', '全部更新完成'),
            ('更新当前', '当前副本已更新'),
            ('更新', '更新完成'),
            ('锁定', '锁定完成'),
            ('解锁', '解锁完成'),
            ('回滚', '回滚完成'),
            ('提交', '提交完成'),
            ('SVN add', '文件已加入版本库'),
            ('复制文件', '文件已添加'),
            ('创建文件', '文件已创建'),
            ('导入', '导入完成'),
        )
        for key, label in mapping:
            if key in text:
                return label
        return '处理完成'

    @staticmethod
    def _worker_attr(worker, name, fallback):
        return getattr(worker, name, fallback) if isinstance(worker, SvnWorker) else fallback

    def _mark_task_failed(self, worker):
        self._task_failed = True
        if isinstance(worker, SvnWorker):
            worker.task_failed = True

    def _task_succeeded(self, result, success=None, *, token: int | None = None):
        if not _is_alive(self):
            return
        worker = self.sender()
        if isinstance(worker, SvnWorker):
            success = success or getattr(worker, 'success_callback', None)
            token = getattr(worker, 'token', token)
        if token is not None and token != getattr(self, '_task_token', None):
            return
        # 回调前先取本任务自己的状态，回调期间可能已开始下一个任务
        shows_loading = self._worker_attr(worker, 'shows_loading', self._task_shows_loading)
        finish_label = self._worker_attr(worker, 'finish_label', getattr(self, '_task_finish_label', None))
        try:
            cb_result = None
            if success is not None:
                cb_result = success(result)
            if cb_result is False:
                self._mark_task_failed(worker)
                if shows_loading:
                    self.loading.fail('处理失败', token=token)
                self.svn_activity.setText('操作失败；本地台账未变更。')
                self.svn_activity.show()
                return
            if shows_loading:
                self.loading.finish(finish_label or '处理完成', token=token)
        except Exception as exc:
            self._task_failed_with_error(str(exc), token=token, worker=worker)

    def _task_failed_with_error(self, error, *, token: int | None = None, worker=None):
        if not _is_alive(self):
            return
        worker = worker if worker is not None else self.sender()
        if isinstance(worker, SvnWorker):
            token = getattr(worker, 'token', token)
        if token is not None and token != getattr(self, '_task_token', None):
            return
        self._mark_task_failed(worker)
        self.svn_activity.setText('SVN 操作失败；本地台账未丢失。')
        self.svn_activity.show()
        if self._worker_attr(worker, 'shows_loading', self._task_shows_loading):
            self.loading.fail('处理失败：' + error[:120], token=token)
        show_warning(self, 'SVN 操作失败', error)

    def _task_finished(self, worker=None):
        worker = worker or self.sender()
        if not _is_alive(self):
            return
        if worker is not self._active_worker:
            return
        self._active_worker = None
        task_failed = self._worker_attr(worker, 'task_failed', getattr(self, '_task_failed', False))
        shows_loading = self._worker_attr(worker, 'shows_loading', self._task_shows_loading)
        explicit_failure = self.svn_activity.text() if (task_failed and hasattr(self, 'svn_activity')) else ''
        if shows_loading:
            self._set_busy('')
        elif not task_failed:
            self.svn_activity.setText('后台状态已更新。')
            self.svn_activity.show()
            def _safe_hide_activity():
                if _is_alive(self) and hasattr(self, 'svn_activity') and _is_alive(self.svn_activity):
                    if not getattr(self, '_active_worker', None):
                        self.svn_activity.hide()
            QTimer.singleShot(2500, _safe_hide_activity)
        if task_failed:
            self.svn_activity.setText(explicit_failure or 'SVN 操作失败；本地台账未丢失。')
            self.svn_activity.show()
        if self._pending_scan_folder:
            self._schedule_pending_scan_dispatch()
            return
        if self._pending_file_refresh:
            self._schedule_pending_file_refresh()

    def _schedule_pending_scan_dispatch(self):
        if self._pending_scan_dispatch_scheduled or not self._pending_scan_folder:
            return
        self._pending_scan_dispatch_scheduled = True
        QTimer.singleShot(0, self._dispatch_pending_scan)

    def _dispatch_pending_scan(self):
        self._pending_scan_dispatch_scheduled = False
        if not _is_alive(self):
            return
        if self._active_worker is not None:
            return
        folder = self._pending_scan_folder
        if not folder:
            return
        if not os.path.isdir(folder):
            self._pending_scan_folder = None
            show_error(self, '需求文件夹扫描', f'等待扫描的目录已不存在：\n{folder}')
            self._schedule_pending_file_refresh()
            return
        self._pending_scan_folder = None
        self._start_task(
            '正在扫描本地需求文件夹和 SVN 工作副本……',
            scan_working_copies,
            (folder,),
            self._scan_finished,
        )

    def _schedule_pending_file_refresh(self):
        if not self._pending_file_refresh:
            return
        self._pending_file_refresh = False

        def _safe_refresh_tree():
            if _is_alive(self):
                self._refresh_file_tree()

        QTimer.singleShot(0, _safe_refresh_tree)

    def closeEvent(self, event):
        self._pending_scan_folder = None
        self._pending_scan_dispatch_scheduled = False
        super().closeEvent(event)

    def _scan_folder(self):
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_month_root')
        folder = QFileDialog.getExistingDirectory(self, '选择按月份整理的需求根目录', start)
        if folder:
            remember_dialog_path('requirement_month_root', folder, is_directory=True)
            if self._active_worker is not None and self._task_shows_loading:
                show_info(self, '需求文件夹扫描', '当前操作正在处理，所选目录未加入扫描队列；请稍后重试。')
                return
            if self._active_worker is not None or self._pending_scan_folder:
                replaced = bool(self._pending_scan_folder)
                self._pending_scan_folder = folder
                self._schedule_pending_scan_dispatch()
                message = (
                    '已用最新选择替换等待扫描的目录；当前后台读取完成后会自动开始扫描。'
                    if replaced else
                    '所选目录已加入扫描队列；当前后台读取完成后会自动开始扫描。'
                )
                show_info(self, '需求文件夹扫描已排队', message)
                return
            self._start_task('正在扫描本地需求文件夹和 SVN 工作副本……', scan_working_copies, (folder,), self._scan_finished)

    def _focus_scanned_requirement(self, copies):
        target = None
        for copy_info in copies:
            path = str(copy_info.get('local_path') or '').strip()
            if path:
                path_key = os.path.normcase(os.path.abspath(path))
                target = next(
                    (item for item in self._requirements
                     if item.get('local_path')
                     and os.path.normcase(os.path.abspath(item.get('local_path'))) == path_key),
                    None,
                )
            if target is None and copy_info.get('svn_url'):
                target = next(
                    (item for item in self._requirements if item.get('svn_url') == copy_info.get('svn_url')),
                    None,
                )
            if target is not None:
                break
        if target is None:
            return False

        if hasattr(self, 'search_edit') and self.search_edit is not None:
            blocked = self.search_edit.signalsBlocked()
            self.search_edit.blockSignals(True)
            try:
                self.search_edit.clear()
            finally:
                self.search_edit.blockSignals(blocked)
        if hasattr(self, '_req_search_timer') and self._req_search_timer.isActive():
            self._req_search_timer.stop()

        for control, default_index in (
            (getattr(self, 'system_filter', None), 0),
            (getattr(self, 'kind_filter', None), 0),
            (getattr(self, 'status_filter', None), 0),
        ):
            if control is None or control.count() <= 0:
                continue
            blocked = control.signalsBlocked()
            control.blockSignals(True)
            try:
                control.setCurrentIndex(default_index)
            finally:
                control.blockSignals(blocked)
        try:
            update_last_choices(requirement_filters={
                'system': self.system_filter.currentData() or '',
                'kind': self.kind_filter.currentText(),
                'status': self.status_filter.currentText(),
            })
        except Exception:
            pass
        return self.focus_requirement(target.get('id'))

    def _scan_finished(self, copies):
        if not isinstance(copies, list):
            show_error(self, '需求文件夹扫描', '扫描结果格式无效，必须为列表。')
            return False
        if len(copies) == 0:
            show_info(self, '需求文件夹扫描', '未识别到任何需求或工作副本目录。')
            return False
        for item in copies:
            if not isinstance(item, dict) or not item or not str(item.get('local_path') or '').strip():
                show_error(self, '需求文件夹扫描', '扫描结果包含无效目录项，必须为包含有效路径的字典。')
                return False
        candidate = copy.deepcopy(self._requirements)
        candidate, added, updated = merge_working_copies(candidate, copies)
        # 扫描新增条目补全系统/月份等空字段（只填空，不覆盖已有）
        candidate = [
            apply_auto_inference(item, systems=self._systems, only_empty=True)
            for item in candidate
        ]
        if not self._save_requirements_candidate(candidate, error_message='无法写入 requirements.json'):
            return False
        if not self._focus_scanned_requirement(copies):
            if isinstance(self._current, dict) and self._current.get('id'):
                cur_id = self._current.get('id')
                self._current = next(
                    (item for item in self._requirements if item.get('id') == cur_id),
                    self._current,
                )
            self._refresh()
        self.requirements_changed.emit()
        missing = sum(not item.get('online_month') for item in copies)
        errors = sum(bool(item.get('error')) for item in copies)
        message = f'识别到 {len(copies)} 个需求目录；新增 {added} 条，更新 {updated} 条。\n已按月份分组，同月按文件最新修改时间排序。'
        if missing: message += f'\n{missing} 条未从目录名识别出月份，可在“编辑”中补充。'
        if errors: message += f'\n{errors} 条 SVN 信息读取失败，已保留本地路径和错误信息。'
        if errors:
            show_warning(self, '需求文件夹扫描部分失败', message)
        else:
            show_success(self, '需求文件夹扫描完成', message)
        if added or updated:
            focus = self._current or (self._requirements[0] if self._requirements else None)
            if focus:
                self._offer_next_steps(focus, context='scan')
        return True

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
        save_requirements(self._requirements)
        self._refresh()
        self.requirements_changed.emit()
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
        save_requirements(self._requirements)
        self._refresh()
        self.requirements_changed.emit()
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
        save_requirements(self._requirements)
        self._refresh()
        self.requirement_saved.emit(candidate)
        self._offer_next_steps(candidate, context='bug')

    def _current_path(self):
        path = self._current.get('local_path', '') if self._current else ''
        return path if path and os.path.isdir(path) else ''

    def _refresh_file_tree(self):
        if hasattr(self, 'file_tree'):
            self.file_tree.blockSignals(True)
            try:
                self.file_tree.clear()
            finally:
                self.file_tree.blockSignals(False)
        self._file_entries_cache = []
        path = self._current_path()
        if not path:
            self._file_tree_path = ''
            if hasattr(self, 'file_count_label'):
                self.file_count_label.setText('')
            return
        if self._active_worker is not None:
            self._pending_file_refresh = True
            return
        self._file_tree_path = path
        # 一次拉取文件列表 + SVN 真实锁状态（覆盖本地 svn_locks 缓存）
        is_svn = bool(self._current and self._current.get('workspace_kind', 'svn') == 'svn')
        if is_svn and os.path.isdir(os.path.join(path, '.svn')):
            self._start_task(
                '正在读取文件库并同步 SVN 锁定状态……',
                workspace_snapshot,
                (path,),
                self._file_tree_loaded,
                show_loading=False,
            )
        else:
            self._start_task(
                '正在读取需求文件夹完整内容……',
                workspace_files,
                (path,),
                self._file_tree_loaded,
                show_loading=False,
            )

    def _file_tree_loaded(self, payload):
        if getattr(self, '_file_tree_path', '') != self._current_path():
            return
        # 兼容：旧回调可能直接是 list
        if isinstance(payload, dict) and 'files' in payload:
            entries = list(payload.get('files') or [])
            locks = payload.get('locks') if isinstance(payload.get('locks'), dict) else {}
            self._apply_synced_svn_locks(locks)
        else:
            entries = list(payload or [])
        self._file_entries_cache = entries
        self._populate_file_tree_from_cache()

    def _apply_synced_svn_locks(self, locks: dict):
        """用 SVN 真实锁覆盖需求台账中的 svn_locks（解决外部解锁后界面仍显示锁定）。"""
        if not self._current:
            return
        if self._current.get('workspace_kind', 'svn') != 'svn':
            return
        # locks: relative_path -> meta dict
        new_locks = {}
        for rel, meta in (locks or {}).items():
            if not rel:
                continue
            # 统一成工作副本相对路径（系统分隔符）
            key = str(rel).replace('/', os.sep).replace('\\', os.sep)
            if isinstance(meta, dict):
                stamp = meta.get('created') or meta.get('owner') or datetime.datetime.now().isoformat(timespec='seconds')
                new_locks[key] = stamp
            elif meta:
                new_locks[key] = str(meta)
        old = self._current.get('svn_locks') or {}
        # 仅在变化时写盘，避免无意义刷盘
        if dict(old) != new_locks:
            self._current['svn_locks'] = new_locks
            # 写回 requirements 列表中的同一对象
            rid = self._current.get('id')
            for item in self._requirements:
                if item.get('id') == rid:
                    item['svn_locks'] = new_locks
                    break
            try:
                save_requirements(self._requirements)
            except Exception:
                pass
        else:
            self._current['svn_locks'] = new_locks

    def _sorted_file_entries(self, entries):
        col = getattr(self, '_file_sort_column', 0)
        reverse = getattr(self, '_file_sort_order', Qt.SortOrder.AscendingOrder) == Qt.SortOrder.DescendingOrder

        def sort_key(entry):
            is_dir = 0 if entry.get('is_dir') else 1
            name = os.path.basename(entry.get('relative_path') or entry.get('path') or '').casefold()
            ftype = str(entry.get('file_type') or ('文件夹' if entry.get('is_dir') else '文件')).casefold()
            mtime = str(entry.get('modified_at') or '')
            size = str(entry.get('size') or '')
            path = str(entry.get('relative_path') or '')
            base = (is_dir,)
            if col == 0:
                return base + (name,)
            if col == 1:
                return base + (ftype, name)
            if col == 2:
                return base + (mtime, name)
            if col == 3:
                return base + (size, name)
            return base + (path.casefold(), name)

        return sorted(entries, key=sort_key, reverse=reverse)

    def _populate_file_tree_from_cache(self):
        from tools.pinyin_search import build_search_blob, match_query
        self.file_tree.blockSignals(True)
        try:
            self.file_tree.clear()
        finally:
            self.file_tree.blockSignals(False)
        entries = self._sorted_file_entries(list(self._file_entries_cache or []))
        query = self.file_search_edit.text().strip() if hasattr(self, 'file_search_edit') else ''
        req_meta = []
        if self._current:
            req_meta = [
                self._current.get('code'), self._current.get('title'),
                self._current.get('system'), self._current.get('record_kind'),
            ]
        keep_rel = set()
        if query:
            for entry in entries:
                blob = build_search_blob(
                    entry.get('relative_path'), entry.get('file_type'),
                    os.path.basename(entry.get('relative_path') or ''),
                    os.path.splitext(entry.get('relative_path') or '')[1],
                    *req_meta,
                )
                if match_query(blob, query):
                    rel = entry.get('relative_path') or ''
                    keep_rel.add(rel)
                    parent = os.path.dirname(rel)
                    while parent:
                        keep_rel.add(parent)
                        parent = os.path.dirname(parent)
            entries = [e for e in entries if (e.get('relative_path') or '') in keep_rel]
        nodes = {'': self.file_tree.invisibleRootItem()}
        shown = 0
        first_file_hit = None
        for entry in entries:
            relative = entry['relative_path']
            parent_key = os.path.dirname(relative)
            parent = nodes.get(parent_key, self.file_tree.invisibleRootItem())
            lock_map = (self._current.get('svn_locks', {}) if self._current else {}) or {}
            rel_norm = relative.replace('/', os.sep).replace('\\', os.sep)
            locked = rel_norm in lock_map or relative.replace('\\', '/') in lock_map
            name = os.path.basename(relative)
            display_name = ('🔒 ' + name) if locked else name
            ftype = entry.get('file_type', '文件夹' if entry['is_dir'] else '文件')
            item = QTreeWidgetItem(parent, (
                display_name, ftype, entry.get('modified_at', ''), entry.get('size', ''), relative,
            ))
            for col in range(5):
                item.setTextAlignment(col, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            # 仅名称列带系统图标；类型/时间/大小/路径不设图标
            from PyQt6.QtGui import QIcon
            item.setIcon(0, _FILE_ICON_PROVIDER.icon(QFileInfo(entry['path'])))
            blank = QIcon()
            for col in range(1, 5):
                item.setIcon(col, blank)
            item.setData(0, Qt.ItemDataRole.UserRole, entry['path'])
            item.setData(0, IS_DIR_ROLE, entry['is_dir'])
            svn_code = str(entry.get('svn_status') or '')
            show_svn = bool(self._current and self._current.get('workspace_kind', 'svn') == 'svn' and svn_code)
            tip = f"{name}\n类型：{ftype}\n路径：{relative}\n完整：{entry.get('path')}"
            if show_svn:
                tip += f"\nSVN：{SVN_STATUS_LABELS.get(svn_code, svn_code)}"
            if locked:
                tip += '\n锁定：是（已与 SVN 同步）'
                lock_val = lock_map.get(rel_norm) or lock_map.get(relative.replace('\\', '/')) or ''
                if lock_val:
                    tip += f'\n锁信息：{lock_val}'
            for col in range(5):
                item.setToolTip(col, tip)
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
                if entry['is_dir'] else QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator
            )
            if show_svn:
                item.setForeground(0, _svn_status_color(svn_code))
            if locked:
                font = item.font(0); font.setBold(True); item.setFont(0, font)
                if not show_svn:
                    try:
                        from ui.theme_manager import ThemeManager
                        item.setForeground(0, QColor(ThemeManager.instance().token('HIGHLIGHT_MARK')))
                    except Exception:
                        item.setForeground(0, QColor('#B24A24'))
            # 文件库搜索：仅过滤 + 定位首个匹配，不改名称样式
            file_hit = bool(query and match_query(build_search_blob(name, relative, ftype), query))
            if file_hit and first_file_hit is None and not entry.get('is_dir'):
                first_file_hit = item
            if entry['is_dir']:
                nodes[relative] = item
            else:
                shown += 1
        self.file_tree.expandAll()
        if first_file_hit is not None:
            from ui.search_highlight import focus_tree_item
            focus_tree_item(self.file_tree, first_file_hit)
        if hasattr(self, 'file_count_label'):
            total = len([e for e in (self._file_entries_cache or []) if not e.get('is_dir')])
            self.file_count_label.setText(f'命中 {shown}/{total} 文件' if query else f'{total} 文件')
        self._update_vcs_action_state()

    def _filter_file_tree_local(self):
        if not hasattr(self, 'file_tree'):
            return
        self._populate_file_tree_from_cache()

    def _on_file_header_clicked(self, column: int):
        if column == getattr(self, '_file_sort_column', 0):
            self._file_sort_order = (
                Qt.SortOrder.DescendingOrder
                if self._file_sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self._file_sort_column = column
            self._file_sort_order = Qt.SortOrder.AscendingOrder
        self._populate_file_tree_from_cache()

    def _selected_file_paths(self):
        paths = []
        try:
            items = list(self.file_tree.selectedItems())
        except RuntimeError:
            items = []
        for item in items:
            if not self._tree_item_alive(item):
                continue
            try:
                path = item.data(0, Qt.ItemDataRole.UserRole)
            except RuntimeError:
                continue
            if path and os.path.exists(path):
                paths.append(path)
        return paths

    def _selected_svn_file(self):
        """兼容：返回当前项单个文件路径。"""
        files = self._selected_file_paths_only()
        return files[0] if len(files) == 1 else (files[0] if files else '')

    def _selected_file_paths_only(self):
        return [p for p in self._selected_file_paths() if os.path.isfile(p)]

    def _all_file_paths_in_tree(self):
        root = self._current_path()
        if not root:
            return []
        result = []
        for entry in self._file_entries_cache or []:
            if entry.get('is_dir'):
                continue
            path = entry.get('path')
            if path and os.path.isfile(path):
                result.append(path)
        return result

    def _vcs_scope_mode(self) -> str:
        """文件库 SVN 操作始终基于当前文件树选择。"""
        return 'selection'

    def _with_vcs_scope(self, _mode: str, callback):
        """兼容右键菜单调用；所有操作固定作用于当前选择。"""
        callback()

    def _resolve_vcs_targets(self, *, files_only: bool, prefer_changes: bool = False):
        """按范围解析操作目标。

        files_only=True：锁定/解锁只收文件。
        prefer_changes：提交/回滚在「全部文件」时也优先有改动的路径更安全——但全部文件模式按字面全部文件。
        """
        root = self._current_path()
        mode = self._vcs_scope_mode()
        if mode == 'all_files':
            paths = self._all_file_paths_in_tree()
            label = f'全部文件 {len(paths)} 个'
        elif mode == 'changes':
            if not root:
                return [], '无工作副本'
            try:
                info = changed_paths(root)
            except (SvnError, ValueError) as exc:
                raise ValueError(str(exc)) from exc
            paths = list(info.get('paths') or [])
            if files_only:
                paths = [p for p in paths if os.path.isfile(p)]
            label = f'全部改动 {len(paths)} 项'
        else:
            paths = self._selected_file_paths_only() if files_only else self._selected_file_paths()
            if files_only:
                label = f'选中文件 {len(paths)} 个'
            else:
                label = f'选中项 {len(paths)} 个'
        # 去重保序
        seen = set()
        unique = []
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            unique.append(os.path.abspath(path))
        return unique, label

    def _select_all_files_in_tree(self):
        if not hasattr(self, 'file_tree'):
            return
        self.file_tree.clearSelection()
        def walk(item):
            count = item.childCount()
            for i in range(count):
                child = item.child(i)
                if child is None:
                    continue
                try:
                    if not bool(child.data(0, IS_DIR_ROLE)):
                        child.setSelected(True)
                except RuntimeError:
                    pass
                walk(child)
        root = self.file_tree.invisibleRootItem()
        walk(root)
        self._update_vcs_action_state()

    def _clear_file_selection(self):
        if hasattr(self, 'file_tree'):
            self.file_tree.clearSelection()
        self._update_vcs_action_state()

    def _update_lock_buttons(self, *_args):
        """兼容旧名。"""
        self._update_vcs_action_state()

    def _update_vcs_action_state(self, *_args):
        try:
            is_svn = bool(
                self._current
                and self._current.get('local_path')
                and self._current.get('workspace_kind', 'svn') == 'svn'
            )
            selected_files = self._selected_file_paths_only()
            target_n = len(selected_files)
            can_file_op = is_svn and target_n > 0
            if hasattr(self, 'lock_file_btn'):
                self.lock_file_btn.setEnabled(can_file_op)
                self.unlock_file_btn.setEnabled(can_file_op)
            if hasattr(self, 'revert_btn'):
                self.revert_btn.setEnabled(can_file_op)
            if hasattr(self, 'commit_btn'):
                self.commit_btn.setEnabled(can_file_op)
        except (RuntimeError, ValueError, OSError):
            for name in ('lock_file_btn', 'unlock_file_btn', 'revert_btn', 'commit_btn'):
                btn = getattr(self, name, None)
                if btn is not None:
                    btn.setEnabled(False)
        self._sync_file_more_menu()

    def _lock_selected_file(self):
        try:
            paths, label = self._resolve_vcs_targets(files_only=True)
        except ValueError as exc:
            show_warning(self, 'SVN 锁定', str(exc)); return
        if not paths:
            show_info(self, 'SVN 锁定', '请先在文件列表中选择要锁定的文件。'); return
        default_msg = 'PengTools 开发锁定'
        if len(paths) == 1:
            prompt = f'锁定说明（{os.path.basename(paths[0])}）：'
        else:
            prompt = f'锁定说明（{label}）：'
        message, accepted = QInputDialog.getText(self, '锁定 SVN 文件', prompt, text=default_msg)
        if not accepted:
            return
        preview = '\n'.join(os.path.basename(p) for p in paths[:12])
        more = f'\n…共 {len(paths)} 个' if len(paths) > 12 else ''
        if len(paths) > 1 and not confirm_action(
            self, '确认批量锁定',
            f'将锁定以下文件：\n\n{preview}{more}\n\n其他人将无法提交这些文件，直到解锁。',
            confirm_text='确认锁定',
            danger=False,
        ):
            return
        self._start_task(
            f'正在锁定 {len(paths)} 个 SVN 文件……',
            lock_files, (paths, message), self._lock_finished,
        )

    def _lock_finished(self, result):
        if not self._current:
            return
        root = self._current_path()
        locks = self._current.setdefault('svn_locks', {})
        now = datetime.datetime.now().isoformat(timespec='seconds')
        for path in result.get('paths') or ([result.get('path')] if result.get('path') else []):
            if not path or not root:
                continue
            try:
                relative = os.path.relpath(path, root)
            except ValueError:
                continue
            locks[relative] = now
        save_requirements(self._requirements)
        self._refresh_file_tree()
        count = int(result.get('count') or len(result.get('paths') or []) or 1)
        show_success(
            self, 'SVN 文件已锁定',
            (result.get('output') or f'已锁定 {count} 个文件。') + '\n其他人将无法提交这些文件，直到解锁。',
        )

    def _unlock_selected_file(self):
        try:
            paths, label = self._resolve_vcs_targets(files_only=True)
        except ValueError as exc:
            show_warning(self, 'SVN 解锁', str(exc)); return
        if not paths:
            show_info(self, 'SVN 解锁', '请先在文件列表中选择要解锁的文件。'); return
        if len(paths) > 1 and not confirm_action(
            self, '确认批量解锁',
            f'将解锁 {len(paths)} 个文件（{label}）。',
            confirm_text='确认解锁',
            danger=False,
        ):
            return
        self._start_task(
            f'正在解锁 {len(paths)} 个 SVN 文件……',
            unlock_files, (paths,), self._unlock_finished,
        )

    def _unlock_finished(self, result):
        if not self._current:
            return
        root = self._current_path()
        locks = self._current.setdefault('svn_locks', {})
        for path in result.get('paths') or ([result.get('path')] if result.get('path') else []):
            if not path or not root:
                continue
            try:
                relative = os.path.relpath(path, root)
            except ValueError:
                continue
            locks.pop(relative, None)
        save_requirements(self._requirements)
        self._refresh_file_tree()
        count = int(result.get('count') or len(result.get('paths') or []) or 1)
        show_success(self, 'SVN 文件已解锁', result.get('output') or f'已解锁 {count} 个文件。')

    def _open_tree_item(self, item, _column):
        if not item:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        is_dir = bool(item.data(0, IS_DIR_ROLE))
        if is_dir or (path and os.path.isdir(path)):
            item.setExpanded(not item.isExpanded())
            return
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _copy_selected_paths(self):
        paths = self._selected_file_paths()
        if not paths:
            return
        QApplication.clipboard().setText('\n'.join(paths))
        show_success(self, '文件库', f'已复制 {len(paths)} 条路径')

    def _export_selected_files(self):
        paths = [p for p in self._selected_file_paths() if os.path.isfile(p)]
        if not paths:
            show_info(self, '导出', '请先选择要导出的文件。')
            return
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_export_dir')
        dest = QFileDialog.getExistingDirectory(self, '选择导出目录', start)
        if not dest:
            return
        remember_dialog_path('requirement_export_dir', dest, is_directory=True)
        import shutil
        ok = 0
        for path in paths:
            try:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
                ok += 1
            except Exception:
                pass
        show_success(self, '导出', f'已导出 {ok}/{len(paths)} 个文件到：\n{dest}')

    def _delete_selected_files(self):
        paths = [p for p in self._selected_file_paths() if os.path.isfile(p) or os.path.isdir(p)]
        if not paths:
            show_info(self, '删除', '请先选择要删除的文件或文件夹。')
            return
        names = '\n'.join(os.path.basename(p) for p in paths[:12])
        more = f'\n…共 {len(paths)} 项' if len(paths) > 12 else ''
        if not confirm_action(
            self, '确认删除文件',
            f'将删除选中项（仅本地文件，不可恢复）：\n\n{names}{more}\n\n默认取消，请确认后再删除。',
            confirm_text='确认删除',
            danger=True,
        ):
            return
        import shutil
        failed = 0
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception:
                failed += 1
        self._refresh_file_tree()
        if failed:
            show_warning(self, '删除', f'完成，但有 {failed} 项失败。')
        else:
            show_success(self, '删除', f'已删除 {len(paths)} 项。')

    def _show_file_menu(self, point):
        item = self.file_tree.itemAt(point)
        if item and not item.isSelected():
            # 右键未在多选集合内时，改为单选该项
            self.file_tree.clearSelection()
            item.setSelected(True)
            self.file_tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('刷新', self._refresh_file_tree)
        menu.addAction('全部展开', self.file_tree.expandAll)
        menu.addAction('全部折叠', self.file_tree.collapseAll)
        menu.addSeparator()
        menu.addAction('复制路径', self._copy_selected_paths)
        menu.addAction('导出到…', self._export_selected_files)
        menu.addAction('删除…', self._delete_selected_files)
        is_svn = bool(self._current and self._current.get('workspace_kind', 'svn') == 'svn')
        selected_files = self._selected_file_paths_only()
        if is_svn and selected_files:
            menu.addSeparator()
            n = len(selected_files)
            menu.addAction(
                f'锁定选中（{n}）',
                lambda: self._with_vcs_scope('selection', self._lock_selected_file),
            )
            menu.addAction(
                f'解锁选中（{n}）',
                lambda: self._with_vcs_scope('selection', self._unlock_selected_file),
            )
            menu.addAction(
                f'回滚选中（{n}）…',
                lambda: self._with_vcs_scope('selection', self._revert_svn_paths),
            )
            menu.addAction(
                f'提交选中（{n}）…',
                lambda: self._with_vcs_scope('selection', self._commit_svn),
            )
        if item:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                is_dir = bool(item.data(0, IS_DIR_ROLE)) or os.path.isdir(path)
                menu.addSeparator()
                if is_dir:
                    menu.addAction('展开/折叠', lambda: item.setExpanded(not item.isExpanded()))
                    menu.addAction('打开文件夹', lambda p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
                else:
                    menu.addAction('打开文件', lambda p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
                    menu.addAction(
                        '打开所在文件夹',
                        lambda p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p))),
                    )
        menu.exec(self.file_tree.viewport().mapToGlobal(point))

    def _open_folder(self):
        self._open_requirement_folder(self._current)

    @staticmethod
    def _inspect_working_copy(path):
        info = working_copy_info(path); info['svn_status'] = svn_status(path)['text']; return info

    def _refresh_current_svn(self):
        path = self._current_path()
        # 刷新状态：普通浏览，不打断、不 Loading
        if path:
            self._start_task(
                '正在刷新当前 SVN 状态……',
                self._inspect_working_copy,
                (path,),
                self._refresh_current_finished,
                show_loading=False,
            )

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
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_svn_add_files')
        paths, _ = QFileDialog.getOpenFileNames(self, '选择要复制并加入 SVN 的文件', start)
        if not paths: return
        remember_dialog_path('requirement_svn_add_files', paths)
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
        root = self._current_path()
        if not root:
            return
        paths = self._selected_file_paths()
        if not paths:
            show_info(self, '提交 SVN', '请先在文件列表中选择要提交的文件或目录。')
            return
        self._confirm_and_commit(root, paths, f'选中 {len(paths)} 项')

    def _commit_current_changes(self):
        """详情区快捷提交：选中了文件就提交选中项，否则提交当前需求目录的全部改动。"""
        root = self._current_path()
        if not root:
            show_info(self, '提交 SVN', '这条需求还没有绑定资料工作副本。')
            return
        if self._selected_file_paths():
            self._commit_svn()
            return
        try:
            info = changed_paths(root)
        except (SvnError, ValueError, OSError) as exc:
            show_warning(self, '提交 SVN', f'读取改动失败：\n{exc}')
            return
        paths = list(info.get('paths') or [])
        if not paths:
            show_info(self, '提交 SVN', '当前需求目录没有需要提交的改动。')
            return
        self._confirm_and_commit(root, paths, f'全部改动 {len(paths)} 项')

    def _confirm_and_commit(self, root, paths, label):
        message, accepted = QInputDialog.getText(self, '提交 SVN', f'提交说明（{label}）：')
        if not accepted or not message.strip():
            return
        names = '\n'.join(os.path.basename(p) for p in paths[:15])
        more = f'\n…共 {len(paths)} 项' if len(paths) > 15 else ''
        detail = f'范围：{label}\n\n{names}{more}\n\n提交说明：{message.strip()}'
        if not confirm_action(
            self, '确认提交 SVN',
            f'将提交以下路径的改动：\n\n{detail}',
            confirm_text='确认提交',
            danger=True,
        ):
            return
        self._start_task(
            f'正在提交 {len(paths)} 项，请勿关闭软件……',
            commit_paths, (paths, message.strip(), root), self._commit_finished,
        )

    def _commit_finished(self, info):
        self._refresh_current_finished(info)
        self._refresh_file_tree()
        count = info.get('count') or ''
        suffix = f'（{count} 项）' if count else ''
        show_success(self, 'SVN 提交完成', (info.get('output') or '提交成功。') + suffix)

    def _revert_svn_paths(self):
        root = self._current_path()
        if not root:
            return
        paths = self._selected_file_paths()
        if not paths:
            show_info(self, '回滚', '请先在文件列表中选择要回滚的文件或目录。')
            return
        label = f'选中 {len(paths)} 项'

        names = '\n'.join(os.path.basename(p) for p in paths[:15])
        more = f'\n…共 {len(paths)} 项' if len(paths) > 15 else ''
        if not confirm_action(
            self, '确认回滚本地修改',
            f'将丢弃本地未提交修改（svn revert），不可恢复：\n\n范围：{label}\n\n{names}{more}\n\n'
            '不会删除未纳入版本库的新文件（? 状态可能仍保留）。',
            confirm_text='确认回滚',
            danger=True,
        ):
            return
        self._start_task(
            f'正在回滚 {len(paths)} 项……',
            revert_paths, (paths,), self._revert_finished,
        )

    def _revert_finished(self, result):
        self._refresh_file_tree()
        if self._current_path():
            try:
                self._refresh_current_svn()
            except Exception:
                pass
        count = result.get('count') or len(result.get('paths') or [])
        show_success(self, '回滚完成', result.get('output') or f'已回滚 {count} 项。')

    def _save_dialog(self, requirement=None, offer_next=True, is_new=False, offer_daily=None, focus_section=None):
        # offer_daily 为旧参数别名：True/False 映射到是否弹出下一步建议
        if offer_daily is not None:
            offer_next = bool(offer_daily)
        dialog = RequirementDialog(requirement, self)
        if focus_section == 'completion' and hasattr(dialog, 'status_combo') and dialog.status_combo is not None:
            dialog.status_combo.setFocus()
            def _focus_status_after_show():
                if _is_alive(dialog) and dialog.isVisible() and _is_alive(dialog.status_combo):
                    dialog.status_combo.setFocus()
            QTimer.singleShot(0, _focus_status_after_show)
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

        candidate = copy.deepcopy(self._requirements)
        index = next((i for i, item in enumerate(candidate) if isinstance(item, dict) and item.get('id') == req_id), -1)
        if index < 0:
            target = {'id': req_id, 'created_at': now}
            candidate.append(target)
            index = len(candidate) - 1
            is_new = True
        else:
            target = candidate[index]

        old_done = dict(target.get('flag_done') or {})
        # 原地更新，保持列表引用稳定
        target.update(values)
        if not str(values.get('local_path') or '').strip():
            clear_workspace_binding(target)
        target['id'] = req_id
        target['has_sql'] = bool(values.get('has_sql') or values.get('sql_parts'))
        target['needs_peripheral_upgrade'] = bool(values.get('needs_peripheral_upgrade'))
        target['needs_interface_update'] = bool(values.get('needs_interface_update'))
        target['temporary_upgrade'] = bool(values.get('temporary_upgrade'))
        target['sql_parts'] = list(values.get('sql_parts') or [])
        target['source_files'] = list(values.get('source_files') or [])
        target['test_points'] = normalize_test_points(
            values['test_points'] if 'test_points' in values else target.get('test_points')
        )
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
        candidate[index] = target

        if not self._save_requirements_candidate(candidate, error_message='无法写入 requirements.json'):
            return None

        self._current = target
        self._refresh()
        self.requirement_saved.emit(target)
        self.svn_activity.setText(
            f'已保存：{target.get("code") or "无编号"} · {target.get("title") or "未命名"}'
            f' · 标记 {flag_status_text(target)}'
        )
        self.svn_activity.show()
        def _safe_hide_activity():
            if _is_alive(self) and hasattr(self, 'svn_activity') and _is_alive(self.svn_activity):
                if not getattr(self, '_active_worker', None):
                    self.svn_activity.hide()
        QTimer.singleShot(2500, _safe_hide_activity)
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
            ('release', '准备本次升级', False),
        ]
        if has_sql:
            actions.append(('sql', '打开发版联动', False))
        if has_path:
            actions.append(('folder', '打开目录', False))
        recommended = 'daily'
        if context == 'scan':
            recommended = 'release'
            message = f'{name}\n\n推荐：准备本次升级核对候选；也可把当前项加入今日日报。'
        elif has_sql:
            message = f'{name}\n\n推荐：加入今日日报。已检测到 SQL，也可发送到发版联动。'
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
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('requirement_import')
        path, _ = QFileDialog.getOpenFileName(
            self, '导入需求文档', start, '需求文档 (*.docx *.xlsx *.txt *.md *.json *.xml *.yaml *.yml *.csv)'
        )
        if not path:
            return
        remember_dialog_path('requirement_import', path)
        self.loading.start_busy('正在导入并整理需求文档……')
        try:
            entries = _document_entries_with_password(path, self)
            for entry in entries:
                entry['name'] = entry.get('source') or os.path.basename(path)
            content = '\n'.join(_entry_text(entry) for entry in entries)
        except (OSError, ValueError) as exc:
            self.loading.fail('导入失败')
            show_warning(self, '导入需求', f'{os.path.basename(path)} 读取失败：{exc}')
            return
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
        save_requirements(self._requirements)
        self._refresh()
        self.requirements_changed.emit()
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
