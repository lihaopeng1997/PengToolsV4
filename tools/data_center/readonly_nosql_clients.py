"""Host-owned read-only facades for Redis and MongoDB.

These facades deliberately accept an already-created client supplied by the
host.  They do not read connection profiles, decrypt secrets, or create a
database connection.  The host must create a fresh client/lease from the
selected profile and pass only that lease here.  A model request is reduced to
the structured operation allowlists in :mod:`nosql_policy`; there is no
command, script, pipeline, collection resolver, or connection override
escape hatch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import base64
import inspect
import json
import math
import secrets
import threading
import time
from typing import Any, Protocol

from .contracts import ToolResult
from .nosql_codec import encode_bounded
from .nosql_policy import MongoReadPolicy, RedisReadPolicy


DEFAULT_LIMIT = 50
MAX_LIMIT = 100
DEFAULT_MAX_VALUE_BYTES = 64 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 12 * 1024
DEFAULT_MAX_PAGES = 16
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_MAX_CURSOR_STATE_BYTES = 32 * 1024
DEFAULT_MAX_CURSOR_STATE_ITEMS = 512
DEFAULT_MAX_CURSOR_STATE_DEPTH = 8
DEFAULT_MAX_CURSOR_TOKEN_CHARS = 256


class ReadOnlyClientError(ValueError):
    """Safe, stable error raised for an invalid host-owned lease."""

    def __init__(self, code: str, message: str, *, result_unknown: bool = False) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.result_unknown = bool(result_unknown)


class CursorCodec(Protocol):
    """Host supplied opaque cursor storage/signing contract."""

    def encode(self, payload: Mapping[str, Any]) -> str:
        ...

    def decode(self, token: str) -> Mapping[str, Any]:
        ...


class OpaqueCursorCodec:
    """Small in-process opaque cursor store for one host-owned lease.

    The token contains only a random handle.  State, including cluster node
    cursors, never appears in a model message.  A production host may inject
    a signed/encrypted or persistent implementation through ``cursor_codec``.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 15 * 60,
        max_tokens: int = 512,
        max_state_bytes: int = DEFAULT_MAX_CURSOR_STATE_BYTES,
        max_state_items: int = DEFAULT_MAX_CURSOR_STATE_ITEMS,
        max_state_depth: int = DEFAULT_MAX_CURSOR_STATE_DEPTH,
        max_token_chars: int = DEFAULT_MAX_CURSOR_TOKEN_CHARS,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        for name, value in (
            ("max_state_bytes", max_state_bytes),
            ("max_state_items", max_state_items),
            ("max_state_depth", max_state_depth),
            ("max_token_chars", max_token_chars),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_tokens = max_tokens
        self.max_state_bytes = max_state_bytes
        self.max_state_items = max_state_items
        self.max_state_depth = max_state_depth
        self.max_token_chars = max_token_chars
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def encode(self, payload: Mapping[str, Any]) -> str:
        if not isinstance(payload, Mapping):
            raise TypeError("cursor payload must be a mapping")
        # Validate and copy incrementally.  Do this before JSON encoding so a
        # malicious/buggy host codec state cannot force a full list or JSON
        # materialization before the hard cursor-state limits are applied.
        try:
            copied = _copy_cursor_state(
                payload,
                max_bytes=self.max_state_bytes,
                max_items=self.max_state_items,
                max_depth=self.max_state_depth,
            )
            encoded = json.dumps(copied, ensure_ascii=False, separators=(",", ":"))
        except TypeError as exc:
            raise TypeError("cursor payload must be bounded JSON shaped") from exc
        if len(encoded.encode("utf-8")) > self.max_state_bytes:
            raise ValueError("cursor payload exceeds state byte limit")
        token = "cursor_" + secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            if len(self._values) >= self.max_tokens:
                oldest = min(self._values, key=lambda key: self._values[key][0])
                self._values.pop(oldest, None)
            self._values[token] = (now + self.ttl_seconds, copied)
        return token

    def decode(self, token: str) -> Mapping[str, Any]:
        if (
            not isinstance(token, str)
            or len(token) > self.max_token_chars
            or not token.startswith("cursor_")
        ):
            raise ReadOnlyClientError("ARGUMENT_INVALID", "分页游标无效")
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            value = self._values.get(token)
            if value is None:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页游标已过期或不属于当前连接")
            # Return a bounded copy so a handler cannot mutate stored scope
            # state.  Re-validate the stored value in case host code touched
            # the in-process store; there is no second unbounded JSON round
            # trip on the decode path.
            try:
                return _copy_cursor_state(
                    value[1],
                    max_bytes=self.max_state_bytes,
                    max_items=self.max_state_items,
                    max_depth=self.max_state_depth,
                )
            except (TypeError, ValueError, OverflowError):
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页状态无效") from None

    def invalidate(self, token: str) -> None:
        with self._lock:
            self._values.pop(str(token), None)

    def _purge(self, now: float) -> None:
        for token, (expires, _payload) in list(self._values.items()):
            if expires <= now:
                self._values.pop(token, None)


def _cancelled(token: Any) -> bool:
    if token is None:
        return False
    method = getattr(token, "is_cancelled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return True
    method = getattr(token, "is_set", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return True
    if callable(token):
        try:
            return bool(token())
        except Exception:
            return True
    return bool(getattr(token, "cancelled", False))


def _copy_cursor_state(
    value: Any,
    *,
    max_bytes: int,
    max_items: int,
    max_depth: int,
) -> dict[str, Any]:
    """Copy cursor state without traversing beyond host-owned hard limits."""

    counters = {"items": 0, "bytes": 2}  # account for the outer JSON object

    def charge(amount: int) -> None:
        counters["bytes"] += max(0, int(amount))
        if counters["bytes"] > max_bytes:
            raise ValueError("cursor payload exceeds state byte limit")

    def charge_text(text: str, overhead: int = 0) -> None:
        # UTF-8 uses at least one byte per code point.  Reject an obviously
        # oversized string before asking Python to allocate an encoded copy;
        # only a string within the configured hard bound is encoded.
        if len(text) > max_bytes:
            raise ValueError("cursor payload exceeds state byte limit")
        charge(len(text.encode("utf-8")) + overhead)

    def copy_item(item: Any, depth: int) -> Any:
        if depth > max_depth:
            raise ValueError("cursor payload exceeds state depth limit")
        if item is None or isinstance(item, bool):
            charge(4 if item is None else 5)
            return item
        if isinstance(item, int):
            text = str(item)
            charge(len(text))
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("cursor payload contains a non-finite number")
            text = repr(item)
            charge(len(text))
            return item
        if isinstance(item, str):
            # The JSON copy is intentionally bounded before serialization.
            charge_text(item, 2)
            return item
        if isinstance(item, Mapping):
            copied: dict[str, Any] = {}
            for key, child in item.items():
                counters["items"] += 1
                if counters["items"] > max_items:
                    raise ValueError("cursor payload exceeds state item limit")
                if not isinstance(key, str):
                    raise TypeError("cursor payload keys must be strings")
                charge_text(key, 4)
                copied[key] = copy_item(child, depth + 1)
            return copied
        if isinstance(item, (list, tuple)):
            copied_list: list[Any] = []
            for child in item:
                counters["items"] += 1
                if counters["items"] > max_items:
                    raise ValueError("cursor payload exceeds state item limit")
                copied_list.append(copy_item(child, depth + 1))
            return copied_list
        raise TypeError("cursor payload must be JSON shaped")

    copied_value = copy_item(value, 0)
    if not isinstance(copied_value, dict):
        raise TypeError("cursor payload must be a mapping")
    return copied_value


def _safe_result(
    *,
    ok: bool,
    code: str = "OK",
    data: Any = None,
    query_id: str | None = None,
    scope: Mapping[str, Any] | None = None,
    truncated: bool = False,
    limits: Mapping[str, Any] | None = None,
    elapsed_ms: int | float | None = None,
) -> ToolResult:
    return ToolResult(
        ok=ok,
        code=code,
        data=data,
        query_id=query_id,
        scope=dict(scope or {}),
        truncated=truncated,
        limits=dict(limits or {}),
        elapsed_ms=elapsed_ms,
    )


def _failure(
    code: str,
    message: str,
    *,
    query_id: str | None = None,
    scope: Mapping[str, Any] | None = None,
    result_unknown: bool = False,
    limits: Mapping[str, Any] | None = None,
) -> ToolResult:
    data: dict[str, Any] = {"message": message}
    if result_unknown:
        data["result_unknown"] = True
    return _safe_result(
        ok=False,
        code=code,
        data=data,
        query_id=query_id,
        scope=scope,
        limits={**dict(limits or {}), **({"result_unknown": True} if result_unknown else {})},
    )


def _driver_call(method: Callable[..., Any], kwargs: Mapping[str, Any]) -> Any:
    """Call a known read method once, adapting tiny fake signatures safely."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**dict(kwargs))
    params = signature.parameters
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return method(**dict(kwargs))
    aliases = {
        "name": ("key",),
        "key": ("name",),
        "filter": ("query", "criteria"),
        "max_time_ms": ("maxTimeMS",),
        "maxTimeMS": ("max_time_ms",),
        "batch_size": ("batchSize",),
        "batchSize": ("batch_size",),
        "allow_disk_use": ("allowDiskUse",),
        "allowDiskUse": ("allow_disk_use",),
    }
    accepted: dict[str, Any] = {}
    # Redis HGET/HSTRLEN are named (name, key) in redis-py while tiny fakes
    # commonly use (key, field).  Resolve that pair before the generic alias
    # pass so the second argument cannot overwrite the hash key.
    if (
        "name" in kwargs
        and "key" in kwargs
        and "key" in params
        and "field" in params
    ):
        accepted["key"] = kwargs["name"]
        accepted["field"] = kwargs["key"]
    for name, value in kwargs.items():
        if name == "name" and "field" in params and "key" in kwargs and "key" in params:
            continue
        if name == "key" and "field" in params and "name" in kwargs and "key" in params:
            continue
        if name in params and params[name].kind != inspect.Parameter.POSITIONAL_ONLY:
            accepted[name] = value
            continue
        for alias in aliases.get(name, ()):
            if alias in params and params[alias].kind != inspect.Parameter.POSITIONAL_ONLY:
                accepted[alias] = value
                break
    positional_only = [item for item in params.values() if item.kind == inspect.Parameter.POSITIONAL_ONLY]
    if positional_only:
        values = []
        for item in positional_only:
            if item.name in kwargs:
                values.append(kwargs[item.name])
            elif item.default is inspect.Parameter.empty:
                raise TypeError("read method signature is unsupported")
            else:
                break
        return method(*values, **accepted)
    return method(**accepted)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _method(driver: Any, name: str) -> Callable[..., Any] | None:
    # ``name`` comes exclusively from these call sites, never from model text.
    value = getattr(driver, name, None)
    return value if callable(value) else None


def _is_transport_error(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def _is_timeout_error(exc: BaseException) -> bool:
    """Recognize common driver timeout errors without importing a driver."""

    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.casefold()
    return "timeout" in name or name in {"networktimeout", "executiontimeout"}


def _close_owner(owner: Any, cursor: Any = None) -> None:
    """Close a cursor and its lease owner after an unknown operation."""

    if cursor is not None:
        _close_quietly(cursor)
    if owner is not None and owner is not cursor:
        _close_quietly(owner)


def _deadline_expired(deadline: float | None) -> bool:
    if deadline is None:
        return False
    return time.monotonic() >= float(deadline)


def _boundary_failure(
    *,
    cancellation: Any,
    deadline: float | None,
    owner: Any,
    cursor: Any = None,
) -> None:
    """Raise a stable result-unknown error after one driver boundary."""

    if _cancelled(cancellation):
        _close_owner(owner, cursor)
        raise ReadOnlyClientError(
            "CANCELLED",
            "读取在底层调用期间被停止，结果未知",
            result_unknown=True,
        )
    if _deadline_expired(deadline):
        _close_owner(owner, cursor)
        raise ReadOnlyClientError(
            "TIMEOUT",
            "读取已超过宿主截止时间，结果未知",
            result_unknown=True,
        )


def _checked_call(
    call: Callable[[], Any],
    *,
    cancellation: Any,
    deadline: float | None,
    owner: Any,
    cursor: Any = None,
    close_result: bool = False,
) -> Any:
    """Execute one driver call and check cancellation immediately on return."""

    try:
        result = call()
    except StopIteration:
        # ``next(iterator)`` uses StopIteration as its normal end marker.  It
        # is still a driver boundary, so a cancellation that arrived while
        # waiting must win over the apparent end of the cursor.
        _boundary_failure(cancellation=cancellation, deadline=deadline, owner=owner, cursor=cursor)
        raise
    except Exception as exc:
        if _is_timeout_error(exc):
            _close_owner(owner, cursor)
            raise ReadOnlyClientError(
                "TIMEOUT",
                "底层只读调用超时，结果未知",
                result_unknown=True,
            ) from None
        # A provider may surface cancellation as a generic transport error.
        # Preserve cancellation/deadline semantics before exposing a generic
        # query failure to the caller.
        if _cancelled(cancellation) or _deadline_expired(deadline):
            _boundary_failure(cancellation=cancellation, deadline=deadline, owner=owner, cursor=cursor)
        raise
    if close_result:
        # A find/aggregate factory returns the cursor itself.  If cancellation
        # was raised while that factory was blocked, close the returned cursor
        # as well as the owner lease before propagating the boundary result.
        try:
            _boundary_failure(cancellation=cancellation, deadline=deadline, owner=owner, cursor=result)
        except ReadOnlyClientError:
            raise
    else:
        _boundary_failure(cancellation=cancellation, deadline=deadline, owner=owner, cursor=cursor)
    return result


def _call_accepts(method: Callable[..., Any], *names: str) -> bool:
    """Return whether a driver method can receive at least one named option."""

    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        # C-extension methods do not expose a signature.  The host must have
        # explicitly verified the driver in this case; do not claim support.
        return False
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return True
    return any(name in params for name in names)


def _call_has_explicit_option(method: Callable[..., Any], *names: str) -> bool:
    """Require an explicit timeout parameter before claiming server enforcement."""

    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    if any(name in params for name in names):
        return True
    # Known PyMongo methods intentionally expose arbitrary keyword options;
    # their driver-owned method names are the only var-keyword case we can
    # identify without a live connection.  Unknown fakes/providers are kept
    # in the honest "hint supplied, enforcement unverified" state.
    module = str(getattr(method, "__module__", ""))
    return module.startswith("pymongo.")


def _read_socket_timeout(owner: Any, explicit_ms: int | None = None) -> tuple[bool, int | float | None]:
    """Inspect host-configured client socket timeout without changing it."""

    if explicit_ms is not None:
        return True, explicit_ms
    candidates: list[Any] = [owner]
    seen: set[int] = set()
    index = 0
    # PyMongo exposes options on the client, while redis-py stores the value
    # in connection_pool.connection_kwargs.  Walk only these fixed objects;
    # never call a factory or ask the driver to create a connection here.
    while index < len(candidates) and index < 16:
        candidate = candidates[index]
        index += 1
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        if isinstance(candidate, Mapping):
            for key in (
                "socket_timeout",
                "socket_timeout_ms",
                "socketTimeoutMS",
                "read_timeout",
                "readTimeout",
            ):
                if key in candidate and _valid_timeout_value(candidate[key]):
                    return True, candidate[key]
        for name in (
            "socket_timeout",
            "socket_timeout_ms",
            "socketTimeoutMS",
            "read_timeout",
            "readTimeout",
        ):
            try:
                value = getattr(candidate, name, None)
            except Exception:
                value = None
            if _valid_timeout_value(value):
                return True, value
        for attr in (
            "connection_pool",
            "options",
            "_options",
            "pool_options",
            "connection_kwargs",
            "_connection_kwargs",
        ):
            try:
                value = getattr(candidate, attr, None)
            except Exception:
                value = None
            if value is not None and id(value) not in seen:
                candidates.append(value)
    return False, None


def _valid_timeout_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _remaining_ms(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int((float(deadline) - time.monotonic()) * 1000))


def _normalize_deadline(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("deadline must be a timestamp")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("deadline must be a timestamp") from exc
    if not math.isfinite(number):
        raise ValueError("deadline must be a finite timestamp")
    return number


def _select_deadline(
    started: float,
    *,
    fixed_deadline: float | None,
    fixed_timeout_ms: int | None,
    supplied_deadline: float | None,
) -> float | None:
    values = [value for value in (fixed_deadline, supplied_deadline) if value is not None]
    if fixed_timeout_ms is not None:
        values.append(started + fixed_timeout_ms / 1000.0)
    return min(values) if values else None


def _timeout_gate(
    *,
    deadline: float | None,
    socket_timeout_configured: bool,
    query_id: str | None,
    scope: Mapping[str, Any],
) -> ToolResult | None:
    if deadline is None:
        return None
    timeout_limits = {
        "deadline": deadline,
        "client_socket_timeout_configured": bool(socket_timeout_configured),
        "server_timeout_enforced": False,
        "timeout_enforced": bool(socket_timeout_configured),
        "timeout_status": _timeout_status(
            deadline=deadline,
            server_timeout_enforced=False,
            socket_timeout_configured=socket_timeout_configured,
        ),
    }
    if _deadline_expired(deadline):
        return _failure(
            "TIMEOUT",
            "宿主只读截止时间已到",
            query_id=query_id,
            scope=scope,
            limits=timeout_limits,
        )
    if not socket_timeout_configured:
        return _failure(
            "TIMEOUT_UNAVAILABLE",
            "宿主截止时间需要已配置的客户端 socket timeout",
            query_id=query_id,
            scope=scope,
            limits=timeout_limits,
        )
    return None


def _timeout_status(
    *,
    deadline: float | None,
    server_timeout_enforced: bool,
    socket_timeout_configured: bool,
) -> str:
    if server_timeout_enforced:
        return "server"
    if deadline is not None and socket_timeout_configured:
        return "client_socket"
    if deadline is not None:
        return "unavailable"
    return "unverified"


def _iter_limited(
    value: Any,
    max_items: int,
    *,
    cancellation: Any = None,
    deadline: float | None = None,
    owner: Any = None,
) -> Sequence[Any]:
    """Consume at most ``max_items`` values from a driver iterable."""

    if value is None:
        return ()
    if isinstance(value, Mapping):
        iterator = iter(value.items())
    else:
        try:
            iterator = iter(value)
        except TypeError as exc:
            raise TypeError("driver batch is not iterable") from exc
    result: list[Any] = []
    for _ in range(max_items):
        try:
            result.append(
                _checked_call(
                    lambda: next(iterator),
                    cancellation=cancellation,
                    deadline=deadline,
                    owner=owner,
                )
            )
        except StopIteration:
            break
    return result


def _state_list(value: Any, *, max_items: int = DEFAULT_MAX_CURSOR_STATE_ITEMS) -> list[Any]:
    """Copy a host cursor-state list without traversing past its hard cap."""

    if value is None:
        return []
    try:
        iterator = iter(value)
    except TypeError as exc:
        raise ReadOnlyClientError("ARGUMENT_INVALID", "分页状态列表无效") from exc
    result: list[Any] = []
    for index in range(max_items + 1):
        try:
            item = next(iterator)
        except StopIteration:
            return result
        if index >= max_items:
            raise ReadOnlyClientError("ARGUMENT_INVALID", "分页状态条目过多")
        result.append(item)
    return result


def _state_mapping(value: Any, *, max_items: int = DEFAULT_MAX_CURSOR_STATE_ITEMS) -> dict[str, Any]:
    """Copy a host cursor-state mapping without a full ``dict`` conversion."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ReadOnlyClientError("ARGUMENT_INVALID", "分页状态映射无效")
    result: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= max_items:
            raise ReadOnlyClientError("ARGUMENT_INVALID", "分页状态条目过多")
        result[str(key)[:512]] = item
    return result


def _collect_cursor(
    cursor: Any,
    limit: int,
    *,
    cancellation: Any,
    deadline: float | None,
    owner: Any,
) -> list[Any]:
    """Collect at most ``limit + 1`` cursor items without ``fetchall``."""

    iterator = _checked_call(
        lambda: iter(cursor),
        cancellation=cancellation,
        deadline=deadline,
        owner=owner,
        cursor=cursor,
    )
    result: list[Any] = []
    while len(result) < limit + 1:
        try:
            item = _checked_call(
                lambda: next(iterator),
                cancellation=cancellation,
                deadline=deadline,
                owner=owner,
                cursor=cursor,
            )
        except StopIteration:
            break
        result.append(item)
    return result


def _pack_cursor_value(value: Any, *, depth: int = 0, items_seen: list[int] | None = None) -> Any:
    """Pack one Redis value for cursor state with bounded recursion."""

    if items_seen is None:
        items_seen = [0]
    if depth > DEFAULT_MAX_CURSOR_STATE_DEPTH:
        raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值嵌套过深")
    if isinstance(value, bytes):
        if len(value) > DEFAULT_MAX_CURSOR_STATE_BYTES:
            raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值超出状态大小上限")
        return {"kind": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        packed: list[Any] = []
        for item in value:
            items_seen[0] += 1
            if items_seen[0] > DEFAULT_MAX_CURSOR_STATE_ITEMS:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值条目过多")
            packed.append(_pack_cursor_value(item, depth=depth + 1, items_seen=items_seen))
        return {"kind": "tuple", "value": packed}
    if isinstance(value, list):
        packed = []
        for item in value:
            items_seen[0] += 1
            if items_seen[0] > DEFAULT_MAX_CURSOR_STATE_ITEMS:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值条目过多")
            packed.append(_pack_cursor_value(item, depth=depth + 1, items_seen=items_seen))
        return {"kind": "list", "value": packed}
    if isinstance(value, Mapping):
        packed = []
        for key, item in value.items():
            items_seen[0] += 1
            if items_seen[0] > DEFAULT_MAX_CURSOR_STATE_ITEMS:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值条目过多")
            packed.append(
                [
                    str(key)[:512],
                    _pack_cursor_value(item, depth=depth + 1, items_seen=items_seen),
                ]
            )
        return {
            "kind": "mapping",
            "value": packed,
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            # Check the character bound first so a huge UTF-8 string is not
            # duplicated merely to discover that it cannot enter cursor state.
            if len(value) > DEFAULT_MAX_CURSOR_STATE_BYTES:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值超出状态大小上限")
            if len(value.encode("utf-8")) > DEFAULT_MAX_CURSOR_STATE_BYTES:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值超出状态大小上限")
        return value if not isinstance(value, str) else value[:2048]
    # Do not call repr/str on an arbitrary driver object: a custom
    # representation can be huge or contain secrets.  Cursor state only needs
    # to be able to resume the supported scalar Redis shapes.
    raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值类型不受支持")


def _unpack_cursor_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        kind = value.get("kind")
        if kind == "bytes":
            encoded = str(value.get("value", ""))
            if len(encoded) > ((DEFAULT_MAX_CURSOR_STATE_BYTES + 2) // 3) * 4:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值超出状态大小上限")
            try:
                return base64.b64decode(encoded.encode("ascii"), validate=True)
            except Exception:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值编码无效") from None
        if kind in {"tuple", "list"}:
            items = []
            for index, item in enumerate(value.get("value", ())):
                if index >= DEFAULT_MAX_CURSOR_STATE_ITEMS:
                    raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值条目过多")
                items.append(_unpack_cursor_value(item))
            return tuple(items) if kind == "tuple" else items
        if kind == "mapping":
            result: dict[str, Any] = {}
            for index, item in enumerate(value.get("value", ())):
                if index >= DEFAULT_MAX_CURSOR_STATE_ITEMS:
                    raise ReadOnlyClientError("ARGUMENT_INVALID", "分页值条目过多")
                if isinstance(item, list) and len(item) == 2:
                    result[str(item[0])[:512]] = _unpack_cursor_value(item[1])
            return result
    return value


class RedisReadOnlyFacade:
    """Structured read-only operations over a host-owned Redis client."""

    _READ_METHODS = {
        "scan": "scan",
        "type": "type",
        "ttl": "ttl",
        "pttl": "pttl",
        "get": "get",
        "strlen": "strlen",
        "hget": "hget",
        "hscan": "hscan",
        "llen": "llen",
        "lrange": "lrange",
        "scard": "scard",
        "sscan": "sscan",
        "zcard": "zcard",
        "zrange": "zrange",
        "zscan": "zscan",
        "xlen": "xlen",
        "xrange": "xrange",
    }

    def __init__(
        self,
        client: Any,
        *,
        connection_id: str,
        profile_revision: str = "",
        mode: str = "standalone",
        cursor_codec: CursorCodec | None = None,
        max_limit: int = MAX_LIMIT,
        default_limit: int = DEFAULT_LIMIT,
        max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        max_pages: int = DEFAULT_MAX_PAGES,
        deadline: float | None = None,
        timeout_ms: int | None = None,
        socket_timeout_ms: int | None = None,
    ) -> None:
        if client is None:
            raise ValueError("client is required")
        if not str(connection_id or "").strip():
            raise ValueError("connection_id is required")
        if str(mode).casefold() not in {"standalone", "cluster"}:
            raise ValueError("unsupported Redis mode")
        for name, value in (
            ("max_limit", max_limit),
            ("default_limit", default_limit),
            ("max_value_bytes", max_value_bytes),
            ("max_output_bytes", max_output_bytes),
            ("max_pages", max_pages),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if default_limit > max_limit or max_limit > 10_000:
            raise ValueError("invalid Redis limits")
        if timeout_ms is not None and (
            isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0
        ):
            raise ValueError("timeout_ms must be positive")
        if socket_timeout_ms is not None and (
            isinstance(socket_timeout_ms, bool) or not isinstance(socket_timeout_ms, int) or socket_timeout_ms <= 0
        ):
            raise ValueError("socket_timeout_ms must be positive")
        self._client = client
        self.connection_id = str(connection_id)
        self.profile_revision = str(profile_revision or "")
        self.mode = str(mode).casefold()
        self.cursor_codec = cursor_codec or OpaqueCursorCodec()
        self.max_limit = int(max_limit)
        self.default_limit = int(default_limit)
        self.max_value_bytes = int(max_value_bytes)
        self.max_output_bytes = int(max_output_bytes)
        self.max_pages = int(max_pages)
        self._fixed_deadline = _normalize_deadline(deadline)
        self._fixed_timeout_ms = int(timeout_ms) if timeout_ms is not None else None
        self._socket_timeout_ms = socket_timeout_ms
        self._socket_timeout_configured, self._socket_timeout_value = _read_socket_timeout(
            client, socket_timeout_ms
        )
        self._closed = False
        self._scope = {"connection_id": self.connection_id, "engine": "redis", "mode": self.mode}

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_quietly(self._client)

    def execute(
        self,
        request: Mapping[str, Any],
        *,
        cancellation: Any = None,
        query_id: str | None = None,
        deadline: float | None = None,
    ) -> ToolResult:
        started = time.monotonic()
        try:
            supplied_deadline = _normalize_deadline(deadline)
        except ValueError:
            return _with_elapsed(
                _failure("ARGUMENT_INVALID", "宿主截止时间无效", query_id=query_id, scope=self._scope),
                _elapsed(started),
            )
        operation_deadline = _select_deadline(
            started,
            fixed_deadline=self._fixed_deadline,
            fixed_timeout_ms=self._fixed_timeout_ms,
            supplied_deadline=supplied_deadline,
        )
        if self._closed:
            return _failure("SESSION_NOT_FOUND", "只读连接已关闭", query_id=query_id, scope=self._scope)
        if _cancelled(cancellation):
            return _failure("CANCELLED", "本轮已停止，未启动工具", query_id=query_id, scope=self._scope)
        gate = _timeout_gate(
            deadline=operation_deadline,
            socket_timeout_configured=self._socket_timeout_configured,
            query_id=query_id,
            scope=self._scope,
        )
        if gate is not None:
            return _with_elapsed(gate, _elapsed(started))
        if not isinstance(request, Mapping):
            return _failure("ARGUMENT_INVALID", "Redis 工具参数必须是对象", query_id=query_id, scope=self._scope)
        decision = RedisReadPolicy(max_limit=self.max_limit, default_limit=self.default_limit).validate(request)
        if not decision.allowed:
            return _failure(decision.code, decision.message, query_id=query_id, scope=self._scope)
        args = dict(decision.arguments)
        operation = str(args["operation"]).casefold()
        try:
            if operation == "scan":
                result = self._scan(args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
            elif operation in {"hscan", "sscan", "zscan"}:
                result = self._member_scan(operation, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
            elif operation in {"lrange", "zrange", "xrange"}:
                result = self._range_read(operation, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
            elif operation in {"get", "hget"}:
                result = self._bounded_value_read(operation, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
            else:
                result = self._scalar_read(operation, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
        except ReadOnlyClientError as exc:
            result = _failure(
                exc.code,
                exc.message,
                query_id=query_id,
                scope=self._scope,
                result_unknown=exc.result_unknown,
            )
        except (TimeoutError,):
            result = _failure("TIMEOUT", "Redis 只读操作超时", query_id=query_id, scope=self._scope, result_unknown=True)
        except Exception:
            result = _failure("QUERY_FAILED", "Redis 只读操作失败", query_id=query_id, scope=self._scope)
        cancelled = _cancelled(cancellation)
        expired = _deadline_expired(operation_deadline)
        if result.ok and (cancelled or expired):
            self.close()
            result = _failure(
                "CANCELLED" if cancelled else "TIMEOUT",
                "Redis 读取完成后状态已改变，结果未知",
                query_id=query_id,
                scope=self._scope,
                result_unknown=True,
            )
        elapsed = int((time.monotonic() - started) * 1000)
        return _with_elapsed(result, elapsed)

    read = execute
    run = execute

    def _check(
        self,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult | None:
        if self._closed:
            return _failure("SESSION_NOT_FOUND", "只读连接已关闭", query_id=query_id, scope=self._scope)
        if _cancelled(cancellation):
            self.close()
            return _failure("CANCELLED", "本轮已停止，未启动新的读取", query_id=query_id, scope=self._scope)
        if _deadline_expired(deadline):
            return _failure(
                "TIMEOUT",
                "宿主只读截止时间已到",
                query_id=query_id,
                scope=self._scope,
                limits=self._timeout_limits({}, deadline),
            )
        return None

    def _timeout_limits(self, values: Mapping[str, Any], deadline: float | None) -> dict[str, Any]:
        """Expose honest timeout capability metadata on every Redis result."""

        return {
            **dict(values),
            "deadline": deadline,
            "client_socket_timeout_configured": self._socket_timeout_configured,
            "server_timeout_enforced": False,
            "timeout_enforced": bool(deadline is not None and self._socket_timeout_configured),
            "timeout_status": _timeout_status(
                deadline=deadline,
                server_timeout_enforced=False,
                socket_timeout_configured=self._socket_timeout_configured,
            ),
        }

    def _call(
        self,
        operation: str,
        kwargs: Mapping[str, Any],
        *,
        cancellation: Any = None,
        deadline: float | None = None,
        cursor: Any = None,
        close_result: bool = False,
    ) -> Any:
        method_name = self._READ_METHODS.get(operation)
        if method_name is None:
            raise ReadOnlyClientError("POLICY_DENIED", "Redis 读取操作未登记")
        method = _method(self._client, method_name)
        if method is None:
            raise ReadOnlyClientError("CAPABILITY_UNAVAILABLE", "Redis 只读操作未被客户端支持")
        return _checked_call(
            lambda: _driver_call(method, kwargs),
            cancellation=cancellation,
            deadline=deadline,
            owner=self,
            cursor=cursor,
            close_result=close_result,
        )

    def _scalar_read(
        self,
        operation: str,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        key = args.get("key")
        value = self._call(operation, {"name": key}, cancellation=cancellation, deadline=deadline)
        encoded = encode_bounded(value, max_bytes=self.max_output_bytes)
        return _safe_result(
            ok=True,
            data={"operation": operation.upper(), "key": encode_bounded(key, max_bytes=1024).value, "value": encoded.value},
            query_id=query_id,
            scope=self._scope,
            truncated=encoded.truncated,
            limits=self._timeout_limits({"max_output_bytes": self.max_output_bytes}, deadline),
        )

    def _bounded_value_read(
        self,
        operation: str,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        key = args.get("key")
        length_operation = "strlen" if operation == "get" else "hstrlen"
        # HSTRLEN is a read-only Redis command and is intentionally used only
        # internally; it is not a model-visible operation in the policy list.
        method = _method(self._client, length_operation)
        if method is None:
            raise ReadOnlyClientError("CAPABILITY_UNAVAILABLE", "大值读取缺少只读长度检查能力")
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        length_kwargs = {"name": key}
        if operation == "hget":
            length_kwargs["key"] = args.get("pattern")
        length = int(
            _checked_call(
                lambda: _driver_call(method, length_kwargs),
                cancellation=cancellation,
                deadline=deadline,
                owner=self,
            )
            or 0
        )
        if length < 0:
            length = 0
        if length > self.max_value_bytes:
            preview = None
            if operation == "get":
                getrange = _method(self._client, "getrange")
                if getrange is not None:
                    preview = _checked_call(
                        lambda: _driver_call(
                            getrange,
                            {"name": key, "start": 0, "end": self.max_value_bytes - 1},
                        ),
                        cancellation=cancellation,
                        deadline=deadline,
                        owner=self,
                    )
            encoded = encode_bounded(preview if preview is not None else b"", max_bytes=min(self.max_output_bytes, self.max_value_bytes))
            return _safe_result(
                ok=True,
                data={"operation": operation.upper(), "key": encode_bounded(key, max_bytes=1024).value, "size": length, "value": encoded.value},
                query_id=query_id,
                scope=self._scope,
                truncated=True,
                limits=self._timeout_limits(
                    {"max_value_bytes": self.max_value_bytes, "preview_only": True},
                    deadline,
                ),
            )
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        if operation == "get":
            value = self._call("get", {"name": key}, cancellation=cancellation, deadline=deadline)
        else:
            value = self._call(
                "hget",
                {"name": key, "key": args.get("pattern")},
                cancellation=cancellation,
                deadline=deadline,
            )
        encoded = encode_bounded(value, max_bytes=min(self.max_output_bytes, self.max_value_bytes))
        return _safe_result(
            ok=True,
            data={"operation": operation.upper(), "key": encode_bounded(key, max_bytes=1024).value, "size": length, "value": encoded.value},
            query_id=query_id,
            scope=self._scope,
            truncated=encoded.truncated,
            limits=self._timeout_limits({"max_value_bytes": self.max_value_bytes}, deadline),
        )

    def _scan_state(self, args: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        token = args.get("cursor")
        pattern = str(args.get("pattern") or "*")
        if token:
            raw_state = self.cursor_codec.decode(str(token))
            if not isinstance(raw_state, Mapping):
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页状态无效")
            # Read only the fixed state fields.  A custom host codec must not
            # be able to make the facade materialize arbitrary extra state.
            state: dict[str, Any] = {
                "kind": raw_state.get("kind"),
                "connection_id": raw_state.get("connection_id"),
                "profile_revision": raw_state.get("profile_revision"),
                "pattern": raw_state.get("pattern", "*"),
                "cursor": raw_state.get("cursor", 0),
                "pending": _state_list(raw_state.get("pending", ())),
                "finished": bool(raw_state.get("finished", False)),
                "partial": bool(raw_state.get("partial", False)),
                "failed_nodes": _state_list(raw_state.get("failed_nodes", ())),
                "cursors": _state_mapping(raw_state.get("cursors", {})),
                "node_labels": _state_mapping(raw_state.get("node_labels", {})),
                "started": bool(raw_state.get("started", False)),
            }
            if state.get("kind") != kind or state.get("connection_id") != self.connection_id or state.get("profile_revision") != self.profile_revision:
                raise ReadOnlyClientError("SCOPE_DENIED", "分页游标与当前只读连接不一致")
            requested_pattern = args.get("pattern")
            if requested_pattern is not None and str(state.get("pattern") or "*") != str(requested_pattern or "*"):
                raise ReadOnlyClientError("ARGUMENT_INVALID", "分页游标与 pattern 不一致")
            return state
        return {
            "kind": kind,
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "pattern": pattern,
            "cursor": 0,
            "pending": [],
            "finished": False,
            "partial": False,
            "failed_nodes": [],
            "cursors": {},
            "node_labels": {},
        }

    def _new_token(self, state: Mapping[str, Any]) -> str:
        return self.cursor_codec.encode(state)

    def _scan(
        self,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        state = self._scan_state(args, kind="redis_scan")
        limit = int(args.get("limit", self.default_limit))
        if self.mode == "cluster":
            values = self._cluster_scan_page(state, limit, args.get("pattern"), cancellation, query_id, deadline)
        else:
            values = self._standalone_scan_page(state, limit, args.get("pattern"), cancellation, query_id, deadline)
        return self._scan_result(values, state, limit, query_id, deadline)

    def _standalone_scan_page(
        self,
        state: dict[str, Any],
        limit: int,
        pattern: Any,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> list[Any]:
        values = [_unpack_cursor_value(item) for item in _state_list(state.get("pending", ()), max_items=limit + 1)]
        values = values[: limit + 1]
        state["pending"] = []
        cursor = int(state.get("cursor", 0) or 0)
        pages = 0
        while len(values) < limit + 1 and not state.get("finished") and pages < self.max_pages:
            failure = self._check(cancellation, query_id, deadline)
            if failure:
                raise ReadOnlyClientError(failure.code, failure.data.get("message", "读取已停止"))
            response = self._call(
                "scan",
                {"cursor": cursor, "match": pattern or None, "count": limit},
                cancellation=cancellation,
                deadline=deadline,
            )
            cursor, batch = _parse_scan_response(response)
            remaining = limit + 1 - len(values)
            values.extend(
                _iter_limited(
                    batch,
                    remaining,
                    cancellation=cancellation,
                    deadline=deadline,
                    owner=self,
                )
            )
            state["finished"] = cursor == 0
            state["cursor"] = cursor
            pages += 1
        if len(values) > limit:
            # Retain only the one extra item needed to prove truncation.  A
            # driver batch that violates its requested count cannot force us
            # to materialize the whole batch or cursor state.
            state["pending"] = [_pack_cursor_value(values[limit])]
            values = values[:limit]
        return values

    def _cluster_scan_page(
        self,
        state: dict[str, Any],
        limit: int,
        pattern: Any,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> list[Any]:
        values = [_unpack_cursor_value(item) for item in _state_list(state.get("pending", ()), max_items=limit + 1)]
        values = values[: limit + 1]
        state["pending"] = []
        cursors = {str(key): int(value or 0) for key, value in _state_mapping(state.get("cursors") or {}).items()}
        if not cursors and not state.get("started"):
            failure = self._check(cancellation, query_id, deadline)
            if failure:
                raise ReadOnlyClientError(failure.code, failure.data.get("message", "读取已停止"))
            response = self._call(
                "scan",
                {"cursor": 0, "match": pattern or None, "count": limit},
                cancellation=cancellation,
                deadline=deadline,
            )
            first_cursors, batch = _parse_cluster_scan_response(response)
            cursors.update(first_cursors)
            values.extend(
                _iter_limited(
                    batch,
                    limit + 1 - len(values),
                    cancellation=cancellation,
                    deadline=deadline,
                    owner=self,
                )
            )
            state["started"] = True
        pages = 0
        failed = _state_list(state.get("failed_nodes") or ())
        labels = _state_mapping(state.get("node_labels") or {})
        while len(values) < limit + 1 and pages < self.max_pages:
            active = [(name, cursor) for name, cursor in cursors.items() if cursor]
            if not active:
                break
            progress = False
            for name, cursor in active:
                failure = self._check(cancellation, query_id, deadline)
                if failure:
                    raise ReadOnlyClientError(failure.code, failure.data.get("message", "读取已停止"))
                node = name
                get_node = getattr(self._client, "get_node", None)
                if callable(get_node):
                    node = _checked_call(
                        lambda: _driver_call(get_node, {"node_name": name}),
                        cancellation=cancellation,
                        deadline=deadline,
                        owner=self,
                    )
                    if node is None:
                        label = labels.setdefault(name, f"node-{len(labels) + 1}")
                        if label not in failed:
                            failed.append(label)
                        state["partial"] = True
                        continue
                kwargs = {"cursor": cursor, "match": pattern or None, "count": limit}
                if callable(get_node) or name != "default":
                    kwargs["target_nodes"] = node
                try:
                    response = self._call(
                        "scan",
                        kwargs,
                        cancellation=cancellation,
                        deadline=deadline,
                    )
                    if isinstance(response, tuple) and len(response) == 2 and isinstance(response[0], Mapping):
                        next_cursor = int(response[0].get(name, 0) or 0)
                        batch = response[1] or []
                    else:
                        next_cursor, batch = _parse_scan_response(response)
                    cursors[name] = next_cursor
                    values.extend(
                        _iter_limited(
                            batch,
                            limit + 1 - len(values),
                            cancellation=cancellation,
                            deadline=deadline,
                            owner=self,
                        )
                    )
                    progress = True
                except Exception as exc:
                    if not _is_transport_error(exc):
                        raise
                    label = labels.setdefault(name, f"node-{len(labels) + 1}")
                    if label not in failed:
                        failed.append(label)
                    state["partial"] = True
                    # Keep the cursor in the opaque state so a later call may
                    # retry the failed node; never report a partial scan done.
                pages += 1
                if len(values) >= limit + 1:
                    break
            if not progress and len(values) < limit:
                break
        state["cursors"] = cursors
        state["failed_nodes"] = failed
        state["node_labels"] = labels
        state["finished"] = all(int(value or 0) == 0 for value in cursors.values()) and not state.get("partial")
        if len(values) > limit:
            state["pending"] = [_pack_cursor_value(values[limit])]
            values = values[:limit]
        return values

    def _scan_result(
        self,
        values: Sequence[Any],
        state: dict[str, Any],
        limit: int,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        has_next = bool(state.get("pending")) or not bool(state.get("finished")) or bool(state.get("partial"))
        token = self._new_token(state) if has_next else None
        encoded_values, encoded_truncated = _encode_items(values, self.max_output_bytes)
        data = {
            "operation": "SCAN",
            "keys": encoded_values,
            "next_cursor": token,
            "finished": bool(state.get("finished")) and not bool(state.get("partial")),
            "partial": bool(state.get("partial")),
            "failed_nodes": list(state.get("failed_nodes") or []),
            "pattern": str(state.get("pattern") or "*"),
        }
        return _safe_result(
            ok=True,
            data=data,
            query_id=query_id,
            scope=self._scope,
            truncated=bool(token) or encoded_truncated,
            limits=self._timeout_limits(
                {"limit": limit, "max_pages": self.max_pages, "max_output_bytes": self.max_output_bytes},
                deadline,
            ),
        )

    def _member_scan(
        self,
        operation: str,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        state = self._scan_state(args, kind=f"redis_{operation}")
        limit = int(args.get("limit", self.default_limit))
        values = [_unpack_cursor_value(item) for item in _state_list(state.get("pending", ()), max_items=limit + 1)]
        values = values[: limit + 1]
        state["pending"] = []
        cursor = int(state.get("cursor", 0) or 0)
        pages = 0
        while len(values) < limit + 1 and not state.get("finished") and pages < self.max_pages:
            failure = self._check(cancellation, query_id, deadline)
            if failure:
                raise ReadOnlyClientError(failure.code, failure.data.get("message", "读取已停止"))
            response = self._call(
                operation,
                {"name": args.get("key"), "cursor": cursor, "match": args.get("pattern") or None, "count": limit},
                cancellation=cancellation,
                deadline=deadline,
            )
            cursor, batch = _parse_scan_response(response)
            values.extend(
                _iter_limited(
                    batch,
                    limit + 1 - len(values),
                    cancellation=cancellation,
                    deadline=deadline,
                    owner=self,
                )
            )
            state["cursor"] = cursor
            state["finished"] = cursor == 0
            pages += 1
        if len(values) > limit:
            state["pending"] = [_pack_cursor_value(values[limit])]
            values = values[:limit]
        token = self._new_token(state) if state.get("pending") or not state.get("finished") else None
        encoded, encoded_truncated = _encode_items(values, self.max_output_bytes)
        return _safe_result(
            ok=True,
            data={"operation": operation.upper(), "key": encode_bounded(args.get("key"), max_bytes=1024).value, "items": encoded, "next_cursor": token, "finished": not bool(token)},
            query_id=query_id,
            scope=self._scope,
            truncated=bool(token) or encoded_truncated,
            limits=self._timeout_limits({"limit": limit, "max_pages": self.max_pages}, deadline),
        )

    def _range_read(
        self,
        operation: str,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        limit = int(args.get("limit", self.default_limit))
        kwargs: dict[str, Any]
        if operation == "xrange":
            kwargs = {"name": args.get("key"), "min": "-", "max": "+", "count": limit + 1}
        else:
            kwargs = {"name": args.get("key"), "start": 0, "end": limit}
            if operation == "zrange":
                kwargs["withscores"] = False
        values = self._call(operation, kwargs, cancellation=cancellation, deadline=deadline)
        values = _iter_limited(
            values,
            limit + 1,
            cancellation=cancellation,
            deadline=deadline,
            owner=self,
        )
        has_more = len(values) > limit
        values = values[:limit]
        encoded, encoded_truncated = _encode_items(values, self.max_output_bytes)
        return _safe_result(
            ok=True,
            data={"operation": operation.upper(), "key": encode_bounded(args.get("key"), max_bytes=1024).value, "items": encoded, "next_cursor": None},
            query_id=query_id,
            scope=self._scope,
            truncated=has_more or encoded_truncated,
            limits=self._timeout_limits({"limit": limit, "server_fetch_limit": limit + 1}, deadline),
        )


class MongoReadOnlyFacade:
    """Structured MongoDB reads over pre-bound collection objects."""

    def __init__(
        self,
        database: Any = None,
        *,
        collections: Mapping[str, Any] | None = None,
        client: Any = None,
        connection_id: str,
        database_name: str,
        allowed_collection_ids: Sequence[str],
        max_limit: int = MAX_LIMIT,
        default_limit: int = DEFAULT_LIMIT,
        max_time_ms: int = DEFAULT_TIMEOUT_MS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        max_document_bytes: int = DEFAULT_MAX_VALUE_BYTES,
        deadline: float | None = None,
        timeout_ms: int | None = None,
        socket_timeout_ms: int | None = None,
    ) -> None:
        if not str(connection_id or "").strip() or not str(database_name or "").strip():
            raise ValueError("connection_id and database_name are required")
        allowed = {str(item).strip().casefold() for item in (allowed_collection_ids or ()) if str(item).strip()}
        if not allowed:
            raise ValueError("Mongo collection allowlist must not be empty")
        if collections is None and database is None:
            raise ValueError("database or collections is required")
        if isinstance(max_limit, bool) or not isinstance(max_limit, int) or not 1 <= max_limit <= 10_000:
            raise ValueError("invalid Mongo max_limit")
        if isinstance(default_limit, bool) or not isinstance(default_limit, int) or not 1 <= default_limit <= max_limit:
            raise ValueError("invalid Mongo default_limit")
        if isinstance(max_time_ms, bool) or not isinstance(max_time_ms, int) or max_time_ms <= 0:
            raise ValueError("invalid Mongo max_time_ms")
        for name, value in (
            ("max_output_bytes", max_output_bytes),
            ("max_document_bytes", max_document_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if timeout_ms is not None and (
            isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0
        ):
            raise ValueError("timeout_ms must be positive")
        if socket_timeout_ms is not None and (
            isinstance(socket_timeout_ms, bool) or not isinstance(socket_timeout_ms, int) or socket_timeout_ms <= 0
        ):
            raise ValueError("socket_timeout_ms must be positive")
        self._client = client
        self._database = database
        self._collections = {str(key): value for key, value in dict(collections or {}).items()}
        self._allowed = frozenset(allowed)
        self.connection_id = str(connection_id)
        self.database_name = str(database_name)
        self.max_limit = int(max_limit)
        self.default_limit = int(default_limit)
        self.max_time_ms = int(max_time_ms)
        self.max_output_bytes = int(max_output_bytes)
        self.max_document_bytes = int(max_document_bytes)
        self._fixed_deadline = _normalize_deadline(deadline)
        self._fixed_timeout_ms = int(timeout_ms) if timeout_ms is not None else None
        self._socket_timeout_ms = socket_timeout_ms
        self._owner_client = self._resolve_owner_client()
        self._socket_timeout_configured, self._socket_timeout_value = _read_socket_timeout(
            self._owner_client, socket_timeout_ms
        )
        self._closed = False
        self._active_cursors: list[Any] = []
        self._scope = {"connection_id": self.connection_id, "engine": "mongodb", "database": self.database_name}
        self.policy = MongoReadPolicy(tuple(allowed))

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for cursor in list(self._active_cursors):
            _close_quietly(cursor)
        self._active_cursors.clear()
        # Only a client explicitly owned by this lease is closed.  A database
        # facade alone may be backed by the manual workbench's client.
        _close_quietly(self._owner_client if self._owner_client is not None else self._client)

    def execute(
        self,
        request: Mapping[str, Any],
        *,
        cancellation: Any = None,
        query_id: str | None = None,
        deadline: float | None = None,
    ) -> ToolResult:
        started = time.monotonic()
        try:
            supplied_deadline = _normalize_deadline(deadline)
        except ValueError:
            return _with_elapsed(
                _failure("ARGUMENT_INVALID", "宿主截止时间无效", query_id=query_id, scope=self._scope),
                _elapsed(started),
            )
        operation_deadline = _select_deadline(
            started,
            fixed_deadline=self._fixed_deadline,
            fixed_timeout_ms=self._fixed_timeout_ms,
            supplied_deadline=supplied_deadline,
        )
        if self._closed:
            return _with_elapsed(_failure("SESSION_NOT_FOUND", "只读连接已关闭", query_id=query_id, scope=self._scope), _elapsed(started))
        if _cancelled(cancellation):
            return _with_elapsed(_failure("CANCELLED", "本轮已停止，未启动工具", query_id=query_id, scope=self._scope), _elapsed(started))
        gate = _timeout_gate(
            deadline=operation_deadline,
            socket_timeout_configured=self._socket_timeout_configured,
            query_id=query_id,
            scope=self._scope,
        )
        if gate is not None:
            return _with_elapsed(gate, _elapsed(started))
        if not isinstance(request, Mapping):
            return _with_elapsed(_failure("ARGUMENT_INVALID", "Mongo 工具参数必须是对象", query_id=query_id, scope=self._scope), _elapsed(started))
        decision = self.policy.validate(request, max_limit=self.max_limit)
        if not decision.allowed:
            return _with_elapsed(_failure(decision.code, decision.message, query_id=query_id, scope=self._scope), _elapsed(started))
        args = dict(decision.arguments)
        collection_id = str(args["collection_id"])
        try:
            collection = self._resolve_collection(
                collection_id,
                cancellation=cancellation,
                deadline=operation_deadline,
            )
        except ReadOnlyClientError as exc:
            return _with_elapsed(
                _failure(
                    exc.code,
                    exc.message,
                    query_id=query_id,
                    scope=self._scope,
                    result_unknown=exc.result_unknown,
                ),
                _elapsed(started),
            )
        if collection is None:
            return _with_elapsed(_failure("SCOPE_DENIED", "集合不在宿主注入的授权范围内", query_id=query_id, scope=self._scope), _elapsed(started))
        try:
            operation = str(args["operation"]).casefold()
            if operation == "find":
                result = self._find(collection, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
            elif operation == "count":
                result = self._count(collection, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
            else:
                result = self._aggregate(collection, args, cancellation=cancellation, query_id=query_id, deadline=operation_deadline)
        except ReadOnlyClientError as exc:
            result = _failure(
                exc.code,
                exc.message,
                query_id=query_id,
                scope=self._scope,
                result_unknown=exc.result_unknown,
            )
        except (TimeoutError,):
            result = _failure("TIMEOUT", "Mongo 只读操作超时", query_id=query_id, scope=self._scope, result_unknown=True)
        except Exception:
            result = _failure("QUERY_FAILED", "Mongo 只读操作失败", query_id=query_id, scope=self._scope)
        cancelled = _cancelled(cancellation)
        expired = _deadline_expired(operation_deadline)
        if result.ok and (cancelled or expired):
            self.close()
            result = _failure(
                "CANCELLED" if cancelled else "TIMEOUT",
                "Mongo 读取完成后状态已改变，结果未知",
                query_id=query_id,
                scope=self._scope,
                result_unknown=True,
            )
        return _with_elapsed(result, _elapsed(started))

    read = execute
    run = execute

    def _resolve_collection(
        self,
        collection_id: str,
        *,
        cancellation: Any = None,
        deadline: float | None = None,
    ) -> Any:
        if collection_id.casefold() not in self._allowed:
            return None
        for key, value in self._collections.items():
            if key.casefold() == collection_id.casefold():
                return value
        if self._database is not None:
            getter = getattr(self._database, "get_collection", None)
            if callable(getter):
                try:
                    return _checked_call(
                        lambda: getter(collection_id),
                        cancellation=cancellation,
                        deadline=deadline,
                        owner=self,
                    )
                except ReadOnlyClientError:
                    raise
                except Exception:
                    return None
            # Collection lookup is host-fixed by the allowlist; the model
            # cannot cause this branch for an unapproved name.
            try:
                return _checked_call(
                    lambda: self._database[collection_id],
                    cancellation=cancellation,
                    deadline=deadline,
                    owner=self,
                )
            except ReadOnlyClientError:
                raise
            except Exception:
                return None
        return None

    def _resolve_owner_client(self) -> Any:
        if self._client is not None:
            return self._client
        for source in (self._database, *self._collections.values()):
            try:
                owner = getattr(source, "client", None)
            except Exception:
                owner = None
            if owner is not None:
                return owner
        return None

    def _effective_max_time_ms(self, deadline: float | None) -> int:
        """Keep the fixed server hint inside the host operation deadline."""

        remaining = _remaining_ms(deadline)
        if remaining is None:
            return self.max_time_ms
        return max(1, min(self.max_time_ms, remaining))

    def _find(
        self,
        collection: Any,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        limit = int(args.get("limit", self.default_limit))
        effective_max_time_ms = self._effective_max_time_ms(deadline)
        kwargs: dict[str, Any] = {
            "filter": args.get("filter", {}),
            "limit": limit + 1,
            "batch_size": min(limit + 1, 50),
            "max_time_ms": effective_max_time_ms,
            "allow_disk_use": False,
        }
        if "projection" in args:
            kwargs["projection"] = args["projection"]
        if "sort" in args:
            kwargs["sort"] = _normalize_sort(args["sort"])
        method = getattr(collection, "find", None)
        if not callable(method):
            return _failure("CAPABILITY_UNAVAILABLE", "Mongo find 能力未注入", query_id=query_id, scope=self._scope)
        timeout_option_supported = _call_accepts(method, "max_time_ms", "maxTimeMS")
        server_timeout_enforced = _call_has_explicit_option(method, "max_time_ms", "maxTimeMS")
        if not timeout_option_supported:
            kwargs.pop("max_time_ms", None)
        cursor = _checked_call(
            lambda: _driver_call(method, kwargs),
            cancellation=cancellation,
            deadline=deadline,
            owner=self,
            close_result=True,
        )
        self._active_cursors.append(cursor)
        try:
            docs_with_extra = _collect_cursor(
                cursor,
                limit,
                cancellation=cancellation,
                deadline=deadline,
                owner=self,
            )
        finally:
            _close_quietly(cursor)
            _remove_identity(self._active_cursors, cursor)
        has_more = len(docs_with_extra) > limit
        docs = docs_with_extra[:limit]
        encoded, encoded_truncated = _encode_documents(docs, self.max_document_bytes, self.max_output_bytes)
        return _safe_result(
            ok=True,
            data={"operation": "find", "collection_id": collection_id_safe(args["collection_id"]), "documents": encoded, "returned": len(encoded), "has_more": has_more},
            query_id=query_id,
            scope={**self._scope, "collection_id": collection_id_safe(args["collection_id"])},
            truncated=has_more or encoded_truncated,
            limits={
                "limit": limit,
                "server_fetch_limit": limit + 1,
                "batch_size": min(limit + 1, 50),
                "maxTimeMS": effective_max_time_ms if server_timeout_enforced else None,
                "server_timeout_enforced": server_timeout_enforced,
                "client_socket_timeout_configured": self._socket_timeout_configured,
                "deadline": deadline,
                "timeout_enforced": bool(server_timeout_enforced or (deadline is not None and self._socket_timeout_configured)),
                "timeout_status": _timeout_status(
                    deadline=deadline,
                    server_timeout_enforced=server_timeout_enforced,
                    socket_timeout_configured=self._socket_timeout_configured,
                ),
                "allowDiskUse": False,
            },
        )

    def _count(
        self,
        collection: Any,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        cap = int(args.get("limit", self.max_limit))
        cap = max(1, min(cap, self.max_limit))
        method = getattr(collection, "count_documents", None)
        if not callable(method):
            return _failure("CAPABILITY_UNAVAILABLE", "Mongo count 能力未注入", query_id=query_id, scope=self._scope)
        timeout_option_supported = _call_accepts(method, "maxTimeMS", "max_time_ms")
        server_timeout_enforced = _call_has_explicit_option(method, "maxTimeMS", "max_time_ms")
        effective_max_time_ms = self._effective_max_time_ms(deadline)
        call_kwargs: dict[str, Any] = {"filter": args.get("filter", {}), "limit": cap + 1}
        if timeout_option_supported:
            call_kwargs["maxTimeMS"] = effective_max_time_ms
        value = int(
            _checked_call(
                lambda: _driver_call(method, call_kwargs),
                cancellation=cancellation,
                deadline=deadline,
                owner=self,
            )
            or 0
        )
        capped = value > cap
        return _safe_result(
            ok=True,
            data={"operation": "count", "collection_id": collection_id_safe(args["collection_id"]), "count": min(value, cap)},
            query_id=query_id,
            scope={**self._scope, "collection_id": collection_id_safe(args["collection_id"])},
            truncated=capped,
            limits={
                "count_limit": cap,
                "server_limit": cap + 1,
                "maxTimeMS": effective_max_time_ms if server_timeout_enforced else None,
                "server_timeout_enforced": server_timeout_enforced,
                "client_socket_timeout_configured": self._socket_timeout_configured,
                "deadline": deadline,
                "timeout_enforced": bool(server_timeout_enforced or (deadline is not None and self._socket_timeout_configured)),
                "timeout_status": _timeout_status(
                    deadline=deadline,
                    server_timeout_enforced=server_timeout_enforced,
                    socket_timeout_configured=self._socket_timeout_configured,
                ),
                "exact": not capped,
            },
        )

    def _aggregate(
        self,
        collection: Any,
        args: Mapping[str, Any],
        *,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult:
        failure = self._check(cancellation, query_id, deadline)
        if failure:
            return failure
        limit = int(args.get("limit", self.default_limit))
        pipeline = [dict(stage) for stage in (args.get("pipeline") or [])]
        # The host appends the hard output guard.  A model cannot remove it or
        # set allowDiskUse/maxTimeMS through the request schema.
        pipeline.append({"$limit": limit + 1})
        method = getattr(collection, "aggregate", None)
        if not callable(method):
            return _failure("CAPABILITY_UNAVAILABLE", "Mongo aggregate 能力未注入", query_id=query_id, scope=self._scope)
        timeout_option_supported = _call_accepts(method, "maxTimeMS", "max_time_ms")
        server_timeout_enforced = _call_has_explicit_option(method, "maxTimeMS", "max_time_ms")
        effective_max_time_ms = self._effective_max_time_ms(deadline)
        call_kwargs: dict[str, Any] = {
            "pipeline": pipeline,
            "allowDiskUse": False,
            "batchSize": min(limit + 1, 50),
        }
        if timeout_option_supported:
            call_kwargs["maxTimeMS"] = effective_max_time_ms
        cursor = _checked_call(
            lambda: _driver_call(method, call_kwargs),
            cancellation=cancellation,
            deadline=deadline,
            owner=self,
            close_result=True,
        )
        self._active_cursors.append(cursor)
        try:
            docs_with_extra = _collect_cursor(
                cursor,
                limit,
                cancellation=cancellation,
                deadline=deadline,
                owner=self,
            )
        finally:
            _close_quietly(cursor)
            _remove_identity(self._active_cursors, cursor)
        has_more = len(docs_with_extra) > limit
        docs = docs_with_extra[:limit]
        encoded, encoded_truncated = _encode_documents(docs, self.max_document_bytes, self.max_output_bytes)
        return _safe_result(
            ok=True,
            data={"operation": "aggregate", "collection_id": collection_id_safe(args["collection_id"]), "documents": encoded, "returned": len(encoded), "has_more": has_more},
            query_id=query_id,
            scope={**self._scope, "collection_id": collection_id_safe(args["collection_id"])},
            truncated=has_more or encoded_truncated,
            limits={
                "limit": limit,
                "server_fetch_limit": limit + 1,
                "batchSize": min(limit + 1, 50),
                "maxTimeMS": effective_max_time_ms if server_timeout_enforced else None,
                "server_timeout_enforced": server_timeout_enforced,
                "client_socket_timeout_configured": self._socket_timeout_configured,
                "deadline": deadline,
                "timeout_enforced": bool(server_timeout_enforced or (deadline is not None and self._socket_timeout_configured)),
                "timeout_status": _timeout_status(
                    deadline=deadline,
                    server_timeout_enforced=server_timeout_enforced,
                    socket_timeout_configured=self._socket_timeout_configured,
                ),
                "allowDiskUse": False,
            },
        )

    def _check(
        self,
        cancellation: Any,
        query_id: str | None,
        deadline: float | None = None,
    ) -> ToolResult | None:
        if self._closed:
            return _failure("SESSION_NOT_FOUND", "只读连接已关闭", query_id=query_id, scope=self._scope)
        if _cancelled(cancellation):
            self.close()
            return _failure("CANCELLED", "本轮已停止，未启动新的读取", query_id=query_id, scope=self._scope)
        if _deadline_expired(deadline):
            return _failure(
                "TIMEOUT",
                "宿主只读截止时间已到",
                query_id=query_id,
                scope=self._scope,
                limits=self._timeout_limits({}, deadline),
            )
        return None

    def _timeout_limits(self, values: Mapping[str, Any], deadline: float | None) -> dict[str, Any]:
        return {
            **dict(values),
            "deadline": deadline,
            "client_socket_timeout_configured": self._socket_timeout_configured,
            "server_timeout_enforced": False,
            "timeout_enforced": bool(deadline is not None and self._socket_timeout_configured),
            "timeout_status": _timeout_status(
                deadline=deadline,
                server_timeout_enforced=False,
                socket_timeout_configured=self._socket_timeout_configured,
            ),
        }


def _parse_scan_response(response: Any) -> tuple[int, Sequence[Any]]:
    if not isinstance(response, tuple) or len(response) != 2:
        raise TypeError("Redis scan response is invalid")
    cursor, values = response
    if isinstance(cursor, Mapping):
        # A cluster map is parsed by the cluster caller; a standalone driver
        # must not silently accept it as a scalar cursor.
        raise TypeError("cluster cursor returned for standalone Redis")
    return int(cursor or 0), values or []


def _parse_cluster_scan_response(response: Any) -> tuple[dict[str, int], Sequence[Any]]:
    if not isinstance(response, tuple) or len(response) != 2:
        raise TypeError("Redis cluster scan response is invalid")
    cursor, values = response
    if isinstance(cursor, Mapping):
        result: dict[str, int] = {}
        for index, (key, value) in enumerate(cursor.items()):
            if index >= DEFAULT_MAX_CURSOR_STATE_ITEMS:
                raise ReadOnlyClientError("ARGUMENT_INVALID", "Redis 集群游标条目过多")
            result[str(key)[:512]] = int(value or 0)
        return result, values or []
    return {"default": int(cursor or 0)}, values or []


def _encode_items(values: Sequence[Any], max_bytes: int) -> tuple[list[Any], bool]:
    result: list[Any] = []
    used = 0
    truncated = False
    for value in values:
        encoded = encode_bounded(value, max_bytes=max(1, min(max_bytes, max_bytes - used)))
        if used + encoded.bytes_used > max_bytes and result:
            truncated = True
            break
        result.append(encoded.value)
        used += encoded.bytes_used
        truncated = truncated or encoded.truncated
    if len(result) < len(values):
        truncated = True
    return result, truncated


def _encode_documents(values: Sequence[Any], max_document_bytes: int, max_output_bytes: int) -> tuple[list[Any], bool]:
    result: list[Any] = []
    used = 0
    truncated = False
    for value in values:
        remaining = max_output_bytes - used
        if remaining <= 0:
            truncated = True
            break
        encoded = encode_bounded(value, max_bytes=min(max_document_bytes, remaining))
        if used + encoded.bytes_used > max_output_bytes and result:
            truncated = True
            break
        result.append(encoded.value)
        used += encoded.bytes_used
        truncated = truncated or encoded.truncated
    if len(result) < len(values):
        truncated = True
    return result, truncated


def _normalize_sort(value: Any) -> list[tuple[str, int]]:
    if not isinstance(value, Mapping):
        raise ReadOnlyClientError("ARGUMENT_INVALID", "sort 必须是对象")
    result: list[tuple[str, int]] = []
    for index, (key, direction) in enumerate(value.items()):
        if index >= 100:
            raise ReadOnlyClientError("ARGUMENT_INVALID", "sort 字段过多")
        if not isinstance(key, str) or not key or len(key) > 512:
            raise ReadOnlyClientError("ARGUMENT_INVALID", "sort 字段无效")
        if isinstance(direction, bool) or direction not in (-1, 1):
            raise ReadOnlyClientError("ARGUMENT_INVALID", "sort 方向必须是 1 或 -1")
        result.append((key, int(direction)))
    return result


def collection_id_safe(value: Any) -> str:
    text = str(value or "").strip()
    return text[:512]


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _remove_identity(items: list[Any], target: Any) -> None:
    for index, item in enumerate(items):
        if item is target:
            items.pop(index)
            return


def _with_elapsed(result: ToolResult, elapsed_ms: int) -> ToolResult:
    return ToolResult(
        ok=result.ok,
        code=result.code,
        data=result.data,
        evidence_id=result.evidence_id,
        query_id=result.query_id,
        scope=result.scope,
        truncated=result.truncated,
        limits=result.limits,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "CursorCodec",
    "MongoReadOnlyFacade",
    "OpaqueCursorCodec",
    "ReadOnlyClientError",
    "RedisReadOnlyFacade",
]
