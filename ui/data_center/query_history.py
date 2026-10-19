"""Reusable, asynchronous browser for non-secret data-center query history.

The view has no panel or active-session dependency.  It receives a public
``WorkspaceStore`` instance and sends an immutable captured ``HistoryRecord``
back to its caller when the user asks to open a new draft.  Store operations
run serially on one owned Qt thread; closing the controller never blocks the
GUI or releases that owner while a store call is still running.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import threading
from typing import Any

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from tools.data_center.workspace_store import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    HistoryRecord,
    WorkspaceSnapshot,
    WorkspaceStoreError,
)


MAX_HISTORY_PENDING_OPERATIONS = 16

_SAFE_STORE_ERROR_CODES = frozenset(
    {
        "CREDENTIAL_TEXT_REJECTED",
        "DRAFT_LIMIT_REACHED",
        "FAVORITE_LIMIT_REACHED",
        "HISTORY_LIMIT_REACHED",
        "HISTORY_NOT_FOUND",
        "INVALID_ID",
        "INVALID_LAYOUT",
        "INVALID_LIMIT",
        "INVALID_PATH",
        "INVALID_RECORD",
        "INVALID_TIMESTAMP",
        "INVALID_TEXT",
        "INVALID_TEXT_KIND",
        "INVALID_TARGET",
        "LAYOUT_LIMIT_REACHED",
        "SCHEMA_MIGRATION_UNSUPPORTED",
        "SCHEMA_MALFORMED",
        "SCHEMA_OUTCOME_UNKNOWN",
        "SCHEMA_VERSION_UNSUPPORTED",
        "SEARCH_TOO_LONG",
        "STORAGE_FAILED",
        "STORAGE_OUTCOME_UNKNOWN",
        "STORE_CLOSED",
        "STORE_LIMIT_REACHED",
        "STORE_RECORD_INVALID",
        "TEXT_TOO_LARGE",
    }
)

_ACTIVE_CONTROLLERS: set["QueryHistoryController"] = set()


@dataclass(frozen=True, slots=True)
class QueryHistoryResult:
    """One immutable search/favorites response tied to a view generation."""

    request_id: str
    generation: int
    view: str
    records: tuple[HistoryRecord, ...]


@dataclass(frozen=True, slots=True)
class QueryHistoryOperation:
    """Safe completion metadata for one mutation; it contains no query text."""

    request_id: str
    operation: str
    affected_count: int = 0
    history_id: str = ""
    favorite: bool | None = None


@dataclass(frozen=True, slots=True)
class QueryHistoryFailure:
    """Safe error summary with no exception text, profile, or SQL body."""

    request_id: str
    operation: str
    generation: int
    code: str


@dataclass(frozen=True, slots=True)
class QueryHistoryCleanupResult:
    """Completion is reported only after the store-owner QThread has exited."""

    completed: bool
    pending_operations: int = 0
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class _StoreRequest:
    request_id: str
    operation: str
    generation: int = 0
    view: str = "history"
    query: str = ""
    connection_id: str | None = None
    limit: int = DEFAULT_SEARCH_LIMIT
    history_id: str = ""
    favorite: bool = False
    include_favorites: bool = False
    record: HistoryRecord | None = None
    workspace: WorkspaceSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _StoreCompletion:
    request_id: str
    operation: str
    generation: int = 0
    view: str = "history"
    records: tuple[HistoryRecord, ...] = ()
    affected_count: int = 0
    history_id: str = ""
    favorite: bool | None = None
    error_code: str = ""
    workspace: WorkspaceSnapshot | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceRestoreResult:
    request_id: str
    snapshot: WorkspaceSnapshot | None


class _WorkspaceStoreWorker(QObject):
    """Execute only public WorkspaceStore calls on its Qt owner thread."""

    completed = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(self, store: Any) -> None:
        super().__init__()
        self._store = store

    @pyqtSlot(object)
    def execute(self, request: _StoreRequest) -> None:
        if not isinstance(request, _StoreRequest):
            return
        try:
            if request.operation == "search":
                records = self._store.search_history(
                    request.query,
                    connection_id=request.connection_id,
                    limit=request.limit,
                )
                completion = _StoreCompletion(
                    request.request_id,
                    request.operation,
                    request.generation,
                    request.view,
                    tuple(records),
                )
            elif request.operation == "favorites":
                records = self._store.list_favorites(limit=request.limit)
                completion = _StoreCompletion(
                    request.request_id,
                    request.operation,
                    request.generation,
                    request.view,
                    tuple(records),
                )
            elif request.operation == "favorite":
                self._store.set_favorite(request.history_id, request.favorite)
                completion = _StoreCompletion(
                    request.request_id,
                    request.operation,
                    history_id=request.history_id,
                    favorite=request.favorite,
                )
            elif request.operation in {"clear_history", "clear_all"}:
                count = self._store.clear_history(
                    include_favorites=request.include_favorites
                )
                completion = _StoreCompletion(
                    request.request_id,
                    request.operation,
                    affected_count=int(count),
                )
            elif request.operation == "save_history" and request.record is not None:
                self._store.save_history(request.record)
                completion = _StoreCompletion(request.request_id, request.operation)
            elif request.operation == "save_workspace" and request.workspace is not None:
                self._store.save_workspace(request.workspace)
                completion = _StoreCompletion(request.request_id, request.operation)
            elif request.operation == "load_workspace":
                completion = _StoreCompletion(request.request_id, request.operation, workspace=self._store.load_workspace())
            else:
                completion = _StoreCompletion(
                    request.request_id,
                    request.operation,
                    generation=request.generation,
                    error_code="INVALID_RECORD",
                )
        except Exception as error:
            completion = _StoreCompletion(
                request.request_id,
                request.operation,
                request.generation,
                request.view,
                history_id=request.history_id,
                favorite=request.favorite if request.operation == "favorite" else None,
                error_code=self._safe_error_code(error),
            )
        self.completed.emit(completion)

    @pyqtSlot()
    def stop(self) -> None:
        self.stopped.emit()

    @staticmethod
    def _safe_error_code(error: BaseException) -> str:
        if isinstance(error, WorkspaceStoreError):
            code = error.code
            if code in _SAFE_STORE_ERROR_CODES:
                return code
        return "STORE_FAILED"


class QueryHistoryController(QObject):
    """Serialize bounded WorkspaceStore work and discard stale view results."""

    records_ready = pyqtSignal(object)
    operation_completed = pyqtSignal(object)
    request_failed = pyqtSignal(object)
    shutdown_pending = pyqtSignal()
    cleanup_finished = pyqtSignal(object)
    workspace_ready = pyqtSignal(object)

    _dispatch = pyqtSignal(object)
    _stop_requested = pyqtSignal()

    def __init__(self, store: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        required = (
            "search_history",
            "list_favorites",
            "set_favorite",
            "clear_history",
            "save_history",
        )
        if store is None or any(not callable(getattr(store, name, None)) for name in required):
            raise TypeError("store must expose the public WorkspaceStore history API")

        self._store = store
        self._sequence = 0
        self._pending: dict[str, _StoreRequest] = {}
        self._view_generation = 0
        self._view = "history"
        self._query = ""
        self._connection_id: str | None = None
        self._limit = DEFAULT_SEARCH_LIMIT
        self._closing = False
        self._cleanup_future: Future[QueryHistoryCleanupResult] | None = None

        self._thread = QThread(self)
        self._thread.setObjectName("data-center-query-history-store-owner")
        self._worker = _WorkspaceStoreWorker(store)
        self._worker.moveToThread(self._thread)
        self._dispatch.connect(
            self._worker.execute, Qt.ConnectionType.QueuedConnection
        )
        self._stop_requested.connect(
            self._worker.stop, Qt.ConnectionType.QueuedConnection
        )
        self._worker.completed.connect(
            self._on_store_completion, Qt.ConnectionType.QueuedConnection
        )
        self._worker.stopped.connect(
            self._thread.quit, Qt.ConnectionType.DirectConnection
        )
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(
            self._on_thread_finished, Qt.ConnectionType.QueuedConnection
        )

        # Keep the Qt owner and injected store alive even if the host releases
        # its reference during a blocked SQLite operation.
        _ACTIVE_CONTROLLERS.add(self)
        self._thread.start()

    @property
    def is_closing(self) -> bool:
        return self._closing

    @property
    def pending_operations(self) -> int:
        return len(self._pending)

    @property
    def current_generation(self) -> int:
        return self._view_generation

    def search_history(
        self,
        query: str = "",
        *,
        connection_id: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> str | None:
        self._assert_qt_thread()
        generation = self._view_generation + 1
        request = self._make_request(
            "search",
            generation=generation,
            view="history",
            query=query,
            connection_id=connection_id,
            limit=limit,
        )
        request_id = self._enqueue(request)
        if request_id is not None:
            self._view_generation = generation
            self._view = "history"
            self._query = query
            self._connection_id = connection_id
            self._limit = limit
        return request_id

    def show_favorites(self, *, limit: int = DEFAULT_SEARCH_LIMIT) -> str | None:
        self._assert_qt_thread()
        generation = self._view_generation + 1
        request = self._make_request(
            "favorites", generation=generation, view="favorites", limit=limit
        )
        request_id = self._enqueue(request)
        if request_id is not None:
            self._view_generation = generation
            self._view = "favorites"
            self._limit = limit
        return request_id

    def set_favorite(self, history_id: str, favorite: bool) -> str | None:
        self._assert_qt_thread()
        return self._enqueue(
            self._make_request(
                "favorite", history_id=history_id, favorite=favorite
            )
        )

    def clear_history(self, *, confirmed: bool = False) -> str | None:
        """Clear ordinary records; favorites are preserved by this action."""

        self._assert_qt_thread()
        if confirmed is not True:
            self._emit_failure("clear_history", 0, "CONFIRMATION_REQUIRED")
            return None
        return self._enqueue(
            self._make_request("clear_history", include_favorites=False)
        )

    def clear_all_history(self, *, confirmed: bool = False) -> str | None:
        """Clear ordinary history and favorites after a separate confirmation."""

        self._assert_qt_thread()
        if confirmed is not True:
            self._emit_failure("clear_all", 0, "CONFIRMATION_REQUIRED")
            return None
        return self._enqueue(
            self._make_request("clear_all", include_favorites=True)
        )

    def save_history(self, record: HistoryRecord) -> str | None:
        """Save exactly the immutable record captured by the caller."""

        self._assert_qt_thread()
        if type(record) is not HistoryRecord:
            self._emit_failure("save_history", 0, "INVALID_RECORD")
            return None
        return self._enqueue(self._make_request("save_history", record=record))

    def save_workspace(self, snapshot: WorkspaceSnapshot) -> str | None:
        self._assert_qt_thread()
        return self._enqueue(self._make_request("save_workspace", workspace=snapshot))

    def load_workspace(self) -> str | None:
        self._assert_qt_thread()
        return self._enqueue(self._make_request("load_workspace"))

    def shutdown(self) -> Future[QueryHistoryCleanupResult]:
        """Request owner shutdown and return immediately with its cleanup future."""

        self._assert_qt_thread()
        if self._cleanup_future is not None:
            return self._cleanup_future
        self._cleanup_future = Future()
        # Cancellation must not stand in for the worker thread's actual exit.
        self._cleanup_future.set_running_or_notify_cancel()
        self._closing = True
        self.shutdown_pending.emit()
        # Same-sender queued signal order places stop after all accepted calls.
        self._stop_requested.emit()
        return self._cleanup_future

    def _make_request(self, operation: str, **values: Any) -> _StoreRequest:
        self._sequence += 1
        return _StoreRequest(request_id=f"history-{self._sequence}", operation=operation, **values)

    def _enqueue(self, request: _StoreRequest) -> str | None:
        if self._closing:
            self._emit_failure(
                request.operation,
                request.generation,
                "CONTROLLER_CLOSED",
                request.request_id,
            )
            return None
        if len(self._pending) >= MAX_HISTORY_PENDING_OPERATIONS:
            self._emit_failure(
                request.operation,
                request.generation,
                "QUEUE_FULL",
                request.request_id,
            )
            return None
        self._pending[request.request_id] = request
        self._dispatch.emit(request)
        return request.request_id

    @pyqtSlot(object)
    def _on_store_completion(self, completion: _StoreCompletion) -> None:
        if not isinstance(completion, _StoreCompletion):
            return
        request = self._pending.pop(completion.request_id, None)
        if request is None or self._closing:
            return
        if completion.error_code:
            if (
                request.operation not in {"search", "favorites"}
                or request.generation == self._view_generation
            ):
                self._emit_failure(
                    request.operation,
                    request.generation,
                    completion.error_code,
                    request.request_id,
                )
            return

        if request.operation == "load_workspace":
            self.workspace_ready.emit(WorkspaceRestoreResult(request.request_id, completion.workspace))
            return

        if request.operation in {"search", "favorites"}:
            if (
                request.generation == self._view_generation
                and request.view == self._view
            ):
                self.records_ready.emit(
                    QueryHistoryResult(
                        request.request_id,
                        request.generation,
                        request.view,
                        completion.records,
                    )
                )
            return

        operation = QueryHistoryOperation(
            request_id=request.request_id,
            operation=request.operation,
            affected_count=completion.affected_count,
            history_id=completion.history_id,
            favorite=completion.favorite,
        )
        self.operation_completed.emit(operation)
        if request.operation in {"favorite", "clear_history", "clear_all", "save_history"}:
            self._refresh_current_view()

    def _refresh_current_view(self) -> None:
        if self._closing:
            return
        if self._view == "favorites":
            self.show_favorites(limit=self._limit)
        else:
            self.search_history(
                self._query,
                connection_id=self._connection_id,
                limit=self._limit,
            )

    def _emit_failure(
        self,
        operation: str,
        generation: int,
        code: str,
        request_id: str = "",
    ) -> None:
        safe_code = code if code in _SAFE_STORE_ERROR_CODES | {
            "CONFIRMATION_REQUIRED", "CONTROLLER_CLOSED", "QUEUE_FULL", "STORE_FAILED"
        } else "STORE_FAILED"
        self.request_failed.emit(
            QueryHistoryFailure(request_id, operation, generation, safe_code)
        )

    @pyqtSlot()
    def _on_thread_finished(self) -> None:
        self._pending.clear()
        result = QueryHistoryCleanupResult(completed=True, pending_operations=0)
        if self._cleanup_future is not None and not self._cleanup_future.done():
            self._cleanup_future.set_result(result)
        self.cleanup_finished.emit(result)
        _ACTIVE_CONTROLLERS.discard(self)

    def _assert_qt_thread(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("QueryHistoryController methods must run on its Qt thread")


class QueryHistoryBrowser(QWidget):
    """Embeddable bilingual history/favorites browser with no session access."""

    open_as_new_draft_requested = pyqtSignal(object)

    def __init__(
        self,
        controller: QueryHistoryController,
        *,
        language: str = "zh",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(controller, QueryHistoryController):
            raise TypeError("controller must be a QueryHistoryController")
        self._font_sync_ready = False
        self.controller = controller
        self._language = "zh" if str(language or "").casefold().startswith("zh") else "en"
        self._records: tuple[HistoryRecord, ...] = ()
        self._selected_record: HistoryRecord | None = None
        self._display_generation = 0
        self._initial_load_requested = False
        self._view = "history"

        self.setObjectName("query-history-browser")
        self.setProperty("themeId", "calm")
        self.setMinimumSize(820, 580)
        self._build_ui()
        self._apply_calm_theme()
        self._set_language_labels()
        self._font_sync_ready = True
        self.setFont(self.font())

        controller.records_ready.connect(self._on_records_ready)
        controller.operation_completed.connect(self._on_operation_completed)
        controller.request_failed.connect(self._on_request_failed)

    def setFont(self, font) -> None:  # noqa: N802 - QWidget API override
        """Keep dense child controls at the font selected for this browser."""

        super().setFont(font)
        if not getattr(self, "_font_sync_ready", False):
            return
        for name in (
            "title_label",
            "query_edit",
            "connection_edit",
            "limit_spin",
            "search_button",
            "history_button",
            "favorites_button",
            "favorite_button",
            "clear_button",
            "clear_all_button",
            "record_list",
            "target_summary",
            "metadata_label",
            "text_preview",
            "restore_button",
            "status_label",
        ):
            control = getattr(self, name, None)
            if isinstance(control, QWidget):
                control.setFont(font)

    def _build_ui(self) -> None:
        from ui.layout_metrics import SPACING_CARD, SPACING_PAGE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING_CARD, SPACING_CARD, SPACING_CARD, SPACING_CARD)
        outer.setSpacing(SPACING_PAGE)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("query-history-title")
        outer.addWidget(self.title_label)

        search_row = QHBoxLayout()
        search_row.setSpacing(SPACING_CARD)
        self.query_edit = QLineEdit(self)
        self.query_edit.setObjectName("query-history-search-text")
        self.query_edit.setClearButtonEnabled(True)
        self.connection_edit = QLineEdit(self)
        self.connection_edit.setObjectName("query-history-connection-filter")
        self.connection_edit.setClearButtonEnabled(True)
        self.limit_spin = QSpinBox(self)
        self.limit_spin.setObjectName("query-history-limit")
        self.limit_spin.setRange(1, MAX_SEARCH_LIMIT)
        self.limit_spin.setValue(DEFAULT_SEARCH_LIMIT)
        self.search_button = QPushButton(self)
        self.search_button.setObjectName("query-history-search-button")
        self.search_button.clicked.connect(self._search)
        self.query_edit.returnPressed.connect(self._search)
        self.connection_edit.returnPressed.connect(self._search)
        search_row.addWidget(self.query_edit, 3)
        search_row.addWidget(self.connection_edit, 2)
        search_row.addWidget(self.limit_spin)
        search_row.addWidget(self.search_button)
        outer.addLayout(search_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(SPACING_CARD)
        self.history_button = QPushButton(self)
        self.history_button.setObjectName("query-history-view-history")
        self.history_button.clicked.connect(self._search)
        self.favorites_button = QPushButton(self)
        self.favorites_button.setObjectName("query-history-view-favorites")
        self.favorites_button.clicked.connect(self._show_favorites)
        self.favorite_button = QPushButton(self)
        self.favorite_button.setObjectName("query-history-toggle-favorite")
        self.favorite_button.setEnabled(False)
        self.favorite_button.clicked.connect(self._toggle_favorite)
        self.clear_button = QPushButton(self)
        self.clear_button.setObjectName("query-history-clear-ordinary")
        self.clear_button.clicked.connect(self._confirm_clear_history)
        self.clear_all_button = QPushButton(self)
        self.clear_all_button.setObjectName("query-history-clear-all")
        self.clear_all_button.clicked.connect(self._confirm_clear_all)
        action_row.addWidget(self.history_button)
        action_row.addWidget(self.favorites_button)
        action_row.addStretch(1)
        action_row.addWidget(self.favorite_button)
        action_row.addWidget(self.clear_button)
        action_row.addWidget(self.clear_all_button)
        outer.addLayout(action_row)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("query-history-splitter")
        self.splitter.setChildrenCollapsible(False)
        self.record_list = QListWidget(self.splitter)
        self.record_list.setObjectName("query-history-record-list")
        self.record_list.setMinimumWidth(260)
        self.record_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.record_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.record_list.currentItemChanged.connect(self._on_current_item_changed)

        detail = QWidget(self.splitter)
        detail.setObjectName("query-history-details")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(SPACING_CARD, SPACING_CARD, SPACING_CARD, SPACING_CARD)
        detail_layout.setSpacing(SPACING_CARD)
        self.target_summary = QPlainTextEdit(detail)
        self.target_summary.setObjectName("query-history-target-summary")
        self.target_summary.setReadOnly(True)
        self.target_summary.setMaximumHeight(132)
        self.target_summary.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.target_summary.setAccessibleName("Captured history target")
        self.metadata_label = QLabel(detail)
        self.metadata_label.setObjectName("query-history-metadata")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text_preview = QPlainTextEdit(detail)
        self.text_preview.setObjectName("query-history-text-preview")
        self.text_preview.setReadOnly(True)
        self.text_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_preview.setAccessibleName("Complete saved SQL or NoSQL text")
        self.restore_button = QPushButton(detail)
        self.restore_button.setObjectName("query-history-open-new-draft")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._open_as_new_draft)
        detail_layout.addWidget(self.target_summary)
        detail_layout.addWidget(self.metadata_label)
        detail_layout.addWidget(self.text_preview, 1)
        detail_layout.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignRight)
        self.splitter.addWidget(self.record_list)
        self.splitter.addWidget(detail)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        outer.addWidget(self.splitter, 1)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("query-history-status")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

    def _set_language_labels(self) -> None:
        self.title_label.setText(self._tr("查询历史与收藏", "Query history and favorites"))
        self.query_edit.setPlaceholderText(self._tr("搜索 SQL / NoSQL 文本", "Search SQL / NoSQL text"))
        self.query_edit.setAccessibleName(self._tr("历史文本搜索", "History text search"))
        self.connection_edit.setPlaceholderText(self._tr("连接 ID（可选）", "Connection ID (optional)"))
        self.connection_edit.setAccessibleName(self._tr("按连接筛选", "Filter by connection"))
        self.limit_spin.setToolTip(self._tr("最多显示条数", "Maximum number of records"))
        self.limit_spin.setAccessibleName(self._tr("最多显示条数", "Maximum number of records"))
        self.search_button.setText(self._tr("搜索", "Search"))
        self.history_button.setText(self._tr("全部历史", "History"))
        self.favorites_button.setText(self._tr("仅收藏", "Favorites"))
        self.clear_button.setText(self._tr("清除普通历史", "Clear history"))
        self.clear_all_button.setText(self._tr("清除历史和收藏", "Clear all"))
        self.restore_button.setText(self._tr("作为新草稿打开", "Open as new draft"))
        self._update_favorite_button()

    def _apply_calm_theme(self) -> None:
        from ui.theme_manager import ThemeManager

        palette = ThemeManager.instance().palette("calm")
        self.setStyleSheet(
            "QWidget#query-history-browser { background: %(app)s; color: %(text)s; }"
            "QLineEdit, QSpinBox, QListWidget, QPlainTextEdit { background: %(surface)s;"
            " color: %(text)s;"
            " border: 1px solid %(border)s; border-radius: 8px; padding: 6px; }"
            "QPushButton { background: %(soft)s; color: %(text)s; border: 1px solid %(border)s;"
            " border-radius: 8px; padding: 6px 10px; min-height: 28px; }"
            "QPushButton:hover { background: %(primary_soft)s; border-color: %(primary)s; }"
            "QPushButton:disabled { background: %(disabled)s; color: %(disabled_text)s; }"
            "QPushButton#query-history-open-new-draft { background: %(primary)s;"
            " color: %(on_primary)s; }"
            "QLabel#query-history-title { color: %(strong)s; font-size: 16px; font-weight: 600; }"
            "QLabel#query-history-status { color: %(muted)s; }"
            "QPlainTextEdit#query-history-target-summary { background: %(soft)s; }"
            % {
                "app": palette["APP_BG"],
                "text": palette["TEXT"],
                "strong": palette["TEXT_STRONG"],
                "muted": palette["TEXT_MUTED"],
                "surface": palette["SURFACE"],
                "soft": palette["SURFACE_SOFT"],
                "border": palette["BORDER"],
                "primary": palette["PRIMARY"],
                "primary_soft": palette["PRIMARY_SOFT"],
                "on_primary": palette["ON_PRIMARY"],
                "disabled": palette["DISABLED_BG"],
                "disabled_text": palette["DISABLED_TEXT"],
            }
        )

    def set_language(self, language: str) -> None:
        self._language = "zh" if str(language or "").casefold().startswith("zh") else "en"
        self._set_language_labels()

    def save_history(self, record: HistoryRecord) -> str | None:
        """Pass a caller-captured immutable record to the injected store owner."""

        return self.controller.save_history(record)

    def reload(self) -> str | None:
        return self._search()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._initial_load_requested:
            self._initial_load_requested = True
            self._search()

    def _search(self) -> str | None:
        self._view = "history"
        connection_id = self.connection_edit.text()
        self.status_label.setText(self._tr("正在读取历史…", "Loading history…"))
        request_id = self.controller.search_history(
            self.query_edit.text(),
            connection_id=None if connection_id == "" else connection_id,
            limit=self.limit_spin.value(),
        )
        if request_id is not None:
            self._display_generation = self.controller.current_generation
        return request_id

    def _show_favorites(self) -> str | None:
        self._view = "favorites"
        self.status_label.setText(self._tr("正在读取收藏…", "Loading favorites…"))
        request_id = self.controller.show_favorites(limit=self.limit_spin.value())
        if request_id is not None:
            self._display_generation = self.controller.current_generation
        return request_id

    def _on_records_ready(self, result: QueryHistoryResult) -> None:
        if not isinstance(result, QueryHistoryResult):
            return
        if result.generation < self._display_generation or result.view != self._view:
            return
        self._display_generation = result.generation
        self._records = result.records
        self._render_records()

    def _render_records(self) -> None:
        selected_id = self._selected_record.history_id if self._selected_record else ""
        self.record_list.clear()
        selected_item = None
        for record in self._records:
            preview = record.text.replace("\r", " ").replace("\n", " ").strip()
            preview = preview[:116] + ("…" if len(preview) > 116 else "")
            empty_preview = self._tr("（空文本）", "(empty text)")
            label = ("★ " if record.is_favorite else "") + (preview or empty_preview)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(self._record_tooltip(record))
            self.record_list.addItem(item)
            if record.history_id == selected_id:
                selected_item = item
        if selected_item is not None:
            self.record_list.setCurrentItem(selected_item)
        elif self.record_list.count():
            self.record_list.setCurrentRow(0)
        else:
            self._selected_record = None
            self._show_selected_record(None)
        if self._records:
            count = len(self._records)
            label = self._tr(f"显示 {count} 条记录", f"Showing {count} records")
        else:
            label = self._tr("没有匹配的记录。", "No matching records.")
        self.status_label.setText(label)

    def _on_current_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        record = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._selected_record = record if isinstance(record, HistoryRecord) else None
        self._show_selected_record(self._selected_record)

    def _show_selected_record(self, record: HistoryRecord | None) -> None:
        enabled = record is not None
        self.favorite_button.setEnabled(enabled)
        self.restore_button.setEnabled(enabled)
        if record is None:
            self.target_summary.clear()
            self.target_summary.setToolTip("")
            self.metadata_label.clear()
            self.text_preview.clear()
            self._update_favorite_button()
            return
        target = record.target
        target_text = "\n".join(
            (
                f"tab_id: {target.tab_id}",
                f"tab_kind: {target.tab_kind.value}",
                f"connection_id: {target.connection_id}",
                f"profile_revision: {target.profile_revision}",
                f"database: {target.database}",
                f"schema: {target.schema}",
            )
        )
        self.target_summary.setPlainText(target_text)
        self.target_summary.setToolTip(target_text)
        favorite_zh = "是" if record.is_favorite else "否"
        favorite_en = "yes" if record.is_favorite else "no"
        row_count = (
            self._tr("未知", "unknown") if record.row_count is None else str(record.row_count)
        )
        evidence_entries = tuple(record.evidence_summary)
        evidence_preview = "；".join(
            self._evidence_detail(item) for item in evidence_entries[:2]
        )
        if len(evidence_entries) > 2:
            evidence_preview += self._tr(
                f"；另有 {len(evidence_entries) - 2} 项",
                f"; plus {len(evidence_entries) - 2} more",
            )
        if not evidence_preview:
            evidence_preview = self._tr("无", "none")
        self.metadata_label.setText(
            self._tr(
                f"类型：{record.text_kind.value}　时间：{record.created_at}　"
                f"状态：{record.state_code}　收藏：{favorite_zh}　行数：{row_count}\n"
                f"证据摘要：{evidence_preview}",
                f"Kind: {record.text_kind.value}    Time: {record.created_at}    "
                f"State: {record.state_code}    Favorite: {favorite_en}    Row count: {row_count}\n"
                f"Evidence: {evidence_preview}",
            )
        )
        evidence_tooltip = "\n".join(
            self._evidence_detail(item) for item in evidence_entries
        ) or self._tr("无", "none")
        self.metadata_label.setToolTip(
            self._tr(
                f"类型={record.text_kind.value}\n时间={record.created_at}\n状态={record.state_code}\n"
                f"收藏={favorite_zh}\n行数={row_count}\n证据摘要：\n{evidence_tooltip}",
                f"Kind={record.text_kind.value}\nTime={record.created_at}\nState={record.state_code}\n"
                f"Favorite={favorite_en}\nRow count={row_count}\nEvidence summary:\n{evidence_tooltip}",
            )
        )
        self.text_preview.setPlainText(record.text)
        self.text_preview.setToolTip(
            self._tr("完整保存文本；不会在此执行。", "Complete saved text; it is not executed here.")
        )
        self._update_favorite_button()

    def _record_tooltip(self, record: HistoryRecord) -> str:
        target = record.target
        row_count = self._tr("未知", "unknown") if record.row_count is None else str(record.row_count)
        evidence = "\n".join(
            self._evidence_detail(item)
            for item in record.evidence_summary
        ) or self._tr("无", "none")
        return self._tr(
            f"标签ID: {target.tab_id}\n标签类型: {target.tab_kind.value}\n连接ID: {target.connection_id}\n"
            f"配置版本: {target.profile_revision}\n数据库: {target.database}\n模式: {target.schema}\n"
            f"时间: {record.created_at}\n状态: {record.state_code}\n行数: {row_count}\n证据摘要:\n{evidence}",
            f"Tab ID: {target.tab_id}\nTab kind: {target.tab_kind.value}\nConnection ID: {target.connection_id}\n"
            f"Profile revision: {target.profile_revision}\nDatabase: {target.database}\nSchema: {target.schema}\n"
            f"Time: {record.created_at}\nState: {record.state_code}\nRow count: {row_count}\nEvidence summary:\n{evidence}",
        )

    def _evidence_detail(self, item: Any) -> str:
        count = self._tr("未知", "unknown") if item.count is None else str(item.count)
        truncated = self._tr("是", "yes") if item.truncated else self._tr("否", "no")
        return self._tr(
            f"ID {item.evidence_id} · 工具 {item.tool_name} · 状态 {item.state_code} · 数量 {count} · 截断 {truncated}",
            f"ID {item.evidence_id} · tool {item.tool_name} · state {item.state_code} · count {count} · truncated {truncated}",
        )

    def _update_favorite_button(self) -> None:
        record = self._selected_record
        if record is None:
            self.favorite_button.setText(self._tr("收藏", "Favorite"))
        else:
            self.favorite_button.setText(
                self._tr(
                    "取消收藏" if record.is_favorite else "收藏",
                    "Unfavorite" if record.is_favorite else "Favorite",
                )
            )

    def _toggle_favorite(self) -> None:
        record = self._selected_record
        if record is None:
            return
        self.status_label.setText(self._tr("正在更新收藏…", "Updating favorite…"))
        self.controller.set_favorite(record.history_id, not record.is_favorite)

    def _confirm_clear_history(self) -> None:
        answer = QMessageBox.question(
            self,
            self._tr("清除历史", "Clear history"),
            self._tr(
                "清除所有普通历史记录？收藏会保留。",
                "Clear ordinary history records? Favorites will be kept.",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.status_label.setText(self._tr("正在清除普通历史…", "Clearing ordinary history…"))
            self.controller.clear_history(confirmed=True)

    def _confirm_clear_all(self) -> None:
        answer = QMessageBox.question(
            self,
            self._tr("清除历史和收藏", "Clear history and favorites"),
            self._tr("这会同时删除普通历史和所有收藏，是否继续？", "This also deletes every favorite. Continue?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.status_label.setText(self._tr("正在清除历史和收藏…", "Clearing history and favorites…"))
            self.controller.clear_all_history(confirmed=True)

    def _open_as_new_draft(self) -> None:
        record = self._selected_record
        if record is not None:
            self.open_as_new_draft_requested.emit(record)

    def _on_operation_completed(self, result: QueryHistoryOperation) -> None:
        if not isinstance(result, QueryHistoryOperation):
            return
        if result.operation in {"clear_history", "clear_all"}:
            self.status_label.setText(
                self._tr(
                    f"已清除 {result.affected_count} 条记录。",
                    f"Cleared {result.affected_count} records.",
                )
            )
        elif result.operation == "save_history":
            self.status_label.setText(self._tr("历史记录已保存。", "History record saved."))
        elif result.operation == "favorite":
            self.status_label.setText(self._tr("收藏状态已更新。", "Favorite updated."))

    def _on_request_failed(self, failure: QueryHistoryFailure) -> None:
        if not isinstance(failure, QueryHistoryFailure):
            return
        messages = {
            "CREDENTIAL_TEXT_REJECTED": self._tr(
                "文本包含凭据内容，未保存。",
                "Text contains credential material and was not saved.",
            ),
            "QUEUE_FULL": self._tr("历史操作队列已满，请稍后重试。", "History queue is full. Try again shortly."),
            "CONFIRMATION_REQUIRED": self._tr(
                "此操作需要明确确认。",
                "This operation needs explicit confirmation.",
            ),
            "STORE_CLOSED": self._tr("历史存储已关闭。", "History storage is closed."),
        }
        safe_message = messages.get(
            failure.code,
            self._tr(f"历史操作失败（{failure.code}）。", f"History operation failed ({failure.code})."),
        )
        self.status_label.setText(safe_message)

    def _tr(self, zh: str, en: str) -> str:
        return zh if self._language == "zh" else en


class QueryHistoryDialog(QDialog):
    """Screen/parent-clamped dialog wrapper for the reusable browser widget."""

    def __init__(
        self,
        controller: QueryHistoryController,
        *,
        language: str = "zh",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.browser = QueryHistoryBrowser(controller, language=language, parent=self)
        self.setObjectName("query-history-dialog")
        title = (
            "查询历史与收藏"
            if str(language or "").casefold().startswith("zh")
            else "Query history and favorites"
        )
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.browser)
        self._clamp_to_available_area(parent)

    def _clamp_to_available_area(self, parent: QWidget | None) -> None:
        screen = self.screen()
        if screen is None and parent is not None:
            screen = parent.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry().adjusted(12, 12, -12, -12)
        if parent is not None and not parent.frameGeometry().isEmpty():
            parent_area = parent.frameGeometry().adjusted(12, 12, -12, -12)
            intersection = available.intersected(parent_area)
            if not intersection.isEmpty():
                available = intersection

        width = max(1, min(1100, available.width()))
        height = max(1, min(800, available.height()))
        self.setMinimumSize(min(860, width), min(580, height))
        self.browser.setMinimumSize(min(820, max(1, width - 24)), min(540, max(1, height - 48)))
        self.resize(width, height)

        x = available.left() + max(0, (available.width() - width) // 2)
        y = available.top() + max(0, (available.height() - height) // 2)
        self.move(x, y)


__all__ = [
    "MAX_HISTORY_PENDING_OPERATIONS",
    "QueryHistoryBrowser",
    "QueryHistoryCleanupResult",
    "QueryHistoryController",
    "QueryHistoryDialog",
    "QueryHistoryFailure",
    "QueryHistoryOperation",
    "QueryHistoryResult",
]
