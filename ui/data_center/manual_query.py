"""Qt adapter for persistent per-SQL-tab ManualSession instances.

The host owns only UI bindings.  DBAPI construction and all connection calls
are delegated to the ManualSession owner thread; Future callbacks cross back to
Qt through queued signals.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
import re
import threading
import uuid
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QMenu, QPushButton, QSizePolicy, QTableView, QToolButton, QWidget

from tools.data_center.manual_driver import (
    ManualDriverError,
    profile_schema_for_provider,
)
from tools.data_center.manual_relational_providers import (
    BoundManualTableScope,
    ManualRelationalProviderFactory,
    classify_manual_sql,
    inspect_manual_provider,
)
from tools.data_center.manual_session import (
    ManualQueryPage,
    ManualSession,
    ManualSessionError,
    ManualSessionState,
    ManualSessionTarget,
    ManualBatchResult,
    ManualCloseResult,
    ManualStatementResult,
    ManualScriptResult,
    StatementKind,
    PAGE_SIZES,
    MAX_SCRIPT_BYTES,
    MAX_SCRIPT_STATEMENTS,
)
from tools.data_center.profile_host import ConnectionChoice, DataCenterProfileHost, ProfileHostError
from tools.data_center.changeset_dialects import create_changeset_dialect
from tools.data_center.object_tree import TreeNode, TreeTarget
from tools.data_center.completion_cache import CompletionCatalog
from tools.data_center.result_export import ExportScope, ExportSnapshot, ExportTarget, write_result_export
from tools.data_center.table_source import TableQuerySpec, TableSourceError, TableSourceOpenResult, build_table_query, build_table_select
from tools.data_center.sql_script import SqlScriptError, has_procedural_block, scan_sql, selected_procedural_sql, statement_at
from tools.data_center.import_session import candidate_target_columns
from ui.data_center.import_controller import ImportTableContext
from ui.data_center.import_host import ImportHost
from tools.sql_guard import classify_statement
from ui.data_center.result_model import ManualResultTableModel
from ui.data_center.result_view import ResultTableView
from ui.data_center.value_inspector import inspect_value
from ui.data_center.result_find import ResultFindDialog
from ui.data_center.result_export import ExportRequestPlan, ResultExportDialog, SnapshotExportWorker
from ui.data_center.table_editor import TableEditorModel, TableEditorWidget


@dataclass(frozen=True, slots=True)
class _FutureDelivery:
    tab_id: str
    generation: int
    request_id: str
    operation: str
    result: Any = None
    error_code: str = ""
    history_query_id: str = ""


class _FutureBridge(QObject):
    completed = pyqtSignal(object)


class ManualShutdownStatus(str, Enum):
    """A nonblocking shutdown result; READY means every owner really exited."""

    READY = "ready"
    READY_TO_CLOSE = "ready_to_close"
    PENDING = "pending"
    CANCELLED = "cancelled"
    DECISION_REQUIRED = "decision_required"


@dataclass(frozen=True, slots=True)
class ManualShutdownSessionSnapshot:
    """Public-safe state for one retained manual owner; excludes SQL and secrets."""

    tab_id: str
    generation: int
    state: str
    busy: bool
    cleanup_complete: bool
    pending_local_changes: int = 0
    parent_sql_tab_id: str = ""
    parent_sql_generation: int = 0
    owner_kind: str = "manual"


@dataclass(frozen=True, slots=True)
class ManualShutdownSnapshot:
    """Immutable view for cross-workspace shutdown preflight aggregation."""

    sessions: tuple[ManualShutdownSessionSnapshot, ...]
    can_commit: bool


@dataclass(slots=True)
class _TabState:
    tab: Any
    target: ManualSessionTarget
    choice: ConnectionChoice
    session: ManualSession
    generation: int
    request_id: str = ""
    active_operation: str = ""
    busy: bool = False
    last_page: ManualQueryPage | None = None
    pages: tuple[ManualQueryPage, ...] = ()
    last_statement: ManualStatementResult | None = None
    last_error: str = ""
    export_status: str = ""
    pending_close: bool = False
    remove_tab_on_close: bool = False
    table_scope: BoundManualTableScope | None = None
    table_result: TableSourceOpenResult | None = None
    table_model: TableEditorModel | None = None
    table_mode_active: bool = False
    table_page_size: int = 200
    query_page_size: int = 200
    table_query: TableQuerySpec = TableQuerySpec()
    script: ManualScriptResult | None = None
    script_index: int = -1
    script_pages: dict[int, tuple[ManualQueryPage, ...]] = field(default_factory=dict)
    history_capture: Any = None
    history_query_id: str = ""


@dataclass(frozen=True, slots=True)
class _ExportContext:
    state: _TabState
    generation: int
    pages: tuple[ManualQueryPage, ...]
    target: ExportTarget
    selected_snapshot: ExportSnapshot | None
    connection_name: str
    database: str
    schema: str
    full_query_available: bool
    full_preview: str


class ManualQueryHost(QObject):
    """Attach manual sessions to an existing production ``AiWorkbenchPanel``.

    Construction and ``install`` are UI-only and never call the driver factory.
    In particular, creating a SQL tab creates only its immutable target and a
    lazy ``ManualSession``; the factory is first invoked by a submitted owner
    job.
    """

    def __init__(
        self,
        panel: Any,
        *,
        profile_host: DataCenterProfileHost | None = None,
        connector: Callable[[dict, Any], Any] | None = None,
        secret_resolver: Callable[[Any], Any] | None = None,
        supported_providers: tuple[str, ...] = ("mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"),
        page_size: int = 200,
        result_budget_bytes: int = 50 * 1024 * 1024,
        export_writer: Callable[..., Any] = write_result_export,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or panel)
        self.panel = panel
        self.profile_host = profile_host or DataCenterProfileHost()
        self.connector = connector
        self.secret_resolver = secret_resolver
        self.supported_providers = frozenset(str(value).casefold() for value in supported_providers)
        self.page_size = page_size
        self.result_budget_bytes = result_budget_bytes
        self._export_writer = export_writer
        self._states_by_tab: dict[int, _TabState] = {}
        self._retired_states: list[_TabState] = []
        self._shutdown_action: str | None = None
        self._bridge = _FutureBridge(self)
        self._bridge.completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._shortcuts: list[QShortcut] = []
        self._closing_states: dict[tuple[str, int], _TabState] = {}
        self._installed = False
        self._shutdown_gated = False
        self._import_host = ImportHost()
        self._import_host.changed.connect(self._import_state_changed)
        self._export_jobs: dict[str, tuple[_ExportContext, SnapshotExportWorker]] = {}
        self._preparing_table_tab = False
        self._layout_mode = "wide"
        self._low_height = False
        self._table_focus_active = False
        self._splitter_sizes_before_table_focus: tuple[int, ...] | None = None
        self._sql_editor_focus_state: dict[int, tuple[int, Any]] = {}
        self._middle_pane_focus_state: tuple[int, Any] | None = None
        self._table_result_layout_state: tuple[Any, ...] | None = None
        self._transaction_actions_in_toolbar = False
        self._completion_catalog = CompletionCatalog()

    def set_completion_catalog(self, catalog: CompletionCatalog) -> None:
        self._completion_catalog = catalog
        self.refresh_completion_bindings()

    def workspace_tab_context(self, tab: Any):
        """Capture text context only; never activate a history draft or driver."""
        from tools.data_center.workspace_store import WorkspaceTarget

        record = getattr(tab, "_history_target_record", None)
        if getattr(tab, "_history_target_locked", False) and record is not None:
            return record.target, "INTERRUPTED" if record.state_code == "INTERRUPTED" else "DRAFT"
        state = self._states_by_tab.get(id(tab))
        if state is None:
            raise ManualSessionError("PROFILE_UNAVAILABLE")
        target = state.target
        interrupted = state.busy or state.session.state not in {ManualSessionState.DISCONNECTED, ManualSessionState.CLEAN}
        return WorkspaceTarget(target.tab_id, target.connection_id, target.profile_revision, target.database, target.schema), "INTERRUPTED" if interrupted else "DRAFT"

    def refresh_completion_bindings(self) -> None:
        for state in self._states_by_tab.values():
            self._bind_completion(state.tab, state)

    def _bind_completion(self, tab: Any, state: _TabState | None) -> None:
        bind = getattr(getattr(tab, "editor", None), "bind_schema", None)
        if callable(bind):
            bind(self._completion_catalog.snapshot(state.target, state.choice.provider)
                 if state and not getattr(tab, "_history_target_locked", False) else None)

    @classmethod
    def install(cls, panel: Any, **kwargs: Any) -> "ManualQueryHost":
        host = cls(panel, **kwargs)
        host._install()
        return host

    def _install(self) -> None:
        if self._installed:
            return
        self._installed = True
        setattr(self.panel, "manual_query_host", self)
        tabs = getattr(self.panel, "sql_tabs", None)
        run_button = getattr(self.panel, "run_btn", None)
        if tabs is None or run_button is None:
            raise TypeError("AiWorkbenchPanel is missing the SQL-tab or run-button seam")

        # The previous handler uses the legacy auto-commit console worker.  A
        # manual host takes exclusive ownership of this action for this panel.
        try:
            run_button.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        run_button.clicked.connect(self.run_current)

        for shortcut in self.panel.findChildren(QShortcut):
            try:
                sequence = shortcut.key().toString().replace(" ", "").casefold()
            except RuntimeError:
                continue
            if sequence in {"ctrl+return", "f5"}:
                shortcut.setEnabled(False)
                replacement = QShortcut(QKeySequence(sequence), self.panel)
                replacement.activated.connect(self.run_current)
                self._shortcuts.append(replacement)

        tabs.currentChanged.connect(self._visible_tab_changed)
        combo = getattr(self.panel, "conn_combo", None)
        if combo is not None:
            combo.currentIndexChanged.connect(self._connection_selection_changed)

        self._install_transaction_actions()
        self._install_typed_result_view()
        self._ensure_tab_state(self.panel._current_tab())
        self._visible_tab_changed(tabs.currentIndex())

    def _install_transaction_actions(self) -> None:
        panel = self.panel
        self.commit_btn = QPushButton("提交事务" if panel.language == "zh" else "Commit", panel)
        self.rollback_btn = QPushButton("回滚事务" if panel.language == "zh" else "Rollback", panel)
        self.stop_btn = QPushButton("停止" if panel.language == "zh" else "Stop", panel)
        self.run_all_btn = QPushButton("执行全部…" if panel.language == "zh" else "Run all…", panel)
        from ui.design_system import apply_button

        apply_button(self.commit_btn, "primary", compact=True)
        apply_button(self.rollback_btn, "secondary", compact=True)
        apply_button(self.stop_btn, "ghost", compact=True)
        apply_button(self.run_all_btn, "secondary", compact=True)
        self.commit_btn.clicked.connect(self.commit_current)
        self.rollback_btn.clicked.connect(self.rollback_current)
        self.stop_btn.clicked.connect(self.cancel_current)
        self.run_all_btn.clicked.connect(self.run_all_current)
        middle_layout = panel._middle_pane.layout()
        editor_toolbar = _layout_containing(middle_layout, panel.run_btn) if middle_layout is not None else None
        if editor_toolbar is None:
            raise TypeError("AiWorkbenchPanel run button is not inside the editor toolbar")
        self.transaction_actions = QWidget(panel._middle_pane)
        self.transaction_actions.setObjectName("manual-transaction-actions")
        action_layout = QHBoxLayout(self.transaction_actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        from ui.layout_metrics import SPACING_CARD

        action_layout.setSpacing(SPACING_CARD)
        for button in (self.commit_btn, self.rollback_btn, self.stop_btn, self.run_all_btn):
            button.setParent(self.transaction_actions)
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            action_layout.addWidget(button)
        # “执行全部”频率低，入口放进「更多」菜单，给同一行的常用按钮让出宽度
        self.run_all_btn.hide()
        self.workbench_more_btn = QToolButton(self.transaction_actions)
        self.workbench_more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.workbench_more_menu = QMenu(self.workbench_more_btn)
        self.workbench_more_btn.setMenu(self.workbench_more_menu)
        action_layout.addWidget(self.workbench_more_btn)
        # 事务按钮并入编辑器工具条同一行（放在弹性空白之前），不再单独占一行
        insert_at = editor_toolbar.count()
        for index in range(editor_toolbar.count()):
            item = editor_toolbar.itemAt(index)
            if item is not None and item.spacerItem() is not None:
                insert_at = index
                break
        editor_toolbar.insertWidget(insert_at, self.transaction_actions)
        self._transaction_actions_in_toolbar = True
        self.workbench_more_menu.aboutToShow.connect(self._refresh_workbench_actions)
        self.refresh_language()

        # The old pagination route submits another auto-commit statement.  The
        # manual host only exposes next-page on the owner's current cursor.
        next_button = getattr(panel, "next_btn", None)
        if next_button is not None:
            try:
                next_button.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            next_button.clicked.connect(self.fetch_next_page)
        all_button = getattr(panel, "all_btn", None)
        if all_button is not None:
            try:
                all_button.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            all_button.setEnabled(False)
            all_button.setToolTip(
                "手动事务模式暂不支持获取全部" if panel.language == "zh" else
                "Fetch all is unavailable in manual transaction mode"
            )

    def _install_typed_result_view(self) -> None:
        """Use the production typed-page model for the manual session path."""
        panel = self.panel
        legacy = getattr(panel, "result", None)
        if legacy is None:
            raise TypeError("AiWorkbenchPanel is missing its legacy result table seam")
        parent = legacy.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is None or layout.indexOf(legacy) < 0:
            raise TypeError("AiWorkbenchPanel result table is not in a layout")
        index = layout.indexOf(legacy)
        legacy.hide()
        self.result_model = ManualResultTableModel(panel)
        self.result_view = ResultTableView(parent, language=getattr(panel, "language", "zh"))
        self.result_view.setObjectName("manual-typed-result-view")
        self.result_view.setModel(self.result_model)
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.result_view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.result_view.setMinimumHeight(120)
        self.result_view.doubleClicked.connect(self._inspect_result_value)
        self.result_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_view.customContextMenuRequested.connect(self._result_value_menu)
        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self.result_view)
        find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(self.find_current_result)
        self._shortcuts.append(find_shortcut)
        try:
            self.result_view.verticalHeader().setDefaultSectionSize(legacy.verticalHeader().defaultSectionSize())
        except (AttributeError, RuntimeError):
            pass
        layout.insertWidget(index, self.result_view, 1)
        setattr(panel, "manual_result_view", self.result_view)
        setattr(panel, "manual_result_model", self.result_model)
        self._install_page_size_control()
        self.table_editor = TableEditorWidget(getattr(panel, "language", "zh"), parent)
        self.table_editor.hide()
        layout.insertWidget(index + 1, self.table_editor, 1)
        self.table_editor.refresh_requested.connect(self.refresh_table_source)
        self.table_editor.next_page_requested.connect(self.fetch_next_page)
        self.table_editor.apply_requested.connect(self.apply_table_preview)
        self.table_editor.import_requested.connect(self.open_import_current)
        self.table_editor.staged_changed.connect(self._table_overlay_changed)
        self.table_editor.sql_view_requested.connect(self._show_sql_view)
        self.table_editor.server_query_requested.connect(self.open_table_query)
        self.table_editor.page_size.currentIndexChanged.connect(self._table_page_size_changed)

    def _install_page_size_control(self) -> None:
        next_button = getattr(self.panel, "next_btn", None)
        parent = next_button.parentWidget() if next_button is not None else None
        layout = _layout_containing(parent.layout(), next_button) if parent is not None else None
        if layout is None:
            raise TypeError("Manual result paging controls are not in a layout")
        self.query_page_size = QComboBox(parent)
        self.query_page_size.setObjectName("manual-query-page-size")
        for size in sorted(PAGE_SIZES):
            self.query_page_size.addItem(self._page_size_text(size), size)
        self.query_page_size.setCurrentIndex(self.query_page_size.findData(self.page_size))
        self.query_page_size.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.query_page_size.setEnabled(False)
        self.query_page_size.setAccessibleName(self._message("每页行数", "Rows per page"))
        self.query_page_size.setToolTip(self._message("每页行数；用于下次执行或读取下一页", "Rows per page for the next execution or page fetch"))
        self.query_page_size.currentIndexChanged.connect(self._query_page_size_changed)
        layout.insertWidget(0, self.query_page_size)
        self.script_results = QComboBox(parent)
        self.script_results.setObjectName("manual-script-results")
        self.script_results.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.script_results.setMinimumContentsLength(14)
        self.script_results.setMaximumWidth(260)
        self.script_results.setAccessibleName(self._message("脚本语句执行结果", "Script statement results"))
        self.script_results.currentIndexChanged.connect(self._script_selection_changed)
        self.script_results.hide()
        layout.insertWidget(1, self.script_results)

    def _query_page_size_changed(self, index: int) -> None:
        state = self._current_state()
        value = self.query_page_size.itemData(index)
        if (
            state is not None and not state.busy and not self._shutdown_gated
            and not state.table_mode_active and type(value) is int and value in PAGE_SIZES
        ):
            state.query_page_size = value
        if state is not None:
            self._sync_paging(state)

    def _page_size_text(self, size: int) -> str:
        return self._message(f"{size} 行/页", f"{size} rows/page")

    def _sync_query_page_size(self, state: _TabState | None) -> None:
        control = getattr(self, "query_page_size", None)
        if control is None:
            return
        previous = control.blockSignals(True)
        control.setCurrentIndex(control.findData(state.query_page_size if state else self.page_size))
        control.blockSignals(previous)
        control.setVisible(state is None or not state.table_mode_active)
        control.setEnabled(state is not None and not state.busy and not self._shutdown_gated)
        self._sync_script_results(state)

    def _sync_script_results(self, state: _TabState | None) -> None:
        control = getattr(self, "script_results", None)
        if control is None:
            return
        previous = control.blockSignals(True)
        control.clear()
        if state is not None and state.script is not None:
            for index, entry in enumerate(state.script.entries):
                label = self._script_entry_label(entry)
                control.addItem(f"{index + 1}. {label} · {entry.sql[:55].replace(chr(10), ' ')}", index)
            control.setCurrentIndex(state.script_index)
        control.blockSignals(previous)
        control.setVisible(control.count() > 0)
        control.setEnabled(state is not None and not state.busy and not self._shutdown_gated)

    def _script_entry_label(self, entry: Any) -> str:
        if entry.result is not None:
            return self._message("完成", "Done")
        if entry.error_code == "SKIPPED":
            return self._message("未执行", "Skipped")
        if entry.error_code == "CANCELLED":
            return self._message("已取消", "Cancelled")
        return self._message("失败", "Failed") + f" ({entry.error_code})"

    def _script_selection_changed(self, index: int) -> None:
        state = self._current_state()
        if state is None or state.script is None or state.busy or self._shutdown_gated:
            return
        if not 0 <= index < len(state.script.entries):
            return
        state.script_index = index
        entry = state.script.entries[index]
        page = entry.result if isinstance(entry.result, ManualQueryPage) else getattr(entry.result, "page", None)
        state.pages = state.script_pages.setdefault(index, (page,)) if page is not None else ()
        state.last_page = state.pages[-1] if state.pages else None
        state.last_statement = entry.result if isinstance(entry.result, ManualStatementResult) else None
        self.result_model.replace_pages(state.pages)
        state.export_status = self._message(
            f"脚本已完成 {state.script.completed_count}/{len(state.script.entries)} 条；当前第 {index + 1} 条：{self._script_entry_label(entry)}。",
            f"Script completed {state.script.completed_count}/{len(state.script.entries)}; statement {index + 1}: {self._script_entry_label(entry)}.",
        )
        if isinstance(entry.result, ManualStatementResult) and entry.result.kind is not StatementKind.QUERY:
            count = entry.result.rowcount
            known = type(count) is int and count >= 0
            state.export_status += self._message(f" 影响 {count} 行。" if known else " 影响行数未知。", f" {count} affected rows." if known else " Affected row count is unknown.")
        if page is not None and page.truncated_by_budget:
            state.export_status += self._message(" 已达到脚本结果预算，仅显示保留的行。", " Script result budget reached; only retained rows are shown.")
        if page is not None and not self._selected_result_can_continue(state) and page.has_more:
            state.export_status += self._message(" 此游标已不可继续，仅保留已读取结果。", " This cursor cannot continue; only loaded rows remain.")
        self._set_status(state.export_status, state=state)
        self._sync_paging(state)
        self._refresh_workbench_actions()

    def _selected_result_can_continue(self, state: _TabState) -> bool:
        if state.script is None:
            return True
        status = state.session.result_source_status
        return bool(state.last_page and status.query_id == state.last_page.query_id and status.can_continue)

    def _inspect_result_value(self, index) -> None:
        if not index.isValid() or index.model() is not self.result_model:
            return
        name = self.result_model.headerData(index.column(), Qt.Orientation.Horizontal)
        value = self.result_model.data(index, Qt.ItemDataRole.UserRole)
        inspect_value(str(name or ""), value, language=getattr(self.panel, "language", "zh"), parent=self.panel)

    def find_current_result(self) -> None:
        if self.result_view.isHidden() or not self.result_model.rowCount():
            return
        finder = getattr(self, "_result_finder", None)
        if finder is None:
            finder = ResultFindDialog(self.result_view, self.result_model, language=getattr(self.panel, "language", "zh"), parent=self.panel)
            self._result_finder = finder
        finder.show()
        finder.raise_()
        finder.activateWindow()

    def _result_value_menu(self, point) -> None:
        index = self.result_view.indexAt(point)
        if not index.isValid():
            return
        menu = QMenu(self.result_view)
        action = menu.addAction(self._message("查看字段值详情…", "Inspect cell value…"))
        action.triggered.connect(lambda: self._inspect_result_value(index))
        try:
            menu.exec(self.result_view.viewport().mapToGlobal(point))
        finally:
            menu.deleteLater()

    def refresh_language(self) -> None:
        """Refresh manual transaction labels after the host language changes."""
        zh = getattr(self.panel, "language", "zh") == "zh"
        result_view = getattr(self, "result_view", None)
        if result_view is not None:
            result_view.set_language("zh" if zh else "en")
        page_size_control = getattr(self, "query_page_size", None)
        if page_size_control is not None:
            for index in range(page_size_control.count()):
                page_size_control.setItemText(index, self._page_size_text(page_size_control.itemData(index)))
            page_size_control.setAccessibleName(self._message("每页行数", "Rows per page"))
            page_size_control.setToolTip(self._message("每页行数；用于下次执行或读取下一页", "Rows per page for the next execution or page fetch"))
        self.commit_btn.setText("提交事务" if zh else "Commit")
        self.rollback_btn.setText("回滚事务" if zh else "Rollback")
        self.stop_btn.setText("停止" if zh else "Stop")
        run_all = getattr(self, "run_all_btn", None)
        if run_all is not None:
            run_all.setText("执行全部…" if zh else "Run all…")
        self.workbench_more_btn.setText("更多" if zh else "More")
        for button in (self.commit_btn, self.rollback_btn, self.stop_btn):
            button.setMinimumWidth(button.sizeHint().width())
        table_editor = getattr(self, "table_editor", None)
        if table_editor is not None:
            table_editor.set_language("zh" if zh else "en")
        tab = self.panel._current_tab()
        if tab is not None and getattr(tab, "_history_target_locked", False):
            self.panel._set_history_draft_status(tab)
        else:
            state = self._states_by_tab.get(id(tab)) if tab is not None else None
            if state is None:
                self._set_status(self._state_text(ManualSessionState.DISCONNECTED))
            elif not state.busy:
                self._set_status(state.export_status or self._state_text(state.session.state), state=state)
        self._refresh_workbench_actions()

    def _refresh_workbench_actions(self) -> None:
        menu = getattr(self, "workbench_more_menu", None)
        if menu is None:
            return
        previous_columns_menu = getattr(self, "_result_columns_menu", None)
        if previous_columns_menu is not None:
            previous_columns_menu.deleteLater()
        self._result_columns_menu = None
        menu.clear()
        panel = self.panel
        run_all_button = getattr(self, "run_all_btn", None)
        if run_all_button is not None:
            run_all_action = menu.addAction(run_all_button.text())
            run_all_action.setEnabled(run_all_button.isEnabled())
            run_all_action.triggered.connect(run_all_button.click)
        history_action = menu.addAction(self._message("查询历史与收藏…", "Query history and favorites…"))
        history_action.triggered.connect(getattr(panel, "_open_query_history", lambda: None))
        menu.addSeparator()
        self.export_action = menu.addAction(self._message("导出查询结果…", "Export query results…"))
        self.export_action.setEnabled(self._can_open_export_dialog(self._current_state()))
        self.export_action.triggered.connect(self.export_current)
        if getattr(self, "result_view", None) is not None:
            find_action = menu.addAction(self._message("查找已加载结果…", "Find in loaded results…"))
            model = self.result_view.model()
            find_action.setEnabled(not self.result_view.isHidden() and model is not None and model.rowCount() > 0)
            find_action.triggered.connect(self.find_current_result)
        editor = getattr(self, "table_editor", None)
        view = editor.table if editor is not None and not editor.isHidden() else getattr(self, "result_view", None)
        if view is not None:
            self._result_columns_menu = view.create_column_menu(menu)
            menu.addMenu(self._result_columns_menu)
        menu.addSeparator()
        for button in (
            getattr(panel, "scan_btn", None),
            getattr(panel, "scan_cancel_btn", None),
            getattr(panel, "save_draft_btn", None),
            getattr(panel, "view_snap_btn", None),
            getattr(panel, "del_snap_btn", None),
            getattr(panel, "model_btn", None),
        ):
            if button is None:
                continue
            action = menu.addAction(button.text())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(lambda _checked=False, target=button: target.click())

    def apply_layout_mode(self, mode: str, *, low_height: bool = False) -> None:
        """Give the typed result pane more growth room in existing compact modes."""
        self._layout_mode = str(mode or "wide").casefold()
        self._low_height = bool(low_height)
        compact = self._low_height or self._layout_mode in {"compact", "narrow"}
        splitter = getattr(self.panel, "body_splitter", None)
        if splitter is not None:
            splitter.setStretchFactor(0, 2 if compact else 3)
            splitter.setStretchFactor(1, 3 if compact else 2)
            sizes = list(splitter.sizes())
            extent = sum(sizes)
            if compact and len(sizes) == 2 and extent >= 400 and sizes[1] < 180:
                # Repair a collapsed legacy split only when the current viewport can
                # satisfy both existing pane minimums without extending the page.
                splitter.setSizes([int(extent * 0.45), int(extent * 0.55)])
        self._sync_table_focus_layout(self._current_state())

    def _sync_table_focus_layout(self, state: _TabState | None) -> None:
        table_visible = bool(state is not None and state.table_mode_active and state.table_model is not None)
        self._set_table_focus_active(table_visible and (self._low_height or self._layout_mode in {"compact", "narrow"}))

    def _move_transaction_actions_to_toolbar(self, compact: bool) -> None:
        """事务按钮常驻编辑器工具条同一行；任何布局下都不再挪回单独一行。"""
        if self._transaction_actions_in_toolbar:
            return
        middle = getattr(self.panel, "_middle_pane", None)
        actions = getattr(self, "transaction_actions", None)
        middle_layout = middle.layout() if middle is not None else None
        toolbar_layout = middle_layout.itemAt(0).layout() if middle_layout is not None and middle_layout.count() else None
        if middle_layout is None or toolbar_layout is None or actions is None:
            return
        if compact:
            if middle_layout.indexOf(actions) >= 0:
                middle_layout.removeWidget(actions)
                toolbar_layout.addWidget(actions)
                self._transaction_actions_in_toolbar = True
        elif toolbar_layout.indexOf(actions) >= 0:
            toolbar_layout.removeWidget(actions)
            middle_layout.insertWidget(1, actions)
            self._transaction_actions_in_toolbar = False

    def _set_table_focus_active(self, focused: bool) -> None:
        focused = bool(focused)
        splitter = getattr(self.panel, "body_splitter", None)
        if splitter is not None and splitter.count() >= 2:
            if focused:
                if not self._table_focus_active:
                    self._splitter_sizes_before_table_focus = tuple(int(size) for size in splitter.sizes())
                tabs = getattr(self.panel, "sql_tabs", None)
                if tabs is not None:
                    live_editor_ids: set[int] = set()
                    for index in range(tabs.count()):
                        tab = tabs.widget(index)
                        editor = getattr(tab, "editor", None)
                        if editor is None:
                            continue
                        live_editor_ids.add(id(editor))
                        if id(editor) not in self._sql_editor_focus_state:
                            self._sql_editor_focus_state[id(editor)] = (
                                int(editor.minimumHeight()),
                                editor.sizePolicy(),
                            )
                        policy = editor.sizePolicy()
                        editor.setMinimumHeight(0)
                        policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
                        editor.setSizePolicy(policy)
                        editor.hide()
                    for editor_id in tuple(self._sql_editor_focus_state):
                        if editor_id not in live_editor_ids:
                            self._sql_editor_focus_state.pop(editor_id, None)
                self._table_focus_active = True
                # Keep the original SQL toolbar, transaction actions, tab bar,
                # and close buttons usable in table focus. Only the editor
                # contents are collapsed; the fixed controls remain in their
                # original owner/layout and retain their existing callbacks.
                middle = getattr(self.panel, "_middle_pane", None)
                if middle is not None:
                    if self._middle_pane_focus_state is None:
                        self._middle_pane_focus_state = (
                            int(middle.minimumHeight()),
                            middle.sizePolicy(),
                        )
                    middle.setMinimumHeight(0)
                    middle.setSizePolicy(
                        QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Ignored,
                    )
                self._move_transaction_actions_to_toolbar(True)
                if self._table_result_layout_state is None:
                    result_tabs = getattr(self.panel, "result_tabs", None)
                    result_page = result_tabs.widget(0) if result_tabs is not None and result_tabs.count() else None
                    self._table_result_layout_state = (
                        int(self.table_editor.minimumHeight()),
                        self.table_editor.sizePolicy(),
                        int(result_tabs.minimumHeight()) if result_tabs is not None else 0,
                        result_tabs.sizePolicy() if result_tabs is not None else None,
                        int(result_page.minimumHeight()) if result_page is not None else 0,
                        result_page.sizePolicy() if result_page is not None else None,
                        bool(getattr(self.panel, "next_btn", None) and self.panel.next_btn.isVisible()),
                        bool(getattr(self.panel, "all_btn", None) and self.panel.all_btn.isVisible()),
                    )
                self.table_editor.setMinimumHeight(0)
                table_policy = self.table_editor.sizePolicy()
                table_policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
                self.table_editor.setSizePolicy(table_policy)
                self.table_editor.set_compact_action_labels(True)
                result_tabs = getattr(self.panel, "result_tabs", None)
                if result_tabs is not None:
                    result_tabs.setMinimumHeight(0)
                    result_policy = result_tabs.sizePolicy()
                    result_policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
                    result_tabs.setSizePolicy(result_policy)
                    result_page = result_tabs.widget(0) if result_tabs.count() else None
                    if result_page is not None:
                        result_page.setMinimumHeight(0)
                        page_policy = result_page.sizePolicy()
                        page_policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
                        result_page.setSizePolicy(page_policy)
                for name in ("next_btn", "all_btn"):
                    button = getattr(self.panel, name, None)
                    if button is not None:
                        button.hide()
                splitter.setCollapsible(0, False)
                sizes = [int(size) for size in splitter.sizes()]
                extent = sum(sizes) or int(splitter.height())
                if extent > 0:
                    desired = int(middle.sizeHint().height()) if middle is not None else 96
                    desired = max(72, min(desired, max(72, extent // 3)))
                    splitter.setSizes([desired, max(1, extent - desired)])
            else:
                was_focused = self._table_focus_active
                self._table_focus_active = False
                self._move_transaction_actions_to_toolbar(False)
                splitter.setCollapsible(0, False)
                tabs = getattr(self.panel, "sql_tabs", None)
                if tabs is not None:
                    for index in range(tabs.count()):
                        tab = tabs.widget(index)
                        editor = getattr(tab, "editor", None)
                        if editor is not None:
                            saved_editor = self._sql_editor_focus_state.get(id(editor))
                            if saved_editor is not None:
                                minimum_height, policy = saved_editor
                                editor.setMinimumHeight(minimum_height)
                                editor.setSizePolicy(policy)
                            editor.show()
                self._sql_editor_focus_state.clear()
                middle = getattr(self.panel, "_middle_pane", None)
                if middle is not None:
                    if self._middle_pane_focus_state is not None:
                        minimum_height, policy = self._middle_pane_focus_state
                        middle.setMinimumHeight(minimum_height)
                        middle.setSizePolicy(policy)
                    self._middle_pane_focus_state = None
                if self._table_result_layout_state is not None:
                    (
                        table_minimum,
                        table_policy,
                        result_minimum,
                        result_policy,
                        page_minimum,
                        page_policy,
                        show_next,
                        show_all,
                    ) = self._table_result_layout_state
                    self.table_editor.setMinimumHeight(table_minimum)
                    self.table_editor.setSizePolicy(table_policy)
                    self.table_editor.set_compact_action_labels(False)
                    result_tabs = getattr(self.panel, "result_tabs", None)
                    if result_tabs is not None:
                        result_tabs.setMinimumHeight(result_minimum)
                        if result_policy is not None:
                            result_tabs.setSizePolicy(result_policy)
                        result_page = result_tabs.widget(0) if result_tabs.count() else None
                        if result_page is not None:
                            result_page.setMinimumHeight(page_minimum)
                            if page_policy is not None:
                                result_page.setSizePolicy(page_policy)
                    next_button = getattr(self.panel, "next_btn", None)
                    if next_button is not None:
                        next_button.setVisible(show_next)
                    all_button = getattr(self.panel, "all_btn", None)
                    if all_button is not None:
                        all_button.setVisible(show_all)
                    self._table_result_layout_state = None
                if was_focused:
                    saved = self._splitter_sizes_before_table_focus
                    if saved and len(saved) == 2:
                        splitter.setSizes(list(saved))
                    self._splitter_sizes_before_table_focus = None
        else:
            self._table_focus_active = focused
        self.table_editor.set_sql_pane_visible(not focused)

    def _show_sql_view(self, show_sql: bool) -> None:
        state = self._current_state()
        if state is None or not state.table_mode_active or state.table_model is None:
            return
        self._set_table_focus_active(not bool(show_sql))

    def _connection_selection_changed(self, *_args: Any) -> None:
        tab = self.panel._current_tab()
        state = self._states_by_tab.get(id(tab)) if tab is not None else None
        if self._shutdown_gated:
            if state is not None:
                self._restore_connection_selection(state)
            return
        if state is None:
            self._ensure_tab_state(tab)
            return
        try:
            next_choice, next_target = self._capture_target(tab)
        except (ProfileHostError, ValueError, TypeError):
            next_choice = None
            next_target = None
        if next_target is None or next_target == state.target:
            return
        cleanup = state.session.poll_cleanup()
        if state.busy or cleanup.busy or cleanup.queued_operations or state.session.state in {
            ManualSessionState.DIRTY,
            ManualSessionState.UNKNOWN,
            ManualSessionState.CANCEL_PENDING,
            ManualSessionState.COMMITTING,
            ManualSessionState.ROLLING_BACK,
        }:
            self._restore_connection_selection(state)
            self._set_status(self._message("当前标签已有手动会话；请先提交/回滚并关闭标签", "This tab has a manual session; commit or roll back and close it first"))
            return
        self._close_state(state, disposition="")
        self._forget_state(state, retain_until_cleanup=True)
        next_generation = state.generation + 1
        setattr(tab, "_manual_generation", next_generation)
        self._ensure_tab_state(tab)
        self._visible_tab_changed(self.panel.sql_tabs.currentIndex())

    def _restore_connection_selection(self, state: _TabState) -> None:
        panel = self.panel
        combo = getattr(panel, "conn_combo", None)
        tab = state.tab
        if combo is None or tab is None:
            return
        target_index = -1
        for index in range(combo.count()):
            item = combo.itemData(index)
            if isinstance(item, dict) and str(item.get("id") or "") == state.target.connection_id:
                target_index = index
                break
        if target_index >= 0:
            previous = combo.blockSignals(True)
            combo.setCurrentIndex(target_index)
            combo.blockSignals(previous)
            item = combo.currentData()
            tab.conn_item = dict(item) if isinstance(item, dict) else None
            try:
                panel._on_connection_changed()
            except Exception:
                pass

    def _capture_target(self, tab: Any) -> tuple[ConnectionChoice, ManualSessionTarget]:
        profile = getattr(tab, "conn_item", None)
        if not isinstance(profile, dict) or not str(profile.get("id") or profile.get("connection_id") or "").strip():
            raise ProfileHostError("PROFILE_UNAVAILABLE")
        choice = self.profile_host.capture_connection(profile)
        if choice.provider not in self.supported_providers:
            raise ManualDriverError("MANUAL_PROVIDER_UNSUPPORTED")
        schema = profile_schema_for_provider(profile, choice.provider)
        tab_id = str(getattr(tab, "_manual_tab_id", "") or uuid.uuid4().hex)
        setattr(tab, "_manual_tab_id", tab_id)
        generation = int(getattr(tab, "_manual_generation", 1) or 1)
        target = ManualSessionTarget(
            tab_id=tab_id,
            connection_id=choice.connection_id,
            profile_revision=choice.revision,
            database=choice.database,
            schema=schema,
            generation=generation,
        )
        return choice, target

    def open_table_source(
        self,
        tree_service: Any,
        tree_target: TreeTarget,
        node: TreeNode,
        tree_generation: int,
    ) -> bool:
        """Open or focus a SQL tab bound to one live, signed table/view node."""
        if self._shutdown_gated or self._preparing_table_tab:
            return False
        if not isinstance(tree_target, TreeTarget) or not isinstance(node, TreeNode):
            self._set_status(self._message("表对象范围无效。", "The table source scope is invalid."))
            return False
        profile = getattr(self.panel, "_bound_conn_item", None)
        if not isinstance(profile, dict):
            profile = self.panel._browse_conn()
        if not isinstance(profile, dict):
            self._set_status(self._message("固定连接不可用。", "The fixed connection is unavailable."))
            return False
        try:
            selected_id = str(profile.get("id") or profile.get("connection_id") or "").strip()
            if selected_id != tree_target.connection_id:
                raise ManualDriverError("TABLE_SCOPE_MISMATCH")
            provider = tree_target.provider.casefold()
            oracle = provider in {"oracle", "oceanbase_oracle"}
            schema_allowlist = (node.schema,) if oracle and node.schema else ()
            selected_database = str(node.database or "") if not oracle else ""
            if not oracle and not selected_database.strip():
                raise ManualDriverError("TABLE_SCOPE_MISMATCH")
            if tree_target.database and selected_database and tree_target.database != selected_database:
                raise ManualDriverError("TABLE_SCOPE_MISMATCH")
            choice = self.profile_host.capture_connection(
                profile,
                database=selected_database or None,
                schema_allowlist=schema_allowlist,
            )
            if oracle and tree_target.database and choice.database != tree_target.database:
                raise ManualDriverError("PROFILE_MISMATCH")
            if (
                choice.connection_id != tree_target.connection_id
                or choice.revision != tree_target.profile_revision
                or choice.provider.casefold() != provider
                or choice.mode.casefold() != tree_target.mode.casefold()
                or (selected_database and choice.database != selected_database)
            ):
                raise ManualDriverError("PROFILE_MISMATCH")
            if provider not in self.supported_providers:
                raise ManualDriverError("MANUAL_PROVIDER_UNSUPPORTED")
            tab_id = uuid.uuid4().hex
            schema = node.schema if oracle else tree_target.database
            target = ManualSessionTarget(
                tab_id=tab_id,
                connection_id=choice.connection_id,
                profile_revision=choice.revision,
                database=choice.database,
                schema=schema if oracle else choice.database,
                generation=1,
            )
            table_scope = BoundManualTableScope.capture(
                tree_service,
                tree_target,
                node,
                tree_generation,
                target,
                choice,
            )
            capability = inspect_manual_provider(choice)
            if not capability.supported:
                raise ManualDriverError(capability.reason_code or "MANUAL_PROVIDER_UNSUPPORTED")
        except (ProfileHostError, ManualDriverError, ValueError, TypeError) as error:
            self._set_status(self._safe_error(error))
            return False

        for state in self._states_by_tab.values():
            existing = state.table_scope
            if (
                existing is not None
                and not state.pending_close
                and existing.tree_target == tree_target
                and existing.node.node_ref == node.node_ref
                and existing.tree_generation == int(tree_generation)
                and not state.session.poll_cleanup().cleanup_complete
            ):
                self.panel.sql_tabs.setCurrentWidget(state.tab)
                return True

        factory = ManualRelationalProviderFactory(
            choice,
            profile_host=self.profile_host,
            connector=self.connector,
            secret_resolver=self.secret_resolver,
            table_scope=table_scope,
        )
        session = ManualSession(
            target,
            factory,
            lambda sql, fixed_choice=choice: classify_manual_sql(fixed_choice, sql),
            page_size=self.page_size,
            result_budget_bytes=self.result_budget_bytes,
        )
        self._preparing_table_tab = True
        try:
            title = f"{node.name} · " + self._message("表数据", "Table data")
            tab = self.panel._new_sql_tab(title=title)
        finally:
            self._preparing_table_tab = False
        if tab is None:
            return False
        setattr(tab, "_manual_tab_id", target.tab_id)
        setattr(tab, "_manual_generation", target.generation)
        setattr(tab, "manual_session", session)
        setattr(tab, "manual_target", target)
        state = _TabState(
            tab=tab,
            target=target,
            choice=choice,
            session=session,
            generation=target.generation,
            table_scope=table_scope,
            table_page_size=self.page_size,
            query_page_size=self.page_size,
        )
        self._states_by_tab[id(tab)] = state
        state.request_id = uuid.uuid4().hex
        state.active_operation = "table_open"
        state.busy = True
        self.result_model.clear()
        self.result_view.hide()
        self.table_editor.bind_model(None, page_size=state.table_page_size, busy=True)
        self.table_editor.show()
        self._set_status(self._message("正在读取所选表…", "Opening the selected table…"), state=state)
        future = session.open_table_source(
            tree_service,
            tree_target,
            node,
            tree_generation=tree_generation,
            choice=choice,
            page_size=state.table_page_size,
        )
        self._watch_future(state, "table_open", future, state.request_id)
        self._update_actions(state)
        return True

    def refresh_table_source(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        state = self._current_state()
        if state is None or state.table_scope is None:
            return
        if state.busy:
            return
        if state.table_model is not None and state.table_model.has_staged_changes:
            self._set_status(self._message(
                "请先应用或放弃本地变更，再刷新表格。",
                "Apply or discard local changes before refreshing the table.",
            ), state=state)
            return
        self._submit_table_source_open(state)

    def open_table_query(self, *_args: Any) -> None:
        state = self._current_state()
        if not self._can_change_table_query(state):
            return
        from ui.data_center.table_query_dialog import TableQueryDialog

        dialog = TableQueryDialog(state.table_model.metadata, state.table_query, getattr(self.panel, "language", "zh"), self.panel)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and self._current_state() is state:
                self.apply_table_query(dialog.query())
        finally:
            dialog.deleteLater()

    def _can_change_table_query(self, state: _TabState | None) -> bool:
        if self._shutdown_gated or state is None or state.busy or not state.table_mode_active or state.table_scope is None or state.table_model is None:
            return False
        if state.table_model.has_staged_changes:
            self._set_status(self._message(
                "请先应用或放弃本地变更，再改变服务端筛选与排序。",
                "Apply or discard local changes before changing server filters and sorting.",
            ), state=state)
            return False
        return True

    def apply_table_query(self, query: TableQuerySpec) -> None:
        state = self._current_state()
        if not self._can_change_table_query(state):
            return
        try:
            dialect = create_changeset_dialect(state.choice, state.target)
            build_table_query(state.table_model.metadata, dialect, query)
        except (TableSourceError, ValueError, TypeError):
            self._set_status(self._message("筛选字段、条件或排序已无效，请重新选择。", "The filter columns, conditions or sorting are invalid; choose them again."), state=state)
            return
        self._submit_table_source_open(state, query=query)

    def _submit_table_source_open(self, state: _TabState, *, query: TableQuerySpec | None = None) -> None:
        scope = state.table_scope
        if scope is None or state.busy:
            return
        state.request_id = uuid.uuid4().hex
        state.active_operation = "table_open"
        state.busy = True
        state.export_status = ""
        self._set_status(self._message("正在读取所选表…", "Opening the selected table…"), state=state)
        future = state.session.open_table_source(
            scope.service,
            scope.tree_target,
            scope.node,
            tree_generation=scope.tree_generation,
            choice=state.choice,
            page_size=state.table_page_size,
            query=state.table_query if query is None else query,
        )
        self._watch_future(state, "table_open", future, state.request_id)
        self._update_actions(state)

    def _table_page_size_changed(self, index: int) -> None:
        state = self._current_state()
        if state is None or state.table_scope is None:
            return
        value = self.table_editor.page_size.itemData(index)
        if value in {50, 200, 500}:
            state.table_page_size = int(value)

    def _table_overlay_changed(self, _pending: bool) -> None:
        state = self._current_state()
        self._update_actions(state)
        self._sync_paging(state)
        self._refresh_workbench_actions()

    def _ensure_tab_state(self, tab: Any) -> _TabState | None:
        if tab is None:
            return None
        if getattr(tab, "_history_target_locked", False):
            return None
        if self._preparing_table_tab:
            return None
        key = id(tab)
        existing = self._states_by_tab.get(key)
        if existing is not None:
            return existing
        if self._shutdown_gated:
            self._set_status(self._message(
                "工作台正在关闭；暂不创建新的手动会话。",
                "The workbench is shutting down; no new manual session can be created.",
            ))
            return None
        try:
            choice, target = self._capture_target(tab)
            capability = inspect_manual_provider(choice)
            if not capability.supported:
                raise ManualDriverError(capability.reason_code or "MANUAL_PROVIDER_UNSUPPORTED")
            factory = ManualRelationalProviderFactory(
                choice,
                profile_host=self.profile_host,
                connector=self.connector,
                secret_resolver=self.secret_resolver,
            )
            classifier = lambda sql, fixed_choice=choice: classify_manual_sql(fixed_choice, sql)
        except (ProfileHostError, ManualDriverError, ValueError, TypeError) as error:
            self._set_status(self._safe_error(error))
            self._update_actions(None)
            return None
        session = ManualSession(
            target,
            factory,
            classifier,
            page_size=self.page_size,
            result_budget_bytes=self.result_budget_bytes,
        )
        state = _TabState(tab=tab, target=target, choice=choice, session=session, generation=target.generation, query_page_size=self.page_size)
        self._states_by_tab[key] = state
        setattr(tab, "manual_session", session)
        setattr(tab, "manual_target", target)
        self._bind_completion(tab, state)
        return state

    def validate_history_record(self, record: Any) -> str:
        """Check a frozen relational target without creating a session or provider."""

        code, _profile, _choice = self._history_profile(record)
        return code

    def _history_profile(self, record: Any) -> tuple[str, dict[str, Any] | None, ConnectionChoice | None]:
        from tools.data_center.workspace_store import HistoryRecord

        if type(record) is not HistoryRecord:
            return "PROFILE_UNAVAILABLE", None, None
        target = record.target
        text_kind = getattr(record.text_kind, "value", record.text_kind)
        tab_kind = getattr(target.tab_kind, "value", target.tab_kind)
        if text_kind != "sql" or tab_kind != "sql":
            return "MANUAL_PROVIDER_UNSUPPORTED", None, None
        if not target.connection_id or not target.profile_revision:
            return "PROFILE_UNAVAILABLE", None, None
        try:
            profile = self.profile_host.load_connection_profile(
                target.connection_id,
                target.profile_revision,
            )
            choice = self.profile_host.capture_connection(
                profile,
                database=target.database,
            )
        except ProfileHostError as error:
            code = error.code if error.code in {"PROFILE_UNAVAILABLE", "PROFILE_MISMATCH"} else "PROFILE_UNAVAILABLE"
            return code, None, None
        except Exception:
            return "PROFILE_UNAVAILABLE", None, None
        if choice.connection_id != target.connection_id or choice.revision != target.profile_revision:
            return "PROFILE_MISMATCH", None, None
        if choice.provider not in self.supported_providers:
            return "MANUAL_PROVIDER_UNSUPPORTED", None, None
        try:
            schema = profile_schema_for_provider(profile, choice.provider)
        except Exception:
            return "PROFILE_UNAVAILABLE", None, None
        if choice.database != target.database or schema != target.schema:
            return "PROFILE_MISMATCH", None, None
        return "", dict(profile), choice

    def history_draft_changed(self, tab: Any) -> None:
        """Refresh the explicit Run affordance for a dormant history draft."""

        if tab is self.panel._current_tab():
            self._update_actions(self._states_by_tab.get(id(tab)))
            self._refresh_workbench_actions()

    def _activate_history_draft(self, tab: Any) -> _TabState | None:
        record = getattr(tab, "_history_target_record", None)
        code, profile, choice = self._history_profile(record)
        if code or profile is None or choice is None:
            tab._history_target_valid = False
            tab._history_target_validation_code = code or "PROFILE_UNAVAILABLE"
            self.panel._set_history_draft_status(tab)
            self._update_actions(None)
            return None
        try:
            from tools.data_center.manual_session import ManualSessionTarget

            generation = int(getattr(tab, "_manual_generation", 1) or 1)
            tab_id = str(getattr(tab, "_manual_tab_id", "") or uuid.uuid4().hex)
            target_record = record.target
            target = ManualSessionTarget(
                tab_id=tab_id,
                connection_id=target_record.connection_id,
                profile_revision=target_record.profile_revision,
                database=target_record.database,
                schema=target_record.schema,
                generation=generation,
            )
            factory = ManualRelationalProviderFactory(
                choice,
                profile_host=self.profile_host,
                connector=self.connector,
                secret_resolver=self.secret_resolver,
            )
            classifier = lambda sql, fixed_choice=choice: classify_manual_sql(fixed_choice, sql)
            session = ManualSession(
                target,
                factory,
                classifier,
                page_size=self.page_size,
                result_budget_bytes=self.result_budget_bytes,
            )
        except Exception:
            tab._history_target_valid = False
            tab._history_target_validation_code = "PROFILE_UNAVAILABLE"
            self.panel._set_history_draft_status(tab)
            self._update_actions(None)
            return None
        state = _TabState(
            tab=tab,
            target=target,
            choice=choice,
            session=session,
            generation=generation,
            query_page_size=self.page_size,
        )
        self._states_by_tab[id(tab)] = state
        setattr(tab, "conn_item", profile)
        setattr(tab, "manual_session", session)
        setattr(tab, "manual_target", target)
        setattr(tab, "_history_target_locked", False)
        setattr(tab, "_history_target_valid", True)
        setattr(tab, "_restored_history_record", record)
        self._update_actions(state)
        return state

    def _visible_tab_changed(self, _index: int = -1) -> None:
        tab = self.panel._current_tab()
        state = self._ensure_tab_state(tab)
        self._bind_completion(tab, state)
        self._update_actions(state)
        if state is None:
            self._set_table_focus_active(False)
            self.table_editor.hide()
            self.result_view.show()
            self._clear_visible_results()
            for name in ("next_btn", "all_btn"):
                button = getattr(self.panel, name, None)
                if button is not None:
                    button.setEnabled(False)
            if tab is not None and getattr(tab, "_history_target_locked", False):
                self.panel._set_history_draft_status(tab)
            else:
                self._set_status(self._state_text(ManualSessionState.DISCONNECTED))
            return
        if state.script is not None and not state.busy:
            self._script_selection_changed(state.script_index)
        if state.table_mode_active and state.table_model is not None:
            self.result_model.replace_pages(state.pages)
            self.result_view.hide()
            self.table_editor.set_server_query_counts(len(state.table_query.filters), len(state.table_query.orders))
            self.table_editor.bind_model(
                state.table_model,
                page_size=state.table_page_size,
                has_more=bool(state.last_page and state.last_page.has_more),
                busy=state.busy,
            )
            self.table_editor.show()
        elif state.last_page is not None:
            self.table_editor.hide()
            self.result_model.replace_pages(state.pages)
            self.result_view.show()
        elif state.last_statement is not None:
            self.table_editor.hide()
            self._clear_visible_results()
        else:
            self.table_editor.hide()
            self._clear_visible_results()
        self._sync_table_focus_layout(state)
        self._set_status(state.export_status or self._state_text(state.session.state), state=state)
        self._sync_paging(state)

    def run_current(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        tab = self.panel._current_tab()
        if tab is not None and getattr(tab, "_history_target_locked", False):
            state = self._activate_history_draft(tab)
        else:
            state = self._ensure_tab_state(tab)
        if state is None:
            return
        if state.busy:
            return
        if state.table_model is not None and state.table_model.has_staged_changes:
            self._set_status(self._message(
                "此表格标签有本地待应用变更；请先预览应用或放弃，再执行 SQL。",
                "This table tab has staged local changes; preview and apply or discard them before running SQL.",
            ), state=state)
            return
        editor = self.panel._current_editor()
        if editor is None:
            return
        try:
            selected, _position = editor.selected_or_all()
            if selected:
                block = selected_procedural_sql(selected, state.choice.provider)
                if block is not None:
                    self._run_selected_block(state, block)
                    return
            sql = self._execution_sql(editor)
        except SqlScriptError as error:
            if error.code == "SQL_PROCEDURAL_SCRIPT_UNSUPPORTED":
                self._set_status(self._message("检测到过程块；请选中完整块，再点击执行或按 Ctrl+Enter。", "Select the complete procedural block, then use Run or Ctrl+Enter."), state=state)
                return
            self._set_status(self._message(
                f"SQL 边界无法可靠识别（{error.code}）；请检查引号、注释和数据库转义模式。",
                f"Cannot determine SQL boundaries ({error.code}); check quotes, comments and server escape mode.",
            ))
            return
        if not sql:
            self._set_status(self._message("没有可执行的语句", "Nothing to run"))
            return
        if _looks_like_procedure_block(sql):
            self._set_status(self._message(
                "存储过程/触发器块不能按分号拆分；当前手动宿主暂不支持执行此类代码。",
                "Stored routine and trigger blocks are not split on semicolons; this manual host does not execute them yet.",
            ))
            return
        classification = classify_manual_sql(state.choice, sql)
        if classification.kind is StatementKind.MULTI_STATEMENT:
            self._set_status(self._message("一次只执行一个语句；请选中目标语句", "Run one statement at a time; select the intended statement"))
            return
        risk = classify_statement(sql, state.choice.dialect or "oracle")
        if risk.get("needs_confirm") and classification.kind not in {StatementKind.DDL, StatementKind.DCL}:
            from ui.confirm_dialog import confirm_action

            zh = getattr(self.panel, "language", "zh") == "zh"
            detail = (
                f"类型：{risk.get('label') or '未知'}\n\n{sql}"
                if zh else
                f"Kind: {risk.get('label') or 'Unknown'}\n\n{sql}"
            )
            if not confirm_action(
                self.panel,
                self.panel._title(),
                detail,
                confirm_text="执行" if zh else "Run",
                danger=True,
            ):
                return
        if classification.kind in {StatementKind.DDL, StatementKind.DCL}:
            if state.session.state not in {ManualSessionState.CLEAN, ManualSessionState.DISCONNECTED}:
                self._set_status(self._message(
                    "DDL/DCL 只允许在 CLEAN 会话中执行；请先提交或回滚。",
                    "DDL/DCL is allowed only in a CLEAN session; commit or roll back first.",
                ))
                return
            from ui.confirm_dialog import confirm_action

            kind_text = "DDL" if classification.kind is StatementKind.DDL else "DCL"
            accepted = confirm_action(
                self.panel,
                self._message("确认执行" + kind_text, "Confirm " + kind_text),
                self._message(
                    f"此操作可能隐式提交事务，且无法通过回滚撤销。\n\n{sql}",
                    f"This operation may implicitly commit and cannot be undone with rollback.\n\n{sql}",
                ),
                confirm_text=self._message("执行", "Run"),
                danger=True,
            )
            if not accepted:
                return
        state.request_id = uuid.uuid4().hex
        state.busy = True
        state.script = None
        state.script_pages.clear()
        state.last_error = ""
        state.export_status = ""
        from ui.data_center.query_history_service import make_history_capture

        state.history_capture = make_history_capture(
            sql,
            connection_id=state.target.connection_id,
            profile_revision=state.target.profile_revision,
            database=state.target.database,
            schema=state.target.schema,
            runtime_tab_id=state.target.tab_id,
            generation=state.generation,
        )
        state.history_query_id = ""
        operation = "query" if classification.kind is StatementKind.QUERY else "submit"
        self._set_status(self._message("正在执行手动事务语句…", "Running manual transaction statement…"))
        if operation == "query":
            future = state.session.query(sql, page_size=state.query_page_size)
        else:
            future = state.session.submit(
                sql,
                ddl_authorized=classification.kind is StatementKind.DDL,
                dcl_authorized=classification.kind is StatementKind.DCL,
                page_size=state.query_page_size,
            )
        self._watch_future(state, operation, future, state.request_id, state.history_capture)
        state.table_mode_active = False
        self.table_editor.hide()
        self.result_view.show()
        self._sync_table_focus_layout(state)
        self._update_actions(state)

    def commit_current(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        state = self._current_state()
        if state is None or state.busy or state.session.state is not ManualSessionState.DIRTY:
            return
        self._submit_transaction_action(state, "commit", state.session.commit())

    def _run_selected_block(self, state: _TabState, sql: str) -> None:
        if state.session.state not in {ManualSessionState.CLEAN, ManualSessionState.DISCONNECTED}:
            self._set_status(self._message("完整过程块要求 CLEAN 会话；请先处理已有事务。", "Complete blocks require a CLEAN session; resolve the existing transaction first."), state=state)
            return
        if len(sql.encode("utf-8")) > MAX_SCRIPT_BYTES:
            self._set_status(self._message("过程块超过可保存的文本上限。", "The block exceeds the workspace text limit."), state=state)
            return
        from ui.confirm_dialog import confirm_action

        detail = self._message(
            f"目标：{state.target.connection_id} / {state.target.database} / {state.target.schema}\n按一整个块提交给数据库，不拆分内部语句。块内提交、回滚、DDL 或其他副作用按服务端语义生效，外层无法保证撤销；失败或取消也不会自动重跑。请核对编辑器中的完整选区。\n\n",
            f"Target: {state.target.connection_id} / {state.target.database} / {state.target.schema}\nSubmit the whole selected block once. Internal commits, rollback, DDL and side effects follow server semantics and may be irreversible. Failure/cancellation never triggers replay. Review the full selection in the editor.\n\n",
        ) + sql[:1600]
        if not confirm_action(self.panel, self._message("执行完整过程块", "Execute complete block"), detail, confirm_text=self._message("执行整个块", "Execute block"), danger=True):
            return
        if self._shutdown_gated or self._current_state() is not state or state.busy:
            return
        from ui.data_center.query_history_service import make_history_capture

        state.history_capture = make_history_capture(sql, connection_id=state.target.connection_id, profile_revision=state.target.profile_revision, database=state.target.database, schema=state.target.schema, runtime_tab_id=state.target.tab_id, generation=state.generation)
        state.history_query_id = ""
        state.script = None
        state.script_pages.clear()
        state.request_id = uuid.uuid4().hex
        state.busy = True
        state.last_error = ""
        state.export_status = ""
        state.table_mode_active = False
        future = state.session.execute_block(sql, provider=state.choice.provider, authorized=True)
        self._watch_future(state, "block", future, state.request_id, state.history_capture)
        self.table_editor.hide()
        self.result_view.show()
        self._set_status(self._message("正在执行完整选中块…", "Executing the complete selected block…"), state=state)
        self._update_actions(state)

    def run_all_current(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        tab = self.panel._current_tab()
        state = self._activate_history_draft(tab) if tab is not None and getattr(tab, "_history_target_locked", False) else self._ensure_tab_state(tab)
        editor = self.panel._current_editor()
        if state is None or state.busy or editor is None:
            return
        if state.table_model is not None and state.table_model.has_staged_changes:
            self._set_status(self._message("请先应用或放弃表格本地变更。", "Apply or discard staged table changes first."), state=state)
            return
        text = editor.toPlainText()
        try:
            script = scan_sql(text, state.choice.provider)
            statements = tuple(segment.sql for segment in script.segments)
            if not 1 <= len(statements) <= MAX_SCRIPT_STATEMENTS or len(text.encode("utf-8")) > MAX_SCRIPT_BYTES:
                raise SqlScriptError("SCRIPT_LIMIT")
            if has_procedural_block(script.code):
                raise SqlScriptError("SCRIPT_PROCEDURAL_BLOCK_UNSUPPORTED")
            kinds = tuple(classify_manual_sql(state.choice, sql).kind for sql in statements)
            if any(kind not in {StatementKind.QUERY, StatementKind.DML, StatementKind.DDL, StatementKind.DCL} for kind in kinds):
                raise SqlScriptError("SCRIPT_STATEMENT_UNSUPPORTED")
        except SqlScriptError as error:
            detail = self._message(f"最多 {MAX_SCRIPT_STATEMENTS} 条、{MAX_SCRIPT_BYTES // 1024} KiB；脚本不能为空。", f"Use 1–{MAX_SCRIPT_STATEMENTS} statements, at most {MAX_SCRIPT_BYTES // 1024} KiB.") if error.code == "SCRIPT_LIMIT" else error.code
            self._set_status(self._message(f"不能执行全部：{detail}", f"Cannot run all: {detail}"), state=state)
            return
        from ui.confirm_dialog import confirm_action

        details = self._message(
            f"共 {len(statements)} 条，目标：{state.target.connection_id} / {state.target.database} / {state.target.schema}\n依次执行；失败或取消后停止剩余语句。已执行写入留在当前事务，提交/回滚需另行操作。DDL/DCL 可能隐式提交且仍受 CLEAN 限制。\n\n",
            f"{len(statements)} statements; target: {state.target.connection_id} / {state.target.database} / {state.target.schema}\nRun in order; stop on failure or cancellation. Writes remain in the current transaction. DDL/DCL may implicitly commit and require CLEAN.\n\n",
        )
        details += "\n".join(f"{index + 1}. {kind.value.upper()} · {sql[:90].replace(chr(10), ' ')}" for index, (sql, kind) in enumerate(zip(statements, kinds)) if index < 10)
        if len(statements) > 10:
            details += self._message(f"\n另 {len(statements) - 10} 条，请在编辑器核对完整脚本。", f"\n{len(statements) - 10} more; review the full script in the editor.")
        if not confirm_action(self.panel, self._message("执行全部", "Run all"), details, confirm_text=self._message(f"执行 {len(statements)} 条", f"Run {len(statements)}"), danger=any(kind is not StatementKind.QUERY for kind in kinds)):
            return
        if self._shutdown_gated or self._current_state() is not state or state.busy:
            return
        from ui.data_center.query_history_service import make_history_capture

        state.history_capture = make_history_capture(text, connection_id=state.target.connection_id, profile_revision=state.target.profile_revision, database=state.target.database, schema=state.target.schema, runtime_tab_id=state.target.tab_id, generation=state.generation)
        state.history_query_id = ""
        state.request_id = uuid.uuid4().hex
        state.busy = True
        state.script = None
        state.script_pages.clear()
        state.table_mode_active = False
        state.export_status = ""
        state.last_error = ""
        future = state.session.run_script(statements, page_size=state.query_page_size, ddl_authorized=StatementKind.DDL in kinds, dcl_authorized=StatementKind.DCL in kinds)
        self._watch_future(state, "script", future, state.request_id, state.history_capture)
        self.table_editor.hide()
        self.result_view.show()
        self._set_status(self._message(f"正在执行 {len(statements)} 条 SQL…", f"Running {len(statements)} statements…"), state=state)
        self._update_actions(state)

    def apply_table_preview(self, preview: Any) -> None:
        if self._shutdown_gated:
            return
        state = self._current_state()
        if (
            state is None or state.busy or state.table_model is None
            or state.table_model.change_set is None or not state.table_model.has_staged_changes
        ):
            return
        try:
            future = state.table_model.change_set.apply(state.session, preview)
        except Exception as error:
            self._set_status(self._safe_error(error), state=state)
            return
        state.request_id = uuid.uuid4().hex
        state.busy = True
        state.active_operation = "table_apply"
        state.export_status = ""
        self._set_status(self._message("正在通过事务保存点应用变更…", "Applying changes through the transaction savepoint…"), state=state)
        self._watch_future(state, "table_apply", future, state.request_id)
        self._update_actions(state)

    def rollback_current(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        state = self._current_state()
        if state is None or state.busy or state.session.state not in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}:
            return
        self._submit_transaction_action(state, "rollback", state.session.rollback())

    def cancel_current(self, *_args: Any) -> None:
        state = self._current_state()
        requested = False
        for context, worker in tuple(self._export_jobs.values()):
            worker.cancel()
            context.state.export_status = self._message("正在取消导出…", "Stopping export…")
            requested = True
        stop_state = state if state is not None and state.busy else None
        if stop_state is None:
            full_exports = self._active_full_export_states()
            stop_state = full_exports[0] if full_exports else None
        if stop_state is not None:
            stop_state.session.cancel_current()
            stopping_full_export_in_other_tab = (
                stop_state is not state and stop_state.active_operation == "export_full"
            )
            if stop_state.active_operation == "export_full":
                stop_state.export_status = self._message("正在取消导出…", "Stopping export…")
            if stopping_full_export_in_other_tab:
                message = self._message(
                    "已请求停止另一标签页的完整查询导出；正在等待当前操作结束。",
                    "Stop requested for the full-query export in another tab; waiting for the current operation to finish.",
                )
                status_state = state
            else:
                message = self._message(
                    "已请求停止；正在等待当前操作结束，连接保持打开。",
                    "Stop requested; waiting for the current operation to finish. The connection remains open.",
                )
                status_state = stop_state
            self._set_status(message, state=status_state)
            requested = True
        if not requested:
            return
        if stop_state is None:
            self._set_status(self._message(
                "已请求停止导出；等待文件操作结束。",
                "Export cancellation requested; waiting for the file operation to finish.",
            ))
        self._update_actions(self._current_state())

    def _submit_transaction_action(self, state: _TabState, operation: str, future: Future) -> None:
        state.request_id = uuid.uuid4().hex
        state.busy = True
        self._set_status(self._message("正在提交事务…", "Committing transaction…") if operation == "commit" else self._message("正在回滚事务…", "Rolling back transaction…"))
        self._watch_future(state, operation, future, state.request_id)
        self._update_actions(state)

    def fetch_next_page(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        state = self._current_state()
        if state is None or state.busy or state.last_page is None or not state.last_page.has_more:
            return
        if not self._selected_result_can_continue(state):
            self._set_status(self._message("此结果游标已被后续语句替换；仅保留已加载结果。", "Later statements replaced this cursor; only loaded rows remain."), state=state)
            return
        if state.table_model is not None and state.table_model.has_staged_changes:
            self._set_status(self._message(
                "请先应用或放弃本地变更，再读取下一页。",
                "Apply or discard local changes before fetching another page.",
            ), state=state)
            return
        state.request_id = uuid.uuid4().hex
        state.busy = True
        state.export_status = ""
        self._set_status(self._message("正在读取下一页…", "Fetching next page…"))
        page_size = state.table_page_size if state.table_mode_active else state.query_page_size
        expected_query_id = state.history_query_id or state.last_page.query_id
        self._watch_future(
            state,
            "page",
            state.session.fetch_next_page(page_size),
            state.request_id,
            None if state.script is not None else state.history_capture,
            expected_query_id=expected_query_id,
        )
        self._update_actions(state)

    def _watch_future(
        self,
        state: _TabState,
        operation: str,
        future: Future,
        request_id: str,
        history_capture: Any = None,
        *,
        expected_query_id: str = "",
    ) -> None:
        state.active_operation = operation
        target = state.target
        if history_capture is not None:
            try:
                self.panel._ensure_query_history_service().track_manual_future(
                    future,
                    history_capture,
                    target_tab_id=target.tab_id,
                    generation=target.generation,
                    expected_query_id=expected_query_id,
                )
            except Exception:
                self.panel._result_status_for_history_failure("STORE_FAILED")

        def completed(result_future: Future) -> None:
            try:
                result = result_future.result()
                error_code = ""
            except ManualSessionError as error:
                result = None
                error_code = error.code
            except Exception:
                result = None
                error_code = "MANUAL_SESSION_FAILED"
            history_query_id = ""
            if history_capture is not None:
                if not error_code:
                    page = result if isinstance(result, ManualQueryPage) else (
                        result.page if isinstance(result, ManualStatementResult) else None
                    )
                    if isinstance(page, ManualQueryPage):
                        matching = (
                            page.tab_id == target.tab_id
                            and page.generation == target.generation
                            and (not expected_query_id or page.query_id == expected_query_id)
                        )
                        history_query_id = page.query_id if matching else ""
            self._bridge.completed.emit(_FutureDelivery(
                target.tab_id,
                target.generation,
                request_id,
                operation,
                result,
                error_code,
                history_query_id,
            ))

        future.add_done_callback(completed)

    @pyqtSlot(object)
    def _deliver(self, delivery: _FutureDelivery) -> None:
        state = self._state_by_target(delivery.tab_id, delivery.generation)
        if state is None or state.request_id != delivery.request_id:
            return
        state.busy = False
        state.active_operation = ""
        if delivery.history_query_id:
            state.history_query_id = delivery.history_query_id
        if delivery.error_code:
            state.last_error = delivery.error_code
            if delivery.operation == "export_full":
                code = delivery.error_code
                if code.startswith("RESULT_EXPORT_"):
                    code = code[len("RESULT_EXPORT_"):]
                state.export_status = self._export_error_text(code)
                self._set_status(state.export_status, state=state)
            else:
                self._set_status(self._safe_error(ManualSessionError(delivery.error_code)), state=state)
            if state.table_mode_active and self._current_state() is state:
                self.table_editor.set_operation_state(
                    has_more=bool(state.last_page and state.last_page.has_more),
                    busy=False,
                )
            if delivery.operation == "close":
                state.pending_close = False
            self._update_actions(state)
            self._refresh_workbench_actions()
            return
        if isinstance(delivery.result, ManualScriptResult):
            state.script = delivery.result
            state.script_pages.clear()
            state.last_error = delivery.result.error_code
            state.table_mode_active = False
            state.script_index = next((index for index, entry in enumerate(delivery.result.entries) if entry.error_code and entry.error_code != "SKIPPED"), len(delivery.result.entries) - 1)
            if self._current_tab_is(state):
                self.table_editor.hide()
                self.result_view.show()
                self._script_selection_changed(state.script_index)
            else:
                state.pages = ()
                state.last_page = None
                state.last_statement = None
        elif isinstance(delivery.result, TableSourceOpenResult):
            if self._completion_catalog.learn_table(delivery.result.metadata):
                self.refresh_completion_bindings()
            state.script = None
            state.script_pages.clear()
            state.table_result = delivery.result
            state.table_query = delivery.result.query
            state.table_model = TableEditorModel(
                delivery.result.metadata,
                state.choice,
                language=getattr(self.panel, "language", "zh"),
            )
            state.table_model.replace_page(delivery.result.page)
            state.table_mode_active = True
            state.table_page_size = delivery.result.page.page_size
            state.pages = (delivery.result.page,)
            state.last_page = delivery.result.page
            state.last_error = ""
            state.last_statement = None
            try:
                dialect = create_changeset_dialect(state.choice, state.target)
                table_sql = build_table_select(delivery.result.metadata, dialect)
            except Exception:
                table_sql = ""
            if table_sql:
                editor = getattr(state.tab, "editor", None)
                if editor is not None:
                    editor.setPlainText(table_sql)
                    state.tab.dirty = False
            if self._current_state() is state:
                self.result_model.replace_page(delivery.result.page)
                self.result_view.hide()
                self.table_editor.set_server_query_counts(len(state.table_query.filters), len(state.table_query.orders))
                self.table_editor.bind_model(
                    state.table_model,
                    page_size=state.table_page_size,
                    has_more=delivery.result.page.has_more,
                    busy=False,
                )
                self.table_editor.show()
                self._sync_table_focus_layout(state)
        elif isinstance(delivery.result, ManualQueryPage):
            if delivery.operation == "page" and state.pages:
                state.pages = (*state.pages, delivery.result)
            else:
                state.pages = (delivery.result,)
            state.last_page = delivery.result
            if state.script is not None:
                state.script_pages[state.script_index] = state.pages
            state.last_error = ""
            if state.table_mode_active and state.table_model is not None:
                if delivery.operation == "page":
                    state.table_model.append_page(delivery.result)
                else:
                    state.table_model.replace_page(delivery.result)
            if self._current_state() is state:
                if state.table_mode_active and state.table_model is not None:
                    if delivery.operation == "page":
                        self.result_model.append_page(delivery.result)
                    else:
                        self.result_model.replace_page(delivery.result)
                    self.table_editor.bind_model(
                        state.table_model,
                        page_size=state.table_page_size,
                        has_more=delivery.result.has_more,
                        busy=False,
                    )
                    self.table_editor.show()
                    self.result_view.hide()
                    self._sync_table_focus_layout(state)
                else:
                    if delivery.operation == "page":
                        self.result_model.append_page(delivery.result)
                    else:
                        self.result_model.replace_page(delivery.result)
                    self.table_editor.hide()
                    self.result_view.show()
                    self._sync_table_focus_layout(state)
        elif isinstance(delivery.result, ManualStatementResult):
            state.pages = ()
            state.last_page = None
            state.last_statement = delivery.result
            state.last_error = ""
            if delivery.result.kind is StatementKind.PROCEDURAL:
                state.export_status = self._message(
                    f"完整块已返回；{self._state_text(delivery.result.state)}。块内副作用按服务端语义处理。",
                    f"Complete block returned; {self._state_text(delivery.result.state)}. Internal effects follow server semantics.",
                )
            if self._current_state() is state:
                self._clear_visible_results()
        elif delivery.operation == "table_apply":
            result = delivery.result
            if isinstance(result, ManualBatchResult) and result.succeeded:
                if state.table_model is not None:
                    state.table_model.clear_staged_after_apply()
                    state.table_model.mark_page_stale()
                state.last_error = ""
                state.export_status = self._message(
                    "已应用到当前事务；结果页保留原始读取值。刷新可查看当前事务值，提交或回滚仍需显式操作。",
                    "Applied to the current transaction; the page keeps its original read values. Refresh to view transaction values. Commit or rollback remains explicit.",
                )
            else:
                code = getattr(result, "error_code", "") or "CHANGESET_FAILED"
                state.last_error = code
                state.export_status = self._safe_error(ManualSessionError(code))
        elif delivery.operation == "commit" or delivery.operation == "rollback":
            state.last_error = ""
        elif delivery.operation == "export_full":
            receipt = delivery.result
            if receipt is not None and hasattr(receipt, "rows") and hasattr(receipt, "path"):
                state.export_status = self._message(
                    f"已导出 {receipt.rows} 行：{receipt.path.name}",
                    f"Exported {receipt.rows} rows: {receipt.path.name}",
                )
            else:
                state.export_status = self._export_error_text("EXPORT_FAILED")
        elif delivery.operation == "close":
            state.pending_close = False
            if isinstance(delivery.result, ManualCloseResult) and delivery.result.closed:
                if delivery.result.transaction_outcome_unknown:
                    state.last_error = "TRANSACTION_OUTCOME_UNKNOWN"
                if state.remove_tab_on_close:
                    self._finish_tab_close(state)
        if self._current_state() is state:
            self._set_status(state.export_status or self._state_text(state.session.state))
            self._sync_paging(state)
            if state.table_mode_active:
                self.table_editor.set_operation_state(
                    has_more=bool(state.last_page and state.last_page.has_more),
                    busy=state.busy,
                )
        self._update_actions(state)
        self._refresh_workbench_actions()

    def _state_by_target(self, tab_id: str, generation: int) -> _TabState | None:
        for state in self._all_session_states():
            if state.target.tab_id == tab_id and state.generation == generation:
                return state
        return None

    def request_tab_close(self, index: int) -> bool:
        tabs = getattr(self.panel, "sql_tabs", None)
        tab = tabs.widget(index) if tabs is not None and index >= 0 else None
        state = self._states_by_tab.get(id(tab)) if tab is not None else None
        if state is None:
            return False
        if self._import_host.has_parent_decision(state.target.tab_id, state.generation):
            self._set_status(self._message(
                "此标签仍有导入预览或事务；请先关闭预览，或明确提交/回滚导入后再关闭标签。",
                "This tab still owns an import preview or transaction. Close the preview or explicitly commit/roll back the import before closing this tab.",
            ), state=state)
            return True
        if state.table_model is not None and state.table_model.has_staged_changes:
            self._set_status(self._message(
                "此标签有本地待应用变更；请先预览应用或放弃，再关闭标签。",
                "This tab has staged local changes; preview and apply or discard them before closing.",
            ), state=state)
            return True
        if state.busy or state.session.poll_cleanup().busy or self._has_export_for_state(state):
            self._set_status(self._message(
                "当前操作仍在进行；请等待或停止后再关闭标签。",
                "An operation is still running; wait or stop it before closing this tab.",
            ), state=state)
            return True
        disposition = ""
        if state.session.state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}:
            disposition = self._ask_dirty_close(state)
            if disposition is None:
                return True
        if disposition in {"commit", "rollback"}:
            self.manual_dirty_close(disposition, index=index)
        else:
            state.remove_tab_on_close = True
            self._close_state(state, disposition="")
        return True

    def _ask_dirty_close(self, state: _TabState) -> str | None:
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox(self.panel)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self._message("关闭手动会话", "Close manual session"))
        if state.session.state is ManualSessionState.UNKNOWN:
            box.setText(self._message(
                "事务结果未知。选择“确认回滚并关闭”以请求回滚并释放连接；结果仍保持未知，不会报告为已确认回滚。",
                "The transaction outcome is unknown. Confirm rollback and close to request rollback and release the connection; the outcome remains unknown and will not be reported as confirmed rollback.",
            ))
            commit = None
        else:
            box.setText(self._message(
                "此标签存在未提交事务。选择提交、回滚，或取消关闭并保留会话。",
                "This tab has an uncommitted transaction. Commit, roll back, or cancel and keep the session open.",
            ))
            commit = box.addButton(self._message("提交并关闭", "Commit and close"), QMessageBox.ButtonRole.AcceptRole)
        rollback = box.addButton(
            self._message("确认回滚并关闭" if state.session.state is ManualSessionState.UNKNOWN else "回滚并关闭",
                          "Confirm rollback and close" if state.session.state is ManualSessionState.UNKNOWN else "Roll back and close"),
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel = box.addButton(self._message("取消关闭", "Keep open"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is commit:
            return "commit"
        if clicked is rollback:
            return "rollback"
        return None

    def manual_dirty_close(self, disposition: str | None, *, index: int | None = None) -> bool:
        """Apply an explicit commit/rollback close decision to one dirty tab.

        Canceling the UI close does not call ``session.close('cancel')``; that
        ManualSession disposition means cancel the active call, roll back, and
        close the connection.
        """
        if self._shutdown_gated or disposition in {None, "cancel", ""}:
            return False
        if disposition not in {"commit", "rollback"}:
            return False
        tabs = self.panel.sql_tabs
        target_index = tabs.currentIndex() if index is None else int(index)
        tab = tabs.widget(target_index) if target_index >= 0 else None
        state = self._states_by_tab.get(id(tab)) if tab is not None else None
        if state is None or state.busy or state.session.state not in {
            ManualSessionState.DIRTY, ManualSessionState.UNKNOWN
        }:
            return False
        if state.session.state is ManualSessionState.UNKNOWN and disposition == "commit":
            self._set_status(self._message(
                "提交结果未知，只能显式确认回滚或取消关闭并保留会话。",
                "Commit outcome is unknown; explicitly confirm rollback or cancel and keep the session open.",
            ), state=state)
            return False
        state.remove_tab_on_close = True
        state.last_error = "TRANSACTION_OUTCOME_UNKNOWN" if state.session.state is ManualSessionState.UNKNOWN else ""
        self._close_state(state, disposition=disposition)
        return True

    def _close_state(self, state: _TabState, *, disposition: str) -> None:
        state.pending_close = True
        state.request_id = uuid.uuid4().hex
        state.busy = True
        self._watch_future(state, "close", state.session.close(disposition=disposition), state.request_id)

    def manual_shutdown_cleanup(self, disposition: str | None = None) -> ManualShutdownStatus:
        """Preflight/poll or explicitly close every per-tab manual owner.

        ``None``/``preflight`` is read-only until a shutdown action has been
        started. Callers can preflight all panels before closing any clean
        session. ``close`` is valid only when every session is CLEAN or
        DISCONNECTED; ``commit``/``rollback`` is the user's explicit choice
        for every dirty session. Once an action starts, ``None`` only polls it.
        """
        action = None if disposition is None else str(disposition).strip().casefold()
        if action not in {None, "preflight", "close", "commit", "rollback", "cancel"}:
            raise ValueError("unsupported manual shutdown disposition")

        if action == "cancel":
            if self._shutdown_action is not None or any(state.pending_close for state in self._all_session_states()):
                return ManualShutdownStatus.PENDING
            self._shutdown_action = None
            return ManualShutdownStatus.CANCELLED

        if self._shutdown_action is not None:
            if action in {"close", "commit", "rollback"} and action != self._shutdown_action:
                return ManualShutdownStatus.PENDING
            return self._poll_shutdown_action()

        if self._export_jobs:
            return ManualShutdownStatus.PENDING

        import_status = self._import_host.cleanup(None)
        if import_status == "pending":
            return ManualShutdownStatus.PENDING

        states = self._all_session_states()
        statuses = [(state, state.session.poll_cleanup()) for state in states]
        if any(
            state.busy or status.busy or status.queued_operations
            or (state in self._retired_states and status.owner_alive)
            for state, status in statuses
        ):
            return ManualShutdownStatus.PENDING

        pending_table_states = [
            state for state in states
            if state.table_model is not None and state.table_model.has_staged_changes
        ]
        transaction_states = {status.state for _, status in statuses if not status.cleanup_complete}
        has_unresolved = bool(transaction_states & {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN})
        if action in {None, "preflight"} and (
            has_unresolved or pending_table_states or import_status == "decision_required"
        ):
            return ManualShutdownStatus.DECISION_REQUIRED
        if action == "close" and import_status == "decision_required":
            return ManualShutdownStatus.DECISION_REQUIRED
        if action == "close" and has_unresolved:
            return ManualShutdownStatus.DECISION_REQUIRED
        if action == "commit" and (
            ManualSessionState.UNKNOWN in transaction_states or pending_table_states
            or not self._import_host.snapshot().can_commit
        ):
            # A local overlay is not part of the transaction, and an UNKNOWN
            # owner outcome forbids any group commit. Resolve either condition
            # before opening a discard prompt or queuing any owner action.
            return ManualShutdownStatus.DECISION_REQUIRED
        if action == "rollback" and any(
            owner.failed_cleanup for owner in self._import_host.snapshot().owners
        ):
            return ManualShutdownStatus.DECISION_REQUIRED

        if pending_table_states:
            # Preflight is a read-only global decision seam. The model overlay
            # has not reached the owner or database, so only an explicit close,
            # commit, or rollback request may offer to discard it.
            from PyQt6.QtWidgets import QMessageBox

            change_count = sum(state.table_model.staged_count for state in pending_table_states)
            answer = QMessageBox.question(
                self.panel,
                self._message("关闭前处理本地变更", "Resolve local changes before closing"),
                self._message(
                    f"有 {change_count} 项表格变更仍仅在本地，尚未发送到数据库。放弃这些本地变更并继续关闭？不会自动应用。",
                    f"{change_count} table change(s) remain local and have not been sent to the database. Discard them and continue closing? They will not be applied automatically.",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return ManualShutdownStatus.CANCELLED
            for state in pending_table_states:
                state.table_model.discard_staged()

        if all(status.cleanup_complete for _, status in statuses) and import_status == "ready":
            self._forget_completed_sessions(statuses)
            return ManualShutdownStatus.READY
        if action in {None, "preflight"}:
            return ManualShutdownStatus.READY_TO_CLOSE

        # The complete owner set was inspected before the first close is
        # queued, preventing a mixed CLEAN/DIRTY/UNKNOWN partial commit.
        self._shutdown_action = action
        import_action_status = self._import_host.cleanup(action)
        if import_action_status in {"pending", "decision_required"}:
            if import_action_status == "decision_required":
                self._shutdown_action = None
                return ManualShutdownStatus.DECISION_REQUIRED
            # The import host has started only after its complete owner set
            # passed preflight; manual owners remain gated and retained here.
        for state, status in statuses:
            if status.cleanup_complete:
                self._forget_state(state)
                continue
            current_state = status.state
            if current_state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}:
                close_disposition = action
            else:
                close_disposition = ""
            self._close_state(state, disposition=close_disposition)
        return self._poll_shutdown_action()

    def manual_shutdown_snapshot(self) -> ManualShutdownSnapshot:
        """Return states needed by a global decision without exposing payloads."""
        entries: list[ManualShutdownSessionSnapshot] = []
        can_commit = True
        for state in self._all_session_states():
            status = state.session.poll_cleanup()
            busy = bool(state.busy or status.busy or status.queued_operations or self._has_export_for_state(state))
            cleanup_complete = bool(status.cleanup_complete and not status.owner_alive)
            pending_local_changes = (
                int(state.table_model.staged_count)
                if state.table_model is not None and state.table_model.has_staged_changes else 0
            )
            entries.append(ManualShutdownSessionSnapshot(
                tab_id=str(state.target.tab_id),
                generation=int(state.generation),
                state=str(status.state.value),
                busy=busy,
                cleanup_complete=cleanup_complete,
                pending_local_changes=pending_local_changes,
                parent_sql_tab_id=str(state.target.tab_id),
                parent_sql_generation=int(state.generation),
                owner_kind="manual",
            ))
            if busy or status.state is ManualSessionState.UNKNOWN or pending_local_changes:
                can_commit = False
        import_snapshot = self._import_host.snapshot()
        for owner in import_snapshot.owners:
            entries.append(ManualShutdownSessionSnapshot(
                tab_id=owner.import_tab_id,
                generation=owner.generation,
                state=owner.state,
                busy=owner.busy,
                cleanup_complete=owner.cleanup_complete,
                pending_local_changes=0,
                parent_sql_tab_id=owner.parent_sql_tab_id,
                parent_sql_generation=owner.parent_sql_generation,
                owner_kind="import",
            ))
        can_commit = can_commit and import_snapshot.can_commit
        return ManualShutdownSnapshot(tuple(entries), can_commit)

    def manual_shutdown_gate(self, enabled: bool) -> bool:
        """Freeze new per-tab work while preserving controls for existing owners."""
        self._shutdown_gated = bool(enabled)
        self._import_host.set_shutdown_gate(self._shutdown_gated)
        table_editor = getattr(self, "table_editor", None)
        if table_editor is not None:
            table_editor.set_shutdown_gated(self._shutdown_gated)
        combo = getattr(self.panel, "conn_combo", None)
        new_tab = getattr(self.panel, "new_tab_btn", None)
        if combo is not None:
            combo.setEnabled(not self._shutdown_gated)
        if new_tab is not None:
            new_tab.setEnabled(not self._shutdown_gated)
        self._update_actions(self._states_by_tab.get(id(self.panel._current_tab())))
        self._sync_paging(self._states_by_tab.get(id(self.panel._current_tab())))
        self._refresh_workbench_actions()
        return True

    def open_import_current(self, *_args: Any) -> bool:
        """Open an importer bound to this table tab's captured signed scope."""
        if self._shutdown_gated:
            return False
        state = self._current_state()
        if (
            state is None or state.busy or state.table_scope is None
            or state.table_result is None or state.table_model is None
        ):
            return False
        candidates = candidate_target_columns(state.table_result.metadata)
        if not candidates.can_approve:
            self._update_import_availability(state)
            reason = candidates.blocking_reason_codes[0] if candidates.blocking_reason_codes else "NO_IMPORTABLE_COLUMNS"
            self._set_status(self._import_reason_text(reason), state=state)
            return False
        scope = state.table_scope
        context = ImportTableContext(
            table_result=state.table_result,
            tree_service=scope.service,
            tree_target=scope.tree_target,
            node=scope.node,
            tree_generation=scope.tree_generation,
            choice=state.choice,
            profile_host=self.profile_host,
            connector=self.connector,
            secret_resolver=self.secret_resolver,
            parent_sql_tab_id=state.target.tab_id,
            parent_sql_generation=state.generation,
        )
        controller = self._import_host.open_import(context, getattr(self.panel, "language", "zh"))
        if controller is None:
            return False
        self._set_status(self._message(
            "导入已固定到此 SQL 标签和签名表范围。",
            "Import is bound to this SQL tab and signed table scope.",
        ), state=state)
        self._update_actions(state)
        return True

    def _import_state_changed(self, *_args: Any) -> None:
        state = self._current_state()
        self._update_actions(state)
        self._refresh_workbench_actions()

    def _update_import_availability(self, state: _TabState | None) -> None:
        if state is None or state.table_model is None or state.table_result is None:
            self.table_editor.set_import_availability(False, self._message(
                "先打开一个已签名的表对象。" if state is not None and state.table_scope is None else "正在读取表对象…",
                "Open a signed table object first." if state is not None and state.table_scope is None else "Reading table metadata…",
            ))
            return
        candidates = candidate_target_columns(state.table_result.metadata)
        if candidates.can_approve:
            self.table_editor.set_import_availability(True, "")
            return
        reason = candidates.blocking_reason_codes[0] if candidates.blocking_reason_codes else "NO_IMPORTABLE_COLUMNS"
        self.table_editor.set_import_availability(False, self._import_reason_text(reason))

    def _import_reason_text(self, code: str) -> str:
        messages = {
            "TABLE_NOT_WRITABLE": ("所选对象为视图或只读对象，不能导入。", "The selected object is a view or read-only; import is unavailable."),
            "UNSUPPORTED_TARGET_TYPE": ("必填字段的数据类型不受支持，不能安全导入。", "A required target type is unsupported, so import is unavailable."),
            "REQUIRED_COLUMN_UNMAPPED": ("有必填字段无法映射，不能导入。", "A required field cannot be mapped, so import is unavailable."),
            "NO_IMPORTABLE_COLUMNS": ("没有可安全导入的目标字段。", "No supported target columns are available for import."),
        }
        zh, en = messages.get(code, ("当前表范围不支持导入。", "The current table scope does not support import."))
        return self._message(zh, en)

    def _all_session_states(self) -> tuple[_TabState, ...]:
        values = [*self._states_by_tab.values(), *self._retired_states]
        unique: dict[tuple[str, int], _TabState] = {}
        for state in values:
            unique[(state.target.tab_id, state.generation)] = state
        return tuple(unique.values())

    def _poll_shutdown_action(self) -> ManualShutdownStatus:
        if self._export_jobs:
            return ManualShutdownStatus.PENDING
        import_status = self._import_host.cleanup(None)
        if import_status == "pending":
            return ManualShutdownStatus.PENDING
        if import_status == "decision_required":
            self._shutdown_action = None
            return ManualShutdownStatus.DECISION_REQUIRED
        statuses = [(state, state.session.poll_cleanup()) for state in self._all_session_states()]
        if all(status.cleanup_complete for _, status in statuses) and import_status == "ready":
            self._forget_completed_sessions(statuses)
            self._shutdown_action = None
            return ManualShutdownStatus.READY
        if any(state.busy or status.busy or status.queued_operations or status.owner_alive for state, status in statuses):
            return ManualShutdownStatus.PENDING
        if any(status.state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN} for _, status in statuses):
            self._shutdown_action = None
            return ManualShutdownStatus.DECISION_REQUIRED
        if any(not status.cleanup_complete for _, status in statuses):
            self._shutdown_action = None
            return ManualShutdownStatus.DECISION_REQUIRED
        return ManualShutdownStatus.PENDING

    def _forget_completed_sessions(self, statuses: list[tuple[_TabState, Any]]) -> None:
        for state, status in statuses:
            if status.cleanup_complete:
                self._forget_state(state)

    def _finish_tab_close(self, state: _TabState) -> None:
        tab = state.tab
        tabs = self.panel.sql_tabs
        index = tabs.indexOf(tab)
        unknown = state.last_error == "TRANSACTION_OUTCOME_UNKNOWN"
        self._forget_state(state, retain_until_cleanup=True)
        if index >= 0 and tabs.count() > 1:
            tabs.removeTab(index)
            tab.deleteLater()
            self._visible_tab_changed(tabs.currentIndex())
        elif index >= 0:
            tab.editor.clear()
            setattr(tab, "_manual_tab_id", "")
            setattr(tab, "_manual_generation", state.generation + 1)
            self._ensure_tab_state(tab)
            self._visible_tab_changed(tabs.currentIndex())
        if unknown:
            self._set_status(self._message(
                "事务结果仍未知；已按明确请求发送回滚并释放旧连接，不表示写入已确认撤销。",
                "The transaction outcome remains unknown. A requested rollback was sent and the old connection released; this does not confirm that writes were undone.",
            ))

    def _forget_state(self, state: _TabState, *, retain_until_cleanup: bool = False) -> None:
        self._states_by_tab.pop(id(state.tab), None)
        if retain_until_cleanup:
            if state not in self._retired_states:
                self._retired_states.append(state)
        else:
            try:
                self._retired_states.remove(state)
            except ValueError:
                pass
        try:
            if getattr(state.tab, "manual_session", None) is state.session:
                delattr(state.tab, "manual_session")
            if getattr(state.tab, "manual_target", None) == state.target:
                delattr(state.tab, "manual_target")
        except (AttributeError, RuntimeError):
            pass

    def _current_state(self) -> _TabState | None:
        return self._ensure_tab_state(self.panel._current_tab())

    def _clear_visible_results(self) -> None:
        self.result_model.clear()

    def _sync_paging(self, state: _TabState | None) -> None:
        if state is None or self._current_tab_is(state):
            self._sync_query_page_size(state)
        page = state.last_page if state is not None else None
        next_button = getattr(self.panel, "next_btn", None)
        if next_button is not None:
            next_button.setEnabled(bool(
                page and page.has_more and not state.busy and not self._shutdown_gated
                and self._selected_result_can_continue(state)
                and not (state.table_model is not None and state.table_model.has_staged_changes)
            ))
        all_button = getattr(self.panel, "all_btn", None)
        if all_button is not None:
            all_button.setEnabled(False)
        if state is not None and state.table_mode_active and state.table_model is not None and self._current_tab_is(state):
            self.table_editor.set_operation_state(
                has_more=bool(page and page.has_more),
                busy=state.busy,
            )

    def _update_actions(self, state: _TabState | None) -> None:
        if state is not None and state.table_model is not None:
            state.table_model.set_editing_enabled(not state.busy and not self._shutdown_gated)
        current = state if state is not None and self._current_tab_is(state) else None
        self._sync_query_page_size(self._states_by_tab.get(id(self.panel._current_tab())))
        run_all = getattr(self, "run_all_btn", None)
        if run_all is not None:
            tab = self.panel._current_tab()
            draft_ready = tab is not None and getattr(tab, "_history_target_locked", False) and getattr(tab, "_history_target_valid", False)
            current_ready = current is not None and not current.busy and not (current.table_model is not None and current.table_model.has_staged_changes)
            run_all.setEnabled(not self._shutdown_gated and bool(draft_ready or current_ready))
        self.commit_btn.setEnabled(bool(
            current is not None and not current.busy
            and not self._shutdown_gated
            and current.session.state is ManualSessionState.DIRTY
        ))
        self.rollback_btn.setEnabled(bool(
            current is not None and not current.busy
            and not self._shutdown_gated
            and current.session.state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}
        ))
        self.stop_btn.setEnabled(bool(
            (current is not None and current.busy)
            or self._export_jobs
            or self._active_full_export_states()
        ))
        # V1.1：事务按钮只在有未提交事务时出现，「停止」只在执行/导出中出现，工具条平时只留常用项
        self.commit_btn.setVisible(self.commit_btn.isEnabled())
        self.rollback_btn.setVisible(self.rollback_btn.isEnabled())
        self.stop_btn.setVisible(self.stop_btn.isEnabled())
        run_button = getattr(self.panel, "run_btn", None)
        if run_button is not None:
            tab = self.panel._current_tab()
            if tab is not None and getattr(tab, "_history_target_locked", False):
                run_button.setEnabled(
                    bool(getattr(tab, "_history_target_valid", False))
                    and not self._shutdown_gated
                )
            else:
                run_button.setEnabled(
                    current is not None and not current.busy and not self._shutdown_gated
                    and not (current.table_model is not None and current.table_model.has_staged_changes)
                )
        table_editor = getattr(self, "table_editor", None)
        if table_editor is None:
            # 安装过程中（事务按钮先于表格编辑器创建）会提前刷新一次动作状态
            return
        if current is not None and current.table_mode_active and current.table_model is not None:
            table_editor.set_operation_state(
                has_more=bool(current.last_page and current.last_page.has_more),
                busy=current.busy,
            )
            table_editor.set_shutdown_gated(self._shutdown_gated)
            self._update_import_availability(current)
        else:
            table_editor.set_import_availability(False, self._message(
                "先打开一个已签名的表对象。", "Open a signed table object first.",
            ))

    def _has_export_for_state(self, state: _TabState) -> bool:
        return any(context.state is state for context, _worker in self._export_jobs.values())

    def _active_full_export_states(self) -> tuple[_TabState, ...]:
        return tuple(
            state for state in self._all_session_states()
            if state.busy and state.active_operation == "export_full"
        )

    def _can_open_export_dialog(self, state: _TabState | None) -> bool:
        return bool(
            state is not None
            and state.pages
            and not state.busy
            and not state.pending_close
            and not self._shutdown_gated
            and not self._export_jobs
            and not self._active_full_export_states()
            and not (state.table_model is not None and state.table_model.has_staged_changes)
            and not (state.table_model is not None and state.table_model.page_stale)
        )

    def _capture_export_context(self, state: _TabState) -> _ExportContext | None:
        pages = tuple(state.pages)
        if not pages or tuple(self.result_model.pages) != pages:
            return None
        if (
            getattr(state.session, "target", None) != state.target
            or state.target.connection_id != state.choice.connection_id
            or state.target.profile_revision != state.choice.revision
        ):
            return None
        first = pages[0]
        target = ExportTarget(first.query_id, first.tab_id, first.generation)
        if target.tab_id != state.target.tab_id or target.generation != state.generation:
            return None
        if any(
            (page.query_id, page.tab_id, page.generation) != (target.query_id, target.tab_id, target.generation)
            for page in pages
        ):
            return None

        selected_snapshot = None
        selection = self.result_view.selectionModel()
        selected_rows = sorted({index.row() for index in selection.selectedRows()}) if selection is not None else []
        if selected_rows:
            rows = tuple(
                tuple(
                    self.result_model.data(
                        self.result_model.index(row_index, column),
                        Qt.ItemDataRole.UserRole,
                    )
                    for column in range(self.result_model.columnCount())
                )
                for row_index in selected_rows
            )
            selected_snapshot = ExportSnapshot(
                target,
                tuple(column.name for column in first.columns),
                rows,
            )

        profile = getattr(state.tab, "conn_item", None)
        connection_name = (
            str(profile.get("name") or "").strip()
            if isinstance(profile, dict) else ""
        ) or state.choice.connection_id
        full_query_available, full_preview = self._full_export_availability(state, target)
        return _ExportContext(
            state=state,
            generation=state.generation,
            pages=pages,
            target=target,
            selected_snapshot=selected_snapshot,
            connection_name=connection_name,
            database=state.target.database,
            schema=state.target.schema,
            full_query_available=full_query_available,
            full_preview=full_preview,
        )

    def _full_export_availability(self, state: _TabState, target: ExportTarget) -> tuple[bool, str]:
        try:
            if (
                getattr(state.session, "target", None) != state.target
                or state.target.connection_id != state.choice.connection_id
                or state.target.profile_revision != state.choice.revision
            ):
                return False, self._message(
                    "当前结果暂不能完整导出。",
                    "The current result cannot be exported in full.",
                )
            status = state.session.result_source_status
            status_target = (str(status.query_id), str(status.tab_id), int(status.generation))
            requested_target = (target.query_id, target.tab_id, target.generation)
            source_state = str(getattr(status.state, "value", status.state)).upper()
            available = bool(
                status_target == requested_target
                and status.can_export_full
                and source_state in {"AVAILABLE", "BUDGET_LIMITED", "COMPLETE"}
            )
            if not available:
                return False, self._message(
                    "当前结果暂不能完整导出。",
                    "The current result cannot be exported in full.",
                )
            pages = max(0, int(status.page_count))
            rows = max(0, int(status.retained_rows))
            page_unit = "page" if pages == 1 else "pages"
            row_unit = "row" if rows == 1 else "rows"
            if source_state == "COMPLETE":
                return True, self._message(
                    f"完整查询：共 {pages} 页、{rows} 行。",
                    f"Full query: {pages} {page_unit}, {rows} {row_unit}.",
                )
            return True, self._message(
                f"完整查询：已加载 {pages} 页、{rows} 行；将继续读取当前结果。",
                f"Full query: {pages} {page_unit} and {rows} loaded {row_unit}; the current result will continue loading.",
            )
        except (AttributeError, TypeError, ValueError):
            return False, self._message(
                "当前结果暂不能完整导出。",
                "The current result cannot be exported in full.",
            )

    def export_current(self, *_args: Any) -> None:
        if self._shutdown_gated:
            return
        state = self._current_state()
        if not self._can_open_export_dialog(state):
            self._set_status(self._message(
                "当前没有可导出的已加载结果。",
                "There is no loaded result available to export.",
            ))
            return
        context = self._capture_export_context(state)
        if context is None:
            self._set_status(self._message(
                "结果已变化，请等待语句完成后重新打开导出窗口。",
                "The result changed; wait for the statement to finish and reopen export.",
            ), state=state)
            return
        dialog = ResultExportDialog(
            self.panel,
            target=context.target,
            pages=context.pages,
            selected_snapshot=context.selected_snapshot,
            connection_name=context.connection_name,
            database=context.database,
            schema=context.schema,
            language=getattr(self.panel, "language", "zh"),
            full_query_available=context.full_query_available,
            full_preview=context.full_preview,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.plan is None:
            return
        plan = dialog.plan
        if not self._export_context_is_current(context, plan):
            self._set_status(self._message(
                "结果已变化，未启动导出。请重新打开导出窗口。",
                "The result changed; export was not started. Reopen the export window.",
            ), state=state)
            return
        if self._export_jobs:
            self._set_status(self._message("已有导出正在进行。", "An export is already running."), state=state)
            return

        if plan.scope is ExportScope.FULL_QUERY:
            state.request_id = uuid.uuid4().hex
            state.busy = True
            state.export_status = self._message("正在导出完整查询…", "Exporting full query…")
            self._watch_future(
                state,
                "export_full",
                state.session.export_full_query(
                    plan.destination,
                    plan.format,
                    plan.target,
                    overwrite=plan.overwrite,
                ),
                state.request_id,
            )
            if self._current_state() is state:
                self._set_status(state.export_status, state=state)
            self._update_actions(self._current_state())
            self._refresh_workbench_actions()
            return

        request_id = uuid.uuid4().hex
        worker = SnapshotExportWorker(
            request_id,
            plan,
            writer=self._export_writer,
            cancel_event=threading.Event(),
            parent=self,
        )
        self._export_jobs[request_id] = (context, worker)
        worker.finished.connect(self._on_export_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.start()
        state.export_status = self._message("正在导出…", "Exporting…")
        if self._current_state() is state:
            self._set_status(state.export_status, state=state)
        self._update_actions(self._current_state())
        self._refresh_workbench_actions()

    def _export_context_is_current(self, context: _ExportContext, plan: ExportRequestPlan) -> bool:
        state = context.state
        retained = any(candidate is state for candidate in self._all_session_states())
        if (
            not retained
            or state.generation != context.generation
            or state.target.tab_id != context.target.tab_id
            or state.target.generation != context.target.generation
            or state.pages is not context.pages
            or state.busy
            or state.pending_close
            or self._shutdown_gated
            or plan.target != context.target
            or tuple(self.result_model.pages) != context.pages
        ):
            return False
        cleanup = state.session.poll_cleanup()
        if cleanup.busy or cleanup.queued_operations:
            return False
        if plan.scope is ExportScope.FULL_QUERY:
            return plan.snapshot is None and self._full_export_availability(state, context.target)[0]
        if plan.snapshot is None:
            return False
        if plan.scope is ExportScope.SELECTED:
            return plan.snapshot == context.selected_snapshot and bool(plan.snapshot.rows)
        if plan.scope is ExportScope.CURRENT_PAGE:
            return self._snapshot_matches_page(plan.snapshot, context.pages)
        return False

    @staticmethod
    def _snapshot_matches_page(snapshot: ExportSnapshot, pages: tuple[ManualQueryPage, ...]) -> bool:
        return any(
            page.page_number == snapshot.page_number
            and tuple(column.name for column in page.columns) == snapshot.columns
            and page.rows == snapshot.rows
            for page in pages
        )

    @pyqtSlot()
    def _on_export_worker_finished(self) -> None:
        worker = self.sender()
        request_id = getattr(worker, "request_id", "")
        if request_id:
            self._finalize_export_worker(str(request_id))

    def _finalize_export_worker(self, request_id: str) -> None:
        job = self._export_jobs.get(request_id)
        if job is None:
            return
        context, worker = job
        if not worker.isFinished():
            QTimer.singleShot(25, lambda key=request_id: self._finalize_export_worker(key))
            return
        self._export_jobs.pop(request_id, None)
        state = context.state
        same_result = state.generation == context.generation and state.pages is context.pages
        if same_result:
            if worker.receipt is not None:
                state.export_status = self._message(
                    f"已导出 {worker.receipt.rows} 行：{worker.receipt.path.name}",
                    f"Exported {worker.receipt.rows} rows: {worker.receipt.path.name}",
                )
            elif worker.error_code == "CANCELLED":
                state.export_status = self._message("导出已取消。", "Export cancelled.")
            else:
                state.export_status = self._export_error_text(worker.error_code or "EXPORT_FAILED")
            if self._current_state() is state and not state.busy:
                self._set_status(state.export_status, state=state)
        worker.deleteLater()
        self._update_actions(self._current_state())
        self._refresh_workbench_actions()

    def _export_error_text(self, code: str) -> str:
        messages = {
            "DESTINATION_EXISTS": ("目标文件已存在。", "The destination file already exists."),
            "DESTINATION_UNAVAILABLE": ("无法使用所选保存位置。", "The selected destination is unavailable."),
            "XLSX_UNAVAILABLE": ("当前环境无法创建 Excel 文件。", "Excel export is unavailable in this environment."),
            "CANCELLED": ("导出已取消。", "Export cancelled."),
        }
        zh, en = messages.get(code, ("导出失败，请检查保存位置或数据格式。", "Export failed; check the destination and result values."))
        return self._message(zh, en)

    def _current_tab_is(self, state: _TabState) -> bool:
        return self.panel._current_tab() is state.tab

    def _set_status(self, text: str, *, state: _TabState | None = None) -> None:
        if state is not None and not self._current_tab_is(state):
            return
        status = getattr(self.panel, "result_status", None)
        if status is not None:
            status.setText(str(text))

    def _state_text(self, state: ManualSessionState) -> str:
        labels = {
            ManualSessionState.DISCONNECTED: self._message("未连接；首次执行时建立独立手动会话", "Disconnected; first execution opens a dedicated manual session"),
            ManualSessionState.CLEAN: self._message("事务 CLEAN", "Transaction CLEAN"),
            ManualSessionState.DIRTY: self._message("事务 DIRTY · 需要显式提交或回滚", "Transaction DIRTY · commit or rollback explicitly"),
            ManualSessionState.UNKNOWN: self._message("事务 UNKNOWN · 不会自动重试提交", "Transaction UNKNOWN · commit will not be retried"),
            ManualSessionState.CANCEL_PENDING: self._message("取消等待 owner 完成当前驱动调用", "Cancellation waits for the owner call to return"),
            ManualSessionState.COMMITTING: self._message("正在提交事务…", "Committing transaction…"),
            ManualSessionState.ROLLING_BACK: self._message("正在回滚事务…", "Rolling back transaction…"),
        }
        return labels.get(state, str(state.value))

    def _safe_error(self, error: BaseException) -> str:
        code = getattr(error, "code", "MANUAL_SESSION_FAILED")
        return self._message(f"手动会话不可用：{code}", f"Manual session unavailable: {code}")

    def _message(self, zh: str, en: str) -> str:
        return zh if getattr(self.panel, "language", "zh") == "zh" else en

    def _execution_sql(self, editor: Any) -> str:
        selected, cursor_position = editor.selected_or_all()
        state = self._current_state()
        dialect = state.choice.provider if state is not None else "generic"
        if selected:
            scan_sql(selected, dialect)
            return selected.strip()
        return statement_at(editor.toPlainText(), cursor_position, dialect)


def _layout_containing(layout: Any, widget: Any) -> Any | None:
    if layout is None:
        return None
    try:
        if layout.indexOf(widget) >= 0:
            return layout
    except RuntimeError:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        child = item.layout() if item is not None else None
        found = _layout_containing(child, widget)
        if found is not None:
            return found
    return None


def _looks_like_procedure_block(sql: str) -> bool:
    try:
        return has_procedural_block(scan_sql(sql).code)
    except SqlScriptError:
        return True


__all__ = ["ManualQueryHost", "ManualShutdownStatus"]
