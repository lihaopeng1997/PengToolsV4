"""Worker-owned DB-API adapters for guarded relational reads.

The legacy database console deliberately lives outside this module.  These
adapters accept an already-created connection from a host factory, make the
connection read-only before the first model query, and expose only a bounded
read method.  They do not import a database driver or read application
configuration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import inspect
import threading
import time
from types import MappingProxyType
from typing import Any

from .sql_policy import SQLDecision, SQLPolicy


MAX_ROW_LIMIT = 100


class ReadOnlyDriverError(RuntimeError):
    """Base error whose text never contains driver or credential details."""

    code = "READONLY_DRIVER_ERROR"
    result_unknown = False

    def __init__(self, message: str | None = None, *, code: str | None = None, result_unknown: bool | None = None) -> None:
        self.code = str(code or self.code)
        if result_unknown is not None:
            self.result_unknown = bool(result_unknown)
        super().__init__(str(message or self.code))


class ReadOnlyInitializationError(ReadOnlyDriverError):
    code = "READONLY_INIT_FAILED"


class ReadOnlyQueryError(ReadOnlyDriverError):
    code = "READONLY_QUERY_FAILED"


class ReadOnlyResultUnknown(ReadOnlyQueryError):
    code = "RESULT_UNKNOWN"
    result_unknown = True


class ReadOnlyCancelled(ReadOnlyResultUnknown):
    code = "CANCELLED"


class ReadOnlyTimeout(ReadOnlyResultUnknown):
    code = "TIMEOUT"


class ReadOnlyThreadOwnershipError(ReadOnlyDriverError):
    code = "THREAD_OWNERSHIP_ERROR"


class ReadOnlyClosedError(ReadOnlyDriverError):
    code = "READONLY_CLOSED"


class UnsupportedReadOnlyProvider(ReadOnlyDriverError):
    code = "UNSUPPORTED_PROVIDER"


@dataclass(frozen=True, slots=True)
class CancellationStatus:
    """The honest result of a cancellation request.

    ``result_unknown`` is true whenever a statement might already have been
    sent to the server.  An unsupported or unverified cancellation mechanism
    is never reported as successful.
    """

    requested: bool
    supported: bool
    result_unknown: bool
    code: str
    method: str | None = None


@dataclass(frozen=True, slots=True)
class ReadOnlyDriverCapabilities:
    provider: str
    dialect: str
    read_only: bool = True
    supports_transactions: bool = True
    supports_cancel: bool = False
    cancel_method: str | None = None
    supports_timeout: bool = False
    timeout_method: str | None = None


@dataclass(frozen=True, slots=True)
class ReadOnlyDriverStrategy:
    """Provider policy; it contains no target address or credential."""

    provider: str
    dialect: str
    init_statements: tuple[str, ...]
    autocommit_attributes: tuple[str, ...]
    connector_options: Mapping[str, Any]
    cancel_methods: tuple[str, ...] = ()
    timeout_attributes: tuple[str, ...] = ()


def canonical_provider(provider: Any, mode: Any = None) -> str:
    """Map connection-provider spellings to the five guarded modes."""

    raw = str(provider or "").strip().casefold().replace("-", "_")
    selected_mode = str(mode or "").strip().casefold()
    if raw in {"oceanbase", "ob"}:
        return "oceanbase_mysql" if selected_mode == "mysql" else "oceanbase_oracle"
    aliases = {
        "oracle": "oracle",
        "mysql": "mysql",
        "oceanbase_oracle": "oceanbase_oracle",
        "ob_oracle": "oceanbase_oracle",
        "oceanbase_mysql": "oceanbase_mysql",
        "ob_mysql": "oceanbase_mysql",
        "dameng": "dameng",
        "dm": "dameng",
        "dm8": "dameng",
    }
    try:
        return aliases[raw]
    except KeyError as exc:
        raise UnsupportedReadOnlyProvider(code="UNSUPPORTED_PROVIDER") from None


def _options(**values: Any) -> Mapping[str, Any]:
    return MappingProxyType(dict(values))


READONLY_DRIVER_MATRIX: Mapping[str, ReadOnlyDriverStrategy] = MappingProxyType(
    {
        "oracle": ReadOnlyDriverStrategy(
            provider="oracle",
            dialect="oracle",
            init_statements=("SET TRANSACTION READ ONLY",),
            autocommit_attributes=("autocommit",),
            connector_options=_options(read_only=True, autocommit=False),
            timeout_attributes=("call_timeout",),
        ),
        "mysql": ReadOnlyDriverStrategy(
            provider="mysql",
            dialect="mysql",
            init_statements=("START TRANSACTION READ ONLY",),
            autocommit_attributes=("autocommit",),
            connector_options=_options(read_only=True, autocommit=False),
            timeout_attributes=("read_timeout",),
        ),
        "oceanbase_oracle": ReadOnlyDriverStrategy(
            provider="oceanbase_oracle",
            dialect="oracle",
            init_statements=("SET TRANSACTION READ ONLY",),
            autocommit_attributes=("autocommit",),
            connector_options=_options(read_only=True, autocommit=False),
            # ODBC SQLCancel is not declared until a host has separately
            # verified its driver.  The default adapter therefore fails
            # closed instead of guessing from a method name.
            timeout_attributes=("timeout",),
        ),
        "oceanbase_mysql": ReadOnlyDriverStrategy(
            provider="oceanbase_mysql",
            dialect="mysql",
            init_statements=("START TRANSACTION READ ONLY",),
            autocommit_attributes=("autocommit",),
            connector_options=_options(read_only=True, autocommit=False),
            timeout_attributes=("read_timeout",),
        ),
        "dameng": ReadOnlyDriverStrategy(
            provider="dameng",
            dialect="oracle",
            init_statements=("SET TRANSACTION READ ONLY",),
            autocommit_attributes=("autoCommit", "autocommit"),
            connector_options=_options(read_only=True, access_mode="read_only", autoCommit=False),
        ),
    }
)

# Friendly aliases used by host code and tests.
READ_ONLY_DRIVER_MATRIX = READONLY_DRIVER_MATRIX
PROVIDER_STRATEGIES = READONLY_DRIVER_MATRIX


def _policy_validator(policy: Any) -> Any:
    for name in ("validate", "decide", "check"):
        method = getattr(policy, name, None)
        if callable(method):
            return method
    if callable(policy):
        return policy
    raise TypeError("SQL policy has no validator")


def _invoke_sql_policy(validator: Any, sql: str, dialect: str) -> Any:
    """Call a host validator once using its declared signature."""

    try:
        signature = inspect.signature(validator)
    except (TypeError, ValueError):
        return validator(sql, dialect)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        return validator(sql, dialect=dialect)
    positional = [
        item for item in params
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return validator(sql, dialect)
    if any(item.kind == inspect.Parameter.KEYWORD_ONLY and item.name == "dialect" for item in params):
        return validator(sql, dialect=dialect)
    return validator(sql)


def _canonical_sql(sql: Any) -> str:
    return " ".join(str(sql or "").strip().split()).casefold()


def _canonical_dialect(dialect: Any) -> str:
    value = str(dialect or "").strip().casefold().replace("_", " ").replace("-", " ")
    value = " ".join(value.split())
    return {
        "oceanbase": "oracle",
        "ob": "oracle",
        "oceanbase oracle": "oracle",
        "ob oracle": "oracle",
        "oceanbase mysql": "mysql",
        "ob mysql": "mysql",
        "dameng": "oracle",
        "dm": "oracle",
        "dm8": "oracle",
    }.get(value, value)


def _is_cancelled(token: Any) -> bool:
    if token is None:
        return False
    checker = getattr(token, "is_cancelled", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return True
    checker = getattr(token, "is_set", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return True
    return bool(getattr(token, "cancelled", False))


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    try:
        return max(0.0, float(deadline) - time.monotonic())
    except (TypeError, ValueError):
        raise ReadOnlyQueryError(code="INVALID_DEADLINE") from None


def _safe_call(method: Any, *args: Any) -> Any:
    """Call a driver method without ever returning its exception text."""

    try:
        return method(*args)
    except ReadOnlyDriverError:
        raise
    except Exception:
        raise ReadOnlyQueryError(code="READONLY_DRIVER_CALL_FAILED") from None


class DBAPIReadOnlyAdapter:
    """One guarded adapter shared by Oracle-compatible and MySQL-compatible modes."""

    def __init__(
        self,
        provider: str,
        connection: Any,
        *,
        max_row_limit: int = MAX_ROW_LIMIT,
        sql_policy: SQLPolicy | Any | None = None,
    ) -> None:
        canonical = canonical_provider(provider)
        try:
            strategy = READONLY_DRIVER_MATRIX[canonical]
        except KeyError:
            raise UnsupportedReadOnlyProvider(code="UNSUPPORTED_PROVIDER") from None
        if connection is None:
            raise ReadOnlyInitializationError(code="CONNECTION_MISSING")
        if isinstance(max_row_limit, bool) or not isinstance(max_row_limit, int) or not 1 <= max_row_limit <= MAX_ROW_LIMIT:
            raise ValueError("max_row_limit must be between 1 and 100")
        self.provider = canonical
        self.dialect = strategy.dialect
        self.strategy = strategy
        self._connection = connection
        self._max_row_limit = int(max_row_limit)
        # The adapter is a second, driver-adjacent enforcement boundary.  It
        # must validate direct calls itself; a lexical prefix check cannot
        # distinguish executable comments, nested writes, or unknown UDFs.
        self._sql_policy = sql_policy if sql_policy is not None else SQLPolicy()
        self._owner_thread: int | None = None
        self._state = "new"
        self._cancel_method_name: str | None = None
        self._timeout_method_name: str | None = None

    def __repr__(self) -> str:
        return f"DBAPIReadOnlyAdapter(provider={self.provider!r}, state={self._state!r})"

    @property
    def state(self) -> str:
        return self._state

    @property
    def initialized(self) -> bool:
        return self._state == "ready"

    @property
    def closed(self) -> bool:
        return self._state in {"closed", "failed"}

    @property
    def owner_thread(self) -> int | None:
        return self._owner_thread

    @property
    def supports_cancel(self) -> bool:
        # QueryExecutor currently cancels only its worker token.  Calling a
        # DB-API ``cancel`` method from another thread has no owner-safe
        # channel, so exposing a method-shaped capability would overpromise.
        return False

    @property
    def capabilities(self) -> ReadOnlyDriverCapabilities:
        return ReadOnlyDriverCapabilities(
            provider=self.provider,
            dialect=self.dialect,
            supports_cancel=False,
            cancel_method=None,
            supports_timeout=self._timeout_method_name is not None,
            timeout_method=self._timeout_method_name,
        )

    def initialize(self) -> "DBAPIReadOnlyAdapter":
        """Claim the worker and establish the provider read-only transaction."""

        if self._state == "ready":
            self._assert_owner()
            return self
        if self._state in {"failed", "closed"}:
            raise ReadOnlyInitializationError(code="READONLY_INIT_FAILED")
        self._claim_owner()
        try:
            self._set_autocommit_false()
            for statement in self.strategy.init_statements:
                self._execute_control(statement)
            self._resolve_capabilities()
            self._state = "ready"
            return self
        except ReadOnlyDriverError:
            self._abort_initialization()
            raise ReadOnlyInitializationError(code="READONLY_INIT_FAILED") from None
        except Exception:
            self._abort_initialization()
            raise ReadOnlyInitializationError(code="READONLY_INIT_FAILED") from None

    def execute_readonly(
        self,
        query: Any = None,
        parameters: Any = None,
        row_limit: int = MAX_ROW_LIMIT,
        deadline: float | None = None,
        cancellation: Any = None,
        cancel_token: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Execute one SELECT/WITH and return bounded rows.

        The method intentionally has no generic ``execute`` alias.  It is
        shaped so :class:`QueryExecutor` can pass its ``ReadOnlyQuery`` and
        cancellation token directly from the worker thread.
        """

        self._assert_ready()
        sql, bound_parameters, limit, query_deadline = self._query_values(
            query,
            parameters,
            row_limit,
            deadline,
        )
        token = cancellation if cancellation is not None else cancel_token
        self._validate_sql(sql)
        if _is_cancelled(token):
            raise ReadOnlyCancelled(code="CANCELLED", result_unknown=False)
        remaining = _remaining(query_deadline)
        if remaining is not None and remaining <= 0:
            raise ReadOnlyTimeout(code="TIMEOUT", result_unknown=False)
        self._apply_timeout(remaining)

        cursor = None
        sent_to_server = False
        try:
            cursor_factory = getattr(self._connection, "cursor", None)
            if not callable(cursor_factory):
                raise ReadOnlyQueryError(code="CURSOR_UNAVAILABLE")
            cursor = _safe_call(cursor_factory)
            execute = getattr(cursor, "execute", None)
            if not callable(execute):
                raise ReadOnlyQueryError(code="CURSOR_EXECUTE_UNAVAILABLE")
            if bound_parameters:
                _safe_call(execute, sql, bound_parameters)
            else:
                _safe_call(execute, sql)
            sent_to_server = True
            if _is_cancelled(token):
                raise ReadOnlyCancelled(code="CANCELLED")
            rows, truncated = self._fetch_rows(cursor, limit, token, query_deadline)
            if _is_cancelled(token):
                raise ReadOnlyCancelled(code="CANCELLED")
            if query_deadline is not None and _remaining(query_deadline) == 0:
                raise ReadOnlyTimeout(code="TIMEOUT")
            return {
                "columns": tuple(getattr(cursor, "description", ()) or ()),
                "rows": tuple(rows),
                "truncated": bool(truncated),
            }
        except ReadOnlyDriverError:
            raise
        except TimeoutError:
            raise ReadOnlyTimeout(code="TIMEOUT", result_unknown=sent_to_server) from None
        except Exception:
            if sent_to_server:
                raise ReadOnlyQueryError(code="READONLY_QUERY_FAILED") from None
            raise ReadOnlyQueryError(code="READONLY_QUERY_FAILED") from None
        finally:
            self._close_cursor(cursor)

    # QueryExecutor recognizes this exact public entry point.  These aliases
    # remain read-only and do not expose a generic cursor.
    execute_read_only = execute_readonly
    query_readonly = execute_readonly
    query_read_only = execute_readonly

    def cancel(self) -> CancellationStatus:
        """Report cooperative cancellation without touching a DB-API driver.

        The caller may be the UI/host thread while the query owns the
        connection in a worker.  Until QueryExecutor supplies an owner-safe
        cancellation channel, invoking a driver method here would race the
        owner and could affect the wrong statement.  Keep the status honest
        and leave token cancellation to QueryExecutor.
        """

        if self._owner_thread is not None and self._owner_thread != threading.get_ident():
            return CancellationStatus(False, False, True, "CANCEL_UNSUPPORTED")
        self._assert_ready()
        return CancellationStatus(False, False, True, "CANCEL_UNSUPPORTED")

    request_cancel = cancel

    def close(self) -> None:
        """Rollback and close on the owning worker; never commit."""

        if self._state in {"closed", "failed"}:
            return
        self._assert_owner()
        self._state = "closed"
        self._rollback_and_close()

    disconnect = close

    def _validate_sql(self, sql: str) -> None:
        """Require a structured allow decision before touching the cursor.

        ``QueryExecutor`` supplies an already-approved request, but this
        adapter is also directly callable by a host.  Reusing the same
        ``SQLPolicy`` here closes that second boundary and prevents a caller
        from bypassing AST checks with a string that merely starts with
        ``SELECT``.
        """

        try:
            validator = _policy_validator(self._sql_policy)
            decision = _invoke_sql_policy(validator, sql, self.dialect)
        except Exception:
            raise ReadOnlyQueryError(code="READ_ONLY_SQL_REQUIRED") from None
        if not isinstance(decision, SQLDecision) or type(decision.allowed) is not bool:
            raise ReadOnlyQueryError(code="READ_ONLY_SQL_REQUIRED")
        if decision.allowed is not True:
            raise ReadOnlyQueryError(code="READ_ONLY_SQL_REQUIRED")
        if _canonical_dialect(decision.dialect) != _canonical_dialect(self.dialect):
            raise ReadOnlyQueryError(code="READ_ONLY_SQL_REQUIRED")
        if _canonical_sql(decision.sql) != _canonical_sql(sql):
            raise ReadOnlyQueryError(code="READ_ONLY_SQL_REQUIRED")

    def _query_values(
        self,
        query: Any,
        parameters: Any,
        row_limit: Any,
        deadline: Any,
    ) -> tuple[str, Any, int, float | None]:
        request_dialect: Any = None
        if isinstance(query, str):
            sql = query
        elif isinstance(query, Mapping):
            sql = query.get("sql", "")
            request_dialect = query.get("dialect")
            if parameters is None:
                parameters = query.get("parameters", {})
            if row_limit == MAX_ROW_LIMIT and query.get("row_limit") is not None:
                row_limit = query.get("row_limit")
            if deadline is None:
                deadline = query.get("deadline")
        else:
            sql = getattr(query, "sql", "")
            request_dialect = getattr(query, "dialect", None)
            if parameters is None:
                parameters = getattr(query, "parameters", {})
            if row_limit == MAX_ROW_LIMIT and getattr(query, "row_limit", None) is not None:
                row_limit = getattr(query, "row_limit")
            if deadline is None:
                deadline = getattr(query, "deadline", None)
        if not isinstance(sql, str) or not sql.strip():
            raise ReadOnlyQueryError(code="SQL_REQUIRED")
        if request_dialect not in (None, "") and _canonical_dialect(request_dialect) != _canonical_dialect(self.dialect):
            raise ReadOnlyQueryError(code="READ_ONLY_SQL_REQUIRED")
        if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= self._max_row_limit:
            raise ReadOnlyQueryError(code="ROW_LIMIT_INVALID")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping) and not isinstance(parameters, Sequence):
            raise ReadOnlyQueryError(code="PARAMETERS_INVALID")
        if deadline is not None:
            try:
                deadline = float(deadline)
            except (TypeError, ValueError):
                raise ReadOnlyQueryError(code="INVALID_DEADLINE") from None
        return sql.strip(), parameters, int(row_limit), deadline

    def _claim_owner(self) -> None:
        current = threading.get_ident()
        if self._owner_thread is None:
            self._owner_thread = current
            return
        if self._owner_thread != current:
            raise ReadOnlyThreadOwnershipError(code="THREAD_OWNERSHIP_ERROR")

    def _assert_owner(self) -> None:
        self._claim_owner()

    def _assert_ready(self) -> None:
        self._assert_owner()
        if self._state == "new":
            raise ReadOnlyInitializationError(code="READONLY_INIT_REQUIRED")
        if self._state in {"failed", "closed"}:
            raise ReadOnlyClosedError(code="READONLY_CLOSED")
        if self._state != "ready":
            raise ReadOnlyInitializationError(code="READONLY_INIT_FAILED")

    def _set_autocommit_false(self) -> None:
        setter = getattr(self._connection, "set_autocommit", None)
        if callable(setter):
            _safe_call(setter, False)
            return
        found = False
        for name in self.strategy.autocommit_attributes:
            try:
                value = getattr(self._connection, name)
            except AttributeError:
                continue
            found = True
            if callable(value):
                _safe_call(value, False)
            else:
                try:
                    setattr(self._connection, name, False)
                except Exception:
                    raise ReadOnlyInitializationError(code="AUTOCOMMIT_CONTROL_UNAVAILABLE") from None
            try:
                current = getattr(self._connection, name)
                if isinstance(current, bool) and current:
                    raise ReadOnlyInitializationError(code="AUTOCOMMIT_CONTROL_FAILED")
            except AttributeError:
                pass
            return
        name = self.strategy.autocommit_attributes[0]
        try:
            setattr(self._connection, name, False)
        except Exception:
            raise ReadOnlyInitializationError(code="AUTOCOMMIT_CONTROL_UNAVAILABLE") from None

    def _execute_control(self, statement: str) -> None:
        cursor = None
        try:
            cursor_factory = getattr(self._connection, "cursor", None)
            if not callable(cursor_factory):
                raise ReadOnlyInitializationError(code="CURSOR_UNAVAILABLE")
            cursor = _safe_call(cursor_factory)
            execute = getattr(cursor, "execute", None)
            if not callable(execute):
                raise ReadOnlyInitializationError(code="CURSOR_EXECUTE_UNAVAILABLE")
            _safe_call(execute, statement)
        finally:
            self._close_cursor(cursor)

    def _resolve_capabilities(self) -> None:
        # No driver cancellation method is trusted until an owner-safe
        # QueryExecutor channel exists.  Keep this explicitly empty even for
        # providers whose DB-API object happens to expose ``cancel``.
        self._cancel_method_name = None
        for name in self.strategy.timeout_attributes:
            try:
                getattr(self._connection, name)
            except AttributeError:
                continue
            self._timeout_method_name = name
            break

    def _apply_timeout(self, remaining: float | None) -> None:
        if remaining is None:
            return
        # The outer QueryExecutor still owns the deadline.  These assignments
        # are provider hints only; unsupported attributes leave cancellation
        # and result handling fail-closed.
        if self._timeout_method_name is None:
            return
        try:
            if self.provider == "oracle":
                setattr(self._connection, self._timeout_method_name, max(1, int(remaining * 1000)))
            elif self._timeout_method_name in {"read_timeout", "timeout"}:
                setattr(self._connection, self._timeout_method_name, max(0.001, float(remaining)))
        except Exception:
            self._timeout_method_name = None

    def _fetch_rows(
        self,
        cursor: Any,
        limit: int,
        token: Any,
        deadline: float | None,
    ) -> tuple[list[Any], bool]:
        fetchmany = getattr(cursor, "fetchmany", None)
        if callable(fetchmany):
            try:
                rows = list(fetchmany(limit + 1) or ())
            except TypeError:
                rows = list(_safe_call(getattr(cursor, "fetchall")) or ()) if callable(getattr(cursor, "fetchall", None)) else []
            rows = rows[: limit + 1]
        elif callable(getattr(cursor, "fetchall", None)):
            rows = list(_safe_call(cursor.fetchall) or ())[: limit + 1]
        else:
            try:
                iterator = iter(cursor)
            except TypeError:
                raise ReadOnlyQueryError(code="CURSOR_FETCH_UNAVAILABLE") from None
            rows = []
            for row in iterator:
                rows.append(row)
                if len(rows) >= limit + 1:
                    break
                if _is_cancelled(token):
                    raise ReadOnlyCancelled(code="CANCELLED")
                if deadline is not None and _remaining(deadline) == 0:
                    raise ReadOnlyTimeout(code="TIMEOUT")
        return rows[:limit], len(rows) > limit

    def _close_cursor(self, cursor: Any) -> None:
        if cursor is None:
            return
        closer = getattr(cursor, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def _rollback_and_close(self) -> None:
        rollback = getattr(self._connection, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
        closer = getattr(self._connection, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def _abort_initialization(self) -> None:
        self._state = "failed"
        self._rollback_and_close()


# Provider-neutral names make the intended single adapter obvious to hosts.
ReadOnlyDriverAdapter = DBAPIReadOnlyAdapter
ReadonlyDriverAdapter = DBAPIReadOnlyAdapter
RelationalReadOnlyAdapter = DBAPIReadOnlyAdapter


def create_readonly_driver_adapter(provider: str, connection: Any, **kwargs: Any) -> DBAPIReadOnlyAdapter:
    adapter = DBAPIReadOnlyAdapter(provider, connection, **kwargs)
    return adapter.initialize()


def get_readonly_driver_adapter(provider: str, connection: Any, **kwargs: Any) -> DBAPIReadOnlyAdapter:
    return create_readonly_driver_adapter(provider, connection, **kwargs)


create_readonly_adapter = create_readonly_driver_adapter
get_readonly_adapter = get_readonly_driver_adapter
adapter_for_provider = get_readonly_driver_adapter


__all__ = [
    "CancellationStatus",
    "DBAPIReadOnlyAdapter",
    "MAX_ROW_LIMIT",
    "PROVIDER_STRATEGIES",
    "READONLY_DRIVER_MATRIX",
    "READ_ONLY_DRIVER_MATRIX",
    "ReadOnlyCancelled",
    "ReadOnlyClosedError",
    "ReadOnlyDriverAdapter",
    "ReadOnlyDriverCapabilities",
    "ReadOnlyDriverError",
    "ReadOnlyDriverStrategy",
    "ReadOnlyInitializationError",
    "ReadOnlyQueryError",
    "ReadOnlyResultUnknown",
    "ReadOnlyThreadOwnershipError",
    "ReadOnlyTimeout",
    "RelationalReadOnlyAdapter",
    "ReadonlyDriverAdapter",
    "UnsupportedReadOnlyProvider",
    "adapter_for_provider",
    "canonical_provider",
    "create_readonly_adapter",
    "create_readonly_driver_adapter",
    "get_readonly_adapter",
    "get_readonly_driver_adapter",
]
