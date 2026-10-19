"""Typed, optimistic row ChangeSets for verified relational base tables.

This module builds reviewable parameterized SQL. It does not connect to a
database; applying changes delegates one savepoint batch to a matching manual
session and requires a one-row result for every staged operation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import threading
import uuid
from types import MappingProxyType
from typing import Any, Protocol

from tools.data_center.manual_session import (
    ManualBatchResult,
    ManualSession,
    ManualSessionTarget,
)


_MAX_IDENTIFIER_LENGTH = 256
_BIND = object()


class ChangeSetError(RuntimeError):
    """Stable error for rejected local edits, previews, or apply requests."""

    def __init__(self, code: str, *, reason: str = ""):
        self.code = str(code or "CHANGESET_FAILED")
        self.reason = str(reason or "")
        super().__init__(f"{self.code}: {self.reason}" if self.reason else self.code)


class EditableSourceKind(str, Enum):
    VERIFIED_TABLE = "verified_table"
    QUERY_RESULT = "query_result"
    JOIN = "join"
    AGGREGATE = "aggregate"
    UNVERIFIED = "unverified"


READ_ONLY_REASONS = MappingProxyType({
    EditableSourceKind.QUERY_RESULT: "查询结果没有经过验证的基础表行身份，当前只读。",
    EditableSourceKind.JOIN: "联接结果没有唯一、已验证的更新目标，当前只读。",
    EditableSourceKind.AGGREGATE: "聚合结果不对应单条基础表记录，当前只读。",
    EditableSourceKind.UNVERIFIED: "表元数据或主键尚未验证，当前只读。",
})


def readonly_reason(source_kind: EditableSourceKind | str) -> str:
    try:
        kind = source_kind if isinstance(source_kind, EditableSourceKind) else EditableSourceKind(str(source_kind))
    except Exception:
        return "结果来源无法验证，当前只读。"
    return READ_ONLY_REASONS.get(kind, "")


@dataclass(frozen=True, slots=True)
class VerifiedColumn:
    name: str
    editable: bool = True
    comparable: bool = True
    required_on_insert: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.name)
        if any(type(value) is not bool for value in (self.editable, self.comparable, self.required_on_insert)):
            raise ChangeSetError("INVALID_COLUMN_METADATA")


@dataclass(frozen=True, slots=True)
class VerifiedTableMetadata:
    """Trusted host-supplied metadata for one verified base table.

    Only a table whose metadata and primary key were verified by the host may
    be represented as ``VERIFIED_TABLE``. Query/join/aggregate descriptions
    carry explicit read-only reasons and are rejected here.
    """

    target: ManualSessionTarget
    table: str
    columns: tuple[VerifiedColumn, ...]
    primary_key: tuple[str, ...]
    source_kind: EditableSourceKind
    concurrency_columns: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, ManualSessionTarget):
            raise ChangeSetError("INVALID_TARGET")
        _validate_identifier(self.table)
        try:
            source = self.source_kind if isinstance(self.source_kind, EditableSourceKind) else EditableSourceKind(str(self.source_kind))
        except Exception as error:
            raise ChangeSetError("UNKNOWN_SOURCE_KIND") from error
        object.__setattr__(self, "source_kind", source)
        if source is not EditableSourceKind.VERIFIED_TABLE:
            raise ChangeSetError("SOURCE_NOT_EDITABLE", reason=readonly_reason(source))

        columns = tuple(self.columns)
        if not columns or any(not isinstance(column, VerifiedColumn) for column in columns):
            raise ChangeSetError("VERIFIED_COLUMNS_REQUIRED")
        names = tuple(column.name for column in columns)
        if len(set(names)) != len(names):
            raise ChangeSetError("DUPLICATE_COLUMN")
        object.__setattr__(self, "columns", columns)
        column_names = set(names)

        primary_key = tuple(str(name) for name in self.primary_key)
        if not primary_key:
            raise ChangeSetError("VERIFIED_PRIMARY_KEY_REQUIRED")
        if len(set(primary_key)) != len(primary_key) or not set(primary_key) <= column_names:
            raise ChangeSetError("INVALID_PRIMARY_KEY")
        for name in primary_key:
            _validate_identifier(name)
        object.__setattr__(self, "primary_key", primary_key)

        if self.concurrency_columns is None:
            concurrency = tuple(
                column.name
                for column in columns
                if column.comparable and column.name not in primary_key
            )
        else:
            concurrency = tuple(str(name) for name in self.concurrency_columns)
        if len(set(concurrency)) != len(concurrency):
            raise ChangeSetError("DUPLICATE_CONCURRENCY_COLUMN")
        if not set(concurrency) <= column_names or set(concurrency) & set(primary_key):
            raise ChangeSetError("INVALID_CONCURRENCY_COLUMNS")
        for name in concurrency:
            column = next(item for item in columns if item.name == name)
            if not column.comparable:
                raise ChangeSetError("NONCOMPARABLE_CONCURRENCY_COLUMN")
        object.__setattr__(self, "concurrency_columns", concurrency)

    @property
    def column_names(self) -> frozenset[str]:
        return frozenset(column.name for column in self.columns)

    @property
    def column_by_name(self) -> dict[str, VerifiedColumn]:
        return {column.name: column for column in self.columns}

    @property
    def read_only_reason(self) -> str:
        return ""


@dataclass(frozen=True, slots=True)
class BoundParameters:
    markers: tuple[str, ...]
    parameters: Any


class SqlDialectPolicy(Protocol):
    """Explicit vendor policy for identifier qualification and bind syntax."""

    def quote_identifier(self, identifier: str) -> str: ...

    def qualified_table(self, target: ManualSessionTarget, table: str) -> str: ...

    def bind_values(self, values: Sequence[Any]) -> BoundParameters: ...


class QmarkSqlDialect:
    """Small portable qmark policy useful for fake drivers and compatible DBs."""

    def quote_identifier(self, identifier: str) -> str:
        _validate_identifier(identifier)
        return '"' + identifier.replace('"', '""') + '"'

    def qualified_table(self, target: ManualSessionTarget, table: str) -> str:
        parts = [part for part in (target.database, target.schema, table) if part]
        return ".".join(self.quote_identifier(part) for part in parts)

    def bind_values(self, values: Sequence[Any]) -> BoundParameters:
        return BoundParameters(tuple("?" for _ in values), tuple(values))


@dataclass(frozen=True, slots=True)
class ChangePreviewStatement:
    operation: str
    sql: str
    parameters: Any
    expected_rowcount: int = 1


@dataclass(frozen=True, slots=True)
class ChangePreview:
    change_set_id: str
    target: ManualSessionTarget
    revision: int
    fingerprint: str
    statements: tuple[ChangePreviewStatement, ...]


@dataclass(frozen=True, slots=True)
class _StagedChange:
    operation: str
    primary_key: tuple[tuple[str, Any], ...]
    original_values: tuple[tuple[str, Any], ...]
    values: tuple[tuple[str, Any], ...]


def _validate_identifier(value: Any) -> str:
    if not isinstance(value, str):
        raise ChangeSetError("UNSAFE_IDENTIFIER")
    name = value
    if not name or len(name) > _MAX_IDENTIFIER_LENGTH or "\x00" in name:
        raise ChangeSetError("UNSAFE_IDENTIFIER")
    return name


def _canonical_value(value: Any) -> Any:
    """Return a typed, deterministic JSON-shaped value for preview hashing."""

    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ChangeSetError("NONFINITE_VALUE_UNSUPPORTED")
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, bytearray):
        return ["bytearray", bytes(value).hex()]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", value.isoformat()]
    if isinstance(value, uuid.UUID):
        return ["uuid", value.hex]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ChangeSetError("UNSTABLE_MAPPING_KEY")
        return ["mapping", [[key, _canonical_value(value[key])] for key in sorted(value)]]
    if isinstance(value, tuple):
        return ["tuple", [_canonical_value(item) for item in value]]
    if isinstance(value, list):
        return ["list", [_canonical_value(item) for item in value]]
    raise ChangeSetError("UNSUPPORTED_PREVIEW_VALUE")


def _values_equal(left: Any, right: Any) -> bool:
    try:
        result = left == right
        return result if isinstance(result, bool) else bool(result)
    except Exception:
        return False


def _freeze_preview_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_preview_value(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_preview_value(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return deepcopy(value)


def _copy_mapping(value: Any, *, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ChangeSetError(code)
    try:
        copied = deepcopy(dict(value))
    except Exception as error:
        raise ChangeSetError("VALUE_NOT_COPYABLE") from error
    if any(not isinstance(key, str) for key in copied):
        raise ChangeSetError(code)
    return copied


class ChangeSet:
    """Stage typed row edits and apply only a still-current confirmed preview."""

    def __init__(self, metadata: VerifiedTableMetadata, dialect: SqlDialectPolicy):
        if not isinstance(metadata, VerifiedTableMetadata):
            raise ChangeSetError("VERIFIED_TABLE_METADATA_REQUIRED")
        if not all(callable(getattr(dialect, method, None)) for method in ("quote_identifier", "qualified_table", "bind_values")):
            raise ChangeSetError("SQL_DIALECT_POLICY_REQUIRED")
        self.metadata = metadata
        self.target = metadata.target
        self._dialect = dialect
        self._change_set_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._changes: list[_StagedChange] = []
        self._row_keys: set[str] = set()
        self._revision = 0
        self._busy = False

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def staged_count(self) -> int:
        with self._lock:
            return len(self._changes)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def stage_update(
        self,
        primary_key: Mapping[str, Any],
        original_values: Mapping[str, Any],
        new_values: Mapping[str, Any],
    ) -> bool:
        with self._lock:
            self._require_idle()
            key = self._normalize_primary_key(primary_key)
            original = self._normalize_original_values(original_values)
            requested = _copy_mapping(new_values, code="INVALID_NEW_VALUES")
            self._validate_known_columns(requested, require_editable=True)
            if set(requested) & set(self.metadata.primary_key):
                raise ChangeSetError("PRIMARY_KEY_EDIT_UNSUPPORTED")
            old_values = dict(original)
            changed = []
            for column in self.metadata.columns:
                name = column.name
                if name not in requested:
                    continue
                value = requested[name]
                if name in old_values and _values_equal(value, old_values[name]):
                    continue
                changed.append((name, deepcopy(value)))
            if not changed:
                return False
            self._append_change(_StagedChange("update", key, original, tuple(changed)))
            return True

    def stage_delete(
        self,
        primary_key: Mapping[str, Any],
        original_values: Mapping[str, Any],
    ) -> None:
        with self._lock:
            self._require_idle()
            key = self._normalize_primary_key(primary_key)
            original = self._normalize_original_values(original_values)
            self._append_change(_StagedChange("delete", key, original, ()))

    def stage_insert(self, values: Mapping[str, Any]) -> None:
        with self._lock:
            self._require_idle()
            inserted = _copy_mapping(values, code="INVALID_INSERT_VALUES")
            self._validate_known_columns(inserted, require_editable=True)
            if not set(self.metadata.primary_key) <= set(inserted):
                raise ChangeSetError("INSERT_PRIMARY_KEY_REQUIRED")
            if any(inserted[name] is None for name in self.metadata.primary_key):
                raise ChangeSetError("NULL_PRIMARY_KEY")
            missing = {
                column.name
                for column in self.metadata.columns
                if column.required_on_insert and column.name not in inserted
            }
            if missing:
                raise ChangeSetError("INSERT_REQUIRED_COLUMN_MISSING")
            key = tuple((name, deepcopy(inserted[name])) for name in self.metadata.primary_key)
            values_tuple = tuple(
                (column.name, deepcopy(inserted[column.name]))
                for column in self.metadata.columns
                if column.name in inserted
            )
            self._append_change(_StagedChange("insert", key, (), values_tuple))

    def discard(self) -> int:
        with self._lock:
            self._require_idle()
            count = len(self._changes)
            if count:
                self._changes.clear()
                self._row_keys.clear()
                self._revision += 1
            return count

    def preview(self) -> ChangePreview:
        with self._lock:
            return self._preview_locked()

    def apply(self, session: ManualSession, confirmed_preview: ChangePreview) -> Future:
        if not isinstance(session, ManualSession):
            raise ChangeSetError("MANUAL_SESSION_REQUIRED")
        if session.target != self.target:
            raise ChangeSetError("SESSION_TARGET_MISMATCH")
        if not isinstance(confirmed_preview, ChangePreview):
            raise ChangeSetError("CONFIRMED_PREVIEW_REQUIRED")

        with self._lock:
            self._require_idle()
            raw_statements = tuple(self._render_change(change) for change in self._changes)
            current = self._preview_locked(raw_statements)
            if (
                confirmed_preview.change_set_id != self._change_set_id
                or confirmed_preview.target != self.target
                or confirmed_preview.revision != current.revision
                or confirmed_preview.fingerprint != current.fingerprint
            ):
                raise ChangeSetError("PREVIEW_STALE")
            if not current.statements:
                raise ChangeSetError("EMPTY_CHANGESET")
            statements = tuple(
                (statement.sql, deepcopy(statement.parameters))
                for statement in raw_statements
            )
            expected = tuple(statement.expected_rowcount for statement in raw_statements)
            self._busy = True

        try:
            batch_future = session.run_savepoint_batch(statements, expected_rowcounts=expected)
        except Exception as error:
            with self._lock:
                self._busy = False
            raise ChangeSetError("BATCH_SUBMISSION_FAILED") from error

        result_future: Future = Future()

        def finish(source: Future) -> None:
            try:
                batch_result: ManualBatchResult = source.result()
            except BaseException as error:
                with self._lock:
                    self._revision += 1
                    self._busy = False
                if not result_future.cancelled():
                    result_future.set_exception(error)
                return
            with self._lock:
                if batch_result.succeeded:
                    self._changes.clear()
                    self._row_keys.clear()
                self._revision += 1
                self._busy = False
            if not result_future.cancelled():
                result_future.set_result(batch_result)

        batch_future.add_done_callback(finish)
        return result_future

    def _require_idle(self) -> None:
        if self._busy:
            raise ChangeSetError("CHANGESET_BUSY")

    def _validate_known_columns(self, values: Mapping[str, Any], *, require_editable: bool) -> None:
        unknown = set(values) - self.metadata.column_names
        if unknown:
            raise ChangeSetError("UNKNOWN_COLUMN")
        if require_editable:
            by_name = self.metadata.column_by_name
            if any(not by_name[name].editable for name in values):
                raise ChangeSetError("COLUMN_NOT_EDITABLE")
        for value in values.values():
            _canonical_value(value)

    def _normalize_primary_key(self, values: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
        key = _copy_mapping(values, code="INVALID_PRIMARY_KEY_VALUES")
        if set(key) != set(self.metadata.primary_key):
            raise ChangeSetError("COMPLETE_PRIMARY_KEY_REQUIRED")
        if any(value is None for value in key.values()):
            raise ChangeSetError("NULL_PRIMARY_KEY")
        self._validate_known_columns(key, require_editable=False)
        return tuple((name, deepcopy(key[name])) for name in self.metadata.primary_key)

    def _normalize_original_values(
        self,
        values: Mapping[str, Any],
    ) -> tuple[tuple[str, Any], ...]:
        original = _copy_mapping(values, code="INVALID_ORIGINAL_VALUES")
        self._validate_known_columns(original, require_editable=False)
        allowed = set(self.metadata.concurrency_columns or ())
        if not set(original) <= allowed:
            raise ChangeSetError("NONCOMPARABLE_ORIGINAL_VALUE")
        required = set(self.metadata.concurrency_columns or ())
        if not required <= set(original):
            raise ChangeSetError("ORIGINAL_VALUES_INCOMPLETE")
        normalized = tuple(
            (name, deepcopy(original[name]))
            for name in self.metadata.concurrency_columns or ()
        )
        for _name, value in normalized:
            _canonical_value(value)
        return normalized

    def _row_identity(self, key: tuple[tuple[str, Any], ...]) -> str:
        canonical = [[name, _canonical_value(value)] for name, value in key]
        return json.dumps(canonical, ensure_ascii=True, separators=(",", ":"))

    def _append_change(self, change: _StagedChange) -> None:
        identity = self._row_identity(change.primary_key)
        if identity in self._row_keys:
            raise ChangeSetError("DUPLICATE_ROW_CHANGE")
        self._changes.append(change)
        self._row_keys.add(identity)
        self._revision += 1

    def _preview_locked(
        self,
        statements: tuple[ChangePreviewStatement, ...] | None = None,
    ) -> ChangePreview:
        if statements is None:
            statements = tuple(self._render_change(change) for change in self._changes)
        digest_payload = {
            "change_set_id": self._change_set_id,
            "target": self.target.as_dict(),
            "revision": self._revision,
            "statements": [
                {
                    "operation": item.operation,
                    "sql": item.sql,
                    "parameters": _canonical_value(item.parameters),
                    "expected_rowcount": item.expected_rowcount,
                }
                for item in statements
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        safe_statements = tuple(
            ChangePreviewStatement(item.operation, item.sql, _freeze_preview_value(item.parameters), item.expected_rowcount)
            for item in statements
        )
        return ChangePreview(
            self._change_set_id,
            self.target,
            self._revision,
            fingerprint,
            safe_statements,
        )

    def _render_change(self, change: _StagedChange) -> ChangePreviewStatement:
        table = self._dialect.qualified_table(self.target, self.metadata.table)
        if not isinstance(table, str) or not table.strip():
            raise ChangeSetError("INVALID_QUALIFIED_TABLE")
        quote = self._dialect.quote_identifier
        parameters: list[Any] = []
        if change.operation == "update":
            parts: list[Any] = [f"UPDATE {table} SET "]
            for index, (name, value) in enumerate(change.values):
                if index:
                    parts.append(", ")
                parts.extend((f"{quote(name)} = ", _BIND))
                parameters.append(value)
            where_parts, where_values = self._render_where(change)
            parameters.extend(where_values)
            parts.append(" WHERE ")
            parts.extend(where_parts)
            sql, bound = self._bind_sql(parts, parameters)
        elif change.operation == "delete":
            where_parts, parameters = self._render_where(change)
            sql, bound = self._bind_sql([f"DELETE FROM {table} WHERE ", *where_parts], parameters)
        elif change.operation == "insert":
            columns = [name for name, _value in change.values]
            parameters = [value for _name, value in change.values]
            parts = [
                f"INSERT INTO {table} ({', '.join(quote(name) for name in columns)}) VALUES ("
            ]
            for index in range(len(parameters)):
                if index:
                    parts.append(", ")
                parts.append(_BIND)
            parts.append(")")
            sql, bound = self._bind_sql(parts, parameters)
        else:
            raise ChangeSetError("UNKNOWN_CHANGE_OPERATION")
        return ChangePreviewStatement(change.operation, sql, bound, 1)

    def _render_where(self, change: _StagedChange) -> tuple[list[Any], list[Any]]:
        conditions: list[tuple[str, Any]] = []
        key_values = dict(change.primary_key)
        old_values = dict(change.original_values)
        for name in self.metadata.primary_key:
            conditions.append((name, key_values[name]))
        for name in self.metadata.concurrency_columns or ():
            conditions.append((name, old_values[name]))
        parts: list[Any] = []
        parameters = []
        for index, (name, value) in enumerate(conditions):
            if index:
                parts.append(" AND ")
            if value is None:
                parts.append(f"{self._dialect.quote_identifier(name)} IS NULL")
            else:
                parts.extend((f"{self._dialect.quote_identifier(name)} = ", _BIND))
                parameters.append(value)
        return parts, parameters

    def _bind_sql(self, parts: Sequence[Any], values: Sequence[Any]) -> tuple[str, Any]:
        try:
            bound = self._dialect.bind_values(tuple(values))
        except Exception as error:
            raise ChangeSetError("DIALECT_BIND_FAILED") from error
        if not isinstance(bound, BoundParameters) or len(bound.markers) != len(values):
            raise ChangeSetError("INVALID_BOUND_PARAMETERS")
        if sum(part is _BIND for part in parts) != len(bound.markers):
            raise ChangeSetError("BIND_MARKER_COUNT_MISMATCH")
        output: list[str] = []
        marker_index = 0
        for part in parts:
            if part is _BIND:
                marker = bound.markers[marker_index]
                marker_index += 1
                if not isinstance(marker, str) or not marker:
                    raise ChangeSetError("INVALID_BIND_MARKER")
                output.append(marker)
            elif isinstance(part, str):
                output.append(part)
            else:
                raise ChangeSetError("INVALID_BIND_MARKER")
        sql = "".join(output)
        if not isinstance(bound.parameters, (Mapping, tuple, list)):
            raise ChangeSetError("INVALID_BOUND_PARAMETER_CONTAINER")
        return sql, deepcopy(bound.parameters)


__all__ = [
    "BoundParameters",
    "ChangePreview",
    "ChangePreviewStatement",
    "ChangeSet",
    "ChangeSetError",
    "EditableSourceKind",
    "QmarkSqlDialect",
    "READ_ONLY_REASONS",
    "SqlDialectPolicy",
    "VerifiedColumn",
    "VerifiedTableMetadata",
    "readonly_reason",
]
