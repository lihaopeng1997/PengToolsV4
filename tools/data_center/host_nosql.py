"""Host-side construction and routing for read-only Redis/Mongo facades.

The legacy connection profile contains addresses, URI text and encrypted
secrets.  Those values are needed only while a host creates a fresh client;
they are never retained in the public target, lease, router, result scope or
their representations.  The model receives only the structured request
handled by :mod:`readonly_nosql_clients`.

This module deliberately has no configuration or driver work at import time.
Callers inject profile/secret loaders and client factories in production and
tests.  The default client factories are lazy fallbacks for the existing
Redis and Mongo drivers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
import threading
from typing import Any

from .contracts import ToolResult
from .readonly_nosql_clients import MongoReadOnlyFacade, RedisReadOnlyFacade


DEFAULT_TIMEOUT_MS = 15_000
_DEFAULT_REDIS_PORT = 6379
_DEFAULT_MONGO_PORT = 27017
_MISSING = object()
_REVISION_IGNORED_KEYS = frozenset({
    "profile_revision",
    "revision",
    "name",
    "label",
    "display_name",
    "group_path",
    "environment",
    "description",
})
_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "passphrase",
    "cookie",
)
_SAFE_ERROR_CODES = frozenset(
    {
        "NOSQL_HOST_ERROR",
        "PROFILE_UNAVAILABLE",
        "PROFILE_INVALID",
        "PROFILE_INCOMPLETE",
        "PROFILE_MISMATCH",
        "PROFILE_NOT_FOUND",
        "PROFILE_AMBIGUOUS",
        "SCOPE_DENIED",
        "SECRET_UNAVAILABLE",
        "CLIENT_CREATE_FAILED",
        "CAPABILITY_UNAVAILABLE",
        "UNSUPPORTED_PROVIDER",
        "UNSUPPORTED_COMBINATION",
        "SESSION_NOT_FOUND",
        "QUERY_FAILED",
        "ARGUMENT_INVALID",
        "POLICY_DENIED",
        "AUTH_FAILED",
        "CANCEL_PENDING",
        "CANCELLED",
        "TIMEOUT",
        "TIMEOUT_UNAVAILABLE",
    }
)
_SENSITIVE_OUTPUT_KEYS = frozenset(
    {
        "uri",
        "url",
        "endpoint",
        "address",
        "host",
        "hostname",
        "dsn",
        "connection_string",
        "connection_uri",
    }
)


class NoSQLHostError(RuntimeError):
    """Stable host error which never includes profile or driver text."""

    code = "NOSQL_HOST_ERROR"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        del message
        candidate = str(code or type(self).code).strip().upper()
        self.code = candidate if candidate in _SAFE_ERROR_CODES else type(self).code
        # Error text is deliberately just a stable code.  Driver/profile
        # messages can contain a URI or credential even when a caller did not
        # intend to expose them.
        super().__init__(self.code)


class NoSQLProfileError(NoSQLHostError):
    code = "PROFILE_UNAVAILABLE"


class NoSQLScopeError(NoSQLHostError):
    code = "SCOPE_DENIED"


class NoSQLSecretError(NoSQLHostError):
    code = "SECRET_UNAVAILABLE"


class NoSQLClientError(NoSQLHostError):
    code = "CLIENT_CREATE_FAILED"


class NoSQLCapabilityError(NoSQLHostError):
    code = "CAPABILITY_UNAVAILABLE"


def _text(value: Any) -> str:
    """Normalize a profile scalar without retaining the source object."""

    if value is _MISSING:
        return ""
    return str(value or "").strip()


def _value(source: Any, *names: str) -> Any:
    """Read a named profile field without calling arbitrary repr/serializers."""

    for name in names:
        if isinstance(source, Mapping):
            try:
                value = source.get(name, _MISSING)
            except Exception:
                value = _MISSING
        else:
            try:
                value = getattr(source, name, _MISSING)
            except Exception:
                value = _MISSING
        if value is not _MISSING:
            return value
    return _MISSING


def _required(source: Any, *names: str) -> str:
    values: list[str] = []
    for name in names:
        value = _value(source, name)
        if value is not _MISSING and _text(value):
            values.append(_text(value))
    if not values:
        raise NoSQLProfileError(code="PROFILE_MISMATCH")
    if len(set(values)) > 1:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    return values[0]


def _optional_alias(source: Any, *names: str) -> str:
    """Read one optional alias while rejecting conflicting non-empty values."""

    values: list[str] = []
    for name in names:
        value = _value(source, name)
        if value is not _MISSING and _text(value):
            values.append(_text(value))
    if len(set(values)) > 1:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    return values[0] if values else ""


def _canonical_provider(value: Any) -> str:
    raw = _text(value).casefold().replace("-", "_")
    aliases = {"mongo": "mongodb", "mongo_db": "mongodb", "redis_cluster": "redis"}
    raw = aliases.get(raw, raw)
    if raw not in {"redis", "mongodb"}:
        raise NoSQLProfileError(code="UNSUPPORTED_PROVIDER")
    return raw


def _provider_from_profile(profile: Any) -> str:
    values: list[str] = []
    for name in ("provider", "engine", "dialect"):
        raw = _value(profile, name)
        value = _text(raw)
        if value:
            values.append(value)
    if not values:
        raise NoSQLProfileError(code="PROFILE_MISMATCH")
    canonical = [_canonical_provider(value) for value in values]
    if len(set(canonical)) > 1:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    return canonical[0]


def _canonical_mode(value: Any, provider: str, *, infer_uri: bool = False, host: str = "") -> str:
    provider = _canonical_provider(provider)
    raw = _text(value).casefold().replace("-", "_")
    if not raw and infer_uri and provider == "mongodb" and host.casefold().startswith("mongodb+srv://"):
        return "srv"
    if not raw:
        return "standalone"
    if raw == "replicaset":
        raw = "replica_set"
    if raw not in {"standalone", "cluster", "replica_set", "srv"}:
        raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
    if provider == "redis" and raw in {"replica_set", "srv"}:
        raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
    if provider == "mongodb":
        is_srv = host.casefold().startswith("mongodb+srv://")
        if host and raw == "srv" and not is_srv:
            raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
        if host and raw != "srv" and is_srv:
            # An SRV URI is a distinct target topology.  Do not silently
            # collapse it into the generic cluster/standalone labels.
            raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
    return raw


def _profile_seed_nodes(profile: Any, provider: str, mode: str) -> tuple[dict[str, Any], ...]:
    """Normalize the persisted seed shape while retaining every cluster node."""

    raw_seeds = _value(profile, "seed_nodes")
    seeds: list[dict[str, Any]] = []
    if raw_seeds is not _MISSING and raw_seeds is not None:
        if not isinstance(raw_seeds, Sequence) or isinstance(raw_seeds, (str, bytes, bytearray)):
            raise NoSQLProfileError(code="PROFILE_INVALID")
        for entry in raw_seeds:
            if not isinstance(entry, Mapping):
                raise NoSQLProfileError(code="PROFILE_INVALID")
            host = _text(entry.get("host"))
            if not host:
                raise NoSQLProfileError(code="PROFILE_INVALID")
            port = _port(entry.get("port"), _DEFAULT_REDIS_PORT if provider == "redis" else _DEFAULT_MONGO_PORT)
            seeds.append({"host": host, "port": port})
    if seeds:
        return tuple(seeds)

    host = _text(_value(profile, "host"))
    if provider == "mongodb" and _is_mongo_uri(host):
        return ()
    # Match the legacy db_connect normalizer: an omitted host means the
    # local default, while malformed explicit seed entries still fail closed.
    host = host or "127.0.0.1"
    default_port = _DEFAULT_REDIS_PORT if provider == "redis" else _DEFAULT_MONGO_PORT
    port = _port(_value(profile, "port"), default_port)
    return ({"host": host, "port": port},)


def _port(value: Any, default: int) -> int:
    raw = default if value is _MISSING or value in (None, "") else value
    if isinstance(raw, bool):
        raise NoSQLProfileError(code="PROFILE_INVALID")
    try:
        port = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise NoSQLProfileError(code="PROFILE_INVALID") from None
    if not 1 <= port <= 65535:
        raise NoSQLProfileError(code="PROFILE_INVALID")
    return port


def _database_index(profile: Any) -> int:
    raw = _value(profile, "database", "db")
    if raw is _MISSING or raw in (None, ""):
        return 0
    if isinstance(raw, bool):
        raise NoSQLProfileError(code="PROFILE_INVALID")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise NoSQLProfileError(code="PROFILE_INVALID") from None
    if value < 0:
        raise NoSQLProfileError(code="PROFILE_INVALID")
    return value


def _database_name(profile: Any, supplied: str | None = None) -> str:
    value = _text(supplied) if supplied is not None else ""
    if not value:
        value = _optional_alias(profile, "database", "database_name", "db_name")
    if not value:
        raise NoSQLProfileError(code="PROFILE_MISMATCH")
    return value


def _is_mongo_uri(value: str) -> bool:
    folded = _text(value).casefold()
    return folded.startswith("mongodb://") or folded.startswith("mongodb+srv://")


def _mongo_uri(profile: Any) -> str:
    """Return one canonical Mongo URI while rejecting conflicting spellings."""

    values: list[str] = []
    for name in ("uri", "connection_uri", "connection_string"):
        raw = _value(profile, name)
        if raw is not _MISSING and _text(raw):
            values.append(_text(raw))
    host = _text(_value(profile, "host"))
    if _is_mongo_uri(host):
        values.append(host)
    if len(set(values)) > 1:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    uri = values[0] if values else ""
    if uri and not _is_mongo_uri(uri):
        raise NoSQLProfileError(code="PROFILE_INVALID")
    return uri


def _validate_topology(
    profile: Any,
    provider: str,
    mode: str,
    uri: str,
    seeds: Sequence[Mapping[str, Any]],
) -> None:
    """Reject combinations that would make a driver lose target identity."""

    if provider == "redis":
        if mode not in {"standalone", "cluster"}:
            raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
        if mode == "standalone" and len(seeds) > 1:
            raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
        return
    if mode == "srv":
        if not uri.casefold().startswith("mongodb+srv://"):
            raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
        raw_seeds = _value(profile, "seed_nodes")
        if raw_seeds not in (_MISSING, None, ()) and raw_seeds != []:
            raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
    elif uri.casefold().startswith("mongodb+srv://"):
        raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")
    # All replica-set spellings must identify one value.  A conflicting alias
    # would otherwise let the driver receive a different topology name from
    # the revision that bound the target.
    _replica_set_name(profile)
    if mode == "standalone" and len(seeds) > 1:
        raise NoSQLProfileError(code="UNSUPPORTED_COMBINATION")


def _is_secret_key(key: Any) -> bool:
    text = str(key or "").strip().casefold().replace("-", "_")
    return any(part in text for part in _SECRET_KEY_PARTS)


def _canonical_revision_value(value: Any, *, key: str = "") -> Any:
    """Build a deterministic revision without retaining secret material."""

    if _is_secret_key(key):
        if value in (None, ""):
            return None
        try:
            raw = str(value).encode("utf-8", errors="surrogatepass")
        except Exception:
            raw = b"<secret>"
        return {"secret_sha256": hashlib.sha256(raw).hexdigest()}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        keys = sorted((str(item) for item in value.keys()), key=lambda item: (item.casefold(), item))
        for raw_key in keys:
            if raw_key.casefold() in _REVISION_IGNORED_KEYS:
                continue
            try:
                child = value.get(raw_key)
            except Exception:
                child = None
            result[raw_key] = _canonical_revision_value(child, key=raw_key)
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical_revision_value(item, key=key) for item in value]
    if isinstance(value, (set, frozenset)):
        result = [_canonical_revision_value(item, key=key) for item in value]
        return sorted(result, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return str(value)


def _revision_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize identity spellings before hashing the current profile."""

    data = dict(profile)
    try:
        connection_id = _optional_alias(data, "connection_id", "id")
    except NoSQLHostError:
        connection_id = ""
    if connection_id:
        data["connection_id"] = connection_id
        data.pop("id", None)
    try:
        provider = _provider_from_profile(data)
    except NoSQLHostError:
        provider = ""
    if provider:
        data["provider"] = provider
        data.pop("engine", None)
        data.pop("dialect", None)
        uri = ""
        try:
            uri = _mongo_uri(data) if provider == "mongodb" else ""
        except NoSQLHostError:
            pass
        host = uri or _text(_value(data, "host"))
        try:
            data["mode"] = _canonical_mode(
                _value(data, "mode"),
                provider,
                infer_uri=True,
                host=host,
            )
        except NoSQLHostError:
            pass
    return data


def compute_profile_revision(profile: Mapping[str, Any]) -> str:
    """Compute a stable profile revision without exposing credentials."""

    if not isinstance(profile, Mapping):
        raise ValueError("profile must be a mapping")
    payload = _canonical_revision_value(_revision_profile(profile))
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replica_set_name(profile: Any) -> str:
    return _optional_alias(profile, "replica_set_name", "replicaSet", "replica_set")


def _auth_mode(profile: Any) -> str:
    raw = _text(_value(profile, "auth_mode")).casefold()
    if raw in {"none", "password", "acl"}:
        return raw
    username = _text(_value(profile, "username"))
    password = _text(
        _value(
            profile,
            "password",
            "password_ciphertext",
            "password_token",
            "secret_ciphertext",
            "secret_token",
            "credential_token",
        )
    )
    if username:
        return "acl"
    if password:
        return "password"
    return "none"


def _profile_needs_secret(profile: Any, provider: str) -> bool:
    """Return whether this persisted profile requires a host secret."""

    if provider == "redis":
        return _auth_mode(profile) != "none"
    return bool(
        _text(_value(profile, "username"))
        or _text(_value(profile, "password"))
        or _text(_value(profile, "password_ciphertext"))
        or _text(_value(profile, "password_token"))
        or _text(_value(profile, "secret_ciphertext"))
        or _text(_value(profile, "secret_token"))
        or _text(_value(profile, "credential_token"))
    )


def _validate_positive_ms(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be positive")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be positive") from None
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _seconds(milliseconds: int) -> float:
    return max(0.001, milliseconds / 1000.0)


def _timeout_seconds_option(options: Mapping[str, Any], *, default_ms: int) -> float:
    raw_seconds = options.get("socket_timeout")
    if raw_seconds is not None:
        try:
            value = float(raw_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("socket timeout is invalid") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("socket timeout is invalid")
        return value
    raw_ms = options.get("socket_timeout_ms", options.get("socketTimeoutMS"))
    if raw_ms is None:
        return _seconds(default_ms)
    return _seconds(_validate_positive_ms(raw_ms, "socket_timeout_ms"))


def _timeout_ms_option(options: Mapping[str, Any], *names: str, default_ms: int) -> int:
    for name in names:
        raw = options.get(name)
        if raw is not None:
            return _validate_positive_ms(raw, name)
    return default_ms


def _call_injected(callback: Any, values: Mapping[str, Any], fallback: tuple[Any, ...] = ()) -> Any:
    """Call a small injected fake/adapter using only declared parameters."""

    if not callable(callback):
        callback = getattr(callback, "connect", None)
    if not callable(callback):
        raise TypeError("injected callback is not callable")
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(*fallback)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        return callback(**dict(values))
    positional: list[Any] = []
    for item in params:
        if item.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            continue
        if item.name in values:
            positional.append(values[item.name])
        elif item.default is inspect.Parameter.empty:
            # The fallback is deliberately positional and contains only
            # values the caller explicitly designated for this callback.
            if len(positional) < len(fallback):
                positional.append(fallback[len(positional)])
            else:
                raise TypeError("injected callback signature is unsupported")
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


def _close_distinct(*values: Any) -> None:
    """Close each distinct owned object while suppressing driver text/errors."""

    seen: set[int] = set()
    for value in values:
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        _close_quietly(value)


def _close_failed_bundle(
    facade: Any,
    owner: Any,
    client: Any,
    *,
    facade_owns_owner: bool,
) -> None:
    """Release partially-built leases without double-closing owners."""

    if facade is not None:
        _close_quietly(facade)
        if facade_owns_owner:
            # A Mongo facade owns the client passed to it.  A bundle may also
            # expose a distinct raw client; that object still belongs to the
            # host and must be released on a failed facade/lease build.
            _close_distinct(client if client is not owner and client is not facade else None)
        else:
            _close_distinct(owner if owner is not facade and owner is not client else None)
        return
    _close_distinct(owner, client)


def _timeout_flags(value: Any) -> tuple[bool, bool]:
    """Inspect driver-owned timeout settings without creating connections."""

    socket_seen = False
    server_seen = False
    queue: list[Any] = list(value) if isinstance(value, (tuple, list)) else [value]
    visited: set[int] = set()
    socket_names = {"socket_timeout", "socket_timeout_ms", "socketTimeoutMS", "read_timeout", "readTimeout"}
    server_names = {"server_selection_timeout", "server_selection_timeout_ms", "serverSelectionTimeoutMS"}
    while queue and len(visited) < 32:
        candidate = queue.pop(0)
        if candidate is None or id(candidate) in visited:
            continue
        visited.add(id(candidate))
        if isinstance(candidate, Mapping):
            items = candidate.items()
        else:
            try:
                items = ((name, getattr(candidate, name, _MISSING)) for name in (*socket_names, *server_names))
            except Exception:
                items = ()
        for name, raw in items:
            if raw is _MISSING or isinstance(raw, bool):
                continue
            try:
                valid = math.isfinite(float(raw)) and float(raw) > 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                continue
            if name in socket_names:
                socket_seen = True
            if name in server_names:
                server_seen = True
        for attr in (
            "connection_pool",
            "options",
            "_options",
            "pool_options",
            "connection_kwargs",
            "_connection_kwargs",
        ):
            try:
                child = getattr(candidate, attr, None)
            except Exception:
                child = None
            if child is not None and id(child) not in visited:
                queue.append(child)
    return socket_seen, server_seen


def _contains_sensitive_text(value: str) -> bool:
    folded = value.casefold()
    return any(
        marker in folded
        for marker in (
            "mongodb://",
            "mongodb+srv://",
            "redis://",
            "rediss://",
            "password=",
            "passwd=",
            "secret=",
            "token=",
            "credential=",
        )
    )


def _sanitize_output(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Keep driver/fake output structured without exposing connection data."""

    if _is_secret_key(key) or key.strip().casefold().replace("-", "_") in _SENSITIVE_OUTPUT_KEYS:
        return "[redacted]"
    if depth > 8:
        return "[redacted]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            safe_key = str(raw_key)
            if _contains_sensitive_text(safe_key):
                safe_key = "[redacted]"
            result[safe_key] = _sanitize_output(raw_value, key=safe_key, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_output(item, depth=depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_sanitize_output(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return "[redacted]" if _contains_sensitive_text(value) else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[redacted]"


def _safe_result_code(value: Any, *, ok: bool) -> str:
    code = _text(value).upper()
    if code in _SAFE_ERROR_CODES or code == "OK":
        return code
    return "OK" if ok else "QUERY_FAILED"


def _safe_identifier(value: Any) -> str | None:
    text = _text(value)
    if not text or _contains_sensitive_text(text):
        return None
    return text


def _public_text(value: Any, name: str, *, required: bool = False) -> str:
    text = _text(value)
    if required and not text:
        raise ValueError(f"{name} is required")
    if _contains_sensitive_text(text):
        raise ValueError(f"{name} is invalid")
    return text


def _safe_elapsed_ms(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(float(value)) or value < 0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return value


def _safe_result_failure(code: str, message: str, *, scope: Mapping[str, Any] | None = None, query_id: str | None = None) -> ToolResult:
    safe_code = _safe_result_code(code, ok=False)
    safe_message = _sanitize_output(message)
    if not isinstance(safe_message, str):
        safe_message = "只读操作失败"
    return ToolResult(
        ok=False,
        code=safe_code,
        data={"message": safe_message},
        query_id=_safe_identifier(query_id),
        scope=_sanitize_output(dict(scope or {})),
    )


@dataclass(frozen=True, slots=True)
class NoSQLTarget:
    """Immutable host scope; no address, URI or credential is stored."""

    connection_id: str
    profile_revision: str
    provider: str
    mode: str = "standalone"
    database_name: str = ""
    allowed_collection_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        connection_id = _public_text(self.connection_id, "connection_id", required=True)
        revision = _public_text(self.profile_revision, "profile_revision", required=True)
        provider = _canonical_provider(self.provider)
        mode = _canonical_mode(self.mode, provider)
        database_name = _public_text(self.database_name, "database_name")
        ids = tuple(dict.fromkeys(_text(item).casefold() for item in (self.allowed_collection_ids or ()) if _text(item)))
        if provider == "mongodb" and not ids:
            raise ValueError("Mongo allowed_collection_ids must not be empty")
        if provider == "mongodb" and not database_name:
            raise ValueError("Mongo database_name is required")
        object.__setattr__(self, "connection_id", connection_id)
        object.__setattr__(self, "profile_revision", revision)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "database_name", database_name)
        object.__setattr__(self, "allowed_collection_ids", ids)

    @property
    def scope(self) -> dict[str, Any]:
        value = {
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "provider": self.provider,
            "mode": self.mode,
        }
        if self.database_name:
            value["database"] = self.database_name
        return value

    def as_dict(self) -> dict[str, Any]:
        value = self.scope
        if self.allowed_collection_ids:
            value["allowed_collection_ids"] = list(self.allowed_collection_ids)
        return value

    def __repr__(self) -> str:
        return (
            "NoSQLTarget("
            f"connection_id={self.connection_id!r}, profile_revision={self.profile_revision!r}, "
            f"provider={self.provider!r}, mode={self.mode!r}, database_name={self.database_name!r}, "
            f"allowed_collection_ids={self.allowed_collection_ids!r})"
        )


@dataclass(frozen=True, slots=True)
class NoSQLClientBundle:
    """Optional explicit result shape for an injected client factory."""

    client: Any
    owner: Any = None
    database: Any = None
    collections: Mapping[str, Any] | None = None

    def __repr__(self) -> str:
        return "NoSQLClientBundle(<owned client>, <owned owner>, <owned database>, <owned collections>)"


class HostNoSQLLease:
    """One host-owned client/facade pair with an idempotent close path."""

    def __init__(
        self,
        target: NoSQLTarget,
        facade: Any,
        owner: Any,
        client: Any,
        *,
        facade_owns_owner: bool = False,
    ) -> None:
        self.target = target
        self._facade = facade
        self._owner = owner
        self._client = client
        self._facade_owns_owner = bool(facade_owns_owner)
        self._closed = False
        self._lock = threading.Lock()

    @property
    def facade(self) -> Any:
        return self._facade

    @property
    def provider(self) -> str:
        return self.target.provider

    @property
    def mode(self) -> str:
        return self.target.mode

    @property
    def connection_id(self) -> str:
        return self.target.connection_id

    @property
    def profile_revision(self) -> str:
        return self.target.profile_revision

    @property
    def database_name(self) -> str:
        return self.target.database_name

    @property
    def allowed_collection_ids(self) -> tuple[str, ...]:
        return self.target.allowed_collection_ids

    @property
    def scope(self) -> dict[str, Any]:
        return self.target.scope

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, request: Mapping[str, Any], **kwargs: Any) -> ToolResult:
        with self._lock:
            if self._closed:
                return _safe_result_failure("SESSION_NOT_FOUND", "只读连接已关闭", scope=self.target.scope, query_id=kwargs.get("query_id"))
        try:
            result = ToolResult.from_value(self._facade.execute(request, **kwargs))
        except Exception:
            return _safe_result_failure("QUERY_FAILED", "只读操作失败", scope=self.target.scope, query_id=kwargs.get("query_id"))
        # The host target is the complete public scope.  Do not merge fields
        # from a driver/fake result because those fields may contain a URI,
        # address, or a credential-bearing diagnostic.
        try:
            safe_data = _sanitize_output(result.data)
            safe_limits = _sanitize_output(result.limits)
            if not isinstance(safe_limits, Mapping):
                safe_limits = {}
            result_ok = bool(result.ok)
            safe_truncated = bool(result.truncated)
        except Exception:
            return _safe_result_failure("QUERY_FAILED", "只读操作失败", scope=self.target.scope, query_id=kwargs.get("query_id"))
        return ToolResult(
            ok=result_ok,
            code=_safe_result_code(result.code, ok=result_ok),
            data=safe_data,
            evidence_id=_safe_identifier(result.evidence_id),
            query_id=_safe_identifier(result.query_id) or _safe_identifier(kwargs.get("query_id")),
            scope=self.target.scope,
            truncated=safe_truncated,
            limits=dict(safe_limits),
            elapsed_ms=_safe_elapsed_ms(result.elapsed_ms),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        _close_quietly(self._facade)
        if self._facade_owns_owner:
            _close_distinct(
                self._client
                if self._client is not self._owner and self._client is not self._facade
                else None
            )
        elif self._owner is not self._facade and self._owner is not self._client:
            _close_quietly(self._owner)

    def __enter__(self) -> "HostNoSQLLease":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"HostNoSQLLease(target={self.target!r}, closed={self.closed!r})"


class HostNoSQLFactory:
    """Resolve one profile and create a fresh read-only NoSQL lease."""

    def __init__(
        self,
        profile: Mapping[str, Any] | Any | None = None,
        *,
        connection_id: str | None = None,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        database_name: str | None = None,
        allowed_collection_ids: Sequence[str] | None = None,
        profile_loader: Callable[..., Any] | None = None,
        loader: Callable[..., Any] | None = None,
        secret_loader: Callable[..., Any] | None = None,
        redis_client_factory: Any = None,
        mongo_client_factory: Any = None,
        redis_factory: Any = None,
        mongo_factory: Any = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        socket_timeout_ms: int | None = None,
        server_selection_timeout_ms: int | None = None,
        facade_options: Mapping[str, Any] | None = None,
    ) -> None:
        if profile is not None and (profile_loader is not None or loader is not None):
            raise ValueError("choose profile or profile_loader, not both")
        if profile_loader is not None and loader is not None:
            raise ValueError("choose profile_loader or loader, not both")
        selected_loader = profile_loader or loader
        if profile is None and selected_loader is None:
            raise ValueError("profile or profile_loader is required")
        if profile is not None and not isinstance(profile, Mapping) and not hasattr(profile, "__dict__"):
            raise ValueError("profile must be a mapping or profile object")
        selected_id = _text(connection_id) if connection_id is not None else ""
        selected_revision = _text(profile_revision) if profile_revision is not None else ""
        revision_was_supplied = bool(selected_revision)
        selected_provider = _text(provider)
        selected_mode = _text(mode)
        selected_database = _text(database_name)
        if profile is not None:
            selected_id = selected_id or _optional_alias(profile, "connection_id", "id")
            selected_revision = selected_revision or _optional_alias(profile, "profile_revision", "revision")
            selected_provider = selected_provider or _provider_from_profile(profile)
            selected_mode = selected_mode or _optional_alias(profile, "mode")
            if not selected_database and selected_provider.casefold() in {"mongo", "mongodb", "mongo_db"}:
                selected_database = _optional_alias(profile, "database", "database_name", "db_name")
            if not revision_was_supplied:
                try:
                    selected_revision = compute_profile_revision(dict(profile))
                except Exception:
                    raise ValueError("profile_revision is unavailable") from None
        if not selected_id or not selected_revision or not selected_provider:
            raise ValueError("connection_id, profile_revision and provider are required")
        canonical_provider = _canonical_provider(selected_provider)
        if profile is not None and canonical_provider == "mongodb":
            host_hint = _mongo_uri(profile) or _text(_value(profile, "host"))
        else:
            host_hint = _text(_value(profile, "host")) if profile is not None else ""
        canonical_mode = _canonical_mode(selected_mode, canonical_provider, infer_uri=True, host=host_hint)
        ids = tuple(str(item).strip() for item in (allowed_collection_ids or ()) if str(item).strip())
        self.target = NoSQLTarget(
            selected_id,
            selected_revision,
            canonical_provider,
            canonical_mode,
            selected_database,
            ids,
        )
        self._profile = dict(profile) if isinstance(profile, Mapping) else profile
        self._profile_loader = selected_loader
        self._secret_loader = secret_loader
        self._redis_factory = (
            redis_client_factory
            if redis_client_factory is not None
            else redis_factory
            if redis_factory is not None
            else _default_redis_client_factory
        )
        self._mongo_factory = (
            mongo_client_factory
            if mongo_client_factory is not None
            else mongo_factory
            if mongo_factory is not None
            else _default_mongo_client_factory
        )
        self.timeout_ms = _validate_positive_ms(timeout_ms, "timeout_ms")
        self.socket_timeout_ms = _validate_positive_ms(socket_timeout_ms, "socket_timeout_ms") if socket_timeout_ms is not None else self.timeout_ms
        self.server_selection_timeout_ms = _validate_positive_ms(server_selection_timeout_ms, "server_selection_timeout_ms") if server_selection_timeout_ms is not None else self.timeout_ms
        protected_facade_options = {
            "client",
            "database",
            "collections",
            "connection_id",
            "profile_revision",
            "mode",
            "database_name",
            "allowed_collection_ids",
            "socket_timeout_ms",
            "timeout_ms",
            "deadline",
        }
        self._facade_options = {
            key: value
            for key, value in dict(facade_options or {}).items()
            if key not in protected_facade_options
        }
        self._active: set[HostNoSQLLease] = set()
        self._closed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"HostNoSQLFactory(target={self.target!r})"

    def connect(
        self,
        connection_id: str | None = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        **kwargs: Any,
    ) -> HostNoSQLLease:
        # Session metadata is accepted for compatibility with the data-center
        # worker factory protocol.  No secret, address, or session metadata is
        # forwarded to the underlying NoSQL facade.
        del session_id, generation
        if kwargs:
            raise NoSQLScopeError(code="SCOPE_DENIED")
        with self._lock:
            if self._closed:
                raise NoSQLHostError(code="SESSION_NOT_FOUND")
        supplied = _text(connection_id)
        if supplied and supplied != self.target.connection_id:
            raise NoSQLScopeError(code="SCOPE_DENIED")

        profile: Any = None
        secret: Any = None
        client: Any = None
        owner: Any = None
        facade: Any = None
        facade_owns_owner = False
        try:
            profile = self._load_profile()
            self._verify_profile(profile)
            secret = self._load_secret(profile)
            options = self._client_options(profile)
            factory = self._redis_factory if self.target.provider == "redis" else self._mongo_factory
            factory_values = {
                "profile": profile,
                "secret": secret,
                "password": secret,
                "connection_id": self.target.connection_id,
                "profile_revision": self.target.profile_revision,
                "provider": self.target.provider,
                "mode": self.target.mode,
                "database_name": self.target.database_name,
                "options": options,
                "client_options": options,
            }
            # Make the bounded provider options available both as one mapping
            # and as named arguments.  This supports small injected fakes and
            # driver wrappers without requiring them to copy private profile
            # fields or accept arbitrary model input.
            factory_values.update(options)
            try:
                produced = _call_injected(
                    factory,
                    factory_values,
                    fallback=(profile, secret),
                )
            except Exception:
                raise NoSQLClientError(code="CLIENT_CREATE_FAILED") from None
            client, owner, database, collections = _unpack_client(produced)
            if client is None:
                raise NoSQLClientError(code="CLIENT_CREATE_FAILED")
            if self.target.provider == "redis":
                facade = RedisReadOnlyFacade(
                    client,
                    connection_id=self.target.connection_id,
                    profile_revision=self.target.profile_revision,
                    mode=self.target.mode,
                    socket_timeout_ms=self.socket_timeout_ms
                    if _timeout_flags((owner, client))[0]
                    else None,
                    **self._facade_options,
                )
            else:
                database = database if database is not None else _resolve_database(client, self.target.database_name)
                collections = _resolve_collections(
                    database,
                    collections,
                    self.target.allowed_collection_ids,
                )
                facade = MongoReadOnlyFacade(
                    database=database,
                    collections=collections,
                    client=owner if owner is not None else client,
                    connection_id=self.target.connection_id,
                    database_name=self.target.database_name,
                    allowed_collection_ids=self.target.allowed_collection_ids,
                    socket_timeout_ms=self.socket_timeout_ms
                    if _timeout_flags((owner, client))[0]
                    else None,
                    **self._facade_options,
                )
                # MongoReadOnlyFacade closes the explicit owner client.  Keep
                # that ownership in one place so a distinct bundle owner is
                # not closed twice by the outer lease.
                facade_owns_owner = True
            lease = HostNoSQLLease(
                self.target,
                facade,
                owner if owner is not None else client,
                client,
                facade_owns_owner=facade_owns_owner,
            )
            with self._lock:
                if self._closed:
                    lease.close()
                    facade = None
                    owner = None
                    client = None
                    raise NoSQLHostError(code="SESSION_NOT_FOUND")
                self._active.add(lease)
            return lease
        except NoSQLHostError:
            _close_failed_bundle(
                facade,
                owner,
                client,
                facade_owns_owner=facade_owns_owner,
            )
            raise
        except Exception:
            _close_failed_bundle(
                facade,
                owner,
                client,
                facade_owns_owner=facade_owns_owner,
            )
            raise NoSQLHostError(code="CLIENT_CREATE_FAILED") from None
        finally:
            profile = None
            secret = None
            client = None
            owner = None

    __call__ = connect

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            leases = list(self._active)
            self._active.clear()
        for lease in leases:
            lease.close()

    def _load_profile(self) -> Any:
        if self._profile is not None:
            return dict(self._profile) if isinstance(self._profile, Mapping) else self._profile
        try:
            value = _call_injected(
                self._profile_loader,
                {
                    "connection_id": self.target.connection_id,
                    "profile_revision": self.target.profile_revision,
                    "provider": self.target.provider,
                    "mode": self.target.mode,
                },
                fallback=(self.target.connection_id, self.target.profile_revision),
            )
        except Exception:
            raise NoSQLProfileError(code="PROFILE_UNAVAILABLE") from None
        if value is None:
            raise NoSQLProfileError(code="PROFILE_UNAVAILABLE")
        return value

    def _verify_profile(self, profile: Any) -> None:
        _canonical_profile(
            profile,
            self.target.connection_id,
            expected_revision=self.target.profile_revision,
            expected_provider=self.target.provider,
            expected_mode=self.target.mode,
            database_name=self.target.database_name,
        )

    def _load_secret(self, profile: Any) -> Any:
        needs_secret = _profile_needs_secret(profile, self.target.provider)
        if self._secret_loader is not None:
            try:
                value = _call_injected(
                    self._secret_loader,
                    {
                        "profile": profile,
                        "connection_id": self.target.connection_id,
                        "profile_revision": self.target.profile_revision,
                    },
                    fallback=(profile,),
                )
            except Exception:
                raise NoSQLSecretError(code="SECRET_UNAVAILABLE") from None
            if (value is None or (needs_secret and not _text(value))) and needs_secret:
                raise NoSQLSecretError(code="SECRET_UNAVAILABLE")
            return "" if value is None else value
        if not needs_secret:
            return ""
        token = _profile_secret_token(profile) if isinstance(profile, Mapping) else _value(profile, "password")
        if token is _MISSING or not _text(token):
            raise NoSQLSecretError(code="SECRET_UNAVAILABLE")
        try:
            # Lazy import keeps package import and test doubles free of config
            # and secure-store side effects.
            from tools.secure_store import decrypt_secret

            value = decrypt_secret(_text(token))
        except Exception:
            raise NoSQLSecretError(code="SECRET_UNAVAILABLE") from None
        if value is None:
            raise NoSQLSecretError(code="SECRET_UNAVAILABLE")
        return value

    def _client_options(self, profile: Any) -> dict[str, Any]:
        uri = _mongo_uri(profile) if self.target.provider == "mongodb" else ""
        host = _text(_value(profile, "host"))
        seeds = _profile_seed_nodes(profile, self.target.provider, self.target.mode)
        _validate_topology(profile, self.target.provider, self.target.mode, uri, seeds)
        common = {
            "provider": self.target.provider,
            "mode": self.target.mode,
            "read_only": True,
            "decode_responses": False,
            "socket_timeout_ms": self.socket_timeout_ms,
            "server_selection_timeout_ms": self.server_selection_timeout_ms,
        }
        if self.target.provider == "redis":
            auth_mode = _auth_mode(profile)
            common.update({
                "database": _database_index(profile),
                "seed_nodes": [dict(seed) for seed in seeds],
                "socket_connect_timeout": _seconds(self.socket_timeout_ms),
                "socket_timeout": _seconds(self.socket_timeout_ms),
                "auth_mode": auth_mode,
                "username": _text(_value(profile, "username")) if auth_mode == "acl" else None,
            })
        else:
            replica_set = _replica_set_name(profile)
            common.update({
                "database_name": self.target.database_name,
                "seed_nodes": [dict(seed) for seed in seeds],
                "uri": uri,
                "host": host if not uri else "",
                "port": None if uri else _port(_value(profile, "port"), _DEFAULT_MONGO_PORT),
                "direct_connection": False
                if self.target.mode == "cluster" or replica_set
                else None,
                "replica_set": replica_set,
                "auth_source": _text(_value(profile, "auth_source")) or self.target.database_name,
                "auth_mechanism": _text(_value(profile, "auth_mechanism")),
                "serverSelectionTimeoutMS": self.server_selection_timeout_ms,
                "socketTimeoutMS": self.socket_timeout_ms,
            })
        return common


def _select_profile(source: Any, connection_id: str) -> Mapping[str, Any]:
    """Select exactly one profile from a mapping, list, or loader result."""

    if source is None:
        raise NoSQLProfileError(code="PROFILE_UNAVAILABLE")
    if isinstance(source, Mapping):
        profile_id = _value(source, "connection_id", "id")
        if profile_id is not _MISSING:
            candidates = [source]
        elif connection_id in source:
            candidate = source[connection_id]
            if isinstance(candidate, Mapping) and not any(
                _text(_value(candidate, key)) for key in ("connection_id", "id")
            ):
                candidate = dict(candidate)
                candidate["id"] = connection_id
            candidates = [candidate]
        elif isinstance(source.get("items"), Sequence) and not isinstance(source.get("items"), (str, bytes, bytearray)):
            candidates = list(source.get("items") or ())
        else:
            candidates = []
    elif isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
        candidates = list(source)
    else:
        try:
            candidates = list(source)
        except (TypeError, ValueError):
            candidates = [source]
    matches: list[Mapping[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = _value(candidate, "connection_id", "id")
        if _text(candidate_id) == connection_id:
            matches.append(candidate)
    if len(matches) != 1:
        raise NoSQLProfileError(code="PROFILE_NOT_FOUND" if not matches else "PROFILE_AMBIGUOUS")
    return matches[0]


def _default_connections_loader() -> Any:
    """Read the legacy connection store without creating or modifying it."""

    # ``tools.db_connect.load_connections`` calls ``ensure_config_dir`` as a
    # convenience for the editor.  The Agent host only needs a read and must
    # not create the user's configuration directory as a side effect.
    from config import HARNESS_CONNECTIONS_FILE

    try:
        with open(HARNESS_CONNECTIONS_FILE, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    items = raw.get("items") if isinstance(raw, Mapping) else raw
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, Mapping) and _text(_value(item, "id", "connection_id"))]


def _default_decryptor(token: Any) -> Any:
    from tools.secure_store import decrypt_secret

    return decrypt_secret(str(token or ""))


def _profile_secret_token(profile: Mapping[str, Any]) -> Any:
    for key in (
        "password",
        "password_ciphertext",
        "password_token",
        "secret_ciphertext",
        "secret_token",
        "credential_token",
    ):
        if key in profile:
            return profile.get(key)
    return ""


def _canonical_profile(
    profile: Mapping[str, Any],
    connection_id: str,
    *,
    expected_revision: str | None = None,
    expected_provider: str | None = None,
    expected_mode: str | None = None,
    database_name: str | None = None,
) -> tuple[dict[str, Any], NoSQLProfileSnapshot]:
    """Normalize one legacy profile into safe host metadata."""

    if not isinstance(profile, Mapping):
        raise NoSQLProfileError(code="PROFILE_INVALID")
    selected_id = _text(connection_id)
    if not selected_id:
        raise ValueError("connection_id is required")
    data = dict(profile)
    loaded_id = _required(data, "connection_id", "id")
    if loaded_id != selected_id:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    try:
        # Legacy revision fields are metadata only.  The target follows the
        # current profile content, so an arbitrary stale value cannot keep an
        # old host/URI/credential/topology target valid.
        revision = compute_profile_revision(data)
    except Exception:
        raise NoSQLProfileError(code="PROFILE_INVALID") from None
    if expected_revision is not None and _text(expected_revision) != revision:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    provider = _provider_from_profile(data)
    uri = _mongo_uri(data) if provider == "mongodb" else ""
    host = uri or _text(_value(data, "host"))
    mode = _canonical_mode(_value(data, "mode"), provider, infer_uri=True, host=host)
    if expected_provider is not None and _canonical_provider(expected_provider) != provider:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    if expected_mode is not None and _canonical_mode(expected_mode, provider, host=host) != mode:
        raise NoSQLScopeError(code="PROFILE_MISMATCH")
    if provider == "mongodb":
        database = _database_name(data, database_name)
    else:
        database = ""
    seeds = _profile_seed_nodes(data, provider, mode)
    _validate_topology(data, provider, mode, uri, seeds)
    data.update({
        "id": selected_id,
        "connection_id": selected_id,
        "profile_revision": revision,
        "provider": provider,
        "mode": mode,
        "dialect": provider,
    })
    return data, NoSQLProfileSnapshot(selected_id, revision, provider, mode, database)


@dataclass(frozen=True, slots=True)
class NoSQLProfileSnapshot:
    """Public-safe metadata resolved from one selected connection profile."""

    connection_id: str
    profile_revision: str
    provider: str
    mode: str
    database_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "connection_id", _public_text(self.connection_id, "connection_id", required=True))
        object.__setattr__(self, "profile_revision", _public_text(self.profile_revision, "profile_revision", required=True))
        object.__setattr__(self, "provider", _canonical_provider(self.provider))
        object.__setattr__(self, "mode", _canonical_mode(self.mode, self.provider))
        object.__setattr__(self, "database_name", _public_text(self.database_name, "database_name"))
        if self.provider == "mongodb" and not self.database_name:
            raise ValueError("Mongo database_name is required")

    @property
    def scope(self) -> dict[str, Any]:
        value = {
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "provider": self.provider,
            "mode": self.mode,
        }
        if self.database_name:
            value["database"] = self.database_name
        return value

    def as_dict(self) -> dict[str, Any]:
        return self.scope

    def __repr__(self) -> str:
        return (
            "NoSQLProfileSnapshot("
            f"connection_id={self.connection_id!r}, profile_revision={self.profile_revision!r}, "
            f"provider={self.provider!r}, mode={self.mode!r}, database_name={self.database_name!r})"
        )


class LazyNoSQLFactory:
    """Resolve one profile on first use, then keep its target immutable."""

    def __init__(
        self,
        host: "NoSQLReadOnlyHost",
        connection_id: str,
        *,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        database_name: str | None = None,
        allowed_collection_ids: Sequence[str] | None = None,
    ) -> None:
        self._host = host
        self.connection_id = _text(connection_id)
        if not self.connection_id:
            raise ValueError("connection_id is required")
        self._expected_revision = None if profile_revision is None else _text(profile_revision)
        self._expected_provider = provider
        self._expected_mode = mode
        self._database_name = database_name
        self._allowed_collection_ids = tuple(str(item).strip() for item in (allowed_collection_ids or ()) if str(item).strip())
        self._factory: HostNoSQLFactory | None = None
        self._target: NoSQLTarget | None = None
        self._closed = False
        self._lock = threading.RLock()

    @property
    def target(self) -> NoSQLTarget | None:
        with self._lock:
            return self._target

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connection_id={self.connection_id!r}, target={self.target!r})"

    def __call__(
        self,
        connection_id: str | None = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        **kwargs: Any,
    ) -> HostNoSQLLease:
        supplied = _text(connection_id)
        if supplied and supplied != self.connection_id:
            raise NoSQLScopeError(code="SCOPE_DENIED")
        if kwargs:
            raise NoSQLScopeError(code="SCOPE_DENIED")
        with self._lock:
            if self._closed:
                raise NoSQLHostError(code="SESSION_NOT_FOUND")
            if self._factory is None:
                profile = self._host._load_profile(self.connection_id)
                normalized, snapshot = _canonical_profile(
                    profile,
                    self.connection_id,
                    expected_revision=self._expected_revision,
                    expected_provider=self._expected_provider,
                    expected_mode=self._expected_mode,
                    database_name=self._database_name,
                )
                database = self._database_name or snapshot.database_name
                self._target = NoSQLTarget(
                    snapshot.connection_id,
                    snapshot.profile_revision,
                    snapshot.provider,
                    snapshot.mode,
                    database,
                    self._allowed_collection_ids,
                )
                self._factory = self._host._build_factory(self._target, initial_profile=normalized)
            factory = self._factory
        return factory(self.connection_id, session_id=session_id, generation=generation)

    connect = __call__

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            factory = self._factory
        if factory is not None:
            factory.close()


class NoSQLReadOnlyHost:
    """Host boundary for profile resolution and NoSQL facade factories."""

    def __init__(
        self,
        profile: Mapping[str, Any] | None = None,
        *,
        profile_loader: Callable[..., Any] | Mapping[str, Any] | None = None,
        loader: Callable[..., Any] | Mapping[str, Any] | None = None,
        connections_loader: Callable[..., Any] | Mapping[str, Any] | None = None,
        connections: Mapping[str, Any] | Sequence[Any] | None = None,
        secret_loader: Callable[..., Any] | None = None,
        decryptor: Callable[[Any], Any] | None = None,
        redis_client_factory: Any = None,
        mongo_client_factory: Any = None,
        redis_factory: Any = None,
        mongo_factory: Any = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        socket_timeout_ms: int | None = None,
        server_selection_timeout_ms: int | None = None,
        facade_options: Mapping[str, Any] | None = None,
    ) -> None:
        if profile_loader is not None and loader is not None:
            raise TypeError("provide profile_loader or loader, not both")
        if profile_loader is None:
            profile_loader = loader
        if profile is not None and (profile_loader is not None or connections_loader is not None or connections is not None):
            raise TypeError("provide profile or a profile/connection loader, not both")
        if profile_loader is not None and (connections_loader is not None or connections is not None):
            raise TypeError("provide profile_loader or connections_loader, not both")
        if connections_loader is not None and connections is not None:
            raise TypeError("provide connections_loader or connections, not both")
        if profile_loader is not None and not callable(profile_loader) and not isinstance(profile_loader, Mapping):
            raise TypeError("profile_loader must be callable or a mapping")
        if connections_loader is not None and not callable(connections_loader) and not isinstance(connections_loader, Mapping):
            raise TypeError("connections_loader must be callable or a mapping")
        self._profile_source = profile if profile is not None else profile_loader
        self._connections_source = connections_loader
        if connections is not None:
            self._connections_source = lambda: connections
        self._secret_loader = secret_loader
        self._decryptor = _default_decryptor if decryptor is None else decryptor
        self._redis_factory = (
            redis_client_factory
            if redis_client_factory is not None
            else redis_factory
        )
        self._mongo_factory = (
            mongo_client_factory
            if mongo_client_factory is not None
            else mongo_factory
        )
        self._timeout_ms = _validate_positive_ms(timeout_ms, "timeout_ms")
        self._socket_timeout_ms = _validate_positive_ms(socket_timeout_ms, "socket_timeout_ms") if socket_timeout_ms is not None else None
        self._server_selection_timeout_ms = _validate_positive_ms(server_selection_timeout_ms, "server_selection_timeout_ms") if server_selection_timeout_ms is not None else None
        self._facade_options = dict(facade_options or {})

    def __repr__(self) -> str:
        return f"{type(self).__name__}(timeout_ms={self._timeout_ms!r})"

    def _load_profile(self, connection_id: str) -> Mapping[str, Any]:
        source = self._profile_source
        if source is None:
            source = self._connections_source or _default_connections_loader
        if isinstance(source, Mapping):
            return _select_profile(source, connection_id)
        try:
            loaded = _call_injected(
                source,
                {"connection_id": connection_id, "id": connection_id},
                fallback=(connection_id,),
            )
        except Exception:
            raise NoSQLProfileError(code="PROFILE_UNAVAILABLE") from None
        return _select_profile(loaded, connection_id)

    def profile_snapshot(self, connection_id: str) -> NoSQLProfileSnapshot:
        selected = _text(connection_id)
        profile = self._load_profile(selected)
        _normalized, snapshot = _canonical_profile(profile, selected)
        return snapshot

    def create_factory(
        self,
        connection_id: str,
        *,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        database_name: str | None = None,
        allowed_collection_ids: Sequence[str] | None = None,
    ) -> HostNoSQLFactory | LazyNoSQLFactory:
        selected = _text(connection_id)
        if not selected:
            raise ValueError("connection_id is required")
        normalized_provider = _canonical_provider(provider) if provider else ""
        expected_revision = _text(profile_revision) if profile_revision is not None else ""
        ids = tuple(str(item).strip() for item in (allowed_collection_ids or ()) if str(item).strip())
        if normalized_provider and expected_revision and mode and (normalized_provider != "mongodb" or database_name):
            target = NoSQLTarget(selected, expected_revision, normalized_provider, mode, _text(database_name), ids)
            return self._build_factory(target)
        return LazyNoSQLFactory(
            self,
            selected,
            profile_revision=expected_revision or None,
            provider=provider,
            mode=mode,
            database_name=database_name,
            allowed_collection_ids=ids,
        )

    def __call__(self, connection_id: str, **kwargs: Any) -> HostNoSQLFactory | LazyNoSQLFactory | HostNoSQLLease:
        session_id = kwargs.pop("session_id", None)
        generation = kwargs.pop("generation", None)
        factory = self.create_factory(connection_id, **kwargs)
        if session_id is not None or generation is not None:
            return factory(connection_id, session_id=session_id, generation=generation)
        return factory

    def connect(self, connection_id: str, **kwargs: Any) -> HostNoSQLLease:
        factory = self.create_factory(
            connection_id,
            profile_revision=kwargs.pop("profile_revision", None),
            provider=kwargs.pop("provider", None),
            mode=kwargs.pop("mode", None),
            database_name=kwargs.pop("database_name", None),
            allowed_collection_ids=kwargs.pop("allowed_collection_ids", None),
        )
        if kwargs:
            raise NoSQLScopeError(code="SCOPE_DENIED")
        return factory(connection_id)

    def _build_factory(self, target: NoSQLTarget, *, initial_profile: Mapping[str, Any] | None = None) -> HostNoSQLFactory:
        def load_profile(connection_id: str, profile_revision: str = "") -> Mapping[str, Any]:
            if initial_profile is not None and load_profile._first:  # type: ignore[attr-defined]
                load_profile._first = False  # type: ignore[attr-defined]
                return initial_profile
            raw = self._load_profile(connection_id)
            normalized, _snapshot = _canonical_profile(
                raw,
                connection_id,
                expected_revision=profile_revision or target.profile_revision,
                expected_provider=target.provider,
                expected_mode=target.mode,
                database_name=target.database_name,
            )
            return normalized

        load_profile._first = initial_profile is not None  # type: ignore[attr-defined]

        def load_secret(profile: Mapping[str, Any]) -> Any:
            needs_secret = _profile_needs_secret(profile, target.provider)
            if self._secret_loader is not None:
                try:
                    value = _call_injected(
                        self._secret_loader,
                        {
                            "profile": profile,
                            "connection_id": target.connection_id,
                            "profile_revision": target.profile_revision,
                            "provider": target.provider,
                            "mode": target.mode,
                        },
                        fallback=(profile,),
                    )
                except Exception:
                    raise NoSQLSecretError(code="SECRET_UNAVAILABLE") from None
                if value is None or (needs_secret and not _text(value)):
                    if needs_secret:
                        raise NoSQLSecretError(code="SECRET_UNAVAILABLE")
                    return ""
                return value
            token = _profile_secret_token(profile)
            if token in (None, ""):
                if needs_secret:
                    raise NoSQLSecretError(code="SECRET_UNAVAILABLE")
                return ""
            try:
                value = self._decryptor(token)
            except Exception:
                raise NoSQLSecretError(code="SECRET_UNAVAILABLE") from None
            if value is None or (str(value) == "" and str(token) != ""):
                raise NoSQLSecretError(code="SECRET_UNAVAILABLE")
            return value

        return HostNoSQLFactory(
            profile=initial_profile,
            connection_id=target.connection_id,
            profile_revision=target.profile_revision,
            provider=target.provider,
            mode=target.mode,
            database_name=target.database_name,
            allowed_collection_ids=target.allowed_collection_ids,
            profile_loader=None if initial_profile is not None else load_profile,
            secret_loader=load_secret,
            redis_client_factory=self._redis_factory,
            mongo_client_factory=self._mongo_factory,
            timeout_ms=self._timeout_ms,
            socket_timeout_ms=self._socket_timeout_ms,
            server_selection_timeout_ms=self._server_selection_timeout_ms,
            facade_options=self._facade_options,
        )


def _unpack_client(value: Any) -> tuple[Any, Any, Any, Mapping[str, Any] | None]:
    if isinstance(value, NoSQLClientBundle):
        owner = value.client if value.owner is None else value.owner
        return value.client, owner, value.database, value.collections
    if isinstance(value, Mapping) and "client" in value:
        client = value.get("client")
        owner = client if value.get("owner") is None else value.get("owner")
        return client, owner, value.get("database"), value.get("collections")
    if value is None:
        return None, None, None, None
    # A Mongo Database returned by a small fake/legacy connector carries its
    # owning client as ``.client``.  Keep the database as the facade source.
    if hasattr(value, "client") and not callable(getattr(value, "execute", None)):
        try:
            owner = getattr(value, "client")
        except Exception:
            owner = None
        if owner is not None:
            return owner, owner, value, None
    return value, value, None, None


def _resolve_database(client: Any, database_name: str) -> Any:
    try:
        return client[database_name]
    except Exception:
        raise NoSQLClientError(code="CLIENT_CREATE_FAILED") from None


def _resolve_collections(
    database: Any,
    provided: Mapping[str, Any] | None,
    allowed: Sequence[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    supplied = dict(provided or {})
    for collection_id in allowed:
        key = str(collection_id)
        value = supplied.get(key)
        if value is None:
            # Case-insensitive matching is fixed by the host allowlist; the
            # model still cannot cause a dynamic database lookup because this
            # loop traverses only the injected set.
            value = next((item for name, item in supplied.items() if str(name).casefold() == key.casefold()), None)
        if value is None:
            try:
                value = database[key]
            except Exception:
                raise NoSQLClientError(code="CLIENT_CREATE_FAILED") from None
        result[key] = value
    return result


def _default_redis_client_factory(profile: Any, secret: Any, **kwargs: Any) -> Any:
    """Create a Redis client lazily using all configured cluster seeds."""

    import redis

    options = dict(kwargs.get("options") or kwargs.get("client_options") or {})
    auth_mode = str(options.get("auth_mode") or "none")
    auth: dict[str, Any] = {"username": None, "password": None}
    if auth_mode == "password":
        auth["password"] = secret or None
    elif auth_mode == "acl":
        auth["username"] = options.get("username") or None
        auth["password"] = secret or None
    seeds = list(options.get("seed_nodes") or ())
    timeout = _timeout_seconds_option(options, default_ms=DEFAULT_TIMEOUT_MS)
    mode = str(options.get("mode") or "standalone")
    if mode == "cluster":
        try:
            from redis.cluster import ClusterNode, RedisCluster

            nodes = [ClusterNode(str(item["host"]), int(item["port"])) for item in seeds]
            return RedisCluster(
                startup_nodes=nodes,
                decode_responses=False,
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
                **auth,
            )
        except ImportError:
            # Older redis-py releases may expose ``RedisCluster`` without
            # ``redis.cluster.ClusterNode``.  Use that path only when it can
            # still receive every configured seed; silently reducing a
            # cluster profile to its first node would change its identity.
            legacy_cluster = getattr(redis, "RedisCluster", None)
            if not callable(legacy_cluster):
                raise RuntimeError("cluster client unavailable")
            try:
                signature = inspect.signature(legacy_cluster)
            except (TypeError, ValueError):
                raise RuntimeError("cluster seed capability unavailable") from None
            params = signature.parameters
            if "startup_nodes" not in params and not any(
                item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()
            ):
                raise RuntimeError("cluster seed capability unavailable")
            return legacy_cluster(
                startup_nodes=[
                    {"host": str(item["host"]), "port": int(item["port"])}
                    for item in seeds
                ],
                decode_responses=False,
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
                **auth,
            )
    first = seeds[0]
    return redis.Redis(
        host=first["host"],
        port=first["port"],
        db=_database_index(profile),
        decode_responses=False,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        **auth,
    )


def _default_mongo_client_factory(profile: Any, secret: Any, **kwargs: Any) -> Any:
    """Create a Mongo client lazily, preserving URI/topology identity."""

    from pymongo import MongoClient

    options = dict(kwargs.get("options") or kwargs.get("client_options") or {})
    timeout = _timeout_ms_option(
        options,
        "serverSelectionTimeoutMS",
        "server_selection_timeout_ms",
        default_ms=DEFAULT_TIMEOUT_MS,
    )
    socket_timeout = _timeout_ms_option(
        options,
        "socketTimeoutMS",
        "socket_timeout_ms",
        default_ms=DEFAULT_TIMEOUT_MS,
    )
    uri = str(options.get("uri") or "")
    replica_set = str(options.get("replica_set") or "")
    username = _text(_value(profile, "username"))
    auth_kwargs: dict[str, Any] = {}
    if username:
        auth_kwargs.update({
            "username": username,
            "password": secret,
            "authSource": str(options.get("auth_source") or "admin"),
        })
        mechanism = str(options.get("auth_mechanism") or "")
        if mechanism.casefold() not in {"", "auto", "automatic", "default"}:
            auth_kwargs["authMechanism"] = mechanism
    if uri:
        if replica_set:
            auth_kwargs["replicaSet"] = replica_set
        return MongoClient(
            uri,
            serverSelectionTimeoutMS=timeout,
            socketTimeoutMS=socket_timeout,
            **auth_kwargs,
        )
    seeds = list(options.get("seed_nodes") or ())
    mode = str(options.get("mode") or "standalone")
    if mode in {"cluster", "replica_set"} or replica_set:
        kwargs_out: dict[str, Any] = {
            "host": [f"{item['host']}:{item['port']}" for item in seeds],
            "directConnection": False,
            "serverSelectionTimeoutMS": timeout,
            "socketTimeoutMS": socket_timeout,
        }
        if replica_set:
            kwargs_out["replicaSet"] = replica_set
    else:
        first = seeds[0]
        kwargs_out = {
            "host": first["host"],
            "port": first["port"],
            "serverSelectionTimeoutMS": timeout,
            "socketTimeoutMS": socket_timeout,
        }
    if username:
        kwargs_out.update(auth_kwargs)
    return MongoClient(**kwargs_out)


class HostNoSQLRouter:
    """Route only the two fixed NoSQL tool names to host-owned leases."""

    _ALIASES = {
        "redis": "redis",
        "redis_read": "redis",
        "redis-read": "redis",
        "mongo": "mongodb",
        "mongodb": "mongodb",
        "mongo_read": "mongodb",
        "mongo-read": "mongodb",
    }

    def __init__(self, leases: HostNoSQLLease | Mapping[str, HostNoSQLLease] | None = None) -> None:
        self._leases: dict[str, HostNoSQLLease] = {}
        if isinstance(leases, HostNoSQLLease):
            self._leases[leases.provider] = leases
        elif isinstance(leases, Mapping):
            for key, lease in leases.items():
                if not isinstance(lease, HostNoSQLLease):
                    raise TypeError("router values must be HostNoSQLLease")
                alias = self._ALIASES.get(_text(key).casefold())
                provider = alias or _canonical_provider(key)
                if provider != lease.provider:
                    raise NoSQLScopeError(code="PROFILE_MISMATCH")
                self._leases[provider] = lease

    def route(self, tool_name: str) -> HostNoSQLLease | None:
        return self._leases.get(self._ALIASES.get(_text(tool_name).casefold(), ""))

    def execute(self, tool_name: str, request: Mapping[str, Any], **kwargs: Any) -> ToolResult:
        lease = self.route(tool_name)
        if lease is None:
            return _safe_result_failure("CAPABILITY_UNAVAILABLE", "NoSQL 工具能力未注入", query_id=kwargs.get("query_id"))
        return lease.execute(request, **kwargs)

    def close(self) -> None:
        for lease in tuple(self._leases.values()):
            lease.close()

    @property
    def leases(self) -> Mapping[str, HostNoSQLLease]:
        return dict(self._leases)

    def __repr__(self) -> str:
        return f"HostNoSQLRouter(providers={tuple(sorted(self._leases))!r})"


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "HostNoSQLFactory",
    "HostNoSQLLease",
    "HostNoSQLRouter",
    "LazyNoSQLFactory",
    "NoSQLCapabilityError",
    "NoSQLClientBundle",
    "NoSQLClientError",
    "NoSQLHostError",
    "NoSQLProfileSnapshot",
    "NoSQLReadOnlyHost",
    "NoSQLProfileError",
    "NoSQLScopeError",
    "NoSQLSecretError",
    "NoSQLTarget",
    "compute_profile_revision",
]
