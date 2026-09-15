"""Host-owned read-only query tool for the data-center Agent.

The model supplies only query arguments.  The host supplies the selected tab
and the corresponding session, so a tool call cannot choose a connection or
session of its own.  :class:`QueryExecutor` remains the read-only boundary;
this module only binds the call to the current session and adapts its result to
the Agent's ``ToolResult`` contract.
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any, Callable

from .contracts import QueryRequest, QueryResult, RunContext, ToolCall, ToolResult
from .session_manager import SessionManager


_QUERY_ARGUMENTS = frozenset({"sql", "parameters", "row_limit"})
_CANCELLATION_NAMES = frozenset({"cancellation", "cancel_token", "cancel", "token"})
_MAX_ROW_LIMIT = 100


class AgentQueryTool:
    """Callable host adapter for one verified read-only Agent query tool.

    ``verified_readonly`` is deliberately an explicit marker.  Hosts can use
    it when constructing a registry to distinguish this adapter from generic
    callables.  The adapter never accepts a session, connection, driver, or
    policy decision from model arguments; those are fixed by the host-owned
    :class:`SessionManager` and :class:`QueryExecutor`.
    """

    name = "query_readonly"
    host_only = True
    verified_readonly = True

    def __init__(self, session_manager: SessionManager, query_executor: Any) -> None:
        if session_manager is None or not callable(getattr(session_manager, "get_session", None)):
            raise TypeError("session_manager must provide get_session")
        if query_executor is None:
            raise TypeError("query_executor is required")
        self.session_manager = session_manager
        self.query_executor = query_executor

    def __call__(
        self,
        call: ToolCall | Mapping[str, Any],
        *,
        context: RunContext,
        cancellation: Any = None,
    ) -> ToolResult:
        """Execute one model call against the session bound to ``context.tab_id``."""

        call_id, name, arguments = _normalize_call(call)
        if not call_id:
            return _failure("ARGUMENT_INVALID", "工具调用缺少 call_id")
        if name and name.casefold() != self.name.casefold():
            return _failure("ARGUMENT_INVALID", "工具能力不一致", query_id=call_id)
        if not isinstance(context, RunContext):
            return _failure("ARGUMENT_INVALID", "工具缺少宿主运行上下文", query_id=call_id)

        unknown = set(arguments) - _QUERY_ARGUMENTS
        if unknown:
            return _failure("ARGUMENT_INVALID", "工具参数不在固定结构中", query_id=call_id)
        sql = arguments.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return _failure("ARGUMENT_INVALID", "query_readonly 需要非空 SQL", query_id=call_id)
        parameters = arguments.get("parameters", {})
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            return _failure("ARGUMENT_INVALID", "parameters 必须是对象", query_id=call_id)

        row_limit = arguments.get("row_limit", 100)
        if (
            isinstance(row_limit, bool)
            or not isinstance(row_limit, int)
            or not 1 <= row_limit <= _MAX_ROW_LIMIT
        ):
            return _failure("ARGUMENT_INVALID", "row_limit 无效", query_id=call_id)

        tab_id = str(context.tab_id or "").strip()
        if not tab_id:
            return _failure("SESSION_NOT_FOUND", "当前标签没有可用查询会话", query_id=call_id)
        session = self.session_manager.get_session(tab_id)
        if session is None:
            return _failure("SESSION_NOT_FOUND", "当前标签没有可用查询会话", query_id=call_id)
        if str(context.connection_id or "").strip() != session.connection_id:
            return _failure("SCOPE_DENIED", "当前标签会话与运行目标不一致", query_id=call_id)

        request = QueryRequest(
            session_id=session.session_id,
            query_id=call_id,
            generation=session.generation,
            sql=sql.strip(),
            parameters=parameters,
            limits={"row_limit": row_limit},
        )
        try:
            raw_result = _invoke_executor(self.query_executor, request, cancellation)
        except TimeoutError:
            return _failure("TIMEOUT", "查询超时", query_id=call_id)
        except Exception:
            # Driver and executor details stay on the host side.
            return _failure("QUERY_FAILED", "只读查询失败", query_id=call_id)
        return _as_tool_result(raw_result, call_id)

    execute = __call__
    run = __call__


def _normalize_call(call: ToolCall | Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any]]:
    if isinstance(call, ToolCall):
        return str(call.call_id or "").strip(), str(call.name or "").strip(), call.arguments
    if not isinstance(call, Mapping):
        return "", "", {}
    call_id = call.get("call_id", call.get("id", ""))
    name = call.get("name", "query_readonly")
    arguments = call.get("arguments", call.get("args", {}))
    if not isinstance(arguments, Mapping):
        return str(call_id or "").strip(), str(name or "").strip(), {}
    return str(call_id or "").strip(), str(name or "").strip(), arguments


def _invoke_executor(executor: Any, request: QueryRequest, cancellation: Any) -> Any:
    method: Callable[..., Any] | None = None
    for name in ("execute", "run", "query", "execute_query"):
        candidate = getattr(executor, name, None)
        if callable(candidate):
            method = candidate
            break
    if method is None and callable(executor):
        method = executor
    if method is None:
        raise TypeError("query_executor has no callable execution method")

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(request, cancellation=cancellation)
    parameters = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters):
        return method(request, cancellation=cancellation)
    keyword_only = {item.name for item in parameters if item.kind == inspect.Parameter.KEYWORD_ONLY}
    positional = [
        item
        for item in parameters
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    args: list[Any] = []
    if positional:
        args.append(request)
    elif not keyword_only and not parameters:
        return method(request)
    # Several host executors expose compatibility aliases for the same
    # cancellation token.  Select one deterministic keyword; passing more
    # than one alias can be rejected by executors such as QueryExecutor.
    preferred_cancellation = next(
        (
            name
            for name in ("cancellation", "cancel_token", "cancel", "token")
            if any(item.name == name and item.kind != inspect.Parameter.POSITIONAL_ONLY for item in parameters)
        ),
        None,
    )
    kwargs: dict[str, Any] = {}
    if preferred_cancellation is not None:
        kwargs[preferred_cancellation] = cancellation
    return method(*args, **kwargs)


def _as_tool_result(value: Any, query_id: str) -> ToolResult:
    if isinstance(value, ToolResult):
        if value.query_id:
            return value
        return ToolResult(
            ok=value.ok,
            code=value.code,
            data=value.data,
            evidence_id=value.evidence_id,
            query_id=query_id,
            scope=value.scope,
            truncated=value.truncated,
            limits=value.limits,
            elapsed_ms=value.elapsed_ms,
        )
    if isinstance(value, QueryResult):
        data = {
            "columns": tuple(dict(column) for column in value.columns),
            "rows": tuple(tuple(row) for row in value.rows),
            "row_count": len(value.rows),
        }
        return ToolResult(
            ok=value.ok,
            code=value.code,
            data=data if value.ok else None,
            evidence_id=value.evidence_id,
            query_id=value.query_id or query_id,
            truncated=value.truncated,
            elapsed_ms=value.elapsed_ms,
        )
    result = ToolResult.from_value(value)
    if result.query_id:
        return result
    return ToolResult(
        ok=result.ok,
        code=result.code,
        data=result.data,
        evidence_id=result.evidence_id,
        query_id=query_id,
        scope=result.scope,
        truncated=result.truncated,
        limits=result.limits,
        elapsed_ms=result.elapsed_ms,
    )


def _failure(code: str, message: str, *, query_id: str | None = None) -> ToolResult:
    return ToolResult(ok=False, code=code, data={"message": message}, query_id=query_id)


__all__ = ["AgentQueryTool"]
