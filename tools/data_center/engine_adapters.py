"""Injected, read-only adapters for the data-center engines.

The data-center Agent receives a tool boundary, never a connection profile.
This module therefore contains capability metadata and small dispatchers around
objects supplied by the host.  It deliberately does not import
``tools.db_connect``: that module owns the legacy manual console path (whose
write statements commit automatically), while an Agent lease must use an
independently injected read-only hook or driver.

The adapters are useful with a fake driver in unit tests and with a future
session manager.  Constructing an adapter performs no network or database
operation.  A rejected operation is returned before a resolver, hook or driver
method is looked up or called.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import inspect
from typing import Any, Protocol

from .contracts import CancellationToken, ToolResult
from .nosql_policy import (
    MONGO_READ_OPERATIONS,
    REDIS_READ_OPERATIONS,
    MongoReadPolicy,
    RedisReadPolicy,
)


# The public engine ids follow the connection/provider ids already used by the
# repository.  OceanBase is split by compatibility mode because parser
# dialect and driver choice are different in the two modes.  ``mongodb`` is
# canonical here; ``mongo`` remains a safe input alias.
RELATIONAL_ENGINES: tuple[str, ...] = (
    "oracle",
    "mysql",
    "oceanbase_oracle",
    "oceanbase_mysql",
    "dameng",
)
NOSQL_ENGINES: tuple[str, ...] = ("redis", "mongodb")

READ_ONLY_QUERY = "query_readonly"
REDIS_READ = "redis_read"
MONGO_READ = "mongo_read"


@dataclass(frozen=True, slots=True)
class EngineCapability:
    """Static capabilities exposed for one canonical engine id.

    ``dialect`` is the SQL/parser dialect to be supplied to an injected
    read-only hook.  No connection, host, username, password or URI is stored
    in this object.
    """

    engine: str
    label: str
    family: str
    dialect: str | None
    operations: frozenset[str]
    read_only: bool = True
    supports_transactions: bool = False
    supports_cancel: bool = False

    @property
    def name(self) -> str:
        """Compatibility alias used by callers that call the id ``name``."""

        return self.engine

    @property
    def parser_dialect(self) -> str | None:
        return self.dialect

    @property
    def read_operations(self) -> frozenset[str]:
        return self.operations

    def allows(self, operation: str) -> bool:
        return str(operation or "").strip().casefold() in {
            item.casefold() for item in self.operations
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "name": self.engine,
            "label": self.label,
            "family": self.family,
            "dialect": self.dialect,
            "operations": sorted(self.operations),
            "read_only": self.read_only,
            "supports_transactions": self.supports_transactions,
            "supports_cancel": self.supports_cancel,
        }


def _relational_operations() -> frozenset[str]:
    return frozenset(
        {
            READ_ONLY_QUERY,
            "query_read_only",
            "readonly_query",
            "read_only_query",
            "read",
            "select",
        }
    )


def _redis_operations() -> frozenset[str]:
    return frozenset({REDIS_READ, *REDIS_READ_OPERATIONS})


def _mongo_operations() -> frozenset[str]:
    return frozenset({MONGO_READ, *MONGO_READ_OPERATIONS})


ENGINE_CAPABILITIES: dict[str, EngineCapability] = {
    "oracle": EngineCapability(
        "oracle", "Oracle", "relational", "oracle", _relational_operations(),
        supports_transactions=True, supports_cancel=True,
    ),
    "mysql": EngineCapability(
        "mysql", "MySQL", "relational", "mysql", _relational_operations(),
        supports_transactions=True, supports_cancel=True,
    ),
    "oceanbase_oracle": EngineCapability(
        "oceanbase_oracle", "OceanBase（Oracle 模式）", "relational", "oracle",
        _relational_operations(), supports_transactions=True, supports_cancel=True,
    ),
    "oceanbase_mysql": EngineCapability(
        "oceanbase_mysql", "OceanBase（MySQL 模式）", "relational", "mysql",
        _relational_operations(), supports_transactions=True, supports_cancel=True,
    ),
    "dameng": EngineCapability(
        "dameng", "达梦", "relational", "oracle", _relational_operations(),
        supports_transactions=True, supports_cancel=True,
    ),
    "redis": EngineCapability(
        "redis", "Redis", "nosql", None, _redis_operations(),
        supports_cancel=False,
    ),
    "mongodb": EngineCapability(
        "mongodb", "MongoDB", "nosql", None, _mongo_operations(),
        supports_cancel=False,
    ),
}

# Friendly public aliases.  Keep the canonical mapping free of aliases so its
# seven entries remain a useful capability matrix for UI/model definitions.
ENGINE_CAPABILITY_MAP = ENGINE_CAPABILITIES
ENGINE_CAPABILITY_MATRIX = ENGINE_CAPABILITIES
ENGINE_MAP = ENGINE_CAPABILITIES
CAPABILITIES = ENGINE_CAPABILITIES
SUPPORTED_ENGINES = tuple(ENGINE_CAPABILITIES)

_ENGINE_ALIASES: dict[str, str] = {
    "ob": "oceanbase_oracle",
    "oceanbase": "oceanbase_oracle",
    "oceanbase_oracle": "oceanbase_oracle",
    "oceanbase-oracle": "oceanbase_oracle",
    "oceanbase oracle": "oceanbase_oracle",
    "ob_oracle": "oceanbase_oracle",
    "ob-oracle": "oceanbase_oracle",
    "oceanbase_mysql": "oceanbase_mysql",
    "oceanbase-mysql": "oceanbase_mysql",
    "oceanbase mysql": "oceanbase_mysql",
    "ob_mysql": "oceanbase_mysql",
    "ob-mysql": "oceanbase_mysql",
    "dm": "dameng",
    "dm8": "dameng",
    "dameng": "dameng",
    "达梦": "dameng",
    "mongo": "mongodb",
    "mongodb": "mongodb",
    "mongo_db": "mongodb",
    "mongo-db": "mongodb",
}


def normalize_engine(engine: str | Mapping[str, Any], mode: str | None = None) -> str:
    """Return a canonical provider id without reading or altering a profile.

    When a connection-like mapping is supplied only its dialect/provider and
    OceanBase mode are consulted.  Credentials and target addresses are never
    read or copied into an adapter.
    """

    if isinstance(engine, Mapping):
        data = engine
        raw = data.get("engine", data.get("provider", data.get("dialect", "")))
        if mode is None:
            mode = data.get("mode")
    else:
        raw = engine
    value = str(raw or "").strip().casefold().replace(" ", " ")
    if value == "oceanbase":
        selected_mode = str(mode or "").strip().casefold()
        return "oceanbase_mysql" if selected_mode == "mysql" else "oceanbase_oracle"
    if value in {"oceanbase_oracle", "oceanbase-oracle", "ob_oracle", "ob-oracle"}:
        return "oceanbase_oracle"
    if value in {"oceanbase_mysql", "oceanbase-mysql", "ob_mysql", "ob-mysql"}:
        return "oceanbase_mysql"
    if value in _ENGINE_ALIASES:
        return _ENGINE_ALIASES[value]
    if value in ENGINE_CAPABILITIES:
        return value
    raise ValueError(f"unsupported data-center engine: {raw!r}")


def get_engine_capability(
    engine: str | Mapping[str, Any], mode: str | None = None
) -> EngineCapability:
    return ENGINE_CAPABILITIES[normalize_engine(engine, mode)]


def engine_capabilities() -> dict[str, EngineCapability]:
    """Return a shallow copy of the static capability matrix."""

    return dict(ENGINE_CAPABILITIES)


class ReadOnlyHook(Protocol):
    """Host-injected relation query hook; it owns the isolated read session."""

    def __call__(self, request: Mapping[str, Any]) -> Any:
        ...


class ReadOnlyRequest(dict[str, Any]):
    """Mapping passed to a relational read-only hook.

    It is a dict for simple fake hooks and also exposes attributes for host
    adapters that prefer a request object.  The only fields are query text,
    parameters, row limit and parser dialect.
    """

    def __init__(
        self,
        sql: str,
        parameters: Mapping[str, Any] | None,
        row_limit: int,
        dialect: str,
    ) -> None:
        super().__init__(
            sql=sql,
            parameters=dict(parameters or {}),
            row_limit=int(row_limit),
            dialect=dialect,
        )

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


# Both spellings appeared in early DC-02 design notes.
ReadOnlyQuery = ReadOnlyRequest


_HOST_OR_CREDENTIAL_KEYS = frozenset(
    {
        "host",
        "hostname",
        "port",
        "user",
        "username",
        "password",
        "passwd",
        "pwd",
        "token",
        "secret",
        "authorization",
        "api_key",
        "apikey",
        "connection",
        "connection_id",
        "database",
        "schema",
        "dsn",
        "uri",
        "ssh",
        "ssh_host",
        "ssh_port",
        "file",
        "file_path",
        "path",
        "python",
        "python_code",
        "code",
        "command",
        "cmd",
        "shell",
        "shell_command",
        "executable",
    }
)

_MISSING_VALUE = object()


def _contains_host_override(value: Any, *, depth: int = 0) -> bool:
    # Only the tool envelope is a connection boundary.  A nested Mongo field
    # named ``host`` or a SQL bind named ``password`` is data selected by the
    # user and must not be mistaken for a request to replace the host profile.
    # Engine-specific policies still reject unknown top-level fields and
    # unsafe Mongo operators.
    if not isinstance(value, Mapping):
        return False
    return any(
        isinstance(key, str) and key.casefold() in _HOST_OR_CREDENTIAL_KEYS
        for key in value
    )


def _contains_nested_credential_key(value: Any, *, depth: int = 0) -> bool:
    """Check relation bind values for an attempted profile override.

    Relation parameters are not user-selected document fields, so a nested
    ``password``/``host`` key there is treated as a credential injection.  The
    Mongo adapter intentionally does not use this helper because a Mongo
    document may legitimately contain a field with one of those names.
    """

    if depth > 12:
        return False
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in _HOST_OR_CREDENTIAL_KEYS:
                return True
            if _contains_nested_credential_key(item, depth=depth + 1):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_nested_credential_key(item, depth=depth + 1) for item in value)
    return False


def _failure(code: str, message: str) -> ToolResult:
    # Never echo request or driver exception text across the model boundary.
    return ToolResult(ok=False, code=code, data={"message": message})


def _success(data: Any, *, truncated: bool = False) -> ToolResult:
    if isinstance(data, ToolResult):
        return data
    if isinstance(data, Mapping) and "ok" in data:
        return ToolResult.from_value(data)
    return ToolResult(ok=True, data=data, truncated=truncated)


def _is_cancelled(token: Any) -> bool:
    if token is None:
        return False
    method = getattr(token, "is_cancelled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return True
    return bool(getattr(token, "cancelled", False))


class EngineAdapter:
    """Base boundary shared by relational and NoSQL adapters."""

    def __init__(
        self,
        capability: EngineCapability | str,
        *,
        driver: Any = None,
    ) -> None:
        self.capability = (
            capability
            if isinstance(capability, EngineCapability)
            else get_engine_capability(capability)
        )
        self.engine = self.capability.engine
        self.dialect = self.capability.dialect
        # This is a host-injected fake/driver only.  It is never constructed by
        # this module and it is intentionally not exposed in model payloads.
        self._driver = driver

    @property
    def capabilities(self) -> EngineCapability:
        return self.capability

    @property
    def supported_operations(self) -> frozenset[str]:
        return self.capability.operations

    def supports(self, operation: str) -> bool:
        return self.capability.allows(operation)

    can = supports

    def execute(
        self,
        operation: str | Mapping[str, Any] | None = None,
        request: Mapping[str, Any] | None = None,
        *,
        cancellation: CancellationToken | Any | None = None,
        decision: Any = None,
        sql_decision: Any = None,
        **fields: Any,
    ) -> ToolResult:
        """Validate and dispatch one structured read operation.

        ``operation`` may be the tool name, a concrete operation such as
        ``GET``, or the complete request mapping.  The flexible call shape is
        convenient for injected registries while all accepted fields remain
        closed by the engine-specific policy.
        """

        if isinstance(operation, Mapping):
            if request is not None or fields:
                return _failure("ARGUMENT_INVALID", "工具请求重复提供")
            request = operation
            operation = request.get("operation")
        if request is None:
            request = {}
        if not isinstance(request, Mapping):
            return _failure("ARGUMENT_INVALID", "工具请求必须是对象")
        if fields:
            merged = dict(request)
            merged.update(fields)
            request = merged
        elif not isinstance(request, dict):
            request = dict(request)
        name = str(operation or request.get("operation") or "").strip()
        if not name and self.capability.family == "relational" and request.get("sql") is not None:
            name = READ_ONLY_QUERY
        if not name:
            return _failure("ARGUMENT_INVALID", "缺少工具能力")
        if _contains_host_override(request):
            return _failure("SCOPE_DENIED", "工具不能覆盖宿主连接或凭据")
        if _is_cancelled(cancellation):
            return _failure("CANCELLED", "本轮已停止，未启动工具")
        if not self.supports(name):
            return _failure("CAPABILITY_UNAVAILABLE", "该引擎不提供此只读能力")
        if self.capability.family != "relational" and (decision is not None or sql_decision is not None):
            return _failure("ARGUMENT_INVALID", "SQL 决策仅适用于关系库")
        guard = sql_decision if sql_decision is not None else decision
        return self._execute_allowed(name, request, cancellation=cancellation, sql_decision=guard)

    # Registry-friendly aliases.
    execute_tool = execute
    dispatch = execute

    def _execute_allowed(
        self,
        operation: str,
        request: Mapping[str, Any],
        *,
        cancellation: Any = None,
        sql_decision: Any = None,
    ) -> ToolResult:
        raise NotImplementedError


def _invoke_readonly_hook(hook: Callable[..., Any], request: ReadOnlyRequest) -> Any:
    """Invoke a host hook once after inspecting its declared signature.

    Inspecting first avoids retrying a hook after a ``TypeError``: an injected
    implementation may have already performed a database action before
    raising one.
    """

    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):
        # A callable without an inspectable signature gets the narrow protocol
        # shape.  It is still invoked once and errors are contained by caller.
        return hook(request)

    params = list(signature.parameters.values())
    positional = [
        item for item in params
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in params)
    has_varkw = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params)
    names = {item.name for item in params}

    # A one-argument request/payload hook is the preferred injection shape.
    if len(positional) == 1 and not has_varargs:
        return hook(request)

    values = {
        "request": request,
        "query": request,
        "payload": request,
        "sql": request.sql,
        "parameters": request.parameters,
        "params": request.parameters,
        "row_limit": request.row_limit,
        "limit": request.row_limit,
        "dialect": request.dialect,
    }
    if has_varkw:
        return hook(
            request.sql,
            request.parameters,
            row_limit=request.row_limit,
            dialect=request.dialect,
        )

    if positional:
        args: list[Any] = []
        for item in positional:
            if item.name in values:
                args.append(values[item.name])
            elif item.default is not inspect.Parameter.empty:
                break
            else:
                # An unknown required positional shape is safest as the
                # mapping protocol; the host receives no credentials.
                return hook(request)
        kwargs = {
            item.name: values[item.name]
            for item in params
            if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
        }
        return hook(*args, **kwargs)

    kwargs = {
        item.name: values[item.name]
        for item in params
        if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
    }
    if kwargs or names:
        return hook(**kwargs)
    return hook(request)


def _policy_callable(policy: Any) -> Callable[..., Any] | None:
    """Resolve one host SQL validator without accepting model supplied code."""

    if policy is None:
        return None
    for name in ("validate", "decide", "check"):
        method = getattr(policy, name, None)
        if callable(method):
            return method
    if callable(policy):
        return policy
    return None


def _invoke_sql_policy(policy: Any, sql: str, dialect: str) -> Any:
    """Invoke an injected validator once using its declared parameter shape."""

    method = _policy_callable(policy)
    if method is None:
        raise TypeError("SQL policy has no validate method")
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(sql, dialect)
    params = list(signature.parameters.values())
    positional = [
        item for item in params
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in params)
    has_varkw = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params)
    values = {
        "sql": sql,
        "query": sql,
        "statement": sql,
        "dialect": dialect,
    }
    if has_varargs:
        return method(sql, dialect)
    if has_varkw:
        return method(sql, dialect=dialect)
    if positional:
        args: list[Any] = []
        for item in positional:
            if item.name in values:
                args.append(values[item.name])
            elif item.default is not inspect.Parameter.empty:
                break
            else:
                raise TypeError("SQL policy parameter shape is unsupported")
        kwargs = {
            item.name: values[item.name]
            for item in params
            if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
        }
        return method(*args, **kwargs)
    kwargs = {
        item.name: values[item.name]
        for item in params
        if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
    }
    return method(**kwargs)


def _decision_value(decision: Any, key: str, default: Any = _MISSING_VALUE) -> Any:
    if isinstance(decision, Mapping):
        return decision.get(key, default)
    return getattr(decision, key, default)


def _check_sql_decision(
    decision: Any,
    *,
    sql: str,
    dialect: str,
) -> tuple[bool, str, str, str]:
    """Check a host decision and return (allowed, code, message, SQL).

    A plain bool or arbitrary truthy value is not a decision.  The host must
    provide a structured object with an explicit boolean ``allowed`` field;
    optional SQL/dialect fields are checked when present so a stale decision
    cannot be reused for a different query.
    """

    allowed = _decision_value(decision, "allowed")
    if type(allowed) is not bool:
        return False, "READ_GUARD_NOT_READY", "只读 SQL 决策不可验证", sql
    code = _decision_value(decision, "code", "")
    message = _decision_value(decision, "message", "")
    approved_sql = _decision_value(decision, "sql", _MISSING_VALUE)
    approved_dialect = _decision_value(decision, "dialect", _MISSING_VALUE)
    if approved_dialect is not _MISSING_VALUE and approved_dialect not in (None, ""):
        if str(approved_dialect).strip().casefold() != str(dialect).strip().casefold():
            return False, "READ_GUARD_NOT_READY", "只读 SQL 决策方言不匹配", sql
    if approved_sql is not _MISSING_VALUE and approved_sql not in (None, ""):
        if not isinstance(approved_sql, str) or approved_sql.strip() != sql.strip():
            return False, "POLICY_DENIED", "SQL 决策与实际查询不一致", sql
        sql = approved_sql.strip()
    if allowed:
        return True, "OK", "", sql
    safe_code = str(code or "POLICY_DENIED")
    if safe_code in {"OK", ""}:
        safe_code = "POLICY_DENIED"
    # Do not carry validator/driver text into the model boundary.
    return False, safe_code, "只读 SQL 策略拒绝执行", sql


class RelationalEngineAdapter(EngineAdapter):
    """Adapter for Oracle/MySQL/OceanBase modes and Dameng.

    The only execution dependency is an injected read-only hook.  The hook is
    responsible for creating an isolated session, applying driver-specific
    read-only protection, timeout and cancellation.  This class never calls
    ``connect``, ``commit``, ``rollback`` or the legacy console helper.
    """

    def __init__(
        self,
        engine: str | Mapping[str, Any] = "oracle",
        read_only_hook: Callable[..., Any] | None = None,
        *,
        readonly_hook: Callable[..., Any] | None = None,
        hook: Callable[..., Any] | None = None,
        sql_policy: Any = None,
        read_only_policy: Any = None,
        sql_validator: Any = None,
        validator: Any = None,
        policy: Any = None,
        mode: str | None = None,
        dialect: str | None = None,
    ) -> None:
        capability = get_engine_capability(engine, mode)
        if capability.family != "relational":
            raise ValueError("RelationalEngineAdapter requires a relational engine")
        super().__init__(capability)
        self.dialect = str(dialect or capability.dialect or "").strip().lower() or None
        self._read_only_hook = read_only_hook or readonly_hook or hook
        # A query is executable only when this host-owned validator returns a
        # structured decision with ``allowed is True``.  A plain lexical check
        # is intentionally not accepted as a read-only guard.
        self._sql_policy = (
            sql_policy
            or read_only_policy
            or sql_validator
            or validator
            or policy
        )

    @property
    def read_only_hook(self) -> Callable[..., Any] | None:
        return self._read_only_hook

    @property
    def sql_policy(self) -> Any:
        return self._sql_policy

    def execute_readonly(
        self,
        sql: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        row_limit: int = 100,
        cancellation: Any = None,
        decision: Any = None,
        sql_decision: Any = None,
    ) -> ToolResult:
        return self.execute(
            READ_ONLY_QUERY,
            {"sql": sql, "parameters": parameters or {}, "row_limit": row_limit},
            cancellation=cancellation,
            decision=decision,
            sql_decision=sql_decision,
        )

    execute_read_only = execute_readonly
    query_readonly = execute_readonly

    def _execute_allowed(
        self,
        operation: str,
        request: Mapping[str, Any],
        *,
        cancellation: Any = None,
        sql_decision: Any = None,
    ) -> ToolResult:
        if self._read_only_hook is None:
            return _failure("CAPABILITY_UNAVAILABLE", "该关系库未注入只读执行能力")
        args = dict(request)
        supplied_operation = args.get("operation")
        if supplied_operation is not None:
            if str(operation).casefold() not in {str(supplied_operation).casefold(), REDIS_READ.casefold(), MONGO_READ.casefold()}:
                # For the relational adapter a conflicting operation is a
                # malformed request, never something to pass to the hook.
                aliases = {READ_ONLY_QUERY, "query_read_only", "readonly_query", "read_only_query", "read", "select"}
                if str(supplied_operation).casefold() not in {item.casefold() for item in aliases}:
                    return _failure("ARGUMENT_INVALID", "工具请求能力不一致")
        unknown = set(args) - {"sql", "parameters", "row_limit", "operation"}
        if unknown:
            return _failure("ARGUMENT_INVALID", "工具参数不在固定结构中")
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return _failure("ARGUMENT_INVALID", "query_readonly 需要非空 SQL")
        parameters = args.get("parameters", {})
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            return _failure("ARGUMENT_INVALID", "parameters 必须是对象")
        if _contains_nested_credential_key(parameters):
            return _failure("SCOPE_DENIED", "工具不能提供连接或凭据")
        row_limit = args.get("row_limit", 100)
        if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= 100:
            return _failure("ARGUMENT_INVALID", "row_limit 超出范围")
        if sql_decision is None:
            if self._sql_policy is None:
                return _failure("READ_GUARD_NOT_READY", "只读 SQL 门禁尚未注入，查询未执行")
            try:
                sql_decision = _invoke_sql_policy(self._sql_policy, sql.strip(), self.dialect or "")
            except Exception:
                return _failure("READ_GUARD_NOT_READY", "只读 SQL 决策不可验证，查询未执行")
        allowed, decision_code, decision_message, approved_sql = _check_sql_decision(
            sql_decision,
            sql=sql.strip(),
            dialect=self.dialect or "",
        )
        if not allowed:
            return _failure(decision_code, decision_message)
        if _is_cancelled(cancellation):
            return _failure("CANCELLED", "本轮已停止，未启动工具")
        query = ReadOnlyRequest(approved_sql, parameters, row_limit, self.dialect or "")
        try:
            value = _invoke_readonly_hook(self._read_only_hook, query)
        except TimeoutError:
            return _failure("TIMEOUT", "查询超时")
        except Exception:
            return _failure("QUERY_FAILED", "只读查询失败")
        return _success(value)


def _call_method_once(method: Callable[..., Any], **kwargs: Any) -> Any:
    """Call a fake/driver method with only declared keyword arguments."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**kwargs)
    params = signature.parameters
    positional_only_names = {
        item.name for item in params.values()
        if item.kind == inspect.Parameter.POSITIONAL_ONLY
    }
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return method(**kwargs)
    accepted = {
        name: value
        for name, value in kwargs.items()
        if name in params and name not in positional_only_names
    }
    # A positional-only fake is uncommon but can be handled without retrying.
    positional_only = [item for item in params.values() if item.kind == inspect.Parameter.POSITIONAL_ONLY]
    if positional_only:
        ordered = [kwargs[item.name] for item in positional_only if item.name in kwargs]
        return method(*ordered, **accepted)
    return method(**accepted)


class RedisEngineAdapter(EngineAdapter):
    """Structured Redis read adapter with no generic command escape hatch."""

    def __init__(
        self,
        driver: Any = None,
        *,
        policy: RedisReadPolicy | None = None,
        max_limit: int = 100,
        default_limit: int = 50,
    ) -> None:
        super().__init__(get_engine_capability("redis"), driver=driver)
        self.policy = policy or RedisReadPolicy(max_limit=max_limit, default_limit=default_limit)

    def read(
        self,
        operation: str,
        *,
        cancellation: Any = None,
        **arguments: Any,
    ) -> ToolResult:
        return self.execute(operation, arguments, cancellation=cancellation)

    redis_read = read

    def _execute_allowed(
        self,
        operation: str,
        request: Mapping[str, Any],
        *,
        cancellation: Any = None,
        sql_decision: Any = None,
    ) -> ToolResult:
        args = dict(request)
        if operation.casefold() == REDIS_READ.casefold():
            concrete = args.get("operation")
        else:
            concrete = operation
            if args.get("operation") is not None and str(args["operation"]).casefold() != concrete.casefold():
                return _failure("ARGUMENT_INVALID", "工具请求能力不一致")
            args["operation"] = concrete
        decision = self.policy.validate(args)
        if not decision.allowed:
            return decision.as_tool_result()
        if self._driver is None:
            return _failure("CAPABILITY_UNAVAILABLE", "Redis 只读驱动未注入")
        normalized = dict(decision.arguments)
        method_name = str(normalized.get("operation", "")).lower()
        method = getattr(self._driver, method_name, None)
        if not callable(method):
            return _failure("CAPABILITY_UNAVAILABLE", "Redis 只读操作未被驱动支持")
        if _is_cancelled(cancellation):
            return _failure("CANCELLED", "本轮已停止，未启动工具")

        key = normalized.get("key")
        pattern = normalized.get("pattern")
        cursor = normalized.get("cursor", 0)
        limit = int(normalized.get("limit", self.policy.default_limit))
        kwargs: dict[str, Any]
        if method_name == "scan":
            kwargs = {"cursor": cursor, "match": pattern, "count": limit}
        elif method_name in {"hscan", "sscan", "zscan"}:
            kwargs = {"name" if method_name == "hscan" else "key": key, "cursor": cursor, "match": pattern, "count": limit}
        elif method_name in {"hget"}:
            # The model schema has no free-form field argument.  ``pattern``
            # is the optional hash field for this structured operation.
            if not pattern:
                return _failure("ARGUMENT_INVALID", "HGET 需要结构化 field")
            kwargs = {"name": key, "key": pattern}
        elif method_name in {"lrange", "zrange"}:
            kwargs = {"name" if method_name == "lrange" else "name": key, "start": 0, "end": limit - 1}
            if method_name == "zrange":
                kwargs["withscores"] = False
        elif method_name == "xrange":
            kwargs = {"name": key, "min": "-", "max": "+", "count": limit}
        else:
            kwargs = {"name" if method_name not in {"type", "ttl", "pttl", "strlen", "llen", "scard", "zcard", "xlen"} else "name": key}
        # Most redis-py methods use ``name`` while the small fake drivers in
        # tests often use ``key``.  Inspecting allows both without a second call.
        parameter_names = set(inspect.signature(method).parameters) if _has_signature(method) else set()
        if parameter_names:
            if method_name != "hget" and "name" in kwargs and "name" not in parameter_names and "key" in parameter_names:
                value = kwargs.pop("name")
                if "key" not in kwargs:
                    kwargs["key"] = value
            if "key" in kwargs and "key" not in parameter_names and "name" in parameter_names:
                kwargs["name"] = kwargs.pop("key")
            if method_name == "hget" and "field" in parameter_names:
                # Convert our structured (hash key, pattern-as-field) pair to
                # the names used by simple fake clients.
                hash_key = kwargs.get("name", key)
                field = kwargs.get("key", pattern)
                kwargs.pop("name", None)
                kwargs.pop("key", None)
                kwargs["key"] = hash_key
                kwargs["field"] = field
        try:
            value = _call_method_once(method, **kwargs)
        except TimeoutError:
            return _failure("TIMEOUT", "Redis 读取超时")
        except Exception:
            return _failure("QUERY_FAILED", "Redis 读取失败")
        return _success(value)


def _has_signature(value: Any) -> bool:
    try:
        inspect.signature(value)
    except (TypeError, ValueError):
        return False
    return True


class MongoEngineAdapter(EngineAdapter):
    """MongoDB find/count/aggregate adapter over an injected collection scope."""

    def __init__(
        self,
        driver: Any = None,
        *,
        collections: Mapping[str, Any] | None = None,
        collection_resolver: Callable[[str], Any] | None = None,
        resolver: Callable[[str], Any] | None = None,
        allowed_collection_ids: Sequence[str] | None = None,
        policy: MongoReadPolicy | None = None,
        max_limit: int = 100,
    ) -> None:
        if collections is None and isinstance(driver, Mapping):
            collections = driver
            driver = None
        super().__init__(get_engine_capability("mongodb"), driver=driver)
        self._collections = dict(collections or {})
        self._collection_resolver = collection_resolver or resolver
        if allowed_collection_ids is None and self._collections:
            allowed_collection_ids = tuple(str(item) for item in self._collections)
        self.policy = policy or MongoReadPolicy(allowed_collection_ids)
        self.max_limit = int(max_limit)
        if not 1 <= self.max_limit <= 10_000:
            raise ValueError("invalid Mongo read limit")

    def read(
        self,
        collection_id: str,
        operation: str,
        *,
        cancellation: Any = None,
        **arguments: Any,
    ) -> ToolResult:
        payload = dict(arguments)
        payload.update(collection_id=collection_id, operation=operation)
        return self.execute(MONGO_READ, payload, cancellation=cancellation)

    mongo_read = read

    def _resolve_collection(self, collection_id: str) -> Any:
        if self._collections:
            if collection_id in self._collections:
                return self._collections[collection_id]
            folded = collection_id.casefold()
            for key, value in self._collections.items():
                if str(key).casefold() == folded:
                    return value
            return None
        if self._collection_resolver is not None:
            return self._collection_resolver(collection_id)
        getter = getattr(self._driver, "get_collection", None)
        if callable(getter):
            return getter(collection_id)
        return None

    def _execute_allowed(
        self,
        operation: str,
        request: Mapping[str, Any],
        *,
        cancellation: Any = None,
        sql_decision: Any = None,
    ) -> ToolResult:
        args = dict(request)
        if operation.casefold() == MONGO_READ.casefold():
            concrete = args.get("operation")
        else:
            concrete = operation
            if args.get("operation") is not None and str(args["operation"]).casefold() != concrete.casefold():
                return _failure("ARGUMENT_INVALID", "工具请求能力不一致")
            args["operation"] = concrete
        decision = self.policy.validate(args, max_limit=self.max_limit)
        if not decision.allowed:
            return decision.as_tool_result()
        normalized = dict(decision.arguments)
        collection_id = str(normalized["collection_id"])
        if _is_cancelled(cancellation):
            return _failure("CANCELLED", "本轮已停止，未启动工具")
        try:
            collection = self._resolve_collection(collection_id)
        except Exception:
            return _failure("CAPABILITY_UNAVAILABLE", "Mongo 集合解析失败")
        if collection is None:
            return _failure("SCOPE_DENIED", "集合不在宿主注入的授权范围内")
        method_name = str(normalized["operation"]).casefold()
        method = getattr(collection, {
            "find": "find",
            "count": "count_documents",
            "aggregate": "aggregate",
        }[method_name], None)
        if not callable(method):
            return _failure("CAPABILITY_UNAVAILABLE", "Mongo 只读操作未被驱动支持")

        try:
            if method_name == "find":
                value = self._find(method, normalized)
                rows = _bounded_values(value, self.max_limit)
                return _success(
                    {
                        "operation": "find",
                        "collection_id": collection_id,
                        "rows": rows,
                    },
                    truncated=len(rows) >= self.max_limit,
                )
            if method_name == "count":
                value = _call_method_once(method, filter=normalized.get("filter", {}))
                return _success({"operation": "count", "collection_id": collection_id, "count": value})
            value = _call_method_once(method, pipeline=normalized.get("pipeline", []))
            rows = _bounded_values(value, self.max_limit)
            return _success(
                {
                    "operation": "aggregate",
                    "collection_id": collection_id,
                    "rows": rows,
                },
                truncated=len(rows) >= self.max_limit,
            )
        except TimeoutError:
            return _failure("TIMEOUT", "Mongo 读取超时")
        except Exception:
            return _failure("QUERY_FAILED", "Mongo 读取失败")

    def _find(self, method: Callable[..., Any], args: Mapping[str, Any]) -> Any:
        kwargs: dict[str, Any] = {"filter": args.get("filter", {})}
        if "projection" in args:
            kwargs["projection"] = args["projection"]
        if "sort" in args:
            sort = args["sort"]
            # PyMongo accepts a list of (field, direction) pairs; preserving a
            # mapping for a fake driver would be ambiguous, so normalize it.
            kwargs["sort"] = list(sort.items()) if isinstance(sort, Mapping) else sort
        try:
            names = set(inspect.signature(method).parameters)
        except (TypeError, ValueError):
            names = set()
        if names and "filter" not in names:
            for alias in ("query", "criteria", "where"):
                if alias in names:
                    kwargs[alias] = kwargs.pop("filter")
                    break
        if names and "projection" not in names and "fields" in names and "projection" in kwargs:
            kwargs["fields"] = kwargs.pop("projection")
        value = _call_method_once(method, **kwargs)
        limit = args.get("limit")
        if isinstance(limit, int):
            limiter = getattr(value, "limit", None)
            if callable(limiter):
                return limiter(limit)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return list(value[:limit])
        return value


def _bounded_values(value: Any, max_items: int) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value[:max_items]
    if isinstance(value, tuple):
        return list(value[:max_items])
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return [value]
    try:
        result: list[Any] = []
        for item in value:
            result.append(item)
            if len(result) >= max_items:
                break
        return result
    except TypeError:
        return [value]


def get_engine_adapter(
    engine: str | Mapping[str, Any],
    *,
    mode: str | None = None,
    read_only_hook: Callable[..., Any] | None = None,
    readonly_hook: Callable[..., Any] | None = None,
    hook: Callable[..., Any] | None = None,
    sql_policy: Any = None,
    read_only_policy: Any = None,
    sql_validator: Any = None,
    validator: Any = None,
    policy: Any = None,
    driver: Any = None,
    collections: Mapping[str, Any] | None = None,
    collection_resolver: Callable[[str], Any] | None = None,
    allowed_collection_ids: Sequence[str] | None = None,
) -> EngineAdapter:
    """Build an adapter from static engine metadata and injected hooks."""

    canonical = normalize_engine(engine, mode)
    if canonical in RELATIONAL_ENGINES:
        return RelationalEngineAdapter(
            canonical,
            read_only_hook or readonly_hook or hook,
            sql_policy=sql_policy,
            read_only_policy=read_only_policy,
            sql_validator=sql_validator,
            validator=validator,
            policy=policy,
            dialect=ENGINE_CAPABILITIES[canonical].dialect,
            mode=mode,
        )
    if canonical == "redis":
        return RedisEngineAdapter(driver, policy=policy)
    if canonical == "mongodb":
        return MongoEngineAdapter(
            driver,
            collections=collections,
            collection_resolver=collection_resolver,
            allowed_collection_ids=allowed_collection_ids,
            policy=policy,
        )
    raise ValueError(f"unsupported data-center engine: {engine!r}")


create_engine_adapter = get_engine_adapter
build_engine_adapter = get_engine_adapter
get_adapter = get_engine_adapter
EngineCapabilities = EngineCapability
RedisAdapter = RedisEngineAdapter
MongoAdapter = MongoEngineAdapter
RelationalAdapter = RelationalEngineAdapter


__all__ = [
    "CAPABILITIES",
    "ENGINE_CAPABILITIES",
    "ENGINE_CAPABILITY_MAP",
    "ENGINE_CAPABILITY_MATRIX",
    "ENGINE_MAP",
    "EngineCapabilities",
    "EngineAdapter",
    "EngineCapability",
    "MONGO_READ",
    "MongoEngineAdapter",
    "MongoAdapter",
    "NOSQL_ENGINES",
    "REDIS_READ",
    "RELATIONAL_ENGINES",
    "READ_ONLY_QUERY",
    "ReadOnlyHook",
    "ReadOnlyQuery",
    "ReadOnlyRequest",
    "RedisEngineAdapter",
    "RedisAdapter",
    "RelationalAdapter",
    "RelationalEngineAdapter",
    "SUPPORTED_ENGINES",
    "build_engine_adapter",
    "create_engine_adapter",
    "engine_capabilities",
    "get_engine_adapter",
    "get_adapter",
    "get_engine_capability",
    "normalize_engine",
]
