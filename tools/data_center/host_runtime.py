"""Pure-Python composition boundary for one data-center Agent run.

``DataCenterAgentRuntime`` binds one immutable :class:`RunContext` to an
injected model, relational query worker, metadata host, and NoSQL router.  It
does not load configuration or credentials at import time and it does not
construct a database client on the caller thread.  Draft mode deliberately
builds only the model/tool envelope and returns closed, no-I/O tool results.
"""

from __future__ import annotations

from collections.abc import Mapping
import contextvars
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import inspect
import json
import math
import re
import threading
import time
import uuid
from typing import Any, Callable

from sqlglot import ErrorLevel, exp, parse
from sqlglot.optimizer.scope import traverse_scope

from tools.agent_core.context import ContextLimits, ContextPlanner
from tools.agent_diag import log_agent_event, log_file_path
from tools.agent_core.text_protocol import (
    TEXT_PROTOCOL_INSTRUCTION,
    parse_qwen_tool_calls,
    visible_model_text,
)

from .agent import AgentRunner
from .agent_query_tool import AgentQueryTool
from .budget import Budget, BudgetLimits
from .contracts import AgentTurn, CancellationToken, RunContext, ToolCall, ToolResult
from .context_evidence import DataCenterEvidencePlanner, EvidenceEpoch
from .host_metadata import MetadataHost, MetadataTarget
from .host_model_adapter import AgentModelHostAdapter
from .host_nosql import HostNoSQLRouter
from .policy import MONGO_READ_OPERATIONS, REDIS_READ_OPERATIONS, DataCenterPolicy
from .nosql_codec import encode_bounded, is_sensitive_name, utf8_size
from .query_executor import QueryExecutor
from .readonly_driver_adapters import canonical_provider
from .result_projection import (
    DEFAULT_MAX_CUMULATIVE_BYTES, DEFAULT_MAX_RESULT_BYTES,
    ProjectionBudget, ResultProjector, _public_failure_message,
)
from .session_manager import SessionManager
from .sql_policy import SQLDecision, SQLPolicy
from .tool_registry import DataCenterToolRegistry, QueryOwnerTracker


_TRUSTED_FUTURE_ADD_DONE_CALLBACK = Future.add_done_callback


class RuntimeConfigurationError(RuntimeError):
    """Raised when the fixed host composition is incomplete."""


_RELATIONAL = frozenset({"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql"})
_NOSQL = frozenset({"redis", "mongodb"})
_QUERY_NAME = "query_readonly"
_NOSQL_NAMES = {"redis_read", "mongo_read"}
_METADATA_NAMES = {"search_objects", "describe_object"}
_IDENTITY_KEYS = {
    "object_id",
    "page_token",
    "next_page_token",
    "collection_id",
    "query_id",
    "evidence_id",
    "name",
    "kind",
    "schema",
    "database",
    "provider",
    "mode",
    "type",
    "value_type",
    "sample_type",
    "column",
    "data_type",
}
_COMMENT_KEYS = {"comment", "comments", "description", "remark", "remarks"}
_SAFE_CODE = {
    "OK",
    "ARGUMENT_INVALID",
    "CAPABILITY_UNAVAILABLE",
    "CANCELLED",
    "DRAFT_NO_DATABASE",
    "METADATA_SCOPE_UNAVAILABLE",
    "NEEDS_CLARIFICATION",
    "PROFILE_MISMATCH",
    "PROFILE_UNAVAILABLE",
    "QUERY_FAILED",
    "READ_GUARD_NOT_READY",
    "SCOPE_DENIED",
    "STALE_CURSOR",
    "TIMEOUT",
    "TOOL_FAILED",
    "UNSUPPORTED_COMBINATION",
}
_SAFE_TEXT = re.compile(r"[^\x00\r\n]")


def _canonical_provider(value: Any, mode: Any = "") -> str:
    raw = str(value or "").strip().casefold().replace("-", "_")
    if raw == "redis":
        return "redis"
    if raw in {"mongo", "mongodb", "mongo_db"}:
        return "mongodb"
    try:
        return canonical_provider(raw, mode)
    except Exception:
        raise RuntimeConfigurationError("unsupported runtime provider") from None


def _dialect(provider: str) -> str:
    return "mysql" if provider in {"mysql", "oceanbase_mysql"} else "oracle"


def _safe_code(value: Any, *, ok: bool) -> str:
    code = str(value or "").strip().upper()
    return code if code in _SAFE_CODE else ("OK" if ok else "TOOL_FAILED")


def _safe_id(value: Any) -> str | None:
    text = str(value or "").strip().replace("\x00", "")
    return text[:512] or None


def _normalized_key_for(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _cancelled(token: Any) -> bool:
    if token is None:
        return False
    checker = getattr(token, "is_cancelled", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return True
    return bool(getattr(token, "cancelled", False))


def _nosql_stop_code(cancellation: Any, deadline: float | None) -> str | None:
    if _deadline_expired(deadline):
        return "TIMEOUT"
    if not _cancelled(cancellation):
        return None
    try:
        reason = str(getattr(cancellation, "reason", "") or "").casefold()
    except Exception:
        reason = ""
    return "TIMEOUT" if "timeout" in reason else "CANCELLED"


def _invoke(callable_value: Any, values: Mapping[str, Any], fallback: tuple[Any, ...] = ()) -> Any:
    if not callable(callable_value):
        callable_value = getattr(callable_value, "connect", None)
    if not callable(callable_value):
        raise TypeError("runtime dependency is not callable")
    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return callable_value(*fallback)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        explicit = {
            item.name
            for item in params
            if item.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        selected = explicit or set(values)
        return callable_value(**{key: values[key] for key in selected if key in values})
    positional: list[Any] = []
    fallback_index = 0
    for item in params:
        if item.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            continue
        if item.name in values:
            positional.append(values[item.name])
        elif item.default is inspect.Parameter.empty and fallback_index < len(fallback):
            positional.append(fallback[fallback_index])
            fallback_index += 1
        elif item.default is inspect.Parameter.empty:
            raise TypeError("runtime dependency signature is unsupported")
    kwargs = {
        item.name: values[item.name]
        for item in params
        if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
    }
    return callable_value(*positional, **kwargs)


def _close(value: Any) -> None:
    method = getattr(value, "close", None)
    if callable(method):
        try:
            method()
        except Exception:
            pass


def _scope_name(value: Any) -> str:
    """Normalize an identifier for the fixed-host scope comparison."""

    return str(value or "").strip().strip('"`[]').casefold()


def _quoted_identifier(value: Any) -> bool:
    return bool(getattr(value, "args", {}).get("quoted", False))


class TargetSQLPolicy:
    """Apply the normal SELECT policy plus the run's physical table scope.

    :class:`SQLPolicy` intentionally validates syntax and read-only semantics,
    but it does not know which database or schema a run owns.  This wrapper
    resolves SQLGlot table parts after that validation and rejects a catalog,
    schema, or unqualified-table resolution outside the immutable
    :class:`RunContext`.  CTE aliases are excluded from the physical table
    check; their underlying tables remain in the AST and are checked.
    """

    def __init__(self, *, provider: str, database: str, schemas: tuple[str, ...], base: Any = None) -> None:
        self.provider = str(provider or "").strip().casefold()
        self.database = _scope_name(database)
        self._database_exact = str(database or "").strip().strip('"`[]')
        self._schemas_exact = tuple(dict.fromkeys(str(item or "").strip().strip('"`[]') for item in schemas if str(item or "").strip()))
        self.schemas = tuple(dict.fromkeys(_scope_name(item) for item in schemas if _scope_name(item)))
        self._base = base or SQLPolicy()
        self.version = getattr(self._base, "version", "dc-02")

    def validate(self, sql: str, dialect: str = "oracle") -> SQLDecision:
        try:
            decision = self._base.validate(sql, dialect)
        except Exception:
            return SQLDecision(False, "SQL_INVALID", "SQL 解析失败", dialect=str(dialect or ""), sql=str(sql or ""))
        if not isinstance(decision, SQLDecision) or not decision.allowed:
            return decision if isinstance(decision, SQLDecision) else SQLDecision(False, "POLICY_DENIED", "SQL 策略拒绝")
        if not self.schemas:
            return self._denied(decision, "scope_schema_missing")
        try:
            statements = parse(decision.sql, read=str(decision.dialect or dialect), error_level=ErrorLevel.RAISE)
            statement = statements[0] if len(statements) == 1 else None
        except Exception:
            statement = None
        if statement is None:
            return self._denied(decision, "scope_parse_failed")
        physical_tables: list[Any] = []
        for scope in traverse_scope(statement):
            physical_tables.extend(source for source in scope.sources.values() if isinstance(source, exp.Table))
        for table in physical_tables:
            table_name = _scope_name(table.name)
            schema_name = _scope_name(table.db)
            catalog_name = _scope_name(table.catalog)
            if "@" in str(table.name or ""):
                return self._denied(decision, "scope_database_link_denied")
            if not schema_name:
                # A connection's current schema/database is outside this
                # wrapper's proof.  Require the model to use the explicit
                # object scope returned by metadata discovery.
                return self._denied(decision, "scope_default_schema_unbound")
            if _quoted_identifier(table.args.get("db")) and str(table.db).strip().strip('"`[]') not in self._schemas_exact:
                return self._denied(decision, "scope_quoted_schema_ambiguous")
            if catalog_name and (not self.database or catalog_name != self.database):
                return self._denied(decision, "scope_catalog_denied")
            if catalog_name and _quoted_identifier(table.args.get("catalog")) and str(table.catalog).strip().strip('"`[]') != self._database_exact:
                return self._denied(decision, "scope_quoted_catalog_ambiguous")
            if schema_name not in self.schemas:
                return self._denied(decision, "scope_schema_denied")
        evidence = tuple(decision.evidence) + (
            "scope=target_bound",
            f"scope_database={self.database or 'current'}",
            f"scope_schema_count={len(self.schemas)}",
        )
        return SQLDecision(
            True,
            code=decision.code,
            message=decision.message,
            dialect=decision.dialect,
            sql=decision.sql,
            tables=decision.tables,
            evidence=evidence,
        )

    decide = validate
    check = validate
    __call__ = validate

    @staticmethod
    def _denied(decision: SQLDecision, reason: str) -> SQLDecision:
        return SQLDecision(
            False,
            "SCOPE_DENIED",
            "SQL 对象超出当前数据中心运行范围",
            dialect=decision.dialect,
            sql=decision.sql,
            tables=decision.tables,
            evidence=tuple(decision.evidence) + (reason,),
        )


class ScopedResultProjector:
    """Shape-preserving model projection shared by every tool in one run."""

    def __init__(
        self,
        *,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_cumulative_bytes: int = DEFAULT_MAX_CUMULATIVE_BYTES,
        max_items: int = 100,
        max_depth: int = 8,
        query_projector: ResultProjector | None = None,
    ) -> None:
        if isinstance(max_result_bytes, bool) or not isinstance(max_result_bytes, int) or max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        if isinstance(max_cumulative_bytes, bool) or not isinstance(max_cumulative_bytes, int) or max_cumulative_bytes <= 0:
            raise ValueError("max_cumulative_bytes must be positive")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 10_000:
            raise ValueError("max_items is invalid")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 16:
            raise ValueError("max_depth is invalid")
        self.max_result_bytes = int(max_result_bytes)
        self.max_cumulative_bytes = int(max_cumulative_bytes)
        self.max_items = int(max_items)
        self.max_depth = int(max_depth)
        self._query_projector = query_projector or ResultProjector(
            max_result_bytes=self.max_result_bytes,
            max_cumulative_bytes=self.max_cumulative_bytes,
        )
        self._budgets: dict[tuple[str, str], ProjectionBudget] = {}
        self._lock = threading.RLock()
        self._active: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
            "data_center_projection_context",
            default=None,
        )

    def activate(self, context: RunContext) -> contextvars.Token[tuple[str, str] | None]:
        key = (str(context.run_id), str(context.tab_id))
        with self._lock:
            self._budgets.setdefault(key, ProjectionBudget(max_bytes=self.max_cumulative_bytes))
        return self._active.set(key)

    def deactivate(self, token: contextvars.Token[tuple[str, str] | None]) -> None:
        self._active.reset(token)

    def release(self, context: RunContext) -> None:
        with self._lock:
            self._budgets.pop((str(context.run_id), str(context.tab_id)), None)

    def _budget(self, supplied: ProjectionBudget | None = None) -> ProjectionBudget:
        key = self._active.get()
        if key is None:
            return supplied or ProjectionBudget(max_bytes=self.max_cumulative_bytes)
        with self._lock:
            return self._budgets.setdefault(key, ProjectionBudget(max_bytes=self.max_cumulative_bytes))

    def project(self, result: Any, *, budget: ProjectionBudget | None = None, **_options: Any) -> ToolResult:
        normalized = ToolResult.from_value(result)
        shared = self._budget(budget)
        if self._is_tabular(normalized.data):
            projected = self._query_projector.project(normalized, budget=shared)
            return ToolResult.from_value(projected.as_dict())
        return self._project_structured(normalized, shared)

    def project_for_context(self, result: Any, context: RunContext) -> ToolResult:
        token = self.activate(context)
        try:
            return self.project(result)
        finally:
            self.deactivate(token)

    def _project_structured(self, result: ToolResult, budget: ProjectionBudget) -> ToolResult:
        ok = bool(result.ok)
        code = _safe_code(result.code, ok=ok)
        if not ok:
            data = {"message": _public_failure_message(result.code, result.data)}
            truncated = False
        else:
            data, truncated = self._shape(result.data)
        limits = self._safe_limits(result.limits)
        limits.update(
            {
                "projection": "scoped_shape",
                "result_bytes": self.max_result_bytes,
                "cumulative_bytes": budget.max_bytes,
            }
        )
        envelope = {
            "ok": ok,
            "code": code,
            "data": data,
            "evidence_id": _safe_id(result.evidence_id),
            "query_id": _safe_id(result.query_id),
            "scope": self._scope(result.scope),
            "truncated": bool(result.truncated or truncated),
            "limits": limits,
            "elapsed_ms": self._safe_elapsed(result.elapsed_ms),
        }
        size = utf8_size(envelope)
        if size > self.max_result_bytes or not budget.reserve(size):
            data = {"truncated": True, "message": "结果超出本轮共享数据预算"}
            envelope["data"] = data
            envelope["truncated"] = True
            limits["truncated"] = True
            size = utf8_size(envelope)
            if size <= self.max_result_bytes and budget.remaining_bytes >= size:
                budget.reserve(size)
            else:
                size = 0
        limits["bytes_used"] = size
        return ToolResult(
            ok=ok,
            code=code,
            data=data,
            evidence_id=_safe_id(result.evidence_id),
            query_id=_safe_id(result.query_id),
            scope=self._scope(result.scope),
            truncated=bool(envelope["truncated"]),
            limits=limits,
            elapsed_ms=self._safe_elapsed(result.elapsed_ms),
        )

    def _shape(self, value: Any, *, key: str = "", depth: int = 0) -> tuple[Any, bool]:
        if depth > self.max_depth:
            return {"type": "truncated", "reason": "depth"}, True
        normalized_key = key.casefold().replace("-", "_")
        if normalized_key in _COMMENT_KEYS or "comment" in normalized_key or "description" in normalized_key:
            return {"type": "untrusted_text", "omitted": True}, True
        if key and is_sensitive_name(key):
            return {"type": "redacted"}, False
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            truncated = False
            for index, (raw_key, raw_value) in enumerate(value.items()):
                if index >= self.max_items:
                    truncated = True
                    break
                source_key = str(raw_key)
                safe_key = "[redacted]" if is_sensitive_name(source_key) else source_key[:512]
                if safe_key != source_key:
                    output[safe_key] = {"type": "redacted"}
                    continue
                if _normalized_key_for(source_key) in {"field_descriptor", "field"} and isinstance(raw_value, Mapping):
                    descriptor_name = str(raw_value.get("name", raw_value.get("field", "")) or "")
                    if is_sensitive_name(descriptor_name):
                        output[safe_key] = {
                            "name": _safe_id(descriptor_name) or "",
                            "value": {"type": "redacted"},
                        }
                        truncated = True
                        continue
                shaped, item_truncated = self._shape(raw_value, key=source_key, depth=depth + 1)
                output[safe_key] = shaped
                truncated = truncated or item_truncated
            return output, truncated
        if isinstance(value, (list, tuple)):
            output: list[Any] = []
            truncated = False
            for item in value[: self.max_items]:
                shaped, item_truncated = self._shape(item, depth=depth + 1)
                output.append(shaped)
                truncated = truncated or item_truncated
            if len(value) > self.max_items:
                truncated = True
            return output, truncated
        if key.casefold() in _IDENTITY_KEYS and isinstance(value, str):
            return _safe_id(value) or "", False
        if value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                return {"type": "number", "value": str(value)}, True
            return value, False
        encoded = encode_bounded(
            value,
            max_bytes=min(self.max_result_bytes, 16 * 1024),
            max_items=self.max_items,
            max_depth=self.max_depth,
        )
        return encoded.value, bool(encoded.truncated)

    @staticmethod
    def _is_tabular(value: Any) -> bool:
        return isinstance(value, Mapping) and (
            "columns" in value and "rows" in value
        )

    @staticmethod
    def _scope(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return None
        allowed = {"connection_id", "profile_revision", "provider", "engine", "mode", "database", "schema", "schemas", "collection_id"}
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key not in allowed or is_sensitive_name(key):
                continue
            if key == "schemas" and isinstance(raw_value, (list, tuple)):
                output[key] = [_safe_id(item) or "" for item in raw_value[:32]]
            elif isinstance(raw_value, (str, int, bool)):
                safe = _safe_id(raw_value) if isinstance(raw_value, str) else raw_value
                if safe is not None:
                    output[key] = safe
        return output

    @staticmethod
    def _safe_limits(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        allowed = {
            "policy_version", "projection", "result_bytes", "cumulative_bytes", "bytes_used",
            "row_limit", "row_count", "limit", "offset", "object_count", "field_count", "truncated",
        }
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key not in allowed:
                continue
            if isinstance(raw_value, bool):
                output[key] = raw_value
            elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
                output[key] = max(-1_000_000_000, min(raw_value, 1_000_000_000))
            elif isinstance(raw_value, float) and math.isfinite(raw_value):
                output[key] = max(-1_000_000_000.0, min(raw_value, 1_000_000_000.0))
            elif isinstance(raw_value, str):
                output[key] = _safe_id(raw_value) or ""
        return output

    @staticmethod
    def _safe_elapsed(value: Any) -> int | float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return max(0, min(value, 86_400_000))
        if isinstance(value, float) and math.isfinite(value):
            return max(0.0, min(value, 86_400_000.0))
        return None


class RuntimeToolRegistry(DataCenterToolRegistry):
    """Closed registry that projects every tool through one run budget."""

    def __init__(
        self,
        executor: Any,
        *,
        projector: ScopedResultProjector | None = None,
        query_owner_tracker: QueryOwnerTracker | None = None,
    ) -> None:
        self.scoped_projector = projector or ScopedResultProjector()
        super().__init__(
            executor,
            policy=DataCenterPolicy(),
            projector=self.scoped_projector,
            query_owner_tracker=query_owner_tracker,
        )

    def execute(self, call: ToolCall | Mapping[str, Any], *, context: RunContext, cancellation: CancellationToken) -> ToolResult:
        token = self.scoped_projector.activate(context)
        try:
            result = super().execute(call, context=context, cancellation=cancellation)
            name = _call_name(call)
            if name.casefold() == _QUERY_NAME:
                return result
            return self.scoped_projector.project(result)
        finally:
            self.scoped_projector.deactivate(token)

    def release_run(self, context: RunContext | str, tab_id: str | None = None) -> None:
        super().release_run(context, tab_id)
        if isinstance(context, RunContext):
            self.scoped_projector.release(context)


def _call_name(call: ToolCall | Mapping[str, Any]) -> str:
    if isinstance(call, ToolCall):
        return str(call.name or "")
    return str(call.get("name", "") or "") if isinstance(call, Mapping) else ""


class _DraftHandler:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, call: ToolCall | Mapping[str, Any], *, context: RunContext | None = None, cancellation: Any = None) -> ToolResult:
        return ToolResult(ok=False, code="DRAFT_NO_DATABASE", data={"message": "draft mode does not access database tools"}, query_id=_call_id(call), scope=context.as_dict() if isinstance(context, RunContext) else None)


class _VerifiedQueryHandler:
    host_only = True
    verified_readonly = True

    def __init__(self, dependency: Any, *, target_policy: TargetSQLPolicy | None = None) -> None:
        self._dependency = dependency
        self._target_policy = target_policy

    def __call__(self, call: ToolCall | Mapping[str, Any], *, context: RunContext, cancellation: Any) -> Any:
        if self._target_policy is not None:
            arguments = _call_arguments(call)
            sql = arguments.get("sql") if isinstance(arguments, Mapping) else None
            decision = self._target_policy.validate(sql, _dialect(self._target_policy.provider))
            if not decision.allowed:
                return ToolResult(
                    ok=False,
                    code=_safe_code(decision.code, ok=False),
                    data={"message": "SQL 对象超出当前数据中心运行范围"},
                    query_id=_call_id(call),
                    scope=context.as_dict(),
                    limits={"policy_version": "dc-02", "scope": "target_bound"},
                )
        method = self._dependency
        if not callable(method):
            for name in ("execute", "run", "query", "execute_query"):
                candidate = getattr(self._dependency, name, None)
                if callable(candidate):
                    method = candidate
                    break
        return _invoke(
            method,
            {
                "call": call,
                "request": call,
                "context": context,
                "cancellation": cancellation,
                "cancel_token": cancellation,
                "deadline": _deadline_from(cancellation),
            },
            fallback=(call,),
        )


class _MetadataHandler:
    def __init__(self, dependency: Any) -> None:
        self._dependency = dependency

    def __call__(self, call: ToolCall | Mapping[str, Any], *, context: RunContext, cancellation: Any) -> Any:
        return _invoke(
            self._dependency,
            {
                "call": call,
                "context": context,
                "cancellation": cancellation,
                "deadline": _deadline_from(cancellation),
            },
            fallback=(call,),
        )


class _RuntimeCancellation:
    """Delegate cancellation while exposing the host-owned run deadline."""

    def __init__(self, delegate: Any, deadline: float | None) -> None:
        self._delegate = delegate
        self.deadline = deadline

    def is_cancelled(self) -> bool:
        return _cancelled(self._delegate) or _deadline_expired(self.deadline)

    @property
    def reason(self) -> str:
        if _deadline_expired(self.deadline):
            return "timeout"
        return str(getattr(self._delegate, "reason", "cancelled") or "cancelled")

    def cancel(self, reason: str = "cancelled") -> None:
        method = getattr(self._delegate, "cancel", None)
        if callable(method):
            method(reason)

    def wait(self, timeout: float | None = None) -> bool:
        remaining = None if self.deadline is None else max(0.0, self.deadline - time.monotonic())
        if timeout is None:
            selected = remaining
        elif remaining is None:
            selected = timeout
        else:
            selected = min(float(timeout), remaining)
        waiter = getattr(self._delegate, "wait", None)
        if callable(waiter):
            return bool(waiter(selected))
        if selected:
            time.sleep(selected)
        return self.is_cancelled()


class _NoSQLOperation:
    """One worker call whose logical result may precede physical completion."""

    _MESSAGES = {
        "TIMEOUT": "read timed out",
        "CANCELLED": "read cancelled",
        "QUERY_FAILED": "NoSQL read failed",
    }

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self._lock = threading.Lock()
        self._stop_code: str | None = None
        self._settled = False
        self._final_result: ToolResult | None = None

    def request_stop(self, code: str) -> str:
        selected = code if code in self._MESSAGES else "QUERY_FAILED"
        with self._lock:
            if not self._settled and self._stop_code is None:
                self._stop_code = selected
            return self._stop_code or selected

    def stop_code(self) -> str | None:
        with self._lock:
            return self._stop_code

    def safe_result(self) -> ToolResult:
        with self._lock:
            code = self._stop_code or "QUERY_FAILED"
            return self._result_for_code(code)

    def final_result_if_settled(self) -> ToolResult | None:
        """Return the immutable logical result without claiming Future completion."""

        with self._lock:
            return self._final_result if self._settled else None

    def select_final_result(
        self,
        result: ToolResult,
        cancellation: Any,
        deadline: float | None,
    ) -> tuple[ToolResult, bool]:
        """Choose success or stop once, at the worker's final retirement edge."""

        with self._lock:
            if self._settled:
                return self._final_result or result, False
            code = self._stop_code or _nosql_stop_code(cancellation, deadline)
            if code is not None:
                self._stop_code = code
                return self._result_for_code(code), True
            self._settled = True
            self._final_result = result
            return result, False

    def retire_stopped_result(self) -> ToolResult:
        with self._lock:
            if not self._settled:
                code = self._stop_code or "QUERY_FAILED"
                self._final_result = self._result_for_code(code)
                self._settled = True
            return self._final_result or self._result_for_code("QUERY_FAILED")

    def _result_for_code(self, code: str) -> ToolResult:
        return ToolResult(
            ok=False,
            code=code,
            data={"message": self._MESSAGES[code]},
            query_id=self.call_id,
        )


class _NoSQLWorker:
    """Serialize NoSQL calls and keep cursor leases on one owner thread."""

    def __init__(
        self,
        *,
        context: RunContext,
        router: Any = None,
        factory: Any = None,
        host: Any = None,
        owner_tracker: QueryOwnerTracker | None = None,
        owns_router: bool = False,
    ) -> None:
        self.context = context
        self.router = router
        self.factory = factory
        self.host = host
        self.owner_tracker = owner_tracker
        self.owns_router = bool(owns_router)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-center-nosql")
        self._lease: Any = None
        self._closed = False
        self._lock = threading.RLock()
        self._generation = 0
        self._active: dict[Future[Any], _NoSQLOperation] = {}
        self._router_close_attempted = False

    def execute(self, tool_name: str, arguments: Mapping[str, Any], *, call_id: str | None, cancellation: Any) -> ToolResult:
        deadline = _deadline_from(cancellation, arguments)
        initial_stop = _nosql_stop_code(cancellation, deadline)
        if initial_stop is not None:
            operation = _NoSQLOperation(str(call_id or ""))
            operation.request_stop(initial_stop)
            return operation.safe_result()
        if type(call_id) is not str or not call_id.strip():
            if self.owner_tracker is not None:
                self.owner_tracker.fail_query_owner(self.context, str(call_id or ""))
            return ToolResult(ok=False, code="TOOL_FAILED", data={"message": "invalid tool call id"}, query_id=call_id)

        operation = _NoSQLOperation(call_id)
        with self._lock:
            if self._closed:
                return ToolResult(ok=False, code="SESSION_NOT_FOUND", data={"message": "NoSQL worker is closed"}, query_id=call_id)
            stop_code = _nosql_stop_code(cancellation, deadline)
            if stop_code is not None:
                operation.request_stop(stop_code)
                return operation.safe_result()
            self._generation += 1
            generation = self._generation
            try:
                future = self._pool.submit(
                    self._run,
                    tool_name,
                    dict(arguments),
                    call_id,
                    cancellation,
                    deadline,
                    operation,
                )
            except Exception:
                if self.owner_tracker is not None:
                    self.owner_tracker.fail_query_owner(self.context, call_id)
                return ToolResult(ok=False, code="QUERY_FAILED", data={"message": "NoSQL read failed"}, query_id=call_id)

            # ThreadPoolExecutor.submit returns the exact stdlib Future. Attach
            # host-owned provenance before either tracking it or waiting.
            future.query_id = call_id
            future.session_id = self.context.tab_id
            future.generation = generation
            self._active[future] = operation
            registered = self.owner_tracker is None
            if self.owner_tracker is not None:
                try:
                    registered = self.owner_tracker.register_query_owner(
                        self.context,
                        call_id,
                        session_id=self.context.tab_id,
                        generation=generation,
                        future=future,
                    )
                except Exception:
                    registered = False
                    self.owner_tracker.fail_query_owner(self.context, call_id)
            try:
                # This auxiliary bookkeeping must not trust a replaceable
                # Future instance method. Owner registration above deliberately
                # remains strict and can reject that same altered protocol.
                _TRUSTED_FUTURE_ADD_DONE_CALLBACK(future, self._forget_future)
            except Exception:
                registered = False
                if self.owner_tracker is not None:
                    self.owner_tracker.fail_query_owner(self.context, call_id)
            if not registered:
                operation.request_stop("QUERY_FAILED")
                future.cancel()
                return operation.safe_result()

        while True:
            # Completion wins a boundary race: the operation is physically
            # complete, so it no longer needs stopped-operation cleanup.
            if future.done():
                try:
                    return future.result()
                except Exception:
                    return ToolResult(ok=False, code="QUERY_FAILED", data={"message": "NoSQL read failed"}, query_id=call_id)
            stop_code = _nosql_stop_code(cancellation, deadline) or operation.stop_code()
            if stop_code is not None:
                operation.request_stop(stop_code)
                settled_result = operation.final_result_if_settled()
                if settled_result is not None:
                    return settled_result
                return operation.safe_result()
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            wait_for = 0.05 if remaining is None else min(0.05, remaining)
            try:
                return future.result(timeout=wait_for)
            except FutureTimeoutError:
                continue
            except Exception:
                return ToolResult(ok=False, code="QUERY_FAILED", data={"message": "NoSQL read failed"}, query_id=call_id)

    def _run(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        call_id: str,
        cancellation: Any,
        deadline: float | None,
        operation: _NoSQLOperation,
    ) -> ToolResult:
        result: ToolResult
        try:
            stop_code = self._stopped_code(operation, cancellation, deadline)
            if stop_code is not None:
                result = operation.safe_result()
            else:
                stale_before_read = self._profile_stale()
                stop_code = self._stopped_code(operation, cancellation, deadline)
                if stop_code is not None:
                    result = operation.safe_result()
                elif stale_before_read:
                    result = ToolResult(ok=False, code="PROFILE_MISMATCH", data={"message": "profile changed"}, query_id=call_id)
                else:
                    lease = self._ensure_lease()
                    stop_code = self._stopped_code(operation, cancellation, deadline)
                    if stop_code is not None:
                        result = operation.safe_result()
                    elif lease is None:
                        result = ToolResult(ok=False, code="CAPABILITY_UNAVAILABLE", data={"message": "NoSQL read capability is not configured"}, query_id=call_id)
                    else:
                        method = getattr(lease, "execute", None)
                        if self.router is not None:
                            method = getattr(self.router, "execute", self.router)
                            values = {"tool_name": tool_name, "request": arguments, "context": self.context, "cancellation": cancellation, "query_id": call_id}
                            raw_result = _invoke(method, values, fallback=(tool_name, arguments))
                        elif callable(method):
                            raw_result = _invoke(method, {"request": arguments, "cancellation": cancellation, "query_id": call_id, "context": self.context}, fallback=(arguments,))
                        else:
                            raise TypeError("NoSQL lease has no execute")

                        stop_code = self._stopped_code(operation, cancellation, deadline)
                        if stop_code is not None:
                            result = operation.safe_result()
                        else:
                            stale_after_read = self._profile_stale()
                            stop_code = self._stopped_code(operation, cancellation, deadline)
                            if stop_code is not None:
                                result = operation.safe_result()
                            elif stale_after_read:
                                self._close_lease()
                                result = ToolResult(ok=False, code="PROFILE_MISMATCH", data={"message": "profile changed"}, query_id=call_id)
                            else:
                                normalized = ToolResult.from_value(raw_result)
                                result = ToolResult(
                                    ok=normalized.ok,
                                    code=_safe_code(normalized.code, ok=normalized.ok),
                                    data=normalized.data,
                                    evidence_id=_safe_id(normalized.evidence_id),
                                    query_id=_safe_id(normalized.query_id) or call_id,
                                    scope=self.context.as_dict(),
                                    truncated=normalized.truncated,
                                    limits=ScopedResultProjector._safe_limits(normalized.limits),
                                    elapsed_ms=ScopedResultProjector._safe_elapsed(normalized.elapsed_ms),
                                )
        except Exception:
            self._close_lease()
            stop_code = self._stopped_code(operation, cancellation, deadline)
            if stop_code is not None:
                result = operation.safe_result()
            else:
                result = ToolResult(ok=False, code="QUERY_FAILED", data={"message": "NoSQL read failed"}, query_id=call_id)
        return self._finish_operation(operation, result, cancellation, deadline)

    def _finish_operation(
        self,
        operation: _NoSQLOperation,
        result: ToolResult,
        cancellation: Any,
        deadline: float | None,
    ) -> ToolResult:
        selected, stopped = operation.select_final_result(result, cancellation, deadline)
        if not stopped:
            return selected
        self._cleanup_stopped_operation()
        return operation.retire_stopped_result()

    @staticmethod
    def _stopped_code(
        operation: _NoSQLOperation,
        cancellation: Any,
        deadline: float | None,
    ) -> str | None:
        code = operation.stop_code()
        if code is not None:
            return code
        code = _nosql_stop_code(cancellation, deadline)
        if code is not None:
            operation.request_stop(code)
        return code

    def _forget_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._active.pop(future, None)

    def _ensure_lease(self) -> Any:
        if self.router is not None:
            return self.router
        if self._lease is not None:
            return self._lease
        if self.factory is None:
            return None
        self._lease = _invoke(
            self.factory,
            {
                "connection_id": self.context.connection_id,
                "profile_revision": self.context.profile_revision,
                "provider": "",
                "mode": "",
                "database_name": self.context.database,
                "database": self.context.database,
                "session_id": self.context.tab_id,
                "generation": 1,
            },
            fallback=(self.context.connection_id,),
        )
        target = getattr(self._lease, "target", None)
        if target is not None:
            if not _target_matches_context(target, self.context):
                self._close_lease()
                raise RuntimeError("NoSQL target scope mismatch")
        return self._lease

    def _profile_stale(self) -> bool:
        source = self.host
        if source is None:
            return False
        checker = getattr(source, "profile_snapshot", None) or getattr(source, "get_profile_snapshot", None)
        if not callable(checker):
            return False
        try:
            snapshot = _invoke(checker, {"connection_id": self.context.connection_id, "profile_revision": self.context.profile_revision}, fallback=(self.context.connection_id,))
            return not _target_matches_context(snapshot, self.context)
        except Exception:
            return True

    def _close_lease(self) -> None:
        lease, self._lease = self._lease, None
        if lease is not None:
            _close(lease)

    def _close_owned_router(self) -> None:
        if not self.owns_router or self.router is None or self._router_close_attempted:
            return
        self._router_close_attempted = True
        _close(self.router)

    def _cleanup_stopped_operation(self) -> None:
        self._close_lease()
        self._close_owned_router()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for operation in tuple(self._active.values()):
                operation.request_stop("CANCELLED")
            # Accepted operations are ahead of this finalizer in the serial
            # queue. Shutdown drains it and never cancels required cleanup.
            self._pool.submit(self._close_owned_resources)
        self._pool.shutdown(wait=True, cancel_futures=False)

    def _close_owned_resources(self) -> None:
        self._close_lease()
        self._close_owned_router()


class _NoSQLHandler:
    def __init__(self, tool_name: str, *, worker: _NoSQLWorker) -> None:
        self.tool_name = tool_name
        self.worker = worker

    def __call__(self, call: ToolCall | Mapping[str, Any], *, context: RunContext, cancellation: Any) -> ToolResult:
        return self.worker.execute(self.tool_name, _call_arguments(call), call_id=_call_id(call), cancellation=cancellation)


def _capability_text(adapter: Any) -> str:
    declared = getattr(adapter, "capability", None)
    if declared is None:
        declared = getattr(adapter, "effective_capability", None)
    return str(getattr(declared, "value", declared) or "").strip().lower()


def build_data_center_system_prompt(
    *,
    provider: str,
    mode: str,
    database: str,
    schemas: tuple[str, ...],
    text_only: bool,
) -> str:
    """Tell the model which connection it is bound to and how to call tools."""

    engine = " / ".join(part for part in (provider, mode) if part) or "未声明"
    scope = ", ".join(schemas) if schemas else "（无）"
    binding = f"当前回答固定在这一条连接上，不能改连别的库。\n引擎：{engine}\n数据库：{database or '（未配置）'}\n范围：{scope}\n"
    if text_only:
        return (
            binding
            + "当前模型配置为仅文本，本轮不能执行查询，也不能假装已经看到库里的数据。"
            "请直接说明需要在设置里把 Agent 能力改为「原生工具调用」，或换一个会返回工具调用的模型。"
        )
    if provider == "redis":
        operations = ", ".join(sorted(REDIS_READ_OPERATIONS))
        guide = (
            "这条连接是 Redis，没有 SQL 表。不要写 SELECT，不要调用 query_readonly。\n"
            f"读取只能调用 redis_read。operation 只能是：{operations}。\n"
            "查键名用 SCAN，pattern 用通配符；看字符串用 GET；看类型用 TYPE。\n"
            "用户如果问某张 SQL 表有多少行，说明表不在这条 Redis 连接上，"
            "需要到 SQL 控制台选中那条数据库并且完成 schema 扫描后再问。\n"
        )
    elif provider == "mongodb":
        operations = ", ".join(sorted(MONGO_READ_OPERATIONS))
        guide = (
            "这条连接是 MongoDB。用 mongo_read 读取已授权集合，"
            f"operation 只能是：{operations}。\n"
            "不要写 SQL，不要调用 query_readonly 或 redis_read。\n"
        )
    else:
        guide = (
            "这条连接是关系库。回答行数、字段或表内容之前必须调用工具，不能说自己无法访问数据库。\n"
            "先 search_objects，只传 keyword，例如 keyword=prptmain。不要传 schema、database、connection。\n"
            "kinds 可以不传；要传就用字符串数组。范围里列出的 schema 只是搜索边界，不要停下来让用户挑选。\n"
            "搜到对象后用 describe_object，再 query_readonly。SQL 必须使用元数据里的 schema.表名。\n"
        )
    return binding + guide + TEXT_PROTOCOL_INSTRUCTION


class _TextProtocolAdapter:
    """Execute explicit Qwen/text tool calls when the gateway returns them as text."""

    def __init__(self, inner: Any, allowed_names: frozenset[str]) -> None:
        self._inner = inner
        self._allowed = allowed_names

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete_turn(self, request: Any, *, cancellation: Any, on_delta: Any) -> AgentTurn:
        turn = self._inner.complete_turn(request, cancellation=cancellation, on_delta=on_delta)
        if getattr(turn, "tool_calls", None) or not str(getattr(turn, "text", "") or "").strip():
            log_agent_event(
                'dc_model_turn',
                native_calls=len(getattr(turn, 'tool_calls', ()) or ()),
                text_chars=len(str(getattr(turn, 'text', '') or '')),
            )
            return turn
        parsed = parse_qwen_tool_calls(turn.text)
        log_agent_event(
            'dc_text_protocol',
            parsed=0 if not parsed else len(parsed),
            names=','.join(item['name'] for item in (parsed or [])[:8]),
            text_chars=len(turn.text or ''),
        )
        if not parsed:
            return turn
        if len(parsed) > 8 or any(item["name"] not in self._allowed for item in parsed):
            log_agent_event('dc_text_protocol_rejected', parsed=len(parsed))
            return turn
        return AgentTurn(
            text=visible_model_text(turn.text),
            tool_calls=tuple(
                ToolCall(f"text-call-{uuid.uuid4().hex[:12]}", item["name"], dict(item.get("arguments") or {}))
                for item in parsed
            ),
            finish_reason="tool_calls",
            usage=getattr(turn, "usage", None) or {},
            model_id=str(getattr(turn, "model_id", "") or ""),
        )


class DataCenterAgentRuntime:
    """Compose one fixed model/connection/tool run without UI dependencies."""

    def __init__(
        self,
        context: RunContext,
        *,
        provider: str | None = None,
        mode: str = "",
        model: Any = None,
        model_adapter: Any = None,
        model_host: Any = None,
        model_config_id: str | None = None,
        model_loader: Callable[[str], Mapping[str, Any]] | None = None,
        model_config: Mapping[str, Any] | None = None,
        transport: Any = None,
        metadata_host: MetadataHost | None = None,
        relational_host: Any = None,
        relational_factory: Any = None,
        nosql_router: Any = None,
        nosql_lease: Any = None,
        nosql_host: Any = None,
        nosql_factory: Any = None,
        session_manager: SessionManager | None = None,
        query_executor: QueryExecutor | Any | None = None,
        query_tool: Any = None,
        projector: ScopedResultProjector | None = None,
        budget: Budget | None = None,
        budget_limits: BudgetLimits | Mapping[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
        on_event: Callable[[Any], None] | None = None,
        event_sink: Callable[[Any], None] | None = None,
        system_prompt: str = "",
        draft: bool = False,
        runner_factory: Callable[..., AgentRunner] | None = None,
    ) -> None:
        if not isinstance(context, RunContext):
            raise RuntimeConfigurationError("context must be a RunContext")
        self.context = context
        intent = str(context.intent or "query").strip().casefold()
        if intent not in {"query", "read", "explore", "metadata", "draft"}:
            raise RuntimeConfigurationError("unsupported runtime intent")
        self.draft = intent == "draft" or bool(draft)
        if provider:
            self.provider = _canonical_provider(provider, mode)
        elif self.draft:
            self.provider = ""
        else:
            self.provider = _infer_provider(relational_host, nosql_router, nosql_lease, metadata_host)
        self.mode = str(mode or "").strip().casefold()
        if self.provider and not self.mode:
            self.mode = "mysql" if self.provider == "oceanbase_mysql" else "oracle" if self.provider == "oceanbase_oracle" else ""
        self._owned_metadata = False
        self._owned_query_executor = False
        self._owned_nosql_router = False
        self._nosql_worker: _NoSQLWorker | None = None
        self._closed = False
        self.projector = projector or ScopedResultProjector()
        self.query_owner_tracker = None if self.draft else QueryOwnerTracker()

        if model_config_id is not None and str(model_config_id).strip() != str(context.model_config_id or "").strip():
            raise RuntimeConfigurationError("model configuration context mismatch")

        self.model_adapter = self._build_model(
            model=model,
            model_adapter=model_adapter,
            model_host=model_host,
            model_config_id=model_config_id,
            model_loader=model_loader,
            model_config=model_config,
            transport=transport,
        )
        adapter_config_id = getattr(self.model_adapter, "model_config_id", None)
        if adapter_config_id and str(adapter_config_id).strip() != str(context.model_config_id or "").strip():
            raise RuntimeConfigurationError("model configuration context mismatch")

        if self.draft:
            metadata_handler = _DraftHandler("metadata")
            query_handler = _DraftHandler("query")
            redis_handler = _DraftHandler("redis")
            mongo_handler = _DraftHandler("mongo")
            self.metadata_host = None
            self.session_manager = session_manager
            self.query_executor = query_executor
            self.nosql_router = None
            self.target_policy = None
        else:
            if not context.profile_revision or not context.database or (self.provider in _RELATIONAL and not context.schema_allowlist):
                raise RuntimeConfigurationError("profile_revision is required for an active run")
            self.target_policy = TargetSQLPolicy(
                provider=self.provider,
                database=context.database,
                schemas=tuple(context.schema_allowlist),
            )
            target = MetadataTarget.from_context(context, provider=self.provider, mode=self.mode)
            self.metadata_host = metadata_host or MetadataHost(
                target,
                relational_factory=relational_factory,
                nosql_factory=nosql_factory,
                relational_host=relational_host,
                nosql_host=nosql_host,
            )
            self._owned_metadata = metadata_host is None
            metadata_handler = _MetadataHandler(self.metadata_host.execute)
            self.session_manager, self.query_executor, query_handler = self._build_query(
                relational_host=relational_host,
                relational_factory=relational_factory,
                session_manager=session_manager,
                query_executor=query_executor,
                query_tool=query_tool,
                owner_tracker=self.query_owner_tracker,
            )
            self.nosql_router = self._build_router(nosql_router, nosql_lease)
            self._owned_nosql_router = (isinstance(nosql_router, Mapping) or (nosql_router is None and nosql_lease is not None))
            self._nosql_worker = _NoSQLWorker(
                context=context,
                router=self.nosql_router,
                factory=nosql_factory,
                host=nosql_host,
                owner_tracker=self.query_owner_tracker,
                owns_router=self._owned_nosql_router,
            )
            redis_handler = _NoSQLHandler("redis_read", worker=self._nosql_worker)
            mongo_handler = _NoSQLHandler("mongo_read", worker=self._nosql_worker)

        if self.draft:
            query_handler = _VerifiedQueryHandler(query_handler)
        elif not getattr(query_handler, "verified_readonly", False):
            query_handler = _VerifiedQueryHandler(query_handler, target_policy=self.target_policy)
        elif self.target_policy is not None:
            query_handler = _VerifiedQueryHandler(query_handler, target_policy=self.target_policy)
        handlers = {
            "search_objects": metadata_handler,
            "describe_object": metadata_handler,
            "query_readonly": query_handler,
            "redis_read": redis_handler,
            "mongo_read": mongo_handler,
        }
        self.registry = RuntimeToolRegistry(
            handlers,
            projector=self.projector,
            query_owner_tracker=self.query_owner_tracker,
        )
        runner_type = runner_factory or AgentRunner
        supplied_prompt = str(system_prompt or "").strip()
        capability_text = _capability_text(self.model_adapter)
        if supplied_prompt:
            effective_system_prompt = supplied_prompt
            if not self.draft and self.provider in _RELATIONAL:
                binding_hint = "Use only schema-qualified physical table names from metadata; do not rely on the connection's default schema."
                effective_system_prompt = f"{effective_system_prompt}\n{binding_hint}".strip()
        elif self.draft:
            effective_system_prompt = ""
        else:
            effective_system_prompt = build_data_center_system_prompt(
                provider=self.provider,
                mode=self.mode,
                database=str(self.context.database or ""),
                schemas=tuple(self.context.schema_allowlist),
                text_only=capability_text == "text_only",
            )
        if not self.draft and capability_text != "text_only":
            allowed_names = frozenset(
                str(item.get("function", {}).get("name") or "")
                for item in self.registry.definitions()
            )
            self.model_adapter = _TextProtocolAdapter(self.model_adapter, allowed_names)
        runner_options = {
            "system_prompt": effective_system_prompt,
            "budget": budget,
            "budget_limits": budget_limits,
            "cancellation": cancellation,
            "on_event": on_event or event_sink,
        }
        self._evidence_planner: DataCenterEvidencePlanner | None = None
        if runner_factory is None and not self.draft:
            base_planner = ContextPlanner(
                ContextLimits(
                    input_token_limit=64_000,
                    context_window_tokens=None,
                    output_reserve_tokens=4_096,
                )
            )
            self._evidence_planner = DataCenterEvidencePlanner(
                base_planner,
                self.registry,
                self.context,
            )
            runner_options["context_planner"] = self._evidence_planner
        self.runner = runner_type(self.model_adapter, self.registry, **runner_options)

    def _build_model(self, *, model: Any, model_adapter: Any, model_host: Any, model_config_id: str | None, model_loader: Any, model_config: Any, transport: Any) -> Any:
        supplied = [item for item in (model, model_adapter, model_host) if item is not None]
        if len(supplied) > 1:
            raise RuntimeConfigurationError("provide one model adapter")
        if supplied:
            return supplied[0]
        if self.draft:
            raise RuntimeConfigurationError("draft mode requires an injected model adapter")
        if not model_config_id:
            raise RuntimeConfigurationError("model_config_id or model adapter is required")
        try:
            return AgentModelHostAdapter(
                model_config_id,
                loader=model_loader,
                config=model_config,
                transport=transport,
            )
        except Exception as exc:
            raise RuntimeConfigurationError("model adapter unavailable") from exc

    def _build_query(
        self,
        *,
        relational_host: Any,
        relational_factory: Any,
        session_manager: SessionManager | None,
        query_executor: Any,
        query_tool: Any,
        owner_tracker: QueryOwnerTracker | None = None,
    ) -> tuple[Any, Any, Any]:
        manager = session_manager
        factory = relational_factory
        if factory is None and relational_host is not None:
            creator = getattr(relational_host, "create_factory", None)
            if callable(creator):
                factory = _invoke(
                    creator,
                    {
                        "connection_id": self.context.connection_id,
                        "profile_revision": self.context.profile_revision,
                        "provider": self.provider,
                        "mode": self.mode,
                        "dialect": _dialect(self.provider),
                    },
                    fallback=(self.context.connection_id,),
                )
        if manager is None and query_executor is not None:
            manager = getattr(query_executor, "session_manager", None)
        if manager is None and factory is not None:
            manager = SessionManager()
        expected_dialect = _dialect(self.provider)
        existing = manager.get_session(self.context.tab_id) if manager is not None else None
        if existing is not None:
            if str(existing.connection_id) != str(self.context.connection_id) or str(existing.dialect).casefold() != expected_dialect.casefold():
                raise RuntimeConfigurationError("query session scope mismatch")
            if factory is not None and getattr(existing, "driver_factory", factory) is not factory:
                raise RuntimeConfigurationError("query session factory mismatch")
        elif manager is not None and factory is not None:
            try:
                manager.create_session(
                    self.context.connection_id,
                    factory,
                    session_id=self.context.tab_id,
                    dialect=expected_dialect,
                )
            except ValueError as exc:
                raise RuntimeConfigurationError("query session could not be bound") from exc
        executor = query_executor
        if executor is None and manager is not None:
            executor = QueryExecutor(
                manager,
                sql_policy=TargetSQLPolicy(
                    provider=self.provider,
                    database=self.context.database,
                    schemas=tuple(self.context.schema_allowlist),
                ),
            )
            self._owned_query_executor = True
        elif executor is not None and hasattr(executor, "sql_policy"):
            try:
                executor.sql_policy = TargetSQLPolicy(
                    provider=self.provider,
                    database=self.context.database,
                    schemas=tuple(self.context.schema_allowlist),
                )
            except Exception as exc:
                raise RuntimeConfigurationError("query executor policy could not be bound") from exc
        if query_tool is not None:
            return manager, executor, query_tool
        if executor is not None and manager is not None:
            return manager, executor, AgentQueryTool(manager, executor, owner_tracker=owner_tracker)
        return manager, executor, _UnavailableQueryHandler()

    @staticmethod
    def _build_router(router: Any, lease: Any) -> Any:
        if router is not None:
            if isinstance(router, Mapping):
                return HostNoSQLRouter(router)
            return router
        if lease is not None:
            return HostNoSQLRouter(lease)
        return None

    def run(
        self,
        user_message: str,
        *,
        messages: Any = None,
        initial_messages: Any = None,
        cancellation: CancellationToken | None = None,
        budget: Budget | None = None,
        on_event: Callable[[Any], None] | None = None,
    ) -> Any:
        effective_budget = budget
        if effective_budget is None:
            new_budget = getattr(self.runner, "_new_budget", None)
            if callable(new_budget):
                effective_budget = new_budget(None)
            else:
                effective_budget = Budget()
        duration = getattr(getattr(effective_budget, "limits", None), "max_duration_s", None)
        if duration is None:
            deadline = None
        else:
            elapsed = float(getattr(effective_budget, "elapsed_s", 0.0) or 0.0)
            deadline = time.monotonic() + max(0.0, float(duration) - max(0.0, elapsed))
        base_token = cancellation or getattr(self.runner, "cancellation", None) or CancellationToken()
        runtime_token = _RuntimeCancellation(base_token, deadline)
        evidence_planner = self._evidence_planner
        evidence_epoch: EvidenceEpoch | None = None
        if evidence_planner is not None:
            try:
                evidence_epoch = self.registry.activate_evidence_run(
                    self.context,
                    provider=self.provider,
                    mode=self.mode,
                )
                evidence_planner.bind_epoch(evidence_epoch)
            except Exception:
                # Context recovery is optional. A broken evidence capability
                # leaves the ordinary Stage A planner path available.
                evidence_epoch = None
        try:
            return self.runner.run(
                self.context,
                user_message,
                messages=messages,
                initial_messages=initial_messages,
                cancellation=runtime_token,
                budget=effective_budget,
                on_event=on_event,
            )
        finally:
            if evidence_planner is not None:
                evidence_planner.clear_epoch(evidence_epoch)
            self.registry.release_run(self.context)

    execute = run

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.metadata_host is not None and self._owned_metadata:
            self.metadata_host.close()
        if self._owned_query_executor and self.query_executor is not None:
            close = getattr(self.query_executor, "shutdown", None) or getattr(self.query_executor, "close", None)
            if callable(close):
                close(wait=True) if close.__name__ == "shutdown" else close()
        if self._nosql_worker is not None:
            self._nosql_worker.close()

    shutdown = close


class _UnavailableQueryHandler:
    host_only = True
    verified_readonly = True

    def __call__(self, call: ToolCall | Mapping[str, Any], *, context: RunContext, cancellation: Any) -> ToolResult:
        return ToolResult(ok=False, code="CAPABILITY_UNAVAILABLE", data={"message": "relational query capability is not configured"}, query_id=_call_id(call), scope=context.as_dict())


def _call_id(call: ToolCall | Mapping[str, Any]) -> str | None:
    if isinstance(call, ToolCall):
        return _safe_id(call.call_id)
    if isinstance(call, Mapping):
        return _safe_id(call.get("call_id", call.get("id", "")))
    return None


def _call_arguments(call: ToolCall | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(call, ToolCall):
        return call.arguments
    if isinstance(call, Mapping):
        value = call.get("arguments", call.get("args", {}))
        return value if isinstance(value, Mapping) else {}
    return {}


def _deadline_from(cancellation: Any, arguments: Mapping[str, Any] | None = None) -> float | None:
    """Read a host-owned monotonic deadline; model arguments are ignored."""

    for name in ("deadline", "deadline_monotonic", "expires_at"):
        value = getattr(cancellation, name, None)
        if isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _deadline_expired(value: float | None) -> bool:
    return value is not None and time.monotonic() >= value


def _target_matches_context(target: Any, context: RunContext) -> bool:
    if target is None:
        return False
    connection_id = getattr(target, "connection_id", None)
    revision = getattr(target, "profile_revision", None)
    if isinstance(target, Mapping):
        connection_id = target.get("connection_id", target.get("id", connection_id))
        revision = target.get("profile_revision", target.get("revision", revision))
    if connection_id is not None and str(connection_id).strip() != str(context.connection_id).strip():
        return False
    if revision is not None and str(revision).strip() != str(context.profile_revision).strip():
        return False
    database = getattr(target, "database_name", getattr(target, "database", None))
    if isinstance(target, Mapping):
        database = target.get("database_name", target.get("database", database))
    if database and context.database and _scope_name(database) != _scope_name(context.database):
        return False
    return True


def _infer_provider(relational_host: Any, router: Any, lease: Any, metadata_host: Any = None) -> str:
    candidate = getattr(getattr(relational_host, "_bound_provider", None), "provider", None)
    for value in (
        candidate,
        getattr(relational_host, "provider", None),
        getattr(lease, "provider", None),
        getattr(metadata_host, "provider", None),
        getattr(getattr(metadata_host, "target", None), "provider", None),
    ):
        if value:
            try:
                return _canonical_provider(value, getattr(lease, "mode", ""))
            except RuntimeConfigurationError:
                pass
    if isinstance(router, Mapping) and router:
        key = next(iter(router))
        try:
            return _canonical_provider(key)
        except RuntimeConfigurationError:
            pass
    provider = getattr(router, "provider", None)
    if provider:
        try:
            return _canonical_provider(provider)
        except RuntimeConfigurationError:
            pass
    raise RuntimeConfigurationError("provider is required for the fixed runtime")


__all__ = [
    "DataCenterAgentRuntime",
    "RuntimeConfigurationError",
    "RuntimeToolRegistry",
    "ScopedResultProjector",
]
