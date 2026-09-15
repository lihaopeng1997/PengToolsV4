"""Worker-backed read-only query execution for the data-center Agent."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import re
import threading
import time
from typing import Any

from .contracts import CancellationToken, QueryRequest, QueryResult, ToolResult
from .session_manager import Session, SessionManager
from .sql_policy import SQLDecision, SQLPolicy


DEFAULT_ROW_LIMIT = 100
DEFAULT_QUERY_TIMEOUT = 15.0
_READ_ONLY_METHODS = (
    "execute_readonly",
    "execute_read_only",
    "query_readonly",
    "query_read_only",
    "read_only_query",
    "readonly_query",
)
_GENERIC_METHODS = ("select", "query", "execute")
_CLOSE_METHODS = ("close", "disconnect")
_DRIVER_NAMES = frozenset({"driver", "connection", "conn", "session", "client"})
_REQUEST_NAMES = frozenset({"request", "query", "payload", "read_query"})
_PARAMETER_NAMES = frozenset({"parameters", "params", "binds", "bindings", "args", "arguments"})
_CANCEL_NAMES = frozenset({"cancellation", "cancel_token", "cancel", "token", "stop_event"})
_SQL_WHITESPACE = re.compile(r"\s+")


class _ReadGuardNotReady(LookupError):
    """The driver has no explicit read-only execution boundary."""


class _DriverCapabilityUnavailable(LookupError):
    """The injected driver has no supported query entry point."""


class ReadOnlyQuery(dict[str, Any]):
    """Bounded request mapping passed to an injected hook or fake driver."""

    def __init__(
        self,
        *,
        sql: str,
        parameters: Mapping[str, Any],
        row_limit: int,
        dialect: str,
        query_id: str,
        deadline: float | None,
        session_id: str,
        connection_id: str,
        generation: int,
    ) -> None:
        super().__init__(
            sql=sql,
            parameters=dict(parameters),
            row_limit=row_limit,
            dialect=dialect,
        )
        self.query_id = query_id
        self.deadline = deadline
        self.session_id = session_id
        self.connection_id = connection_id
        self.generation = generation

    @property
    def sql(self) -> str:
        return str(self["sql"])

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self["parameters"]

    @property
    def row_limit(self) -> int:
        return int(self["row_limit"])

    @property
    def dialect(self) -> str:
        return str(self["dialect"])


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """Normalized immutable request captured before a worker is queued."""

    session_id: str
    query_id: str
    generation: int
    sql: str
    parameters: Mapping[str, Any]
    row_limit: int
    deadline: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", str(self.session_id or "").strip())
        object.__setattr__(self, "query_id", str(self.query_id or "").strip())
        object.__setattr__(self, "sql", str(self.sql or "").strip())
        object.__setattr__(self, "parameters", dict(self.parameters or {}))


@dataclass(slots=True)
class _Task:
    spec: QuerySpec
    token: Any
    callback: Callable[[QueryResult], None] | None
    decision: SQLDecision | None
    future: Future[QueryResult] | None = None
    timed_out: bool = False
    timer: threading.Timer | None = None


class QueryExecutor:
    """Run approved SQL in a worker-owned, read-only driver connection.

    ``submit`` returns a normal ``Future``.  ``execute`` waits for that future.
    A callback is called only while the original session generation remains
    current; closing or replacing a session suppresses its late result.
    """

    def __init__(
        self,
        session_manager: SessionManager | Any | None = None,
        sql_policy: SQLPolicy | Any | None = None,
        read_only_hook: Callable[..., Any] | None = None,
        *,
        driver_factory: Any = None,
        connect: Any = None,
        readonly_hook: Callable[..., Any] | None = None,
        default_row_limit: int = DEFAULT_ROW_LIMIT,
        max_row_limit: int = DEFAULT_ROW_LIMIT,
        default_timeout: float | None = DEFAULT_QUERY_TIMEOUT,
        timeout: float | None = None,
        max_workers: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if session_manager is not None and not isinstance(session_manager, SessionManager):
            if driver_factory is None and connect is None and _looks_like_factory(session_manager):
                driver_factory = session_manager
                session_manager = None
        if driver_factory is not None and connect is not None:
            raise TypeError("provide driver_factory or connect, not both")
        if session_manager is None:
            session_manager = SessionManager(driver_factory if driver_factory is not None else connect)
        if not isinstance(session_manager, SessionManager):
            raise TypeError("session_manager must be a SessionManager")
        if read_only_hook is not None and readonly_hook is not None:
            raise TypeError("provide read_only_hook or readonly_hook, not both")
        if isinstance(default_row_limit, bool) or not isinstance(default_row_limit, int) or default_row_limit < 1:
            raise ValueError("default_row_limit must be a positive integer")
        if isinstance(max_row_limit, bool) or not isinstance(max_row_limit, int) or max_row_limit < 1:
            raise ValueError("max_row_limit must be a positive integer")
        if default_row_limit > max_row_limit:
            default_row_limit = max_row_limit
        effective_timeout = default_timeout if timeout is None else timeout
        if effective_timeout is not None and float(effective_timeout) < 0:
            raise ValueError("default_timeout must be non-negative or None")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")

        self.session_manager = session_manager
        self.sql_policy = sql_policy if sql_policy is not None else SQLPolicy()
        self.read_only_hook = read_only_hook if read_only_hook is not None else readonly_hook
        self.default_row_limit = int(default_row_limit)
        self.max_row_limit = int(max_row_limit)
        self.default_timeout = None if effective_timeout is None else float(effective_timeout)
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="data-center-query")
        self._tasks: dict[str, _Task] = {}
        self._lock = threading.RLock()
        self._shutdown = False

    def submit(
        self,
        request: QueryRequest | QuerySpec | Mapping[str, Any] | Any,
        *,
        callback: Callable[[QueryResult], None] | None = None,
        on_result: Callable[[QueryResult], None] | None = None,
        cancellation: Any = None,
        cancel_token: Any = None,
        decision: SQLDecision | None = None,
        policy_decision: SQLDecision | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
        row_limit: int | None = None,
    ) -> Future[QueryResult]:
        """Queue a query without creating a driver on the caller thread."""

        if callback is not None and on_result is not None:
            raise TypeError("provide callback or on_result, not both")
        if cancellation is not None and cancel_token is not None:
            raise TypeError("provide cancellation or cancel_token, not both")
        if decision is not None and policy_decision is not None:
            raise TypeError("provide decision or policy_decision, not both")
        supplied_decision = decision if decision is not None else policy_decision
        try:
            spec = _normalize_request(
                request,
                default_row_limit=self.default_row_limit,
                max_row_limit=self.max_row_limit,
                clock=self._clock,
                timeout=timeout,
                deadline=deadline,
                row_limit=row_limit,
                default_timeout=self.default_timeout,
            )
        except Exception:
            return _completed(_failure_result("ARGUMENT_INVALID", _request_query_id(request)))
        if supplied_decision is not None and not isinstance(supplied_decision, SQLDecision):
            return _completed(_failure_result("POLICY_DENIED", spec.query_id))

        token = cancellation if cancellation is not None else cancel_token
        token = token if token is not None else CancellationToken()
        cb = callback if callback is not None else on_result
        task = _Task(spec=spec, token=token, callback=cb, decision=supplied_decision)
        with self._lock:
            if self._shutdown:
                return _completed(_failure_result("EXECUTOR_CLOSED", spec.query_id))
            if spec.query_id in self._tasks:
                return _completed(_failure_result("DUPLICATE_QUERY_ID", spec.query_id))
            self._tasks[spec.query_id] = task
            if spec.deadline is not None:
                delay = max(0.0, spec.deadline - float(self._clock()))
                task.timer = threading.Timer(delay, self._deadline_cancel, args=(task,))
                task.timer.daemon = True
                task.timer.start()
            future = self._pool.submit(self._run, task)
            task.future = future
            future.query_id = spec.query_id  # type: ignore[attr-defined]
            future.session_id = spec.session_id  # type: ignore[attr-defined]
            future.generation = spec.generation  # type: ignore[attr-defined]
            future.deadline = spec.deadline  # type: ignore[attr-defined]
            return future

    enqueue = submit
    execute_async = submit

    def execute(
        self,
        request: QueryRequest | QuerySpec | Mapping[str, Any] | Any,
        *,
        cancellation: Any = None,
        cancel_token: Any = None,
        decision: SQLDecision | None = None,
        policy_decision: SQLDecision | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
        row_limit: int | None = None,
        wait_timeout: float | None = None,
    ) -> QueryResult:
        future = self.submit(
            request,
            cancellation=cancellation,
            cancel_token=cancel_token,
            decision=decision,
            policy_decision=policy_decision,
            timeout=timeout,
            deadline=deadline,
            row_limit=row_limit,
        )
        # A synchronous caller must honour the query's own deadline even when
        # it did not supply a separate ``wait_timeout``. ``Future.result``
        # otherwise waits for an uncooperative driver to finish, which makes a
        # 50 ms query timeout look like a 400 ms timeout. The worker is left
        # running so it can close its own driver in ``_run``'s finally block.
        effective_wait = wait_timeout
        query_deadline = getattr(future, "deadline", None)
        if query_deadline is not None:
            remaining = max(0.0, float(query_deadline) - float(self._clock()))
            effective_wait = remaining if effective_wait is None else min(float(effective_wait), remaining)
        try:
            return future.result(timeout=effective_wait)
        except TimeoutError:
            query_id = str(getattr(future, "query_id", _request_query_id(request)) or "")
            self.cancel(query_id, reason="timeout")
            return _failure_result("TIMEOUT", query_id)

    run = execute
    query = execute
    execute_query = execute

    def cancel(self, query_id: str, reason: str = "cancelled") -> bool:
        """Request cooperative cancellation and suppress late callbacks."""

        qid = str(query_id or "").strip()
        with self._lock:
            task = self._tasks.get(qid)
            if task is None:
                return False
            if str(reason or "").strip().casefold() == "timeout":
                task.timed_out = True
            _cancel_token(task.token, reason)
            future = task.future
            if future is not None and future.cancel():
                self._tasks.pop(qid, None)
            return True

    cancel_query = cancel
    stop = cancel

    def is_running(self, query_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(str(query_id or "").strip())
            return task is not None and (task.future is None or not task.future.done())

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            tasks = tuple(self._tasks.values())
        for task in tasks:
            _cancel_token(task.token, "executor_shutdown")
        self._pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    close = shutdown

    def _deadline_cancel(self, task: _Task) -> None:
        with self._lock:
            if self._tasks.get(task.spec.query_id) is not task:
                return
            task.timed_out = True
        _cancel_token(task.token, "timeout")

    def _run(self, task: _Task) -> QueryResult:
        spec = task.spec
        driver: Any = None
        timer = task.timer
        try:
            if _is_cancelled(task.token):
                return _terminal(task, "CANCELLED")
            if _deadline_expired(spec.deadline, self._clock()):
                task.timed_out = True
                return _terminal(task, "TIMEOUT")
            session = self.session_manager.get_session(spec.session_id)
            if session is None:
                return _failure_result("SESSION_NOT_FOUND", spec.query_id)
            if not self.session_manager.is_current(spec.session_id, spec.generation, session.connection_id):
                return _failure_result("STALE_GENERATION", spec.query_id)
            approved_sql = self._approved_sql(spec, session, task.decision)
            if approved_sql is None:
                return _failure_result("POLICY_DENIED", spec.query_id)

            # The factory is invoked only after the worker starts.
            factory = self.session_manager._factory_for(session)
            if factory is None:
                return _failure_result("CAPABILITY_UNAVAILABLE", spec.query_id)
            driver = _create_driver(factory, session)
            if driver is None:
                return _failure_result("CAPABILITY_UNAVAILABLE", spec.query_id)
            if _is_cancelled(task.token):
                return _terminal(task, "CANCELLED")
            query = ReadOnlyQuery(
                sql=approved_sql,
                parameters=spec.parameters,
                row_limit=spec.row_limit,
                dialect=session.dialect,
                query_id=spec.query_id,
                deadline=spec.deadline,
                session_id=spec.session_id,
                connection_id=session.connection_id,
                generation=spec.generation,
            )
            value = None
            if self.read_only_hook is not None:
                value = _invoke_hook(self.read_only_hook, driver, query, task.token, spec)
                if isinstance(value, bool):
                    if value is False:
                        return _failure_result("POLICY_DENIED", spec.query_id)
                    value = None
            if value is None:
                value = _invoke_driver(
                    driver,
                    query,
                    task.token,
                    spec,
                    allow_generic=self.read_only_hook is not None,
                )
            if task.timed_out or _deadline_expired(spec.deadline, self._clock()):
                task.timed_out = True
                return _terminal(task, "TIMEOUT")
            if _is_cancelled(task.token):
                return _terminal(task, "CANCELLED")
            result = _normalize_result(value, query_id=spec.query_id, row_limit=spec.row_limit)
            if not self.session_manager.is_current(spec.session_id, spec.generation, session.connection_id):
                return _failure_result("STALE_RESULT", spec.query_id)
            if task.callback is not None:
                try:
                    task.callback(result)
                except Exception:
                    pass
            return result
        except TimeoutError:
            task.timed_out = True
            return _terminal(task, "TIMEOUT")
        except _ReadGuardNotReady:
            return _failure_result("READ_GUARD_NOT_READY", spec.query_id)
        except _DriverCapabilityUnavailable:
            return _failure_result("CAPABILITY_UNAVAILABLE", spec.query_id)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            if task.timed_out or _deadline_expired(spec.deadline, self._clock()):
                task.timed_out = True
                return _terminal(task, "TIMEOUT")
            if _is_cancelled(task.token):
                return _terminal(task, "CANCELLED")
            return _failure_result("QUERY_FAILED", spec.query_id)
        finally:
            if timer is not None:
                timer.cancel()
            if driver is not None:
                _close_driver(driver)
            with self._lock:
                if self._tasks.get(spec.query_id) is task:
                    self._tasks.pop(spec.query_id, None)

    def _approved_sql(self, spec: QuerySpec, session: Session, supplied: SQLDecision | None) -> str | None:
        decision = supplied
        if decision is None:
            try:
                decision = _invoke_policy(_policy_validator(self.sql_policy), spec.sql, session.dialect)
            except Exception:
                return None
        if not isinstance(decision, SQLDecision) or decision.allowed is not True:
            return None
        if _canonical_dialect(decision.dialect) != _canonical_dialect(session.dialect):
            return None
        if not isinstance(decision.sql, str) or not decision.sql.strip():
            return None
        # A decision approves exactly the request that was checked.  Execute
        # the request text after the comparison so a caller cannot pair an
        # allow decision for one statement with a different statement.
        if _canonical_sql(decision.sql) != _canonical_sql(spec.sql):
            return None
        return spec.sql.strip() or None


def _looks_like_factory(value: Any) -> bool:
    if isinstance(value, Mapping):
        return True
    if inspect.isfunction(value) or inspect.ismethod(value) or inspect.isclass(value):
        return True
    return callable(value) or callable(getattr(value, "connect", None))


def _canonical_sql(sql: Any) -> str:
    return _SQL_WHITESPACE.sub(" ", str(sql or "").strip()).casefold()


def _canonical_dialect(dialect: Any) -> str:
    value = str(dialect or "").strip().casefold().replace("_", " ").replace("-", " ")
    value = _SQL_WHITESPACE.sub(" ", value)
    aliases = {
        "ob": "oracle",
        "oceanbase": "oracle",
        "oceanbase oracle": "oracle",
        "ob oracle": "oracle",
        "oceanbase mysql": "mysql",
        "ob mysql": "mysql",
        "dameng": "oracle",
        "dm": "oracle",
        "dm8": "oracle",
    }
    return aliases.get(value, value)


def _normalize_request(
    request: QueryRequest | QuerySpec | Mapping[str, Any] | Any,
    *,
    default_row_limit: int,
    max_row_limit: int,
    clock: Callable[[], float],
    timeout: float | None,
    deadline: float | None,
    row_limit: int | None,
    default_timeout: float | None,
) -> QuerySpec:
    if isinstance(request, Mapping):
        get = lambda key, default=None: request.get(key, default)
    else:
        get = lambda key, default=None: getattr(request, key, default)
    session_id = str(get("session_id", "") or "").strip()
    query_id = str(get("query_id", "") or "").strip()
    sql = get("sql", None)
    generation = get("generation", None)
    if not session_id or not query_id or not isinstance(sql, str) or not sql.strip():
        raise ValueError("session_id, query_id and sql are required")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("generation must be a positive integer")
    parameters = get("parameters", {})
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be a mapping")
    limits = get("limits", {}) or {}
    if not isinstance(limits, Mapping):
        raise ValueError("limits must be a mapping")
    selected_limit = row_limit if row_limit is not None else get("row_limit", None)
    if selected_limit is None:
        selected_limit = limits.get("row_limit", default_row_limit)
    if isinstance(selected_limit, bool) or not isinstance(selected_limit, int) or not 1 <= selected_limit <= max_row_limit:
        raise ValueError("row_limit exceeds the read-only limit")

    selected_deadline = deadline if deadline is not None else get("deadline", None)
    if selected_deadline is None:
        selected_deadline = limits.get("deadline", None)
    if selected_deadline is None:
        selected_timeout = timeout if timeout is not None else limits.get("timeout", default_timeout)
        if selected_timeout is not None:
            if isinstance(selected_timeout, bool) or float(selected_timeout) < 0:
                raise ValueError("timeout must be non-negative")
            selected_deadline = float(clock()) + float(selected_timeout)
    else:
        if isinstance(selected_deadline, bool):
            raise ValueError("deadline must be a timestamp")
        selected_deadline = float(selected_deadline)
    return QuerySpec(session_id, query_id, generation, sql, parameters, selected_limit, selected_deadline)


def _request_query_id(request: Any) -> str:
    if isinstance(request, Mapping):
        return str(request.get("query_id", "") or "")
    return str(getattr(request, "query_id", "") or "")


def _policy_validator(policy: Any) -> Callable[..., Any]:
    for name in ("validate", "decide", "check"):
        method = getattr(policy, name, None)
        if callable(method):
            return method
    if callable(policy):
        return policy
    raise TypeError("sql_policy has no validator")


def _invoke_policy(validator: Callable[..., Any], sql: str, dialect: str) -> Any:
    try:
        signature = inspect.signature(validator)
    except (TypeError, ValueError):
        return validator(sql, dialect)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        return validator(sql, dialect=dialect)
    positional = [item for item in params if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    keyword_only = {item.name for item in params if item.kind == inspect.Parameter.KEYWORD_ONLY}
    if len(positional) >= 2:
        return validator(sql, dialect)
    if "dialect" in keyword_only:
        return validator(sql, dialect=dialect)
    return validator(sql)


def _invoke_hook(hook: Callable[..., Any], driver: Any, query: ReadOnlyQuery, token: Any, spec: QuerySpec) -> Any:
    values = {
        "driver": driver, "connection": driver, "conn": driver, "session": driver, "client": driver,
        "request": query, "query": query, "payload": query, "read_query": query,
        "sql": query.sql, "parameters": query.parameters, "params": query.parameters,
        "row_limit": query.row_limit, "limit": query.row_limit, "deadline": query.deadline,
        "query_id": spec.query_id, "session_id": spec.session_id, "generation": spec.generation,
        "cancellation": token, "cancel_token": token, "cancel": token, "token": token,
    }
    return _call_declared(hook, values, fallback=(query, driver, query.sql, query.parameters))


def _invoke_driver(
    driver: Any,
    query: ReadOnlyQuery,
    token: Any,
    spec: QuerySpec,
    *,
    allow_generic: bool,
) -> Any:
    method = next(
        (
            getattr(driver, name, None)
            for name in _READ_ONLY_METHODS
            if callable(getattr(driver, name, None))
        ),
        None,
    )
    if method is None and allow_generic:
        method = next(
            (
                getattr(driver, name, None)
                for name in _GENERIC_METHODS
                if callable(getattr(driver, name, None))
            ),
            None,
        )
    if method is not None:
        values = {
            "driver": driver, "connection": driver, "conn": driver, "session": driver, "client": driver,
            "request": query, "query": query, "payload": query, "read_query": query,
            "sql": query.sql, "statement": query.sql, "parameters": query.parameters, "params": query.parameters,
            "binds": query.parameters, "bindings": query.parameters, "args": query.parameters,
            "row_limit": query.row_limit, "limit": query.row_limit, "max_rows": query.row_limit,
            "deadline": query.deadline, "timeout": _remaining_timeout(query.deadline),
            "query_id": spec.query_id, "session_id": spec.session_id, "generation": spec.generation,
            "cancellation": token, "cancel_token": token, "cancel": token, "token": token,
        }
        return _call_declared(method, values, fallback=(query.sql, query.parameters, query.row_limit))
    cursor_factory = getattr(driver, "cursor", None)
    if not callable(cursor_factory):
        if allow_generic:
            raise _DriverCapabilityUnavailable("driver has no read-only query method")
        raise _ReadGuardNotReady("driver has no explicit read-only query method")
    if not allow_generic:
        raise _ReadGuardNotReady("generic cursor requires a read-only hook")
    cursor = cursor_factory()
    try:
        execute = getattr(cursor, "execute", None)
        if not callable(execute):
            raise LookupError("cursor has no execute method")
        _call_declared(execute, {"sql": query.sql, "statement": query.sql, "parameters": query.parameters, "params": query.parameters}, fallback=(query.sql, query.parameters))
        rows = []
        for row in cursor:
            rows.append(row)
            if len(rows) > query.row_limit:
                break
        return {"columns": getattr(cursor, "description", ()) or (), "rows": rows, "truncated": len(rows) > query.row_limit}
    finally:
        _close_driver(cursor)


def _call_declared(method: Callable[..., Any], values: Mapping[str, Any], *, fallback: tuple[Any, ...]) -> Any:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(*fallback)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        kwargs = {key: value for key, value in values.items() if key not in _DRIVER_NAMES}
        return method(**kwargs)
    positional = [item for item in params if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    args: list[Any] = []
    fallback_index = 0
    for item in positional:
        if item.name in values:
            args.append(values[item.name])
        elif fallback_index < len(fallback):
            args.append(fallback[fallback_index])
            fallback_index += 1
        elif item.default is inspect.Parameter.empty:
            raise TypeError(f"unsupported callable parameter: {item.name}")
    kwargs = {
        item.name: values[item.name]
        for item in params
        if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
    }
    return method(*args, **kwargs)


def _create_driver(factory: Any, session: Session) -> Any:
    if isinstance(factory, Mapping):
        factory = factory.get(session.connection_id)
    if factory is None:
        return None
    if any(callable(getattr(factory, name, None)) for name in _READ_ONLY_METHODS) or callable(getattr(factory, "cursor", None)):
        return factory
    connector = factory if callable(factory) else getattr(factory, "connect", None)
    if not callable(connector):
        return factory
    values = {
        "connection_id": session.connection_id,
        "connection": session.connection_id,
        "session_id": session.session_id,
        "generation": session.generation,
        "session": session,
    }
    return _call_declared(connector, values, fallback=(session.connection_id,))


def _close_driver(driver: Any) -> None:
    for name in _CLOSE_METHODS:
        closer = getattr(driver, name, None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
            return


def _normalize_result(value: Any, *, query_id: str, row_limit: int) -> QueryResult:
    if isinstance(value, QueryResult):
        return QueryResult(
            ok=value.ok, code=value.code, query_id=query_id, columns=value.columns,
            rows=value.rows[:row_limit], truncated=bool(value.truncated or len(value.rows) > row_limit),
            elapsed_ms=value.elapsed_ms, evidence_id=value.evidence_id,
        )
    if isinstance(value, ToolResult):
        if not value.ok:
            return QueryResult(ok=False, code=value.code, query_id=query_id, elapsed_ms=value.elapsed_ms, evidence_id=value.evidence_id)
        result = _tabular(value.data, query_id, row_limit, value.truncated)
        return QueryResult(ok=result.ok, code=result.code, query_id=query_id, columns=result.columns, rows=result.rows, truncated=result.truncated, elapsed_ms=value.elapsed_ms, evidence_id=value.evidence_id)
    if isinstance(value, Mapping) and value.get("ok") is False:
        return QueryResult(ok=False, code=str(value.get("code", "QUERY_FAILED")), query_id=query_id)
    if isinstance(value, Mapping) and "data" in value and ("ok" in value or "code" in value):
        result = _tabular(value.get("data"), query_id, row_limit, bool(value.get("truncated", False)))
        return QueryResult(ok=result.ok, code=result.code, query_id=query_id, columns=result.columns, rows=result.rows, truncated=result.truncated, elapsed_ms=value.get("elapsed_ms", value.get("elapsedMs")), evidence_id=value.get("evidence_id", value.get("evidenceId")))
    return _tabular(value, query_id, row_limit)


def _tabular(value: Any, query_id: str, row_limit: int, source_truncated: bool = False) -> QueryResult:
    columns_raw: Any = ()
    rows_raw: Any = ()
    if isinstance(value, Mapping):
        columns_raw = value.get("columns", value.get("description", ()))
        rows_raw = value.get("rows", value.get("items", value.get("data", ())))
        source_truncated = bool(source_truncated or value.get("truncated", False))
        source_count = value.get("row_count", value.get("total_rows"))
    elif _looks_like_cursor(value):
        columns_raw = getattr(value, "description", ()) or ()
        rows_raw = value
        source_count = None
    elif _is_columns_rows(value):
        columns_raw, rows_raw = value
        source_count = None
    else:
        rows_raw = value
        source_count = None
    columns = _columns(columns_raw)
    rows = _rows(rows_raw, columns)
    if not columns and rows:
        width = max(len(row) for row in rows)
        columns = tuple({"name": f"column_{index + 1}"} for index in range(width))
    if columns:
        width = len(columns)
        rows = tuple(tuple(row[index] if index < len(row) else None for index in range(width)) for row in rows)
    truncated = bool(source_truncated or len(rows) > row_limit)
    if isinstance(source_count, int) and not isinstance(source_count, bool):
        truncated = truncated or source_count > row_limit
    return QueryResult(ok=True, code="OK", query_id=query_id, columns=columns, rows=rows[:row_limit], truncated=truncated)


def _columns(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None or not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    result = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            result.append(dict(item))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and item:
            result.append({"name": str(item[0]), **({"type": item[1]} if len(item) > 1 else {})})
        else:
            result.append({"name": str(item) if item is not None else f"column_{index + 1}"})
    return tuple(result)


def _rows(value: Any, columns: Sequence[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    if value is None:
        return ()
    if _looks_like_cursor(value):
        try:
            value = list(value)
        except TypeError:
            return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        value = [value]
    names = [str(item.get("name", "")) for item in columns]
    rows = []
    for row in value:
        if isinstance(row, Mapping):
            rows.append(tuple(row.get(name) for name in names))
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            rows.append(tuple(row))
        else:
            rows.append((row,))
    return tuple(rows)


def _looks_like_cursor(value: Any) -> bool:
    return value is not None and hasattr(value, "description") and callable(getattr(value, "__iter__", None))


def _is_columns_rows(value: Any) -> bool:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return False
    first, second = value
    return isinstance(first, Sequence) and not isinstance(first, (str, bytes, bytearray)) and not isinstance(second, (str, bytes, bytearray))


def _failure_result(code: str, query_id: str = "") -> QueryResult:
    return QueryResult(ok=False, code=str(code), query_id=str(query_id or ""))


def _completed(result: QueryResult) -> Future[QueryResult]:
    future: Future[QueryResult] = Future()
    future.set_result(result)
    future.query_id = result.query_id  # type: ignore[attr-defined]
    return future


def _terminal(task: _Task, code: str) -> QueryResult:
    return _failure_result(code, task.spec.query_id)


def _deadline_expired(deadline: float | None, now: float) -> bool:
    return deadline is not None and float(now) >= float(deadline)


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, float(deadline) - time.monotonic())


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


def _cancel_token(token: Any, reason: str) -> None:
    cancel = getattr(token, "cancel", None)
    if callable(cancel):
        try:
            cancel(reason)
        except TypeError:
            try:
                cancel()
            except Exception:
                pass
        except Exception:
            pass
        return
    setter = getattr(token, "set", None)
    if callable(setter):
        try:
            setter()
        except Exception:
            pass


__all__ = [
    "DEFAULT_QUERY_TIMEOUT",
    "DEFAULT_ROW_LIMIT",
    "QueryExecutor",
    "QuerySpec",
    "ReadOnlyQuery",
]
