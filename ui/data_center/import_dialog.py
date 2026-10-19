"""Manual CSV/XLSX import dialog for one captured signed table selection."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tools.data_center.import_preview import (
    ColumnMapping,
    CsvSource,
    ImportPreview,
    ImportPreviewError,
    ImportValueKind,
    NullRule,
    SourcePreview,
    TargetColumn,
    XlsxSource,
)
from tools.data_center.import_session import ImportSessionError, ImportTargetCandidate
from ui.data_center.import_controller import ImportController, ImportProgress
from ui.design_system import apply_button
from ui.dialog_buttons import clamp_dialog_geometry


class ManualImportDialog(QDialog):
    """Source options, indexed field mapping, bounded preview, and decisions."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        controller: ImportController,
        language: str = "zh",
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._zh = str(language or "zh").casefold().startswith("zh")
        self._mapping_rows: list[tuple[int, QComboBox, QLineEdit]] = []
        self._last_progress: ImportProgress | None = None
        self.setObjectName("manual-import-dialog")
        self.setWindowTitle(self._tr("导入到所选表", "Import into selected table"))
        self.setModal(True)
        self.controller.attach_dialog(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QFrame(self)
        header.setObjectName("manual-import-header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(4, 2, 4, 4)
        header_layout.setSpacing(4)
        title = QLabel(self._tr("文件导入", "File import"), header)
        title.setObjectName("manual-import-title")
        target = self.controller.context.table_result.metadata
        target_text = self._tr("连接", "Connection") + f": {self.controller.context.choice.connection_id}"
        target_text += " · " + self._tr("数据库", "Database") + f": {target.target.database}"
        if target.target.schema and target.target.schema != target.target.database:
            target_text += " · " + self._tr("所有者", "Owner") + f": {target.target.schema}"
        self.target_label = QLabel(target_text, header)
        self.target_label.setObjectName("manual-import-target")
        self.target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.target_label.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(self.target_label)
        root.addWidget(header)

        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("manual-import-content-scroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget(self.content_scroll)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(2, 2, 8, 6)
        body_layout.setSpacing(12)
        self._build_source_options(body_layout)
        self._build_mapping(body_layout)
        self._build_preview(body_layout)
        body_layout.addStretch(1)
        self.content_scroll.setWidget(body)
        root.addWidget(self.content_scroll, 1)

        self.status_label = QLabel(self._tr("选择文件并读取有限预览。", "Choose a file and inspect a bounded preview."), self)
        self.status_label.setObjectName("manual-import-status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._build_footer(root)
        self.controller.progress_changed.connect(self._on_progress)
        self.controller.preview_ready.connect(self._on_preview_ready)
        self.controller.operation_finished.connect(self._on_operation_finished)
        self._refresh_actions(self.controller.progress())
        self._fit_geometry()

    def _build_source_options(self, parent_layout: QVBoxLayout) -> None:
        source_frame = QFrame(self)
        source_frame.setObjectName("manual-import-source-card")
        form = QFormLayout(source_frame)
        form.setContentsMargins(14, 12, 14, 12)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.path_edit = QLineEdit(source_frame)
        self.path_edit.setObjectName("manual-import-source-path")
        self.path_edit.setClearButtonEnabled(True)
        self.browse_button = QPushButton(self._tr("选择文件…", "Choose file…"), source_frame)
        self.browse_button.setObjectName("manual-import-browse")
        apply_button(self.browse_button, "secondary", compact=True)
        self.browse_button.clicked.connect(self._browse_source)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        path_host = QWidget(source_frame)
        path_host.setLayout(path_row)

        self.encoding_combo = QComboBox(source_frame)
        self.encoding_combo.setObjectName("manual-import-encoding")
        self.encoding_combo.setEditable(True)
        for name in ("utf-8", "utf-8-sig", "gb18030", "big5", "latin-1"):
            self.encoding_combo.addItem(name, name)
        self.delimiter_edit = QLineEdit(",", source_frame)
        self.delimiter_edit.setObjectName("manual-import-delimiter")
        self.delimiter_edit.setMaxLength(1)
        self.delimiter_edit.setMaximumWidth(90)
        self.sheet_edit = QLineEdit(source_frame)
        self.sheet_edit.setObjectName("manual-import-sheet")
        self.sheet_edit.setPlaceholderText(self._tr("填写工作表名称", "Enter worksheet name"))
        self.inspect_button = QPushButton(self._tr("读取表头与预览", "Read header and preview"), source_frame)
        self.inspect_button.setObjectName("manual-import-inspect")
        apply_button(self.inspect_button, "primary", compact=True)
        self.inspect_button.clicked.connect(self._inspect_source)

        self.encoding_label = QLabel(self._tr("CSV 编码", "CSV encoding"), source_frame)
        self.delimiter_label = QLabel(self._tr("分隔符", "Delimiter"), source_frame)
        self.sheet_label = QLabel(self._tr("XLSX 工作表", "XLSX worksheet"), source_frame)
        self.csv_options = QWidget(source_frame)
        csv_row = QHBoxLayout(self.csv_options)
        csv_row.setContentsMargins(0, 0, 0, 0)
        csv_row.setSpacing(8)
        csv_row.addWidget(self.encoding_label)
        csv_row.addWidget(self.encoding_combo)
        csv_row.addWidget(self.delimiter_label)
        csv_row.addWidget(self.delimiter_edit)

        form.addRow(self._tr("来源文件", "Source file"), path_host)
        self.csv_options_label = QLabel(self._tr("CSV 选项", "CSV options"), source_frame)
        form.addRow(self.csv_options_label, self.csv_options)
        form.addRow(self.sheet_label, self.sheet_edit)
        form.addRow("", self.inspect_button)
        parent_layout.addWidget(source_frame)
        self.path_edit.textChanged.connect(self._update_source_format)
        self.path_edit.textChanged.connect(self._source_options_changed)
        self.encoding_combo.currentTextChanged.connect(self._source_options_changed)
        self.delimiter_edit.textChanged.connect(self._source_options_changed)
        self.sheet_edit.textChanged.connect(self._source_options_changed)
        self._update_source_format()

        null_frame = QFrame(self)
        null_frame.setObjectName("manual-import-null-card")
        null_layout = QVBoxLayout(null_frame)
        null_layout.setContentsMargins(14, 8, 14, 8)
        null_layout.setSpacing(6)
        self.null_tokens_edit = QLineEdit("NULL", null_frame)
        self.null_tokens_edit.setObjectName("manual-import-null-tokens")
        self.null_tokens_edit.setMaximumWidth(190)
        self.csv_empty_null = QCheckBox(self._tr("CSV 空字符串视为 NULL", "CSV empty string is NULL"), null_frame)
        self.csv_empty_null.setObjectName("manual-import-csv-empty-null")
        self.xlsx_empty_null = QCheckBox(self._tr("XLSX 空字符串视为 NULL", "XLSX empty string is NULL"), null_frame)
        self.xlsx_empty_null.setObjectName("manual-import-xlsx-empty-null")
        self.xlsx_blank_null = QCheckBox(self._tr("XLSX 空白单元格视为 NULL", "XLSX blank cell is NULL"), null_frame)
        self.xlsx_blank_null.setObjectName("manual-import-xlsx-blank-null")
        self.xlsx_blank_null.setChecked(True)
        null_header = QHBoxLayout()
        null_header.addWidget(QLabel(self._tr("NULL 标记", "NULL tokens"), null_frame))
        null_header.addWidget(self.null_tokens_edit)
        null_header.addStretch(1)
        null_layout.addLayout(null_header)
        # Keep each policy label on a full-width row. A single horizontal row
        # clips the XLSX labels at narrow widths and larger UI font sizes.
        null_layout.addWidget(self.csv_empty_null)
        null_layout.addWidget(self.xlsx_empty_null)
        null_layout.addWidget(self.xlsx_blank_null)
        parent_layout.addWidget(null_frame)
        self.null_tokens_edit.textChanged.connect(self._mapping_options_changed)
        self.csv_empty_null.toggled.connect(self._mapping_options_changed)
        self.xlsx_empty_null.toggled.connect(self._mapping_options_changed)
        self.xlsx_blank_null.toggled.connect(self._mapping_options_changed)

    def _build_mapping(self, parent_layout: QVBoxLayout) -> None:
        self.mapping_title = QLabel(self._tr("按来源列序号映射", "Map by source column index"), self)
        self.mapping_title.setObjectName("manual-import-mapping-title")
        mapping_heading = QHBoxLayout()
        mapping_heading.addWidget(self.mapping_title, 1)
        self.preview_button = QPushButton(self._tr("生成映射预览", "Preview mapping"), self)
        self.preview_button.setObjectName("manual-import-preview-mapping")
        apply_button(self.preview_button, "secondary", compact=True)
        self.preview_button.clicked.connect(self._preview_mapping)
        mapping_heading.addWidget(self.preview_button)
        parent_layout.addLayout(mapping_heading)
        self.mapping_table = QTableWidget(0, 4, self)
        self.mapping_table.setObjectName("manual-import-mapping-table")
        self.mapping_table.setHorizontalHeaderLabels((
            self._tr("来源列号", "Source column"),
            self._tr("来源表头", "Source header"),
            self._tr("目标字段（* 必填）", "Target field (* required)"),
            self._tr("日期格式", "Date format"),
        ))
        self.mapping_table.setAlternatingRowColors(True)
        self.mapping_table.setShowGrid(False)
        self.mapping_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.mapping_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.mapping_table.verticalHeader().setVisible(False)
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.mapping_table.setColumnWidth(3, 190)
        self.mapping_table.setMinimumHeight(150)
        self.mapping_table.setMaximumHeight(250)
        parent_layout.addWidget(self.mapping_table)
        self.excluded_label = QLabel(self)
        self.excluded_label.setObjectName("manual-import-excluded-fields")
        self.excluded_label.setWordWrap(True)
        parent_layout.addWidget(self.excluded_label)

    def _build_preview(self, parent_layout: QVBoxLayout) -> None:
        title = QLabel(self._tr("有限预览", "Bounded preview"), self)
        title.setObjectName("manual-import-preview-title")
        parent_layout.addWidget(title)
        self.preview_table = QTableWidget(0, 0, self)
        self.preview_table.setObjectName("manual-import-preview-table")
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setShowGrid(False)
        self.preview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.preview_table.setMinimumHeight(112)
        self.preview_table.setMaximumHeight(230)
        parent_layout.addWidget(self.preview_table)
        self.issue_label = QLabel(self)
        self.issue_label.setObjectName("manual-import-issues")
        self.issue_label.setWordWrap(True)
        parent_layout.addWidget(self.issue_label)

    def _build_footer(self, root: QVBoxLayout) -> None:
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.stop_button = QPushButton(self._tr("停止", "Stop"), self)
        self.stop_button.setObjectName("manual-import-stop")
        self.start_button = QPushButton(self._tr("开始导入", "Start import"), self)
        self.start_button.setObjectName("manual-import-start")
        self.commit_button = QPushButton(self._tr("提交", "Commit"), self)
        self.commit_button.setObjectName("manual-import-commit")
        self.rollback_button = QPushButton(self._tr("回滚", "Rollback"), self)
        self.rollback_button.setObjectName("manual-import-rollback")
        self.retry_button = QPushButton(self._tr("重试清理", "Retry cleanup"), self)
        self.retry_button.setObjectName("manual-import-retry-cleanup")
        self.close_button = QPushButton(self._tr("关闭", "Close"), self)
        self.close_button.setObjectName("manual-import-close")
        for button, role in (
            (self.stop_button, "secondary"),
            (self.start_button, "primary"),
            (self.commit_button, "primary"),
            (self.rollback_button, "danger"),
            (self.retry_button, "secondary"),
            (self.close_button, "secondary"),
        ):
            apply_button(button, role, compact=True)
        self.stop_button.clicked.connect(self._stop)
        self.start_button.clicked.connect(self._start_import)
        self.commit_button.clicked.connect(self.controller.commit)
        self.rollback_button.clicked.connect(self.controller.rollback)
        self.retry_button.clicked.connect(self.controller.retry_cleanup)
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.stop_button)
        footer.addStretch(1)
        footer.addWidget(self.retry_button)
        footer.addWidget(self.rollback_button)
        footer.addWidget(self.commit_button)
        footer.addWidget(self.start_button)
        footer.addWidget(self.close_button)
        root.addLayout(footer)

    def _tr(self, zh: str, en: str) -> str:
        return zh if self._zh else en

    def _browse_source(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self._tr("选择 CSV 或 XLSX 文件", "Choose a CSV or XLSX file"),
            self.path_edit.text().strip(),
            "CSV/XLSX (*.csv *.xlsx);;CSV (*.csv);;Excel (*.xlsx)",
        )
        if path:
            self.path_edit.setText(path)

    def _update_source_format(self) -> None:
        xlsx = Path(self.path_edit.text().strip()).suffix.casefold() == ".xlsx"
        self.csv_options.setVisible(not xlsx)
        self.csv_options_label.setVisible(not xlsx)
        self.sheet_label.setVisible(xlsx)
        self.sheet_edit.setVisible(xlsx)

    def _source_options_changed(self, *_args) -> None:
        if self.controller.clear_preview():
            self.mapping_table.setRowCount(0)
            self._mapping_rows.clear()
            self.excluded_label.clear()
            self.preview_table.clear()
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            self.issue_label.clear()
            self._refresh_actions(self.controller.progress())

    def _mapping_options_changed(self, *_args) -> None:
        if self.controller.clear_mapping_preview():
            self.preview_table.clear()
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            self.issue_label.clear()
            self._refresh_actions(self.controller.progress())

    def _source_from_form(self):
        path = self.path_edit.text().strip()
        suffix = Path(path).suffix.casefold()
        if not path:
            raise ImportPreviewError("INVALID_SOURCE")
        if suffix == ".csv":
            delimiter = self.delimiter_edit.text()
            if len(delimiter) != 1:
                raise ImportPreviewError("INVALID_OPTIONS")
            encoding = self.encoding_combo.currentText().strip()
            return CsvSource(path, encoding=encoding, delimiter=delimiter)
        if suffix == ".xlsx":
            sheet = self.sheet_edit.text().strip()
            if not sheet:
                raise ImportPreviewError("INVALID_SHEET")
            return XlsxSource(path, sheet_name=sheet)
        raise ImportPreviewError("INVALID_FORMAT")

    def _inspect_source(self) -> None:
        try:
            source = self._source_from_form()
        except ImportPreviewError as error:
            self.status_label.setText(self._error_text(error.code))
            return
        if self.controller.inspect_source(source):
            self.status_label.setText(self._tr("正在读取有限预览…", "Reading a bounded preview…"))
            self._refresh_actions(self.controller.progress())

    def _populate_mapping(self, source_preview: SourcePreview) -> None:
        self.mapping_table.setRowCount(len(source_preview.columns))
        self._mapping_rows.clear()
        candidate_options = tuple(
            item for item in self.controller.target_candidates.columns if item.target is not None
        )
        mapped_target_names: set[str] = set()
        for row, source_column in enumerate(source_preview.columns):
            source_index = source_column.index
            source_item = QTableWidgetItem(str(source_index + 1))
            source_item.setData(Qt.ItemDataRole.UserRole, source_index)
            source_item.setToolTip(self._tr(
                f"来源列号 {source_index + 1}（内部索引 {source_index}）；重复表头按列号分别映射。",
                f"Source column {source_index + 1} (zero-based index {source_index}); duplicate headers map by column.",
            ))
            header_item = QTableWidgetItem(source_column.label or self._tr("（空表头）", "(blank header)"))
            header_item.setToolTip(self._tr(
                f"来源索引 {source_index}", f"Source index {source_index}"
            ))
            self.mapping_table.setItem(row, 0, source_item)
            self.mapping_table.setItem(row, 1, header_item)

            target_combo = QComboBox(self.mapping_table)
            target_combo.addItem(self._tr("跳过此列", "Skip this column"), None)
            selected_index = 0
            for candidate in candidate_options:
                target = candidate.target
                if target is None:
                    continue
                label = self._target_label(candidate)
                target_combo.addItem(label, target)
                target_combo.setItemData(
                    target_combo.count() - 1,
                    self._target_tooltip(candidate),
                    Qt.ItemDataRole.ToolTipRole,
                )
                if source_column.label == target.name and target.name not in mapped_target_names:
                    selected_index = target_combo.count() - 1
                    mapped_target_names.add(target.name)
            target_combo.setCurrentIndex(selected_index)
            target_combo.setToolTip(str(
                target_combo.currentData(Qt.ItemDataRole.ToolTipRole) or ""
            ))
            self.mapping_table.setCellWidget(row, 2, target_combo)

            date_edit = QLineEdit(self.mapping_table)
            date_edit.setPlaceholderText("%Y-%m-%d")
            date_edit.setEnabled(False)
            self.mapping_table.setCellWidget(row, 3, date_edit)
            target_combo.currentIndexChanged.connect(
                lambda _index, combo=target_combo, edit=date_edit: self._sync_date_format(combo, edit)
            )
            target_combo.currentIndexChanged.connect(
                lambda _index, combo=target_combo: combo.setToolTip(str(
                    combo.currentData(Qt.ItemDataRole.ToolTipRole) or ""
                ))
            )
            self._sync_date_format(target_combo, date_edit)
            target_combo.currentIndexChanged.connect(self._mapping_options_changed)
            date_edit.textChanged.connect(self._mapping_options_changed)
            self._mapping_rows.append((source_index, target_combo, date_edit))

        excluded = [item for item in self.controller.target_candidates.columns if not item.supported]
        if excluded:
            details = " · ".join(f"{item.name}: {item.reason_code}" for item in excluded)
            self.excluded_label.setText(self._tr(
                "不可映射字段：" + details,
                "Unavailable target fields: " + details,
            ))
        elif self.controller.target_candidates.blocking_reason_codes:
            self.excluded_label.setText(", ".join(self.controller.target_candidates.blocking_reason_codes))
        else:
            self.excluded_label.clear()
        self.preview_table.clear()
        self.preview_table.setRowCount(0)
        self.preview_table.setColumnCount(0)
        self.issue_label.clear()
        self.status_label.setText(self._tr(
            f"已读取 {len(source_preview.rows)} 行预览；现在按来源列序号选择目标字段。",
            f"Read {len(source_preview.rows)} preview rows; map target fields by source index.",
        ))
        self._fit_geometry()

    def _sync_date_format(self, combo: QComboBox, edit: QLineEdit) -> None:
        target = combo.currentData()
        if not isinstance(target, TargetColumn) or target.kind not in {
            ImportValueKind.DATE, ImportValueKind.DATETIME,
        }:
            edit.setEnabled(False)
            edit.clear()
            return
        edit.setEnabled(True)
        if not edit.text().strip():
            edit.setText("%Y-%m-%d" if target.kind is ImportValueKind.DATE else "%Y-%m-%d %H:%M:%S")

    def _target_label(self, candidate: ImportTargetCandidate) -> str:
        target = candidate.target
        if target is None:
            return candidate.name
        if target.kind is ImportValueKind.DECIMAL:
            kind = "DEC"
        else:
            kind = target.kind.value.upper()
        required = " *" if candidate.required_on_insert else ""
        return f"{target.name} · {kind}{required}"

    def _target_tooltip(self, candidate: ImportTargetCandidate) -> str:
        target = candidate.target
        if target is None:
            return candidate.reason_code or candidate.name
        if target.kind is ImportValueKind.DECIMAL:
            kind = f"DECIMAL({target.decimal_precision},{target.decimal_scale})"
        else:
            kind = target.kind.value.upper()
        detail = f"{target.name} · {kind}"
        if candidate.required_on_insert:
            detail += " · " + self._tr("必填", "Required on insert")
        return detail

    def _selection_for_preview(self):
        mapping: list[ColumnMapping] = []
        targets: list[TargetColumn] = []
        for source_index, combo, date_edit in self._mapping_rows:
            target = combo.currentData()
            if target is None:
                continue
            if not isinstance(target, TargetColumn):
                raise ImportSessionError("INVALID_MAPPING")
            date_format = date_edit.text().strip() if date_edit.isEnabled() else None
            mapping.append(ColumnMapping(source_index, target.name, date_format or None))
            targets.append(target)
        tokens = tuple(token.strip() for token in self.null_tokens_edit.text().split("|") if token.strip())
        null_rule = NullRule(
            tokens, self.csv_empty_null.isChecked(),
            tokens, self.xlsx_empty_null.isChecked(), self.xlsx_blank_null.isChecked(),
        )
        return tuple(mapping), tuple(targets), null_rule

    def _preview_mapping(self) -> None:
        try:
            mapping, targets, null_rule = self._selection_for_preview()
        except (ImportPreviewError, ImportSessionError, TypeError) as error:
            self.status_label.setText(self._error_text(getattr(error, "code", "INVALID_MAPPING")))
            return
        if not mapping:
            self.status_label.setText(self._tr("至少映射一个来源列。", "Map at least one source column."))
            return
        if self.controller.preview_mapping(mapping, targets, null_rule):
            self.status_label.setText(self._tr("正在生成映射预览…", "Building the mapped preview…"))
            self._refresh_actions(self.controller.progress())

    def _start_import(self) -> None:
        answer = QMessageBox.question(
            self,
            self._tr("确认导入", "Confirm import"),
            self._tr(
                "将按当前映射追加文件中的行。导入不会自动提交；完成后仍需明确提交或回滚。",
                "Append rows using the current mapping. The import will remain uncommitted until you explicitly commit or roll back.",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.controller.approve_and_start(user_approved=True):
            self.status_label.setText(self._tr(
                "正在导入；进度为事务内计数，等待完成后再提交或回滚。",
                "Importing; counts are provisional until you commit or roll back.",
            ))
            self._refresh_actions(self.controller.progress())
        else:
            progress = self.controller.progress()
            self.status_label.setText(self._error_text(progress.safe_code or "IMPORT_NOT_READY"))

    def _stop(self) -> None:
        if self.controller.stop():
            self.status_label.setText(self._tr(
                "正在等待导入 owner 完成当前操作并回滚…",
                "Waiting for the import owner to finish its current call and roll back…",
            ))

    @pyqtSlot(object)
    def _on_preview_ready(self, value: object) -> None:
        if isinstance(value, SourcePreview):
            self._populate_mapping(value)
        elif isinstance(value, ImportPreview):
            self._populate_preview(value)

    def _populate_preview(self, preview: ImportPreview) -> None:
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(preview.targets) + 1)
        self.preview_table.setHorizontalHeaderLabels((
            self._tr("来源行", "Source row"), *(target.name for target in preview.targets),
        ))
        self.preview_table.setRowCount(len(preview.rows))
        for row_index, preview_row in enumerate(preview.rows):
            self.preview_table.setItem(row_index, 0, QTableWidgetItem(str(preview_row.source_row_index + 1)))
            for column_index, value in enumerate(preview_row.values, start=1):
                item = QTableWidgetItem(_display_value(value))
                item.setToolTip(type(value).__name__ if value is not None else "NULL")
                self.preview_table.setItem(row_index, column_index, item)
        if preview.issues:
            details = " · ".join(
                f"{issue.code} (row {issue.row_index + 1}, column {issue.column_index + 1})"
                for issue in preview.issues[:12]
            )
            self.issue_label.setText(self._tr("预览问题：", "Preview issues: ") + details)
        else:
            self.issue_label.setText(self._tr("预览转换无错误。", "Preview conversion has no errors."))
        source = preview.source_preview
        count_text = self._tr(
            f"预览 {len(preview.rows)} 行" + ("；文件还有更多行。" if source.has_more else "；已到文件末尾。"),
            f"Previewed {len(preview.rows)} rows" + ("; more source rows remain." if source.has_more else "; end of source reached."),
        )
        self.status_label.setText(count_text)
        self._fit_geometry()
        self._refresh_actions(self.controller.progress())

    @pyqtSlot(object)
    def _on_progress(self, progress: ImportProgress) -> None:
        self._last_progress = progress
        state = progress.state
        if state in {"RUNNING", "CANCEL_PENDING", "COMMITTING", "ROLLING_BACK"}:
            self.status_label.setText(self._tr(
                f"{state} · 已插入 {progress.rows_processed} 行 / {progress.chunks_processed} 批；计数仍未提交。",
                f"{state} · {progress.rows_processed} rows in {progress.chunks_processed} batches; transaction is uncommitted.",
            ))
        elif state == "READY_TO_COMMIT":
            self.status_label.setText(self._tr(
                f"已处理 {progress.rows_processed} 行 / {progress.chunks_processed} 批；请选择提交或回滚。",
                f"Processed {progress.rows_processed} rows in {progress.chunks_processed} batches; choose commit or rollback.",
            ))
        elif state == "UNKNOWN":
            self.status_label.setText(self._error_text(progress.safe_code or "TRANSACTION_OUTCOME_UNKNOWN"))
        elif progress.safe_code:
            self.status_label.setText(self._error_text(progress.safe_code))
        self._refresh_actions(progress)

    @pyqtSlot(str, object, str)
    def _on_operation_finished(self, operation: str, result: Any, error_code: str) -> None:
        if error_code:
            self.status_label.setText(self._error_text(error_code))
        elif operation == "commit" and result is not None:
            self.status_label.setText(self._tr("提交已完成。", "Commit completed."))
        elif operation == "rollback" and result is not None:
            self.status_label.setText(self._tr("回滚已完成。", "Rollback completed."))
        self._refresh_actions(self.controller.progress())

    def _refresh_actions(self, progress: ImportProgress) -> None:
        self._last_progress = progress
        state = progress.state
        shutdown_gated = self.controller.shutdown_gated
        busy = state in {"INSPECTING", "PREVIEWING", "QUEUED", "RUNNING", "CANCEL_PENDING", "COMMITTING", "ROLLING_BACK"}
        can_edit = self.controller.can_edit_source and not busy
        for widget in (
            self.path_edit, self.encoding_combo, self.delimiter_edit, self.sheet_edit,
            self.null_tokens_edit, self.csv_empty_null, self.xlsx_empty_null,
            self.xlsx_blank_null,
        ):
            widget.setEnabled(can_edit)
        self.inspect_button.setEnabled(can_edit)
        self.browse_button.setEnabled(can_edit)
        self.mapping_table.setEnabled(
            can_edit and self.controller.source_preview is not None
        )
        self.preview_button.setEnabled(
            can_edit and self.controller.source_preview is not None
            and state in {"SOURCE_READY", "PREVIEW_READY", "PREVIEW_FAILED", "CANCELLED"}
        )
        self.start_button.setEnabled(
            state == "PREVIEW_READY"
            and progress.preview_issue_count == 0
            and self.controller.target_candidates.can_approve
            and not shutdown_gated
            and not busy
        )
        self.stop_button.setEnabled(state in {"INSPECTING", "PREVIEWING", "QUEUED", "RUNNING"})
        ready = state == "READY_TO_COMMIT"
        self.commit_button.setEnabled(ready and not shutdown_gated)
        self.rollback_button.setEnabled(ready and not shutdown_gated)
        cleanup_retryable = progress.connection_retained and state not in {
            "READY_TO_COMMIT", "RUNNING", "QUEUED", "CANCEL_PENDING", "COMMITTING", "ROLLING_BACK",
        }
        self.retry_button.setVisible(cleanup_retryable)
        self.retry_button.setEnabled(cleanup_retryable)

    def _error_text(self, code: str) -> str:
        messages = {
            "INVALID_SOURCE": ("请选择有效的来源文件。", "Choose a valid source file."),
            "INVALID_FORMAT": ("仅支持 CSV 与 XLSX 文件。", "Only CSV and XLSX files are supported."),
            "INVALID_ENCODING": ("文件编码无法解码。", "The file encoding could not be decoded."),
            "INVALID_SHEET": ("请填写有效的 XLSX 工作表名称。", "Enter a valid XLSX worksheet name."),
            "INVALID_MAPPING": ("来源列映射无效。", "The source-column mapping is invalid."),
            "PREVIEW_HAS_ERRORS": ("预览存在转换错误；修正映射或格式后重试。", "The preview contains conversion errors; adjust the mapping or format."),
            "REQUIRED_COLUMN_UNMAPPED": ("必填目标字段尚未映射。", "A required target field is not mapped."),
            "TABLE_NOT_WRITABLE": ("所选对象不是可导入的基础表。", "The selected object is not a writable base table."),
            "UNSUPPORTED_TARGET_TYPE": ("必填字段的数据类型不受支持。", "A required field uses an unsupported type."),
            "CONNECTION_CLOSE_FAILED": ("连接清理失败；请显式重试清理。", "Connection cleanup failed; retry cleanup explicitly."),
            "COMMIT_OUTCOME_UNKNOWN": ("提交结果未知；不会自动重试提交。", "Commit outcome is unknown; commit will not be retried."),
            "ROLLBACK_OUTCOME_UNKNOWN": ("回滚结果未知；连接仍需显式处理。", "Rollback outcome is unknown; the connection needs explicit handling."),
            "TRANSACTION_OUTCOME_UNKNOWN": ("事务结果未知；确认后才能关闭此窗口。", "Transaction outcome is unknown; acknowledge it before closing."),
            "SOURCE_CHANGED": ("来源文件在预览后发生变化。", "The source changed after preview."),
            "STALE_TABLE_REF": ("所选表已刷新或失效；请重新打开导入。", "The signed table selection is stale; reopen import."),
            "TABLE_SCOPE_STALE": ("所选表范围已失效；请重新打开导入。", "The selected table scope is stale; reopen import."),
        }
        zh, en = messages.get(code, (f"导入状态：{code}", f"Import status: {code}"))
        return self._tr(zh, en)

    def _fit_geometry(self) -> None:
        clamp_dialog_geometry(self, 1160, 760, min_width=720)

    def closeEvent(self, event) -> None:
        if self.controller.shutdown_gated:
            event.ignore()
            return
        progress = self.controller.progress()
        if progress.state == "READY_TO_COMMIT":
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(self._tr("导入尚未提交", "Import is still uncommitted"))
            box.setText(self._tr(
                "选择回滚并关闭，或保留窗口继续处理事务。",
                "Roll back and close, or keep the window open to decide the transaction.",
            ))
            rollback = box.addButton(self._tr("回滚并关闭", "Roll back and close"), QMessageBox.ButtonRole.DestructiveRole)
            keep = box.addButton(self._tr("保持打开", "Keep open"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is rollback:
                self.controller.request_close(action="rollback")
                event.accept()
            else:
                event.ignore()
            return
        if progress.state == "UNKNOWN":
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(self._tr("事务结果未知", "Transaction outcome is unknown"))
            box.setText(self._tr(
                "导入结果不会自动重试。关闭窗口只确认已了解未知状态；资源仍由导入注册表跟踪。",
                "The import will not be retried automatically. Closing acknowledges the unknown outcome; the registry continues tracking resources.",
            ))
            acknowledge = box.addButton(self._tr("确认并关闭", "Acknowledge and close"), QMessageBox.ButtonRole.DestructiveRole)
            keep = box.addButton(self._tr("保持打开", "Keep open"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is acknowledge:
                self.controller.request_close(action="acknowledge")
                event.accept()
            else:
                event.ignore()
            return
        self.controller.request_close()
        event.accept()


__all__ = ["ManualImportDialog"]


def _display_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)
