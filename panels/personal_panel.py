# -*- coding: utf-8 -*-
import datetime
import copy
import os

from PyQt6.QtCore import QDate, QStringListModel, QTime, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QInputDialog, QLineEdit, QHeaderView,
    QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QTimeEdit, QVBoxLayout, QWidget,
)

from tools.daily_reports import (
    is_reminder_due, load_reminder_settings, load_reports, report_markdown,
    save_reminder_settings, save_reports,
)
from ui.confirm_dialog import confirm_action, show_info, show_success, show_warning
from ui.field_metrics import size_combo, size_compact_button, size_date, size_line
from tools.personal_knowledge import (
    CATEGORIES, entry_fingerprint, export_word_entry, export_workbook_entry,
    extract_document_entries, extract_document_text, extract_workbook_entries,
    load_custom_entries, load_seed_entries, organize_content, save_custom_entries,
    search_entries,
)
from tools.requirements import daily_template


class PasteKnowledgeDialog(QDialog):
    def __init__(self, initial_text='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('粘贴后自动整理')
        self.resize(760, 580)
        layout = QVBoxLayout(self)
        note = QLabel('整篇直接粘贴即可。软件会自动切段、生成标题、判断分类并批量保存。')
        note.setObjectName('ops-safety-note')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.content_edit = QPlainTextEdit(initial_text)
        self.content_edit.setPlaceholderText('把杂乱笔记、连接信息、操作说明或学习内容全部粘贴到这里……')
        layout.addWidget(self.content_edit, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('自动整理并保存')
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_checked(self):
        if not self.content_edit.toPlainText().strip():
            show_warning(self, 'PengTools 私人版', '请先粘贴内容。')
            return
        self.accept()

    def text(self):
        return self.content_edit.toPlainText()


class KnowledgeEditDialog(QDialog):
    def __init__(self, entry=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('编辑学习内容' if entry else '新增学习内容')
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(entry.get('title', '') if entry else '')
        size_line(self.title_edit, 'std')
        self.category_combo = QComboBox()
        size_combo(self.category_combo, 'md')
        for key, label in CATEGORIES.items():
            if key != 'all':
                self.category_combo.addItem(label, key)
        if entry:
            self.category_combo.setCurrentIndex(max(0, self.category_combo.findData(entry.get('category'))))
        self.tags_edit = QLineEdit(entry.get('tags', '') if entry else '')
        size_line(self.tags_edit, 'std')
        form.addRow('标题', self.title_edit)
        form.addRow('分类', self.category_combo)
        form.addRow('标签', self.tags_edit)
        layout.addLayout(form)
        self.content_edit = QPlainTextEdit(entry.get('content', '') if entry else '')
        self.content_edit.setPlaceholderText('记录知识、操作步骤、问题处理过程或任意学习内容……')
        layout.addWidget(self.content_edit, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_checked(self):
        if not self.title_edit.text().strip() or not self.content_edit.toPlainText().strip():
            show_warning(self, 'PengTools 私人版', '标题和内容不能为空。')
            return
        self.accept()

    def values(self):
        return {
            'title': self.title_edit.text().strip(),
            'category': self.category_combo.currentData(),
            'tags': self.tags_edit.text().strip(),
            'content': self.content_edit.toPlainText().strip(),
        }


class KnowledgeTab(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._seed_entries = load_seed_entries()
        self._custom_entries = load_custom_entries()
        self._filtered = []
        self._current = None
        self._hidden_rows = {}
        self._hidden_columns = {}
        self._highlighted_cells = []
        self._setup_ui()
        self.set_language(language)
        self._refresh()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        actions = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('ops-search')
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True)
        self._suggestion_model = QStringListModel(self)
        self._suggestion_targets = {}
        self._suggestion_query = ''
        self._completer = QCompleter(self._suggestion_model, self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setMaxVisibleItems(10)
        self._completer.activated[str].connect(self._activate_suggestion)
        self.search_edit.setCompleter(self._completer)
        self.search_edit.textChanged.connect(self._on_search_changed)
        actions.addWidget(self.search_edit, 1)
        self.category_combo = QComboBox()
        size_combo(self.category_combo, 'md')
        for key, label in CATEGORIES.items():
            self.category_combo.addItem(label, key)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        actions.addWidget(self.category_combo)
        self.paste_btn = QPushButton('直接粘贴整理')
        self.paste_btn.setObjectName('primary-btn')
        self.paste_btn.clicked.connect(self._paste_content)
        actions.addWidget(self.paste_btn)
        self.import_btn = QPushButton('导入文档')
        self.import_btn.clicked.connect(self._import_documents)
        actions.addWidget(self.import_btn)
        self.add_btn = QPushButton('新增一条')
        self.add_btn.clicked.connect(self._add_entry)
        actions.addWidget(self.add_btn)
        root.addLayout(actions)

        self.note = QLabel()
        self.note.setObjectName('ops-safety-note')
        self.note.setWordWrap(True)
        self.note.hide()  # 首次提示用 tooltip；不占常驻版面
        root.addWidget(self.note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QFrame()
        left.setObjectName('ops-list-card')
        left_layout = QVBoxLayout(left)
        count_row = QHBoxLayout()
        self.list_title = QLabel('资料库')
        self.list_title.setObjectName('section-title')
        count_row.addWidget(self.list_title)
        count_row.addStretch()
        self.result_count = QLabel()
        self.result_count.setObjectName('small-label')
        count_row.addWidget(self.result_count)
        left_layout.addLayout(count_row)
        self.entry_list = QListWidget()
        self.entry_list.setObjectName('ops-command-list')
        self.entry_list.currentItemChanged.connect(self._show_entry)
        left_layout.addWidget(self.entry_list)
        splitter.addWidget(left)

        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setContentsMargins(10, 2, 2, 2)
        title_row = QHBoxLayout()
        self.title_label = QLabel('请选择内容')
        self.title_label.setObjectName('ops-title')
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        self.category_badge = QLabel()
        self.category_badge.setObjectName('ops-category-badge')
        title_row.addWidget(self.category_badge)
        self.sensitive_badge = QLabel('含访问资料')
        self.sensitive_badge.setObjectName('private-sensitive-badge')
        title_row.addWidget(self.sensitive_badge)
        detail.addLayout(title_row)
        self.meta_label = QLabel()
        self.meta_label.setObjectName('small-label')
        detail.addWidget(self.meta_label)
        self.content_stack = QStackedWidget()
        self.content_view = QPlainTextEdit()
        self.content_view.setReadOnly(True)
        self.content_view.setObjectName('private-content')
        self.content_stack.addWidget(self.content_view)
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table_status = QLabel()
        self.table_status.setObjectName('small-label')
        table_layout.addWidget(self.table_status)
        self.table_view = QTableWidget()
        self.table_view.setObjectName('private-workbook-table')
        self.table_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setWordWrap(False)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table_view.cellDoubleClicked.connect(self._copy_table_cell)
        table_layout.addWidget(self.table_view, 1)
        table_copy_actions = QHBoxLayout()
        # 复制 / 导出 合并为下拉，避免一排重复动作
        from PyQt6.QtWidgets import QToolButton, QMenu
        self.copy_menu_btn = QToolButton()
        self.copy_menu_btn.setText('复制')
        self.copy_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        copy_menu = QMenu(self.copy_menu_btn)
        copy_menu.addAction('复制单元格', self._copy_table_cell_action)
        self.copy_row_btn = copy_menu.addAction('复制整行', self._copy_current_row)
        self.copy_visible_btn = copy_menu.addAction('复制当前展示', self._copy_visible_table)
        self.copy_all_btn = copy_menu.addAction('复制整表', self._copy_all_table)
        self.copy_menu_btn.setMenu(copy_menu)
        table_copy_actions.addWidget(self.copy_menu_btn)
        self.edit_cell_btn = QPushButton('修改单元格'); self.edit_cell_btn.clicked.connect(self._edit_table_cell)
        table_copy_actions.addWidget(self.edit_cell_btn)
        table_copy_actions.addStretch()
        table_layout.addLayout(table_copy_actions)
        table_view_actions = QHBoxLayout()
        self.hide_rows_btn = QPushButton('隐藏选中行'); self.hide_rows_btn.clicked.connect(self._hide_selected_rows)
        self.hide_column_btn = QPushButton('隐藏当前列'); self.hide_column_btn.clicked.connect(self._hide_current_column)
        self.restore_table_btn = QPushButton('恢复全部行列'); self.restore_table_btn.clicked.connect(self._restore_hidden_table)
        for button in (self.hide_rows_btn, self.hide_column_btn, self.restore_table_btn):
            table_view_actions.addWidget(button)
        table_view_actions.addStretch()
        self.export_menu_btn = QToolButton()
        self.export_menu_btn.setText('导出')
        self.export_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self.export_menu_btn)
        self.export_visible_btn = export_menu.addAction('导出当前展示', lambda: self._export_table(True))
        self.export_all_btn = export_menu.addAction('导出整表', lambda: self._export_table(False))
        self.export_menu_btn.setMenu(export_menu)
        table_view_actions.addWidget(self.export_menu_btn)
        table_layout.addLayout(table_view_actions)
        self.content_stack.addWidget(table_page)
        word_page = QWidget()
        word_layout = QVBoxLayout(word_page); word_layout.setContentsMargins(0, 0, 0, 0)
        self.word_status = QLabel('Word 编辑器：可直接修改内容，保存后写入本机资料库')
        self.word_status.setObjectName('small-label'); word_layout.addWidget(self.word_status)
        self.word_view = QTextEdit(); self.word_view.setAcceptRichText(True); word_layout.addWidget(self.word_view, 1)
        word_actions = QHBoxLayout(); word_actions.addStretch()
        self.save_word_btn = QPushButton('保存 Word 修改'); self.save_word_btn.clicked.connect(self._save_word_document)
        self.export_word_btn = QPushButton('导出 DOCX'); self.export_word_btn.clicked.connect(self._export_word_document)
        word_actions.addWidget(self.save_word_btn); word_actions.addWidget(self.export_word_btn); word_layout.addLayout(word_actions)
        self.content_stack.addWidget(word_page)
        detail.addWidget(self.content_stack, 1)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.edit_btn = QPushButton('编辑')
        self.edit_btn.clicked.connect(self._edit_entry)
        action_row.addWidget(self.edit_btn)
        self.update_btn = QPushButton('更新文件')
        self.update_btn.clicked.connect(self._update_entry_file)
        action_row.addWidget(self.update_btn)
        self.delete_btn = QPushButton('删除')
        self.delete_btn.setObjectName('ops-delete-custom')
        self.delete_btn.clicked.connect(self._delete_entry)
        action_row.addWidget(self.delete_btn)
        self.copy_btn = QPushButton('复制内容')
        self.copy_btn.setObjectName('primary-btn')
        self.copy_btn.clicked.connect(self._copy_entry)
        action_row.addWidget(self.copy_btn)
        detail.addLayout(action_row)
        splitter.addWidget(right)
        splitter.setSizes([360, 680])
        root.addWidget(splitter, 1)

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible, apply_splitter_orientation, editor_min_height
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        for name in ('splitter', 'main_splitter', 'content_splitter', 'learn_splitter'):
            sp = getattr(self, name, None)
            if sp is not None:
                apply_splitter_orientation(sp, mode, min_editor=editor_min_height())
                sp.setChildrenCollapsible(False)
        for name in ('content_edit', 'editor', 'report_edit', 'private_content'):
            ed = getattr(self, name, None)
            if ed is not None and hasattr(ed, 'setMinimumHeight'):
                ed.setMinimumHeight(editor_min_height())

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.search_edit.setPlaceholderText('全文搜索：标题、正文、标签、来源……' if zh else 'Search title, content, tags and source…')
        self.note.setText(
            '懒人模式：整篇粘贴或一次选择多个文档，自动切段、命名和分类。私人数据只保存在本机；标记为访问资料的内容请勿外发。'
            if zh else
            'Lazy mode: paste everything or import multiple documents for automatic splitting and classification. Data stays local.'
        )

    def all_entries(self):
        overrides = {entry.get('base_seed_id'): entry for entry in self._custom_entries if entry.get('base_seed_id')}
        entries = [overrides.get(entry.get('id'), entry) for entry in self._seed_entries]
        entries.extend(entry for entry in self._custom_entries if not entry.get('base_seed_id'))
        return entries

    def _on_search_changed(self, text):
        self._refresh()
        self._update_suggestions(text)

    def _on_category_changed(self, _index):
        self._refresh()
        self._update_suggestions(self.search_edit.text())

    def _update_suggestions(self, query):
        query = query.strip()
        self._suggestion_query = query
        self._suggestion_targets = {}
        if not query:
            self._suggestion_model.setStringList([])
            return
        terms = [term.casefold() for term in query.split() if term]
        category = self.category_combo.currentData() or 'all'
        labels = []
        for entry in self.all_entries():
            if category != 'all' and entry.get('category') != category:
                continue
            entry_text = ' '.join(str(entry.get(key, '')) for key in ('title', 'tags', 'source', 'sheet_name')).casefold()
            if all(term in entry_text for term in terms):
                label = f"资料 · {entry.get('title', '未命名')}"
                labels.append(label)
                self._suggestion_targets[label] = (entry.get('id'), None)
            if entry.get('content_type') != 'workbook_sheet':
                content = str(entry.get('content', ''))
                if all(term in content.casefold() for term in terms):
                    line = next((line.strip() for line in content.splitlines() if all(term in line.casefold() for term in terms)), '')
                    label = f"内容 · {entry.get('title', '未命名')} · {line[:80]}"
                    labels.append(label)
                    self._suggestion_targets[label] = (entry.get('id'), None)
            else:
                for row_index, row in enumerate(entry.get('rows', [])):
                    row_text = '  '.join(str(value) for value in row if str(value).strip())
                    if row_text and all(term in row_text.casefold() for term in terms):
                        label = f"表格 · {entry.get('sheet_name', '')} · 第 {row_index + 1} 行 · {row_text[:90]}"
                        labels.append(label)
                        self._suggestion_targets[label] = (entry.get('id'), row_index)
                    if len(labels) >= 12:
                        break
            if len(labels) >= 12:
                break
        labels = list(dict.fromkeys(labels))[:12]
        self._suggestion_model.setStringList(labels)
        if labels and self.search_edit.hasFocus():
            self._completer.setCompletionPrefix('')
            self._completer.complete()

    def _activate_suggestion(self, label):
        target = self._suggestion_targets.get(label)
        if not target:
            return
        # QCompleter inserts its full display label before emitting activated.
        # Restore the user's actual keyword and use the label only for navigation.
        if self.search_edit.text() != self._suggestion_query:
            self.search_edit.blockSignals(True)
            self.search_edit.setText(self._suggestion_query)
            self.search_edit.blockSignals(False)
            self._refresh()
        entry_id, row_index = target
        for index in range(self.entry_list.count()):
            item = self.entry_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole).get('id') == entry_id:
                self.entry_list.setCurrentItem(item)
                if row_index is not None and not self.table_view.isRowHidden(row_index):
                    target_item = next(
                        (self.table_view.item(row_index, column) for column in range(self.table_view.columnCount())
                         if self.table_view.item(row_index, column)), None
                    )
                    if target_item:
                        self.table_view.setCurrentItem(target_item)
                        self.table_view.scrollToItem(target_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                        self.table_view.clearSelection()
                break

    def _refresh(self, *_args):
        if not hasattr(self, 'entry_list'):
            return
        current_id = self._current.get('id') if self._current else None
        self._filtered = search_entries(
            self.all_entries(), self.search_edit.text(), self.category_combo.currentData() or 'all'
        )
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        selected_item = None
        for entry in self._filtered:
            category = CATEGORIES.get(entry.get('category'), CATEGORIES['other'])
            source = '已更新' if entry.get('builtin_source') else ('内置' if entry.get('builtin') else '我的')
            file_type = entry.get('file_type') or ('EXCEL' if entry.get('content_type') == 'workbook_sheet' else 'TXT')
            item = QListWidgetItem(f"{entry.get('title', '未命名')}\n{file_type} · {category} · {source}")
            if entry.get('content_type') == 'workbook_sheet':
                tooltip = f"Excel 工作表：{entry.get('sheet_name', '')}\n{entry.get('row_count', 0)} 行 × {entry.get('column_count', 0)} 列"
            else:
                tooltip = entry.get('content', '')[:800]
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.entry_list.addItem(item)
            if entry.get('id') == current_id:
                selected_item = item
        self.result_count.setText(f'{len(self._filtered)} 条')
        if self.entry_list.count():
            selected_item = selected_item or self.entry_list.item(0)
            self.entry_list.setCurrentItem(selected_item)
        self.entry_list.blockSignals(False)
        if selected_item:
            selected = selected_item.data(Qt.ItemDataRole.UserRole)
            if self._current and selected.get('id') == self._current.get('id'):
                if selected.get('content_type') == 'workbook_sheet':
                    self._filter_workbook_rows(selected)
            else:
                self._show_entry(selected_item)
        else:
            self._show_entry(None)

    def _show_entry(self, current, _previous=None):
        self._current = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not self._current:
            self.title_label.setText('没有匹配内容')
            self.category_badge.clear()
            self.sensitive_badge.hide()
            self.meta_label.clear()
            self.content_view.clear()
            self.table_view.clear()
            self.table_status.clear()
            self.copy_btn.setEnabled(False)
            self.edit_btn.hide()
            self.update_btn.hide()
            self.delete_btn.hide()
            return
        entry = self._current
        self.title_label.setText(entry.get('title', '未命名'))
        self.category_badge.setText(CATEGORIES.get(entry.get('category'), CATEGORIES['other']))
        self.sensitive_badge.setVisible(bool(entry.get('sensitive')))
        is_table = entry.get('content_type') == 'workbook_sheet'
        is_word = entry.get('content_type') == 'word_document'
        if entry.get('builtin'):
            source_kind = '内置资料 · 可修改更新'
        elif entry.get('builtin_source'):
            source_kind = '内置资料 · 已本机更新'
        elif is_table:
            source_kind = '我的表格 · 可修改'
        else:
            source_kind = '我的内容 · 可编辑'
        sheet_meta = f"  ·  工作表：{entry.get('sheet_name')}" if is_table else ''
        self.meta_label.setText(f"{source_kind}  ·  来源：{entry.get('source', '手工新增')}{sheet_meta}  ·  {entry.get('updated_at', '')}")
        if is_table:
            self._show_workbook(entry)
            self.content_stack.setCurrentIndex(1)
            self.copy_btn.setText('复制表格')
        elif is_word:
            if entry.get('document_html'):
                self.word_view.setHtml(entry.get('document_html'))
            else:
                self.word_view.setPlainText(entry.get('content', ''))
            self.content_stack.setCurrentIndex(2)
            self.copy_btn.setText('复制内容')
        else:
            self.content_view.setPlainText(entry.get('content', ''))
            self.content_stack.setCurrentIndex(0)
            self.copy_btn.setText('复制内容')
        self.copy_btn.setEnabled(True)
        self.copy_btn.setVisible(not is_table)
        self.edit_btn.setVisible(not is_table and not is_word)
        self.update_btn.setVisible(True)
        self.delete_btn.setVisible(not entry.get('builtin'))
        self.delete_btn.setText('恢复内置原版' if entry.get('builtin_source') else '删除')

    @staticmethod
    def _column_name(index):
        value, result = index + 1, ''
        while value:
            value, remainder = divmod(value - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _show_workbook(self, entry):
        rows = entry.get('rows', [])
        column_count = entry.get('column_count') or max((len(row) for row in rows), default=0)
        self.table_view.setUpdatesEnabled(False)
        self.table_view.clear()
        self.table_view.setRowCount(len(rows))
        self.table_view.setColumnCount(column_count)
        self.table_view.setHorizontalHeaderLabels([self._column_name(index) for index in range(column_count)])
        self.table_view.setVerticalHeaderLabels([str(index + 1) for index in range(len(rows))])
        header_rows = set(entry.get('header_rows', []))
        styles = entry.get('cell_styles', {})
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row[:column_count]):
                if not value:
                    continue
                item = QTableWidgetItem(str(value))
                style = styles.get(f'{row_index},{column_index}', {})
                if style.get('bold') or row_index in header_rows:
                    font = item.font(); font.setBold(True); item.setFont(font)
                background = style.get('background')
                if background:
                    item.setBackground(QColor(background))
                elif row_index in header_rows:
                    try:
                        from ui.theme_manager import ThemeManager
                        item.setBackground(QColor(ThemeManager.instance().token('PRIMARY_SOFT')))
                    except Exception:
                        item.setBackground(QColor('#E8EEFF'))
                item.setToolTip(str(value))
                self.table_view.setItem(row_index, column_index, item)
        for index, width in enumerate(entry.get('column_widths', [])):
            self.table_view.setColumnWidth(index, min(max(int(float(width) * 7 + 12), 48), 420))
            self.table_view.setColumnHidden(index, index in self._hidden_columns.get(entry.get('id'), set()))
        self.table_view.setUpdatesEnabled(True)
        self._filter_workbook_rows(entry)

    def _filter_workbook_rows(self, entry):
        for item, background in self._highlighted_cells:
            item.setBackground(background)
        self._highlighted_cells = []
        rows = entry.get('rows', [])
        terms = [term.casefold() for term in self.search_edit.text().split() if term.strip()]
        header_rows = set(entry.get('header_rows', []))
        manually_hidden = self._hidden_rows.get(entry.get('id'), set())
        metadata = ' '.join(str(entry.get(key, '')) for key in ('title', 'tags', 'source', 'sheet_name')).casefold()
        metadata_match = bool(terms) and all(term in metadata for term in terms)
        matches = 0
        first_match = None
        for row_index, row in enumerate(rows):
            row_match = not terms or metadata_match or all(term in '\t'.join(map(str, row)).casefold() for term in terms)
            if terms and row_match and row_index not in header_rows:
                matches += 1
            hidden_by_search = bool(terms) and not row_match and row_index not in header_rows
            self.table_view.setRowHidden(row_index, row_index in manually_hidden or hidden_by_search)
            if terms and row_match and not metadata_match:
                for column_index in range(min(len(row), self.table_view.columnCount())):
                    item = self.table_view.item(row_index, column_index)
                    if item and any(term in item.text().casefold() for term in terms):
                        self._highlighted_cells.append((item, QBrush(item.background())))
                        try:
                            from ui.theme_manager import ThemeManager
                            item.setBackground(QColor(ThemeManager.instance().token('SEARCH_MATCH')))
                        except Exception:
                            item.setBackground(QColor('#FFF19C'))
                        first_match = first_match or item
        if first_match:
            self.table_view.setCurrentItem(first_match)
            self.table_view.scrollToItem(first_match, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.table_view.clearSelection()
        if terms:
            shown = len(rows) if metadata_match else matches
            self.table_status.setText(f'实时定位并高亮：匹配 {shown} / 总计 {len(rows)} 行；双击单元格可复制')
        else:
            self.table_status.setText(f'Excel 视图：{len(rows)} 行 × {entry.get("column_count", 0)} 列；双击单元格可复制')

    def _copy_table_cell(self, row, column):
        item = self.table_view.item(row, column)
        if item:
            QApplication.clipboard().setText(item.text())

    def _copy_table_cell_action(self):
        row = self.table_view.currentRow()
        column = self.table_view.currentColumn()
        if row < 0 or column < 0:
            show_info(self, '复制单元格', '请先选择一个单元格。')
            return
        self._copy_table_cell(row, column)

    def _table_text(self, row_indexes, column_indexes):
        lines = []
        for row in row_indexes:
            values = []
            for column in column_indexes:
                item = self.table_view.item(row, column)
                values.append(item.text() if item else '')
            lines.append('\t'.join(values))
        return '\n'.join(lines)

    def _copy_current_row(self):
        row = self.table_view.currentRow()
        if row < 0:
            show_info(self, '复制整行', '请先选择一行。')
            return
        QApplication.clipboard().setText(self._table_text([row], range(self.table_view.columnCount())))

    def _copy_visible_table(self):
        rows = [row for row in range(self.table_view.rowCount()) if not self.table_view.isRowHidden(row)]
        columns = [column for column in range(self.table_view.columnCount()) if not self.table_view.isColumnHidden(column)]
        QApplication.clipboard().setText(self._table_text(rows, columns))

    def _copy_all_table(self):
        QApplication.clipboard().setText(self._table_text(range(self.table_view.rowCount()), range(self.table_view.columnCount())))

    def _hide_selected_rows(self):
        if not self._current:
            return
        rows = {index.row() for index in self.table_view.selectionModel().selectedRows()}
        if not rows and self.table_view.currentRow() >= 0:
            rows = {self.table_view.currentRow()}
        self._hidden_rows.setdefault(self._current.get('id'), set()).update(rows)
        self._filter_workbook_rows(self._current)

    def _hide_current_column(self):
        if not self._current or self.table_view.currentColumn() < 0:
            return
        column = self.table_view.currentColumn()
        self._hidden_columns.setdefault(self._current.get('id'), set()).add(column)
        self.table_view.setColumnHidden(column, True)

    def _restore_hidden_table(self):
        if not self._current:
            return
        self._hidden_rows.pop(self._current.get('id'), None)
        self._hidden_columns.pop(self._current.get('id'), None)
        for column in range(self.table_view.columnCount()):
            self.table_view.setColumnHidden(column, False)
        self._filter_workbook_rows(self._current)

    def _edit_table_cell(self):
        if not self._current or self.table_view.currentRow() < 0 or self.table_view.currentColumn() < 0:
            show_info(self, '修改单元格', '请先选择要修改的单元格。')
            return
        row, column = self.table_view.currentRow(), self.table_view.currentColumn()
        item = self.table_view.item(row, column)
        old_value = item.text() if item else ''
        value, accepted = QInputDialog.getMultiLineText(self, '修改单元格', f'{self._column_name(column)}{row + 1}：', old_value)
        if not accepted:
            return
        if item is None:
            item = QTableWidgetItem(); self.table_view.setItem(row, column, item)
        item.setText(value); item.setToolTip(value)
        updated = copy.deepcopy(self._current)
        updated['rows'] = [
            [self.table_view.item(r, c).text() if self.table_view.item(r, c) else '' for c in range(self.table_view.columnCount())]
            for r in range(self.table_view.rowCount())
        ]
        self._persist_updated_entry(updated)

    def _export_table(self, visible_only):
        if not self._current:
            return
        suffix = '当前展示' if visible_only else '整表'
        suggested = f"{os.path.splitext(self._current.get('source', '学习资料'))[0]}_{self._current.get('sheet_name', 'Sheet')}_{suffix}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, f'导出{suffix}', suggested, 'Excel 工作簿 (*.xlsx)')
        if not path:
            return
        if not path.lower().endswith('.xlsx'):
            path += '.xlsx'
        rows = [row for row in range(self.table_view.rowCount()) if not self.table_view.isRowHidden(row)] if visible_only else None
        columns = [column for column in range(self.table_view.columnCount()) if not self.table_view.isColumnHidden(column)] if visible_only else None
        try:
            export_workbook_entry(self._current, path, rows, columns)
        except OSError as exc:
            show_warning(self, '导出 Excel', str(exc)); return
        show_success(self, '导出 Excel', f'已导出：\n{path}')

    def _save_word_document(self):
        if not self._current or self._current.get('content_type') != 'word_document':
            return
        updated = copy.deepcopy(self._current)
        updated['content'] = self.word_view.toPlainText().strip()
        updated['document_html'] = self.word_view.toHtml()
        self._persist_updated_entry(updated)
        show_success(self, 'Word 编辑器', 'Word 内容已保存到本机资料库。')

    def _export_word_document(self):
        if not self._current or self._current.get('content_type') != 'word_document':
            return
        suggested = f"{os.path.splitext(self._current.get('source', '学习资料'))[0]}_编辑后.docx"
        path, _ = QFileDialog.getSaveFileName(self, '导出 Word 文档', suggested, 'Word 文档 (*.docx)')
        if not path:
            return
        if not path.lower().endswith('.docx'):
            path += '.docx'
        entry = copy.deepcopy(self._current); entry['content'] = self.word_view.toPlainText(); entry['document_html'] = self.word_view.toHtml()
        try:
            export_word_entry(entry, path)
        except OSError as exc:
            show_warning(self, '导出 Word', str(exc)); return
        show_success(self, '导出 Word', f'已导出：\n{path}')

    def _append_entries(self, entries):
        existing = {entry_fingerprint(entry) for entry in self.all_entries()}
        unique = []
        for entry in entries:
            fingerprint = entry_fingerprint(entry)
            if fingerprint not in existing:
                unique.append(entry)
                existing.add(fingerprint)
        self._custom_entries.extend(unique)
        save_custom_entries(self._custom_entries)
        self.search_edit.clear()
        self.category_combo.setCurrentIndex(0)
        self._refresh()
        return len(unique), len(entries) - len(unique)

    def _paste_content(self):
        clipboard = QApplication.clipboard().text().strip()
        dialog = PasteKnowledgeDialog(clipboard, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entries = organize_content(dialog.text(), source='直接粘贴')
        added, duplicates = self._append_entries(entries)
        show_success(
            self, '自动整理完成', f'已自动整理并新增 {added} 条内容。\n跳过重复内容 {duplicates} 条。'
        )

    def _import_documents(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要整理的文档', '',
            '支持的文档 (*.txt *.md *.log *.sql *.json *.xml *.yaml *.yml *.csv *.docx *.xlsx)'
        )
        if not paths:
            return
        entries, errors = [], []
        for path in paths:
            try:
                if os.path.splitext(path)[1].lower() == '.xlsx':
                    entries.extend(extract_workbook_entries(path, source=os.path.basename(path)))
                else:
                    entries.extend(extract_document_entries(path))
            except PermissionError as exc:
                if str(exc) != 'PASSWORD_REQUIRED':
                    errors.append(f'{os.path.basename(path)}：{exc}')
                    continue
                password, accepted = QInputDialog.getText(
                    self, '工作簿密码', f'请输入 {os.path.basename(path)} 的打开密码：',
                    QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    errors.append(f'{os.path.basename(path)}：已取消密码输入')
                    continue
                try:
                    entries.extend(extract_workbook_entries(path, password, source=os.path.basename(path)))
                except (OSError, ValueError) as password_error:
                    errors.append(f'{os.path.basename(path)}：{password_error}')
            except (OSError, ValueError) as exc:
                errors.append(f'{os.path.basename(path)}：{exc}')
        added, duplicates = self._append_entries(entries)
        message = f'已从 {len(paths) - len(errors)} 个文档新增 {added} 条，跳过重复 {duplicates} 条。'
        if errors:
            message += '\n\n未导入：\n' + '\n'.join(errors)
        show_success(self, '文档整理完成', message)

    def _add_entry(self):
        dialog = KnowledgeEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        entry = organize_content(values['content'], source='手工新增')[0]
        entry.update(values)
        self._append_entries([entry])

    def _persist_updated_entry(self, updated):
        updated = copy.deepcopy(updated)
        was_builtin = bool(updated.get('builtin') or updated.get('builtin_source'))
        if updated.get('builtin'):
            updated['base_seed_id'] = updated.get('id')
        updated['builtin'] = False
        updated['builtin_source'] = was_builtin
        updated['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        key = updated.get('base_seed_id') or updated.get('id')
        self._custom_entries = [
            entry for entry in self._custom_entries
            if (entry.get('base_seed_id') or entry.get('id')) != key
        ]
        self._custom_entries.append(updated)
        self._current = updated
        save_custom_entries(self._custom_entries)
        self._refresh()

    def _edit_entry(self):
        if not self._current:
            return
        dialog = KnowledgeEditDialog(self._current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = copy.deepcopy(self._current)
        updated.update(dialog.values())
        self._persist_updated_entry(updated)

    def _workbook_entries_with_password(self, path):
        try:
            return extract_workbook_entries(path, source=os.path.basename(path))
        except PermissionError as exc:
            if str(exc) != 'PASSWORD_REQUIRED':
                raise
            password, accepted = QInputDialog.getText(
                self, '工作簿密码', f'请输入 {os.path.basename(path)} 的打开密码：',
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return []
            return extract_workbook_entries(path, password, source=os.path.basename(path))

    def _update_entry_file(self):
        if not self._current:
            return
        is_table = self._current.get('content_type') == 'workbook_sheet'
        file_filter = 'Excel 工作簿 (*.xlsx)' if is_table else '文档 (*.txt *.md *.log *.sql *.json *.xml *.yaml *.yml *.csv *.docx)'
        path, _ = QFileDialog.getOpenFileName(self, '选择更新后的文件', '', file_filter)
        if not path:
            return
        try:
            if is_table:
                candidates = self._workbook_entries_with_password(path)
                if not candidates:
                    return
                replacement = next((entry for entry in candidates if entry.get('sheet_name') == self._current.get('sheet_name')), None)
                if replacement is None and len(candidates) > 1:
                    names = [entry.get('sheet_name', '') for entry in candidates]
                    name, accepted = QInputDialog.getItem(self, '选择工作表', '用哪个工作表更新当前资料：', names, 0, False)
                    if not accepted:
                        return
                    replacement = candidates[names.index(name)]
                replacement = replacement or candidates[0]
            else:
                organized = extract_document_entries(path)
                if not organized:
                    raise ValueError('文件中没有可更新的内容')
                replacement = organized[0]
        except (OSError, ValueError, PermissionError) as exc:
            show_warning(self, '更新资料', str(exc)); return
        replacement['id'] = self._current.get('id')
        replacement['title'] = self._current.get('title', replacement.get('title'))
        replacement['base_seed_id'] = self._current.get('base_seed_id')
        replacement['builtin'] = self._current.get('builtin', False)
        replacement['builtin_source'] = self._current.get('builtin_source', False)
        self._persist_updated_entry(replacement)
        show_success(self, '更新资料', '当前资料已经更新并保存到本机。')

    def _delete_entry(self):
        if not self._current or self._current.get('builtin'):
            return
        restore = bool(self._current.get('builtin_source'))
        prompt = f"确定恢复“{self._current.get('title')}”的内置原版吗？" if restore else f"确定删除“{self._current.get('title')}”吗？"
        if not confirm_action(
            self, '恢复内置资料' if restore else '删除内容', prompt,
            '确认恢复' if restore else '确认删除',
        ):
            return
        key = self._current.get('base_seed_id') or self._current.get('id')
        self._custom_entries = [entry for entry in self._custom_entries if (entry.get('base_seed_id') or entry.get('id')) != key]
        self._current = None
        save_custom_entries(self._custom_entries)
        self._refresh()

    def _copy_entry(self):
        if self._current:
            if self._current.get('content_type') == 'workbook_sheet':
                content = '\n'.join('\t'.join(map(str, row)) for row in self._current.get('rows', []))
            else:
                content = self._current.get('content', '')
            QApplication.clipboard().setText(content)
            # 不改按钮文案，避免点击后出现“已复制”闪烁


class DailyReportTab(QWidget):
    reminder_due = pyqtSignal(str, str)
    _REPORT_FIELDS = ('completed', 'issues', 'tomorrow', 'notes')

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._reports = load_reports()
        self._reminder = load_reminder_settings()
        self._loading = False
        self._loaded_key = ''
        # 未点保存的编辑缓存：切日期/历史后仍可恢复
        self._drafts: dict[str, dict] = {}
        self._setup_ui()
        self._refresh_dates()
        self._load_date(QDate.currentDate())
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_reminder)
        self._timer.start(30000)
        QTimer.singleShot(1200, self._check_reminder)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        # 提醒设置迁入设置页；此处仅简短状态 + 入口
        reminder = QFrame()
        reminder.setObjectName('private-reminder-card')
        reminder_layout = QHBoxLayout(reminder)
        reminder_layout.setContentsMargins(10, 6, 10, 6)
        self.reminder_status = QLabel()
        self.reminder_status.setObjectName('small-label')
        reminder_layout.addWidget(self.reminder_status, 1)
        self.reminder_settings_btn = QPushButton('提醒设置')
        self.reminder_settings_btn.setProperty('compactAction', True)
        self.reminder_settings_btn.setObjectName('ghost-btn')
        self.reminder_settings_btn.clicked.connect(self._open_reminder_settings)
        reminder_layout.addWidget(self.reminder_settings_btn)
        # 兼容旧引用（测试/外部）
        self.reminder_enabled = QCheckBox()
        self.reminder_enabled.hide()
        self.reminder_enabled.setChecked(self._reminder['enabled'])
        self.reminder_time = QTimeEdit()
        self.reminder_time.hide()
        self.reminder_time.setDisplayFormat('HH:mm')
        self.reminder_time.setTime(QTime.fromString(self._reminder['time'], 'HH:mm'))
        self.save_reminder_btn = QPushButton()
        self.save_reminder_btn.hide()
        self.reminder_hint = self.reminder_status
        root.addWidget(reminder)
        self._refresh_reminder_status()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QFrame()
        left.setObjectName('ops-list-card')
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel('日报历史'))
        self.date_list = QListWidget()
        self.date_list.setObjectName('ops-command-list')
        self.date_list.currentItemChanged.connect(self._select_history)
        left_layout.addWidget(self.date_list)
        splitter.addWidget(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor = QWidget()
        form_layout = QVBoxLayout(editor)
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_row.addWidget(QLabel('日报日期'))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setObjectName('release-date')
        self.date_edit.setDisplayFormat('yyyy-MM-dd')
        size_date(self.date_edit)
        self.date_edit.dateChanged.connect(self._on_date_edit_changed)
        date_row.addWidget(self.date_edit)
        self.today_btn = QPushButton('今天')
        size_compact_button(self.today_btn)
        self.today_btn.clicked.connect(self._go_today)
        date_row.addWidget(self.today_btn)
        self.copy_as_today_btn = QPushButton('复制为今日')
        size_compact_button(self.copy_as_today_btn)
        self.copy_as_today_btn.setToolTip('把当前编辑中的内容一键写成今天的日报（可再改再保存）')
        self.copy_as_today_btn.clicked.connect(self._copy_as_today)
        date_row.addWidget(self.copy_as_today_btn)
        date_row.addStretch()
        self.unsaved_label = QLabel('')
        self.unsaved_label.setObjectName('field-hint')
        date_row.addWidget(self.unsaved_label)
        form_layout.addLayout(date_row)
        self.completed = self._report_editor(form_layout, '今日完成', '完成的需求、问题处理、沟通结果……')
        self.issues = self._report_editor(form_layout, '问题与风险', '阻塞、风险、需要协助的事项；没有可留空……')
        self.tomorrow = self._report_editor(form_layout, '明日计划', '下一步准备完成的事项……')
        self.notes = self._report_editor(form_layout, '备注', '补充信息、链接或待跟踪内容……', 70)
        for ed in (self.completed, self.issues, self.tomorrow, self.notes):
            ed.textChanged.connect(self._on_editor_changed)
        actions = QHBoxLayout()
        self.delete_btn = QPushButton('删除当日日报')
        self.delete_btn.setObjectName('ops-delete-custom')
        self.delete_btn.clicked.connect(self._delete_report)
        actions.addWidget(self.delete_btn)
        actions.addStretch()
        self.copy_btn = QPushButton('复制 Markdown')
        self.copy_btn.clicked.connect(self._copy_report)
        actions.addWidget(self.copy_btn)
        self.save_btn = QPushButton('保存日报')
        self.save_btn.setObjectName('primary-btn')
        self.save_btn.clicked.connect(self._save_report)
        actions.addWidget(self.save_btn)
        form_layout.addLayout(actions)
        scroll.setWidget(editor)
        splitter.addWidget(scroll)
        splitter.setSizes([235, 790])
        root.addWidget(splitter, 1)

    def _report_editor(self, layout, title, placeholder, height=105):
        label = QLabel(title)
        label.setObjectName('section-title')
        layout.addWidget(label)
        editor = QPlainTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setMinimumHeight(height)
        layout.addWidget(editor)
        return editor

    def _date_key(self):
        return self.date_edit.date().toString('yyyy-MM-dd')

    def _current_values(self):
        return {
            'completed': self.completed.toPlainText().strip(),
            'issues': self.issues.toPlainText().strip(),
            'tomorrow': self.tomorrow.toPlainText().strip(),
            'notes': self.notes.toPlainText().strip(),
            'updated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        }

    def _fields_snapshot(self, source: dict | None = None) -> dict:
        src = source if isinstance(source, dict) else {}
        return {k: str(src.get(k) or '').strip() for k in self._REPORT_FIELDS}

    def _is_dirty(self, key: str, values: dict | None = None) -> bool:
        vals = self._fields_snapshot(values if values is not None else self._current_values())
        saved = self._fields_snapshot(self._reports.get(key) or {})
        return any(vals[k] != saved[k] for k in self._REPORT_FIELDS)

    def _stash_current_editors(self):
        """切换日期前：把当前未保存编辑收进草稿缓存。"""
        if self._loading or not self._loaded_key:
            return
        values = self._current_values()
        if self._is_dirty(self._loaded_key, values):
            self._drafts[self._loaded_key] = self._fields_snapshot(values)
        else:
            self._drafts.pop(self._loaded_key, None)

    def _update_unsaved_hint(self):
        if not hasattr(self, 'unsaved_label'):
            return
        key = self._loaded_key or self._date_key()
        if self._is_dirty(key):
            self.unsaved_label.setText('● 未保存（切换日期会保留草稿）')
        else:
            self.unsaved_label.setText('')

    def _on_editor_changed(self):
        if self._loading:
            return
        self._update_unsaved_hint()

    def _go_today(self):
        today = QDate.currentDate()
        if self.date_edit.date() != today:
            self.date_edit.setDate(today)
        else:
            self._load_date(today)

    def _on_date_edit_changed(self, date_value):
        self._load_date(date_value)

    def _refresh_dates(self):
        current = self._date_key() if hasattr(self, 'date_edit') else ''
        today = datetime.date.today().isoformat()
        keys = set(self._reports) | set(self._drafts)
        if current:
            keys.add(current)
        keys.add(today)
        self.date_list.blockSignals(True)
        self.date_list.clear()
        selected = None
        for date_value in sorted(keys, reverse=True):
            label = date_value
            if date_value == today:
                label = f'{date_value}（今天）'
            if date_value in self._drafts and self._is_dirty(date_value, self._drafts[date_value]):
                label = f'{label} · 未保存'
            elif date_value not in self._reports:
                label = f'{label} · 未写'
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, date_value)
            self.date_list.addItem(item)
            if date_value == current:
                selected = item
        if selected is not None:
            self.date_list.setCurrentItem(selected)
        self.date_list.blockSignals(False)

    def _load_date(self, date_value):
        if self._loading:
            return
        # 先暂存当前编辑中的内容
        self._stash_current_editors()
        self._loading = True
        try:
            if isinstance(date_value, QDate):
                key = date_value.toString('yyyy-MM-dd')
                if self.date_edit.date() != date_value:
                    self.date_edit.blockSignals(True)
                    self.date_edit.setDate(date_value)
                    self.date_edit.blockSignals(False)
            else:
                key = str(date_value or '').strip()
                parsed = QDate.fromString(key, 'yyyy-MM-dd')
                if parsed.isValid() and self.date_edit.date() != parsed:
                    self.date_edit.blockSignals(True)
                    self.date_edit.setDate(parsed)
                    self.date_edit.blockSignals(False)
            if not key:
                key = self._date_key()
            # 优先未保存草稿，再回落已保存
            report = self._drafts.get(key) or self._reports.get(key) or {}
            self.completed.setPlainText(report.get('completed', ''))
            self.issues.setPlainText(report.get('issues', ''))
            self.tomorrow.setPlainText(report.get('tomorrow', ''))
            self.notes.setPlainText(report.get('notes', ''))
            self._loaded_key = key
            self.delete_btn.setEnabled(key in self._reports)
            self._refresh_dates()
            self._update_unsaved_hint()
        finally:
            self._loading = False

    def _select_history(self, current, _previous=None):
        if not current or self._loading:
            return
        date_value = current.data(Qt.ItemDataRole.UserRole)
        date = QDate.fromString(str(date_value), 'yyyy-MM-dd')
        if not date.isValid():
            return
        if date != self.date_edit.date():
            self.date_edit.setDate(date)
        else:
            self._load_date(date)

    def _save_report(self):
        key = self._date_key()
        self._reports[key] = self._current_values()
        self._drafts.pop(key, None)
        save_reports(self._reports)
        self._loaded_key = key
        self._refresh_dates()
        self.delete_btn.setEnabled(True)
        self._update_unsaved_hint()
        show_success(self, '日报', '日报已保存到本机。')

    def _copy_report(self):
        QApplication.clipboard().setText(report_markdown(self._date_key(), self._current_values()))

    def _copy_as_today(self):
        """把当前编辑内容（含未保存）一键写成今天的日报草稿。"""
        source_key = self._loaded_key or self._date_key()
        payload = self._fields_snapshot(self._current_values())
        if not any(payload.values()):
            show_warning(self, '日报', '当前内容为空，没有可复制的内容。')
            return
        today = datetime.date.today().isoformat()
        today_date = QDate.currentDate()
        if source_key == today and not self._is_dirty(today, payload):
            show_info(self, '日报', '已经是今天的日报了。')
            return
        # 暂存源日草稿后切到今天并写入
        self._stash_current_editors()
        self._drafts[today] = dict(payload)
        self._loading = True
        try:
            if self.date_edit.date() != today_date:
                self.date_edit.blockSignals(True)
                self.date_edit.setDate(today_date)
                self.date_edit.blockSignals(False)
            self.completed.setPlainText(payload.get('completed', ''))
            self.issues.setPlainText(payload.get('issues', ''))
            self.tomorrow.setPlainText(payload.get('tomorrow', ''))
            self.notes.setPlainText(payload.get('notes', ''))
            self._loaded_key = today
            self.delete_btn.setEnabled(today in self._reports)
            self._refresh_dates()
            self._update_unsaved_hint()
        finally:
            self._loading = False
        show_success(
            self, '日报',
            f'已把 {source_key} 的内容复制为今日（{today}）草稿，请确认后点「保存日报」。',
        )

    def _delete_report(self):
        key = self._date_key()
        if key not in self._reports and key not in self._drafts:
            return
        if not confirm_action(self, '删除日报', f'即将删除 {key} 的日报。\n\n删除后无法恢复，是否继续？'):
            return
        self._reports.pop(key, None)
        self._drafts.pop(key, None)
        save_reports(self._reports)
        self._loaded_key = ''
        self._refresh_dates()
        self._load_date(self.date_edit.date())

    def _refresh_reminder_status(self):
        self._reminder = load_reminder_settings()
        enabled = bool(self._reminder.get('enabled'))
        time_text = self._reminder.get('time') or '18:00'
        self.reminder_status.setText(
            f'提醒已开启 · {time_text}' if enabled else '提醒未开启'
        )
        self.reminder_enabled.setChecked(enabled)
        try:
            self.reminder_time.setTime(QTime.fromString(str(time_text), 'HH:mm'))
        except Exception:
            pass

    def _open_reminder_settings(self):
        parent = self.window()
        if hasattr(parent, 'navigate_to'):
            parent.navigate_to(7)
            return

    def _save_reminder(self):
        previous_time = self._reminder.get('time')
        selected_time = self.reminder_time.time().toString('HH:mm')
        self._reminder.update({'enabled': self.reminder_enabled.isChecked(), 'time': selected_time})
        if selected_time != previous_time:
            self._reminder['last_reminder_date'] = ''
        self._reminder = save_reminder_settings(self._reminder)
        self._refresh_reminder_status()
        show_success(self, '日报提醒', '提醒设置已保存。')

    def _check_reminder(self):
        self._reminder = load_reminder_settings()
        if not is_reminder_due(self._reminder):
            return
        today = datetime.date.today().isoformat()
        self._reminder['last_reminder_date'] = today
        self._reminder = save_reminder_settings(self._reminder)
        self._refresh_reminder_status()
        self.reminder_due.emit('PengToolsHub · 日报提醒', '到时间啦，记得整理今天的完成事项和明日计划。')

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_reminder_status()

    def add_requirement(self, requirement):
        self.date_edit.setDate(QDate.currentDate())
        template = daily_template(requirement)
        for editor, key in ((self.completed, 'completed'), (self.tomorrow, 'tomorrow'), (self.notes, 'notes')):
            current = editor.toPlainText().strip()
            addition = template[key]
            if addition not in current:
                editor.setPlainText('\n'.join(part for part in (current, addition) if part))
        self._save_report()


class PersonalPanel(QWidget):
    reminder_due = pyqtSignal(str, str)

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.knowledge_tab = KnowledgeTab(language)
        self.daily_tab = DailyReportTab(language)
        self.daily_tab.reminder_due.connect(self.reminder_due.emit)
        self.stack.addWidget(self.knowledge_tab)
        self.stack.addWidget(self.daily_tab)
        root.addWidget(self.stack, 1)
        self.set_language(language)

    def apply_layout_mode(self, mode, low_height=False):
        if hasattr(self.knowledge_tab, 'apply_layout_mode'):
            self.knowledge_tab.apply_layout_mode(mode, low_height)
        if hasattr(self.daily_tab, 'apply_layout_mode'):
            try:
                self.daily_tab.apply_layout_mode(mode, low_height)
            except Exception:
                pass

    def set_language(self, language):
        self.language = language
        self.knowledge_tab.set_language(language)

    def open_daily_report(self):
        self.stack.setCurrentWidget(self.daily_tab)

    def open_learning(self):
        self.stack.setCurrentWidget(self.knowledge_tab)

    def add_requirement_to_daily(self, requirement):
        self.open_daily_report()
        self.daily_tab.add_requirement(requirement)
