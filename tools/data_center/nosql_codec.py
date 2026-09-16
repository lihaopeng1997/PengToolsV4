"""Bounded, non-executable codecs for Redis values and MongoDB BSON.

The data-center Agent must not receive driver objects or arbitrary BSON
instances.  This module turns values returned by an injected read-only client
into small JSON-shaped values with explicit type tags.  It never imports a
database driver at module import time and it never evaluates code contained in
an object.

The codec is intentionally independent from :mod:`result_projection`.
Projection limits protect the model envelope; this codec also protects the
NoSQL adapter before a driver value is placed in that envelope.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import base64
import binascii
import json
import math
import re
from typing import Any, Iterable


DEFAULT_MAX_VALUE_BYTES = 12 * 1024
DEFAULT_MAX_TEXT_CHARS = 512
DEFAULT_MAX_BINARY_PREVIEW_BYTES = 256
DEFAULT_MAX_ITEMS = 100
DEFAULT_MAX_DEPTH = 8

_SENSITIVE_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "secret",
        "authorization",
        "apikey",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "jwt",
    }
)
_SENSITIVE_PART = re.compile(
    r"(?:^|_)(?:password|passwd|pwd|token|secret|authorization|api_key|apikey|access_token|refresh_token|client_secret|private_key|credential|credentials|jwt)(?:_|$)"
)
_HEX_24 = re.compile(r"^[0-9a-fA-F]{24}$")


class CodecError(ValueError):
    """Raised when a value cannot be safely decoded or bounded."""


@dataclass(frozen=True, slots=True)
class EncodedValue:
    """A bounded JSON value and the accounting metadata for it."""

    value: Any
    truncated: bool = False
    bytes_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "truncated": self.truncated,
            "bytes_used": self.bytes_used,
        }


def is_sensitive_name(name: Any, extra: Iterable[str] = ()) -> bool:
    """Return whether a field name is an obvious credential field."""

    normalized = _normalize_name(name)
    extra_names = {_normalize_name(item) for item in extra if str(item).strip()}
    return normalized in _SENSITIVE_NAMES or normalized in extra_names or bool(
        _SENSITIVE_PART.search(normalized)
    )


def encode_bounded(
    value: Any,
    *,
    max_bytes: int = DEFAULT_MAX_VALUE_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    max_binary_preview_bytes: int = DEFAULT_MAX_BINARY_PREVIEW_BYTES,
    sensitive_names: Iterable[str] = (),
) -> EncodedValue:
    """Encode one value into a bounded, JSON-compatible typed structure.

    The input object is never mutated.  ``bytes`` and BSON binary values are
    represented by size plus a small Base64 preview.  Script-bearing BSON
    values are represented by an omission marker and their source is never
    returned.  A second fitting pass guarantees that the serialized result is
    at most ``max_bytes`` UTF-8 bytes.
    """

    _validate_positive("max_bytes", max_bytes)
    _validate_positive("max_depth", max_depth)
    _validate_positive("max_items", max_items)
    _validate_positive("max_text_chars", max_text_chars)
    _validate_positive("max_binary_preview_bytes", max_binary_preview_bytes)
    encoder = _Encoder(
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_items=max_items,
        max_text_chars=max_text_chars,
        max_binary_preview_bytes=max_binary_preview_bytes,
        sensitive_names=tuple(sensitive_names),
    )
    encoded = encoder.encode(value)
    fitted, fit_truncated = _fit_json(encoded, max_bytes)
    return EncodedValue(
        value=fitted,
        truncated=bool(encoder.truncated or fit_truncated),
        bytes_used=utf8_size(fitted),
    )


def encode_bson(value: Any, **kwargs: Any) -> Any:
    """Compatibility helper returning only the bounded encoded value."""

    return encode_bounded(value, **kwargs).value


def encode_redis_value(value: Any, **kwargs: Any) -> Any:
    """Alias used by Redis adapters."""

    return encode_bson(value, **kwargs)


def encode_mongo_document(value: Any, **kwargs: Any) -> Any:
    """Alias used by Mongo adapters."""

    return encode_bson(value, **kwargs)


def safe_json_dumps(value: Any) -> str:
    """Serialize an already encoded value without driver-specific defaults."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utf8_size(value: Any) -> int:
    return len(safe_json_dumps(value).encode("utf-8"))


def decode_extended_json(
    value: Any,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_binary_bytes: int = 64 * 1024,
) -> Any:
    """Decode a deliberately small Extended JSON subset without ``eval``.

    This helper is for a future host-side Mongo input adapter.  The current
    policy still decides whether an operator is allowed before this function
    is called.  Only common scalar BSON literals are accepted; code, DBRef,
    JavaScript and unknown dollar wrappers are rejected.
    """

    _validate_positive("max_depth", max_depth)
    _validate_positive("max_items", max_items)
    _validate_positive("max_binary_bytes", max_binary_bytes)
    return _decode_extended_json(value, depth=0, max_depth=max_depth, max_items=max_items, max_binary_bytes=max_binary_bytes)


def _decode_extended_json(
    value: Any,
    *,
    depth: int,
    max_depth: int,
    max_items: int,
    max_binary_bytes: int,
) -> Any:
    if depth > max_depth:
        raise CodecError("BSON 输入嵌套过深")
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise CodecError("BSON 输入数值无效")
        return value
    if isinstance(value, Mapping):
        if len(value) > max_items:
            raise CodecError("BSON 输入字段过多")
        if len(value) == 1:
            key, item = next(iter(value.items()))
            if not isinstance(key, str):
                raise CodecError("BSON 输入字段名无效")
            if key == "$oid":
                if not isinstance(item, str) or not _HEX_24.fullmatch(item):
                    raise CodecError("ObjectId 格式无效")
                try:
                    from bson import ObjectId
                except ImportError:
                    return {"$oid": item.lower()}
                return ObjectId(item)
            if key == "$date":
                if isinstance(item, str):
                    return _parse_datetime(item)
                if isinstance(item, Mapping) and set(item) == {"$numberLong"}:
                    try:
                        millis = int(item["$numberLong"])
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise CodecError("日期格式无效") from exc
                    from datetime import timezone
                    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
                raise CodecError("日期格式无效")
            if key == "$numberLong":
                if not isinstance(item, str) or not re.fullmatch(r"-?[0-9]{1,19}", item):
                    raise CodecError("Int64 格式无效")
                return int(item)
            if key == "$numberDecimal":
                if not isinstance(item, str) or len(item) > 128:
                    raise CodecError("Decimal128 格式无效")
                try:
                    return Decimal(item)
                except Exception as exc:
                    raise CodecError("Decimal128 格式无效") from exc
            if key == "$binary":
                if not isinstance(item, Mapping):
                    raise CodecError("Binary 格式无效")
                raw = item.get("base64")
                subtype = item.get("subType", "00")
                if not isinstance(raw, str) or len(raw) > ((max_binary_bytes + 2) // 3) * 4 + 4:
                    raise CodecError("Binary 超出大小上限")
                if not isinstance(subtype, str) or not re.fullmatch(r"[0-9a-fA-F]{2}", subtype):
                    raise CodecError("Binary subtype 无效")
                try:
                    decoded = base64.b64decode(raw.encode("ascii"), validate=True)
                except (ValueError, UnicodeError, binascii.Error) as exc:
                    raise CodecError("Binary 编码无效") from exc
                if len(decoded) > max_binary_bytes:
                    raise CodecError("Binary 超出大小上限")
                try:
                    from bson.binary import Binary
                except ImportError:
                    return decoded
                return Binary(decoded, subtype=int(subtype, 16))
            if key in {"$code", "$scope", "$dbPointer", "$minKey", "$maxKey", "$regularExpression"}:
                raise CodecError("BSON 脚本或特殊类型不允许作为 Agent 输入")
            if key.startswith("$"):
                raise CodecError("未知 BSON 类型包装器")
        decoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CodecError("BSON 输入字段名无效")
            decoded[key] = _decode_extended_json(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_binary_bytes=max_binary_bytes,
            )
        return decoded
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > max_items:
            raise CodecError("BSON 输入数组过长")
        return [
            _decode_extended_json(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_binary_bytes=max_binary_bytes,
            )
            for item in value
        ]
    raise CodecError("BSON 输入不是受支持的 JSON 值")


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if not text or len(text) > 80:
        raise CodecError("日期格式无效")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise CodecError("日期格式无效") from exc


class _Encoder:
    __slots__ = (
        "max_bytes",
        "max_depth",
        "max_items",
        "max_text_chars",
        "max_binary_preview_bytes",
        "sensitive_names",
        "truncated",
    )

    def __init__(
        self,
        *,
        max_bytes: int,
        max_depth: int,
        max_items: int,
        max_text_chars: int,
        max_binary_preview_bytes: int,
        sensitive_names: tuple[str, ...],
    ) -> None:
        self.max_bytes = max_bytes
        self.max_depth = max_depth
        self.max_items = max_items
        self.max_text_chars = max_text_chars
        self.max_binary_preview_bytes = max_binary_preview_bytes
        self.sensitive_names = sensitive_names
        self.truncated = False

    def encode(self, value: Any, *, field_name: str | None = None, depth: int = 0) -> Any:
        if field_name is not None and is_sensitive_name(field_name, self.sensitive_names):
            return {"type": "redacted"}
        if depth > self.max_depth:
            self.truncated = True
            return {"type": "truncated", "reason": "depth"}
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "boolean", "value": value}
        if isinstance(value, int):
            kind = "int64" if value.__class__.__name__ == "Int64" else "integer"
            return {"type": kind, "value": int(value)}
        if isinstance(value, Decimal):
            return {"type": "decimal128", "value": _bounded_text(str(value), self.max_text_chars, self)}
        if isinstance(value, float):
            if not math.isfinite(value):
                self.truncated = True
                return {"type": "number", "value": str(value)}
            return {"type": "number", "value": value}
        if isinstance(value, datetime):
            return {"type": "datetime", "value": value.isoformat()}
        if isinstance(value, date):
            return {"type": "date", "value": value.isoformat()}
        if isinstance(value, time):
            return {"type": "time", "value": value.isoformat()}

        class_name = value.__class__.__name__
        module_name = value.__class__.__module__
        if class_name == "ObjectId" and module_name.startswith("bson"):
            return {"type": "object_id", "value": _bounded_text(str(value), self.max_text_chars, self)}
        if class_name == "Decimal128" and module_name.startswith("bson"):
            return {"type": "decimal128", "value": _bounded_text(str(value), self.max_text_chars, self)}
        if class_name in {"Code", "JavaScript"} and module_name.startswith("bson"):
            self.truncated = True
            return {"type": "code", "omitted": True}
        if class_name == "MinKey" and module_name.startswith("bson"):
            return {"type": "min_key"}
        if class_name == "MaxKey" and module_name.startswith("bson"):
            return {"type": "max_key"}
        if class_name == "Timestamp" and module_name.startswith("bson"):
            seconds = getattr(value, "time", None)
            increment = getattr(value, "inc", None)
            return {"type": "timestamp", "time": int(seconds or 0), "inc": int(increment or 0)}
        if class_name == "Regex" and module_name.startswith("bson"):
            return {
                "type": "regex",
                "pattern": _bounded_text(str(getattr(value, "pattern", "")), self.max_text_chars, self),
                "options": _bounded_text(str(getattr(value, "flags", "")), 64, self),
            }
        if class_name == "DBRef" and module_name.startswith("bson"):
            self.truncated = True
            return {"type": "db_ref", "omitted": True}
        if isinstance(value, (bytes, bytearray, memoryview)):
            # Slice only the bounded preview.  Converting a multi-megabyte
            # bytearray/memoryview to ``bytes`` first would duplicate the
            # entire driver value before the output budget is applied.
            size = len(value)
            preview = bytes(value[: self.max_binary_preview_bytes])
            result: dict[str, Any] = {
                "type": "binary",
                "size": size,
                "preview_base64": base64.b64encode(preview).decode("ascii"),
            }
            subtype = getattr(value, "subtype", None)
            if subtype is not None:
                try:
                    result["subtype"] = int(subtype)
                except (TypeError, ValueError):
                    pass
            if len(preview) < size:
                result["truncated"] = True
                self.truncated = True
            return result
        # ``bson.code.Code`` subclasses ``str``.  BSON class handling must
        # happen before the generic string branch so script source is never
        # copied into the model envelope.
        if isinstance(value, str):
            text = _bounded_text(value, self.max_text_chars, self)
            return {"type": "string", "value": text, **({"truncated": True} if text != value else {})}
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            item_count = 0
            was_truncated = False
            for key, item in value.items():
                item_count += 1
                if item_count > self.max_items:
                    self.truncated = True
                    was_truncated = True
                    break
                raw_key = str(key)
                # Test the original key before bounding it.  A long key with a
                # sensitive suffix must not evade redaction because the suffix
                # was cut off by the display limit.
                key_text = _bounded_text(raw_key, self.max_text_chars, self)
                encoded_item = self.encode(item, field_name=raw_key, depth=depth + 1)
                result[key_text] = encoded_item
                trial = {
                    "type": "object",
                    "value": result,
                    **({"truncated": True} if was_truncated else {}),
                }
                if utf8_size(trial) > self.max_bytes:
                    result.pop(key_text, None)
                    self.truncated = True
                    was_truncated = True
                    break
            return {
                "type": "object",
                "value": result,
                **({"truncated": True} if was_truncated else {}),
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            result: list[Any] = []
            was_truncated = False
            for item_count, item in enumerate(value, start=1):
                if item_count > self.max_items:
                    self.truncated = True
                    was_truncated = True
                    break
                encoded_item = self.encode(item, depth=depth + 1)
                trial = {
                    "type": "array",
                    "value": [*result, encoded_item],
                    **({"truncated": True} if was_truncated else {}),
                }
                if utf8_size(trial) > self.max_bytes:
                    self.truncated = True
                    was_truncated = True
                    break
                result.append(encoded_item)
            return {
                "type": "array",
                "value": result,
                **({"truncated": True} if was_truncated else {}),
            }
        if isinstance(value, set):
            result: list[Any] = []
            was_truncated = False
            for item_count, item in enumerate(value, start=1):
                if item_count > self.max_items:
                    self.truncated = True
                    was_truncated = True
                    break
                encoded_item = self.encode(item, depth=depth + 1)
                trial = {
                    "type": "set",
                    "value": [*result, encoded_item],
                    **({"truncated": True} if was_truncated else {}),
                }
                if utf8_size(trial) > self.max_bytes:
                    self.truncated = True
                    was_truncated = True
                    break
                result.append(encoded_item)
            return {
                "type": "set",
                "value": result,
                **({"truncated": True} if was_truncated else {}),
            }
        # Unknown driver values are metadata only.  Never call a custom
        # serializer or execute a representation supplied by the object.
        self.truncated = True
        return {
            "type": "unsupported",
            "class": _bounded_text(class_name or "value", 96, self),
        }


def _bounded_text(value: Any, limit: int, encoder: _Encoder | None = None) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    if encoder is not None:
        encoder.truncated = True
    return text[:limit]


def _fit_json(value: Any, max_bytes: int) -> tuple[Any, bool]:
    """Trim an encoded value while preserving a truthful marker."""

    if utf8_size(value) <= max_bytes:
        return value, False
    marker = {"type": "truncated", "reason": "byte_budget"}
    if utf8_size(marker) > max_bytes:
        # Even a one-byte budget must satisfy the advertised bound.  ``0`` is
        # the smallest valid JSON value and the accompanying boolean records
        # that the original value was discarded.
        return ({} if max_bytes >= 2 else 0), True
    if isinstance(value, Mapping):
        result = dict(value)
        if isinstance(result.get("value"), Mapping):
            children: dict[str, Any] = {}
            for key, item in result["value"].items():
                trial = dict(result)
                trial["value"] = {**children, key: item}
                if utf8_size(trial) > max_bytes:
                    break
                children[key] = item
            result["value"] = children
            result["truncated"] = True
            if utf8_size(result) <= max_bytes:
                return result, True
        if isinstance(result.get("value"), list):
            children = []
            for item in result["value"]:
                trial = dict(result)
                trial["value"] = children + [item]
                if utf8_size(trial) > max_bytes:
                    break
                children.append(item)
            result["value"] = children
            result["truncated"] = True
            if utf8_size(result) <= max_bytes:
                return result, True
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            trial = items + [item]
            if utf8_size(trial) > max_bytes:
                break
            items.append(item)
        if items:
            return items, True
    return marker, True


def _normalize_name(value: Any) -> str:
    # Split both ``apiToken`` and acronym boundaries before case folding.  The
    # old lower-case-only normalization treated ``apiToken`` as ``apitoken``
    # and allowed common credential spellings through the sensitive-field
    # check.
    text = str(value)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _validate_positive(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


__all__ = [
    "CodecError",
    "DEFAULT_MAX_BINARY_PREVIEW_BYTES",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MAX_TEXT_CHARS",
    "DEFAULT_MAX_VALUE_BYTES",
    "EncodedValue",
    "decode_extended_json",
    "encode_bounded",
    "encode_bson",
    "encode_mongo_document",
    "encode_redis_value",
    "is_sensitive_name",
    "safe_json_dumps",
    "utf8_size",
]
