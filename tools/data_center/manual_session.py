"""Owner-thread manual transaction sessions for relational data-center tabs.

This module is an injected DBAPI boundary. It does not load connection profiles,
credentials, Qt, or drivers. A caller supplies a target-bound driver factory;
the factory and every connection/cursor operation run on one persistent owner
thread for the lifetime of this tab session.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum
import re
import sys
import threading
import uuid
from typing import Any, Protocol, TypeAlias

from .workspace_store import MAX_TEXT_BYTES as MAX_SCRIPT_BYTES


PAGE_SIZES = frozenset({50, 200, 500})
DEFAULT_PAGE_SIZE = 200
MAX_RESULT_BYTES = 50 * 1024 * 1024
MAX_RETAINED_RESULT_PAGES = 100_000
MAX_PENDING_PREFETCH_ROWS = max(PAGE_SIZES) + 1
MAX_PENDING_PREFETCH_BYTES = MAX_RESULT_BYTES
MAX_SCRIPT_STATEMENTS = 256


class ManualSessionState(str, Enum):
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    COMMITTING = "COMMITTING"
    ROLLING_BACK = "ROLLING_BACK"
    UNKNOWN = "UNKNOWN"
    DISCONNECTED = "DISCONNECTED"
    CANCEL_PENDING = "CANCEL_PENDING"


class ManualResultSourceState(str, Enum):
    NONE = "NONE"
    AVAILABLE = "AVAILABLE"
    BUDGET_LIMITED = "BUDGET_LIMITED"
    EXPORTING = "EXPORTING"
    COMPLETE = "COMPLETE"
    EXPORTED = "EXPORTED"
    UNAVAILABLE = "UNAVAILABLE"


class StatementKind(str, Enum):
    QUERY = "query"
    DML = "dml"
    DDL = "ddl"
    DCL = "dcl"
    OTHER = "other"
    PROCEDURAL = "procedural"
    TRANSACTION_CONTROL = "transaction_control"
    MULTI_STATEMENT = "multi_statement"


class TransactionEffect(str, Enum):
    """Explicit transaction impact supplied by a vendor-aware classifier."""

    NO_CHANGE = "no_change"
    DIRTY = "dirty"
    IMPLICIT_COMMIT = "implicit_commit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StatementClassification:
    """Statement class plus its transaction impact; never infer this from is_read."""

    kind: StatementKind
    effect: TransactionEffect

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StatementKind):
            object.__setattr__(self, "kind", StatementKind(str(self.kind).strip().lower()))
        if not isinstance(self.effect, TransactionEffect):
            object.__setattr__(self, "effect", TransactionEffect(str(self.effect).strip().lower()))


class DdlTransactionBehavior(str, Enum):
    TRANSACTIONAL = "transactional"
    IMPLICIT_COMMIT = "implicit_commit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ManualSessionTarget:
    """Non-secret identity pinned to one manual tab for its entire lifetime."""

    tab_id: str
    connection_id: str
    profile_revision: str
    database: str = ""
    schema: str = ""
    generation: int = 1

    def __post_init__(self) -> None:
        for name in ("tab_id", "connection_id", "profile_revision"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "database", str(self.database or ""))
        object.__setattr__(self, "schema", str(self.schema or ""))
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("generation must be a positive integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "tab_id": self.tab_id,
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "database": self.database,
            "schema": self.schema,
            "generation": self.generation,
        }


class ManualSessionError(RuntimeError):
    """Stable, non-sensitive error returned through a Future."""

    def __init__(self, code: str):
        self.code = str(code or "MANUAL_SESSION_FAILED")
        super().__init__(self.code)


class ManualCursor(Protocol):
    @property
    def description(self) -> Any: ...

    @property
    def rowcount(self) -> Any: ...

    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...

    def close(self) -> Any: ...


class ManualDriver(Protocol):
    """Per-tab adapter around a connection and its driver-specific semantics."""

    def configure_manual_commit(self) -> Any:
        """Disable/verify autocommit before the first statement."""

    # Adapters may additionally expose a non-blocking, explicitly
    # thread-safe ``request_interrupt()`` hook.  The session only calls that
    # hook from cancel_current(); every ordinary DBAPI operation remains on
    # the owner thread.  The hook must never close/release the connection.

    def execute(self, sql: str, parameters: Any) -> ManualCursor: ...

    def commit(self) -> Any: ...

    def rollback(self) -> Any: ...

    def close(self) -> Any: ...

    def ddl_transaction_behavior(self, sql: str) -> DdlTransactionBehavior: ...

    def create_savepoint(self, name: str) -> Any: ...

    def rollback_to_savepoint(self, name: str) -> Any: ...

    def release_savepoint(self, name: str) -> Any: ...


DriverFactory: TypeAlias = Callable[[ManualSessionTarget], ManualDriver]
SqlClassifier: TypeAlias = Callable[[str], StatementClassification]


@dataclass(frozen=True, slots=True)
class ManualColumn:
    name: str
    type_code: Any = None
    display_size: Any = None
    internal_size: Any = None
    precision: Any = None
    scale: Any = None
    null_ok: Any = None


@dataclass(frozen=True, slots=True)
class ManualQueryPage:
    query_id: str
    tab_id: str
    generation: int
    columns: tuple[ManualColumn, ...]
    rows: tuple[tuple[Any, ...], ...]
    page_number: int
    page_size: int
    has_more: bool
    result_bytes: int
    truncated_by_budget: bool = False
    rowcount: Any = None


@dataclass(frozen=True, slots=True)
class ManualStatementResult:
    query_id: str
    tab_id: str
    generation: int
    kind: StatementKind
    rowcount: Any
    state: ManualSessionState
    page: ManualQueryPage | None = None
    ddl_behavior: DdlTransactionBehavior | None = None


@dataclass(frozen=True, slots=True)
class ManualScriptEntry:
    sql: str
    result: ManualQueryPage | ManualStatementResult | None = None
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class ManualScriptResult:
    query_id: str
    tab_id: str
    generation: int
    entries: tuple[ManualScriptEntry, ...]
    error_code: str = ""

    @property
    def completed_count(self) -> int:
        return sum(entry.result is not None for entry in self.entries)


@dataclass(frozen=True, slots=True)
class ManualTransactionResult:
    tab_id: str
    generation: int
    state: ManualSessionState
    action: str
    applied: bool
    outcome_known: bool


@dataclass(frozen=True, slots=True)
class ManualBatchResult:
    tab_id: str
    generation: int
    savepoint: str
    succeeded: bool
    statements_completed: int
    rowcounts: tuple[Any, ...]
    state: ManualSessionState
    error_code: str = ""
    conflict_index: int | None = None
    expected_rowcount: int | None = None
    actual_rowcount: Any = None


@dataclass(frozen=True, slots=True)
class ManualCloseResult:
    tab_id: str
    generation: int
    closed: bool
    state: ManualSessionState
    transaction_outcome_unknown: bool


@dataclass(frozen=True, slots=True)
class ManualCleanupStatus:
    tab_id: str
    generation: int
    state: ManualSessionState
    owner_alive: bool
    busy: bool
    queued_operations: int
    connection_retained: bool
    transaction_outcome_unknown: bool
    cleanup_complete: bool


@dataclass(frozen=True, slots=True)
class ManualResultSourceStatus:
    """Lock-safe snapshot of whether the current typed rowset can be used."""

    query_id: str
    tab_id: str
    generation: int
    state: ManualResultSourceState
    can_continue: bool
    can_export_full: bool
    cursor_open: bool
    page_count: int
    retained_rows: int
    retained_bytes: int
    pending_rows: int
    pending_bytes: int
    reason_code: str = ""


@dataclass(slots=True)
class _Job:
    operation: str
    arguments: tuple[Any, ...]
    future: Future


class ManualOperationFuture(Future):
    """A queued operation keeps its captured session identity on errors too."""
    def __init__(self, target: ManualSessionTarget):
        super().__init__()
        self._manual_target = target

    @property
    def manual_target(self) -> ManualSessionTarget:
        return self._manual_target


def _completed_future(*, result: Any = None, error: BaseException | None = None) -> Future:
    future: Future = Future()
    if error is None:
        future.set_result(result)
    else:
        future.set_exception(error)
    return future


def _mask_comments_and_literals(sql: str) -> str:
    from .sql_script import scan_sql

    return scan_sql(sql).code


_TX_CONTROL_AT_BOUNDARY = re.compile(
    r"(?:^|;)\s*(?:COMMIT\b|ROLLBACK\b|BEGIN(?:\s+(?:TRANSACTION|TRAN))?\b|"
    r"START\s+TRANSACTION\b|SAVEPOINT\b|SAVE\s+TRANSACTION\b|RELEASE\b|"
    r"PREPARE\s+TRANSACTION\b|SET\s+(?:(?:SESSION|LOCAL|GLOBAL)\s+)?"
    r"(?:@@(?:SESSION\.)?)?AUTOCOMMIT\b|SET\s+TRANSACTION\b)",
    re.IGNORECASE,
)


def _contains_transaction_control(sql: str) -> bool:
    from .sql_script import SqlScriptError

    try:
        return bool(_TX_CONTROL_AT_BOUNDARY.search(_mask_comments_and_literals(sql)))
    except SqlScriptError:
        return True


def _estimate_value_bytes(value: Any, seen: set[int] | None = None) -> int:
    """Bound retained Python row size without stringifying typed DB values."""

    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    try:
        size = sys.getsizeof(value)
    except Exception:
        size = 64
    if isinstance(value, Mapping):
        size += sum(_estimate_value_bytes(key, seen) + _estimate_value_bytes(item, seen) for key, item in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_estimate_value_bytes(item, seen) for item in value)
    return size


def _column_from_description(item: Any) -> ManualColumn:
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
        values = list(item)
    else:
        names = ("name", "type_code", "display_size", "internal_size", "precision", "scale", "null_ok")
        values = [getattr(item, name, None) for name in names]
    values.extend([None] * (7 - len(values)))
    return ManualColumn(
        name=str(values[0] or ""),
        type_code=values[1],
        display_size=values[2],
        internal_size=values[3],
        precision=values[4],
        scale=values[5],
        null_ok=values[6],
    )


class ManualSession:
    """One persistent owner-thread database session bound to a manual tab."""

    def __init__(
        self,
        target: ManualSessionTarget,
        driver_factory: DriverFactory,
        classify_sql: SqlClassifier,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        result_budget_bytes: int = MAX_RESULT_BYTES,
    ) -> None:
        if not isinstance(target, ManualSessionTarget):
            raise TypeError("target must be ManualSessionTarget")
        if not callable(driver_factory) or not callable(classify_sql):
            raise TypeError("driver_factory and classify_sql must be callable")
        if page_size not in PAGE_SIZES:
            raise ValueError("page_size must be 50, 200, or 500")
        if isinstance(result_budget_bytes, bool) or not isinstance(result_budget_bytes, int):
            raise ValueError("result_budget_bytes must be an integer")
        if not 1 <= result_budget_bytes <= MAX_RESULT_BYTES:
            raise ValueError("result_budget_bytes must be between 1 byte and 50 MiB")
        self.target = target
        self._driver_factory = driver_factory
        self._classify_sql = classify_sql
        self._default_page_size = page_size
        self._result_budget = result_budget_bytes
        self._active_result_budget = result_budget_bytes

        self._condition = threading.Condition(threading.RLock())
        self._jobs: deque[_Job] = deque()
        self._owner: threading.Thread | None = None
        self._owner_ident: int | None = None
        self._active_job: _Job | None = None
        self._driver: ManualDriver | None = None
        self._connected = False
        self._cursor: ManualCursor | None = None
        self._columns: tuple[ManualColumn, ...] = ()
        self._lookahead: tuple[tuple[Any, ...], int] | None = None
        self._pending_rows: deque[tuple[tuple[Any, ...], int]] = deque()
        self._pending_prefetch_bytes = 0
        self._issued_pages: list[ManualQueryPage] = []
        self._retained_rows = 0
        self._last_page: ManualQueryPage | None = None
        self._query_id = ""
        self._page_number = 0
        self._page_size = page_size
        self._result_bytes = 0
        self._paging_budget_blocked = False
        self._result_source_state = ManualResultSourceState.NONE
        self._result_source_reason = ""
        self._result_source_cursor_open = False
        self._result_source_status = ManualResultSourceStatus(
            query_id="",
            tab_id=target.tab_id,
            generation=target.generation,
            state=ManualResultSourceState.NONE,
            can_continue=False,
            can_export_full=False,
            cursor_open=False,
            page_count=0,
            retained_rows=0,
            retained_bytes=0,
            pending_rows=0,
            pending_bytes=0,
        )
        self._rowcount: Any = None
        self._transaction_state = ManualSessionState.DISCONNECTED
        self._transaction_outcome_unknown = False
        self._cancel_requested = threading.Event()
        self._closing = False
        self._closed = False
        self._stop_owner = False

    def __repr__(self) -> str:
        return f"ManualSession(tab_id={self.target.tab_id!r}, state={self.state.value!r})"

    @property
    def state(self) -> ManualSessionState:
        with self._condition:
            if self._cancel_requested.is_set() and self._active_job is not None:
                return ManualSessionState.CANCEL_PENDING
            return self._transaction_state

    @property
    def last_page(self) -> ManualQueryPage | None:
        with self._condition:
            return self._last_page

    @property
    def result_source_status(self) -> ManualResultSourceStatus:
        """Return the latest immutable status snapshot without touching the DB."""

        with self._condition:
            return self._result_source_status

    def _enqueue(self, operation: str, *arguments: Any) -> Future:
        future: Future = ManualOperationFuture(self.target)
        with self._condition:
            if self._closed or self._closing:
                future.set_exception(ManualSessionError("SESSION_CLOSING"))
                return future
            if self._cancel_requested.is_set():
                future.set_exception(ManualSessionError("CANCEL_PENDING"))
                return future
            job = _Job(operation, tuple(arguments), future)
            self._jobs.append(job)
            if self._owner is None:
                self._owner = threading.Thread(
                    target=self._owner_loop,
                    name=f"manual-db-owner-{self.target.tab_id}",
                    daemon=True,
                )
                self._owner.start()
            self._condition.notify_all()
        return future

    @staticmethod
    def _copy_parameters(parameters: Any) -> Any:
        if parameters is None:
            return ()
        if isinstance(parameters, (Mapping, tuple, list)):
            try:
                return deepcopy(parameters)
            except Exception as error:
                raise ManualSessionError("PARAMETERS_NOT_COPYABLE") from error
        raise ManualSessionError("PARAMETERS_MUST_BE_MAPPING_OR_SEQUENCE")

    @staticmethod
    def _validate_page_size(page_size: int) -> int:
        if page_size not in PAGE_SIZES:
            raise ManualSessionError("INVALID_PAGE_SIZE")
        return int(page_size)

    def submit(
        self,
        sql: str,
        parameters: Any = None,
        *,
        ddl_authorized: bool = False,
        dcl_authorized: bool = False,
        page_size: int | None = None,
    ) -> Future:
        try:
            sql_text = str(sql or "")
            copied = self._copy_parameters(parameters)
            size = self._validate_page_size(self._default_page_size if page_size is None else page_size)
        except ManualSessionError as error:
            return _completed_future(error=error)
        return self._enqueue(
            "submit", sql_text, copied, bool(ddl_authorized), bool(dcl_authorized), size
        )

    def query(
        self,
        sql: str,
        parameters: Any = None,
        *,
        page_size: int | None = None,
    ) -> Future:
        try:
            sql_text = str(sql or "")
            copied = self._copy_parameters(parameters)
            size = self._validate_page_size(self._default_page_size if page_size is None else page_size)
        except ManualSessionError as error:
            return _completed_future(error=error)
        return self._enqueue("query", sql_text, copied, False, False, size)

    def fetch_next_page(self, page_size: int | None = None) -> Future:
        try:
            size = self._validate_page_size(self._default_page_size if page_size is None else page_size)
        except ManualSessionError as error:
            return _completed_future(error=error)
        return self._enqueue("fetch_next_page", size)

    def run_script(
        self,
        statements: Sequence[str],
        *,
        page_size: int | None = None,
        ddl_authorized: bool = False,
        dcl_authorized: bool = False,
    ) -> Future:
        """Execute an explicitly reviewed script as one owner job, stopping on failure.

        Completed statements stay in the current transaction. This operation
        never adds a commit, rollback, savepoint or statement replay.
        """
        try:
            if not isinstance(statements, (tuple, list)) or not 1 <= len(statements) <= MAX_SCRIPT_STATEMENTS:
                raise ManualSessionError("SCRIPT_STATEMENT_LIMIT")
            copied = tuple(statements)
            if any(type(sql) is not str for sql in copied) or sum(len(sql.encode("utf-8")) for sql in copied) > MAX_SCRIPT_BYTES:
                raise ManualSessionError("SCRIPT_TEXT_LIMIT")
            size = self._validate_page_size(self._default_page_size if page_size is None else page_size)
            from .sql_script import has_procedural_block, scan_sql

            for sql in copied:
                if has_procedural_block(scan_sql(sql).code):
                    raise ManualSessionError("SCRIPT_PROCEDURAL_BLOCK_UNSUPPORTED")
                classification = self._classify(sql)
                if classification.kind not in {StatementKind.QUERY, StatementKind.DML, StatementKind.DDL, StatementKind.DCL}:
                    raise ManualSessionError("SCRIPT_STATEMENT_UNSUPPORTED")
                if classification.kind is StatementKind.DDL and not ddl_authorized:
                    raise ManualSessionError("DDL_CONFIRMATION_REQUIRED")
                if classification.kind is StatementKind.DCL and not dcl_authorized:
                    raise ManualSessionError("DCL_CONFIRMATION_REQUIRED")
        except (ManualSessionError, ValueError, TypeError) as error:
            code = error.code if isinstance(error, ManualSessionError) else "SCRIPT_INVALID"
            return _completed_future(error=ManualSessionError(code))
        return self._enqueue("script", copied, size, ddl_authorized, dcl_authorized)

    def execute_block(self, sql: str, *, provider: str, authorized: bool = False) -> Future:
        """Explicit human-only complete-block path, separate from query tools."""
        try:
            if authorized is not True:
                raise ManualSessionError("BLOCK_CONFIRMATION_REQUIRED")
            if type(sql) is not str or len(sql.encode("utf-8")) > MAX_SCRIPT_BYTES:
                raise ManualSessionError("BLOCK_TEXT_LIMIT")
            from .sql_script import selected_procedural_sql

            text = selected_procedural_sql(sql, provider)
            if text is None:
                raise ManualSessionError("BLOCK_BODY_REQUIRED")
        except (ManualSessionError, ValueError, TypeError) as error:
            code = error.code if isinstance(error, ManualSessionError) else "BLOCK_INVALID"
            return _completed_future(error=ManualSessionError(code))
        return self._enqueue("block", text, provider)

    def open_table_source(
        self,
        tree_service: Any,
        tree_target: Any,
        node: Any,
        *,
        tree_generation: int,
        choice: Any,
        page_size: int | None = None,
        query: Any = None,
    ) -> Future:
        """Queue one signed object-tree table/view open on this tab's owner.

        The signed tree generation is validated only by ObjectTreeService;
        the complete captured ManualSessionTarget is separately rebound to
        the supplied immutable connection choice before queueing and again by
        the owner immediately before metadata I/O.
        """

        try:
            from .changeset_dialects import create_changeset_dialect
            from .object_tree import ObjectTreeError
            from .table_source import TableQuerySpec, TableSourceError, validate_table_query, validate_table_target

            resolver = getattr(tree_service, "resolve_table_source", None)
            if not callable(resolver):
                raise TableSourceError("INVALID_TABLE_REF")
            ref = resolver(tree_target, node, generation=tree_generation)
            validate_table_target(ref, tree_target, self.target, choice)
            create_changeset_dialect(choice, self.target)
            size = self._validate_page_size(self._default_page_size if page_size is None else page_size)
            query = validate_table_query(TableQuerySpec() if query is None else query)
        except Exception as error:
            from .changeset import ChangeSetError
            from .object_tree import ObjectTreeError
            from .table_source import TableSourceError

            if isinstance(error, ManualSessionError):
                code = error.code
            elif isinstance(error, ObjectTreeError):
                code = "STALE_TABLE_REF" if error.code == "STALE_NODE" else "TABLE_TARGET_MISMATCH"
            elif isinstance(error, TableSourceError):
                code = error.code
            elif isinstance(error, ChangeSetError):
                code = "UNSUPPORTED_TABLE_DIALECT" if error.code == "UNSUPPORTED_SQL_DIALECT" else "TABLE_TARGET_MISMATCH"
            else:
                code = "INVALID_TABLE_SOURCE_REQUEST"
            return _completed_future(error=ManualSessionError(code))
        return self._enqueue(
            "open_table_source",
            tree_service,
            tree_target,
            node,
            tree_generation,
            self.target,
            choice,
            size,
            query,
        )

    def export_full_query(
        self,
        destination: str,
        format: str,
        target: Any,
        *,
        overwrite: bool = False,
        limits: Any = None,
    ) -> Future:
        """Queue a full export of this session's current typed result source.

        The target is revalidated when the owner dispatches the job. ``limits``
        defaults to the frozen writer's standard limits; request construction
        performs no filesystem or database I/O. Once the owner starts the
        export, its Future is running and ``Future.cancel()`` returns False;
        use ``cancel_current()`` to request cooperative cancellation.
        """

        try:
            from .manual_result_export import make_full_query_request

            request_args = {"session_target": self.target, "overwrite": overwrite}
            if limits is not None:
                request_args["limits"] = limits
            request = make_full_query_request(destination, format, target, **request_args)
        except Exception as error:
            from .result_export import ExportError

            code = error.code if isinstance(error, ExportError) else "INVALID_REQUEST"
            return _completed_future(error=ManualSessionError(f"RESULT_EXPORT_{code}"))
        return self._enqueue("export_full_query", request)

    def commit(self) -> Future:
        return self._enqueue("commit")

    def rollback(self) -> Future:
        return self._enqueue("rollback")

    def run_savepoint_batch(
        self,
        statements: Sequence[tuple[str, Any]],
        *,
        expected_rowcounts: Sequence[int] | None = None,
    ) -> Future:
        """Run DML inside one savepoint and optionally require exact rowcounts.

        A mismatch is returned as a failed ``ManualBatchResult`` only after the
        savepoint rollback succeeds. Unknown DBAPI rowcounts never satisfy an
        explicit expected count.
        """
        try:
            copied = tuple((str(sql or ""), self._copy_parameters(parameters)) for sql, parameters in statements)
            expected = None if expected_rowcounts is None else tuple(expected_rowcounts)
        except (ManualSessionError, TypeError, ValueError) as error:
            code = error.code if isinstance(error, ManualSessionError) else "INVALID_BATCH"
            return _completed_future(error=ManualSessionError(code))
        if not copied:
            return _completed_future(error=ManualSessionError("EMPTY_BATCH"))
        if expected is not None and len(expected) != len(copied):
            return _completed_future(error=ManualSessionError("EXPECTED_ROWCOUNT_LENGTH_MISMATCH"))
        if expected is not None and any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in expected
        ):
            return _completed_future(error=ManualSessionError("INVALID_EXPECTED_ROWCOUNT"))
        normalized_expected = (None,) * len(copied) if expected is None else expected
        return self._enqueue("savepoint_batch", copied, normalized_expected)

    def cancel_current(self) -> bool:
        """Stop queued work and mark a running operation cancel-pending.

        No connection method is called from the caller thread. If a driver is
        blocked in execute/fetch, its owner and connection remain alive until
        that call actually returns.
        """

        interrupt = None
        cancelled_futures: list[Future] = []
        with self._condition:
            if self._closed or self._closing:
                return False
            active = self._active_job is not None
            if active:
                self._cancel_requested.set()
                interrupt = getattr(self._driver, "request_interrupt", None)
            while self._jobs:
                job = self._jobs.popleft()
                if not job.future.done():
                    cancelled_futures.append(job.future)
            self._condition.notify_all()
        for future in cancelled_futures:
            if not future.done():
                future.set_exception(ManualSessionError("CANCELLED_BEFORE_START"))
        if callable(interrupt):
            try:
                # This optional hook is an out-of-band interrupt contract,
                # never a substitute for waiting for the owner-thread call.
                interrupt()
            except Exception:
                pass
        return active

    def close(self, *, disposition: str | None = None) -> Future:
        action = str(disposition or "").strip().lower()
        if action not in {"", "commit", "rollback", "cancel"}:
            return _completed_future(error=ManualSessionError("INVALID_CLOSE_DISPOSITION"))
        future: Future = Future()
        interrupt = None
        cancelled_futures: list[Future] = []
        with self._condition:
            if self._closed:
                return _completed_future(result=ManualCloseResult(
                    self.target.tab_id,
                    self.target.generation,
                    True,
                    self._transaction_state,
                    self._transaction_outcome_unknown,
                ))
            if self._closing:
                future.set_exception(ManualSessionError("SESSION_CLOSING"))
                return future
            self._closing = True
            if action == "cancel" and self._active_job is not None:
                self._cancel_requested.set()
                interrupt = getattr(self._driver, "request_interrupt", None)
            while self._jobs:
                queued = self._jobs.popleft()
                if not queued.future.done():
                    cancelled_futures.append(queued.future)
            self._jobs.append(_Job("close", (action,), future))
            if self._owner is None:
                self._owner = threading.Thread(
                    target=self._owner_loop,
                    name=f"manual-db-owner-{self.target.tab_id}",
                    daemon=True,
                )
                self._owner.start()
            self._condition.notify_all()
        for queued_future in cancelled_futures:
            if not queued_future.done():
                queued_future.set_exception(ManualSessionError("CANCELLED_BY_CLOSE"))
        if callable(interrupt):
            try:
                interrupt()
            except Exception:
                pass
        return future

    def poll_cleanup(self) -> ManualCleanupStatus:
        with self._condition:
            owner_alive = bool(self._owner and self._owner.is_alive())
            active = self._active_job is not None
            state = (
                ManualSessionState.CANCEL_PENDING
                if self._cancel_requested.is_set() and active
                else self._transaction_state
            )
            return ManualCleanupStatus(
                tab_id=self.target.tab_id,
                generation=self.target.generation,
                state=state,
                owner_alive=owner_alive,
                busy=active,
                queued_operations=len(self._jobs),
                connection_retained=self._driver is not None,
                transaction_outcome_unknown=self._transaction_outcome_unknown,
                cleanup_complete=self._closed and not owner_alive and self._driver is None,
            )

    def _publish_result_source_status(
        self,
        state: ManualResultSourceState | None = None,
        reason_code: str | None = None,
    ) -> None:
        if state is not None:
            self._result_source_state = state
        if reason_code is not None:
            self._result_source_reason = reason_code
        current_state = self._result_source_state
        can_export = current_state in {
            ManualResultSourceState.AVAILABLE,
            ManualResultSourceState.BUDGET_LIMITED,
            ManualResultSourceState.COMPLETE,
        } and bool(self._issued_pages)
        can_continue = (
            current_state is ManualResultSourceState.AVAILABLE
            and self._result_source_cursor_open
            and not self._paging_budget_blocked
        )
        with self._condition:
            self._result_source_status = ManualResultSourceStatus(
                query_id=self._query_id,
                tab_id=self.target.tab_id,
                generation=self.target.generation,
                state=current_state,
                can_continue=can_continue,
                can_export_full=can_export,
                cursor_open=self._result_source_cursor_open,
                page_count=len(self._issued_pages),
                retained_rows=self._retained_rows,
                retained_bytes=self._result_bytes,
                pending_rows=len(self._pending_rows),
                pending_bytes=self._pending_prefetch_bytes,
                reason_code=self._result_source_reason,
            )

    def _clear_result_source_pages(self) -> None:
        self._issued_pages.clear()
        self._retained_rows = 0
        self._pending_rows.clear()
        self._pending_prefetch_bytes = 0
        self._lookahead = None

    def _invalidate_result_source(self, reason_code: str) -> None:
        self._clear_result_source_pages()
        self._paging_budget_blocked = False
        self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, reason_code)

    def _invalidate_incomplete_result_source(self, reason_code: str) -> None:
        if self._result_source_state in {
            ManualResultSourceState.AVAILABLE,
            ManualResultSourceState.BUDGET_LIMITED,
            ManualResultSourceState.EXPORTING,
        }:
            self._invalidate_result_source(reason_code)

    def _append_issued_page(self, page: ManualQueryPage) -> bool:
        if len(self._issued_pages) >= MAX_RETAINED_RESULT_PAGES:
            self._invalidate_result_source("PAGE_HISTORY_LIMIT")
            return False
        self._issued_pages.append(page)
        self._retained_rows += len(page.rows)
        return True

    def _set_transaction_state(self, state: ManualSessionState) -> None:
        with self._condition:
            self._transaction_state = state

    def _owner_loop(self) -> None:
        self._owner_ident = threading.get_ident()
        while True:
            with self._condition:
                while not self._jobs and not self._stop_owner:
                    self._condition.wait()
                if self._stop_owner and not self._jobs:
                    return
                job = self._jobs.popleft()
                self._active_job = job
            result = None
            error: BaseException | None = None
            try:
                # Long-running owner operations become observably RUNNING
                # before their first DB call. Future.cancel() therefore only
                # cancels them while queued, never as proof their driver ended.
                should_run = (
                    job.future.set_running_or_notify_cancel()
                    if job.operation in {"export_full_query", "open_table_source", "close"}
                    else not job.future.cancelled()
                )
                if should_run:
                    if self._cancel_requested.is_set() and job.operation not in {"commit", "rollback", "close"}:
                        raise ManualSessionError("CANCELLED")
                    result = self._dispatch(job.operation, *job.arguments)
            except ManualSessionError as exc:
                error = exc
            except Exception:
                error = ManualSessionError("MANUAL_SESSION_FAILED")
            with self._condition:
                self._active_job = None
                self._cancel_requested.clear()
                if job.operation == "close" and (error is not None or not should_run):
                    self._closing = False
                self._condition.notify_all()
            if not job.future.done():
                if error is None:
                    job.future.set_result(result)
                else:
                    job.future.set_exception(error)
            if job.operation == "close" and error is None and result and result.closed:
                with self._condition:
                    self._stop_owner = True
                    self._condition.notify_all()

    def _dispatch(self, operation: str, *arguments: Any) -> Any:
        if operation == "submit":
            return self._execute_statement(*arguments, expect_query=False)
        if operation == "query":
            return self._execute_statement(*arguments, expect_query=True)
        if operation == "fetch_next_page":
            return self._fetch_next_page_owner(*arguments)
        if operation == "export_full_query":
            return self._export_full_query_owner(*arguments)
        if operation == "open_table_source":
            return self._open_table_source_owner(*arguments)
        if operation == "commit":
            return self._commit_owner()
        if operation == "rollback":
            return self._rollback_owner()
        if operation == "savepoint_batch":
            return self._savepoint_batch_owner(*arguments)
        if operation == "script":
            return self._script_owner(*arguments)
        if operation == "block":
            return self._block_owner(*arguments)
        if operation == "close":
            return self._close_owner(*arguments)
        raise ManualSessionError("UNKNOWN_OPERATION")

    def _block_owner(self, sql: str, provider: str) -> ManualStatementResult:
        if self._transaction_state not in {ManualSessionState.DISCONNECTED, ManualSessionState.CLEAN}:
            raise ManualSessionError("BLOCK_REQUIRES_CLEAN_TRANSACTION")
        if self._cancel_requested.is_set():
            raise ManualSessionError("CANCELLED")
        driver = self._ensure_connected_owner()
        execute = getattr(driver, "execute_block", None)
        if not callable(execute):
            raise ManualSessionError("BLOCK_EXECUTION_UNAVAILABLE")
        validate = getattr(driver, "validate_block", None)
        if callable(validate):
            validate(sql, provider)
        self._invalidate_result_source("SOURCE_REPLACED")
        self._close_cursor_owner()
        self._mark_unknown()
        try:
            rowcount = execute(sql, provider)
        except Exception as error:
            raise ManualSessionError("BLOCK_EXECUTION_FAILED") from error
        probe = getattr(driver, "block_transaction_state", None)
        try:
            state = probe() if callable(probe) else ManualSessionState.UNKNOWN
        except Exception:
            state = ManualSessionState.UNKNOWN
        if type(state) is ManualSessionState and state in {ManualSessionState.CLEAN, ManualSessionState.DIRTY}:
            with self._condition:
                self._transaction_outcome_unknown = False
            self._set_transaction_state(state)
        if self._cancel_requested.is_set():
            raise ManualSessionError("CANCELLED")
        return ManualStatementResult(uuid.uuid4().hex, self.target.tab_id, self.target.generation, StatementKind.PROCEDURAL, rowcount, self._transaction_state)

    def _script_owner(self, statements: tuple[str, ...], page_size: int, ddl_authorized: bool, dcl_authorized: bool) -> ManualScriptResult:
        entries = []
        reserved_bytes = 0
        error_code = ""
        for sql in statements:
            if error_code:
                entries.append(ManualScriptEntry(sql, error_code="SKIPPED"))
                continue
            try:
                if self._cancel_requested.is_set():
                    raise ManualSessionError("CANCELLED")
                result = self._execute_statement(
                    sql, (), ddl_authorized, dcl_authorized, page_size,
                    expect_query=False,
                    retained_budget=max(0, self._result_budget - reserved_bytes),
                )
                page = result if isinstance(result, ManualQueryPage) else result.page
                if page is not None:
                    reserved_bytes += page.result_bytes
                entries.append(ManualScriptEntry(sql, result))
            except Exception as error:
                error_code = error.code if isinstance(error, ManualSessionError) else "MANUAL_SESSION_FAILED"
                entries.append(ManualScriptEntry(sql, error_code=error_code))
        return ManualScriptResult(uuid.uuid4().hex, self.target.tab_id, self.target.generation, tuple(entries), error_code)

    def _open_table_source_owner(
        self,
        tree_service: Any,
        tree_target: Any,
        node: Any,
        tree_generation: int,
        expected_target: ManualSessionTarget,
        choice: Any,
        page_size: int,
        query: Any,
    ) -> Any:
        from .changeset_dialects import create_changeset_dialect
        from .object_tree import ObjectTreeError
        from .table_source import (
            TableSourceError,
            TableSourceOpenResult,
            build_table_query,
            inspect_table,
            validate_table_target,
        )

        if expected_target != self.target:
            raise ManualSessionError("TABLE_TARGET_MISMATCH")
        if self._cancel_requested.is_set():
            raise ManualSessionError("CANCELLED")
        try:
            ref = tree_service.resolve_table_source(tree_target, node, generation=tree_generation)
            validate_table_target(ref, tree_target, expected_target, choice)
            dialect = create_changeset_dialect(choice, expected_target)
        except ObjectTreeError as error:
            code = "STALE_TABLE_REF" if error.code == "STALE_NODE" else "TABLE_TARGET_MISMATCH"
            raise ManualSessionError(code) from None
        except TableSourceError as error:
            raise ManualSessionError(error.code) from None
        except Exception as error:
            from .changeset import ChangeSetError

            code = "UNSUPPORTED_TABLE_DIALECT" if isinstance(error, ChangeSetError) and error.code == "UNSUPPORTED_SQL_DIALECT" else "TABLE_TARGET_MISMATCH"
            raise ManualSessionError(code) from None

        # Opening a new table source replaces the current query source just as
        # a manual query does. Close its live cursor before catalog queries so
        # drivers never have overlapping results on this connection.
        self._invalidate_result_source("SOURCE_REPLACED")
        self._close_cursor_owner()
        driver = self._ensure_connected_owner()
        try:
            metadata = inspect_table(driver, ref, expected_target, choice, dialect, self._cancel_requested)
            if self._cancel_requested.is_set():
                raise ManualSessionError("CANCELLED")
            current_ref = tree_service.resolve_table_source(tree_target, node, generation=tree_generation)
            validate_table_target(current_ref, tree_target, expected_target, choice)
            if current_ref != ref:
                raise ManualSessionError("STALE_TABLE_REF")
            sql, parameters = build_table_query(metadata, dialect, query)
        except ObjectTreeError as error:
            code = "STALE_TABLE_REF" if error.code == "STALE_NODE" else "TABLE_TARGET_MISMATCH"
            raise ManualSessionError(code) from None
        except TableSourceError as error:
            raise ManualSessionError(error.code) from None
        page = self._execute_statement(
            sql,
            parameters,
            False,
            False,
            page_size,
            expect_query=True,
        )
        if not isinstance(page, ManualQueryPage):
            raise ManualSessionError("TABLE_PAGE_UNAVAILABLE")
        return TableSourceOpenResult(metadata, page, query)

    def _classify(self, sql: str) -> StatementClassification:
        if not sql.strip():
            raise ManualSessionError("EMPTY_SQL")
        if _contains_transaction_control(sql):
            raise ManualSessionError("TRANSACTION_SQL_FORBIDDEN")
        try:
            classification = self._classify_sql(sql)
            if not isinstance(classification, StatementClassification):
                raise TypeError("classifier must return StatementClassification")
        except Exception as error:
            if isinstance(error, ManualSessionError):
                raise
            raise ManualSessionError("SQL_CLASSIFICATION_FAILED") from error
        if classification.kind in {StatementKind.TRANSACTION_CONTROL, StatementKind.MULTI_STATEMENT}:
            raise ManualSessionError(
                "TRANSACTION_SQL_FORBIDDEN"
                if classification.kind is StatementKind.TRANSACTION_CONTROL
                else "MULTI_STATEMENT_UNSUPPORTED"
            )
        if classification.kind is StatementKind.DML and classification.effect is not TransactionEffect.DIRTY:
            raise ManualSessionError("DML_REQUIRES_DIRTY_EFFECT")
        return classification

    def _ensure_connected_owner(self) -> ManualDriver:
        if self._driver is not None and self._connected:
            return self._driver
        if self._driver is not None:
            # A returned driver may be retained only so its owner can finish
            # setup cleanup. Never replace or use it after configuration
            # failed; explicit owner-thread close is the sole allowed action.
            raise ManualSessionError("CONNECTION_SETUP_FAILED")
        if self._transaction_state is ManualSessionState.UNKNOWN:
            raise ManualSessionError("TRANSACTION_OUTCOME_UNKNOWN")
        if self._closed:
            raise ManualSessionError("SESSION_CLOSED")
        try:
            driver = self._driver_factory(self.target)
        except Exception as error:
            self._set_transaction_state(ManualSessionState.DISCONNECTED)
            safe_factory_codes = {
                "PROFILE_MISMATCH",
                "PROFILE_UNAVAILABLE",
                "MANUAL_PROVIDER_UNSUPPORTED",
            }
            if isinstance(error, ManualSessionError) and error.code in safe_factory_codes:
                raise ManualSessionError(error.code) from None
            raise ManualSessionError("CONNECTION_FAILED") from None
        if driver is None:
            self._set_transaction_state(ManualSessionState.DISCONNECTED)
            raise ManualSessionError("CONNECTION_FACTORY_RETURNED_NONE")
        with self._condition:
            # Keep the actual owner handle before any configuration call can
            # fail. Cleanup then retries against this exact connection.
            self._driver = driver
            self._connected = False
            self._condition.notify_all()
        configure = getattr(driver, "configure_manual_commit", None)
        if not callable(configure):
            self._set_transaction_state(ManualSessionState.DISCONNECTED)
            raise ManualSessionError("MANUAL_COMMIT_CONFIGURATION_REQUIRED")
        try:
            configure()
        except Exception:
            self._set_transaction_state(ManualSessionState.DISCONNECTED)
            raise ManualSessionError("MANUAL_COMMIT_CONFIGURATION_FAILED") from None
        with self._condition:
            self._driver = driver
            self._connected = True
            self._transaction_outcome_unknown = False
            self._transaction_state = ManualSessionState.CLEAN
            self._condition.notify_all()
        return driver

    def _validate_execution(
        self,
        sql: str,
        expect_query: bool,
        ddl_authorized: bool,
        dcl_authorized: bool,
    ) -> StatementClassification:
        classification = self._classify(sql)
        kind = classification.kind
        if expect_query and kind is not StatementKind.QUERY:
            raise ManualSessionError("QUERY_API_REQUIRES_READ_STATEMENT")
        if kind is StatementKind.DDL:
            if not ddl_authorized:
                raise ManualSessionError("DDL_CONFIRMATION_REQUIRED")
            if self._transaction_state is ManualSessionState.DIRTY:
                raise ManualSessionError("DDL_REQUIRES_CLEAN_TRANSACTION")
        if kind is StatementKind.DCL:
            if not dcl_authorized:
                raise ManualSessionError("DCL_CONFIRMATION_REQUIRED")
            if self._transaction_state is ManualSessionState.DIRTY:
                raise ManualSessionError("DCL_REQUIRES_CLEAN_TRANSACTION")
        if self._transaction_state in {ManualSessionState.COMMITTING, ManualSessionState.ROLLING_BACK}:
            raise ManualSessionError("TRANSACTION_BUSY")
        if self._transaction_state is ManualSessionState.UNKNOWN:
            raise ManualSessionError("TRANSACTION_OUTCOME_UNKNOWN")
        if (
            self._transaction_state is ManualSessionState.DIRTY
            and classification.effect in {TransactionEffect.UNKNOWN, TransactionEffect.IMPLICIT_COMMIT}
        ):
            raise ManualSessionError("STATEMENT_EFFECT_UNSAFE_FOR_DIRTY_TRANSACTION")
        return classification

    def _execute_statement(
        self,
        sql: str,
        parameters: Any,
        ddl_authorized: bool,
        dcl_authorized: bool,
        page_size: int,
        *,
        expect_query: bool,
        retained_budget: int | None = None,
    ) -> ManualStatementResult | ManualQueryPage:
        classification = self._validate_execution(sql, expect_query, ddl_authorized, dcl_authorized)
        kind = classification.kind
        driver = self._ensure_connected_owner()
        if self._cancel_requested.is_set():
            raise ManualSessionError("CANCELLED")
        if kind in {StatementKind.DDL, StatementKind.DCL} and self._transaction_state is not ManualSessionState.CLEAN:
            raise ManualSessionError("DDL_REQUIRES_CLEAN_TRANSACTION" if kind is StatementKind.DDL else "DCL_REQUIRES_CLEAN_TRANSACTION")
        self._invalidate_result_source("SOURCE_REPLACED")
        self._close_cursor_owner()
        query_id = uuid.uuid4().hex
        self._query_id = query_id
        self._page_number = 0
        self._page_size = page_size
        self._columns = ()
        self._lookahead = None
        self._result_bytes = 0
        self._active_result_budget = self._result_budget if retained_budget is None else retained_budget
        self._paging_budget_blocked = False
        self._rowcount = None
        self._clear_result_source_pages()
        self._result_source_cursor_open = False
        self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, "STATEMENT_PENDING")
        try:
            cursor = driver.execute(sql, parameters)
        except Exception as error:
            if getattr(error, "connection_lost", False) or classification.effect in {
                TransactionEffect.UNKNOWN,
                TransactionEffect.IMPLICIT_COMMIT,
            }:
                self._mark_unknown()
            elif kind is StatementKind.DML or classification.effect is TransactionEffect.DIRTY:
                self._set_transaction_state(ManualSessionState.DIRTY)
            self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, "STATEMENT_EXECUTION_FAILED")
            raise ManualSessionError("STATEMENT_EXECUTION_FAILED") from error

        self._apply_transaction_effect(classification.effect)

        if self._cancel_requested.is_set():
            try:
                cursor.close()
            except Exception:
                pass
            if classification.effect in {TransactionEffect.UNKNOWN, TransactionEffect.IMPLICIT_COMMIT}:
                self._mark_unknown()
            self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, "STATEMENT_CANCELLED")
            raise ManualSessionError("CANCELLED")

        self._cursor = cursor
        self._result_source_cursor_open = True
        self._publish_result_source_status(ManualResultSourceState.AVAILABLE)
        self._columns = tuple(_column_from_description(item) for item in (cursor.description or ()))
        self._rowcount = cursor.rowcount
        self._lookahead = None
        self._result_bytes = 0
        self._paging_budget_blocked = False
        if self._columns:
            page = self._fetch_page_owner(page_size)
            if expect_query:
                return page
            return ManualStatementResult(
                query_id=query_id,
                tab_id=self.target.tab_id,
                generation=self.target.generation,
                kind=kind,
                rowcount=self._rowcount,
                state=self._transaction_state,
                page=page,
                ddl_behavior=(
                    DdlTransactionBehavior.TRANSACTIONAL
                    if kind is StatementKind.DDL and classification.effect is TransactionEffect.DIRTY
                    else DdlTransactionBehavior.IMPLICIT_COMMIT
                    if kind is StatementKind.DDL and classification.effect is TransactionEffect.IMPLICIT_COMMIT
                    else DdlTransactionBehavior.UNKNOWN
                    if kind is StatementKind.DDL and classification.effect is TransactionEffect.UNKNOWN
                    else None
                ),
            )
        self._close_cursor_owner()
        if expect_query:
            empty = ManualQueryPage(
                query_id=query_id,
                tab_id=self.target.tab_id,
                generation=self.target.generation,
                columns=(),
                rows=(),
                page_number=1,
                page_size=page_size,
                has_more=False,
                result_bytes=0,
                rowcount=self._rowcount,
            )
            with self._condition:
                self._last_page = empty
            self._append_issued_page(empty)
            self._result_source_cursor_open = False
            self._publish_result_source_status(ManualResultSourceState.COMPLETE)
            return empty
        self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, "NO_RESULT_SET")
        return ManualStatementResult(
            query_id=query_id,
            tab_id=self.target.tab_id,
            generation=self.target.generation,
            kind=kind,
            rowcount=self._rowcount,
            state=self._transaction_state,
            ddl_behavior=(
                DdlTransactionBehavior.TRANSACTIONAL
                if kind is StatementKind.DDL and classification.effect is TransactionEffect.DIRTY
                else DdlTransactionBehavior.IMPLICIT_COMMIT
                if kind is StatementKind.DDL and classification.effect is TransactionEffect.IMPLICIT_COMMIT
                else DdlTransactionBehavior.UNKNOWN
                if kind is StatementKind.DDL and classification.effect is TransactionEffect.UNKNOWN
                else None
            ),
        )

    def _apply_transaction_effect(self, effect: TransactionEffect) -> None:
        if effect is TransactionEffect.NO_CHANGE:
            return
        if effect is TransactionEffect.DIRTY:
            self._set_transaction_state(ManualSessionState.DIRTY)
        elif effect is TransactionEffect.IMPLICIT_COMMIT:
            self._set_transaction_state(ManualSessionState.CLEAN)
        else:
            self._mark_unknown()

    def _fetch_page_owner(self, page_size: int) -> ManualQueryPage:
        if self._cursor is None:
            raise ManualSessionError("NO_ACTIVE_RESULT")
        if self._paging_budget_blocked:
            raise ManualSessionError("RESULT_BUDGET_EXHAUSTED")
        rows: list[tuple[Any, ...]] = []
        truncated = False
        exhausted = False
        if self._lookahead is not None:
            rows.append(self._lookahead[0])
            self._lookahead = None
        while len(rows) < page_size + 1:
            if self._cancel_requested.is_set():
                self._invalidate_result_source("CANCELLED")
                self._close_cursor_owner()
                raise ManualSessionError("CANCELLED")
            while self._pending_rows and len(rows) < page_size + 1:
                pending_row, pending_size = self._pending_rows.popleft()
                self._pending_prefetch_bytes -= pending_size
                if self._result_bytes + pending_size > self._active_result_budget:
                    self._pending_rows.appendleft((pending_row, pending_size))
                    self._pending_prefetch_bytes += pending_size
                    truncated = True
                    self._paging_budget_blocked = True
                    break
                self._result_bytes += pending_size
                rows.append(pending_row)
            if truncated or len(rows) >= page_size + 1:
                break
            if not self._pending_rows and self._cursor is None:
                exhausted = True
                break
            count = page_size + 1 - len(rows)
            try:
                batch = self._cursor.fetchmany(count) if self._cursor is not None else ()
            except Exception as error:
                if getattr(error, "connection_lost", False) and self._transaction_state is ManualSessionState.DIRTY:
                    self._mark_unknown()
                code = "CANCELLED" if self._cancel_requested.is_set() else "RESULT_FETCH_FAILED"
                if code != "CANCELLED" and getattr(error, "code", "") in {"RESULT_VALUE_LIMIT", "LOB_TYPE_UNSUPPORTED", "LOB_VALUE_INVALID"}:
                    code = error.code
                self._invalidate_result_source(code)
                self._close_cursor_owner(ignore_errors=True)
                raise ManualSessionError(code) from error
            if self._cancel_requested.is_set():
                self._invalidate_result_source("CANCELLED")
                self._close_cursor_owner(ignore_errors=True)
                raise ManualSessionError("CANCELLED")
            if not batch:
                exhausted = True
                break
            for index, raw_row in enumerate(batch):
                if self._cancel_requested.is_set():
                    self._invalidate_result_source("CANCELLED")
                    self._close_cursor_owner(ignore_errors=True)
                    raise ManualSessionError("CANCELLED")
                row = tuple(raw_row)
                row_size = _estimate_value_bytes(row)
                if self._result_bytes + row_size > self._active_result_budget:
                    truncated = True
                    self._paging_budget_blocked = True
                    if not self._buffer_pending_batch(batch, index):
                        self._invalidate_result_source("PREFETCH_LIMIT")
                        self._close_cursor_owner(ignore_errors=True)
                    break
                self._result_bytes += row_size
                rows.append(row)
                if len(rows) >= page_size + 1:
                    if index + 1 < len(batch) and not self._buffer_pending_batch(batch, index + 1):
                        self._invalidate_result_source("PREFETCH_LIMIT")
                        self._close_cursor_owner(ignore_errors=True)
                    break
            if truncated or len(rows) >= page_size + 1:
                break

        has_more = len(rows) > page_size
        if has_more:
            lookahead = rows.pop()
            self._lookahead = (lookahead, _estimate_value_bytes(lookahead))
        elif exhausted:
            self._close_cursor_owner(ignore_errors=True)
        self._page_number += 1
        page = ManualQueryPage(
            query_id=self._query_id,
            tab_id=self.target.tab_id,
            generation=self.target.generation,
            columns=self._columns,
            rows=tuple(rows),
            page_number=self._page_number,
            page_size=page_size,
            has_more=has_more,
            result_bytes=self._result_bytes,
            truncated_by_budget=truncated,
            rowcount=self._rowcount,
        )
        with self._condition:
            self._last_page = page
        source_state = self._result_source_state
        if source_state is not ManualResultSourceState.UNAVAILABLE:
            self._append_issued_page(page)
            if self._result_source_state is not ManualResultSourceState.UNAVAILABLE:
                if truncated:
                    self._publish_result_source_status(ManualResultSourceState.BUDGET_LIMITED)
                elif exhausted:
                    self._result_source_cursor_open = False
                    self._publish_result_source_status(ManualResultSourceState.COMPLETE)
                else:
                    self._result_source_cursor_open = self._cursor is not None
                    self._publish_result_source_status(ManualResultSourceState.AVAILABLE)
        return page

    def _buffer_pending_batch(self, batch: Sequence[Sequence[Any]], start: int) -> bool:
        pending: list[tuple[tuple[Any, ...], int]] = []
        pending_bytes = self._pending_prefetch_bytes
        for raw_row in batch[start:]:
            row = tuple(raw_row)
            row_size = _estimate_value_bytes(row)
            pending.append((row, row_size))
            pending_bytes += row_size
            if (
                len(self._pending_rows) + len(pending) > MAX_PENDING_PREFETCH_ROWS
                or pending_bytes > MAX_PENDING_PREFETCH_BYTES
            ):
                return False
        self._pending_rows.extend(pending)
        self._pending_prefetch_bytes = pending_bytes
        return True

    def _queue_pending_records(
        self,
        records: Sequence[tuple[tuple[Any, ...], int]],
        *,
        prepend: bool = False,
    ) -> bool:
        if not records:
            return True
        total_bytes = self._pending_prefetch_bytes + sum(size for _row, size in records)
        if len(self._pending_rows) + len(records) > MAX_PENDING_PREFETCH_ROWS:
            return False
        if total_bytes > MAX_PENDING_PREFETCH_BYTES:
            return False
        if prepend:
            for record in reversed(records):
                self._pending_rows.appendleft(record)
        else:
            self._pending_rows.extend(records)
        self._pending_prefetch_bytes = total_bytes
        return True

    def _export_full_query_owner(self, request: Any) -> Any:
        from .manual_result_export import (
            ManualResultExportRequest,
            write_full_query,
        )
        from .result_export import ExportError

        if not isinstance(request, ManualResultExportRequest):
            raise ManualSessionError("RESULT_EXPORT_INVALID_REQUEST")
        if request.session_target != self.target:
            raise ManualSessionError("RESULT_EXPORT_SESSION_TARGET_MISMATCH")
        target = request.target
        if (
            target.query_id != self._query_id
            or target.tab_id != self.target.tab_id
            or target.generation != self.target.generation
        ):
            raise ManualSessionError("RESULT_EXPORT_TARGET_MISMATCH")
        prior_state = self._result_source_state
        prior_reason = self._result_source_reason
        if prior_state not in {
            ManualResultSourceState.AVAILABLE,
            ManualResultSourceState.BUDGET_LIMITED,
            ManualResultSourceState.COMPLETE,
        } or not self._issued_pages:
            raise ManualSessionError("RESULT_SOURCE_UNAVAILABLE")
        if prior_state in {ManualResultSourceState.AVAILABLE, ManualResultSourceState.BUDGET_LIMITED}:
            if self._cursor is None:
                raise ManualSessionError("RESULT_SOURCE_UNAVAILABLE")
        progress = {"advanced": False}
        self._publish_result_source_status(ManualResultSourceState.EXPORTING)
        try:
            receipt = write_full_query(
                request,
                self._iter_full_query_pages(progress, ExportError),
                cancel_check=self._cancel_requested.is_set,
            )
        except ExportError as error:
            failure_code = "CANCELLED" if self._cancel_requested.is_set() else error.code
            if progress["advanced"]:
                self._close_cursor_owner(ignore_errors=True)
                self._clear_result_source_pages()
                self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, failure_code)
            else:
                self._publish_result_source_status(prior_state, prior_reason)
            if failure_code == "CANCELLED":
                raise ManualSessionError("CANCELLED") from None
            raise ManualSessionError(f"RESULT_EXPORT_{failure_code}") from None
        except Exception:
            if progress["advanced"]:
                self._close_cursor_owner(ignore_errors=True)
                self._clear_result_source_pages()
                self._publish_result_source_status(ManualResultSourceState.UNAVAILABLE, "EXPORT_FAILED")
            else:
                self._publish_result_source_status(prior_state, prior_reason)
            raise ManualSessionError("RESULT_EXPORT_FAILED") from None
        if progress["advanced"]:
            self._close_cursor_owner(ignore_errors=True)
            self._clear_result_source_pages()
            self._publish_result_source_status(ManualResultSourceState.EXPORTED)
        else:
            self._publish_result_source_status(ManualResultSourceState.COMPLETE)
        return receipt

    def _iter_full_query_pages(self, progress: dict[str, bool], export_error: type[Exception]):
        pages = tuple(self._issued_pages)
        if not pages:
            raise export_error("INCOMPLETE_PAGE_CHAIN")
        last_page = pages[-1]
        budget_page = last_page.truncated_by_budget
        if budget_page:
            if (
                self._result_source_state is not ManualResultSourceState.EXPORTING
                or not self._pending_rows
                or self._cursor is None
            ):
                raise export_error("SOURCE_TRUNCATED")
            last_page = replace(last_page, has_more=True, truncated_by_budget=False)
        for index, page in enumerate(pages):
            yield last_page if index == len(pages) - 1 else page
        if not last_page.has_more:
            return
        if self._cursor is None:
            raise export_error("INCOMPLETE_PAGE_CHAIN")
        yield from self._iter_cursor_export_pages(progress, last_page.page_number, export_error)

    def _iter_cursor_export_pages(
        self,
        progress: dict[str, bool],
        prior_page_number: int,
        export_error: type[Exception],
    ):
        page_number = prior_page_number
        result_bytes = self._result_bytes
        page_size = self._page_size
        while True:
            if self._cancel_requested.is_set():
                raise export_error("CANCELLED")
            rows: list[tuple[Any, ...]] = []
            page_bytes = 0
            exhausted = False
            batch: Sequence[Sequence[Any]] = ()
            batch_index = 0
            while len(rows) < page_size + 1:
                if self._cancel_requested.is_set():
                    raise export_error("CANCELLED")
                record: tuple[tuple[Any, ...], int] | None = None
                record_origin = ""
                if self._lookahead is not None:
                    record = self._lookahead
                    self._lookahead = None
                    record_origin = "lookahead"
                elif self._pending_rows:
                    record = self._pending_rows.popleft()
                    self._pending_prefetch_bytes -= record[1]
                    record_origin = "pending"
                elif batch_index < len(batch):
                    row = tuple(batch[batch_index])
                    batch_index += 1
                    record = (row, _estimate_value_bytes(row))
                    record_origin = "batch"
                else:
                    if self._cursor is None:
                        exhausted = True
                        break
                    count = page_size + 1 - len(rows)
                    progress["advanced"] = True
                    try:
                        batch = self._cursor.fetchmany(count)
                    except Exception as error:
                        if getattr(error, "connection_lost", False) and self._transaction_state is ManualSessionState.DIRTY:
                            self._mark_unknown()
                        code = "CANCELLED" if self._cancel_requested.is_set() else "RESULT_FETCH_FAILED"
                        self._invalidate_result_source(code)
                        self._close_cursor_owner(ignore_errors=True)
                        raise export_error("CANCELLED" if code == "CANCELLED" else "PAGE_SOURCE_FAILED") from error
                    if self._cancel_requested.is_set():
                        self._invalidate_result_source("CANCELLED")
                        self._close_cursor_owner(ignore_errors=True)
                        raise export_error("CANCELLED")
                    batch_index = 0
                    if not batch:
                        exhausted = True
                        break
                    continue

                progress["advanced"] = True
                row, row_bytes = record
                if len(rows) >= page_size:
                    if record_origin == "lookahead":
                        self._lookahead = record
                    elif record_origin == "pending":
                        self._pending_rows.appendleft(record)
                        self._pending_prefetch_bytes += row_bytes
                    else:
                        tail = [(row, row_bytes)]
                        tail.extend(
                            (tuple(batch[index]), _estimate_value_bytes(tuple(batch[index])))
                            for index in range(batch_index, len(batch))
                        )
                        if not self._queue_pending_records(tail):
                            self._invalidate_result_source("PREFETCH_LIMIT")
                            self._close_cursor_owner(ignore_errors=True)
                            raise export_error("PREFETCH_LIMIT")
                    break
                if page_bytes + row_bytes > MAX_PENDING_PREFETCH_BYTES:
                    if record_origin == "lookahead":
                        self._lookahead = record
                    elif record_origin == "pending":
                        self._pending_rows.appendleft(record)
                        self._pending_prefetch_bytes += row_bytes
                    else:
                        tail = [(row, row_bytes)]
                        tail.extend(
                            (tuple(batch[index]), _estimate_value_bytes(tuple(batch[index])))
                            for index in range(batch_index, len(batch))
                        )
                        if not self._queue_pending_records(tail):
                            self._invalidate_result_source("PREFETCH_LIMIT")
                            self._close_cursor_owner(ignore_errors=True)
                            raise export_error("PREFETCH_LIMIT")
                    break
                rows.append(row)
                page_bytes += row_bytes
                if record_origin != "lookahead":
                    result_bytes += row_bytes

            has_more = len(rows) > page_size
            if has_more:
                lookahead = rows.pop()
                lookahead_bytes = _estimate_value_bytes(lookahead)
                self._lookahead = (lookahead, lookahead_bytes)
            elif self._pending_rows or self._lookahead is not None:
                has_more = True
            elif exhausted:
                self._close_cursor_owner(ignore_errors=True)
            elif self._cursor is not None:
                has_more = True
            if not rows and exhausted:
                return
            page_number += 1
            yield ManualQueryPage(
                query_id=self._query_id,
                tab_id=self.target.tab_id,
                generation=self.target.generation,
                columns=self._columns,
                rows=tuple(rows),
                page_number=page_number,
                page_size=page_size,
                has_more=has_more,
                result_bytes=result_bytes,
                truncated_by_budget=False,
                rowcount=self._rowcount,
            )
            if not has_more:
                return

    def _fetch_next_page_owner(self, page_size: int) -> ManualQueryPage:
        return self._fetch_page_owner(page_size)

    def _close_cursor_owner(self, *, ignore_errors: bool = False) -> None:
        cursor = self._cursor
        self._cursor = None
        self._lookahead = None
        self._result_source_cursor_open = False
        if cursor is None:
            self._publish_result_source_status()
            return
        try:
            cursor.close()
        except Exception as error:
            if not ignore_errors:
                raise ManualSessionError("CURSOR_CLOSE_FAILED") from error
        finally:
            self._publish_result_source_status()

    def _mark_unknown(self) -> None:
        with self._condition:
            self._transaction_outcome_unknown = True
            self._transaction_state = ManualSessionState.UNKNOWN
            self._condition.notify_all()

    def _commit_owner(self) -> ManualTransactionResult:
        if self._transaction_state is ManualSessionState.CLEAN:
            return ManualTransactionResult(self.target.tab_id, self.target.generation, self._transaction_state, "commit", False, True)
        if self._transaction_state is ManualSessionState.UNKNOWN:
            raise ManualSessionError("TRANSACTION_OUTCOME_UNKNOWN")
        if self._transaction_state is not ManualSessionState.DIRTY:
            raise ManualSessionError("SESSION_NOT_CONNECTED_OR_BUSY")
        driver = self._driver
        if driver is None or not self._connected:
            self._mark_unknown()
            raise ManualSessionError("COMMIT_OUTCOME_UNKNOWN")
        self._invalidate_incomplete_result_source("TRANSACTION_BOUNDARY")
        self._close_cursor_owner()
        self._set_transaction_state(ManualSessionState.COMMITTING)
        try:
            driver.commit()
        except Exception as error:
            self._mark_unknown()
            raise ManualSessionError("COMMIT_OUTCOME_UNKNOWN") from error
        with self._condition:
            self._transaction_outcome_unknown = False
        self._set_transaction_state(ManualSessionState.CLEAN)
        return ManualTransactionResult(self.target.tab_id, self.target.generation, self._transaction_state, "commit", True, True)

    def _rollback_owner(self) -> ManualTransactionResult:
        if self._transaction_state is ManualSessionState.CLEAN:
            return ManualTransactionResult(self.target.tab_id, self.target.generation, self._transaction_state, "rollback", False, True)
        if self._transaction_state not in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}:
            raise ManualSessionError("SESSION_NOT_CONNECTED_OR_BUSY")
        driver = self._driver
        if driver is None:
            self._mark_unknown()
            raise ManualSessionError("ROLLBACK_OUTCOME_UNKNOWN")
        previous_unknown = self._transaction_state is ManualSessionState.UNKNOWN
        self._invalidate_incomplete_result_source("TRANSACTION_BOUNDARY")
        self._close_cursor_owner()
        self._set_transaction_state(ManualSessionState.ROLLING_BACK)
        try:
            driver.rollback()
        except Exception as error:
            self._mark_unknown()
            raise ManualSessionError("ROLLBACK_OUTCOME_UNKNOWN") from error
        if previous_unknown:
            self._mark_unknown()
            return ManualTransactionResult(self.target.tab_id, self.target.generation, self._transaction_state, "rollback", True, False)
        with self._condition:
            self._transaction_outcome_unknown = False
        self._set_transaction_state(ManualSessionState.CLEAN)
        return ManualTransactionResult(self.target.tab_id, self.target.generation, self._transaction_state, "rollback", True, True)

    def _classify_batch_statement(self, sql: str) -> StatementKind:
        classification = self._classify(sql)
        if classification.kind is not StatementKind.DML or classification.effect is not TransactionEffect.DIRTY:
            raise ManualSessionError("SAVEPOINT_BATCH_REQUIRES_DML")
        return classification.kind

    def _savepoint_batch_owner(
        self,
        statements: tuple[tuple[str, Any], ...],
        expected_rowcounts: tuple[int | None, ...],
    ) -> ManualBatchResult:
        if self._transaction_state is ManualSessionState.UNKNOWN:
            raise ManualSessionError("SESSION_NOT_READY")
        for sql, _parameters in statements:
            self._classify_batch_statement(sql)
        driver = self._ensure_connected_owner()
        if self._cancel_requested.is_set():
            raise ManualSessionError("CANCELLED")
        create = getattr(driver, "create_savepoint", None)
        rollback_to = getattr(driver, "rollback_to_savepoint", None)
        release = getattr(driver, "release_savepoint", None)
        if not all(callable(method) for method in (create, rollback_to, release)):
            raise ManualSessionError("SAVEPOINT_UNSUPPORTED")
        previous_state = self._transaction_state
        name = f"pt_{uuid.uuid4().hex[:16]}"
        self._invalidate_incomplete_result_source("SOURCE_REPLACED")
        self._close_cursor_owner()
        try:
            create(name)
        except Exception as error:
            if getattr(error, "connection_lost", False):
                self._mark_unknown()
            raise ManualSessionError("SAVEPOINT_CREATE_FAILED") from error
        rowcounts: list[Any] = []
        completed = 0
        conflict_index = None
        conflict_expected = None
        conflict_actual = None
        try:
            for index, (sql, parameters) in enumerate(statements):
                if self._cancel_requested.is_set():
                    raise ManualSessionError("CANCELLED")
                cursor = driver.execute(sql, parameters)
                actual_rowcount = cursor.rowcount
                rowcounts.append(actual_rowcount)
                completed += 1
                try:
                    cursor.close()
                except Exception as error:
                    raise ManualSessionError("CURSOR_CLOSE_FAILED") from error
                self._set_transaction_state(ManualSessionState.DIRTY)
                expected_rowcount = expected_rowcounts[index]
                rowcount_is_known = isinstance(actual_rowcount, int) and not isinstance(actual_rowcount, bool)
                if expected_rowcount is not None and (
                    not rowcount_is_known or actual_rowcount != expected_rowcount
                ):
                    conflict_index = index
                    conflict_expected = expected_rowcount
                    conflict_actual = actual_rowcount
                    raise ManualSessionError("ROWCOUNT_CONFLICT")
            release(name)
        except Exception as error:
            try:
                rollback_to(name)
                release(name)
            except Exception:
                self._mark_unknown()
                raise ManualSessionError("SAVEPOINT_ROLLBACK_FAILED") from error
            self._set_transaction_state(previous_state)
            code = error.code if isinstance(error, ManualSessionError) else "STATEMENT_EXECUTION_FAILED"
            return ManualBatchResult(
                self.target.tab_id, self.target.generation, name, False, completed,
                tuple(rowcounts), self._transaction_state, code,
                conflict_index, conflict_expected, conflict_actual,
            )
        self._set_transaction_state(ManualSessionState.DIRTY)
        return ManualBatchResult(
            self.target.tab_id, self.target.generation, name, True, completed,
            tuple(rowcounts), self._transaction_state,
        )

    def _close_owner(self, disposition: str) -> ManualCloseResult:
        state = self._transaction_state
        if disposition == "" and state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}:
            raise ManualSessionError("CLOSE_REQUIRES_TRANSACTION_DECISION")
        if state is ManualSessionState.UNKNOWN and disposition == "commit":
            raise ManualSessionError("TRANSACTION_OUTCOME_UNKNOWN")
        if disposition == "commit" and state is ManualSessionState.DIRTY:
            self._commit_owner()
        elif disposition in {"rollback", "cancel"} and state in {ManualSessionState.DIRTY, ManualSessionState.UNKNOWN}:
            self._rollback_owner()
        self._invalidate_incomplete_result_source("SESSION_CLOSED")
        self._close_cursor_owner()
        driver = self._driver
        if driver is not None:
            try:
                driver.close()
            except Exception as error:
                if self._connected:
                    self._mark_unknown()
                else:
                    self._set_transaction_state(ManualSessionState.DISCONNECTED)
                raise ManualSessionError("CONNECTION_CLOSE_FAILED") from None
        prior_unknown = self._transaction_outcome_unknown or self._transaction_state is ManualSessionState.UNKNOWN
        with self._condition:
            self._driver = None
            self._connected = False
            self._closed = True
            self._closing = False
            self._transaction_state = ManualSessionState.DISCONNECTED
            self._condition.notify_all()
        self._invalidate_result_source("SESSION_CLOSED")
        self._result_source_cursor_open = False
        self._publish_result_source_status()
        return ManualCloseResult(
            self.target.tab_id,
            self.target.generation,
            True,
            ManualSessionState.DISCONNECTED,
            prior_unknown,
        )


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_RESULT_BYTES",
    "MAX_SCRIPT_BYTES",
    "MAX_SCRIPT_STATEMENTS",
    "PAGE_SIZES",
    "DdlTransactionBehavior",
    "DriverFactory",
    "ManualBatchResult",
    "ManualCleanupStatus",
    "ManualCloseResult",
    "ManualColumn",
    "ManualCursor",
    "ManualDriver",
    "ManualQueryPage",
    "ManualResultSourceState",
    "ManualResultSourceStatus",
    "ManualSession",
    "ManualSessionError",
    "ManualSessionState",
    "ManualSessionTarget",
    "ManualStatementResult",
    "ManualOperationFuture",
    "ManualScriptEntry",
    "ManualScriptResult",
    "ManualTransactionResult",
    "StatementClassification",
    "SqlClassifier",
    "StatementKind",
    "TransactionEffect",
]
