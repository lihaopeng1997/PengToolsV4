"""Closed data-center Agent tool registry.

The registry is the only DC-01 object that knows how to hand a validated call
to an injected host executor.  It never creates a database connection and it
never interprets a model supplied connection, file, shell, or credential
field.  The executor receives the fixed ``RunContext`` from the host together
with normalized tool arguments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import inspect
import threading
from typing import Any, Protocol

from .contracts import CancellationToken, RunContext, ToolCall, ToolResult
from .policy import (
    DataCenterPolicy,
    MONGO_READ_OPERATIONS,
    REDIS_READ_OPERATIONS,
    TOOL_NAMES,
)
from .result_projection import ProjectionBudget, ResultProjector


_MAX_PROJECTION_RUNS = 128


class ToolExecutor(Protocol):
    """Shape accepted by the registry's injected fake/host executor."""

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        context: RunContext,
        cancellation: CancellationToken,
    ) -> ToolResult | Mapping[str, Any] | Any:
        ...


def _function_schema(name: str, description: str, properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(properties),
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def default_tool_definitions() -> tuple[dict[str, Any], ...]:
    """Return the native-tools schema for exactly the six DC-01 tools."""

    text = {"type": "string"}
    bounded_text = {"type": "string", "maxLength": 24_000}
    id_text = {"type": "string", "minLength": 1, "maxLength": 512}
    positive_limit = {"type": "integer", "minimum": 1, "maximum": 100}
    definitions = (
        _function_schema(
            "search_objects",
            "在宿主注入的数据库对象范围内搜索元数据。",
            {
                "keyword": bounded_text,
                "kinds": {"type": "array", "items": text, "maxItems": 20},
                "page_token": id_text,
            },
        ),
        _function_schema(
            "describe_object",
            "读取宿主已授权对象的字段与索引摘要。",
            {
                "object_id": id_text,
                "field_filter": {"type": "array", "items": id_text, "maxItems": 100},
                "page_token": id_text,
            },
            required=("object_id",),
        ),
        _function_schema(
            "query_readonly",
            "提交一条待执行的查询文本；宿主在后续执行层做只读校验。",
            {
                "sql": {"type": "string", "minLength": 1, "maxLength": 24_000},
                "parameters": {"type": "object"},
                "row_limit": positive_limit,
            },
            required=("sql",),
        ),
        _function_schema(
            "redis_read",
            "执行宿主固定连接上的结构化 Redis 只读操作。",
            {
                "operation": {"type": "string", "enum": sorted(REDIS_READ_OPERATIONS)},
                "key": id_text,
                "pattern": text,
                "cursor": {"oneOf": [{"type": "integer", "minimum": 0}, text]},
                "limit": positive_limit,
            },
            required=("operation",),
        ),
        _function_schema(
            "mongo_read",
            "在宿主已授权集合上执行限定的 MongoDB 读取操作。",
            {
                "collection_id": id_text,
                "operation": {"type": "string", "enum": sorted(MONGO_READ_OPERATIONS)},
                "filter": {"type": "object"},
                "projection": {"type": "object"},
                "sort": {"type": "object"},
                "pipeline": {"type": "array", "maxItems": 20},
                "limit": positive_limit,
            },
            required=("collection_id", "operation"),
        ),
        _function_schema(
            "request_clarification",
            "暂停本轮并请求用户选择对象。",
            {
                "question": {"type": "string", "minLength": 1, "maxLength": 4_000},
                "candidate_ids": {"type": "array", "items": id_text, "maxItems": 20},
            },
            required=("question",),
        ),
    )
    return definitions


class DataCenterToolRegistry:
    """Validate and dispatch the DC-01 closed tool set."""

    def __init__(
        self,
        executor: Any,
        *,
        policy: DataCenterPolicy | None = None,
        projector: Any | None = None,
    ) -> None:
        self._executor = executor
        self._policy = policy or DataCenterPolicy()
        self._projector = projector
        # ``AgentRunner`` sends the value returned by ``execute`` straight
        # back to the model.  Keep a model-safe query projector separate from
        # the optional legacy ``execute_for_model`` projector, and share one
        # byte budget for all queries in a run.
        self._query_projector = projector or ResultProjector()
        self._projection_budgets: dict[tuple[str, str], ProjectionBudget] = {}
        self._projection_lock = threading.RLock()

    @property
    def policy(self) -> DataCenterPolicy:
        return self._policy

    def definitions(self) -> tuple[dict[str, Any], ...]:
        """Return schemas filtered to the policy's enabled closed set."""

        enabled = set(self._policy.allowed_tools)
        return tuple(item for item in default_tool_definitions() if item["function"]["name"] in enabled)

    # Friendly aliases used by model adapters and tests.
    tool_definitions = definitions
    schemas = definitions

    def validate_tool_call(self, call: ToolCall | Mapping[str, Any], context: RunContext) -> ToolResult:
        return self._policy.validate_tool_call(call, context)

    def execute(
        self,
        call: ToolCall | Mapping[str, Any],
        *,
        context: RunContext,
        cancellation: CancellationToken,
    ) -> ToolResult:
        """Validate first, then invoke only the injected executor.

        A rejected call returns before resolving or invoking an executor.  The
        caller can therefore assert zero fake database calls for every policy
        failure case.
        """

        decision = self._policy.validate_tool_call(call, context)
        if not decision.ok:
            return decision
        if _is_cancelled(cancellation):
            return _failure("CANCELLED", "本轮已停止，未启动工具")

        data = decision.data
        if not isinstance(data, Mapping):
            return _failure("ARGUMENT_INVALID", "策略未返回结构化工具参数")
        name = data.get("name")
        call_id = data.get("call_id")
        arguments = data.get("arguments")
        if not isinstance(name, str) or not isinstance(call_id, str) or not isinstance(arguments, Mapping):
            return _failure("ARGUMENT_INVALID", "策略未返回完整工具调用")

        handler = self._resolve_handler(name)
        if handler is None:
            return _failure("CAPABILITY_UNAVAILABLE", "数据中心工具执行器未配置")

        # SQL remains closed for the generic DC-01 executor boundary.  The
        # later query adapter is opened only when the resolved host handler
        # carries both explicit read-only markers.  Model arguments can never
        # set these markers because they are read from the injected object.
        if name == "query_readonly" and not _is_verified_readonly_handler(handler):
            return _failure("READ_GUARD_NOT_READY", "只读 SQL 门禁尚未就绪，查询未执行")

        try:
            value = _invoke_handler(handler, name, dict(arguments), call_id, context, cancellation)
        except TimeoutError:
            return _failure("TIMEOUT", "工具执行超时")
        except Exception:
            # Error text from a driver/fake must not be allowed to become a
            # model instruction or expose a credential.  The detailed error
            # remains the host's responsibility outside this boundary.
            return _failure("QUERY_FAILED" if name == "query_readonly" else "TOOL_FAILED", "工具执行失败")

        result = ToolResult.from_value(value)
        if result.query_id is None and name == "query_readonly" and isinstance(value, Mapping):
            query_id = value.get("query_id", value.get("queryId"))
            if query_id is not None:
                result = ToolResult(
                    ok=result.ok,
                    code=result.code,
                    data=result.data,
                    evidence_id=result.evidence_id,
                    query_id=str(query_id),
                    scope=result.scope,
                    truncated=result.truncated,
                    limits=result.limits,
                    elapsed_ms=result.elapsed_ms,
                )
        if name == "query_readonly":
            return self._project_query_result(result, context)
        return result

    def execute_for_model(
        self,
        call: ToolCall | Mapping[str, Any],
        *,
        context: RunContext,
        cancellation: CancellationToken,
        budget: Any | None = None,
        **projection_options: Any,
    ) -> Any:
        """Execute and, when configured, project the result for model input."""

        result = self.execute(call, context=context, cancellation=cancellation)
        if _call_name(call) == "query_readonly":
            # ``execute`` already returns the model-safe query view because
            # AgentRunner uses that method directly.  Do not project it a
            # second time when an older host still calls this helper.
            return result
        if self._projector is None:
            return result
        project = getattr(self._projector, "project", self._projector)
        return project(result, budget=budget, **projection_options)

    def release_run(self, context: RunContext | str, tab_id: str | None = None) -> None:
        """Release a run's shared projection budget after its terminal event."""

        key = _projection_key(context, tab_id)
        with self._projection_lock:
            self._projection_budgets.pop(key, None)

    def _project_query_result(self, result: ToolResult, context: RunContext) -> ToolResult:
        """Project one verified query result before the runner sees it."""

        project = getattr(self._query_projector, "project", self._query_projector)
        if not callable(project):
            return _failure("CAPABILITY_UNAVAILABLE", "查询结果投影器未配置")
        key = _projection_key(context)
        with self._projection_lock:
            budget = self._projection_budgets.get(key)
            if budget is None:
                if len(self._projection_budgets) >= _MAX_PROJECTION_RUNS:
                    oldest = next(iter(self._projection_budgets))
                    self._projection_budgets.pop(oldest, None)
                max_bytes = getattr(self._query_projector, "max_cumulative_bytes", None)
                if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
                    max_bytes = ProjectionBudget().max_bytes
                budget = ProjectionBudget(max_bytes=max_bytes)
                self._projection_budgets[key] = budget
            try:
                projected = project(result, budget=budget)
            except Exception:
                # Projection is part of the model boundary.  A broken host
                # projector must not make native result data escape it.
                return _failure("QUERY_FAILED", "查询结果投影失败")

        if isinstance(projected, ToolResult):
            return projected
        as_dict = getattr(projected, "as_dict", None)
        if callable(as_dict):
            projected = as_dict()
        return ToolResult.from_value(projected)

    def _resolve_handler(self, name: str) -> Callable[..., Any] | None:
        executor = self._executor
        if executor is None:
            return None
        if isinstance(executor, Mapping):
            handler = executor.get(name)
            return handler if callable(handler) else None
        # A host may inject one callable per tool, or inject the concrete
        # AgentQueryTool itself.  Prefer a named query handler when present,
        # then preserve a marked callable object so its host markers remain
        # visible to the read-only gate below.
        if name == "query_readonly":
            named_handler = getattr(executor, name, None)
            if callable(named_handler):
                return named_handler
            if callable(executor) and _is_verified_readonly_handler(executor):
                return executor
        for attribute in ("execute_tool", "execute"):
            handler = getattr(executor, attribute, None)
            if callable(handler):
                return handler
        if callable(executor):
            return executor
        return None


def _is_verified_readonly_handler(handler: Any) -> bool:
    """Return whether a handler is explicitly owned by a read-only host.

    ``AgentQueryTool`` is callable, while integrations may inject its bound
    ``execute`` method.  In the latter case the markers live on ``__self__``;
    inspect both objects without accepting truthy or model-supplied values.
    """

    candidates = (handler, getattr(handler, "__self__", None))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if getattr(candidate, "host_only", False) is True and getattr(candidate, "verified_readonly", False) is True:
                return True
        except Exception:
            # A hostile descriptor must fail closed at this boundary.
            continue
    return False


def _call_name(call: ToolCall | Mapping[str, Any]) -> str:
    if isinstance(call, ToolCall):
        return str(call.name or "")
    if isinstance(call, Mapping):
        return str(call.get("name", "") or "")
    return ""


def _projection_key(context: RunContext | str, tab_id: str | None = None) -> tuple[str, str]:
    if isinstance(context, RunContext):
        return str(context.run_id or ""), str(context.tab_id or "")
    return str(context or ""), str(tab_id or "")


def _invoke_handler(
    handler: Callable[..., Any],
    name: str,
    arguments: Mapping[str, Any],
    call_id: str,
    context: RunContext,
    cancellation: CancellationToken,
) -> Any:
    """Adapt a small set of explicit fake-executor signatures once.

    Signature inspection avoids the unsafe pattern of retrying a call after a
    ``TypeError``: a handler may already have performed an external action
    before raising that error.
    """

    call = ToolCall(call_id=call_id, name=name, arguments=arguments)
    try:
        signature = inspect.signature(handler)
        parameters = list(signature.parameters.values())
    except (TypeError, ValueError):
        return handler(name, arguments, context=context, cancellation=cancellation)

    positional = [
        parameter
        for parameter in parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    first_name = positional[0].name.casefold() if positional else ""
    if first_name in {"call", "tool_call", "request"}:
        positional_args: tuple[Any, ...] = (call,)
    elif first_name in {"name", "tool", "tool_name"}:
        positional_args = (name, arguments)
    elif first_name in {"arguments", "args", "params", "payload"}:
        positional_args = (arguments,)
    elif len(positional) >= 3:
        positional_args = (name, arguments, context)
    elif len(positional) == 2:
        positional_args = (name, arguments)
    elif len(positional) == 1:
        positional_args = (arguments,)
    else:
        positional_args = ()

    keyword_names = {parameter.name for parameter in parameters}
    keyword_args: dict[str, Any] = {}
    if "context" in keyword_names:
        keyword_args["context"] = context
    if "cancellation" in keyword_names:
        keyword_args["cancellation"] = cancellation
    elif "cancel_token" in keyword_names:
        keyword_args["cancel_token"] = cancellation
    elif "cancel" in keyword_names:
        keyword_args["cancel"] = cancellation
    return handler(*positional_args, **keyword_args)


def _is_cancelled(cancellation: Any) -> bool:
    checker = getattr(cancellation, "is_cancelled", None)
    return bool(checker()) if callable(checker) else bool(getattr(cancellation, "cancelled", False))


def _failure(code: str, message: str) -> ToolResult:
    return ToolResult(ok=False, code=code, data={"message": message}, limits={"policy_version": "dc-01"})


# Some integrations refer to the concrete class simply as ToolRegistry.  Keep
# the alias local to this module; contracts.ToolRegistry remains the Protocol.
ToolRegistry = DataCenterToolRegistry


__all__ = [
    "DataCenterToolRegistry",
    "ToolExecutor",
    "ToolRegistry",
    "default_tool_definitions",
]
