"""Closed-set policy for the first data-center Agent slice.

DC-01 deliberately keeps this module small.  It validates the tool name and
the JSON-shaped arguments that came from the model, then returns a normalized
``ToolResult``.  It does *not* execute anything and it does not claim to be a
SQL parser or a database read-only guard.  Those checks belong to the later
execution slice.

The host supplies the ``RunContext``.  A model call can refer to the tools in
the current context, but it cannot replace that context with a connection,
host, file, shell, or Python value of its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
from typing import Any

from .contracts import RunContext, ToolCall, ToolResult


POLICY_VERSION = "dc-01"

TOOL_NAMES: tuple[str, ...] = (
    "search_objects",
    "describe_object",
    "query_readonly",
    "redis_read",
    "mongo_read",
    "request_clarification",
)

# These are intentionally exact top-level names.  In particular, a parameter
# named ``command`` is not accepted by redis_read and a parameter named
# ``connection_id`` is not accepted by any tool.
TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    "search_objects": frozenset({"keyword", "kinds", "page_token"}),
    "describe_object": frozenset({"object_id", "field_filter", "page_token"}),
    "query_readonly": frozenset({"sql", "parameters", "row_limit"}),
    "redis_read": frozenset({"operation", "key", "pattern", "cursor", "limit"}),
    "mongo_read": frozenset(
        {"collection_id", "operation", "filter", "projection", "sort", "pipeline", "limit"}
    ),
    "request_clarification": frozenset({"question", "candidate_ids"}),
}

REDIS_READ_OPERATIONS: frozenset[str] = frozenset(
    {
        "SCAN",
        "TYPE",
        "TTL",
        "PTTL",
        "GET",
        "STRLEN",
        "HGET",
        "HSCAN",
        "LLEN",
        "LRANGE",
        "SCARD",
        "SSCAN",
        "ZCARD",
        "ZRANGE",
        "ZSCAN",
        "XLEN",
        "XRANGE",
    }
)
MONGO_READ_OPERATIONS: frozenset[str] = frozenset({"find", "count", "aggregate"})
MONGO_PIPELINE_STAGES: frozenset[str] = frozenset(
    {"$match", "$project", "$group", "$sort", "$limit", "$skip", "$unwind", "$count", "$addfields", "$set", "$unset"}
)
MONGO_FORBIDDEN_OPERATORS: frozenset[str] = frozenset(
    {
        "$out",
        "$merge",
        "$where",
        "$function",
        "$accumulator",
        "$lookup",
        "$unionwith",
        "$graphlookup",
        "$facet",
    }
)
MONGO_SAFE_OPERATORS: frozenset[str] = frozenset(
    {
        # Query predicates and expression operators used by the read-only
        # subset.  Unknown dollar-prefixed operators fail closed below.
        "$and",
        "$or",
        "$nor",
        "$not",
        "$eq",
        "$ne",
        "$gt",
        "$gte",
        "$lt",
        "$lte",
        "$in",
        "$nin",
        "$exists",
        "$regex",
        "$options",
        "$elemMatch",
        "$size",
        "$all",
        "$type",
        "$expr",
        "$text",
        "$mod",
        "$bitsAllSet",
        "$bitsAnySet",
        "$bitsAllClear",
        "$bitsAnyClear",
        "$literal",
        "$cond",
        "$ifNull",
        "$ifNull",
        "$concat",
        "$toString",
        "$toInt",
        "$toLong",
        "$toDecimal",
        "$toDate",
        "$add",
        "$subtract",
        "$multiply",
        "$divide",
        "$sum",
        "$avg",
        "$min",
        "$max",
        "$first",
        "$last",
        "$push",
        "$addToSet",
        "$arrayElemAt",
        "$dateToString",
        "$substr",
        "$substrBytes",
        "$substrCP",
        "$trim",
        "$ltrim",
        "$rtrim",
        "$toLower",
        "$toUpper",
        "$setField",
        "$getField",
        "$map",
        "$filter",
        "$reduce",
        # Pipeline stage names are checked separately, but are included so a
        # recursive walk over a complete stage object remains closed.
        *MONGO_PIPELINE_STAGES,
    }
)

_MISSING = object()
_MAX_ID_CHARS = 512
_MAX_TEXT_CHARS = 24_000
_MAX_LIST_ITEMS = 100
_MAX_JSON_DEPTH = 8

# A model must never be able to smuggle an execution target through a tool's
# argument object.  Unknown names are rejected too, but these names get a
# scope-specific code so the UI can explain the reason without echoing input.
HOST_OVERRIDE_ARGUMENTS: frozenset[str] = frozenset(
    {
        "host",
        "hostname",
        "port",
        "username",
        "user",
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
        "schema_name",
        "dsn",
        "uri",
        "ssh",
        "ssh_host",
        "ssh_port",
        "file",
        "file_path",
        "filepath",
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


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Optional richer view of a validation result.

    ``validate_tool_call`` returns the standard ``ToolResult`` because that is
    the runner boundary.  This helper keeps an explicit ``allowed`` flag for
    callers that want to branch without interpreting an error code.
    """

    allowed: bool
    result: ToolResult
    tool_name: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def code(self) -> str:
        return self.result.code

    def as_tool_result(self) -> ToolResult:
        return self.result


class DataCenterPolicy:
    """Validate model-produced calls against the DC-01 closed tool set."""

    version = POLICY_VERSION

    def __init__(
        self,
        *,
        allowed_tools: Sequence[str] = TOOL_NAMES,
        allowed_object_ids: Sequence[str] | None = None,
        allowed_collection_ids: Sequence[str] | None = None,
    ) -> None:
        requested = tuple(str(name) for name in allowed_tools)
        unknown = set(requested).difference(TOOL_NAMES)
        if unknown:
            raise ValueError("allowed_tools contains an unknown data-center tool")
        self._allowed_tools = frozenset(requested)
        self._allowed_object_ids = _normalized_id_set(allowed_object_ids)
        self._allowed_collection_ids = _normalized_id_set(allowed_collection_ids)

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(name for name in TOOL_NAMES if name in self._allowed_tools)

    def validate_tool_call(self, call: ToolCall | Mapping[str, Any], context: RunContext) -> ToolResult:
        """Return ``ok=True`` with normalized arguments when a call is allowed.

        This method only validates.  No injected executor is reachable from
        this class, which makes a rejected call observable with zero I/O.
        ``query_readonly`` checks only that ``sql`` is a non-empty string in
        DC-01; SQL AST and engine enforcement are explicitly deferred.
        """

        call_id, name, arguments, call_error = _unpack_call(call)
        if call_error is not None:
            return _failure("ARGUMENT_INVALID", call_error)

        if name not in self._allowed_tools:
            return _failure("POLICY_DENIED", "该工具不在数据中心只读工具白名单中")

        scope, scope_error = _host_scope(context)
        if scope_error is not None:
            return _failure("SCOPE_DENIED", scope_error)

        if not isinstance(arguments, Mapping):
            return _failure("ARGUMENT_INVALID", "工具参数必须是对象")
        args = dict(arguments)

        override_keys = {str(key).casefold() for key in args}.intersection(HOST_OVERRIDE_ARGUMENTS)
        if override_keys:
            return _failure("SCOPE_DENIED", "工具不能覆盖宿主连接、文件或命令目标")

        unexpected = set(args).difference(TOOL_ARGUMENTS[name])
        if unexpected:
            return _failure("ARGUMENT_INVALID", "工具参数不在该工具的固定参数集合中")

        try:
            normalized = self._validate_arguments(name, args)
        except _ValidationError as exc:
            return _failure(exc.code, exc.message)

        # call_id is carried only as protocol metadata.  It is never treated
        # as an execution target and is not allowed to alter the host scope.
        data = {
            "call_id": call_id,
            "name": name,
            "arguments": normalized,
        }
        return ToolResult(
            ok=True,
            code="OK",
            data=data,
            scope=scope,
            limits={"policy_version": self.version},
        )

    def decide(self, call: ToolCall | Mapping[str, Any], context: RunContext) -> PolicyDecision:
        """Validate a call and expose a direct boolean for small adapters."""

        result = self.validate_tool_call(call, context)
        name = _call_name(call)
        arguments = result.data.get("arguments", {}) if result.ok and isinstance(result.data, Mapping) else {}
        return PolicyDecision(result.ok, result, name if isinstance(name, str) else None, arguments)

    def _validate_arguments(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_objects":
            return _validate_search_objects(args)
        if name == "describe_object":
            return _validate_describe_object(args, self._allowed_object_ids)
        if name == "query_readonly":
            return _validate_query_readonly(args)
        if name == "redis_read":
            return _validate_redis_read(args)
        if name == "mongo_read":
            return _validate_mongo_read(args, self._allowed_collection_ids)
        if name == "request_clarification":
            return _validate_request_clarification(args, self._allowed_object_ids)
        # The caller checks the closed set before reaching this branch.  Keep a
        # fail-closed guard in case a future edit adds a name without a schema.
        raise _ValidationError("POLICY_DENIED", "该工具没有已登记的参数策略")


def validate_tool_call(call: ToolCall | Mapping[str, Any], context: RunContext) -> ToolResult:
    """Module-level convenience entry point for the default DC-01 policy."""

    return DataCenterPolicy().validate_tool_call(call, context)


def decide_tool_call(call: ToolCall | Mapping[str, Any], context: RunContext) -> PolicyDecision:
    """Module-level validation with an explicit ``allowed`` property."""

    return DataCenterPolicy().decide(call, context)


def _validate_search_objects(args: dict[str, Any]) -> dict[str, Any]:
    keyword = _optional_string(args, "keyword", max_chars=_MAX_TEXT_CHARS)
    kinds = args.get("kinds", _MISSING)
    if kinds is not _MISSING:
        kinds = _string_list(kinds, "kinds", max_items=20, max_chars=64)
    page_token = _optional_string(args, "page_token", max_chars=_MAX_ID_CHARS)
    return _compact({"keyword": keyword, "kinds": kinds, "page_token": page_token})


def _validate_describe_object(args: dict[str, Any], allowed_ids: frozenset[str]) -> dict[str, Any]:
    object_id = _required_id(args, "object_id")
    if allowed_ids and object_id.casefold() not in allowed_ids:
        raise _ValidationError("SCOPE_DENIED", "对象不在宿主注入的授权范围内")
    field_filter = args.get("field_filter", _MISSING)
    if field_filter is not _MISSING:
        field_filter = _string_list(field_filter, "field_filter", max_items=100, max_chars=256)
    page_token = _optional_string(args, "page_token", max_chars=_MAX_ID_CHARS)
    return _compact({"object_id": object_id, "field_filter": field_filter, "page_token": page_token})


def _validate_query_readonly(args: dict[str, Any]) -> dict[str, Any]:
    sql = args.get("sql", _MISSING)
    if not isinstance(sql, str) or not sql.strip():
        raise _ValidationError("ARGUMENT_INVALID", "query_readonly 需要非空 SQL 字符串")
    if len(sql) > _MAX_TEXT_CHARS:
        raise _ValidationError("ARGUMENT_INVALID", "SQL 文本超出本轮输入上限")

    parameters = args.get("parameters", _MISSING)
    if parameters is not _MISSING:
        if not isinstance(parameters, Mapping):
            raise _ValidationError("ARGUMENT_INVALID", "parameters 必须是对象")
        parameters = _json_value(parameters, "parameters")
        if not isinstance(parameters, Mapping):  # defensive; _json_value preserves mappings
            raise _ValidationError("ARGUMENT_INVALID", "parameters 必须是对象")
        parameters = dict(parameters)

    row_limit = args.get("row_limit", _MISSING)
    if row_limit is not _MISSING:
        if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= 100:
            raise _ValidationError("ARGUMENT_INVALID", "row_limit 必须在 1 到 100 之间")

    # No SQL keyword inspection is intentional here.  DC-01 does not contain
    # a real SQL AST or a database executor; DC-02 owns that enforcement.
    return _compact({"sql": sql, "parameters": parameters, "row_limit": row_limit})


def _validate_redis_read(args: dict[str, Any]) -> dict[str, Any]:
    operation = args.get("operation", _MISSING)
    if not isinstance(operation, str) or not operation.strip():
        raise _ValidationError("ARGUMENT_INVALID", "redis_read 需要 operation")
    operation = operation.strip().upper()
    if operation not in REDIS_READ_OPERATIONS:
        raise _ValidationError("POLICY_DENIED", "Redis operation 不在只读白名单中")

    key = _optional_string(args, "key", max_chars=_MAX_ID_CHARS)
    pattern = _optional_string(args, "pattern", max_chars=_MAX_ID_CHARS)
    cursor = args.get("cursor", _MISSING)
    if cursor is not _MISSING:
        if isinstance(cursor, bool) or not isinstance(cursor, (int, str)):
            raise _ValidationError("ARGUMENT_INVALID", "cursor 必须是 Redis 游标")
        if isinstance(cursor, int) and cursor < 0:
            raise _ValidationError("ARGUMENT_INVALID", "cursor 不能为负数")
        if isinstance(cursor, str) and (not cursor.strip() or len(cursor) > 128):
            raise _ValidationError("ARGUMENT_INVALID", "cursor 格式无效")
    limit = args.get("limit", _MISSING)
    if limit is not _MISSING:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise _ValidationError("ARGUMENT_INVALID", "limit 必须在 1 到 100 之间")
    return _compact({"operation": operation, "key": key, "pattern": pattern, "cursor": cursor, "limit": limit})


def _validate_mongo_read(args: dict[str, Any], allowed_ids: frozenset[str]) -> dict[str, Any]:
    collection_id = _required_id(args, "collection_id")
    if allowed_ids and collection_id.casefold() not in allowed_ids:
        raise _ValidationError("SCOPE_DENIED", "集合不在宿主注入的授权范围内")
    operation = args.get("operation", _MISSING)
    if not isinstance(operation, str) or operation.strip().casefold() not in MONGO_READ_OPERATIONS:
        raise _ValidationError("ARGUMENT_INVALID", "mongo_read operation 无效")
    operation = operation.strip().casefold()

    result: dict[str, Any] = {"collection_id": collection_id, "operation": operation}
    for key in ("filter", "projection", "sort"):
        value = args.get(key, _MISSING)
        if value is not _MISSING:
            if not isinstance(value, Mapping):
                raise _ValidationError("ARGUMENT_INVALID", f"{key} 必须是对象")
            result[key] = _json_value(value, key)
            _reject_mongo_forbidden(result[key], key)
    pipeline = args.get("pipeline", _MISSING)
    if pipeline is not _MISSING:
        if not isinstance(pipeline, Sequence) or isinstance(pipeline, (str, bytes, bytearray)):
            raise _ValidationError("ARGUMENT_INVALID", "pipeline 必须是数组")
        if operation != "aggregate":
            raise _ValidationError("ARGUMENT_INVALID", "只有 aggregate 支持 pipeline")
        normalized_pipeline = _json_value(list(pipeline), "pipeline")
        if not isinstance(normalized_pipeline, list) or len(normalized_pipeline) > 20:
            raise _ValidationError("ARGUMENT_INVALID", "pipeline 项数超出上限")
        for index, stage in enumerate(normalized_pipeline):
            if not isinstance(stage, Mapping) or len(stage) != 1:
                raise _ValidationError("ARGUMENT_INVALID", f"pipeline[{index}] 必须是单阶段对象")
            stage_name = next(iter(stage)).casefold()
            if stage_name not in MONGO_PIPELINE_STAGES:
                raise _ValidationError("POLICY_DENIED", "Mongo 聚合阶段不在只读白名单中")
            _reject_mongo_forbidden(stage, f"pipeline[{index}]")
        result["pipeline"] = normalized_pipeline
    limit = args.get("limit", _MISSING)
    if limit is not _MISSING:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise _ValidationError("ARGUMENT_INVALID", "limit 必须在 1 到 100 之间")
        result["limit"] = limit
    return result


def _reject_mongo_forbidden(value: Any, path: str, *, depth: int = 0) -> None:
    """Reject write/script and unknown Mongo operators at every nesting level."""

    if depth > _MAX_JSON_DEPTH:
        raise _ValidationError("ARGUMENT_INVALID", f"{path} 嵌套过深")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.startswith("$"):
                lowered = key.casefold()
                if lowered in MONGO_FORBIDDEN_OPERATORS:
                    raise _ValidationError("POLICY_DENIED", "Mongo 参数包含被禁止的写入或脚本操作")
                if lowered not in {operator.casefold() for operator in MONGO_SAFE_OPERATORS}:
                    raise _ValidationError("POLICY_DENIED", "Mongo 参数包含未登记的表达式")
            _reject_mongo_forbidden(item, f"{path}.{key}", depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_mongo_forbidden(item, f"{path}[{index}]", depth=depth + 1)


def _validate_request_clarification(args: dict[str, Any], allowed_ids: frozenset[str]) -> dict[str, Any]:
    question = args.get("question", _MISSING)
    if not isinstance(question, str) or not question.strip() or len(question) > 4_000:
        raise _ValidationError("ARGUMENT_INVALID", "question 需要非空文本")
    candidate_ids = args.get("candidate_ids", _MISSING)
    if candidate_ids is not _MISSING:
        candidate_ids = _string_list(candidate_ids, "candidate_ids", max_items=20, max_chars=_MAX_ID_CHARS)
        if allowed_ids and any(item.casefold() not in allowed_ids for item in candidate_ids):
            raise _ValidationError("SCOPE_DENIED", "候选对象不在宿主注入的授权范围内")
    return _compact({"question": question, "candidate_ids": candidate_ids})


def _unpack_call(call: ToolCall | Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any], str | None]:
    if isinstance(call, ToolCall):
        call_id, name, arguments = call.call_id, call.name, call.arguments
    elif isinstance(call, Mapping):
        call_id = call.get("call_id", call.get("id", ""))
        name = call.get("name", "")
        arguments = call.get("arguments", {})
    else:
        return "", "", {}, "工具调用必须是结构化对象"
    if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > _MAX_ID_CHARS:
        return "", "", {}, "工具调用缺少有效 call_id"
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        return "", "", {}, "工具调用缺少有效工具名"
    if not isinstance(arguments, Mapping):
        return call_id, name, {}, "工具参数必须是对象"
    return call_id, name, arguments, None


def _call_name(call: ToolCall | Mapping[str, Any]) -> Any:
    if isinstance(call, ToolCall):
        return call.name
    if isinstance(call, Mapping):
        return call.get("name")
    return None


def _host_scope(context: RunContext) -> tuple[dict[str, Any], str | None]:
    if context is None:
        return {}, "缺少宿主注入的运行上下文"
    connection_id = getattr(context, "connection_id", None)
    if not isinstance(connection_id, str) or not connection_id.strip():
        return {}, "宿主运行上下文没有固定连接目标"
    database = getattr(context, "database", "")
    if database is None:
        database = ""
    if not isinstance(database, str):
        return {}, "宿主运行上下文目标无效"
    schemas = getattr(context, "schema_allowlist", ()) or ()
    if isinstance(schemas, (str, bytes, bytearray)):
        return {}, "宿主运行上下文 schema 范围无效"
    try:
        schema_allowlist = tuple(str(item) for item in schemas)
    except TypeError:
        return {}, "宿主运行上下文 schema 范围无效"
    return {
        "connection_id": connection_id,
        "database": database,
        "schema_allowlist": schema_allowlist,
        "profile_revision": str(getattr(context, "profile_revision", "") or ""),
    }, None


def _required_id(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name, _MISSING)
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_ID_CHARS:
        raise _ValidationError("ARGUMENT_INVALID", f"{name} 需要非空标识符")
    if any(ord(char) < 32 for char in value):
        raise _ValidationError("ARGUMENT_INVALID", f"{name} 包含无效字符")
    return value.strip()


def _optional_string(args: Mapping[str, Any], name: str, *, max_chars: int) -> str | None:
    value = args.get(name, _MISSING)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, str) or len(value) > max_chars:
        raise _ValidationError("ARGUMENT_INVALID", f"{name} 必须是受限文本")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise _ValidationError("ARGUMENT_INVALID", f"{name} 包含无效字符")
    return value


def _string_list(value: Any, name: str, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _ValidationError("ARGUMENT_INVALID", f"{name} 必须是字符串数组")
    if len(value) > max_items:
        raise _ValidationError("ARGUMENT_INVALID", f"{name} 项数超出上限")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > max_chars:
            raise _ValidationError("ARGUMENT_INVALID", f"{name} 包含无效标识符")
        result.append(item.strip())
    return result


def _json_value(value: Any, path: str, *, depth: int = 0) -> Any:
    """Copy a bounded JSON-shaped value without evaluating it."""

    if depth > _MAX_JSON_DEPTH:
        raise _ValidationError("ARGUMENT_INVALID", f"{path} 嵌套过深")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > _MAX_TEXT_CHARS:
            raise _ValidationError("ARGUMENT_INVALID", f"{path} 文本过长")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ValidationError("ARGUMENT_INVALID", f"{path} 数值无效")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        if len(value) > _MAX_LIST_ITEMS:
            raise _ValidationError("ARGUMENT_INVALID", f"{path} 字段过多")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise _ValidationError("ARGUMENT_INVALID", f"{path} 字段名无效")
            result[key] = _json_value(item, f"{path}.{key}", depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_LIST_ITEMS:
            raise _ValidationError("ARGUMENT_INVALID", f"{path} 项数超出上限")
        return [_json_value(item, f"{path}[]", depth=depth + 1) for item in value]
    raise _ValidationError("ARGUMENT_INVALID", f"{path} 不是 JSON 值")


def _normalized_id_set(values: Sequence[str] | None) -> frozenset[str]:
    if values is None:
        return frozenset()
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("authorized IDs must be non-empty strings")
        result.add(value.strip().casefold())
    return frozenset(result)


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not _MISSING and value is not None}


def _failure(code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        code=code,
        data={"message": message},
        limits={"policy_version": POLICY_VERSION},
    )


@dataclass(frozen=True, slots=True)
class _ValidationError(Exception):
    code: str
    message: str


__all__ = [
    "DataCenterPolicy",
    "HOST_OVERRIDE_ARGUMENTS",
    "MONGO_READ_OPERATIONS",
    "MONGO_PIPELINE_STAGES",
    "MONGO_FORBIDDEN_OPERATORS",
    "MONGO_SAFE_OPERATORS",
    "POLICY_VERSION",
    "PolicyDecision",
    "REDIS_READ_OPERATIONS",
    "TOOL_ARGUMENTS",
    "TOOL_NAMES",
    "decide_tool_call",
    "validate_tool_call",
]
