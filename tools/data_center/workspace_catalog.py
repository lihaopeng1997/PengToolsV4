"""Read-only catalog projection for the unified data-center workspace.

The catalog reads the existing connection store without normalizing records on
load. UI-facing summaries omit authentication fields; full profiles stay behind
``get_profile`` for an explicitly opened connection workspace.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Iterable

from tools.db_contracts import DIALECTS


KNOWN_ENVIRONMENTS = {
    "local": "Local",
    "development": "Development",
    "dev": "Development",
    "test": "Test",
    "testing": "Test",
    "staging": "Staging",
    "stage": "Staging",
    "production": "Production",
    "prod": "Production",
}
_SOURCE_REVISION_UNKNOWN = object()


class _ConnectionRecords(list):
    """Default-reader rows with the byte revision used for safe mutations."""

    def __init__(self, rows: list[object], revision: str) -> None:
        super().__init__(rows)
        self.revision = revision


@dataclass(frozen=True)
class ConnectionSummary:
    connection_id: str
    name: str
    dialect: str
    engine_label: str
    group_path: tuple[str, ...]
    environment_key: str
    environment_label: str
    environment_raw: str
    host: str
    database: str
    favorite: bool


def _group_parts(value: object) -> tuple[str, ...]:
    if isinstance(value, (tuple, list)):
        raw_parts = value
    elif isinstance(value, str):
        raw_parts = value.replace("\\", "/").split("/")
    elif value is None:
        raw_parts = ()
    else:
        raw_parts = ()
    result = []
    for part in raw_parts:
        if not isinstance(part, str):
            continue
        try:
            text = part.strip()
        except Exception:
            continue
        if text:
            result.append(text)
    return tuple(result)


def _environment(value: object) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    key = raw.casefold()
    if key in KNOWN_ENVIRONMENTS:
        canonical = key
        label = KNOWN_ENVIRONMENTS[key]
        color_key = {
            "local": "local", "development": "development", "dev": "development",
            "test": "test", "testing": "test", "staging": "staging", "stage": "staging",
            "production": "production", "prod": "production",
        }[key]
        return color_key, label, raw
    return "unknown", "Unknown environment", raw


def _load_connection_records() -> list[object]:
    """Read the existing store without silently converting failures to empty.

    Existing files must contain either the historical raw list or an object
    with an ``items`` list. FileNotFoundError is left to WorkspaceCatalog, which
    treats it as empty until the default loader has read an existing file. Row values are
    returned unchanged so malformed records can be reported locally.
    """
    from config import HARNESS_CONNECTIONS_FILE

    with open(HARNESS_CONNECTIONS_FILE, "rb") as stream:
        source = stream.read(16 * 1024 * 1024 + 1)
    if len(source) > 16 * 1024 * 1024:
        raise ValueError("connection catalog is too large")
    raw = json.loads(source.decode("utf-8"))
    if isinstance(raw, list):
        return _ConnectionRecords(raw, hashlib.sha256(source).hexdigest())
    if isinstance(raw, dict):
        version_marker = object()
        version = raw.get("format_version", version_marker)
        if version is not version_marker and (type(version) is not int or version != 1):
            raise ValueError("unsupported connection catalog format version")
        if isinstance(raw.get("items"), list):
            return _ConnectionRecords(raw["items"], hashlib.sha256(source).hexdigest())
    raise ValueError("connection catalog has an unsupported JSON shape")


class WorkspaceCatalog:
    """Small store adapter with injectable I/O for isolated tests."""

    def __init__(
        self,
        *,
        loader: Callable[[], Iterable[object]] | None = None,
        saver: Callable[[dict, str | None], dict] | None = None,
        deleter: Callable[[str], None] | None = None,
    ) -> None:
        self._uses_default_loader = loader is None
        self._uses_default_saver = saver is None
        self._uses_default_deleter = deleter is None
        if loader is None:
            loader = _load_connection_records
        if saver is None or deleter is None:
            from tools import db_connect
            saver = saver or db_connect.upsert_connection
            deleter = deleter or db_connect.delete_connection
        self._loader = loader
        self._saver = saver
        self._deleter = deleter
        self._profiles: dict[str, dict] = {}
        self._summaries: tuple[ConnectionSummary, ...] = ()
        self.ambiguous_ids: tuple[str, ...] = ()
        self.record_errors: tuple[str, ...] = ()
        self.last_error = ""
        self._has_successful_read = False
        self._source_revision: str | None | object = _SOURCE_REVISION_UNKNOWN

    def refresh(self) -> tuple[ConnectionSummary, ...]:
        profiles: dict[str, dict] = {}
        summaries: list[ConnectionSummary] = []
        labels = dict(DIALECTS)
        try:
            loaded_rows = self._loader()
            rows = list(loaded_rows or ())
            source_revision = (
                getattr(loaded_rows, "revision", _SOURCE_REVISION_UNKNOWN)
                if self._uses_default_loader else _SOURCE_REVISION_UNKNOWN
            )
        except FileNotFoundError as exc:
            if self._uses_default_loader and (
                not self._has_successful_read or self._source_revision is None
            ):
                rows = []
                source_revision = None
            else:
                self.last_error = f"Connection catalog could not be loaded ({exc.__class__.__name__})."
                return self._summaries
        except Exception as exc:
            self.last_error = f"Connection catalog could not be loaded ({exc.__class__.__name__})."
            return self._summaries
        self._has_successful_read = True
        self.last_error = ""
        self._source_revision = source_revision
        rows_by_id: dict[str, list[dict]] = {}
        record_errors: list[str] = []
        for row_index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                record_errors.append(f"Record {row_index}: malformed connection record.")
                continue
            try:
                item = dict(row)
                connection_id = str(item.get("id") or item.get("connection_id") or "").strip()
            except Exception:
                record_errors.append(f"Record {row_index}: malformed connection record.")
                continue
            if not connection_id:
                record_errors.append(f"Record {row_index}: missing connection ID.")
                continue
            rows_by_id.setdefault(connection_id, []).append(item)
        ambiguous = tuple(sorted(key for key, group in rows_by_id.items() if len(group) > 1))
        self.ambiguous_ids = ambiguous
        for connection_id, group in rows_by_id.items():
            if connection_id in ambiguous:
                continue
            item = group[0]
            profiles[connection_id] = item
            try:
                dialect = str(item.get("dialect") or "").strip().lower()
                env_key, env_label, env_raw = _environment(item.get("environment"))
                raw_group = item.get("group_path")
                if (
                    isinstance(raw_group, (list, tuple))
                    and any(not isinstance(segment, str) for segment in raw_group)
                ) or (raw_group is not None and not isinstance(raw_group, (str, list, tuple))):
                    record_errors.append(f"Connection {connection_id}: malformed project path ignored.")
                summaries.append(ConnectionSummary(
                    connection_id=connection_id,
                    name=str(item.get("name") or connection_id),
                    dialect=dialect,
                    engine_label=str(labels.get(dialect, dialect.upper() or "Unknown engine")),
                    group_path=_group_parts(item.get("group_path")),
                    environment_key=env_key,
                    environment_label=env_label,
                    environment_raw=env_raw,
                    host=str(item.get("host") or ""),
                    database=str(item.get("database") or ""),
                    favorite=bool(item.get("favorite", item.get("is_favorite", False))),
                ))
            except Exception:
                profiles.pop(connection_id, None)
                record_errors.append(f"Connection {connection_id}: invalid catalog fields.")
        self._profiles = profiles
        self._summaries = tuple(summaries)
        self.record_errors = tuple(record_errors)
        return self._summaries

    @property
    def summaries(self) -> tuple[ConnectionSummary, ...]:
        return self._summaries

    def get_summary(self, connection_id: str) -> ConnectionSummary | None:
        wanted = str(connection_id or "")
        return next((item for item in self._summaries if item.connection_id == wanted), None)

    def get_profile(self, connection_id: str) -> dict | None:
        item = self._profiles.get(str(connection_id or ""))
        return dict(item) if item is not None else None

    def save(self, item: dict, password: str | None = None) -> dict:
        if self.last_error:
            return {}
        data = dict(item or {})
        connection_id = str(data.get("id") or data.get("connection_id") or "").strip()
        existing = self.get_profile(connection_id) if connection_id else None
        if existing is not None:
            merged = dict(existing)
            merged.update(data)
            data = merged
        if self._uses_default_saver and self._source_revision is not _SOURCE_REVISION_UNKNOWN:
            saved = self._saver(data, password, expected_revision=self._source_revision)
        else:
            saved = self._saver(data, password)
        result = dict(saved or data)
        self.refresh()
        return result

    def set_favorite(self, connection_id: str, favorite: bool) -> dict | None:
        item = self.get_profile(connection_id)
        if item is None:
            return None
        item["favorite"] = bool(favorite)
        return self.save(item, None)

    def delete(self, connection_id: str) -> None:
        if self.last_error:
            return
        if self._uses_default_deleter and self._source_revision is not _SOURCE_REVISION_UNKNOWN:
            self._deleter(str(connection_id or ""), expected_revision=self._source_revision)
        else:
            self._deleter(str(connection_id or ""))
        self.refresh()


__all__ = ["ConnectionSummary", "WorkspaceCatalog", "KNOWN_ENVIRONMENTS"]
