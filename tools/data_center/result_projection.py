"""Bounded, typed projection of data-center results for model input.

The database/grid side keeps native values in ``QueryResult``/``ToolResult``.
This module creates a separate JSON-shaped view for the model.  It preserves
the important distinctions (NULL, empty text, decimal, dates and binary),
redacts obvious credential columns, and applies both per-result and per-run
UTF-8 budgets.  It does not write files or contact a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
import json
import math
import re
from typing import Any

from .contracts import QueryResult, ToolResult


DEFAULT_MAX_ROWS = 50
HARD_MAX_ROWS = 100
MAX_FETCH_ROWS = 201
DEFAULT_MAX_CELL_CHARS = 512
DEFAULT_MAX_RESULT_BYTES = 12 * 1024
DEFAULT_MAX_CUMULATIVE_BYTES = 48 * 1024
DEFAULT_MAX_COLUMNS = 100

_MISSING = object()
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


@dataclass(slots=True)
class ProjectionBudget:
    """Mutable byte budget shared by all tool results in one Agent run."""

    max_bytes: int = DEFAULT_MAX_CUMULATIVE_BYTES
    used_bytes: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if isinstance(self.used_bytes, bool) or not isinstance(self.used_bytes, int) or self.used_bytes < 0:
            raise ValueError("used_bytes must be a non-negative integer")
        if self.used_bytes > self.max_bytes:
            raise ValueError("used_bytes cannot exceed max_bytes")

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.max_bytes - self.used_bytes)

    def reserve(self, size: int) -> bool:
        if size < 0 or size > self.remaining_bytes:
            return False
        self.used_bytes += size
        return True


@dataclass(frozen=True, slots=True)
class ProjectedResult:
    """Model-safe result and the accounting metadata for one projection."""

    ok: bool
    code: str
    data: Any = None
    evidence_id: str | None = None
    query_id: str | None = None
    scope: Any = None
    truncated: bool = False
    limits: Mapping[str, Any] = field(default_factory=dict)
    elapsed_ms: int | float | None = None
    bytes_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "data": self.data,
            "evidence_id": self.evidence_id,
            "query_id": self.query_id,
            "scope": self.scope,
            "truncated": self.truncated,
            "limits": dict(self.limits),
            "elapsed_ms": self.elapsed_ms,
        }

    def as_model_message(self, call_id: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": _json_text(self.as_dict()),
        }


class ResultProjector:
    """Project native tool/query results under fixed row, cell and byte caps."""

    def __init__(
        self,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_cell_chars: int = DEFAULT_MAX_CELL_CHARS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_cumulative_bytes: int = DEFAULT_MAX_CUMULATIVE_BYTES,
        sensitive_columns: Iterable[str] = (),
    ) -> None:
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= HARD_MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {HARD_MAX_ROWS}")
        if isinstance(max_cell_chars, bool) or not isinstance(max_cell_chars, int) or max_cell_chars <= 0:
            raise ValueError("max_cell_chars must be positive")
        if isinstance(max_result_bytes, bool) or not isinstance(max_result_bytes, int) or max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        if isinstance(max_cumulative_bytes, bool) or not isinstance(max_cumulative_bytes, int) or max_cumulative_bytes <= 0:
            raise ValueError("max_cumulative_bytes must be positive")
        self.max_rows = max_rows
        self.max_cell_chars = max_cell_chars
        self.max_result_bytes = max_result_bytes
        self.max_cumulative_bytes = max_cumulative_bytes
        self.sensitive_columns = frozenset(_normalize_name(value) for value in sensitive_columns if str(value).strip())

    def project(
        self,
        result: ToolResult | QueryResult | Mapping[str, Any] | Any,
        *,
        budget: ProjectionBudget | None = None,
        sensitive_columns: Iterable[str] = (),
        max_rows: int | None = None,
        max_cell_chars: int | None = None,
    ) -> ProjectedResult:
        """Return a separate JSON-shaped view; the input object is untouched."""

        row_cap = self.max_rows if max_rows is None else max_rows
        cell_cap = self.max_cell_chars if max_cell_chars is None else max_cell_chars
        if isinstance(row_cap, bool) or not isinstance(row_cap, int) or not 1 <= row_cap <= HARD_MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {HARD_MAX_ROWS}")
        if isinstance(cell_cap, bool) or not isinstance(cell_cap, int) or cell_cap <= 0:
            raise ValueError("max_cell_chars must be positive")
        budget = budget or ProjectionBudget(self.max_cumulative_bytes)
        normalized = _normalize_result(result)
        extra_sensitive = frozenset(_normalize_name(value) for value in sensitive_columns if str(value).strip())
        sensitive = self.sensitive_columns.union(extra_sensitive)

        if not normalized["ok"]:
            return self._project_failure(normalized, budget)

        columns, rows, source_truncated, source_row_count = _tabular_data(normalized["data"])
        column_names = tuple(item["name"] for item in columns)
        projected_columns = [
            {
                "name": item["name"],
                "type": item.get("type") or "unknown",
                "sensitive": bool(item.get("sensitive")) or _is_sensitive_name(item["name"], sensitive),
            }
            for item in columns[:DEFAULT_MAX_COLUMNS]
        ]
        column_sensitive = {
            item["name"]: bool(item.get("sensitive")) or _is_sensitive_name(item["name"], sensitive)
            for item in columns[:DEFAULT_MAX_COLUMNS]
        }
        # A mapping row may contain a key not in the source descriptor.  The
        # tabular normalizer folds such keys into column_names before here.
        projected_rows: list[list[Any]] = []
        row_limit_hit = source_truncated or len(rows) > row_cap or source_row_count > row_cap
        candidate_rows = rows[:row_cap]
        for row in candidate_rows:
            projected_rows.append(
                [
                    _project_value(
                        value,
                        sensitive=column_sensitive.get(column_name, _is_sensitive_name(column_name, sensitive)),
                        max_cell_chars=cell_cap,
                    )
                    for column_name, value in zip(column_names, row, strict=False)
                ]
            )

        base_limits = {
            "row_limit": row_cap,
            "hard_row_limit": HARD_MAX_ROWS,
            "max_fetch_rows": MAX_FETCH_ROWS,
            "cell_chars": cell_cap,
            "result_bytes": self.max_result_bytes,
            "cumulative_bytes": budget.max_bytes,
            "source_rows": source_row_count,
        }
        payload_base = {
            "columns": projected_columns,
            "rows": [],
            "row_count": source_row_count,
        }
        chosen_rows: list[list[Any]] = []
        max_available = min(self.max_result_bytes, budget.remaining_bytes)
        # Fit rows against the complete envelope, not just the row array, so
        # the published model message obeys the stated UTF-8 budget.
        for row in projected_rows:
            trial = dict(payload_base)
            trial["rows"] = chosen_rows + [row]
            trial_truncated = row_limit_hit or len(chosen_rows) + 1 < len(rows)
            trial_limits = dict(base_limits)
            trial_limits["returned_rows"] = len(chosen_rows) + 1
            trial_limits["truncated"] = trial_truncated
            envelope = self._envelope(normalized, trial, trial_truncated, trial_limits)
            if _utf8_size(envelope) > max_available:
                row_limit_hit = True
                break
            chosen_rows.append(row)

        # If metadata alone does not fit, reduce the metadata to a small,
        # bounded form.  This keeps the budget invariant deterministic even
        # when a fake result contains hundreds of long column names.
        payload = dict(payload_base)
        payload["rows"] = chosen_rows
        final_truncated = row_limit_hit or len(chosen_rows) < len(rows)
        limits = dict(base_limits)
        limits.update({"returned_rows": len(chosen_rows), "truncated": final_truncated})
        envelope = self._envelope(normalized, payload, final_truncated, limits)
        if _utf8_size(envelope) > max_available:
            projected_columns = _fit_columns(projected_columns, max_available, normalized, limits, self._envelope)
            payload["columns"] = projected_columns
            envelope = self._envelope(normalized, payload, final_truncated, limits)
        if _utf8_size(envelope) > max_available:
            # The remaining budget can be smaller than even a normal metadata
            # envelope.  Keep a compact, truthful truncation marker.
            payload = {"columns": [], "rows": [], "row_count": source_row_count}
            final_truncated = True
            limits["returned_rows"] = 0
            limits["truncated"] = True
            envelope = self._envelope(normalized, payload, final_truncated, limits)

        size = _utf8_size(envelope)
        if not budget.reserve(size):
            # A concurrent/host supplied budget may have changed between the
            # candidate calculation and reserve.  Return a small marker and
            # do not pretend any rows were delivered.
            payload = {"columns": [], "rows": [], "row_count": source_row_count}
            final_truncated = True
            limits["returned_rows"] = 0
            limits["truncated"] = True
            envelope = self._envelope(normalized, payload, final_truncated, limits)
            size = _utf8_size(envelope)
            if not budget.reserve(min(size, budget.remaining_bytes)):
                size = 0

        return ProjectedResult(
            ok=True,
            code=normalized["code"],
            data=payload,
            evidence_id=normalized["evidence_id"],
            query_id=normalized["query_id"],
            scope=_safe_scope(normalized["scope"]),
            truncated=final_truncated,
            limits=limits,
            elapsed_ms=normalized["elapsed_ms"],
            bytes_used=size,
        )

    def _project_failure(self, normalized: Mapping[str, Any], budget: ProjectionBudget) -> ProjectedResult:
        limits = {
            "result_bytes": self.max_result_bytes,
            "cumulative_bytes": budget.max_bytes,
            "returned_rows": 0,
            "truncated": False,
        }
        envelope = self._envelope(
            normalized,
            {"message": "工具未成功完成，未提供结果数据"},
            False,
            limits,
        )
        size = _utf8_size(envelope)
        if size <= self.max_result_bytes and budget.reserve(size):
            pass
        else:
            size = 0
        return ProjectedResult(
            ok=False,
            code=normalized["code"],
            data={"message": "工具未成功完成，未提供结果数据"},
            evidence_id=normalized["evidence_id"],
            query_id=normalized["query_id"],
            scope=_safe_scope(normalized["scope"]),
            truncated=False,
            limits=limits,
            elapsed_ms=normalized["elapsed_ms"],
            bytes_used=size,
        )

    @staticmethod
    def _envelope(
        normalized: Mapping[str, Any],
        data: Any,
        truncated: bool,
        limits: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "ok": bool(normalized["ok"]),
            "code": normalized["code"],
            "data": data,
            "evidence_id": normalized["evidence_id"],
            "query_id": normalized["query_id"],
            "scope": _safe_scope(normalized["scope"]),
            "truncated": truncated,
            "limits": dict(limits),
            "elapsed_ms": normalized["elapsed_ms"],
        }


def project_result(
    result: ToolResult | QueryResult | Mapping[str, Any] | Any,
    *,
    budget: ProjectionBudget | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_cell_chars: int = DEFAULT_MAX_CELL_CHARS,
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    max_cumulative_bytes: int = DEFAULT_MAX_CUMULATIVE_BYTES,
    sensitive_columns: Iterable[str] = (),
) -> ProjectedResult:
    """Convenience function using the DC-01 defaults."""

    return ResultProjector(
        max_rows=max_rows,
        max_cell_chars=max_cell_chars,
        max_result_bytes=max_result_bytes,
        max_cumulative_bytes=max_cumulative_bytes,
        sensitive_columns=sensitive_columns,
    ).project(result, budget=budget)


project_tool_result = project_result
project_query_result = project_result


def _normalize_result(result: ToolResult | QueryResult | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(result, ToolResult):
        return {
            "ok": result.ok,
            "code": result.code,
            "data": result.data,
            "evidence_id": result.evidence_id,
            "query_id": result.query_id,
            "scope": result.scope,
            "truncated": result.truncated,
            "limits": dict(result.limits),
            "elapsed_ms": result.elapsed_ms,
        }
    if isinstance(result, QueryResult):
        return {
            "ok": result.ok,
            "code": result.code,
            "data": {"columns": result.columns, "rows": result.rows},
            "evidence_id": result.evidence_id,
            "query_id": result.query_id,
            "scope": None,
            "truncated": result.truncated,
            "limits": {},
            "elapsed_ms": result.elapsed_ms,
        }
    if isinstance(result, Mapping):
        return {
            "ok": bool(result.get("ok", True)),
            "code": str(result.get("code", "OK" if result.get("ok", True) else "TOOL_FAILED")),
            "data": result.get("data", result),
            "evidence_id": result.get("evidence_id", result.get("evidenceId")),
            "query_id": result.get("query_id", result.get("queryId")),
            "scope": result.get("scope"),
            "truncated": bool(result.get("truncated", False)),
            "limits": result.get("limits", {}) or {},
            "elapsed_ms": result.get("elapsed_ms", result.get("elapsedMs")),
        }
    return {
        "ok": True,
        "code": "OK",
        "data": result,
        "evidence_id": None,
        "query_id": None,
        "scope": None,
        "truncated": False,
        "limits": {},
        "elapsed_ms": None,
    }


def _tabular_data(data: Any) -> tuple[list[dict[str, Any]], list[tuple[Any, ...]], bool, int]:
    source_truncated = False
    source_row_count: int | None = None
    columns_raw: Any = _MISSING
    rows_raw: Any = _MISSING
    if isinstance(data, Mapping):
        columns_raw = data.get("columns", _MISSING)
        rows_raw = data.get("rows", data.get("items", _MISSING))
        source_truncated = bool(data.get("truncated", False))
        raw_count = data.get("row_count", data.get("total_rows", data.get("count", _MISSING)))
        if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0:
            source_row_count = raw_count
    if rows_raw is _MISSING:
        if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
            rows_raw = data
        else:
            rows_raw = [data]
    if not isinstance(rows_raw, Sequence) or isinstance(rows_raw, (str, bytes, bytearray)):
        rows_raw = [rows_raw]
    rows_list = list(rows_raw)
    columns = _normalize_columns(columns_raw, rows_list)
    names = [item["name"] for item in columns]

    # Add keys found in mapping rows to a stable column order.
    if any(isinstance(row, Mapping) for row in rows_list):
        for row in rows_list:
            if isinstance(row, Mapping):
                for key in row:
                    key_name = str(key)
                    if key_name not in names and len(columns) < DEFAULT_MAX_COLUMNS:
                        columns.append({"name": key_name, "type": "unknown", "sensitive": False})
                        names.append(key_name)

    normalized_rows: list[tuple[Any, ...]] = []
    for row in rows_list[:MAX_FETCH_ROWS]:
        if isinstance(row, Mapping):
            normalized_rows.append(tuple(row.get(name, _MISSING) for name in names))
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            values = list(row)
            if len(values) > len(names):
                for index in range(len(names), min(len(values), DEFAULT_MAX_COLUMNS)):
                    name = f"column_{index + 1}"
                    columns.append({"name": name, "type": "unknown", "sensitive": False})
                    names.append(name)
            normalized_rows.append(tuple(values[: len(names)] + [_MISSING] * max(0, len(names) - len(values))))
        else:
            if not columns:
                columns = [{"name": "value", "type": "unknown"}]
                names = ["value"]
            normalized_rows.append((row,))
    if len(rows_list) > MAX_FETCH_ROWS:
        source_truncated = True
    if source_row_count is None:
        source_row_count = len(rows_list)
    return columns[:DEFAULT_MAX_COLUMNS], normalized_rows, source_truncated, source_row_count


def _normalize_columns(raw: Any, rows: Sequence[Any]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    if raw is not _MISSING and isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        for index, item in enumerate(raw[:DEFAULT_MAX_COLUMNS]):
            if isinstance(item, Mapping):
                name = item.get("name", item.get("column", f"column_{index + 1}"))
                kind = item.get("type", item.get("data_type", "unknown"))
                sensitive = bool(item.get("sensitive", False))
            else:
                name, kind, sensitive = item, "unknown", False
            columns.append({"name": str(name), "type": str(kind), "sensitive": sensitive})
    if columns:
        return columns
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, Mapping):
            for key in row:
                name = str(key)
                if name not in seen and len(columns) < DEFAULT_MAX_COLUMNS:
                    columns.append({"name": name, "type": "unknown", "sensitive": False})
                    seen.add(name)
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            while len(columns) < min(len(row), DEFAULT_MAX_COLUMNS):
                index = len(columns)
                columns.append({"name": f"column_{index + 1}", "type": "unknown", "sensitive": False})
        else:
            if not columns:
                columns.append({"name": "value", "type": "unknown", "sensitive": False})
    return columns


def _project_value(value: Any, *, sensitive: bool, max_cell_chars: int, depth: int = 0) -> Any:
    if sensitive:
        return {"type": "redacted"}
    if value is _MISSING:
        return {"type": "missing"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": format(value, "f")}
    if isinstance(value, float):
        return {"type": "number", "value": value if math.isfinite(value) else str(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "bytes", "size": len(value)}
    if isinstance(value, str):
        if len(value) > max_cell_chars:
            return {"type": "string", "value": value[:max_cell_chars], "truncated": True}
        return {"type": "string", "value": value}
    if depth >= 8:
        return {"type": "string", "value": str(value)[:max_cell_chars], "truncated": len(str(value)) > max_cell_chars}
    if isinstance(value, Mapping):
        mapped: dict[str, Any] = {}
        for key, item in list(value.items())[:DEFAULT_MAX_COLUMNS]:
            key_name = str(key)
            mapped[key_name] = _project_value(
                item,
                sensitive=_is_sensitive_name(key_name, frozenset()),
                max_cell_chars=max_cell_chars,
                depth=depth + 1,
            )
        return {"type": "object", "value": mapped}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {
            "type": "array",
            "value": [
                _project_value(item, sensitive=False, max_cell_chars=max_cell_chars, depth=depth + 1)
                for item in list(value)[:DEFAULT_MAX_COLUMNS]
            ],
        }
    # BSON ObjectId/Decimal128 and other driver types are represented without
    # importing a driver.  Their textual representation is still bounded.
    text = str(value)
    return {"type": value.__class__.__name__.casefold(), "value": text[:max_cell_chars], "truncated": len(text) > max_cell_chars}


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _is_sensitive_name(name: Any, extra: frozenset[str]) -> bool:
    normalized = _normalize_name(name)
    return normalized in _SENSITIVE_NAMES or normalized in extra or bool(_SENSITIVE_PART.search(normalized))


def _safe_scope(scope: Any) -> Any:
    if scope is None:
        return None
    if isinstance(scope, Mapping):
        allowed = {"connection_id", "database", "schema", "schema_allowlist", "object_id", "collection_id"}
        safe: dict[str, Any] = {}
        for key, value in scope.items():
            if str(key) in allowed and isinstance(value, (str, int, float, bool, type(None), list, tuple)):
                if isinstance(value, (list, tuple)):
                    safe[str(key)] = [str(item)[:512] for item in value[:20]]
                elif isinstance(value, str):
                    safe[str(key)] = value[:512]
                else:
                    safe[str(key)] = value
        return safe
    return None


def _fit_columns(
    columns: list[dict[str, Any]],
    max_available: int,
    normalized: Mapping[str, Any],
    limits: Mapping[str, Any],
    envelope_fn: Any,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for column in columns:
        trial = selected + [column]
        data = {"columns": trial, "rows": [], "row_count": 0}
        if _utf8_size(envelope_fn(normalized, data, True, limits)) > max_available:
            break
        selected.append(column)
    return selected


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _utf8_size(value: Any) -> int:
    return len(_json_text(value).encode("utf-8"))


__all__ = [
    "DEFAULT_MAX_CELL_CHARS",
    "DEFAULT_MAX_CUMULATIVE_BYTES",
    "DEFAULT_MAX_RESULT_BYTES",
    "DEFAULT_MAX_ROWS",
    "HARD_MAX_ROWS",
    "MAX_FETCH_ROWS",
    "ProjectedResult",
    "ProjectionBudget",
    "ResultProjector",
    "project_query_result",
    "project_result",
    "project_tool_result",
]
