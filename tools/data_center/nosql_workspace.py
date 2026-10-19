"""Pure, bounded codec for NoSQL workbench history and dormant drafts.

The body begins with the exact editor text and ends with a readable Canonical
Extended JSON trailer.  This module has no UI, profile, database, or network
dependencies; callers own dispatch and persistence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from typing import Any

from bson import json_util
from bson.decimal128 import Decimal128

from tools.data_center.workspace_store import (
    MAX_TEXT_BYTES,
    WorkspaceTabKind,
    WorkspaceTarget,
)


NOSQL_WORKSPACE_FORMAT = "pengtools.nosql-workspace"
NOSQL_WORKSPACE_VERSION = 1
NOSQL_WORKSPACE_TRAILER = "\n\nPT-NOSQL-HISTORY/"

_TRAILER_BYTES = NOSQL_WORKSPACE_TRAILER.encode("ascii")
_BASE_FIELDS = frozenset(
    {
        "format",
        "version",
        "provider",
        "action",
        "editor_bytes",
        "dispatched_text",
        "database",
        "mode",
        "collection",
        "parameters",
    }
)
_REDIS_PARAMETER_FIELDS = frozenset({"db_index"})
_MONGO_PARAMETER_FIELDS = frozenset({"filter", "projection", "sort", "skip", "limit"})
_MODE_PATTERN = re.compile(r"^[a-z0-9_.-]{1,64}$")
_MAX_TARGET_TEXT_BYTES = 256
_MAX_VALUE_DEPTH = 64

_JSON_OPTIONS = json_util.CANONICAL_JSON_OPTIONS.with_options(
    tz_aware=True,
    tzinfo=timezone.utc,
)


class NoSqlWorkspaceCodecError(ValueError):
    """A safe codec error with copyable display text and no embedded payload."""

    __slots__ = ("code", "display_text")

    def __init__(self, code: str, display_text: str = "") -> None:
        self.code = code
        self.display_text = display_text
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NoSqlWorkspaceSnapshot:
    """An immutable target and encoded body; decoded arguments are always fresh."""

    target: WorkspaceTarget
    body: str

    def __post_init__(self) -> None:
        _decode_body(self.target, self.body)

    @property
    def editor_text(self) -> str:
        return _decode_body(self.target, self.body)[0]

    @property
    def provider(self) -> str:
        return _decode_body(self.target, self.body)[1]["provider"]

    @property
    def action(self) -> str:
        return _decode_body(self.target, self.body)[1]["action"]

    @property
    def dispatched_text(self) -> str:
        return _decode_body(self.target, self.body)[1]["dispatched_text"]

    def worker_arguments(self) -> dict[str, Any]:
        """Return a fresh argument tree; caller mutation cannot change this snapshot."""

        _editor_text, metadata = _decode_body(self.target, self.body)
        result: dict[str, Any] = {
            "provider": metadata["provider"],
            "action": metadata["action"],
            "database": metadata["database"],
            "mode": metadata["mode"],
            "collection": metadata["collection"],
            "dispatched_text": metadata["dispatched_text"],
        }
        if metadata["provider"] == "redis":
            result["db_index"] = metadata["parameters"]["db_index"]
        else:
            # _decode_body performed a fresh Extended JSON load for this call.
            result.update(metadata["parameters"])
        return result


def capture_redis_command(
    target: WorkspaceTarget,
    editor_text: str,
    dispatched_text: str,
    *,
    mode: str,
    db_index: int,
) -> NoSqlWorkspaceSnapshot:
    """Freeze the exact Redis command text and its resolved mode/database."""

    _validate_target(target, WorkspaceTabKind.REDIS)
    _validate_editor_text(editor_text, dispatched_text, allow_blank=False)
    _validate_mode(mode)
    if target.schema != mode:
        _raise("INVALID_TARGET")
    if type(db_index) is not int or db_index < 0 or target.database != str(db_index):
        _raise("INVALID_PARAMETERS")

    metadata = {
        "format": NOSQL_WORKSPACE_FORMAT,
        "version": NOSQL_WORKSPACE_VERSION,
        "provider": "redis",
        "action": "command",
        "editor_bytes": len(editor_text.encode("utf-8")),
        "dispatched_text": dispatched_text,
        "database": target.database,
        "mode": mode,
        "collection": None,
        "parameters": {"db_index": db_index},
    }
    return NoSqlWorkspaceSnapshot(target, _encode_body(editor_text, metadata))


def capture_mongo_run(
    target: WorkspaceTarget,
    editor_text: str,
    dispatched_text: str,
    *,
    action: str,
    mode: str,
    database: str,
    collection: str,
    filter: Mapping[str, Any],
    projection: Mapping[str, Any] | None,
    sort: Mapping[str, Any] | list[Any] | None,
    skip: int,
    limit: int,
) -> NoSqlWorkspaceSnapshot:
    """Freeze Mongo worker arguments exactly as dispatched, without parsing text."""

    _validate_target(target, WorkspaceTabKind.MONGO)
    if type(action) is not str or action not in {"query", "command"}:
        _raise("UNSUPPORTED_ACTION")
    allow_blank = action == "query"
    _validate_editor_text(editor_text, dispatched_text, allow_blank=allow_blank)
    _validate_mode(mode)
    _validate_target_text(database, allow_empty=False)
    _validate_target_text(collection, allow_empty=False)
    if target.database != database or target.schema != collection:
        _raise("INVALID_TARGET")
    if not isinstance(filter, Mapping):
        _raise("INVALID_PARAMETERS")
    if projection is not None and not isinstance(projection, Mapping):
        _raise("INVALID_PARAMETERS")
    if sort is not None and not isinstance(sort, (Mapping, list)):
        _raise("INVALID_PARAMETERS")
    if type(skip) is not int or type(limit) is not int:
        _raise("INVALID_PARAMETERS")

    parameters = {
        "filter": _clone_extended_value(filter),
        "projection": None if projection is None else _clone_extended_value(projection),
        "sort": None if sort is None else _clone_extended_value(sort),
        "skip": skip,
        "limit": limit,
    }
    metadata = {
        "format": NOSQL_WORKSPACE_FORMAT,
        "version": NOSQL_WORKSPACE_VERSION,
        "provider": "mongo",
        "action": action,
        "editor_bytes": len(editor_text.encode("utf-8")),
        "dispatched_text": dispatched_text,
        "database": database,
        "mode": mode,
        "collection": collection,
        "parameters": parameters,
    }
    return NoSqlWorkspaceSnapshot(target, _encode_body(editor_text, metadata))


def decode_nosql_workspace(
    target: WorkspaceTarget,
    body: str,
) -> NoSqlWorkspaceSnapshot:
    """Validate a stored body or raise a safe, non-runnable decode error."""

    return NoSqlWorkspaceSnapshot(target, body)


def _encode_body(editor_text: str, metadata: dict[str, Any]) -> str:
    try:
        metadata_text = json_util.dumps(
            metadata,
            json_options=_JSON_OPTIONS,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        decoded = _loads_canonical_metadata(metadata_text)
        if not _same_extended_value(metadata, decoded):
            _raise("INVALID_PARAMETERS")
        body = f"{editor_text}{NOSQL_WORKSPACE_TRAILER}{NOSQL_WORKSPACE_VERSION} {metadata_text}"
        body_size = len(body.encode("utf-8"))
    except NoSqlWorkspaceCodecError:
        raise
    except Exception:
        _raise("INVALID_PARAMETERS")
    if body_size > MAX_TEXT_BYTES:
        _raise("TEXT_TOO_LARGE")
    return body


def _decode_body(
    target: WorkspaceTarget,
    body: str,
) -> tuple[str, dict[str, Any]]:
    display_text = _safe_display_text(body) if type(body) is str else ""
    try:
        _validate_target_shape(target)
        if type(body) is not str:
            _raise("CORRUPT_ENVELOPE", display_text)
        try:
            body_bytes = body.encode("utf-8")
        except UnicodeEncodeError:
            _raise("CORRUPT_ENVELOPE", display_text)
        if len(body_bytes) > MAX_TEXT_BYTES:
            _raise("TEXT_TOO_LARGE", display_text)
        marker_offset = body_bytes.rfind(_TRAILER_BYTES)
        if marker_offset < 0:
            _raise("CORRUPT_ENVELOPE", display_text)
        raw_bytes = body_bytes[:marker_offset]
        trailer_bytes = body_bytes[marker_offset + len(_TRAILER_BYTES) :]
        try:
            trailer = trailer_bytes.decode("utf-8")
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _raise("CORRUPT_ENVELOPE", display_text)
        version_match = re.match(r"^(?P<version>[0-9]+) (?P<metadata>.+)$", trailer, re.DOTALL)
        if version_match is None:
            _raise("CORRUPT_ENVELOPE", display_text)
        if version_match.group("version") != str(NOSQL_WORKSPACE_VERSION):
            _raise("UNSUPPORTED_VERSION", display_text)
        metadata = _loads_canonical_metadata(version_match.group("metadata"))
        _validate_metadata(target, raw_text, len(raw_bytes), metadata)
        return raw_text, metadata
    except NoSqlWorkspaceCodecError as error:
        if error.display_text:
            raise
        raise NoSqlWorkspaceCodecError(error.code, _safe_display_text(display_text)) from None
    except Exception:
        _raise("CORRUPT_ENVELOPE", _safe_display_text(display_text))


def _loads_canonical_metadata(text: str) -> dict[str, Any]:
    try:
        json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
        metadata = json_util.loads(text, json_options=_JSON_OPTIONS)
        if type(metadata) is not dict:
            _raise("CORRUPT_ENVELOPE")
        canonical_text = json_util.dumps(
            metadata,
            json_options=_JSON_OPTIONS,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if canonical_text != text:
            _raise("CORRUPT_ENVELOPE")
        return metadata
    except NoSqlWorkspaceCodecError:
        raise
    except Exception:
        _raise("CORRUPT_ENVELOPE")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _raise("CORRUPT_ENVELOPE")
        result[key] = value
    return result


def _validate_metadata(
    target: WorkspaceTarget,
    editor_text: str,
    editor_bytes: int,
    metadata: dict[str, Any],
) -> None:
    if set(metadata) != _BASE_FIELDS:
        _raise("CORRUPT_ENVELOPE")
    if metadata.get("format") != NOSQL_WORKSPACE_FORMAT:
        _raise("CORRUPT_ENVELOPE")
    version = metadata.get("version")
    if type(version) is not int:
        _raise("CORRUPT_ENVELOPE")
    if version != NOSQL_WORKSPACE_VERSION:
        _raise("UNSUPPORTED_VERSION")
    provider = metadata.get("provider")
    if type(provider) is not str or provider not in {"redis", "mongo"}:
        _raise("UNSUPPORTED_PROVIDER")
    expected_kind = WorkspaceTabKind.REDIS if provider == "redis" else WorkspaceTabKind.MONGO
    _validate_target(target, expected_kind)
    action = metadata.get("action")
    allowed_actions = {"command"} if provider == "redis" else {"query", "command"}
    if type(action) is not str or action not in allowed_actions:
        _raise("UNSUPPORTED_ACTION")
    if type(metadata.get("editor_bytes")) is not int or metadata["editor_bytes"] != editor_bytes:
        _raise("CORRUPT_ENVELOPE")
    _validate_editor_text(
        editor_text,
        metadata.get("dispatched_text"),
        allow_blank=(provider == "mongo" and action == "query"),
    )
    database = metadata.get("database")
    mode = metadata.get("mode")
    collection = metadata.get("collection")
    _validate_target_text(database, allow_empty=False)
    _validate_mode(mode)
    if database != target.database:
        _raise("INVALID_TARGET")

    parameters = metadata.get("parameters")
    if type(parameters) is not dict:
        _raise("INVALID_PARAMETERS")
    if provider == "redis":
        if set(parameters) != _REDIS_PARAMETER_FIELDS:
            _raise("INVALID_PARAMETERS")
        if type(collection) is not type(None) or target.schema != mode:
            _raise("INVALID_TARGET")
        db_index = parameters.get("db_index")
        if type(db_index) is not int or db_index < 0 or target.database != str(db_index):
            _raise("INVALID_PARAMETERS")
        return

    if set(parameters) != _MONGO_PARAMETER_FIELDS:
        _raise("INVALID_PARAMETERS")
    _validate_target_text(collection, allow_empty=False)
    if target.schema != collection:
        _raise("INVALID_TARGET")
    _validate_mongo_parameters(parameters)


def _validate_mongo_parameters(parameters: dict[str, Any]) -> None:
    query_filter = parameters.get("filter")
    projection = parameters.get("projection")
    sort = parameters.get("sort")
    skip = parameters.get("skip")
    limit = parameters.get("limit")
    if not isinstance(query_filter, Mapping):
        _raise("INVALID_PARAMETERS")
    if projection is not None and not isinstance(projection, Mapping):
        _raise("INVALID_PARAMETERS")
    if sort is not None and not isinstance(sort, (Mapping, list)):
        _raise("INVALID_PARAMETERS")
    if type(skip) is not int or type(limit) is not int:
        _raise("INVALID_PARAMETERS")


def _validate_target(target: WorkspaceTarget, expected_kind: WorkspaceTabKind) -> None:
    _validate_target_shape(target)
    if target.tab_kind is not expected_kind:
        _raise("UNSUPPORTED_PROVIDER")
    _validate_target_text(target.database, allow_empty=False)
    if type(target.schema) is not str:
        _raise("INVALID_TARGET")
    _validate_target_text(target.schema, allow_empty=False)


def _validate_target_shape(target: WorkspaceTarget) -> None:
    if type(target) is not WorkspaceTarget or type(target.tab_kind) is not WorkspaceTabKind:
        _raise("INVALID_TARGET")
    for name in ("tab_id", "connection_id", "profile_revision"):
        _validate_target_text(getattr(target, name), allow_empty=False)
    if type(target.database) is not str or type(target.schema) is not str:
        _raise("INVALID_TARGET")


def _validate_target_text(value: Any, *, allow_empty: bool) -> None:
    if type(value) is not str or (not value and not allow_empty):
        _raise("INVALID_TARGET")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _raise("INVALID_TARGET")
    if len(encoded) > _MAX_TARGET_TEXT_BYTES:
        _raise("INVALID_TARGET")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        _raise("INVALID_TARGET")


def _validate_mode(mode: Any) -> None:
    if type(mode) is not str or not _MODE_PATTERN.fullmatch(mode):
        _raise("INVALID_TARGET")


def _validate_editor_text(editor_text: Any, dispatched_text: Any, *, allow_blank: bool) -> None:
    if type(editor_text) is not str or type(dispatched_text) is not str:
        _raise("INVALID_TEXT")
    try:
        editor_text.encode("utf-8")
        dispatched_text.encode("utf-8")
    except UnicodeEncodeError:
        _raise("INVALID_TEXT")
    for value in (editor_text, dispatched_text):
        if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
            _raise("INVALID_TEXT")
    if dispatched_text != editor_text.strip():
        _raise("INVALID_TEXT")
    if not allow_blank and not dispatched_text:
        _raise("INVALID_TEXT")


def _clone_extended_value(value: Any, active: set[int] | None = None, depth: int = 0) -> Any:
    if depth > _MAX_VALUE_DEPTH:
        _raise("INVALID_PARAMETERS")
    if active is None:
        active = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            _raise("INVALID_PARAMETERS")
        active.add(identity)
        try:
            cloned: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    _raise("INVALID_PARAMETERS")
                _validate_parameter_text(key)
                if key in cloned:
                    _raise("INVALID_PARAMETERS")
                cloned[key] = _clone_extended_value(item, active, depth + 1)
        except NoSqlWorkspaceCodecError:
            raise
        except Exception:
            _raise("INVALID_PARAMETERS")
        finally:
            active.remove(identity)
        return cloned
    if type(value) is list:
        identity = id(value)
        if identity in active:
            _raise("INVALID_PARAMETERS")
        active.add(identity)
        try:
            return [_clone_extended_value(item, active, depth + 1) for item in value]
        except NoSqlWorkspaceCodecError:
            raise
        except Exception:
            _raise("INVALID_PARAMETERS")
        finally:
            active.remove(identity)
    if type(value) is tuple:
        _raise("INVALID_PARAMETERS")
    if type(value) is str:
        _validate_parameter_text(value)
    return value


def _validate_parameter_text(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _raise("INVALID_PARAMETERS")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        _raise("INVALID_PARAMETERS")


def _same_extended_value(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        if list(left.keys()) != list(right.keys()):
            return False
        return all(_same_extended_value(left[key], right[key]) for key in left)
    if type(left) is list or type(right) is list:
        return (
            type(left) is list
            and type(right) is list
            and len(left) == len(right)
            and all(_same_extended_value(a, b) for a, b in zip(left, right))
        )
    if type(left) is not type(right):
        return False
    if isinstance(left, datetime):
        if left.microsecond % 1000 != 0 or right.microsecond % 1000 != 0:
            return False
        left_utc = left.replace(tzinfo=timezone.utc) if left.tzinfo is None else left.astimezone(timezone.utc)
        right_utc = right.replace(tzinfo=timezone.utc) if right.tzinfo is None else right.astimezone(timezone.utc)
        return left_utc == right_utc
    if type(left) is float:
        if math.isnan(left) and math.isnan(right):
            return True
        return left == right and math.copysign(1.0, left) == math.copysign(1.0, right)
    if isinstance(left, Decimal128):
        return str(left) == str(right)
    try:
        return bool(left == right)
    except Exception:
        return False


def _safe_display_text(text: str) -> str:
    try:
        encoded = text.encode("utf-8", errors="replace")
    except Exception:
        return ""
    if len(encoded) <= MAX_TEXT_BYTES:
        return text
    return encoded[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")


def _raise(code: str, display_text: str = "") -> None:
    raise NoSqlWorkspaceCodecError(code, display_text) from None


__all__ = [
    "NOSQL_WORKSPACE_FORMAT",
    "NOSQL_WORKSPACE_TRAILER",
    "NOSQL_WORKSPACE_VERSION",
    "NoSqlWorkspaceCodecError",
    "NoSqlWorkspaceSnapshot",
    "capture_mongo_run",
    "capture_redis_command",
    "decode_nosql_workspace",
]
