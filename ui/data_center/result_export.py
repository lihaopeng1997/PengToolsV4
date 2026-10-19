"""Small Qt surface for exporting an immutable typed-result snapshot.

The manual-query host captures row values on the GUI thread.  This module only
collects an explicit scope, format, and destination, then hands that snapshot
to the existing non-Qt writer in a worker owned by the host.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Any

from PyQt6.QtCore import QThread, Qt
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from tools.data_center.result_export import (
    ExportError,
    ExportReceipt,
    ExportScope,
    ExportSnapshot,
    ExportTarget,
    write_result_export,
)


@dataclass(frozen=True, slots=True)
class ExportRequestPlan:
    """One user-approved snapshot export; it contains no live model/session."""

    scope: ExportScope
    format: str
    destination: str
    target: ExportTarget
    snapshot: ExportSnapshot | None
    overwrite: bool = False


class SnapshotExportWorker(QThread):
    """Write one copied snapshot and retain only safe completion metadata."""

    def __init__(
        self,
        request_id: str,
        plan: ExportRequestPlan,
        *,
        writer=write_result_export,
        cancel_event: threading.Event | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.request_id = str(request_id)
        self.plan = plan
        self._writer = writer
        self.cancel_event = cancel_event or threading.Event()
        self.receipt: ExportReceipt | None = None
        self.error_code = ""

    def cancel(self) -> None:
        """Request cooperative cancellation; the host retains this worker until finished."""

        self.cancel_event.set()

    def run(self) -> None:
        try:
            self.receipt = self._writer(
                self.plan.destination,
                self.plan.format,
                self.plan.scope,
                target=self.plan.target,
                snapshot=self.plan.snapshot,
                cancel_check=self.cancel_event.is_set,
                overwrite=self.plan.overwrite,
            )
        except ExportError as error:
            self.error_code = error.code
        except Exception:
            self.error_code = "EXPORT_FAILED"


class ResultExportDialog(QDialog):
    """Choose selected rows or one already-loaded page and a local file."""

    def __init__(
        self,
        parent,
        *,
        target: ExportTarget,
        pages: tuple[Any, ...],
        selected_snapshot: ExportSnapshot | None,
        connection_name: str,
        database: str,
        schema: str,
        language: str = "zh",
        full_query_available: bool = False,
        full_preview: str = "",
    ) -> None:
        super().__init__(parent)
        self._zh = str(language or "zh").casefold().startswith("zh")
        self._target = target
        self._pages = tuple(pages)
        self._selected_snapshot = selected_snapshot
        self._full_query_available = bool(full_query_available)
        self._full_preview = str(full_preview or "")
        self.plan: ExportRequestPlan | None = None

        self.setWindowTitle(self._tr("导出查询结果", "Export query results"))
        self.setModal(True)
        self.setMinimumWidth(410)

        layout = QVBoxLayout(self)
        from ui.layout_metrics import SPACING_CARD

        layout.setContentsMargins(SPACING_CARD, SPACING_CARD, SPACING_CARD, SPACING_CARD)
        layout.setSpacing(SPACING_CARD)

        self.target_label = QLabel(self._target_text(connection_name, database, schema), self)
        self.target_label.setObjectName("result-export-target")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        form = QFormLayout()
        form.setHorizontalSpacing(SPACING_CARD)
        form.setVerticalSpacing(max(6, SPACING_CARD // 2))
        self.scope_combo = QComboBox(self)
        self.scope_combo.setObjectName("result-export-scope")
        self.scope_combo.addItem(self._tr("选中行", "Selected rows"), ExportScope.SELECTED.value)
        self.scope_combo.addItem(self._tr("已加载页", "Loaded page"), ExportScope.CURRENT_PAGE.value)
        self.scope_combo.addItem(self._tr("完整查询", "Full query"), ExportScope.FULL_QUERY.value)
        scope_model = self.scope_combo.model()
        if isinstance(scope_model, QStandardItemModel):
            selected_item = scope_model.item(0)
            selected_item.setEnabled(selected_snapshot is not None and bool(selected_snapshot.rows))
            full_item = scope_model.item(2)
            full_item.setEnabled(self._full_query_available)
        if selected_snapshot is None or not selected_snapshot.rows:
            self.scope_combo.setCurrentIndex(1)
        if not full_query_available:
            self.scope_combo.setItemData(
                2,
                self._tr("当前结果暂不能完整导出", "The current result cannot be exported in full."),
                Qt.ItemDataRole.ToolTipRole,
            )

        self.page_combo = QComboBox(self)
        self.page_combo.setObjectName("result-export-page")
        for page in self._pages:
            self.page_combo.addItem(
                self._tr(f"第 {page.page_number} 页 · {len(page.rows)} 行", f"Page {page.page_number} · {len(page.rows)} rows"),
                page.page_number,
            )
        if self.page_combo.count():
            self.page_combo.setCurrentIndex(self.page_combo.count() - 1)

        self.format_combo = QComboBox(self)
        self.format_combo.setObjectName("result-export-format")
        for value, label in (("csv", "CSV"), ("json", "JSON"), ("xlsx", "Excel (.xlsx)")):
            self.format_combo.addItem(label, value)

        self.path_edit = QLineEdit(self)
        self.path_edit.setObjectName("result-export-path")
        self.path_edit.setClearButtonEnabled(True)
        self.browse_btn = QPushButton(self._tr("浏览…", "Browse…"), self)
        self.browse_btn.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_btn)

        form.addRow(self._tr("范围", "Range"), self.scope_combo)
        form.addRow(self._tr("页码", "Page"), self.page_combo)
        form.addRow(self._tr("格式", "Format"), self.format_combo)
        form.addRow(self._tr("保存到", "Save to"), path_row)
        layout.addLayout(form)

        self.preview_label = QLabel(self)
        self.preview_label.setObjectName("result-export-preview")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        self.format_note = QLabel(
            self._tr(
                "CSV 使用类型标记保留数据类型；JSON 和 Excel 保留类型信息。",
                "CSV uses typed-cell markers; JSON and Excel preserve typed values.",
            ),
            self,
        )
        self.format_note.setObjectName("result-export-format-note")
        self.format_note.setWordWrap(True)
        layout.addWidget(self.format_note)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("result-export-error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton(self._tr("取消", "Cancel"), self)
        self.save_btn = QPushButton(self._tr("导出", "Export"), self)
        from ui.design_system import apply_button

        apply_button(self.cancel_btn, "secondary", compact=True)
        apply_button(self.save_btn, "primary", compact=True)
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._accept_export)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        self.scope_combo.currentIndexChanged.connect(self._refresh_preview)
        self.page_combo.currentIndexChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _tr(self, zh: str, en: str) -> str:
        return zh if self._zh else en

    def _target_text(self, connection_name: str, database: str, schema: str) -> str:
        values = [
            self._tr("连接：", "Connection: ") + (str(connection_name).strip() or self._tr("未命名", "Unnamed")),
            self._tr("数据库：", "Database: ") + (str(database).strip() or self._tr("未指定", "Not specified")),
        ]
        schema_text = str(schema or "").strip()
        database_text = str(database or "").strip()
        if schema_text and schema_text != database_text:
            values.append(self._tr("Schema：", "Schema: ") + schema_text)
        return " · ".join(values)

    def _selected_scope(self) -> ExportScope:
        try:
            return ExportScope(str(self.scope_combo.currentData()))
        except (ValueError, TypeError):
            return ExportScope.CURRENT_PAGE

    def _selected_page(self):
        page_number = self.page_combo.currentData()
        return next((page for page in self._pages if page.page_number == page_number), None)

    def _refresh_preview(self, *_args: Any) -> None:
        scope = self._selected_scope()
        self.page_combo.setEnabled(scope is ExportScope.CURRENT_PAGE)
        if scope is ExportScope.SELECTED and self._selected_snapshot is not None:
            snapshot = self._selected_snapshot
            page_numbers = sorted({
                page.page_number
                for page in self._pages
                if page.query_id == snapshot.target.query_id
                and page.tab_id == snapshot.target.tab_id
                and page.generation == snapshot.target.generation
            })
            if len(page_numbers) == 1:
                source = self._tr(f"第 {page_numbers[0]} 页", f"page {page_numbers[0]}")
            elif page_numbers:
                source = self._tr(
                    f"第 {page_numbers[0]}–{page_numbers[-1]} 页",
                    f"pages {page_numbers[0]}–{page_numbers[-1]}",
                )
            else:
                source = self._tr("已加载结果", "loaded results")
            text = self._tr(
                f"范围预览：{source}中的 {len(snapshot.rows)} 行",
                f"Preview: {len(snapshot.rows)} selected rows from {source}",
            )
        elif scope is ExportScope.SELECTED:
            text = self._tr("请先选择要导出的行。", "Select rows to export first.")
        elif scope is ExportScope.CURRENT_PAGE:
            page = self._selected_page()
            if page is None:
                text = self._tr("当前没有可导出的已加载页。", "There is no loaded page to export.")
            else:
                text = self._tr(
                    f"范围预览：第 {page.page_number} 页，共 {len(page.rows)} 行",
                    f"Preview: page {page.page_number}, {len(page.rows)} rows",
                )
        else:
            text = self._full_preview if self._full_query_available and self._full_preview else self._tr(
                "当前结果暂不能完整导出。", "The current result cannot be exported in full."
            )
        self.preview_label.setText(text)

    def _browse(self) -> None:
        fmt = str(self.format_combo.currentData() or "csv")
        filters = {
            "csv": "CSV (*.csv)",
            "json": "JSON (*.json)",
            "xlsx": "Excel (*.xlsx)",
        }
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self._tr("导出查询结果", "Export query results"),
            self.path_edit.text().strip(),
            filters.get(fmt, ""),
        )
        if path:
            self.path_edit.setText(path)

    def _accept_export(self) -> None:
        self.error_label.clear()
        scope = self._selected_scope()
        if scope is ExportScope.FULL_QUERY:
            if not self._full_query_available:
                self.error_label.setText(self._tr("当前结果暂不能完整导出。", "The current result cannot be exported in full."))
                return
        fmt = str(self.format_combo.currentData() or "")
        destination = self.path_edit.text().strip()
        if not destination:
            self.error_label.setText(self._tr("请选择保存位置。", "Choose a destination file."))
            return
        expected_suffix = "." + fmt
        suffix = Path(destination).suffix.casefold()
        if not suffix:
            destination += expected_suffix
            self.path_edit.setText(destination)
        elif suffix != expected_suffix:
            self.error_label.setText(self._tr(
                f"所选格式需要 {expected_suffix} 文件扩展名。",
                f"The selected format requires the {expected_suffix} extension.",
            ))
            return

        if scope is ExportScope.FULL_QUERY:
            snapshot = None
        elif scope is ExportScope.SELECTED:
            snapshot = self._selected_snapshot
        else:
            page = self._selected_page()
            snapshot = None if page is None else ExportSnapshot(
                self._target,
                tuple(column.name for column in page.columns),
                page.rows,
                page.page_number,
            )
        if scope is ExportScope.SELECTED and (snapshot is None or not snapshot.rows):
            self.error_label.setText(self._tr("所选范围没有可导出的行。", "The selected range has no rows to export."))
            return
        if scope is ExportScope.CURRENT_PAGE and snapshot is None:
            self.error_label.setText(self._tr("当前没有可导出的已加载页。", "There is no loaded page to export."))
            return
        if snapshot is not None and snapshot.target != self._target:
            self.error_label.setText(self._tr("结果已变化，请重新打开导出窗口。", "The result changed; reopen the export dialog."))
            return

        overwrite = False
        if os.path.lexists(destination):
            answer = QMessageBox.question(
                self,
                self._tr("确认覆盖文件", "Confirm file replacement"),
                self._tr(
                    "目标文件已存在。是否替换它？",
                    "The destination already exists. Replace it?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True

        self.plan = ExportRequestPlan(scope, fmt, destination, self._target, snapshot, overwrite)
        self.accept()


__all__ = ["ExportRequestPlan", "ResultExportDialog"]
