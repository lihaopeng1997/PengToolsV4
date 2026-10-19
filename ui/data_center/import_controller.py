"""Qt owner for bounded import preview and dedicated import sessions."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import threading
import uuid
import weakref
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from tools.data_center.import_preview import (
    DEFAULT_IMPORT_LIMITS,
    ApprovedImport,
    ColumnMapping,
    ImportLimits,
    ImportPreview,
    ImportPreviewError,
    ImportSource,
    NullRule,
    SourcePreview,
    TargetColumn,
    approve_import,
    close_import_snapshot,
    inspect_import_source,
    preview_import,
)
from tools.data_center.import_session import (
    ImportSession,
    ImportSessionError,
    ImportSessionRequest,
    ImportSessionStatus,
    ImportTargetCandidates,
    candidate_target_columns,
)
from tools.data_center.manual_relational_providers import (
    BoundManualTableScope,
    ManualRelationalProviderFactory,
)
from tools.data_center.manual_driver import ManualDriverError
from tools.data_center.manual_session import ManualSessionTarget
from tools.data_center.object_tree import TreeNode, TreeTarget
from tools.data_center.profile_host import ConnectionChoice, DataCenterProfileHost
from tools.data_center.table_source import TableSourceOpenResult


@dataclass(frozen=True, slots=True)
class ImportTableContext:
    """Captured table selection and provider dependencies for one import."""

    table_result: TableSourceOpenResult
    tree_service: Any
    tree_target: TreeTarget
    node: TreeNode
    tree_generation: int
    choice: ConnectionChoice
    profile_host: DataCenterProfileHost
    connector: Callable[..., Any] | None = None
    secret_resolver: Callable[..., Any] | None = None
    parent_sql_tab_id: str = ""
    parent_sql_generation: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.table_result, TableSourceOpenResult)
            or not callable(getattr(self.tree_service, "resolve_table_source", None))
            or not isinstance(self.tree_target, TreeTarget)
            or not isinstance(self.node, TreeNode)
            or not isinstance(self.choice, ConnectionChoice)
            or not isinstance(self.profile_host, DataCenterProfileHost)
            or not isinstance(self.parent_sql_tab_id, str)
            or isinstance(self.tree_generation, bool)
            or not isinstance(self.tree_generation, int)
            or self.tree_generation < 0
            or isinstance(self.parent_sql_generation, bool)
            or not isinstance(self.parent_sql_generation, int)
            or self.parent_sql_generation < 0
        ):
            raise ImportSessionError("INVALID_TARGET")
        metadata = self.table_result.metadata
        signed_ref = metadata.signed_ref
        if (
            signed_ref.target != self.tree_target
            or signed_ref.node_ref != self.node.node_ref
            or signed_ref.tree_generation != self.tree_generation
            or metadata.target.connection_id != self.choice.connection_id
            or metadata.target.profile_revision != self.choice.revision
            or metadata.target.database != self.choice.database
            or self.tree_target.connection_id != self.choice.connection_id
            or self.tree_target.profile_revision != self.choice.revision
            or self.tree_target.provider != self.choice.provider.casefold()
            or self.tree_target.mode != self.choice.mode.casefold()
        ):
            raise ImportSessionError("TARGET_MISMATCH")


@dataclass(frozen=True, slots=True)
class ImportProgress:
    """Immutable UI update; contains only bounded preview and safe status data."""

    import_id: str
    state: str
    preview_rows: int
    preview_has_more: bool
    preview_issue_count: int
    rows_processed: int
    chunks_processed: int
    total_rows: int | None
    transaction_outcome: str
    owner_alive: bool
    connection_retained: bool
    cleanup_future_done: bool
    safe_code: str = ""


@dataclass(frozen=True, slots=True)
class ImportRegistryEntry:
    import_id: str
    state: str
    rows_processed: int
    chunks_processed: int
    transaction_outcome: str
    owner_alive: bool
    cleanup_future_done: bool
    connection_retained: bool
    decision_required: bool
    unknown_unacknowledged: bool
    failed_cleanup: bool


@dataclass(frozen=True, slots=True)
class ImportRegistrySnapshot:
    entries: tuple[ImportRegistryEntry, ...]
    active_preview_count: int
    ready_to_commit_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    failed_cleanup_ids: tuple[str, ...]
    active_ids: tuple[str, ...]
    can_close: bool


@dataclass(slots=True)
class _RetainedSession:
    session: ImportSession
    controller: "ImportController"
    approved: ApprovedImport
    dialog: Any = None
    unknown_acknowledged: bool = False


@dataclass(slots=True)
class _RetainedWorker:
    controller: "ImportController"
    worker: QThread
    dialog: Any = None
    finished_delivered: bool = False


class ImportSessionRegistry:
    """Retain import UI/session owners until workers and DB owners really exit."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, _RetainedSession] = {}
        self._workers: dict[str, _RetainedWorker] = {}

    def attach_dialog(self, controller: "ImportController", dialog: Any) -> None:
        try:
            controller._dialog_ref = weakref.ref(dialog)
        except TypeError:
            controller._dialog_ref = lambda: dialog
        with self._lock:
            for entry in self._sessions.values():
                if entry.controller is controller:
                    entry.dialog = dialog
            for entry in self._workers.values():
                if entry.controller is controller:
                    entry.dialog = dialog

    def retain_worker(self, controller: "ImportController", worker: QThread) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            dialog_ref = controller._dialog_ref
            dialog = None if dialog_ref is None else dialog_ref()
            self._workers[token] = _RetainedWorker(controller, worker, dialog)
        return token

    def release_worker(self, token: str) -> None:
        with self._lock:
            entry = self._workers.get(token)
            if entry is not None:
                entry.finished_delivered = True
                if not entry.worker.isRunning():
                    self._workers.pop(token, None)

    def retain_session(
        self,
        import_id: str,
        session: ImportSession,
        controller: "ImportController",
        approved: ApprovedImport,
    ) -> None:
        with self._lock:
            dialog_ref = controller._dialog_ref
            dialog = None if dialog_ref is None else dialog_ref()
            self._sessions[import_id] = _RetainedSession(session, controller, approved, dialog)

    def acknowledge_unknown(self, import_id: str) -> bool:
        with self._lock:
            entry = self._sessions.get(import_id)
            if entry is None or _status_state(entry.session.status) != "UNKNOWN":
                return False
            entry.unknown_acknowledged = True
            return True

    def request_cleanup(self, import_id: str, *, action: str = "") -> Future | None:
        """Request safe owner cleanup; READY/UNKNOWN require explicit actions."""

        with self._lock:
            entry = self._sessions.get(import_id)
        if entry is None:
            return None
        status = entry.session.status
        state = _status_state(status)
        if state == "READY_TO_COMMIT":
            return entry.session.rollback() if action == "rollback" else None
        if state == "UNKNOWN":
            if action != "acknowledge":
                return None
            self.acknowledge_unknown(import_id)
            return entry.session.close()
        if status.connection_retained and status.safe_code == "CONNECTION_CLOSE_FAILED":
            return None
        return entry.session.close()

    def retry_cleanup(self, import_id: str) -> Future | None:
        with self._lock:
            entry = self._sessions.get(import_id)
        if entry is None:
            return None
        try:
            return entry.session.retry_cleanup()
        except ImportSessionError:
            return None

    def collect_finished(self) -> None:
        """Drop strong references only after cleanup is done and owner is dead."""

        with self._lock:
            for import_id, entry in tuple(self._sessions.items()):
                status = entry.session.status
                if (
                    entry.session.cleanup_future.done()
                    and not status.owner_alive
                    and not status.connection_retained
                    and (
                        _status_state(status) != "UNKNOWN"
                        or entry.unknown_acknowledged
                    )
                ):
                    self._sessions.pop(import_id, None)
            for token, entry in tuple(self._workers.items()):
                if not entry.worker.isRunning() and entry.finished_delivered:
                    self._workers.pop(token, None)

    def snapshot(self) -> ImportRegistrySnapshot:
        self.collect_finished()
        with self._lock:
            retained = tuple(self._sessions.items())
            active_preview_count = len(self._workers)
        entries: list[ImportRegistryEntry] = []
        for import_id, retained_entry in retained:
            session = retained_entry.session
            status = session.status
            state = _status_state(status)
            cleanup_done = session.cleanup_future.done()
            ready = state == "READY_TO_COMMIT"
            unknown = state == "UNKNOWN" and not retained_entry.unknown_acknowledged
            failed_cleanup = bool(
                status.safe_code == "CONNECTION_CLOSE_FAILED"
                or (
                    status.connection_retained
                    and state in {"COMMITTED", "ROLLED_BACK", "FAILED", "CANCELLED", "UNKNOWN"}
                )
                or (cleanup_done and not status.cleanup_complete)
            )
            entries.append(ImportRegistryEntry(
                import_id=import_id,
                state=state,
                rows_processed=status.rows_processed,
                chunks_processed=status.chunks_processed,
                transaction_outcome=status.transaction_outcome,
                owner_alive=status.owner_alive,
                cleanup_future_done=cleanup_done,
                connection_retained=status.connection_retained,
                decision_required=ready,
                unknown_unacknowledged=unknown,
                failed_cleanup=failed_cleanup,
            ))
        ready_ids = tuple(entry.import_id for entry in entries if entry.decision_required)
        unknown_ids = tuple(entry.import_id for entry in entries if entry.unknown_unacknowledged)
        failed_ids = tuple(entry.import_id for entry in entries if entry.failed_cleanup)
        active_ids = tuple(entry.import_id for entry in entries if entry.state in {
            "CREATED", "QUEUED", "RUNNING", "CANCEL_PENDING", "COMMITTING", "ROLLING_BACK",
        } or entry.owner_alive)
        can_close = not (
            active_preview_count or ready_ids or unknown_ids or failed_ids or active_ids
        )
        return ImportRegistrySnapshot(
            tuple(entries), active_preview_count, ready_ids, unknown_ids,
            failed_ids, active_ids, can_close,
        )


DEFAULT_IMPORT_SESSION_REGISTRY = ImportSessionRegistry()


@dataclass(frozen=True, slots=True)
class _WorkerDelivery:
    request_id: str
    operation: str
    source_preview: SourcePreview | None = None
    preview: ImportPreview | None = None
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class _FutureDelivery:
    import_id: str
    operation: str
    result: Any = None
    error_code: str = ""
    future_id: int = 0


class _ImportBridge(QObject):
    future_completed = pyqtSignal(object)


class _ImportWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(
        self,
        request_id: str,
        operation: str,
        *,
        source: ImportSource | None = None,
        source_preview: SourcePreview | None = None,
        mapping: tuple[ColumnMapping, ...] = (),
        targets: tuple[TargetColumn, ...] = (),
        null_rule: NullRule | None = None,
        limits: ImportLimits = DEFAULT_IMPORT_LIMITS,
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.operation = operation
        self.source = source
        self.source_preview = source_preview
        self.mapping = mapping
        self.targets = targets
        self.null_rule = null_rule
        self.limits = limits
        self.cancel_event = cancel_event or threading.Event()

    def run(self) -> None:
        source_preview = self.source_preview
        try:
            if self.operation == "inspect":
                if self.source is None:
                    raise ImportPreviewError("INVALID_SOURCE")
                source_preview = inspect_import_source(
                    self.source,
                    limits=self.limits,
                    cancel_check=self.cancel_event.is_set,
                )
                if self.cancel_event.is_set():
                    close_import_snapshot(source_preview)
                    source_preview = None
                    raise ImportPreviewError("CANCELLED")
                self.completed.emit(_WorkerDelivery(
                    self.request_id, self.operation, source_preview=source_preview,
                ))
                return
            if source_preview is None or self.null_rule is None:
                raise ImportPreviewError("INVALID_OPTIONS")
            preview = preview_import(source_preview, self.mapping, self.targets, self.null_rule)
            self.completed.emit(_WorkerDelivery(
                self.request_id, self.operation, source_preview=source_preview, preview=preview,
            ))
        except ImportPreviewError as error:
            if self.operation == "inspect" and source_preview is not None:
                close_import_snapshot(source_preview)
            self.completed.emit(_WorkerDelivery(
                self.request_id, self.operation, error_code=error.code,
            ))
        except Exception:
            if self.operation == "inspect" and source_preview is not None:
                close_import_snapshot(source_preview)
            self.completed.emit(_WorkerDelivery(
                self.request_id, self.operation, error_code="IMPORT_PREVIEW_FAILED",
            ))


class ImportController(QObject):
    """Asynchronous preview and dedicated transaction owner for one table."""

    progress_changed = pyqtSignal(object)
    preview_ready = pyqtSignal(object)
    operation_finished = pyqtSignal(str, object, str)

    def __init__(
        self,
        context: ImportTableContext,
        *,
        registry: ImportSessionRegistry | None = None,
        limits: ImportLimits = DEFAULT_IMPORT_LIMITS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(context, ImportTableContext) or not isinstance(limits, ImportLimits):
            raise ImportSessionError("INVALID_REQUEST")
        self.context = context
        self.target_candidates: ImportTargetCandidates = candidate_target_columns(
            context.table_result.metadata,
        )
        self.registry = registry or DEFAULT_IMPORT_SESSION_REGISTRY
        self.limits = limits
        self.import_id = f"import-{uuid.uuid4().hex}"
        self.import_generation = 1
        self.import_tab_id = f"import-tab-{self.import_id.removeprefix('import-')}"
        self._state = "EMPTY"
        self._source_preview: SourcePreview | None = None
        self._preview: ImportPreview | None = None
        self._approved: ApprovedImport | None = None
        self._approval_created = False
        self._worker: _ImportWorker | None = None
        self._worker_token = ""
        self._worker_cancel: threading.Event | None = None
        self._session: ImportSession | None = None
        self._safe_code = ""
        self._close_requested = False
        self._shutdown_gated = False
        self._unknown_acknowledged = False
        self._future_lock = threading.RLock()
        self._pending_futures: dict[int, Future] = {}
        self._bridge = _ImportBridge(self)
        self._dialog_ref: Callable[[], Any] | None = None
        self._bridge.future_completed.connect(self._on_future_completed)
        self._timer = QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._poll_status)
        self._last_progress: ImportProgress | None = None

    @property
    def session(self) -> ImportSession | None:
        return self._session

    @property
    def preview(self) -> ImportPreview | None:
        return self._preview

    @property
    def source_preview(self) -> SourcePreview | None:
        return self._source_preview

    @property
    def can_edit_source(self) -> bool:
        return not self._shutdown_gated and self._session is None and not self._approval_created

    @property
    def shutdown_gated(self) -> bool:
        return self._shutdown_gated

    @property
    def operation_pending(self) -> bool:
        with self._future_lock:
            future_pending = any(not future.done() for future in self._pending_futures.values())
        return future_pending or bool(self._worker is not None and self._worker.isRunning())

    @property
    def worker_pending(self) -> bool:
        return bool(self._worker is not None and self._worker.isRunning())

    @property
    def unknown_acknowledged(self) -> bool:
        return self._unknown_acknowledged

    def set_shutdown_gate(self, enabled: bool) -> None:
        self._shutdown_gated = bool(enabled)
        self._emit_progress()

    def clear_preview(self) -> bool:
        if self._shutdown_gated or not self.can_edit_source or self._worker is not None and self._worker.isRunning():
            return False
        self._release_source_preview()
        self._preview = None
        self._safe_code = ""
        self._state = "EMPTY"
        self._emit_progress()
        return True

    def clear_mapping_preview(self) -> bool:
        if self._shutdown_gated or self._session is not None or self._worker is not None and self._worker.isRunning():
            return False
        if self._source_preview is None:
            return False
        self._preview = None
        self._safe_code = ""
        self._state = "SOURCE_READY"
        self._emit_progress()
        return True

    def attach_dialog(self, dialog: Any) -> None:
        self.registry.attach_dialog(self, dialog)

    def inspect_source(self, source: ImportSource) -> bool:
        if self._shutdown_gated:
            return False
        if self._worker is not None and self._worker.isRunning():
            return False
        if self._approval_created:
            return False
        self._release_source_preview()
        self._preview = None
        self._safe_code = ""
        self._state = "INSPECTING"
        cancel_event = threading.Event()
        worker = _ImportWorker(
            uuid.uuid4().hex,
            "inspect",
            source=source,
            limits=self.limits,
            cancel_event=cancel_event,
        )
        self._start_worker(worker, cancel_event)
        return True

    def preview_mapping(
        self,
        mapping: tuple[ColumnMapping, ...],
        targets: tuple[TargetColumn, ...],
        null_rule: NullRule,
    ) -> bool:
        if self._shutdown_gated:
            return False
        if (
            self._source_preview is None
            or self._worker is not None and self._worker.isRunning()
            or self._approval_created
        ):
            return False
        try:
            selected_mapping = tuple(mapping)
            selected_targets = tuple(targets)
            self._validate_mapping_candidates(selected_mapping, selected_targets)
        except (TypeError, ImportSessionError) as error:
            self._safe_code = getattr(error, "code", "INVALID_MAPPING")
            self._state = "PREVIEW_FAILED"
            self._emit_progress()
            return False
        self._preview = None
        self._safe_code = ""
        self._state = "PREVIEWING"
        cancel_event = threading.Event()
        worker = _ImportWorker(
            uuid.uuid4().hex,
            "mapping",
            source_preview=self._source_preview,
            mapping=selected_mapping,
            targets=selected_targets,
            null_rule=null_rule,
            limits=self.limits,
            cancel_event=cancel_event,
        )
        self._start_worker(worker, cancel_event)
        return True

    def approve_and_start(self, *, user_approved: bool) -> bool:
        if self._shutdown_gated:
            return False
        if (
            not self.target_candidates.can_approve
            or self._preview is None
            or self._worker is not None and self._worker.isRunning()
            or self._approval_created
        ):
            return False
        try:
            self._validate_mapping_candidates(self._preview.mapping, self._preview.targets)
            approved = approve_import(self._preview, user_approved=user_approved)
            self._approval_created = True
            metadata = self.context.table_result.metadata
            captured_target = ManualSessionTarget(
                tab_id=self.import_tab_id,
                connection_id=self.context.choice.connection_id,
                profile_revision=self.context.choice.revision,
                database=self.context.choice.database,
                schema=metadata.target.schema,
                generation=self.import_generation,
            )
            scope = BoundManualTableScope.capture(
                self.context.tree_service,
                self.context.tree_target,
                self.context.node,
                self.context.tree_generation,
                captured_target,
                self.context.choice,
            )
            factory = ManualRelationalProviderFactory(
                self.context.choice,
                profile_host=self.context.profile_host,
                connector=self.context.connector,
                secret_resolver=self.context.secret_resolver,
                table_scope=scope,
            )
            request = ImportSessionRequest(
                approved,
                self.context.tree_service,
                self.context.tree_target,
                self.context.node,
                self.context.tree_generation,
                captured_target,
                self.context.choice,
            )
            session = ImportSession(request, driver_factory=factory)
        except (ImportPreviewError, ImportSessionError, ManualDriverError, ValueError) as error:
            self._safe_code = getattr(error, "code", "INVALID_TARGET")
            if self._approval_created and "approved" in locals():
                close_import_snapshot(approved)
            self._state = "FAILED"
            self._emit_progress()
            return False
        self._approved = approved
        self._session = session
        self.registry.retain_session(self.import_id, session, self, approved)
        self._state = "QUEUED"
        self._timer.start()
        self._watch_future("run", session.start())
        self._emit_progress()
        return True

    def commit(self) -> bool:
        if self._shutdown_gated:
            return False
        return self._start_commit()

    def _start_commit(self) -> bool:
        if self._session is None or _status_state(self._session.status) != "READY_TO_COMMIT":
            return False
        self._state = "COMMITTING"
        self._watch_future("commit", self._session.commit())
        self._emit_progress()
        return True

    def rollback(self) -> bool:
        if self._shutdown_gated:
            return False
        return self._start_rollback()

    def _start_rollback(self) -> bool:
        if self._session is None or _status_state(self._session.status) != "READY_TO_COMMIT":
            return False
        self._state = "ROLLING_BACK"
        self._watch_future("rollback", self._session.rollback())
        self._emit_progress()
        return True

    def stop(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            if self._worker_cancel is not None:
                self._worker_cancel.set()
            self._state = "CANCEL_PENDING"
            self._emit_progress()
            return True
        if self._session is None:
            return False
        state = _status_state(self._session.status)
        if state == "READY_TO_COMMIT":
            return False
        result = self._session.cancel_current()
        if result:
            self._state = "CANCEL_PENDING"
            self._emit_progress()
        return result

    def retry_cleanup(self) -> bool:
        if self._session is None:
            return False
        future = self.registry.retry_cleanup(self.import_id)
        if future is None:
            return False
        self._watch_future("retry_cleanup", future)
        return True

    def request_close(self, *, action: str = "") -> bool:
        """Close policy seam for a future host; active owners remain registered."""

        if self._shutdown_gated:
            return False

        self._close_requested = True
        if self._worker is not None and self._worker.isRunning():
            self.stop()
            self._release_source_preview()
            self._preview = None
            return True
        if self._session is None:
            self._release_source_preview()
            return True
        status = self._session.status
        if (
            self._session.cleanup_future.done()
            and not status.owner_alive
            and not status.connection_retained
        ):
            if _status_state(status) == "UNKNOWN" and action == "acknowledge":
                self._unknown_acknowledged = self.registry.acknowledge_unknown(self.import_id)
                self.registry.collect_finished()
                return self._unknown_acknowledged
            if _status_state(status) != "UNKNOWN":
                return True
        future = self.registry.request_cleanup(self.import_id, action=action)
        if future is None:
            return False
        state = _status_state(self._session.status)
        if state == "READY_TO_COMMIT" and action == "rollback":
            self._watch_future("rollback", future)
        elif state == "UNKNOWN" and action == "acknowledge":
            self._unknown_acknowledged = self.registry.acknowledge_unknown(self.import_id)
            self._watch_future("close", future)
        else:
            self._watch_future("close", future)
        return True

    def acknowledge_unknown(self) -> bool:
        if self._shutdown_gated or self._session is None:
            return False
        acknowledged = self.registry.acknowledge_unknown(self.import_id)
        self._unknown_acknowledged = bool(acknowledged)
        self._emit_progress()
        return acknowledged

    def _host_discard_preview(self) -> bool:
        """Release a local preview after a host-wide, gated close preflight."""
        if self._session is not None or self.operation_pending:
            return False
        self._close_requested = True
        self._release_source_preview()
        self._preview = None
        self._state = "CANCELLED"
        self._emit_progress()
        return True

    def progress(self) -> ImportProgress:
        session_status = self._session.status if self._session is not None else None
        cleanup_done = (
            self._session.cleanup_future.done()
            if self._session is not None
            else True
        )
        if session_status is not None:
            state = _status_state(session_status)
        else:
            state = self._state
        source_preview = self._source_preview
        preview = self._preview
        return ImportProgress(
            import_id=self.import_id,
            state=state,
            preview_rows=0 if preview is None else len(preview.rows),
            preview_has_more=False if source_preview is None else source_preview.has_more,
            preview_issue_count=0 if preview is None else len(preview.issues),
            rows_processed=0 if session_status is None else session_status.rows_processed,
            chunks_processed=0 if session_status is None else session_status.chunks_processed,
            total_rows=None if session_status is None else session_status.total_rows,
            transaction_outcome="none" if session_status is None else session_status.transaction_outcome,
            owner_alive=False if session_status is None else session_status.owner_alive,
            connection_retained=False if session_status is None else session_status.connection_retained,
            cleanup_future_done=cleanup_done,
            safe_code=self._safe_code if session_status is None else session_status.safe_code,
        )

    def _validate_mapping_candidates(
        self,
        mapping: tuple[ColumnMapping, ...],
        targets: tuple[TargetColumn, ...],
    ) -> None:
        if not self.target_candidates.can_approve:
            raise ImportSessionError(
                self.target_candidates.blocking_reason_codes[0] if self.target_candidates.blocking_reason_codes
                else "NO_IMPORTABLE_COLUMNS"
            )
        if not mapping or len(mapping) != len(targets):
            raise ImportSessionError("INVALID_MAPPING")
        candidate_by_name = {
            item.target.name: item.target
            for item in self.target_candidates.columns
            if item.target is not None
        }
        mapped_names = tuple(item.target_name for item in mapping)
        if len(set(mapped_names)) != len(mapped_names):
            raise ImportSessionError("INVALID_MAPPING")
        for name, target in zip(mapped_names, targets):
            if candidate_by_name.get(name) != target or target.name != name:
                raise ImportSessionError("IMPORT_TARGET_MISMATCH")
        for candidate in self.target_candidates.columns:
            if candidate.required_on_insert and candidate.target is not None and candidate.name not in mapped_names:
                raise ImportSessionError("REQUIRED_COLUMN_UNMAPPED")

    def _start_worker(self, worker: _ImportWorker, cancel_event: threading.Event) -> None:
        self._worker = worker
        self._worker_cancel = cancel_event
        self._worker_token = self.registry.retain_worker(self, worker)
        worker.completed.connect(self._on_worker_completed)
        worker.finished.connect(self._on_worker_thread_finished)
        self._emit_progress()
        worker.start()

    @pyqtSlot(object)
    def _on_worker_completed(self, delivery: _WorkerDelivery) -> None:
        if self._worker is None or delivery.request_id != self._worker.request_id:
            return
        if self._close_requested:
            if delivery.source_preview is not None:
                close_import_snapshot(delivery.source_preview)
            elif delivery.preview is not None:
                close_import_snapshot(delivery.preview.source_preview)
            self._release_source_preview()
            self._preview = None
            self._safe_code = delivery.error_code or "CANCELLED"
            self._state = "CANCELLED"
            self._emit_progress()
            self.operation_finished.emit(delivery.operation, None, self._safe_code)
            return
        if delivery.error_code:
            self._safe_code = delivery.error_code
            self._state = "CANCELLED" if delivery.error_code == "CANCELLED" else "PREVIEW_FAILED"
        elif delivery.operation == "inspect" and delivery.source_preview is not None:
            self._source_preview = delivery.source_preview
            self._state = "SOURCE_READY"
            self.preview_ready.emit(delivery.source_preview)
        elif delivery.operation == "mapping" and delivery.preview is not None:
            self._preview = delivery.preview
            self._state = "PREVIEW_READY"
            self.preview_ready.emit(delivery.preview)
        self._emit_progress()
        self.operation_finished.emit(delivery.operation, delivery.preview or delivery.source_preview, delivery.error_code)

    @pyqtSlot(str)
    def _on_worker_finished(self, request_id: str) -> None:
        worker = self._worker
        if worker is None or worker.request_id != request_id:
            return
        self.registry.release_worker(self._worker_token)
        self._worker = None
        self._worker_token = ""
        self._worker_cancel = None
        self._emit_progress()

    @pyqtSlot()
    def _on_worker_thread_finished(self) -> None:
        worker = self.sender()
        if isinstance(worker, _ImportWorker):
            self._on_worker_finished(worker.request_id)

    def _watch_future(self, operation: str, future: Future) -> None:
        import_id = self.import_id
        future_id = id(future)
        with self._future_lock:
            self._pending_futures[future_id] = future

        def completed(result_future: Future) -> None:
            try:
                result = result_future.result()
                error_code = ""
            except (ImportSessionError, ImportPreviewError) as error:
                result = None
                error_code = error.code
            except Exception:
                result = None
                error_code = "IMPORT_FAILED"
            self._bridge.future_completed.emit(
                _FutureDelivery(import_id, operation, result, error_code, future_id),
            )

        future.add_done_callback(completed)

    @pyqtSlot(object)
    def _on_future_completed(self, delivery: _FutureDelivery) -> None:
        if delivery.import_id != self.import_id:
            return
        with self._future_lock:
            self._pending_futures.pop(delivery.future_id, None)
        if delivery.error_code:
            self._safe_code = delivery.error_code
        elif delivery.operation == "run":
            self._safe_code = ""
        elif delivery.operation in {"commit", "rollback"}:
            self._safe_code = getattr(delivery.result, "safe_code", "")
        self.registry.collect_finished()
        self._emit_progress()
        self.operation_finished.emit(delivery.operation, delivery.result, delivery.error_code)

    @pyqtSlot()
    def _poll_status(self) -> None:
        self.registry.collect_finished()
        progress = self.progress()
        if progress != self._last_progress:
            self._last_progress = progress
            self.progress_changed.emit(progress)
        if (
            self._session is not None
            and self._session.cleanup_future.done()
            and not self._session.status.owner_alive
        ):
            self._timer.stop()

    def _emit_progress(self) -> None:
        progress = self.progress()
        self._last_progress = progress
        self.progress_changed.emit(progress)

    def _release_source_preview(self) -> None:
        if self._source_preview is not None:
            close_import_snapshot(self._source_preview)
            self._source_preview = None


def _status_state(status: ImportSessionStatus) -> str:
    state = status.state
    return str(getattr(state, "value", state))


__all__ = [
    "DEFAULT_IMPORT_SESSION_REGISTRY",
    "ImportController",
    "ImportProgress",
    "ImportRegistryEntry",
    "ImportRegistrySnapshot",
    "ImportSessionRegistry",
    "ImportTableContext",
]
