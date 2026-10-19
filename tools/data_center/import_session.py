"""Dedicated owner-thread transaction for approved relational imports.

The service accepts an injected fresh-driver factory and a live signed table
selection. Construction is inert. Database work starts only after ``start``;
successful insertion stops at ``READY_TO_COMMIT`` until the caller makes a
separate commit decision.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import re
import threading
import uuid
from typing import Any, Callable, Protocol, Sequence

from tools.data_center.changeset import ChangeSetError, SqlDialectPolicy
from tools.data_center.changeset_dialects import create_changeset_dialect
from tools.data_center.import_preview import (
    ApprovedImport,
    ImportChunk,
    ImportPreviewError,
    ImportValueKind,
    TargetColumn,
    close_import_snapshot,
    iter_import_chunks,
)
from tools.data_center.manual_session import ManualSessionTarget
from tools.data_center.object_tree import ObjectTreeError, TreeNode, TreeTarget, VerifiedTableRef
from tools.data_center.profile_host import ConnectionChoice
from tools.data_center.table_source import (
    TableColumnMetadata,
    TableSourceError,
    TableSourceMetadata,
    inspect_table,
    validate_table_target,
)


class ImportSessionState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_PENDING = "CANCEL_PENDING"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    COMMITTING = "COMMITTING"
    ROLLING_BACK = "ROLLING_BACK"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    CANCELLED_BEFORE_START = "CANCELLED_BEFORE_START"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ImportSessionError(RuntimeError):
    """Stable safe import error; never retains SQL, paths, or source values."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "IMPORT_SESSION_FAILED")
        super().__init__(self.code)


class _ImportCursor(Protocol):
    rowcount: Any

    def fetchmany(self, size: int) -> Any: ...

    def close(self) -> Any: ...


class ImportDriver(Protocol):
    """Public adapter surface used only on this import's owner thread."""

    def configure_manual_commit(self) -> Any: ...

    def execute(self, sql: str, parameters: Any) -> _ImportCursor: ...

    def executemany(self, sql: str, parameter_rows: Sequence[Any]) -> _ImportCursor: ...

    def commit(self) -> Any: ...

    def rollback(self) -> Any: ...

    def close(self) -> Any: ...


DriverFactory = Callable[[ManualSessionTarget], ImportDriver]


class _SignedTree(Protocol):
    def resolve_table_source(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        generation: int,
    ) -> VerifiedTableRef: ...


@dataclass(frozen=True, slots=True)
class ImportSessionRequest:
    approved: ApprovedImport
    tree_service: _SignedTree
    tree_target: TreeTarget
    node: TreeNode
    tree_generation: int
    target: ManualSessionTarget
    choice: ConnectionChoice

    def __post_init__(self) -> None:
        if not isinstance(self.approved, ApprovedImport):
            raise ImportSessionError("INVALID_APPROVAL")
        if (
            not callable(getattr(self.tree_service, "resolve_table_source", None))
            or not isinstance(self.tree_target, TreeTarget)
            or not isinstance(self.node, TreeNode)
            or not isinstance(self.target, ManualSessionTarget)
            or not isinstance(self.choice, ConnectionChoice)
            or isinstance(self.tree_generation, bool)
            or not isinstance(self.tree_generation, int)
            or self.tree_generation < 0
        ):
            raise ImportSessionError("INVALID_TARGET")
        mysql_scope = self.tree_target.provider in {"mysql", "oceanbase_mysql"}
        global_mysql_root = mysql_scope and not self.tree_target.database
        if (
            self.tree_target.connection_id != self.choice.connection_id
            or self.tree_target.profile_revision != self.choice.revision
            or (self.tree_target.database != self.choice.database and not global_mysql_root)
            or self.tree_target.provider.casefold() != self.choice.provider.casefold()
            or self.tree_target.mode.casefold() != self.choice.mode.casefold()
            or self.target.connection_id != self.choice.connection_id
            or self.target.profile_revision != self.choice.revision
            or not self.target.database
            or self.target.database != self.choice.database
            or self.node.database != self.choice.database
        ):
            raise ImportSessionError("TARGET_MISMATCH")


@dataclass(frozen=True, slots=True)
class ImportSessionStatus:
    state: str
    rows_processed: int
    chunks_processed: int
    total_rows: int | None
    safe_code: str
    transaction_outcome: str
    owner_alive: bool
    connection_retained: bool
    cleanup_complete: bool


@dataclass(frozen=True, slots=True)
class ImportRunResult:
    state: str
    rows_processed: int
    chunks_processed: int


@dataclass(frozen=True, slots=True)
class ImportDecisionResult:
    state: str
    committed: bool
    rolled_back: bool
    rows_processed: int
    cleanup_complete: bool
    safe_code: str = ""


@dataclass(frozen=True, slots=True)
class ImportCleanupResult:
    complete: bool
    connection_retained: bool
    safe_code: str = ""


@dataclass(frozen=True, slots=True)
class _ColumnWritePolicy:
    metadata: TableColumnMetadata
    target: TargetColumn
    max_characters: int | None = None
    max_bytes: int | None = None
    minimum_integer: int | None = None
    maximum_integer: int | None = None


@dataclass(frozen=True, slots=True)
class _InsertPlan:
    sql: str
    target_names: tuple[str, ...]
    columns: tuple[_ColumnWritePolicy, ...]
    dialect: SqlDialectPolicy


@dataclass(frozen=True, slots=True)
class ImportTargetCandidate:
    """One catalog column and its safe, typed import suggestion if supported."""

    name: str
    target: TargetColumn | None
    required_on_insert: bool | None
    generated: bool | None
    reason_code: str = ""

    @property
    def supported(self) -> bool:
        return self.target is not None


@dataclass(frozen=True, slots=True)
class ImportTargetCandidates:
    """Candidate-only view of catalog metadata; start() still revalidates it."""

    columns: tuple[ImportTargetCandidate, ...]
    blocking_reason_codes: tuple[str, ...]

    @property
    def can_approve(self) -> bool:
        return not self.blocking_reason_codes and any(item.supported for item in self.columns)


def candidate_target_columns(metadata: TableSourceMetadata) -> ImportTargetCandidates:
    """Suggest typed import targets from table metadata without acquiring a driver.

    The returned values are a preview aid only. ImportSession resolves the live
    signed node and inspects the catalog again before it inserts any row.
    """

    if (
        not isinstance(metadata, TableSourceMetadata)
        or not isinstance(metadata.target, ManualSessionTarget)
        or not isinstance(metadata.signed_ref, VerifiedTableRef)
        or not isinstance(metadata.columns, tuple)
    ):
        raise ImportSessionError("TABLE_METADATA_INVALID")

    blockers: list[str] = []
    if metadata.signed_ref.kind != "table" or metadata.is_base_table is not True:
        blockers.append("TABLE_NOT_WRITABLE")
    if not metadata.columns:
        blockers.append("TABLE_METADATA_INVALID")
        return ImportTargetCandidates((), tuple(dict.fromkeys(blockers)))

    duplicate_names: set[str] = set()
    seen_names: set[str] = set()
    for column in metadata.columns:
        if isinstance(column, TableColumnMetadata) and isinstance(column.name, str):
            if column.name in seen_names:
                duplicate_names.add(column.name)
            seen_names.add(column.name)
    if duplicate_names:
        blockers.append("TABLE_METADATA_INVALID")

    candidates: list[ImportTargetCandidate] = []
    for column in metadata.columns:
        if not isinstance(column, TableColumnMetadata) or not isinstance(column.name, str) or not column.name:
            raise ImportSessionError("TABLE_METADATA_INVALID")

        required = column.required_on_insert if isinstance(column.required_on_insert, bool) else None
        generated = column.generated if isinstance(column.generated, bool) else None
        target: TargetColumn | None = None
        reason = ""
        if column.name in duplicate_names:
            reason = "TABLE_METADATA_INVALID"
        elif generated is None:
            reason = "GENERATION_METADATA_UNKNOWN"
        elif (
            not isinstance(column.nullable, bool)
            or (column.has_default is not None and not isinstance(column.has_default, bool))
            or required is None
            or required != (not column.nullable and generated is False and column.has_default is not True)
        ):
            reason = "INSERT_METADATA_INCONSISTENT"
        elif generated:
            reason = "GENERATED_COLUMN"
        else:
            try:
                target = _column_policy(
                    column,
                    metadata.signed_ref.target.provider,
                    None,
                ).target
            except ImportSessionError as error:
                reason = error.code

        candidate = ImportTargetCandidate(column.name, target, required, generated, reason)
        candidates.append(candidate)
        if reason in {
            "GENERATION_METADATA_UNKNOWN",
            "INSERT_METADATA_INCONSISTENT",
            "TABLE_METADATA_INVALID",
        }:
            blockers.append(reason)
        if required is True and not candidate.supported and reason:
            blockers.append(reason)

    if not any(item.supported for item in candidates):
        blockers.append("NO_IMPORTABLE_COLUMNS")
    return ImportTargetCandidates(tuple(candidates), tuple(dict.fromkeys(blockers)))


_MYSQL_DECIMAL_RE = re.compile(
    r"^(?:decimal|numeric)\s*\(\s*([0-9]+)\s*,\s*([0-9]+)\s*\)(?:\s+unsigned)?$",
    re.IGNORECASE,
)
_MYSQL_INTEGER_RE = re.compile(
    r"^(tinyint|smallint|mediumint|int|integer|bigint)(?:\s*\(([0-9]+)\))?(?:\s+unsigned)?(?:\s+zerofill)?$",
    re.IGNORECASE,
)
_MYSQL_CHAR_RE = re.compile(r"^(char|varchar)\s*\(\s*([0-9]+)\s*\)$", re.IGNORECASE)
_MYSQL_TEMPORAL_RE = re.compile(r"^(datetime|timestamp)(?:\s*\(\s*[0-9]+\s*\))?$", re.IGNORECASE)
_ORACLE_TIMESTAMP_RE = re.compile(
    r"^timestamp(?:\s*\(\s*[0-9]+\s*\))?(?:\s+with(?:\s+local)?\s+time\s+zone)?$",
    re.IGNORECASE,
)
_MYSQL_INT_BOUNDS = {
    "tinyint": (-(2**7), 2**7 - 1, 2**8 - 1),
    "smallint": (-(2**15), 2**15 - 1, 2**16 - 1),
    "mediumint": (-(2**23), 2**23 - 1, 2**24 - 1),
    "int": (-(2**31), 2**31 - 1, 2**32 - 1),
    "integer": (-(2**31), 2**31 - 1, 2**32 - 1),
    "bigint": (-(2**63), 2**63 - 1, 2**64 - 1),
}
_MYSQL_TEXT_BYTES = {
    "tinytext": 255,
    "text": 65_535,
    "mediumtext": 16_777_215,
    "longtext": 4_294_967_295,
}


class ImportSession:
    """One dedicated importer worker; no user/manual-tab session is accepted."""

    def __init__(self, request: ImportSessionRequest, *, driver_factory: DriverFactory) -> None:
        if not isinstance(request, ImportSessionRequest) or not callable(driver_factory):
            raise ImportSessionError("INVALID_REQUEST")
        self.request = request
        self._driver_factory = driver_factory
        self._condition = threading.Condition(threading.RLock())
        self._cancel = threading.Event()
        self._state = ImportSessionState.CREATED
        self._safe_code = ""
        self._transaction_outcome = "none"
        self._rows_processed = 0
        self._chunks_processed = 0
        self._driver: ImportDriver | None = None
        self._owner: threading.Thread | None = None
        self._run_future: Future[ImportRunResult] | None = None
        self._decision_future: Future[ImportDecisionResult] | None = None
        self._cleanup_future: Future[ImportCleanupResult] = Future()
        self._decision: str | None = None
        self._retry_close = False
        self._waiting_for_close_retry = False
        self._retry_futures: list[Future[ImportCleanupResult]] = []
        self._cleanup_code = ""

    @property
    def status(self) -> ImportSessionStatus:
        with self._condition:
            owner_alive = bool(self._owner and self._owner.is_alive())
            return ImportSessionStatus(
                state=self._state,
                rows_processed=self._rows_processed,
                chunks_processed=self._chunks_processed,
                total_rows=None,
                safe_code=self._safe_code,
                transaction_outcome=self._transaction_outcome,
                owner_alive=owner_alive,
                connection_retained=self._driver is not None,
                cleanup_complete=self._cleanup_future.done() and self._driver is None,
            )

    @property
    def cleanup_future(self) -> Future[ImportCleanupResult]:
        return self._cleanup_future

    @property
    def decision_future(self) -> Future[ImportDecisionResult] | None:
        with self._condition:
            return self._decision_future

    def start(self) -> Future[ImportRunResult]:
        with self._condition:
            if self._state != ImportSessionState.CREATED:
                raise ImportSessionError("SESSION_ALREADY_STARTED")
            future: Future[ImportRunResult] = Future()
            self._run_future = future
            self._state = ImportSessionState.QUEUED
            owner = threading.Thread(
                target=self._owner_main,
                name=f"data-center-import-{uuid.uuid4().hex[:12]}",
                daemon=True,
            )
            self._owner = owner
        try:
            owner.start()
        except Exception:
            with self._condition:
                self._state = ImportSessionState.FAILED
                self._safe_code = "OWNER_START_FAILED"
            close_import_snapshot(self.request.approved)
            future.set_exception(ImportSessionError("OWNER_START_FAILED"))
            self._finish_without_connection()
        return future

    def cancel_current(self) -> bool:
        """Cancel queued work or request owner-thread rollback of active work."""

        with self._condition:
            if self._state == ImportSessionState.CREATED:
                self._state = ImportSessionState.CANCELLED_BEFORE_START
                self._safe_code = "CANCELLED_BEFORE_START"
                release_now = True
            elif self._state == ImportSessionState.QUEUED:
                future = self._run_future
                if future is not None and future.cancel():
                    self._cancel.set()
                    self._condition.notify_all()
                    return True
                self._cancel.set()
                self._state = ImportSessionState.CANCEL_PENDING
                release_now = False
            elif self._state in {ImportSessionState.RUNNING, ImportSessionState.READY_TO_COMMIT}:
                self._cancel.set()
                if self._state == ImportSessionState.READY_TO_COMMIT:
                    self._choose_decision_locked("rollback")
                else:
                    self._state = ImportSessionState.CANCEL_PENDING
                self._condition.notify_all()
                return True
            else:
                return False
        if release_now:
            close_import_snapshot(self.request.approved)
            self._finish_without_connection()
        return True

    def commit(self) -> Future[ImportDecisionResult]:
        return self._request_decision("commit")

    def rollback(self) -> Future[ImportDecisionResult]:
        return self._request_decision("rollback")

    def close(self) -> Future[ImportCleanupResult]:
        """Cancel active work or choose rollback when awaiting commit approval."""

        with self._condition:
            if self._state == ImportSessionState.CREATED:
                self._state = ImportSessionState.CANCELLED_BEFORE_START
                self._safe_code = "CANCELLED_BEFORE_START"
                release_now = True
            elif self._state == ImportSessionState.QUEUED:
                future = self._run_future
                if future is not None:
                    future.cancel()
                self._cancel.set()
                release_now = False
            elif self._state == ImportSessionState.RUNNING:
                self._cancel.set()
                self._state = ImportSessionState.CANCEL_PENDING
                self._condition.notify_all()
                release_now = False
            elif self._state == ImportSessionState.READY_TO_COMMIT:
                self._choose_decision_locked("rollback")
                self._condition.notify_all()
                release_now = False
            elif self._waiting_for_close_retry:
                self._retry_close_locked()
                return self._cleanup_future
            elif self._driver is not None and self._cleanup_code == "CONNECTION_CLOSE_FAILED":
                # The decision future can be observed just before the owner
                # enters its retry wait. Preserve the close request across
                # that small hand-off window.
                self._retry_close_locked()
                return self._cleanup_future
            else:
                return self._cleanup_future
        if release_now:
            close_import_snapshot(self.request.approved)
            self._finish_without_connection()
        return self._cleanup_future

    def retry_cleanup(self) -> Future[ImportCleanupResult]:
        with self._condition:
            if (
                self._driver is None
                or not self._waiting_for_close_retry
                and self._cleanup_code != "CONNECTION_CLOSE_FAILED"
            ):
                return _error_future(ImportSessionError("CLEANUP_NOT_PENDING"))
            future: Future[ImportCleanupResult] = Future()
            future.set_running_or_notify_cancel()
            self._retry_futures.append(future)
            self._retry_close = True
            self._condition.notify_all()
            return future

    def _request_decision(self, action: str) -> Future[ImportDecisionResult]:
        with self._condition:
            if self._state != ImportSessionState.READY_TO_COMMIT or self._decision is not None:
                return _error_future(ImportSessionError("IMPORT_NOT_READY"))
            future: Future[ImportDecisionResult] = Future()
            future.set_running_or_notify_cancel()
            self._decision_future = future
            self._choose_decision_locked(action)
            self._condition.notify_all()
            return future

    def _retry_close_locked(self) -> None:
        self._retry_close = True
        self._condition.notify_all()

    def _choose_decision_locked(self, action: str) -> None:
        self._decision = action
        self._state = (
            ImportSessionState.COMMITTING if action == "commit" else ImportSessionState.ROLLING_BACK
        )

    def _owner_main(self) -> None:
        run_future = self._run_future
        if run_future is None:
            self._finish_without_connection()
            return
        with self._condition:
            if not run_future.set_running_or_notify_cancel():
                self._state = ImportSessionState.CANCELLED_BEFORE_START
                self._safe_code = "CANCELLED_BEFORE_START"
                cancelled_before_start = True
            else:
                self._state = ImportSessionState.RUNNING
                cancelled_before_start = False
        if cancelled_before_start:
            close_import_snapshot(self.request.approved)
            self._finish_without_connection()
            return

        try:
            self._check_cancel()
            self._check_approved_snapshot()
            driver = self._create_driver_owner()
            self._check_cancel()
            metadata, dialect, ref = self._inspect_target_owner(driver)
            plan = _build_insert_plan(
                metadata,
                self.request.approved,
                dialect,
                expected_target=self.request.target,
                expected_ref=ref,
            )
            self._run_insert_owner(driver, plan)
            self._check_cancel()
            with self._condition:
                # Cancellation can arrive after the preceding check but
                # before this lock is acquired. Recheck while publishing the
                # commit-ready state so a cancelled run cannot race into a
                # user commit decision.
                self._check_cancel()
                if self._rows_processed == 0:
                    raise ImportSessionError("EMPTY_IMPORT")
                self._state = ImportSessionState.READY_TO_COMMIT
                self._transaction_outcome = "pending_user_decision"
                result = ImportRunResult(
                    ImportSessionState.READY_TO_COMMIT,
                    self._rows_processed,
                    self._chunks_processed,
                )
                self._condition.notify_all()
                if not run_future.done():
                    run_future.set_result(result)
                self._condition.wait_for(
                    lambda: self._decision is not None or self._cancel.is_set()
                )
                decision = self._decision or "rollback"
                if decision == "rollback":
                    self._state = ImportSessionState.ROLLING_BACK
            self._finish_user_decision_owner(driver, decision)
        except BaseException as error:
            code = _safe_error_code(error)
            self._fail_and_rollback_owner(code)

    def _create_driver_owner(self) -> ImportDriver:
        try:
            driver = self._driver_factory(self.request.target)
        except Exception:
            raise ImportSessionError("CONNECTION_FAILED") from None
        if driver is None:
            raise ImportSessionError("CONNECTION_FAILED")
        with self._condition:
            # Retain the owner before any adapter call so even setup failures
            # can be rolled back and closed on this same thread.
            self._driver = driver
        required = ("configure_manual_commit", "execute", "executemany", "commit", "rollback", "close")
        if any(not callable(getattr(driver, name, None)) for name in required):
            raise ImportSessionError("DRIVER_UNAVAILABLE")
        try:
            driver.configure_manual_commit()
        except Exception:
            raise ImportSessionError("AUTOCOMMIT_CONTROL_UNVERIFIED") from None
        return driver

    def _inspect_target_owner(
        self,
        driver: ImportDriver,
    ) -> tuple[TableSourceMetadata, SqlDialectPolicy, VerifiedTableRef]:
        try:
            ref = self.request.tree_service.resolve_table_source(
                self.request.tree_target,
                self.request.node,
                generation=self.request.tree_generation,
            )
            validate_table_target(
                ref,
                self.request.tree_target,
                self.request.target,
                self.request.choice,
            )
            dialect = create_changeset_dialect(self.request.choice, self.request.target)
            self._check_cancel()
            metadata = inspect_table(
                driver,
                ref,
                self.request.target,
                self.request.choice,
                dialect,
                self._cancel,
            )
            self._check_cancel()
            current_ref = self.request.tree_service.resolve_table_source(
                self.request.tree_target,
                self.request.node,
                generation=self.request.tree_generation,
            )
            validate_table_target(
                current_ref,
                self.request.tree_target,
                self.request.target,
                self.request.choice,
            )
            if current_ref != ref:
                raise ImportSessionError("STALE_TABLE_REF")
            if metadata.target != self.request.target or metadata.signed_ref != ref:
                raise ImportSessionError("TABLE_METADATA_SCOPE_MISMATCH")
            return metadata, dialect, ref
        except ImportSessionError:
            raise
        except ObjectTreeError as error:
            code = "STALE_TABLE_REF" if error.code == "STALE_NODE" else "TABLE_TARGET_MISMATCH"
            raise ImportSessionError(code) from None
        except TableSourceError as error:
            raise ImportSessionError(error.code) from None
        except ChangeSetError as error:
            code = "UNSUPPORTED_TABLE_DIALECT" if error.code == "UNSUPPORTED_SQL_DIALECT" else "TABLE_TARGET_MISMATCH"
            raise ImportSessionError(code) from None
        except Exception:
            raise ImportSessionError("TABLE_METADATA_FAILED") from None

    def _run_insert_owner(self, driver: ImportDriver, plan: _InsertPlan) -> None:
        chunks = iter_import_chunks(
            self.request.approved,
            cancel_check=self._cancel.is_set,
        )
        try:
            for chunk in chunks:
                self._check_cancel()
                if chunk.target_names != plan.target_names or not chunk.rows:
                    raise ImportSessionError("IMPORT_PLAN_MISMATCH")
                parameter_rows = tuple(
                    plan.dialect.bind_values(row).parameters
                    for row in chunk.rows
                )
                self._validate_chunk_rows(plan, chunk)
                cursor = None
                try:
                    cursor = driver.executemany(plan.sql, parameter_rows)
                    try:
                        rowcount = cursor.rowcount
                    except Exception:
                        raise ImportSessionError("ROWCOUNT_UNKNOWN") from None
                except ImportSessionError:
                    raise
                except Exception:
                    raise ImportSessionError("INSERT_EXECUTION_FAILED") from None
                finally:
                    if cursor is not None:
                        try:
                            cursor.close()
                        except Exception:
                            raise ImportSessionError("CURSOR_CLOSE_FAILED") from None
                if isinstance(rowcount, bool) or not isinstance(rowcount, int):
                    raise ImportSessionError("ROWCOUNT_UNKNOWN")
                if rowcount < 0:
                    raise ImportSessionError("ROWCOUNT_UNKNOWN")
                if rowcount != len(chunk.rows):
                    raise ImportSessionError("ROWCOUNT_MISMATCH")
                with self._condition:
                    self._rows_processed += rowcount
                    self._chunks_processed += 1
                    self._condition.notify_all()
                self._check_cancel()
        finally:
            chunks.close()

    @staticmethod
    def _validate_chunk_rows(plan: _InsertPlan, chunk: ImportChunk) -> None:
        for row in chunk.rows:
            if len(row) != len(plan.columns):
                raise ImportSessionError("IMPORT_PLAN_MISMATCH")
            for value, policy in zip(row, plan.columns):
                target = policy.target
                column = policy.metadata
                if value is None:
                    if not target.nullable or not column.nullable:
                        raise ImportSessionError("NULL_NOT_ALLOWED")
                    continue
                if target.kind is ImportValueKind.TEXT:
                    if not isinstance(value, str):
                        raise ImportSessionError("IMPORT_VALUE_TYPE_MISMATCH")
                    if policy.max_characters is not None and len(value) > policy.max_characters:
                        raise ImportSessionError("VALUE_OUT_OF_RANGE")
                    if (
                        policy.max_bytes is not None
                        and _conservative_encoded_size(value, plan.dialect) > policy.max_bytes
                    ):
                        raise ImportSessionError("VALUE_OUT_OF_RANGE")
                elif target.kind is ImportValueKind.INTEGER:
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise ImportSessionError("IMPORT_VALUE_TYPE_MISMATCH")
                    if (
                        policy.minimum_integer is not None
                        and value < policy.minimum_integer
                        or policy.maximum_integer is not None
                        and value > policy.maximum_integer
                    ):
                        raise ImportSessionError("VALUE_OUT_OF_RANGE")
                elif target.kind is ImportValueKind.DECIMAL:
                    if not isinstance(value, Decimal) or not value.is_finite():
                        raise ImportSessionError("IMPORT_VALUE_TYPE_MISMATCH")
                elif target.kind is ImportValueKind.DATE:
                    from datetime import date, datetime

                    if not isinstance(value, date) or isinstance(value, datetime):
                        raise ImportSessionError("IMPORT_VALUE_TYPE_MISMATCH")
                elif target.kind is ImportValueKind.DATETIME:
                    from datetime import datetime

                    if not isinstance(value, datetime):
                        raise ImportSessionError("IMPORT_VALUE_TYPE_MISMATCH")

    def _finish_user_decision_owner(self, driver: ImportDriver, decision: str) -> None:
        failure_code = ""
        if decision == "commit":
            try:
                driver.commit()
            except Exception:
                failure_code = "COMMIT_OUTCOME_UNKNOWN"
                with self._condition:
                    self._state = ImportSessionState.UNKNOWN
                    self._safe_code = failure_code
                    self._transaction_outcome = "unknown"
            else:
                with self._condition:
                    self._state = ImportSessionState.COMMITTED
                    self._transaction_outcome = "committed"
        else:
            try:
                driver.rollback()
            except Exception:
                failure_code = "ROLLBACK_OUTCOME_UNKNOWN"
                with self._condition:
                    self._state = ImportSessionState.UNKNOWN
                    self._safe_code = failure_code
                    self._transaction_outcome = "unknown"
            else:
                with self._condition:
                    self._state = ImportSessionState.ROLLED_BACK
                    self._transaction_outcome = "rolled_back"
        close_ok = self._close_driver_owner()
        if not failure_code and not close_ok:
            self._cleanup_code = "CONNECTION_CLOSE_FAILED"
        self._release_snapshot()
        with self._condition:
            result = ImportDecisionResult(
                state=self._state,
                committed=decision == "commit" and not failure_code,
                rolled_back=decision == "rollback" and not failure_code,
                rows_processed=self._rows_processed,
                cleanup_complete=close_ok,
                safe_code=failure_code or ("" if close_ok else "CONNECTION_CLOSE_FAILED"),
            )
            decision_future = self._decision_future
            if failure_code:
                if decision_future is not None and not decision_future.done():
                    decision_future.set_exception(ImportSessionError(failure_code))
            elif decision_future is not None and not decision_future.done():
                decision_future.set_result(result)
        if not close_ok:
            self._wait_for_close_retry_owner()

    def _fail_and_rollback_owner(self, code: str) -> None:
        safe_code = code or "IMPORT_FAILED"
        driver = self._driver
        if driver is None:
            with self._condition:
                if safe_code == "CANCELLED":
                    self._state = ImportSessionState.CANCELLED_BEFORE_START
                else:
                    self._state = ImportSessionState.FAILED
                self._safe_code = safe_code
                self._transaction_outcome = "none"
            self._release_snapshot()
            self._finish_without_connection()
            self._complete_run_error(safe_code)
            return
        with self._condition:
            self._state = ImportSessionState.ROLLING_BACK
        try:
            driver.rollback()
        except Exception:
            safe_code = "ROLLBACK_OUTCOME_UNKNOWN"
            with self._condition:
                self._state = ImportSessionState.UNKNOWN
                self._safe_code = safe_code
                self._transaction_outcome = "unknown"
        else:
            with self._condition:
                self._state = ImportSessionState.ROLLED_BACK if safe_code == "CANCELLED" else ImportSessionState.FAILED
                self._safe_code = safe_code
                self._transaction_outcome = "rolled_back"
        self._release_snapshot()
        close_ok = self._close_driver_owner()
        self._complete_run_error(safe_code)
        if not close_ok:
            self._cleanup_code = "CONNECTION_CLOSE_FAILED"
            self._wait_for_close_retry_owner()

    def _complete_run_error(self, code: str) -> None:
        future = self._run_future
        if future is not None and not future.done():
            future.set_exception(ImportSessionError(code))

    def _close_driver_owner(self) -> bool:
        driver = self._driver
        if driver is None:
            self._finish_without_connection()
            return True
        try:
            driver.close()
        except Exception:
            with self._condition:
                self._cleanup_code = "CONNECTION_CLOSE_FAILED"
                self._condition.notify_all()
            return False
        with self._condition:
            self._driver = None
            self._condition.notify_all()
        self._finish_without_connection()
        return True

    def _wait_for_close_retry_owner(self) -> None:
        while True:
            with self._condition:
                self._waiting_for_close_retry = True
                self._condition.notify_all()
                self._condition.wait_for(lambda: self._retry_close)
                self._retry_close = False
                self._waiting_for_close_retry = False
                driver = self._driver
                futures = tuple(self._retry_futures)
                self._retry_futures.clear()
            close_ok = False
            try:
                if driver is not None:
                    driver.close()
                close_ok = True
            except Exception:
                close_ok = False
            if close_ok:
                with self._condition:
                    self._driver = None
                    self._cleanup_code = ""
                    self._condition.notify_all()
                self._finish_without_connection()
                result = ImportCleanupResult(True, False)
                for future in futures:
                    if not future.done():
                        future.set_result(result)
                return
            result = ImportCleanupResult(False, True, "CONNECTION_CLOSE_FAILED")
            for future in futures:
                if not future.done():
                    future.set_result(result)

    def _finish_without_connection(self) -> None:
        if self._driver is not None:
            return
        try:
            close_import_snapshot(self.request.approved)
        except Exception:
            pass
        if not self._cleanup_future.done():
            try:
                self._cleanup_future.set_result(
                    ImportCleanupResult(True, False, self._cleanup_code)
                )
            except Exception:
                pass

    def _release_snapshot(self) -> None:
        try:
            close_import_snapshot(self.request.approved)
        except Exception:
            pass

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise ImportSessionError("CANCELLED")

    def _check_approved_snapshot(self) -> None:
        snapshot = getattr(self.request.approved, "_snapshot", None)
        if snapshot is None or bool(getattr(snapshot, "closed", True)):
            raise ImportSessionError("SOURCE_CHANGED")


def _error_future(error: BaseException) -> Future:
    future: Future = Future()
    future.set_exception(error)
    return future


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, ImportSessionError):
        return error.code
    if isinstance(error, ImportPreviewError):
        return error.code
    if isinstance(error, TableSourceError):
        return error.code
    if isinstance(error, ObjectTreeError):
        return "STALE_TABLE_REF" if error.code == "STALE_NODE" else "TABLE_TARGET_MISMATCH"
    if isinstance(error, ChangeSetError):
        return "UNSUPPORTED_TABLE_DIALECT" if error.code == "UNSUPPORTED_SQL_DIALECT" else "TABLE_TARGET_MISMATCH"
    return "IMPORT_FAILED"


def _build_insert_plan(
    metadata: TableSourceMetadata,
    approved: ApprovedImport,
    dialect: SqlDialectPolicy,
    *,
    expected_target: ManualSessionTarget,
    expected_ref: VerifiedTableRef,
) -> _InsertPlan:
    if (
        not isinstance(metadata, TableSourceMetadata)
        or not isinstance(expected_target, ManualSessionTarget)
        or not isinstance(expected_ref, VerifiedTableRef)
        or metadata.target != expected_target
        or metadata.signed_ref != expected_ref
        or metadata.signed_ref.kind != "table"
        or metadata.is_base_table is not True
    ):
        raise ImportSessionError("TABLE_NOT_WRITABLE")
    if not metadata.columns:
        raise ImportSessionError("TABLE_METADATA_INVALID")
    for column in metadata.columns:
        if not isinstance(column, TableColumnMetadata):
            raise ImportSessionError("TABLE_METADATA_INVALID")
        if column.generated is not True and column.generated is not False:
            raise ImportSessionError("GENERATION_METADATA_UNKNOWN")
        if (
            not isinstance(column.nullable, bool)
            or (column.has_default is not None and not isinstance(column.has_default, bool))
            or not isinstance(column.required_on_insert, bool)
            or column.required_on_insert
            != (not column.nullable and column.generated is False and column.has_default is not True)
        ):
            raise ImportSessionError("INSERT_METADATA_INCONSISTENT")
    by_name = {column.name: column for column in metadata.columns}
    if len(by_name) != len(metadata.columns):
        raise ImportSessionError("TABLE_METADATA_INVALID")
    targets = {target.name: target for target in approved.targets}
    if len(targets) != len(approved.targets):
        raise ImportSessionError("INVALID_MAPPING")
    selected: list[_ColumnWritePolicy] = []
    selected_names = tuple(mapping.target_name for mapping in approved.mapping)
    if not selected_names or len(set(selected_names)) != len(selected_names):
        raise ImportSessionError("INVALID_MAPPING")
    for name in selected_names:
        column = by_name.get(name)
        target = targets.get(name)
        if column is None or target is None:
            raise ImportSessionError("SCHEMA_CHANGED")
        if column.generated is not False:
            raise ImportSessionError("GENERATED_COLUMN_MAPPED")
        derived = _column_policy(column, metadata.signed_ref.target.provider, target)
        if derived.target != target:
            raise ImportSessionError("SCHEMA_CHANGED")
        selected.append(derived)
    selected_set = set(selected_names)
    for column in metadata.columns:
        if column.name not in selected_set and column.required_on_insert:
            raise ImportSessionError("REQUIRED_COLUMN_UNMAPPED")
    try:
        table_sql = dialect.qualified_table(metadata.target, metadata.signed_ref.raw_name)
        column_sql = ", ".join(dialect.quote_identifier(item.metadata.name) for item in selected)
        bound = dialect.bind_values((None,) * len(selected))
        if len(bound.markers) != len(selected) or any(not marker for marker in bound.markers):
            raise ImportSessionError("DIALECT_BIND_MISMATCH")
        sql = f"INSERT INTO {table_sql} ({column_sql}) VALUES ({', '.join(bound.markers)})"
    except ImportSessionError:
        raise
    except Exception:
        raise ImportSessionError("TABLE_IDENTIFIER_UNSAFE") from None
    return _InsertPlan(sql, selected_names, tuple(selected), dialect)


def _column_policy(
    column: TableColumnMetadata,
    provider: str,
    target: TargetColumn | None,
) -> _ColumnWritePolicy:
    provider = provider.casefold()
    if not isinstance(column.type_name, str) or not column.type_name:
        raise ImportSessionError("TYPE_METADATA_UNKNOWN")
    type_name = column.type_name.strip().casefold()
    if not isinstance(column.nullable, bool) or (
        target is not None and target.nullable != column.nullable
    ):
        raise ImportSessionError("SCHEMA_CHANGED")

    if provider in {"mysql", "oceanbase_mysql"}:
        decimal_match = _MYSQL_DECIMAL_RE.fullmatch(type_name)
        if decimal_match:
            precision, scale = (int(value) for value in decimal_match.groups())
            if precision > 65 or scale > 30 or scale > precision:
                raise ImportSessionError("NUMERIC_METADATA_UNKNOWN")
            selected = TargetColumn(column.name, ImportValueKind.DECIMAL, column.nullable, precision, scale)
            return _require_target_match(selected, target, column)
        integer_match = _MYSQL_INTEGER_RE.fullmatch(type_name)
        if integer_match:
            base_name, display_width = integer_match.groups()
            if base_name.casefold() == "tinyint" and display_width == "1":
                raise ImportSessionError("UNSUPPORTED_TARGET_TYPE")
            unsigned = "unsigned" in type_name or "zerofill" in type_name
            lower, signed_upper, unsigned_upper = _MYSQL_INT_BOUNDS[base_name.casefold()]
            minimum = 0 if unsigned else lower
            maximum = unsigned_upper if unsigned else signed_upper
            selected = TargetColumn(column.name, ImportValueKind.INTEGER, column.nullable)
            matched = _require_target_match(selected, target, column)
            return _ColumnWritePolicy(column, matched.target, minimum_integer=minimum, maximum_integer=maximum)
        char_match = _MYSQL_CHAR_RE.fullmatch(type_name)
        if char_match:
            _, characters = char_match.groups()
            selected = TargetColumn(column.name, ImportValueKind.TEXT, column.nullable)
            matched = _require_target_match(selected, target, column)
            return _ColumnWritePolicy(column, matched.target, max_characters=int(characters))
        if type_name in _MYSQL_TEXT_BYTES:
            selected = TargetColumn(column.name, ImportValueKind.TEXT, column.nullable)
            matched = _require_target_match(selected, target, column)
            return _ColumnWritePolicy(column, matched.target, max_bytes=_MYSQL_TEXT_BYTES[type_name])
        if type_name == "date":
            selected = TargetColumn(column.name, ImportValueKind.DATE, column.nullable)
            return _require_target_match(selected, target, column)
        if _MYSQL_TEMPORAL_RE.fullmatch(type_name):
            selected = TargetColumn(column.name, ImportValueKind.DATETIME, column.nullable)
            return _require_target_match(selected, target, column)
        raise ImportSessionError("UNSUPPORTED_TARGET_TYPE")

    if provider in {"oracle", "oceanbase_oracle"}:
        if type_name in {"number", "numeric", "decimal"}:
            precision, scale = column.data_precision, column.data_scale
            if (
                isinstance(precision, bool)
                or not isinstance(precision, int)
                or precision <= 0
                or precision > 38
                or isinstance(scale, bool)
                or not isinstance(scale, int)
                or scale < 0
            ):
                raise ImportSessionError("NUMERIC_METADATA_UNKNOWN")
            selected = TargetColumn(column.name, ImportValueKind.DECIMAL, column.nullable, precision, scale)
            return _require_target_match(selected, target, column)
        if type_name in {"char", "nchar", "varchar2", "nvarchar2"}:
            if (
                isinstance(column.data_length, bool)
                or not isinstance(column.data_length, int)
                or column.data_length <= 0
            ):
                raise ImportSessionError("COLUMN_LENGTH_UNKNOWN")
            selected = TargetColumn(column.name, ImportValueKind.TEXT, column.nullable)
            matched = _require_target_match(selected, target, column)
            return _ColumnWritePolicy(column, matched.target, max_bytes=column.data_length)
        if type_name in {"clob", "nclob"}:
            selected = TargetColumn(column.name, ImportValueKind.TEXT, column.nullable)
            return _require_target_match(selected, target, column)
        if type_name == "date" or _ORACLE_TIMESTAMP_RE.fullmatch(type_name):
            selected = TargetColumn(column.name, ImportValueKind.DATETIME, column.nullable)
            return _require_target_match(selected, target, column)
        raise ImportSessionError("UNSUPPORTED_TARGET_TYPE")
    raise ImportSessionError("UNSUPPORTED_PROVIDER")


def _require_target_match(
    selected: TargetColumn,
    requested: TargetColumn | None,
    column: TableColumnMetadata,
) -> _ColumnWritePolicy:
    if requested is not None and selected != requested:
        raise ImportSessionError("SCHEMA_CHANGED")
    return _ColumnWritePolicy(column, selected)


def _conservative_encoded_size(value: str, dialect: SqlDialectPolicy) -> int:
    # MySQL utf8mb4 needs at most four bytes per code point. Oracle's legacy
    # UTF8 uses CESU-8 for supplementary points, so reserve up to six bytes.
    if type(dialect).__name__.startswith("Oracle"):
        return sum(
            1 if ord(char) <= 0x7F else
            2 if ord(char) <= 0x7FF else
            3 if ord(char) <= 0xFFFF else 6
            for char in value
        )
    return len(value.encode("utf-8", "strict"))


__all__ = [
    "DriverFactory",
    "ImportTargetCandidate",
    "ImportTargetCandidates",
    "ImportCleanupResult",
    "ImportDecisionResult",
    "ImportDriver",
    "ImportRunResult",
    "ImportSession",
    "ImportSessionError",
    "ImportSessionRequest",
    "ImportSessionState",
    "ImportSessionStatus",
    "candidate_target_columns",
]
