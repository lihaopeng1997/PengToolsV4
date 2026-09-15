"""Structured read-only policy for the data-center NoSQL adapters.

The Agent never receives a Redis command string or a MongoDB expression to
evaluate.  This module accepts JSON-shaped arguments, copies them into a
bounded structure, and returns a normalized request only when the operation
belongs to the small read-only subset approved for DC-02.  It deliberately
does not import a database driver and does not perform I/O.

MongoDB validation walks every nested mapping and sequence.  In particular,
``$out``, ``$merge``, JavaScript operators, collection-expanding stages and
unknown dollar expressions are rejected even when they are hidden below a
``$match`` or another otherwise valid stage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
from typing import Any

from .contracts import ToolResult


POLICY_VERSION = "dc-02"

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

# Stage names are kept separate from expression names.  A stage is accepted
# only as the single key of an aggregate pipeline item; seeing the same name
# nested inside a document does not grant it expression semantics.
MONGO_PIPELINE_STAGES: frozenset[str] = frozenset(
    {
        "$match",
        "$project",
        "$group",
        "$sort",
        "$limit",
        "$skip",
        "$unwind",
        "$count",
        "$addfields",
        "$set",
        "$unset",
    }
)

# These are blocked independently from the unknown-expression check so the
# caller can report a stable policy denial for an obviously dangerous request.
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
        "$collstats",
        "$indexstats",
        "$listlocalcursorspr",
        "$listsearchindexes",
        "$planCacheStats",
        "$search",
        "$searchmeta",
        "$vectorSearch",
    }
)

# Explicitly registered query and projection operators.  This is intentionally
# a closed set: an unfamiliar dollar expression is not treated as harmless
# simply because the surrounding command is a read operation.
MONGO_SAFE_EXPRESSIONS: frozenset[str] = frozenset(
    {
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
        "$switch",
        "$ifNull",
        "$isArray",
        "$concat",
        "$toString",
        "$toInt",
        "$toLong",
        "$toDouble",
        "$toDecimal",
        "$toDate",
        "$add",
        "$subtract",
        "$multiply",
        "$divide",
        "$round",
        "$trunc",
        "$sum",
        "$avg",
        "$min",
        "$max",
        "$stdDevPop",
        "$stdDevSamp",
        "$first",
        "$last",
        "$push",
        "$addToSet",
        "$arrayElemAt",
        "$arrayToObject",
        "$objectToArray",
        "$dateToString",
        "$dateFromString",
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
        "$mergeObjects",
        "$range",
        "$reverseArray",
        "$zip",
    }
)

_MISSING = object()
_MAX_TEXT_CHARS = 24_000
_MAX_KEY_CHARS = 512
_MAX_ITEMS = 100
_MAX_PIPELINE_STAGES = 20
_MAX_DEPTH = 12
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

_REDIS_ARGUMENTS = frozenset({"operation", "key", "pattern", "cursor", "limit"})
_MONGO_ARGUMENTS = frozenset(
    {"collection_id", "operation", "filter", "projection", "sort", "pipeline", "limit"}
)
_HOST_ARGUMENTS = frozenset(
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


class NoSQLPolicyError(ValueError):
    """A stable policy error that is safe to expose to the tool boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True, slots=True)
class NoSQLDecision:
    """Normalized request or a fail-closed policy result."""

    allowed: bool
    code: str = "OK"
    arguments: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _copy_json(self.arguments, "arguments") if self.arguments else {})

    @property
    def ok(self) -> bool:
        return self.allowed

    @property
    def data(self) -> Mapping[str, Any]:
        return self.arguments

    def as_tool_result(self) -> ToolResult:
        return ToolResult(
            ok=self.allowed,
            code=self.code,
            data=dict(self.arguments) if self.allowed else {"message": self.message},
            limits={"policy_version": POLICY_VERSION},
        )

    # ``ToolResult``-like convenience used by small adapters and tests.
    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.allowed,
            "code": self.code,
            "data": dict(self.arguments) if self.allowed else {"message": self.message},
            "limits": {"policy_version": POLICY_VERSION},
        }


class RedisReadPolicy:
    """Validate one structured Redis read request."""

    version = POLICY_VERSION

    def __init__(self, *, max_limit: int = _MAX_LIMIT, default_limit: int = _DEFAULT_LIMIT) -> None:
        if not 1 <= int(default_limit) <= int(max_limit) <= 10_000:
            raise ValueError("invalid Redis limits")
        self.max_limit = int(max_limit)
        self.default_limit = int(default_limit)

    def validate(self, request: Mapping[str, Any] | None = None, **kwargs: Any) -> NoSQLDecision:
        return validate_redis_request(
            request,
            max_limit=self.max_limit,
            default_limit=self.default_limit,
            **kwargs,
        )

    __call__ = validate


class MongoReadPolicy:
    """Validate find/count/aggregate against an injected collection scope."""

    version = POLICY_VERSION

    def __init__(self, allowed_collection_ids: Sequence[str] | None = None) -> None:
        self.allowed_collection_ids = _normalize_ids(allowed_collection_ids)

    def validate(self, request: Mapping[str, Any] | None = None, **kwargs: Any) -> NoSQLDecision:
        return validate_mongo_request(
            request,
            allowed_collection_ids=self.allowed_collection_ids,
            **kwargs,
        )

    __call__ = validate


class NoSQLPolicy:
    """Dispatch Redis and Mongo validation without ever touching a driver."""

    version = POLICY_VERSION

    def __init__(self, *, allowed_collection_ids: Sequence[str] | None = None) -> None:
        self.redis = RedisReadPolicy()
        self.mongo = MongoReadPolicy(allowed_collection_ids)

    def validate_redis(self, request: Mapping[str, Any] | None = None, **kwargs: Any) -> NoSQLDecision:
        return self.redis.validate(request, **kwargs)

    def validate_mongo(self, request: Mapping[str, Any] | None = None, **kwargs: Any) -> NoSQLDecision:
        return self.mongo.validate(request, **kwargs)

    def validate(self, tool_name: str, request: Mapping[str, Any] | None = None) -> NoSQLDecision:
        name = str(tool_name or "").strip().casefold()
        if name in {"redis", "redis_read", "redis-read"}:
            return self.validate_redis(request)
        if name in {"mongo", "mongodb", "mongo_read", "mongo-read"}:
            return self.validate_mongo(request)
        return _failure("CAPABILITY_UNAVAILABLE", "NoSQL 工具能力未登记")


def validate_redis_request(
    request: Mapping[str, Any] | None = None,
    *,
    operation: str | None = None,
    key: str | None = None,
    pattern: str | None = None,
    cursor: int | str | None = None,
    limit: int | None = None,
    max_limit: int = _MAX_LIMIT,
    default_limit: int | None = _DEFAULT_LIMIT,
) -> NoSQLDecision:
    """Return a normalized structured Redis read request.

    ``request`` is the model payload.  Keyword arguments are provided for
    adapters that already split the structured fields.  A command string,
    credentials, host, or any unknown field is rejected before a handler can
    be resolved.
    """

    try:
        args = _merge_request(request, {"operation": operation, "key": key, "pattern": pattern, "cursor": cursor, "limit": limit})
        _reject_unknown_keys(args, _REDIS_ARGUMENTS)
        raw_operation = args.get("operation", _MISSING)
        if not isinstance(raw_operation, str) or not raw_operation.strip():
            raise NoSQLPolicyError("ARGUMENT_INVALID", "Redis operation 不能为空")
        normalized_operation = raw_operation.strip().upper()
        if normalized_operation not in REDIS_READ_OPERATIONS:
            raise NoSQLPolicyError("POLICY_DENIED", "Redis operation 不在只读白名单中")

        normalized: dict[str, Any] = {"operation": normalized_operation}
        raw_key = args.get("key", _MISSING)
        if raw_key is not _MISSING and raw_key is not None:
            normalized["key"] = _bounded_string(raw_key, "key", _MAX_KEY_CHARS, required=True)
        raw_pattern = args.get("pattern", _MISSING)
        if raw_pattern is not _MISSING and raw_pattern is not None:
            normalized["pattern"] = _bounded_string(raw_pattern, "pattern", _MAX_KEY_CHARS)

        raw_cursor = args.get("cursor", _MISSING)
        if raw_cursor is not _MISSING and raw_cursor is not None:
            if isinstance(raw_cursor, bool) or not isinstance(raw_cursor, (int, str)):
                raise NoSQLPolicyError("ARGUMENT_INVALID", "cursor 必须是 Redis 游标")
            if isinstance(raw_cursor, int) and raw_cursor < 0:
                raise NoSQLPolicyError("ARGUMENT_INVALID", "cursor 不能为负数")
            if isinstance(raw_cursor, str) and (not raw_cursor.strip() or len(raw_cursor) > 128):
                raise NoSQLPolicyError("ARGUMENT_INVALID", "cursor 格式无效")
            normalized["cursor"] = raw_cursor

        raw_limit = args.get("limit", _MISSING)
        if raw_limit is _MISSING or raw_limit is None:
            if default_limit is not None:
                raw_limit = default_limit
        if raw_limit is not _MISSING and raw_limit is not None:
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or not 1 <= raw_limit <= int(max_limit):
                raise NoSQLPolicyError("ARGUMENT_INVALID", "limit 超出 Redis 读取范围")
            normalized["limit"] = int(raw_limit)

        # Every operation except SCAN needs a concrete key.  HSCAN/SSCAN/
        # ZSCAN/XRANGE use the same key field and remain structured calls.
        if normalized_operation != "SCAN" and not normalized.get("key"):
            raise NoSQLPolicyError("ARGUMENT_INVALID", "该 Redis operation 需要 key")
        return NoSQLDecision(True, arguments=normalized)
    except NoSQLPolicyError as exc:
        return _failure(exc.code, exc.message)
    except (TypeError, ValueError) as exc:
        return _failure("ARGUMENT_INVALID", str(exc))


def validate_mongo_request(
    request: Mapping[str, Any] | None = None,
    *,
    collection_id: str | None = None,
    operation: str | None = None,
    filter: Mapping[str, Any] | None = None,
    projection: Mapping[str, Any] | None = None,
    sort: Mapping[str, Any] | None = None,
    pipeline: Sequence[Any] | None = None,
    limit: int | None = None,
    allowed_collection_ids: Sequence[str] | None = None,
    max_limit: int = _MAX_LIMIT,
) -> NoSQLDecision:
    """Validate a MongoDB find/count/aggregate request recursively."""

    try:
        args = _merge_request(
            request,
            {
                "collection_id": collection_id,
                "operation": operation,
                "filter": filter,
                "projection": projection,
                "sort": sort,
                "pipeline": pipeline,
                "limit": limit,
            },
        )
        _reject_unknown_keys(args, _MONGO_ARGUMENTS)
        normalized_collection = _bounded_string(args.get("collection_id", _MISSING), "collection_id", _MAX_KEY_CHARS, required=True)
        allowed = _normalize_ids(allowed_collection_ids)
        if allowed and normalized_collection.casefold() not in allowed:
            raise NoSQLPolicyError("SCOPE_DENIED", "集合不在宿主注入的授权范围内")

        raw_operation = args.get("operation", _MISSING)
        if not isinstance(raw_operation, str) or raw_operation.strip().casefold() not in MONGO_READ_OPERATIONS:
            raise NoSQLPolicyError("POLICY_DENIED", "Mongo operation 不在只读白名单中")
        normalized_operation = raw_operation.strip().casefold()
        normalized: dict[str, Any] = {
            "collection_id": normalized_collection,
            "operation": normalized_operation,
        }

        for name in ("filter", "projection", "sort"):
            value = args.get(name, _MISSING)
            if value is _MISSING or value is None:
                continue
            if not isinstance(value, Mapping):
                raise NoSQLPolicyError("ARGUMENT_INVALID", f"{name} 必须是对象")
            copied = _copy_json(value, name)
            _walk_mongo_document(copied, name)
            normalized[name] = copied

        raw_pipeline = args.get("pipeline", _MISSING)
        if raw_pipeline is not _MISSING and raw_pipeline is not None:
            if normalized_operation != "aggregate":
                raise NoSQLPolicyError("ARGUMENT_INVALID", "只有 aggregate 支持 pipeline")
            if not isinstance(raw_pipeline, Sequence) or isinstance(raw_pipeline, (str, bytes, bytearray)):
                raise NoSQLPolicyError("ARGUMENT_INVALID", "pipeline 必须是数组")
            if len(raw_pipeline) > _MAX_PIPELINE_STAGES:
                raise NoSQLPolicyError("ARGUMENT_INVALID", "pipeline 项数超出上限")
            normalized_pipeline: list[dict[str, Any]] = []
            for index, stage in enumerate(raw_pipeline):
                if not isinstance(stage, Mapping) or len(stage) != 1:
                    raise NoSQLPolicyError("ARGUMENT_INVALID", f"pipeline[{index}] 必须是单阶段对象")
                raw_stage_name, raw_stage_value = next(iter(stage.items()))
                if not isinstance(raw_stage_name, str) or not raw_stage_name.startswith("$"):
                    raise NoSQLPolicyError("POLICY_DENIED", "Mongo 聚合阶段未登记")
                stage_name = _canonical_stage_name(raw_stage_name)
                if stage_name not in MONGO_PIPELINE_STAGES:
                    if stage_name in MONGO_FORBIDDEN_OPERATORS:
                        raise NoSQLPolicyError("POLICY_DENIED", "Mongo 聚合阶段包含写入或脚本操作")
                    raise NoSQLPolicyError("POLICY_DENIED", "Mongo 聚合阶段不在只读白名单中")
                copied_stage_value = _copy_json(raw_stage_value, f"pipeline[{index}].{raw_stage_name}")
                _walk_mongo_document(copied_stage_value, f"pipeline[{index}].{raw_stage_name}")
                normalized_pipeline.append({_canonical_stage_output(stage_name): copied_stage_value})
            normalized["pipeline"] = normalized_pipeline

        raw_limit = args.get("limit", _MISSING)
        if raw_limit is not _MISSING and raw_limit is not None:
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or not 1 <= raw_limit <= int(max_limit):
                raise NoSQLPolicyError("ARGUMENT_INVALID", "limit 超出 Mongo 读取范围")
            normalized["limit"] = int(raw_limit)
        return NoSQLDecision(True, arguments=normalized)
    except NoSQLPolicyError as exc:
        return _failure(exc.code, exc.message)
    except (TypeError, ValueError) as exc:
        return _failure("ARGUMENT_INVALID", str(exc))


def validate_mongo_pipeline(pipeline: Sequence[Any]) -> list[dict[str, Any]]:
    """Validate and normalize only a Mongo aggregate pipeline.

    This lower-level helper raises :class:`NoSQLPolicyError` so adapter code
    that already handles structured policy exceptions can use it directly.
    """

    decision = validate_mongo_request(
        {"collection_id": "_policy_probe", "operation": "aggregate", "pipeline": pipeline}
    )
    if not decision.allowed:
        raise NoSQLPolicyError(decision.code, decision.message)
    return list(decision.arguments.get("pipeline", []))


def reject_mongo_unsafe(value: Any, path: str = "value") -> None:
    """Raise a policy error when a nested Mongo document is unsafe."""

    copied = _copy_json(value, path)
    _walk_mongo_document(copied, path)


def _merge_request(request: Mapping[str, Any] | None, keywords: Mapping[str, Any]) -> dict[str, Any]:
    if request is not None and not isinstance(request, Mapping):
        raise NoSQLPolicyError("ARGUMENT_INVALID", "NoSQL 工具参数必须是对象")
    result = dict(request or {})
    # Explicit keyword arguments are only overlays when a caller supplied a
    # non-None value.  This keeps ``validate_*_request(payload)`` lossless.
    for key, value in keywords.items():
        if value is not None:
            result[key] = value
    return result


def _reject_unknown_keys(args: Mapping[str, Any], allowed: frozenset[str]) -> None:
    for key in args:
        if not isinstance(key, str):
            raise NoSQLPolicyError("ARGUMENT_INVALID", "工具参数字段名无效")
        if key.casefold() in _HOST_ARGUMENTS:
            raise NoSQLPolicyError("SCOPE_DENIED", "工具不能覆盖宿主连接或凭据")
        if key not in allowed:
            raise NoSQLPolicyError("ARGUMENT_INVALID", "工具参数不在固定结构中")


def _bounded_string(value: Any, name: str, max_chars: int, *, required: bool = False) -> str:
    if value is _MISSING or value is None:
        if required:
            raise NoSQLPolicyError("ARGUMENT_INVALID", f"{name} 不能为空")
        return ""
    if not isinstance(value, str):
        raise NoSQLPolicyError("ARGUMENT_INVALID", f"{name} 必须是文本")
    result = value.strip()
    if required and not result:
        raise NoSQLPolicyError("ARGUMENT_INVALID", f"{name} 不能为空")
    if len(result) > max_chars or any(ord(char) < 32 for char in result):
        raise NoSQLPolicyError("ARGUMENT_INVALID", f"{name} 格式无效")
    return result


def _copy_json(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 嵌套过深")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > _MAX_TEXT_CHARS:
            raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 文本过长")
        if isinstance(value, str) and any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 包含无效字符")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 数值无效")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_ITEMS:
            raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 字段过多")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_KEY_CHARS:
                raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 字段名无效")
            result[key] = _copy_json(item, f"{path}.{key}", depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_ITEMS:
            raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 项数超出上限")
        return [_copy_json(item, f"{path}[{index}]", depth=depth + 1) for index, item in enumerate(value)]
    raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 不是 JSON 值")


def _walk_mongo_document(value: Any, path: str, *, depth: int = 0) -> None:
    """Recursively reject write/script/unknown Mongo operators."""

    if depth > _MAX_DEPTH:
        raise NoSQLPolicyError("ARGUMENT_INVALID", f"{path} 嵌套过深")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.startswith("$"):
                folded = key.casefold()
                forbidden = {item.casefold() for item in MONGO_FORBIDDEN_OPERATORS}
                safe = {item.casefold() for item in MONGO_SAFE_EXPRESSIONS}
                if folded in forbidden:
                    raise NoSQLPolicyError("POLICY_DENIED", "Mongo 参数包含被禁止的写入、扩展或脚本操作")
                if folded not in safe:
                    # Pipeline stages are checked by the caller at the stage
                    # boundary; they are not valid nested expressions.
                    raise NoSQLPolicyError("POLICY_DENIED", "Mongo 参数包含未登记的表达式")
            _walk_mongo_document(item, f"{path}.{key}", depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _walk_mongo_document(item, f"{path}[{index}]", depth=depth + 1)


def _canonical_stage_name(value: str) -> str:
    folded = value.casefold()
    # ``$addFields`` is the only allowlisted stage whose case-folded spelling
    # differs from its canonical output spelling.
    return folded


def _canonical_stage_output(value: str) -> str:
    outputs = {
        "$addfields": "$addFields",
        "$match": "$match",
        "$project": "$project",
        "$group": "$group",
        "$sort": "$sort",
        "$limit": "$limit",
        "$skip": "$skip",
        "$unwind": "$unwind",
        "$count": "$count",
        "$set": "$set",
        "$unset": "$unset",
    }
    return outputs[value]


def _normalize_ids(values: Sequence[str] | None) -> frozenset[str]:
    if values is None:
        return frozenset()
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("authorized collection IDs must be non-empty strings")
        result.add(value.strip().casefold())
    return frozenset(result)


def _failure(code: str, message: str) -> NoSQLDecision:
    return NoSQLDecision(False, code=str(code), message=str(message))


# Friendly aliases for callers that use the tool names as function prefixes.
validate_redis_read = validate_redis_request
validate_mongo_read = validate_mongo_request
validate_redis_call = validate_redis_request
validate_mongo_call = validate_mongo_request
is_redis_read_allowed = lambda request: validate_redis_request(request).allowed
is_mongo_read_allowed = lambda request: validate_mongo_request(request).allowed


__all__ = [
    "MONGO_FORBIDDEN_OPERATORS",
    "MONGO_PIPELINE_STAGES",
    "MONGO_READ_OPERATIONS",
    "MONGO_SAFE_EXPRESSIONS",
    "NoSQLDecision",
    "NoSQLPolicy",
    "NoSQLPolicyError",
    "POLICY_VERSION",
    "REDIS_READ_OPERATIONS",
    "RedisReadPolicy",
    "MongoReadPolicy",
    "is_mongo_read_allowed",
    "is_redis_read_allowed",
    "reject_mongo_unsafe",
    "validate_mongo_call",
    "validate_mongo_pipeline",
    "validate_mongo_read",
    "validate_mongo_request",
    "validate_redis_call",
    "validate_redis_read",
    "validate_redis_request",
]
