"""Host-owned, read-only metadata tools for the data-center Agent.

Metadata is deliberately a separate execution path from ``query_readonly``.
The model can request a bounded object search or an exact opaque object
description, but it cannot provide catalog SQL, a table name outside the
bound schema, a connection, or a driver option.  Catalog statements are
selected from this module's fixed provider templates and are executed through
the already-initialized read-only lease.

The host owns one small worker so Redis cursor state and its lease stay on the
same worker thread across pages.  Relational pages close their lease after
each bounded request.  No database driver, Qt module, configuration loader,
or credential store is imported at module load time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
import inspect
import math
import re
import secrets
import threading
import time
from types import MappingProxyType
from typing import Any

from .contracts import RunContext, ToolCall, ToolResult
from .readonly_driver_adapters import canonical_provider


MAX_METADATA_PAGE = 20
MAX_METADATA_ROWS = 100
MAX_DIRECTORY_PAGE = 50
MAX_CURSOR_ENTRIES = 128
MAX_OBJECT_ENTRIES = 512
MAX_CURSOR_AGE_SECONDS = 15 * 60
MAX_KEY_CHARS = 512
MAX_COMMENT_CHARS = 512
_MISSING = object()
_RELATIONAL_PROVIDERS = frozenset(
    {"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql"}
)
_NOSQL_PROVIDERS = frozenset({"redis", "mongodb"})
_RELATIONAL_KINDS = frozenset({"table", "view"})
_ORACLE_KINDS = ("TABLE", "VIEW")
_MYSQL_KINDS = ("BASE TABLE", "VIEW")
_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "authorization",
    "private_key",
    "api_key",
    "apikey",
)
_COMMENT_KEYS = frozenset({"comment", "comments", "description", "remark", "remarks"})


class MetadataHostError(RuntimeError):
    """Stable metadata error whose text is safe to cross the tool boundary."""

    def __init__(self, code: str, message: str = "metadata operation failed") -> None:
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)


class MetadataScopeError(MetadataHostError):
    pass


class MetadataCursorError(MetadataHostError):
    pass


@dataclass(frozen=True, slots=True)
class MetadataTarget:
    """Immutable target captured when a metadata host is created."""

    connection_id: str
    profile_revision: str
    provider: str
    mode: str = ""
    database: str = ""
    schema_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        connection_id = _required_text(self.connection_id, "connection_id")
        revision = _required_text(self.profile_revision, "profile_revision")
        provider = _canonical_metadata_provider(self.provider, self.mode)
        mode = str(self.mode or "").strip().casefold()
        database = _bounded_identifier(self.database, "database", required=False)
        raw_schemas = self.schema_allowlist
        if isinstance(raw_schemas, (str, bytes, bytearray)):
            raise ValueError("schema_allowlist must be a sequence")
        schemas: list[str] = []
        for item in raw_schemas or ():
            value = _bounded_identifier(item, "schema", required=True)
            if value.casefold() not in {existing.casefold() for existing in schemas}:
                schemas.append(value)
        object.__setattr__(self, "connection_id", connection_id)
        object.__setattr__(self, "profile_revision", revision)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "database", database)
        object.__setattr__(self, "schema_allowlist", tuple(schemas))

    @classmethod
    def from_context(
        cls,
        context: RunContext,
        *,
        provider: str,
        mode: str = "",
        database: str | None = None,
        schema_allowlist: Sequence[str] | None = None,
    ) -> "MetadataTarget":
        if not isinstance(context, RunContext):
            raise TypeError("context must be a RunContext")
        return cls(
            context.connection_id,
            context.profile_revision,
            provider,
            mode,
            context.database if database is None else database,
            tuple(context.schema_allowlist if schema_allowlist is None else schema_allowlist),
        )

    @property
    def scope_key(self) -> tuple[str, str, str, str, str, tuple[str, ...]]:
        return (
            self.connection_id,
            self.profile_revision,
            self.provider,
            self.mode,
            self.database,
            self.schema_allowlist,
        )

    @property
    def scope(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "provider": self.provider,
            "mode": self.mode,
            "database": self.database,
            "schemas": list(self.schema_allowlist),
        }
        return value


@dataclass(frozen=True, slots=True)
class CatalogTemplate:
    """A trusted catalog statement pair; callers cannot replace the SQL."""

    provider: str
    dialect: str
    search_sql: str
    describe_sql: str
    parameter_style: str
    comments_available: bool = False


# Numeric binds are intentional for Oracle-compatible modes.  The existing
# OceanBase ODBC wrapper translates only :1/:2/... positional binds; named
# Oracle binds would fail at the driver boundary even though they look valid.
CATALOG_TEMPLATES: Mapping[str, CatalogTemplate] = MappingProxyType({
    "oracle": CatalogTemplate(
        "oracle",
        "oracle",
        """SELECT owner, object_name, object_type, subobject_name
FROM (
  SELECT q.*, ROWNUM rn
  FROM (
    SELECT owner, object_name, object_type, subobject_name
    FROM all_objects
    WHERE owner = :1
      AND object_name LIKE :2 ESCAPE '\\'
      AND object_type IN (:3, :4)
      AND subobject_name IS NULL
    ORDER BY object_name, object_type
  ) q
  WHERE ROWNUM <= :5
)
WHERE rn > :6""",
        """SELECT owner, table_name, column_name, data_type,
       data_length, nullable, column_id, comments
FROM (
  SELECT q.*, ROWNUM rn
  FROM (
    SELECT c.owner, c.table_name, c.column_name, c.data_type,
           c.data_length, c.nullable, c.column_id, cc.comments
    FROM all_tab_columns c
    LEFT JOIN all_col_comments cc
      ON cc.owner = c.owner
     AND cc.table_name = c.table_name
     AND cc.column_name = c.column_name
    WHERE c.owner = :1
      AND c.table_name = :2
    ORDER BY c.column_id
  ) q
  WHERE ROWNUM <= :3
)
WHERE rn > :4""",
        "numeric",
        True,
    ),
    "oceanbase_oracle": CatalogTemplate(
        "oceanbase_oracle",
        "oracle",
        """SELECT owner, object_name, object_type, subobject_name
FROM (
  SELECT q.*, ROWNUM rn
  FROM (
    SELECT owner, object_name, object_type, subobject_name
    FROM all_objects
    WHERE owner = :1
      AND object_name LIKE :2 ESCAPE '\\'
      AND object_type IN (:3, :4)
      AND subobject_name IS NULL
    ORDER BY object_name, object_type
  ) q
  WHERE ROWNUM <= :5
)
WHERE rn > :6""",
        # OceanBase Oracle-mode comment catalog column names differ between
        # documented releases.  Do not claim comment compatibility until a
        # live target version is selected; fields are returned without joins.
        """SELECT owner, table_name, column_name, data_type,
       data_length, nullable, column_id
FROM (
  SELECT q.*, ROWNUM rn
  FROM (
    SELECT owner, table_name, column_name, data_type,
           data_length, nullable, column_id
    FROM all_tab_columns
    WHERE owner = :1
      AND table_name = :2
    ORDER BY column_id
  ) q
  WHERE ROWNUM <= :3
)
WHERE rn > :4""",
        "numeric",
        False,
    ),
    "mysql": CatalogTemplate(
        "mysql",
        "mysql",
        """SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s
  AND TABLE_NAME LIKE %s ESCAPE '\\'
  AND TABLE_TYPE IN (%s, %s)
ORDER BY TABLE_NAME, TABLE_TYPE
LIMIT %s OFFSET %s""",
        """SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE,
       IS_NULLABLE, ORDINAL_POSITION, COLUMN_DEFAULT, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %s
  AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION
LIMIT %s OFFSET %s""",
        "format",
        True,
    ),
    "oceanbase_mysql": CatalogTemplate(
        "oceanbase_mysql",
        "mysql",
        """SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s
  AND TABLE_NAME LIKE %s ESCAPE '\\'
  AND TABLE_TYPE IN (%s, %s)
ORDER BY TABLE_NAME, TABLE_TYPE
LIMIT %s OFFSET %s""",
        """SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE,
       IS_NULLABLE, ORDINAL_POSITION, COLUMN_DEFAULT, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %s
  AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION
LIMIT %s OFFSET %s""",
        "format",
        True,
    ),
})


@dataclass(frozen=True, slots=True)
class DirectoryTemplate:
    """Closed metadata-only catalog statements for the user object tree."""

    provider: str
    dialect: str
    databases_sql: str = ""
    schemas_sql: str = ""
    objects_sql: str = ""


DIRECTORY_TEMPLATES: Mapping[str, DirectoryTemplate] = MappingProxyType({
    "oracle": DirectoryTemplate(
        "oracle",
        "oracle",
        schemas_sql="""SELECT owner FROM (
  SELECT q.owner, ROWNUM rn
  FROM (
    SELECT DISTINCT owner
    FROM all_objects
    WHERE object_type IN ('TABLE', 'VIEW') AND subobject_name IS NULL
    ORDER BY owner
  ) q
  WHERE ROWNUM <= :1
)
WHERE rn > :2""",
        objects_sql="""SELECT object_name, object_type FROM (
  SELECT q.object_name, q.object_type, ROWNUM rn
  FROM (
    SELECT object_name, object_type
    FROM all_objects
    WHERE owner = :1
      AND object_type IN ('TABLE', 'VIEW')
      AND subobject_name IS NULL
    ORDER BY object_name, object_type
  ) q
  WHERE ROWNUM <= :2
)
WHERE rn > :3""",
    ),
    "oceanbase_oracle": DirectoryTemplate(
        "oceanbase_oracle",
        "oracle",
        schemas_sql="""SELECT owner FROM (
  SELECT q.owner, ROWNUM rn
  FROM (
    SELECT DISTINCT owner
    FROM all_objects
    WHERE object_type IN ('TABLE', 'VIEW') AND subobject_name IS NULL
    ORDER BY owner
  ) q
  WHERE ROWNUM <= :1
)
WHERE rn > :2""",
        objects_sql="""SELECT object_name, object_type FROM (
  SELECT q.object_name, q.object_type, ROWNUM rn
  FROM (
    SELECT object_name, object_type
    FROM all_objects
    WHERE owner = :1
      AND object_type IN ('TABLE', 'VIEW')
      AND subobject_name IS NULL
    ORDER BY object_name, object_type
  ) q
  WHERE ROWNUM <= :2
)
WHERE rn > :3""",
    ),
    "mysql": DirectoryTemplate(
        "mysql",
        "mysql",
        databases_sql="""SELECT SCHEMA_NAME
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
ORDER BY SCHEMA_NAME
LIMIT %s OFFSET %s""",
        objects_sql="""SELECT TABLE_NAME, TABLE_TYPE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s
ORDER BY TABLE_NAME, TABLE_TYPE
LIMIT %s OFFSET %s""",
    ),
    "oceanbase_mysql": DirectoryTemplate(
        "oceanbase_mysql",
        "mysql",
        databases_sql="""SELECT SCHEMA_NAME
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
ORDER BY SCHEMA_NAME
LIMIT %s OFFSET %s""",
        objects_sql="""SELECT TABLE_NAME, TABLE_TYPE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s
ORDER BY TABLE_NAME, TABLE_TYPE
LIMIT %s OFFSET %s""",
    ),
})


@dataclass(frozen=True, slots=True)
class _ObjectRef:
    provider: str
    mode: str
    database: str
    schema: str
    kind: str
    name: str
    target_key: tuple[Any, ...]


@dataclass(slots=True)
class _CursorState:
    target_key: tuple[Any, ...]
    kind: str
    args: tuple[Any, ...]
    offset: int = 0
    inner_cursor: str | None = None
    lease_key: str | None = None
    created: float = 0.0


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _bounded_identifier(value: Any, name: str, *, required: bool) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{name} must be non-empty")
    if len(text) > MAX_KEY_CHARS:
        raise ValueError(f"{name} is too long")
    if "\x00" in text:
        raise ValueError(f"{name} is invalid")
    return text


def _directory_identifier(value: Any, name: str, *, required: bool) -> str:
    """Validate an issued tree path without normalizing the metadata name."""

    if not isinstance(value, str):
        raise MetadataHostError("ARGUMENT_INVALID", f"{name} must be text")
    if required and not value:
        raise MetadataHostError("ARGUMENT_INVALID", f"{name} must be non-empty")
    try:
        encoded_length = len(value.encode("utf-8", "strict"))
    except UnicodeError:
        raise MetadataHostError("ARGUMENT_INVALID", f"{name} is invalid") from None
    if encoded_length > MAX_KEY_CHARS or "\x00" in value:
        raise MetadataHostError("ARGUMENT_INVALID", f"{name} is invalid")
    return value


def _canonical_metadata_provider(value: Any, mode: Any = "") -> str:
    raw = str(value or "").strip().casefold().replace("-", "_")
    if raw in {"redis"}:
        return "redis"
    if raw in {"mongo", "mongodb", "mongo_db"}:
        return "mongodb"
    try:
        provider = canonical_provider(raw, mode)
    except Exception:
        raise ValueError("unsupported metadata provider") from None
    if provider not in _RELATIONAL_PROVIDERS:
        # Dameng is deliberately unavailable in this package because the
        # service-file timeout precedence is not bound by the current profile.
        raise ValueError("metadata provider is unavailable")
    return provider


def _cancelled(token: Any) -> bool:
    if token is None:
        return False
    method = getattr(token, "is_cancelled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return True
    return bool(getattr(token, "cancelled", False))


def _expired(deadline: float | None) -> bool:
    if deadline is None:
        return False
    try:
        return float(deadline) <= time.monotonic()
    except (TypeError, ValueError, OverflowError):
        return True


def _safe_text(value: Any, limit: int = MAX_KEY_CHARS) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char not in {"\x00", "\r", "\n"})
    return text[:limit]


def _safe_comment(value: Any) -> str | None:
    if value is None:
        return None
    text = _safe_text(value, MAX_COMMENT_CHARS)
    return text or None


def _safe_code(value: Any, *, ok: bool) -> str:
    code = str(value or "").strip().upper()
    allowed = {
        "OK",
        "ARGUMENT_INVALID",
        "CAPABILITY_UNAVAILABLE",
        "CANCELLED",
        "CONNECT_FAILED",
        "METADATA_SCOPE_UNAVAILABLE",
        "METADATA_TEMPLATE_UNAVAILABLE",
        "NEEDS_CLARIFICATION",
        "PROFILE_MISMATCH",
        "PROFILE_UNAVAILABLE",
        "QUERY_FAILED",
        "READONLY_INIT_FAILED",
        "SCOPE_DENIED",
        "STALE_CURSOR",
        "TIMEOUT",
        "TOOL_FAILED",
        "UNSUPPORTED_COMBINATION",
    }
    if code in allowed:
        return code
    return "OK" if ok else "QUERY_FAILED"


def _failure(code: str, message: str, *, scope: Mapping[str, Any] | None = None, query_id: str | None = None) -> ToolResult:
    return ToolResult(
        ok=False,
        code=_safe_code(code, ok=False),
        data={"message": str(message)},
        query_id=_safe_identifier(query_id),
        scope=dict(scope or {}),
    )


def _safe_identifier(value: Any) -> str | None:
    text = _safe_text(value)
    return text or None


def _normalize_tool_call(call: ToolCall | Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any]]:
    if isinstance(call, ToolCall):
        return str(call.call_id), str(call.name), call.arguments
    if not isinstance(call, Mapping):
        return "", "", {}
    call_id = call.get("call_id", call.get("id", ""))
    name = call.get("name", "")
    arguments = call.get("arguments", call.get("args", {}))
    return str(call_id or ""), str(name or ""), arguments if isinstance(arguments, Mapping) else {}


def _invoke_declared(callback: Any, values: Mapping[str, Any], fallback: tuple[Any, ...] = ()) -> Any:
    if not callable(callback):
        callback = getattr(callback, "connect", None)
    if not callable(callback):
        raise TypeError("metadata factory is not callable")
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(*fallback)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        explicit = {
            item.name
            for item in params
            if item.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        }
        selected = explicit or set(values)
        return callback(
            **{
                key: values[key]
                for key in selected
                if key in values and key not in {"secret", "password"}
            }
        )
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
            raise TypeError("metadata callback signature is unsupported")
    kwargs = {
        item.name: values[item.name]
        for item in params
        if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
    }
    return callback(*positional, **kwargs)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _result(value: Any, *, query_id: str | None, scope: Mapping[str, Any]) -> ToolResult:
    if isinstance(value, Mapping) and "ok" not in value and ("rows" in value or "columns" in value):
        result = ToolResult(ok=True, data=dict(value))
    else:
        result = ToolResult.from_value(value)
    return ToolResult(
        ok=bool(result.ok),
        code=_safe_code(result.code, ok=bool(result.ok)),
        data=result.data,
        evidence_id=_safe_identifier(result.evidence_id),
        query_id=_safe_identifier(result.query_id) or _safe_identifier(query_id),
        scope=dict(scope),
        truncated=bool(result.truncated),
        limits=dict(result.limits),
        elapsed_ms=result.elapsed_ms,
    )


def _rows(value: Any) -> list[Any]:
    result = ToolResult.from_value(value)
    if not result.ok:
        return []
    data = result.data
    if isinstance(data, Mapping):
        raw = data.get("rows", data.get("items", ()))
    else:
        raw = data
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        return []
    return list(raw)[: MAX_METADATA_ROWS + 1]


def _row_value(row: Any, index: int, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
        return row[index] if 0 <= index < len(row) else default
    return default


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normal_kinds(provider: str, value: Any) -> tuple[str, ...]:
    if value is None or (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) == 0
    ):
        return tuple(sorted(_RELATIONAL_KINDS)) if provider in _RELATIONAL_PROVIDERS else ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise MetadataHostError("ARGUMENT_INVALID", "kinds must be an array")
    if len(value) > 20:
        raise MetadataHostError("ARGUMENT_INVALID", "too many object kinds")
    normalized: list[str] = []
    for raw in value:
        text = str(raw or "").strip().casefold()
        if provider in _RELATIONAL_PROVIDERS:
            if text not in _RELATIONAL_KINDS:
                raise MetadataHostError("ARGUMENT_INVALID", "object kind is not supported")
        elif text not in {"key", "redis_key", "collection", "mongo_collection"}:
            raise MetadataHostError("ARGUMENT_INVALID", "object kind is not supported")
        canonical = "table" if text == "base table" else text
        if canonical not in normalized:
            normalized.append(canonical)
    return tuple(normalized)


class MetadataHost:
    """Execute the closed metadata tools on a host-owned worker."""

    def __init__(
        self,
        target: MetadataTarget | RunContext | Mapping[str, Any],
        *,
        provider: str | None = None,
        mode: str = "",
        database: str | None = None,
        schema_allowlist: Sequence[str] | None = None,
        lease_factory: Any = None,
        relational_factory: Any = None,
        nosql_factory: Any = None,
        relational_host: Any = None,
        nosql_host: Any = None,
        profile_snapshot: Callable[..., Any] | None = None,
        max_page_size: int = MAX_METADATA_PAGE,
        max_cursor_entries: int = MAX_CURSOR_ENTRIES,
        max_object_entries: int = MAX_OBJECT_ENTRIES,
    ) -> None:
        self.target = _coerce_target(
            target,
            provider=provider,
            mode=mode,
            database=database,
            schema_allowlist=schema_allowlist,
        )
        if lease_factory is not None and (relational_factory is not None or nosql_factory is not None):
            raise ValueError("choose lease_factory or provider factory")
        selected_factory = lease_factory
        if selected_factory is None:
            selected_factory = relational_factory if self.target.provider in _RELATIONAL_PROVIDERS else nosql_factory
        if selected_factory is None:
            host = relational_host if self.target.provider in _RELATIONAL_PROVIDERS else nosql_host
            if host is not None:
                creator = getattr(host, "create_factory", None)
                if callable(creator):
                    selected_factory = _invoke_declared(
                        creator,
                        {
                            "connection_id": self.target.connection_id,
                            "profile_revision": self.target.profile_revision,
                            "provider": self.target.provider,
                            "mode": self.target.mode,
                            "dialect": _dialect(self.target.provider),
                            "database_name": self.target.database,
                        },
                        fallback=(self.target.connection_id,),
                    )
                if profile_snapshot is None:
                    profile_snapshot = getattr(host, "profile_snapshot", None)
        self._factory = selected_factory
        self._profile_snapshot = profile_snapshot
        if isinstance(max_page_size, bool) or not isinstance(max_page_size, int) or not 1 <= max_page_size <= MAX_METADATA_PAGE:
            raise ValueError("max_page_size must be between 1 and 20")
        if isinstance(max_cursor_entries, bool) or not isinstance(max_cursor_entries, int) or not 1 <= max_cursor_entries <= 1024:
            raise ValueError("max_cursor_entries is invalid")
        if isinstance(max_object_entries, bool) or not isinstance(max_object_entries, int) or not 1 <= max_object_entries <= 4096:
            raise ValueError("max_object_entries is invalid")
        self.max_page_size = int(max_page_size)
        self.max_cursor_entries = int(max_cursor_entries)
        self.max_object_entries = int(max_object_entries)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-center-metadata")
        self._worker_thread_id: int | None = None
        self._closed = False
        self._state_lock = threading.RLock()
        self._close_future: Future[Any] | None = None
        self._worker_leases: dict[str, Any] = {}
        self._worker_cursors: dict[str, _CursorState] = {}
        self._worker_objects: dict[str, _ObjectRef] = {}
        self._sequence = 0

    def __repr__(self) -> str:
        return f"{type(self).__name__}(target={self.target!r}, closed={self._closed!r})"

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        future = self.close_async()
        try:
            future.result(timeout=2.0)
        except FutureTimeout:
            pass
        except Exception:
            pass

    def close_async(self) -> Future[Any]:
        """Close worker-owned metadata state and resolve only after its thread exits."""

        with self._state_lock:
            if self._close_future is not None:
                return self._close_future
            self._closed = True
            completion: Future[Any] = Future()
            self._close_future = completion
        try:
            worker_cleanup = self._pool.submit(self._close_worker)
        except RuntimeError:
            worker_cleanup = Future()
            worker_cleanup.set_result(None)

        def finish() -> None:
            try:
                worker_cleanup.result()
            except Exception:
                pass
            self._pool.shutdown(wait=True, cancel_futures=True)
            if not completion.done():
                completion.set_result(None)

        threading.Thread(target=finish, name="data-center-metadata-close", daemon=True).start()
        return completion

    def execute(
        self,
        call: ToolCall | Mapping[str, Any],
        *,
        context: RunContext | None = None,
        cancellation: Any = None,
        deadline: float | None = None,
    ) -> ToolResult:
        call_id, name, arguments = _normalize_tool_call(call)
        if name.casefold() not in {"search_objects", "describe_object"}:
            return _failure("ARGUMENT_INVALID", "metadata tool is not registered", scope=self.target.scope, query_id=call_id)
        if context is not None and not _context_matches_target(context, self.target):
            return _failure("SCOPE_DENIED", "metadata target does not match run context", scope=self.target.scope, query_id=call_id)
        if name.casefold() == "search_objects":
            return self.search_objects(arguments, cancellation=cancellation, deadline=deadline, query_id=call_id)
        return self.describe_object(arguments, cancellation=cancellation, deadline=deadline, query_id=call_id)

    def search_objects(
        self,
        arguments: Mapping[str, Any] | None = None,
        *,
        keyword: Any = None,
        kinds: Any = None,
        page_token: Any = None,
        cancellation: Any = None,
        deadline: float | None = None,
        query_id: str | None = None,
    ) -> ToolResult:
        values = dict(arguments or {}) if isinstance(arguments, Mapping) else {}
        if keyword is None and "keyword" in values:
            keyword = values.get("keyword")
        if kinds is None and "kinds" in values:
            kinds = values.get("kinds")
        if page_token is None and "page_token" in values:
            page_token = values.get("page_token")
        if set(values).difference({"keyword", "kinds", "page_token"}):
            return _failure("ARGUMENT_INVALID", "metadata search arguments are closed", scope=self.target.scope, query_id=query_id)
        try:
            text = _safe_text(keyword) if keyword is not None else ""
            if len(text) > MAX_KEY_CHARS:
                raise MetadataHostError("ARGUMENT_INVALID", "keyword is too long")
            normalized_kinds = _normal_kinds(self.target.provider, kinds)
            token = _safe_text(page_token) if page_token is not None else ""
            if len(token) > MAX_KEY_CHARS:
                raise MetadataHostError("ARGUMENT_INVALID", "page token is too long")
            if self.target.provider in _RELATIONAL_PROVIDERS and not self.target.schema_allowlist:
                return _failure("METADATA_SCOPE_UNAVAILABLE", "宿主没有提供 schema 范围", scope=self.target.scope, query_id=query_id)
            if _cancelled(cancellation):
                return _failure("CANCELLED", "metadata operation cancelled", scope=self.target.scope, query_id=query_id)
            if _expired(deadline):
                return _failure("TIMEOUT", "metadata deadline expired", scope=self.target.scope, query_id=query_id)
        except MetadataHostError as exc:
            return _failure(exc.code, exc.message, scope=self.target.scope, query_id=query_id)
        return self._submit(
            lambda: self._search_worker(
                text,
                normalized_kinds,
                token or None,
                cancellation=cancellation,
                deadline=deadline,
                query_id=query_id,
            ),
            cancellation=cancellation,
            deadline=deadline,
            query_id=query_id,
        )

    def describe_object(
        self,
        arguments: Mapping[str, Any] | None = None,
        *,
        object_id: Any = None,
        field_filter: Any = None,
        page_token: Any = None,
        cancellation: Any = None,
        deadline: float | None = None,
        query_id: str | None = None,
    ) -> ToolResult:
        values = dict(arguments or {}) if isinstance(arguments, Mapping) else {}
        if object_id is None:
            object_id = values.get("object_id")
        if field_filter is None and "field_filter" in values:
            field_filter = values.get("field_filter")
        if page_token is None and "page_token" in values:
            page_token = values.get("page_token")
        if set(values).difference({"object_id", "field_filter", "page_token"}):
            return _failure("ARGUMENT_INVALID", "metadata description arguments are closed", scope=self.target.scope, query_id=query_id)
        try:
            token = _bounded_identifier(object_id, "object_id", required=True)
            if field_filter is None:
                fields: tuple[str, ...] = ()
            elif isinstance(field_filter, (str, bytes, bytearray)) or not isinstance(field_filter, Sequence):
                raise MetadataHostError("ARGUMENT_INVALID", "field_filter must be an array")
            else:
                if len(field_filter) > 100:
                    raise MetadataHostError("ARGUMENT_INVALID", "field_filter is too large")
                fields = tuple(_bounded_identifier(item, "field", required=True) for item in field_filter)
            page = _bounded_identifier(page_token, "page_token", required=False) if page_token is not None else ""
            if _cancelled(cancellation):
                return _failure("CANCELLED", "metadata operation cancelled", scope=self.target.scope, query_id=query_id)
            if _expired(deadline):
                return _failure("TIMEOUT", "metadata deadline expired", scope=self.target.scope, query_id=query_id)
        except (ValueError, MetadataHostError) as exc:
            code = exc.code if isinstance(exc, MetadataHostError) else "ARGUMENT_INVALID"
            message = exc.message if isinstance(exc, MetadataHostError) else "metadata argument is invalid"
            return _failure(code, message, scope=self.target.scope, query_id=query_id)
        return self._submit(
            lambda: self._describe_worker(
                token,
                fields,
                page or None,
                cancellation=cancellation,
                deadline=deadline,
                query_id=query_id,
            ),
            cancellation=cancellation,
            deadline=deadline,
            query_id=query_id,
        )

    def directory_children(
        self,
        parent_kind: Any,
        *,
        database: Any = "",
        schema: Any = "",
        offset: Any = 0,
        page_size: Any = 50,
        cancellation: Any = None,
        deadline: float | None = None,
        query_id: str | None = None,
    ) -> ToolResult:
        """Browse a fixed provider catalog page for the UI tree only.

        This method is intentionally not accepted by ``execute`` and its
        template IDs are not model tool names.  ObjectTreeService validates
        the signed parent node before calling it.
        """

        provider = self.target.provider
        template = DIRECTORY_TEMPLATES.get(provider)
        if template is None:
            return _failure("CAPABILITY_UNAVAILABLE", "directory metadata is unavailable for this provider", scope=self.target.scope, query_id=query_id)
        try:
            parent = str(parent_kind or "").strip().casefold()
            if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_DIRECTORY_PAGE:
                raise MetadataHostError("ARGUMENT_INVALID", "directory page size is invalid")
            if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
                raise MetadataHostError("ARGUMENT_INVALID", "directory offset is invalid")
            if provider in {"mysql", "oceanbase_mysql"}:
                if parent == "root":
                    operation = "databases"
                    params: tuple[Any, ...] = (page_size + 1, offset)
                elif parent == "database":
                    selected_database = _directory_identifier(database, "database", required=True)
                    operation = "objects"
                    params = (selected_database, page_size + 1, offset)
                else:
                    raise MetadataHostError("SCOPE_DENIED", "directory parent kind is invalid")
            else:
                if parent == "root":
                    operation = "schemas"
                    params = (offset + page_size + 1, offset)
                elif parent == "schema":
                    selected_schema = _directory_identifier(schema, "schema", required=True)
                    operation = "objects"
                    params = (selected_schema, offset + page_size + 1, offset)
                else:
                    raise MetadataHostError("SCOPE_DENIED", "directory parent kind is invalid")
            if _cancelled(cancellation):
                raise MetadataHostError("CANCELLED", "directory operation cancelled")
            if _expired(deadline):
                raise MetadataHostError("TIMEOUT", "directory deadline expired")
        except MetadataHostError as exc:
            return _failure(exc.code, exc.message, scope=self.target.scope, query_id=query_id)
        return self._submit(
            lambda: self._directory_worker(
                operation,
                params,
                page_size,
                offset,
                cancellation=cancellation,
                deadline=deadline,
                query_id=query_id,
            ),
            cancellation=cancellation,
            deadline=deadline,
            query_id=query_id,
        )

    def _directory_worker(
        self,
        operation: str,
        parameters: tuple[Any, ...],
        page_size: int,
        offset: int,
        *,
        cancellation: Any,
        deadline: float | None,
        query_id: str | None,
    ) -> ToolResult:
        self._worker_thread_id = threading.get_ident()
        template = DIRECTORY_TEMPLATES.get(self.target.provider)
        sql = getattr(template, f"{operation}_sql", "") if template is not None else ""
        if not sql:
            return _failure("CAPABILITY_UNAVAILABLE", "directory template is unavailable", scope=self.target.scope, query_id=query_id)
        lease = None
        try:
            lease = self._open_lease(cancellation=cancellation, deadline=deadline)
            raw = _call_relational_lease(
                lease,
                sql,
                parameters,
                template_id=f"{self.target.provider}:directory_{operation}",
                row_limit=page_size + 1,
                cancellation=cancellation,
                deadline=deadline,
                query_id=query_id,
            )
            self._verify_profile_snapshot()
            result = _result(raw, query_id=query_id, scope=self.target.scope)
            if not result.ok:
                return result
            rows = _rows(result)
            visible = rows[:page_size]
            entries: list[dict[str, str]] = []
            for row in visible:
                name = _row_value(row, 0, "name", _row_value(row, 0, "SCHEMA_NAME", _row_value(row, 0, "owner", _row_value(row, 0, "object_name", _row_value(row, 0, "TABLE_NAME")))))
                if not isinstance(name, str) or not name or "\x00" in name:
                    continue
                if operation == "databases":
                    kind = "database"
                elif operation == "schemas":
                    kind = "schema"
                else:
                    raw_kind = str(_row_value(row, 1, "object_type", _row_value(row, 1, "TABLE_TYPE", "TABLE")) or "TABLE").upper()
                    kind = "view" if "VIEW" in raw_kind else "table"
                entries.append({"name": name, "kind": kind})
            has_more = len(rows) > page_size or bool(result.truncated)
            return ToolResult(
                ok=True,
                code="OK",
                data={"entries": entries, "next_offset": offset + len(visible) if has_more else None},
                query_id=_safe_identifier(query_id),
                scope=self.target.scope,
                truncated=has_more,
                limits={"page_size": page_size, "directory_only": True},
                elapsed_ms=result.elapsed_ms,
            )
        except MetadataHostError as exc:
            return _failure(exc.code, exc.message, scope=self.target.scope, query_id=query_id)
        except Exception:
            return _failure("QUERY_FAILED", "directory metadata failed", scope=self.target.scope, query_id=query_id)
        finally:
            _close_quietly(lease)

    def _submit(
        self,
        callback: Callable[[], ToolResult],
        *,
        cancellation: Any,
        deadline: float | None,
        query_id: str | None,
    ) -> ToolResult:
        with self._state_lock:
            if self._closed:
                return _failure("SESSION_NOT_FOUND", "metadata host is closed", scope=self.target.scope, query_id=query_id)
        try:
            future = self._pool.submit(callback)
        except RuntimeError:
            return _failure("SESSION_NOT_FOUND", "metadata host is closed", scope=self.target.scope, query_id=query_id)
        while True:
            if _cancelled(cancellation):
                if not future.done():
                    future.cancel()
                return _failure("CANCELLED", "metadata operation cancelled", scope=self.target.scope, query_id=query_id)
            if _expired(deadline):
                if not future.done():
                    future.cancel()
                return _failure("TIMEOUT", "metadata deadline expired", scope=self.target.scope, query_id=query_id)
            try:
                return ToolResult.from_value(future.result(timeout=0.05))
            except FutureTimeout:
                continue
            except MetadataHostError as exc:
                return _failure(exc.code, exc.message, scope=self.target.scope, query_id=query_id)
            except Exception:
                return _failure("QUERY_FAILED", "metadata operation failed", scope=self.target.scope, query_id=query_id)

    def _search_worker(
        self,
        keyword: str,
        kinds: tuple[str, ...],
        page_token: str | None,
        *,
        cancellation: Any,
        deadline: float | None,
        query_id: str | None,
    ) -> ToolResult:
        self._worker_thread_id = threading.get_ident()
        state = self._read_cursor(page_token, "search", (keyword, kinds))
        if self.target.provider in _RELATIONAL_PROVIDERS:
            return self._search_relational(state, keyword, kinds, cancellation, deadline, query_id)
        if self.target.provider == "redis":
            return self._search_redis(state, keyword, kinds, cancellation, deadline, query_id)
        return self._search_mongo(state, keyword, kinds, cancellation, deadline, query_id)

    def _describe_worker(
        self,
        object_token: str,
        fields: tuple[str, ...],
        page_token: str | None,
        *,
        cancellation: Any,
        deadline: float | None,
        query_id: str | None,
    ) -> ToolResult:
        self._worker_thread_id = threading.get_ident()
        ref = self._worker_objects.get(object_token)
        if ref is None or ref.target_key != self.target.scope_key:
            return _failure("SCOPE_DENIED", "object id is not bound to this target", scope=self.target.scope, query_id=query_id)
        state = self._read_cursor(page_token, "describe", (object_token, fields))
        if self.target.provider in _RELATIONAL_PROVIDERS:
            return self._describe_relational(ref, state, fields, cancellation, deadline, query_id)
        if self.target.provider == "redis":
            return self._describe_redis(ref, cancellation, deadline, query_id)
        return self._describe_mongo(ref, fields, cancellation, deadline, query_id)

    def _search_relational(
        self,
        state: _CursorState | None,
        keyword: str,
        kinds: tuple[str, ...],
        cancellation: Any,
        deadline: float | None,
        query_id: str | None,
    ) -> ToolResult:
        template = CATALOG_TEMPLATES.get(self.target.provider)
        if template is None:
            return _failure("METADATA_TEMPLATE_UNAVAILABLE", "catalog template is unavailable", scope=self.target.scope, query_id=query_id)
        schemas = tuple(self.target.schema_allowlist)
        schema_index = 0
        offset = state.offset if state is not None else 0
        if state is not None and len(state.args) >= 3:
            try:
                schema_index = int(state.args[2])
            except (TypeError, ValueError):
                schema_index = 0
        if schema_index < 0 or schema_index >= len(schemas):
            return _failure("STALE_CURSOR", "page cursor does not match this metadata target", scope=self.target.scope, query_id=query_id)
        like_keyword = keyword.upper() if self.target.provider in {"oracle", "oceanbase_oracle"} else keyword
        kind_values = _catalog_kinds(self.target.provider, kinds)
        objects: list[dict[str, Any]] = []
        elapsed_ms = 0
        lease = None
        try:
            from tools.agent_diag import log_agent_event
            log_agent_event(
                'metadata_search_start',
                provider=self.target.provider,
                schema_count=len(schemas),
                schema_index=schema_index,
                keyword_len=len(keyword),
                kinds=','.join(kinds),
            )
            lease = self._open_lease(cancellation=cancellation, deadline=deadline)
            has_more = False
            while schema_index < len(schemas) and len(objects) <= self.max_page_size:
                schema = schemas[schema_index]
                remaining = self.max_page_size + 1 - len(objects)
                params = _search_parameters(template, schema, like_keyword, kind_values, offset, remaining)
                raw = _call_relational_lease(
                    lease,
                    template.search_sql,
                    params,
                    template_id=f"{self.target.provider}:search",
                    row_limit=remaining,
                    cancellation=cancellation,
                    deadline=deadline,
                    query_id=query_id,
                )
                self._verify_profile_snapshot()
                result = _result(raw, query_id=query_id, scope=self.target.scope)
                elapsed_ms += int(result.elapsed_ms or 0)
                if not result.ok:
                    log_agent_event('metadata_search_end', code=result.code, schema=schema, returned=len(objects))
                    return result
                rows = _rows(result)
                room = remaining
                visible = rows[:room]
                for row in visible:
                    name = _safe_text(_row_value(row, 1, "object_name", _row_value(row, 1, "TABLE_NAME")))
                    raw_type = _safe_text(_row_value(row, 2, "object_type", _row_value(row, 2, "TABLE_TYPE"))).upper()
                    kind = "view" if raw_type in {"VIEW"} else "table"
                    if kind not in kinds:
                        continue
                    comment = _safe_comment(_row_value(row, 3, "table_comment", _row_value(row, 3, "TABLE_COMMENT")))
                    ref = _ObjectRef(self.target.provider, self.target.mode, self.target.database, schema, kind, name, self.target.scope_key)
                    object_id = self._issue_object(ref)
                    item: dict[str, Any] = {
                        "object_id": object_id,
                        "name": name,
                        "kind": kind,
                        "schema": schema,
                        "inferred": False,
                    }
                    if comment is not None:
                        item["comment"] = comment
                        item["comment_untrusted"] = True
                    objects.append(item)
                page_full = len(objects) > self.max_page_size
                schema_has_more = len(rows) >= remaining or bool(result.truncated)
                if page_full or schema_has_more:
                    has_more = True
                    next_offset = offset + len(visible)
                    next_schema_index = schema_index
                    if not schema_has_more:
                        next_schema_index += 1
                        next_offset = 0
                    next_token = None
                    if next_schema_index < len(schemas):
                        next_token = self._next_page(
                            _CursorState(
                                self.target.scope_key,
                                "search",
                                (keyword, kinds, next_schema_index),
                                next_offset,
                                created=time.monotonic(),
                            )
                        )
                    else:
                        has_more = False
                    log_agent_event('metadata_search_end', code='OK', returned=min(len(objects), self.max_page_size), has_more=bool(next_token))
                    return ToolResult(
                        ok=True,
                        code="OK",
                        data={"objects": objects[: self.max_page_size], "next_page_token": next_token, "returned": min(len(objects), self.max_page_size)},
                        evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None),
                        query_id=_safe_identifier(query_id),
                        scope=self.target.scope,
                        truncated=bool(next_token),
                        limits={"page_size": self.max_page_size, "catalog_template": self.target.provider, "offset": offset, "schema_index": schema_index},
                        elapsed_ms=elapsed_ms,
                    )
                schema_index += 1
                offset = 0
            log_agent_event('metadata_search_end', code='OK', returned=len(objects), has_more=False)
            return ToolResult(
                ok=True,
                code="OK",
                data={"objects": objects, "next_page_token": None, "returned": len(objects)},
                evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None),
                query_id=_safe_identifier(query_id),
                scope=self.target.scope,
                truncated=False,
                limits={"page_size": self.max_page_size, "catalog_template": self.target.provider, "offset": 0},
                elapsed_ms=elapsed_ms,
            )
        except MetadataHostError as exc:
            from tools.agent_diag import log_agent_event
            log_agent_event('metadata_search_end', code=exc.code)
            return _failure(exc.code, exc.message, scope=self.target.scope, query_id=query_id)
        except Exception:
            from tools.agent_diag import log_agent_event
            log_agent_event('metadata_search_end', code='QUERY_FAILED')
            return _failure("QUERY_FAILED", "metadata search failed", scope=self.target.scope, query_id=query_id)
        finally:
            _close_quietly(lease)

    def _describe_relational(
        self,
        ref: _ObjectRef,
        state: _CursorState | None,
        fields: tuple[str, ...],
        cancellation: Any,
        deadline: float | None,
        query_id: str | None,
    ) -> ToolResult:
        template = CATALOG_TEMPLATES.get(self.target.provider)
        if template is None:
            return _failure("METADATA_TEMPLATE_UNAVAILABLE", "catalog template is unavailable", scope=self.target.scope, query_id=query_id)
        offset = state.offset if state is not None else 0
        params = _describe_parameters(template, ref.schema, ref.name, offset, self.max_page_size + 1)
        lease = None
        try:
            lease = self._open_lease(cancellation=cancellation, deadline=deadline)
            raw = _call_relational_lease(
                lease,
                template.describe_sql,
                params,
                template_id=f"{self.target.provider}:describe",
                row_limit=self.max_page_size + 1,
                cancellation=cancellation,
                deadline=deadline,
                query_id=query_id,
            )
            self._verify_profile_snapshot()
            result = _result(raw, query_id=query_id, scope=self.target.scope)
            if not result.ok:
                return result
            rows = _rows(result)
            has_more = len(rows) > self.max_page_size or bool(result.truncated)
            visible = rows[: self.max_page_size]
            fields_out: list[dict[str, Any]] = []
            for row in visible:
                name = _safe_text(_row_value(row, 2, "column_name", _row_value(row, 2, "COLUMN_NAME")))
                if fields and name.casefold() not in {item.casefold() for item in fields}:
                    continue
                data_type = _safe_text(_row_value(row, 3, "data_type", _row_value(row, 3, "COLUMN_TYPE")), 128)
                item: dict[str, Any] = {
                    "name": name,
                    "type": data_type,
                    "nullable": _safe_text(_row_value(row, 5, "nullable", _row_value(row, 4, "IS_NULLABLE")), 32),
                    "ordinal": _row_value(row, 6, "column_id", _row_value(row, 5, "ORDINAL_POSITION")),
                    "inferred": False,
                }
                comment_index = 7
                if template.comments_available:
                    comment = _safe_comment(_row_value(row, comment_index, "comments", _row_value(row, 7, "COLUMN_COMMENT")))
                    if comment is not None:
                        item["comment"] = comment
                        item["comment_untrusted"] = True
                else:
                    item["comment_available"] = False
                fields_out.append(item)
            next_token = self._next_page(
                _CursorState(self.target.scope_key, "describe", (self._find_object_token(ref), fields), offset + len(visible), created=time.monotonic())
            ) if has_more else None
            return ToolResult(
                ok=True,
                code="OK",
                data={
                    "object_id": self._find_object_token(ref),
                    "name": ref.name,
                    "kind": ref.kind,
                    "schema": ref.schema,
                    "fields": fields_out,
                    "next_page_token": next_token,
                    "comments_available": bool(template.comments_available),
                },
                evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None),
                query_id=_safe_identifier(query_id),
                scope=self.target.scope,
                truncated=bool(next_token) or bool(result.truncated),
                limits={"page_size": self.max_page_size, "catalog_template": self.target.provider, "offset": offset},
                elapsed_ms=result.elapsed_ms,
            )
        except MetadataHostError as exc:
            return _failure(exc.code, exc.message, scope=self.target.scope, query_id=query_id)
        except Exception:
            return _failure("QUERY_FAILED", "metadata description failed", scope=self.target.scope, query_id=query_id)
        finally:
            _close_quietly(lease)

    def _search_redis(self, state: _CursorState | None, keyword: str, kinds: tuple[str, ...], cancellation: Any, deadline: float | None, query_id: str | None) -> ToolResult:
        lease = self._lease_for_cursor(state)
        lease_key = state.lease_key if state is not None else None
        try:
            request: dict[str, Any] = {"operation": "SCAN", "pattern": f"*{_escape_like(keyword)}*" if keyword else "*", "limit": self.max_page_size}
            if state is not None and state.inner_cursor:
                request["cursor"] = state.inner_cursor
            if lease is None:
                lease = self._open_lease(cancellation=cancellation, deadline=deadline)
            raw = _call_nosql_lease(lease, request, cancellation, deadline, query_id)
            self._verify_profile_snapshot()
            result = _result(raw, query_id=query_id, scope=self.target.scope)
            if not result.ok:
                return result
            data = result.data if isinstance(result.data, Mapping) else {}
            keys = list(data.get("keys", ())) if isinstance(data.get("keys", ()), Sequence) else []
            objects: list[dict[str, Any]] = []
            for encoded_key in keys[: self.max_page_size]:
                key = _display_value(encoded_key)
                if not isinstance(key, str):
                    key = _safe_text(key)
                value_type = "unknown"
                typed = _call_nosql_lease(lease, {"operation": "TYPE", "key": key}, cancellation, deadline, query_id)
                typed_result = _result(typed, query_id=query_id, scope=self.target.scope)
                if typed_result.ok and isinstance(typed_result.data, Mapping):
                    value_type = _safe_text(_display_value(typed_result.data.get("value", "unknown")), 64) or "unknown"
                ref = _ObjectRef("redis", self.target.mode, self.target.database, "", "redis_key", key, self.target.scope_key)
                object_id = self._issue_object(ref)
                objects.append({"object_id": object_id, "name": key, "kind": "redis_key", "value_type": value_type, "inferred": False})
            inner = data.get("next_cursor")
            if inner:
                lease_key = lease_key or self._new_state_key("lease")
                self._worker_leases[lease_key] = lease
                next_token = self._next_page(_CursorState(self.target.scope_key, "search", (keyword, kinds), inner_cursor=str(inner), lease_key=lease_key, created=time.monotonic()))
                lease = None
            else:
                next_token = None
            return ToolResult(ok=True, code="OK", data={"objects": objects, "next_page_token": next_token, "returned": len(objects)}, evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None), query_id=_safe_identifier(query_id), scope=self.target.scope, truncated=bool(next_token), limits={"page_size": self.max_page_size, "provider": "redis"}, elapsed_ms=result.elapsed_ms)
        except Exception:
            return _failure("QUERY_FAILED", "metadata search failed", scope=self.target.scope, query_id=query_id)
        finally:
            _close_quietly(lease)

    def _describe_redis(self, ref: _ObjectRef, cancellation: Any, deadline: float | None, query_id: str | None) -> ToolResult:
        lease = None
        try:
            lease = self._open_lease(cancellation=cancellation, deadline=deadline)
            type_result = _result(_call_nosql_lease(lease, {"operation": "TYPE", "key": ref.name}, cancellation, deadline, query_id), query_id=query_id, scope=self.target.scope)
            self._verify_profile_snapshot()
            if not type_result.ok:
                return type_result
            data = type_result.data if isinstance(type_result.data, Mapping) else {}
            value_type = _safe_text(_display_value(data.get("value", "unknown")), 64) or "unknown"
            return ToolResult(ok=True, code="OK", data={"object_id": self._find_object_token(ref), "name": ref.name, "kind": ref.kind, "value_type": value_type, "inferred": False}, evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None), query_id=_safe_identifier(query_id), scope=self.target.scope, limits={"provider": "redis"})
        except Exception:
            return _failure("QUERY_FAILED", "metadata description failed", scope=self.target.scope, query_id=query_id)
        finally:
            _close_quietly(lease)

    def _search_mongo(self, state: _CursorState | None, keyword: str, kinds: tuple[str, ...], cancellation: Any, deadline: float | None, query_id: str | None) -> ToolResult:
        del cancellation, deadline
        ids = self._allowed_collection_ids()
        if not ids:
            return _failure("METADATA_SCOPE_UNAVAILABLE", "Mongo collection scope is not host-authorized", scope=self.target.scope, query_id=query_id)
        offset = state.offset if state is not None else 0
        matches = [item for item in ids if not keyword or keyword.casefold() in item.casefold()]
        visible = matches[offset : offset + self.max_page_size]
        objects: list[dict[str, Any]] = []
        for name in visible:
            ref = _ObjectRef("mongodb", self.target.mode, self.target.database, "", "mongo_collection", name, self.target.scope_key)
            objects.append({"object_id": self._issue_object(ref), "name": name, "kind": "mongo_collection", "inferred": False})
        has_more = offset + len(visible) < len(matches)
        next_token = self._next_page(_CursorState(self.target.scope_key, "search", (keyword, kinds), offset + len(visible), created=time.monotonic())) if has_more else None
        return ToolResult(ok=True, code="OK", data={"objects": objects, "next_page_token": next_token, "returned": len(objects)}, evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None), query_id=_safe_identifier(query_id), scope=self.target.scope, truncated=bool(next_token), limits={"page_size": self.max_page_size, "provider": "mongodb", "scope_source": "host_allowlist"})

    def _describe_mongo(self, ref: _ObjectRef, fields: tuple[str, ...], cancellation: Any, deadline: float | None, query_id: str | None) -> ToolResult:
        lease = None
        try:
            lease = self._open_lease(cancellation=cancellation, deadline=deadline)
            raw = _call_nosql_lease(lease, {"collection_id": ref.name, "operation": "find", "filter": {}, "projection": {}, "limit": 1}, cancellation, deadline, query_id)
            self._verify_profile_snapshot()
            result = _result(raw, query_id=query_id, scope=self.target.scope)
            if not result.ok:
                return result
            data = result.data if isinstance(result.data, Mapping) else {}
            documents = data.get("documents", ()) if isinstance(data, Mapping) else ()
            document = _display_value(documents[0]) if isinstance(documents, Sequence) and documents else {}
            if not isinstance(document, Mapping):
                document = {}
            allowed = {item.casefold() for item in fields}
            field_values: list[dict[str, Any]] = []
            for name, value in list(document.items())[:100]:
                safe_name = _safe_text(name)
                if fields and safe_name.casefold() not in allowed:
                    continue
                field_values.append({"name": safe_name, "sample_type": _type_name(value), "inferred": True})
            return ToolResult(ok=True, code="OK", data={"object_id": self._find_object_token(ref), "name": ref.name, "kind": ref.kind, "fields": field_values, "sampled": bool(document), "inferred": True, "comments_available": False}, evidence_id=_safe_identifier(f"metadata:{query_id}" if query_id else None), query_id=_safe_identifier(query_id), scope=self.target.scope, truncated=False, limits={"sample_limit": 1, "provider": "mongodb", "fields_inferred": True}, elapsed_ms=result.elapsed_ms)
        except Exception:
            return _failure("QUERY_FAILED", "metadata description failed", scope=self.target.scope, query_id=query_id)
        finally:
            _close_quietly(lease)

    def _open_lease(self, *, cancellation: Any, deadline: float | None) -> Any:
        if _cancelled(cancellation):
            raise MetadataHostError("CANCELLED", "metadata operation cancelled")
        if _expired(deadline):
            raise MetadataHostError("TIMEOUT", "metadata deadline expired")
        self._verify_profile_snapshot()
        self._verify_factory_target()
        if self._factory is None:
            raise MetadataHostError("CAPABILITY_UNAVAILABLE", "metadata lease factory is not configured")
        values = {
            "connection_id": self.target.connection_id,
            "session_id": f"metadata-{self._next_sequence()}",
            "generation": 1,
            "profile_revision": self.target.profile_revision,
            "provider": self.target.provider,
            "mode": self.target.mode,
            "dialect": _dialect(self.target.provider),
            "database_name": self.target.database,
        }
        try:
            lease = _invoke_declared(self._factory, values, fallback=(self.target.connection_id,))
        except Exception:
            raise MetadataHostError("CONNECT_FAILED", "metadata lease unavailable") from None
        if lease is None:
            raise MetadataHostError("CONNECT_FAILED", "metadata lease unavailable")
        self._verify_lease_target(lease)
        return lease

    def _verify_profile_snapshot(self) -> None:
        checker = self._profile_snapshot
        if not callable(checker):
            return
        try:
            snapshot = _invoke_declared(
                checker,
                {
                    "connection_id": self.target.connection_id,
                    "id": self.target.connection_id,
                    "profile_revision": self.target.profile_revision,
                    "provider": self.target.provider,
                    "mode": self.target.mode,
                    "database_name": self.target.database,
                },
                fallback=(self.target.connection_id,),
            )
        except Exception:
            raise MetadataHostError("PROFILE_UNAVAILABLE", "metadata profile snapshot unavailable") from None
        if not _target_like_matches(snapshot, self.target):
            raise MetadataHostError("PROFILE_MISMATCH", "metadata profile revision is stale")

    def _verify_factory_target(self) -> None:
        candidate = getattr(self._factory, "target", None)
        if candidate is None:
            return
        if not _target_like_matches(candidate, self.target):
            raise MetadataHostError("SCOPE_DENIED", "metadata factory target is stale")

    def _verify_lease_target(self, lease: Any) -> None:
        candidate = getattr(lease, "target", lease)
        if not _target_like_matches(candidate, self.target):
            _close_quietly(lease)
            raise MetadataHostError("SCOPE_DENIED", "metadata lease target mismatch")

    def _schema_gate(self) -> ToolResult | None:
        if len(self.target.schema_allowlist) == 1:
            return None
        if len(self.target.schema_allowlist) > 1:
            return _failure("NEEDS_CLARIFICATION", "选择一个授权 schema 后才能读取对象结构", scope=self.target.scope, query_id=None)
        return _failure("METADATA_SCOPE_UNAVAILABLE", "宿主没有提供 schema 范围", scope=self.target.scope, query_id=None)

    def _read_cursor(self, token: str | None, kind: str, args: tuple[Any, ...]) -> _CursorState | None:
        if not token:
            return None
        state = self._worker_cursors.get(token)
        if state is None:
            raise MetadataCursorError("STALE_CURSOR", "page cursor is unknown or expired")
        if time.monotonic() - state.created > MAX_CURSOR_AGE_SECONDS:
            self._drop_cursor(token)
            raise MetadataCursorError("STALE_CURSOR", "page cursor expired")
        if state.target_key != self.target.scope_key or state.kind != kind or state.args != args:
            raise MetadataCursorError("SCOPE_DENIED", "page cursor does not match this metadata target")
        # Consume the public page token while retaining a Redis lease, when
        # present, until the worker has used it for this page.  The caller's
        # finally block owns cleanup if the operation fails.
        self._worker_cursors.pop(token, None)
        return state

    def _next_page(self, state: _CursorState) -> str:
        token = self._new_state_key("page")
        state.created = state.created or time.monotonic()
        self._worker_cursors[token] = state
        self._trim_state(self._worker_cursors, self.max_cursor_entries)
        return token

    def _drop_cursor(self, token: str) -> None:
        state = self._worker_cursors.pop(token, None)
        if state is not None and state.lease_key:
            _close_quietly(self._worker_leases.pop(state.lease_key, None))

    def _lease_for_cursor(self, state: _CursorState | None) -> Any:
        if state is None or not state.lease_key:
            return None
        lease = self._worker_leases.get(state.lease_key)
        if lease is None:
            raise MetadataCursorError("STALE_CURSOR", "cursor lease has expired")
        return lease

    def _issue_object(self, ref: _ObjectRef) -> str:
        token = self._new_state_key("object")
        self._worker_objects[token] = ref
        self._trim_state(self._worker_objects, self.max_object_entries)
        return token

    def _find_object_token(self, ref: _ObjectRef) -> str:
        for token, candidate in self._worker_objects.items():
            if candidate == ref:
                return token
        return self._issue_object(ref)

    def _new_state_key(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(18)}"

    def _next_sequence(self) -> int:
        with self._state_lock:
            self._sequence += 1
            return self._sequence

    @staticmethod
    def _trim_state(mapping: dict[str, Any], limit: int) -> None:
        while len(mapping) > limit:
            mapping.pop(next(iter(mapping)), None)

    def _allowed_collection_ids(self) -> tuple[str, ...]:
        candidate = getattr(self._factory, "target", None)
        values = getattr(candidate, "allowed_collection_ids", ()) if candidate is not None else ()
        return tuple(_safe_text(value) for value in values if _safe_text(value))

    def _close_worker(self) -> None:
        self._worker_thread_id = threading.get_ident()
        for lease in tuple(self._worker_leases.values()):
            _close_quietly(lease)
        self._worker_leases.clear()
        self._worker_cursors.clear()
        self._worker_objects.clear()


def _coerce_target(value: MetadataTarget | RunContext | Mapping[str, Any], **kwargs: Any) -> MetadataTarget:
    if isinstance(value, MetadataTarget):
        return value
    if isinstance(value, RunContext):
        provider = kwargs.get("provider")
        if not provider:
            raise ValueError("provider is required with RunContext")
        return MetadataTarget.from_context(value, provider=provider, mode=kwargs.get("mode", ""), database=kwargs.get("database"), schema_allowlist=kwargs.get("schema_allowlist"))
    if isinstance(value, Mapping):
        data = dict(value)
        provider = kwargs.get("provider", data.get("provider", data.get("engine", data.get("dialect", ""))))
        return MetadataTarget(
            data.get("connection_id", data.get("id", "")),
            data.get("profile_revision", data.get("revision", "")),
            provider,
            kwargs.get("mode", data.get("mode", "")),
            kwargs.get("database", data.get("database", data.get("database_name", ""))),
            tuple(kwargs.get("schema_allowlist") or data.get("schema_allowlist", data.get("schemas", ())) or ()),
        )
    raise TypeError("target must be MetadataTarget, RunContext, or mapping")


def _context_matches_target(context: RunContext, target: MetadataTarget) -> bool:
    return (
        context.connection_id == target.connection_id
        and context.profile_revision == target.profile_revision
        and context.database == target.database
        and tuple(context.schema_allowlist) == target.schema_allowlist
    )


def _target_like_matches(candidate: Any, target: MetadataTarget) -> bool:
    def get(name: str, default: Any = "") -> Any:
        if isinstance(candidate, Mapping):
            return candidate.get(name, default)
        return getattr(candidate, name, default)

    try:
        provider = _canonical_metadata_provider(get("provider", get("engine", "")), get("mode", ""))
    except Exception:
        return False
    connection_id = str(get("connection_id", get("id", "")) or "").strip()
    revision = str(get("profile_revision", get("revision", "")) or "").strip()
    if connection_id != target.connection_id or revision != target.profile_revision or provider != target.provider:
        return False
    mode = str(get("mode", "") or "").strip().casefold()
    if target.mode and mode != target.mode:
        return False
    candidate_database = get("database", get("database_name", ""))
    if candidate_database and target.database and str(candidate_database).strip().casefold() != target.database.casefold():
        return False
    candidate_schemas = get("schema_allowlist", get("schemas", ()))
    if candidate_schemas and target.schema_allowlist:
        try:
            candidate_set = {str(item).strip().casefold() for item in candidate_schemas}
        except Exception:
            return False
        if candidate_set != {item.casefold() for item in target.schema_allowlist}:
            return False
    return True


def _dialect(provider: str) -> str:
    return "mysql" if provider in {"mysql", "oceanbase_mysql"} else "oracle"


def _catalog_kinds(provider: str, kinds: Sequence[str]) -> tuple[str, str]:
    if provider in {"mysql", "oceanbase_mysql"}:
        values = ["BASE TABLE" if item == "table" else "VIEW" for item in kinds]
    else:
        values = ["TABLE" if item == "table" else "VIEW" for item in kinds]
    values = list(values[:2])
    while len(values) < 2:
        values.append("__NO_SUCH_OBJECT_KIND__")
    return values[0], values[1]


def _search_parameters(template: CatalogTemplate, schema: str, keyword: str, kinds: Sequence[str], offset: int, limit: int) -> tuple[Any, ...]:
    if template.parameter_style == "numeric":
        return (schema, f"%{_escape_like(keyword)}%", kinds[0], kinds[1], int(offset) + int(limit), int(offset))
    return (schema, f"%{_escape_like(keyword)}%", kinds[0], kinds[1], int(limit), int(offset))


def _describe_parameters(template: CatalogTemplate, schema: str, name: str, offset: int, limit: int) -> tuple[Any, ...]:
    if template.parameter_style == "numeric":
        return (schema, name, int(offset) + int(limit), int(offset))
    return (schema, name, int(limit), int(offset))


def _call_relational_lease(lease: Any, sql: str, params: Sequence[Any], *, template_id: str, row_limit: int, cancellation: Any, deadline: float | None, query_id: str | None) -> Any:
    metadata_method = getattr(lease, "execute_metadata", None)
    if callable(metadata_method):
        return _invoke_declared(
            metadata_method,
            {
                "template_id": template_id,
                "parameters": tuple(params),
                "row_limit": row_limit,
                "deadline": deadline,
                "cancellation": cancellation,
                "cancel_token": cancellation,
                "query_id": query_id,
            },
            fallback=(template_id, tuple(params), row_limit, deadline, cancellation),
        )
    method = None
    for name in ("execute_readonly", "execute_read_only", "query_readonly", "query_read_only"):
        candidate = getattr(lease, name, None)
        if callable(candidate):
            method = candidate
            break
    if method is None:
        raise MetadataHostError("CAPABILITY_UNAVAILABLE", "metadata lease has no read-only query boundary")
    request = {"sql": sql, "parameters": tuple(params), "row_limit": int(row_limit), "dialect": getattr(lease, "dialect", "")}
    values = {
        "query": request,
        "request": request,
        "sql": sql,
        "parameters": tuple(params),
        "params": tuple(params),
        "row_limit": row_limit,
        "limit": row_limit,
        "deadline": deadline,
        "cancellation": cancellation,
        "cancel_token": cancellation,
        "query_id": query_id,
    }
    return _invoke_declared(method, values, fallback=(request, tuple(params), row_limit, deadline, cancellation))


def _call_nosql_lease(lease: Any, request: Mapping[str, Any], cancellation: Any, deadline: float | None, query_id: str | None) -> Any:
    method = getattr(lease, "execute", None)
    if not callable(method):
        method = getattr(getattr(lease, "facade", None), "execute", None)
    if not callable(method):
        raise MetadataHostError("CAPABILITY_UNAVAILABLE", "NoSQL lease has no structured read boundary")
    return _invoke_declared(
        method,
        {"request": dict(request), "cancellation": cancellation, "deadline": deadline, "query_id": query_id},
        fallback=(dict(request),),
    )


def _display_value(value: Any) -> Any:
    """Unwrap only the typed containers emitted by the existing NoSQL codec."""

    if isinstance(value, Mapping) and "type" in value and "value" in value:
        kind = str(value.get("type", ""))
        if kind in {"string", "integer", "int64", "number", "boolean", "decimal128", "datetime", "date", "time", "object_id"}:
            return value.get("value")
        if kind == "object" and isinstance(value.get("value"), Mapping):
            return {str(key): _display_value(item) for key, item in value["value"].items()}
        if kind == "array" and isinstance(value.get("value"), Sequence):
            return [_display_value(item) for item in value["value"]]
    return value


def _type_name(value: Any) -> str:
    if isinstance(value, Mapping) and value.get("type"):
        return _safe_text(value.get("type"), 64)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "binary"
    return type(value).__name__[:64]


__all__ = [
    "CATALOG_TEMPLATES",
    "CatalogTemplate",
    "DIRECTORY_TEMPLATES",
    "DirectoryTemplate",
    "MAX_DIRECTORY_PAGE",
    "MAX_METADATA_PAGE",
    "MetadataCursorError",
    "MetadataHost",
    "MetadataHostError",
    "MetadataScopeError",
    "MetadataTarget",
]
