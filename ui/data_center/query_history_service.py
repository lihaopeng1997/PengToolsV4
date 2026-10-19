"""Application-owned query history persistence and worker completion sinks.

The service owns one explicit-path ``WorkspaceStore``.  Query-history
controllers borrow it and serialize all persistence work on their own Qt
worker threads.  Creating the service or freezing a capture performs no file
or database I/O.
"""

from __future__ import annotations

from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
import uuid

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot

from tools.data_center.manual_driver import profile_schema_for_provider
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.workspace_store import (
    HistoryEvidenceSummary,
    HistoryRecord,
    WorkspaceStore,
    WorkspaceTabKind,
    WorkspaceTarget,
    WorkspaceTextKind,
)
from ui.data_center.query_history import (
    QueryHistoryCleanupResult,
    QueryHistoryController,
    QueryHistoryFailure,
)
from tools.data_center.manual_session import ManualOperationFuture, ManualQueryPage, ManualScriptResult, ManualSessionError, ManualStatementResult
from tools.data_center.nosql_workspace import decode_nosql_workspace


RELATIONAL_PROVIDERS = frozenset({"mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"})
_NOSQL_PROVIDERS = frozenset({"redis", "redis_cluster", "mongodb", "mongo", "mongo_db"})
_HISTORY_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "LIMIT_REACHED", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class HistoryCapture:
    """Immutable execution identity frozen before a database operation starts."""

    history_id: str
    target: WorkspaceTarget
    text: str
    text_kind: WorkspaceTextKind
    created_at: str

    def record(
        self,
        state_code: str,
        *,
        row_count: int | None = None,
        evidence_summary: tuple[HistoryEvidenceSummary, ...] = (),
    ) -> HistoryRecord:
        state = state_code if state_code in _HISTORY_STATES else "UNKNOWN"
        count = row_count if type(row_count) is int and row_count >= 0 else None
        evidence = evidence_summary if type(evidence_summary) is tuple else ()
        return HistoryRecord(
            history_id=self.history_id,
            target=self.target,
            text=self.text,
            text_kind=self.text_kind,
            created_at=self.created_at,
            state_code=state,
            row_count=count,
            evidence_summary=evidence,
        )


@dataclass(slots=True)
class _LegacySink:
    capture: HistoryCapture
    terminal_seen: bool = False


@dataclass(slots=True)
class _NoSqlSink:
    capture: HistoryCapture
    provider: str
    action: str
    terminal_seen: bool = False


@dataclass(frozen=True, slots=True)
class _ManualHistoryCompletion:
    """Allowlisted Future completion sent to the app-owned Qt sink."""

    key: str
    state_code: str
    result_tab_id: str = ""
    result_generation: int = 0
    query_id: str = ""
    row_count: int | None = None


def _dispatch_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _valid_runtime_tab_id(tab_id: str, generation: int) -> str | None:
    if not tab_id or len(tab_id) > 110 or not tab_id.replace("_", "").replace("-", "").isalnum():
        return None
    if type(generation) is not int or generation < 1:
        return None
    return f"{tab_id}:{generation}"


def make_history_capture(
    text: str,
    *,
    connection_id: str,
    profile_revision: str,
    database: str = "",
    schema: str = "",
    runtime_tab_id: str,
    generation: int,
    text_kind: WorkspaceTextKind = WorkspaceTextKind.SQL,
    tab_kind: WorkspaceTabKind = WorkspaceTabKind.SQL,
) -> HistoryCapture | None:
    """Freeze a bounded target and exact dispatched text without touching storage."""

    sql = text if type(text) is str else ""
    frozen_tab_id = _valid_runtime_tab_id(runtime_tab_id, generation)
    if not sql.strip() or frozen_tab_id is None:
        return None
    try:
        target = WorkspaceTarget(
            tab_id=frozen_tab_id,
            connection_id=str(connection_id or ""),
            profile_revision=str(profile_revision or ""),
            database=str(database or ""),
            schema=str(schema or ""),
            tab_kind=tab_kind,
        )
    except (TypeError, ValueError):
        return None
    return HistoryCapture(
        history_id=uuid.uuid4().hex,
        target=target,
        text=sql,
        text_kind=text_kind,
        created_at=_dispatch_timestamp(),
    )


def capture_legacy_relational_history(
    text: str,
    profile: Any,
    *,
    runtime_tab_id: str,
    generation: int,
    profile_host: DataCenterProfileHost | None = None,
) -> HistoryCapture | None:
    """Capture the exact relational profile behind the legacy SQL Run route."""

    if not isinstance(profile, dict):
        return None
    try:
        choice = (profile_host or DataCenterProfileHost()).capture_connection(profile)
        if not choice.provider or choice.provider in _NOSQL_PROVIDERS:
            return None
        schema = profile_schema_for_provider(profile, choice.provider)
        return make_history_capture(
            text,
            connection_id=choice.connection_id,
            profile_revision=choice.revision,
            database=choice.database,
            schema=schema,
            runtime_tab_id=runtime_tab_id,
            generation=generation,
        )
    except Exception:
        return None


def exact_nonnegative_count(value: Any) -> int | None:
    """Accept only a count supplied as an exact, non-negative integer."""

    return value if type(value) is int and value >= 0 else None


def legacy_result_summary(payload: Any) -> tuple[str, int | None]:
    """Extract only allowlisted status and count fields from a worker payload."""

    if type(payload) is not dict:
        return "COMPLETED", None
    raw_status = payload.get("status")
    state = {
        "ERROR": "FAILED",
        "CANCELLED": "CANCELLED",
        "LIMIT_REACHED": "LIMIT_REACHED",
    }.get(raw_status if type(raw_status) is str else "", "COMPLETED")
    return state, exact_nonnegative_count(payload.get("rowcount"))


class DataCenterHistoryService(QObject):
    """Own the shared history store and every borrowing query controller."""

    save_failed = pyqtSignal(str)
    _manual_completion_ready = pyqtSignal(object)

    def __init__(
        self,
        path: str | None = None,
        *,
        store: Any = None,
        store_factory: Callable[[str], Any] = WorkspaceStore,
        controller_factory: Callable[..., QueryHistoryController] = QueryHistoryController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if store is None:
            if path is None:
                from config import DATA_CENTER_WORKSPACE_FILE

                path = DATA_CENTER_WORKSPACE_FILE
            self._store = store_factory(path)
        else:
            self._store = store
        self._controller_factory = controller_factory
        self._controllers: set[QueryHistoryController] = set()
        self._retiring: dict[Future[QueryHistoryCleanupResult], QueryHistoryController] = {}
        self._cleanup_futures: set[Future[QueryHistoryCleanupResult]] = set()
        self._proven_cleanup_futures: set[Future[QueryHistoryCleanupResult]] = set()
        self._legacy_sinks: dict[int, tuple[Any, _LegacySink]] = {}
        self._nosql_sinks: dict[int, tuple[Any, _NoSqlSink]] = {}
        self._manual_pending: dict[str, tuple[HistoryCapture, str, int, str]] = {}
        self._persistence_controller: QueryHistoryController | None = None
        self._closing = False
        self._store_closed = False
        self._cleanup_error_code = ""
        self._manual_completion_ready.connect(
            self._on_manual_completion_ready,
            Qt.ConnectionType.QueuedConnection,
        )

    @property
    def store(self) -> Any:
        """The shared store is exposed only for dependency injection and inspection."""

        return self._store

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def cleanup_error_code(self) -> str:
        return self._cleanup_error_code

    def new_controller(self) -> QueryHistoryController:
        """Create a dialog controller that borrows this service's store."""

        if self._closing:
            raise RuntimeError("history service is closing")
        controller = self._controller_factory(self._store)
        controller.request_failed.connect(self._on_controller_failure)
        self._controllers.add(controller)
        return controller

    def save_history(self, record: HistoryRecord) -> str | None:
        """Queue an immutable record through the serial store worker."""

        if self._closing:
            self.save_failed.emit("STORE_CLOSED")
            return None
        if type(record) is not HistoryRecord:
            self.save_failed.emit("INVALID_RECORD")
            return None
        controller = self._get_persistence_controller()
        return controller.save_history(record)

    def save_capture(
        self,
        capture: HistoryCapture,
        state_code: str,
        *,
        row_count: int | None = None,
        evidence_summary: tuple[HistoryEvidenceSummary, ...] = (),
    ) -> str | None:
        if type(capture) is not HistoryCapture:
            return None
        record = capture.record(
            state_code,
            row_count=row_count,
            evidence_summary=evidence_summary,
        )
        return self.save_history(record)

    def attach_legacy_worker(self, worker: Any, capture: HistoryCapture | None) -> None:
        """Keep an app-owned sink connected through the worker's real finish."""

        if capture is None or self._closing or worker is None:
            return
        key = id(worker)
        if key in self._legacy_sinks:
            return
        self._get_persistence_controller()
        sink = _LegacySink(capture)
        self._legacy_sinks[key] = (worker, sink)
        try:
            worker.completed.connect(self._on_legacy_completed)
            worker.failed.connect(self._on_legacy_failed)
            worker.finished.connect(self._on_legacy_finished)
        except (AttributeError, RuntimeError, TypeError):
            self._legacy_sinks.pop(key, None)

    def attach_nosql_worker(self, worker: Any, capture: HistoryCapture | None) -> bool:
        """Retain a NoSQL worker sink with its exact public Qt signal signatures.

        This route is independent of panel-owned UI delivery. The codec is
        decoded at registration so terminal handling trusts only its typed
        provider/action identity. Persistence creation remains best-effort for
        the caller, which must still run an otherwise accepted database task.
        """

        if capture is None or self._closing or worker is None:
            return False
        if type(capture) is not HistoryCapture or capture.text_kind != WorkspaceTextKind.NOSQL:
            return False
        key = id(worker)
        if key in self._nosql_sinks:
            return True
        try:
            snapshot = decode_nosql_workspace(capture.target, capture.text)
            self._get_persistence_controller()
        except Exception:
            return False
        sink = _NoSqlSink(capture, snapshot.provider, snapshot.action)
        try:
            worker.completed.connect(self._on_nosql_completed)
            worker.failed.connect(self._on_nosql_failed)
            worker.finished.connect(self._on_nosql_finished)
        except (AttributeError, RuntimeError, TypeError):
            for signal, slot in (
                (getattr(worker, "completed", None), self._on_nosql_completed),
                (getattr(worker, "failed", None), self._on_nosql_failed),
                (getattr(worker, "finished", None), self._on_nosql_finished),
            ):
                if signal is not None:
                    try:
                        signal.disconnect(slot)
                    except (TypeError, RuntimeError):
                        pass
            return False
        self._nosql_sinks[key] = (worker, sink)
        return True

    def detach_unstarted_nosql_worker(self, worker: Any) -> bool:
        """Remove a history sink only when QThread never accepted start()."""

        if worker is None:
            return False
        entry = self._nosql_sinks.get(id(worker))
        if entry is None or entry[0] is not worker:
            return False
        try:
            if worker.isRunning() or worker.isFinished():
                return False
        except RuntimeError:
            return False
        self._nosql_sinks.pop(id(worker), None)
        for signal, slot in (
            (worker.completed, self._on_nosql_completed),
            (worker.failed, self._on_nosql_failed),
            (worker.finished, self._on_nosql_finished),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        return True

    def track_manual_future(
        self,
        future: Future,
        capture: HistoryCapture | None,
        *,
        target_tab_id: str,
        generation: int,
        expected_query_id: str = "",
    ) -> str | None:
        """Register dispatch before execution; persist from this app-owned sink.

        The Future callback runs on whichever thread completes the session
        operation. It reduces the result to fixed identity/count fields and
        emits to this QObject's queued sink; it never calls a controller from
        the provider/session worker thread and does not depend on the widget
        delivery bridge remaining connected.
        """

        if QThread.currentThread() != self.thread():
            raise RuntimeError("manual history registration must run on the Qt owner thread")
        if self._closing or capture is None or not isinstance(future, Future):
            return None
        if type(target_tab_id) is not str or type(generation) is not int or generation < 1:
            return None
        key = uuid.uuid4().hex
        self._manual_pending[key] = (
            capture,
            target_tab_id,
            generation,
            expected_query_id if type(expected_query_id) is str else "",
        )
        # Ensure first manual save has an application-owned serial persistence
        # worker before any completion can be emitted.
        try:
            self._get_persistence_controller()
        except Exception:
            self._manual_pending.pop(key, None)
            raise

        def completed(result_future: Future) -> None:
            state = "UNKNOWN"
            result_tab_id = ""
            result_generation = 0
            query_id = ""
            row_count = None
            try:
                result = result_future.result()
            except CancelledError:
                pass
            except ManualSessionError as error:
                state = "CANCELLED" if error.code == "CANCELLED" else "FAILED"
                if isinstance(result_future, ManualOperationFuture):
                    target = result_future.manual_target
                    captured = capture.target
                    if (target.connection_id, target.profile_revision, target.database, target.schema) == (captured.connection_id, captured.profile_revision, captured.database, captured.schema):
                        result_tab_id = target.tab_id
                        result_generation = target.generation
            except Exception:
                state = "FAILED"
            else:
                if isinstance(result, ManualScriptResult):
                    state = "CANCELLED" if result.error_code == "CANCELLED" else "FAILED" if result.error_code else "COMPLETED"
                    result_tab_id = result.tab_id
                    result_generation = result.generation
                    query_id = result.query_id
                elif isinstance(result, ManualQueryPage):
                    state = "COMPLETED"
                    result_tab_id = result.tab_id
                    result_generation = result.generation
                    query_id = result.query_id
                    row_count = exact_nonnegative_count(result.rowcount)
                elif isinstance(result, ManualStatementResult):
                    state = "COMPLETED"
                    result_tab_id = result.tab_id
                    result_generation = result.generation
                    row_count = exact_nonnegative_count(result.rowcount)
            self._manual_completion_ready.emit(_ManualHistoryCompletion(
                key,
                state,
                result_tab_id if type(result_tab_id) is str else "",
                result_generation if type(result_generation) is int else 0,
                query_id if type(query_id) is str else "",
                row_count,
            ))

        try:
            future.add_done_callback(completed)
        except Exception:
            self._manual_pending.pop(key, None)
            raise
        return key

    def retire_controller(self, controller: QueryHistoryController) -> Future[QueryHistoryCleanupResult]:
        """Stop one dialog owner while leaving the shared store open."""

        future = controller.shutdown()
        self._track_cleanup(controller, future)
        return future

    def drain(self) -> bool:
        """Drain every worker owner, then close the shared store exactly once."""

        if self._legacy_sinks or self._nosql_sinks or self._manual_pending:
            # Keep accepting the terminal save until every attached worker has
            # emitted its actual finished signal and every Future sink has
            # queued its record, even after cancellation was requested.
            return False
        self._cleanup_error_code = ""
        if not self._closing:
            self._closing = True
        retiring_controllers = set(self._retiring.values())
        for controller in tuple(self._controllers):
            if controller in retiring_controllers:
                continue
            try:
                future = controller.shutdown()
            except RuntimeError:
                # A failed request is not evidence that the Qt owner exited.
                # Keep the controller retained and retry on the next poll.
                self._cleanup_error_code = "CONTROLLER_CLEANUP_PENDING"
                continue
            self._track_cleanup(controller, future)
            retiring_controllers.add(controller)
        if self._cleanup_error_code:
            return False
        for future in self._cleanup_futures:
            if not future.done():
                return False
            if future not in self._proven_cleanup_futures:
                self._cleanup_error_code = "CONTROLLER_CLEANUP_UNPROVEN"
                return False
        if not self._store_closed:
            try:
                self._store.close()
            finally:
                self._store_closed = True
        return True

    def _get_persistence_controller(self) -> QueryHistoryController:
        if self._persistence_controller is None:
            self._persistence_controller = self.new_controller()
        return self._persistence_controller

    def _track_cleanup(
        self,
        controller: QueryHistoryController,
        future: Future[QueryHistoryCleanupResult],
    ) -> None:
        self._cleanup_futures.add(future)
        self._retiring[future] = controller
        future.add_done_callback(self._on_controller_cleanup_done)

    def _on_controller_cleanup_done(self, future: Future[QueryHistoryCleanupResult]) -> None:
        controller = self._retiring.get(future)
        if controller is None:
            return
        if not self._cleanup_future_proves_finished(future, controller):
            self._cleanup_error_code = "CONTROLLER_CLEANUP_UNPROVEN"
            return
        self._retiring.pop(future, None)
        self._proven_cleanup_futures.add(future)
        self._controllers.discard(controller)
        if controller is self._persistence_controller:
            self._persistence_controller = None

    @staticmethod
    def _cleanup_future_proves_finished(
        future: Future[QueryHistoryCleanupResult], controller: QueryHistoryController
    ) -> bool:
        if not future.done():
            return False
        try:
            result = future.result()
            thread = getattr(controller, "_thread", None)
            return bool(
                isinstance(result, QueryHistoryCleanupResult)
                and result.completed is True
                and thread is not None
                and thread.isFinished()
                and not thread.isRunning()
            )
        except Exception:
            return False

    @pyqtSlot(object)
    def _on_controller_failure(self, failure: QueryHistoryFailure) -> None:
        if isinstance(failure, QueryHistoryFailure) and failure.operation == "save_history":
            self.save_failed.emit(failure.code)

    @pyqtSlot(object)
    def _on_legacy_completed(self, payload: Any) -> None:
        worker = self.sender()
        entry = self._legacy_sinks.get(id(worker)) if worker is not None else None
        if entry is None or entry[0] is not worker:
            return
        sink = entry[1]
        if sink.terminal_seen:
            return
        sink.terminal_seen = True
        state, row_count = legacy_result_summary(payload)
        self.save_capture(sink.capture, state, row_count=row_count)

    @pyqtSlot(str)
    def _on_legacy_failed(self, _message: str) -> None:
        worker = self.sender()
        entry = self._legacy_sinks.get(id(worker)) if worker is not None else None
        if entry is None or entry[0] is not worker:
            return
        sink = entry[1]
        if sink.terminal_seen:
            return
        sink.terminal_seen = True
        self.save_capture(sink.capture, "FAILED")

    @pyqtSlot()
    def _on_legacy_finished(self) -> None:
        worker = self.sender()
        entry = self._legacy_sinks.pop(id(worker), None) if worker is not None else None
        if entry is None or entry[0] is not worker:
            return
        sink = entry[1]
        if not sink.terminal_seen:
            self.save_capture(sink.capture, "UNKNOWN")

    @pyqtSlot(str, object)
    def _on_nosql_completed(self, kind: str, payload: Any) -> None:
        worker = self.sender()
        entry = self._nosql_sinks.get(id(worker)) if worker is not None else None
        if entry is None or entry[0] is not worker:
            return
        sink = entry[1]
        if sink.terminal_seen:
            return
        sink.terminal_seen = True
        if type(kind) is not str or kind != sink.action:
            self._save_nosql_terminal(sink, "UNKNOWN")
            return
        row_count = None
        if sink.provider == "mongo" and sink.action == "query" and type(payload) is dict:
            row_count = exact_nonnegative_count(payload.get("row_count"))
        self._save_nosql_terminal(sink, "COMPLETED", row_count=row_count)

    @pyqtSlot(str, str)
    def _on_nosql_failed(self, kind: str, _safe_message: str) -> None:
        worker = self.sender()
        entry = self._nosql_sinks.get(id(worker)) if worker is not None else None
        if entry is None or entry[0] is not worker:
            return
        sink = entry[1]
        if sink.terminal_seen:
            return
        sink.terminal_seen = True
        state = "FAILED" if type(kind) is str and kind == sink.action else "UNKNOWN"
        self._save_nosql_terminal(sink, state)

    @pyqtSlot()
    def _on_nosql_finished(self) -> None:
        worker = self.sender()
        entry = self._nosql_sinks.pop(id(worker), None) if worker is not None else None
        if entry is None or entry[0] is not worker:
            return
        sink = entry[1]
        if not sink.terminal_seen:
            self._save_nosql_terminal(sink, "UNKNOWN")

    def _save_nosql_terminal(
        self,
        sink: _NoSqlSink,
        state: str,
        *,
        row_count: int | None = None,
    ) -> None:
        try:
            self.save_capture(sink.capture, state, row_count=row_count)
        except Exception:
            # A history write must never change the already-dispatched DB result.
            self.save_failed.emit("SAVE_FAILED")

    @pyqtSlot(object)
    def _on_manual_completion_ready(self, completion: Any) -> None:
        if not isinstance(completion, _ManualHistoryCompletion):
            return
        pending = self._manual_pending.get(completion.key)
        if pending is None:
            return
        capture, target_tab_id, generation, expected_query_id = pending
        matching = (
            completion.result_tab_id == target_tab_id
            and completion.result_generation == generation
            and (not expected_query_id or completion.query_id == expected_query_id)
        )
        state = completion.state_code if matching else "UNKNOWN"
        row_count = completion.row_count if matching else None
        # Queue persistence before releasing the drain barrier. save_history
        # only enqueues on the UI-owned controller; its serial signal order
        # keeps this request ahead of the subsequent stop request.
        self.save_capture(capture, state, row_count=row_count)
        self._manual_pending.pop(completion.key, None)


_APPLICATION_HISTORY_SERVICE: DataCenterHistoryService | None = None


def get_application_history_service() -> DataCenterHistoryService:
    """Return the lazily created process-wide service; construction is I/O-free."""

    global _APPLICATION_HISTORY_SERVICE
    if _APPLICATION_HISTORY_SERVICE is None:
        _APPLICATION_HISTORY_SERVICE = DataCenterHistoryService()
    return _APPLICATION_HISTORY_SERVICE


def drain_existing_application_history_service() -> bool:
    """Drain the service only if a product route already created it."""

    service = _APPLICATION_HISTORY_SERVICE
    return service.drain() if service is not None else True


def application_history_cleanup_error_code() -> str:
    """Return a safe pending-cleanup code without creating the service."""

    service = _APPLICATION_HISTORY_SERVICE
    return service.cleanup_error_code if service is not None else ""


def reset_application_history_service_for_tests() -> None:
    """Forget only an already-drained singleton; intended for isolated tests."""

    global _APPLICATION_HISTORY_SERVICE
    service = _APPLICATION_HISTORY_SERVICE
    if service is not None and not service._store_closed:
        raise RuntimeError("cannot reset an undrained history service")
    _APPLICATION_HISTORY_SERVICE = None


__all__ = [
    "DataCenterHistoryService",
    "HistoryCapture",
    "application_history_cleanup_error_code",
    "RELATIONAL_PROVIDERS",
    "capture_legacy_relational_history",
    "drain_existing_application_history_service",
    "exact_nonnegative_count",
    "get_application_history_service",
    "legacy_result_summary",
    "make_history_capture",
]
