"""SQLite persistence for non-secret data-center workspace text and layout.

The store has no Qt, driver, profile, or connection dependency. Construction only
captures an explicit path; each public operation opens and closes its own local
SQLite connection so callers can run it on a worker thread without restoring a
database session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import TypeAlias


SCHEMA_VERSION = 2
APPLICATION_ID = 0x50545753  # "PTWS"
MAX_TEXT_BYTES = 256 * 1024
MAX_SEARCH_CHARS = 256
MAX_DRAFTS = 500
MAX_HISTORY = 5000
MAX_FAVORITES = 1000
MAX_LAYOUT_TABS = 500
MAX_STORE_BYTES = 64 * 1024 * 1024
DEFAULT_SEARCH_LIMIT = 100
MAX_SEARCH_LIMIT = 200
SQLITE_TIMEOUT_SECONDS = 3.0
MAX_HISTORY_ROW_COUNT = 1_000_000_000
MAX_HISTORY_EVIDENCE_ITEMS = 16
MAX_HISTORY_EVIDENCE_BYTES = 8 * 1024


class WorkspaceStoreError(RuntimeError):
    """Stable, non-sensitive workspace persistence error."""

    def __init__(self, code: str):
        self.code = str(code or "WORKSPACE_STORE_FAILED")
        super().__init__(self.code)


class WorkspaceTextKind(str, Enum):
    SQL = "sql"
    NOSQL = "nosql"


class WorkspaceTabKind(str, Enum):
    SQL = "sql"
    REDIS = "redis"
    MONGO = "mongo"


@dataclass(frozen=True, slots=True)
class WorkspaceTarget:
    tab_id: str
    connection_id: str
    profile_revision: str
    database: str = ""
    schema: str = ""
    tab_kind: WorkspaceTabKind = WorkspaceTabKind.SQL


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class DraftRecord:
    draft_id: str
    target: WorkspaceTarget
    text: str
    text_kind: WorkspaceTextKind
    updated_at: str = field(default_factory=_utc_now)
    state_code: str = "DRAFT"


@dataclass(frozen=True, slots=True)
class HistoryEvidenceSummary:
    """Bounded non-result evidence metadata safe to persist with history."""

    evidence_id: str
    tool_name: str
    state_code: str
    count: int | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    history_id: str
    target: WorkspaceTarget
    text: str
    text_kind: WorkspaceTextKind
    created_at: str = field(default_factory=_utc_now)
    state_code: str = "RECORDED"
    is_favorite: bool = False
    row_count: int | None = None
    evidence_summary: tuple[HistoryEvidenceSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    tabs: tuple[WorkspaceTarget, ...]
    selected_tab_id: str | None = None
    saved_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    layout: WorkspaceLayout
    drafts: tuple[DraftRecord, ...]


def workspace_draft_slot(record: DraftRecord) -> str:
    prefix = record.target.tab_id + ":"
    if not record.draft_id.startswith(prefix):
        raise WorkspaceStoreError("INVALID_RECORD")
    return record.draft_id[len(prefix):]


def validate_workspace_snapshot(snapshot: WorkspaceSnapshot) -> None:
    """Validate the complete replacement before opening any SQLite file."""
    if type(snapshot) is not WorkspaceSnapshot or type(snapshot.drafts) is not tuple:
        raise WorkspaceStoreError("INVALID_RECORD")
    tabs, _selected, _saved = _validate_layout(snapshot.layout)
    if len(snapshot.drafts) > MAX_DRAFTS:
        raise WorkspaceStoreError("DRAFT_LIMIT_REACHED")
    targets = {tab.tab_id: tab for tab in tabs}
    slots = {tab.tab_id: set() for tab in tabs}
    size = 0
    for draft in snapshot.drafts:
        if type(draft) is not DraftRecord:
            raise WorkspaceStoreError("INVALID_RECORD")
        _record_values(draft)
        if targets.get(draft.target.tab_id) != draft.target:
            raise WorkspaceStoreError("INVALID_LAYOUT")
        slot = workspace_draft_slot(draft)
        required = {WorkspaceTabKind.SQL: {"sql"}, WorkspaceTabKind.REDIS: {"command"},
                    WorkspaceTabKind.MONGO: {"documents", "command"}}[draft.target.tab_kind]
        allowed = required | ({"snapshot"} if draft.target.tab_kind is not WorkspaceTabKind.SQL else set())
        if slot not in allowed or slot in slots[draft.target.tab_id]:
            raise WorkspaceStoreError("INVALID_RECORD")
        slots[draft.target.tab_id].add(slot)
        size += len(draft.text.encode("utf-8"))
    if size > MAX_STORE_BYTES:
        raise WorkspaceStoreError("STORE_LIMIT_REACHED")
    for tab in tabs:
        required = {WorkspaceTabKind.SQL: {"sql"}, WorkspaceTabKind.REDIS: {"command"},
                    WorkspaceTabKind.MONGO: {"documents", "command"}}[tab.tab_kind]
        if not required <= slots[tab.tab_id]:
            raise WorkspaceStoreError("INVALID_RECORD")


_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_STATE = re.compile(r"^[A-Z0-9_-]{1,64}$")
_SAFE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CREDENTIAL_NAMES = (
    r"password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|access[_-]?key|secret|secret[_-]?key|client[_-]?secret|authorization|private[_-]?key"
)
_CREDENTIAL_UNQUOTED_ASSIGNMENT = re.compile(
    rf"(?i)[\"']?\b(?:{_CREDENTIAL_NAMES})\b[\"']?\s*(?:=|:)\s*(?P<value>[^\s,;]+)"
)
_CREDENTIAL_URL_PARAMETER = re.compile(
    rf"(?i)[?&;](?:{_CREDENTIAL_NAMES})=([^&;\s]+)"
)
_URI_AUTHORITY = re.compile(
    r"(?i)\b(?:jdbc:[a-z0-9+.-]+|https?|postgres(?:ql)?|mysql|mariadb|oracle|sqlserver|"
    r"mongodb(?:\+srv)?|redis|rediss|ssh|sftp)://[^\s/@:]+:[^\s/@]+@"
)
_IDENTIFIED_BY = re.compile(
    r"(?is)\bidentified\s+by\s+(?P<value>'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)
_USER_CREDENTIAL_CLAUSE = re.compile(
    rf"(?is)^\s*(?:CREATE|ALTER)\s+(?:USER|ROLE|LOGIN)\b.*?\b(?:password|passwd|pwd|token|access[_-]?token|api[_-]?key|secret)\b\s*(?:=|:)?\s*(?P<value>'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)
_AUTH_COMMAND = re.compile(r"(?is)^\s*(?:redis(?:-cli)?\s+)?auth\s+\S")
_MONGO_AUTH_CALL = re.compile(r"(?is)\.\s*(?:auth|changeUserPassword)\s*\(")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PLACEHOLDER = re.compile(r"^(?:\?|%s|:[A-Za-z_][A-Za-z0-9_]*|\$[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})$")

_TEXT_KIND: TypeAlias = WorkspaceTextKind


def _validate_nonsecret_string(value: object, *, name: str, allow_empty: bool, max_bytes: int = 256) -> str:
    if type(value) is not str:
        raise WorkspaceStoreError("INVALID_RECORD")
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise WorkspaceStoreError("INVALID_RECORD") from None
    if (not value and not allow_empty) or encoded_size > max_bytes:
        raise WorkspaceStoreError("INVALID_RECORD")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise WorkspaceStoreError("INVALID_RECORD")
    _reject_credential_text(value)
    return value


def _validate_id(value: object, *, name: str) -> str:
    if type(value) is not str or not _SAFE_ID.fullmatch(value):
        raise WorkspaceStoreError("INVALID_RECORD")
    _reject_credential_text(value)
    return value


def _validate_target(target: object) -> WorkspaceTarget:
    if type(target) is not WorkspaceTarget:
        raise WorkspaceStoreError("INVALID_RECORD")
    for name in ("tab_id", "connection_id", "profile_revision"):
        _validate_nonsecret_string(getattr(target, name), name=name, allow_empty=False)
    for name in ("database", "schema"):
        _validate_nonsecret_string(getattr(target, name), name=name, allow_empty=True)
    if type(target.tab_kind) is not WorkspaceTabKind:
        raise WorkspaceStoreError("INVALID_RECORD")
    return target


def _validate_text_kind(kind: object) -> _TEXT_KIND:
    if type(kind) is not WorkspaceTextKind:
        raise WorkspaceStoreError("INVALID_RECORD")
    return kind


def _validate_timestamp(value: object) -> str:
    if type(value) is not str or len(value) > 64:
        raise WorkspaceStoreError("INVALID_RECORD")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise WorkspaceStoreError("INVALID_TIMESTAMP") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkspaceStoreError("INVALID_TIMESTAMP")
    return value


def _validate_state_code(value: object) -> str:
    if type(value) is not str or not _SAFE_STATE.fullmatch(value):
        raise WorkspaceStoreError("INVALID_RECORD")
    return value


def _validate_body(value: object) -> str:
    if type(value) is not str:
        raise WorkspaceStoreError("INVALID_RECORD")
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise WorkspaceStoreError("INVALID_RECORD") from None
    if encoded_size > MAX_TEXT_BYTES:
        raise WorkspaceStoreError("TEXT_TOO_LARGE")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise WorkspaceStoreError("INVALID_RECORD")
    _reject_credential_text(value)
    return value


def _reject_credential_text(value: str) -> None:
    if not value:
        return
    if _PRIVATE_KEY.search(value) or _URI_AUTHORITY.search(value):
        raise WorkspaceStoreError("CREDENTIAL_TEXT_REJECTED")
    identified_by = _IDENTIFIED_BY.search(value)
    if identified_by is not None:
        candidate = identified_by.group("value")
        if candidate[:1] in ("'", '"') and candidate[-1:] == candidate[:1]:
            candidate = candidate[1:-1].strip()
        if not _PLACEHOLDER.fullmatch(candidate):
            raise WorkspaceStoreError("CREDENTIAL_TEXT_REJECTED")
    user_credential = _USER_CREDENTIAL_CLAUSE.search(value)
    if user_credential is not None:
        candidate = user_credential.group("value")
        if candidate[:1] in ("'", '"') and candidate[-1:] == candidate[:1]:
            candidate = candidate[1:-1].strip()
        if candidate.casefold() not in {"null", "true", "false"} and not _PLACEHOLDER.fullmatch(candidate):
            raise WorkspaceStoreError("CREDENTIAL_TEXT_REJECTED")
    if _AUTH_COMMAND.search(value) or _MONGO_AUTH_CALL.search(value):
        raise WorkspaceStoreError("CREDENTIAL_TEXT_REJECTED")
    for match in _CREDENTIAL_URL_PARAMETER.finditer(value):
        if not _PLACEHOLDER.fullmatch(match.group(1)):
            raise WorkspaceStoreError("CREDENTIAL_TEXT_REJECTED")
    for match in _CREDENTIAL_UNQUOTED_ASSIGNMENT.finditer(value):
        literal = match.group("value").strip()
        if literal[:1] in ("'", '"') and literal[-1:] == literal[:1]:
            literal = literal[1:-1].strip()
        if literal.casefold() in {"null", "true", "false"}:
            continue
        if literal and not _PLACEHOLDER.fullmatch(literal):
            raise WorkspaceStoreError("CREDENTIAL_TEXT_REJECTED")


def _validate_row_count(value: object, *, code: str = "INVALID_RECORD") -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_HISTORY_ROW_COUNT:
        raise WorkspaceStoreError(code)
    return value


def _validate_evidence_summary(value: object) -> tuple[HistoryEvidenceSummary, ...]:
    if type(value) is not tuple or len(value) > MAX_HISTORY_EVIDENCE_ITEMS:
        raise WorkspaceStoreError("INVALID_RECORD")
    validated: list[HistoryEvidenceSummary] = []
    seen_ids: set[str] = set()
    for item in value:
        if type(item) is not HistoryEvidenceSummary:
            raise WorkspaceStoreError("INVALID_RECORD")
        evidence_id = _validate_id(item.evidence_id, name="evidence_id")
        if evidence_id in seen_ids:
            raise WorkspaceStoreError("INVALID_RECORD")
        seen_ids.add(evidence_id)
        if type(item.tool_name) is not str or not _SAFE_TOOL_NAME.fullmatch(item.tool_name):
            raise WorkspaceStoreError("INVALID_RECORD")
        _reject_credential_text(item.tool_name)
        state_code = _validate_state_code(item.state_code)
        count = _validate_row_count(item.count)
        if type(item.truncated) is not bool:
            raise WorkspaceStoreError("INVALID_RECORD")
        validated.append(
            HistoryEvidenceSummary(evidence_id, item.tool_name, state_code, count, item.truncated)
        )
    return tuple(validated)


def _evidence_summary_json(value: tuple[HistoryEvidenceSummary, ...]) -> str:
    validated = _validate_evidence_summary(value)
    serialized = json.dumps(
        [
            {
                "evidence_id": item.evidence_id,
                "tool_name": item.tool_name,
                "state_code": item.state_code,
                "count": item.count,
                "truncated": item.truncated,
            }
            for item in validated
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if len(serialized.encode("utf-8")) > MAX_HISTORY_EVIDENCE_BYTES:
        raise WorkspaceStoreError("INVALID_RECORD")
    return serialized


def _parse_evidence_summary(serialized: object) -> tuple[HistoryEvidenceSummary, ...]:
    if type(serialized) is not str:
        raise WorkspaceStoreError("STORE_RECORD_INVALID")
    encoded_size = len(serialized.encode("utf-8", errors="replace"))
    if encoded_size > MAX_HISTORY_EVIDENCE_BYTES:
        raise WorkspaceStoreError("STORE_RECORD_INVALID")
    try:
        parsed = json.loads(serialized)
        if type(parsed) is not list:
            raise ValueError
        records = tuple(
            HistoryEvidenceSummary(
                evidence_id=item["evidence_id"],
                tool_name=item["tool_name"],
                state_code=item["state_code"],
                count=item["count"],
                truncated=item["truncated"],
            )
            for item in parsed
            if type(item) is dict
            and set(item) == {"evidence_id", "tool_name", "state_code", "count", "truncated"}
        )
        if len(records) != len(parsed):
            raise ValueError
        return _validate_evidence_summary(records)
    except (KeyError, TypeError, ValueError, WorkspaceStoreError):
        raise WorkspaceStoreError("STORE_RECORD_INVALID") from None


def _record_values(record: DraftRecord | HistoryRecord) -> tuple[object, ...]:
    if type(record) not in (DraftRecord, HistoryRecord):
        raise WorkspaceStoreError("INVALID_RECORD")
    record_id_name = "draft_id" if type(record) is DraftRecord else "history_id"
    identifier = _validate_id(getattr(record, record_id_name), name=record_id_name)
    target = _validate_target(record.target)
    body = _validate_body(record.text)
    kind = _validate_text_kind(record.text_kind)
    timestamp = _validate_timestamp(record.updated_at if type(record) is DraftRecord else record.created_at)
    state = _validate_state_code(record.state_code)
    if type(record) is HistoryRecord and type(record.is_favorite) is not bool:
        raise WorkspaceStoreError("INVALID_RECORD")
    values: tuple[object, ...] = (
        identifier,
        kind.value,
        body,
        target.tab_id,
        target.tab_kind.value,
        target.connection_id,
        target.profile_revision,
        target.database,
        target.schema,
        timestamp,
        state,
    )
    if type(record) is HistoryRecord:
        row_count = _validate_row_count(record.row_count)
        evidence_summary = _evidence_summary_json(record.evidence_summary)
        return (*values, row_count, evidence_summary)
    return values


def _history_from_row(row: tuple[object, ...]) -> HistoryRecord:
    try:
        record = HistoryRecord(
            history_id=row[0],
            text_kind=WorkspaceTextKind(row[1]),
            text=row[2],
            target=WorkspaceTarget(
                row[3], row[5], row[6], row[7], row[8], WorkspaceTabKind(row[4])
            ),
            created_at=row[9],
            state_code=row[10],
            is_favorite=bool(row[11]),
            row_count=row[12],
            evidence_summary=_parse_evidence_summary(row[13]),
        )
        _record_values(record)
        return record
    except (WorkspaceStoreError, TypeError, ValueError, IndexError):
        raise WorkspaceStoreError("STORE_RECORD_INVALID") from None


def _validate_layout(
    layout: object,
) -> tuple[tuple[WorkspaceTarget, ...], str | None, str]:
    if type(layout) is not WorkspaceLayout or type(layout.tabs) is not tuple:
        raise WorkspaceStoreError("INVALID_LAYOUT")
    if len(layout.tabs) > MAX_LAYOUT_TABS:
        raise WorkspaceStoreError("LAYOUT_LIMIT_REACHED")
    tabs = tuple(_validate_target(tab) for tab in layout.tabs)
    tab_ids = tuple(tab.tab_id for tab in tabs)
    if len(set(tab_ids)) != len(tab_ids):
        raise WorkspaceStoreError("DUPLICATE_TAB_ID")
    selected_tab_id = layout.selected_tab_id
    if selected_tab_id is not None:
        selected_tab_id = _validate_nonsecret_string(
            selected_tab_id, name="selected_tab_id", allow_empty=False
        )
        if selected_tab_id not in tab_ids:
            raise WorkspaceStoreError("SELECTED_TAB_NOT_FOUND")
    saved_at = _validate_timestamp(layout.saved_at)
    return tabs, selected_tab_id, saved_at


def _write_draft(connection, values):
    connection.execute(
        """INSERT INTO dc_workspace_drafts (
            draft_id, text_kind, body, tab_id, tab_kind, connection_id,
            profile_revision, database_name, schema_name, updated_at, state_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(draft_id) DO UPDATE SET
            text_kind=excluded.text_kind, body=excluded.body,
            tab_id=excluded.tab_id, tab_kind=excluded.tab_kind,
            connection_id=excluded.connection_id, profile_revision=excluded.profile_revision,
            database_name=excluded.database_name, schema_name=excluded.schema_name,
            updated_at=excluded.updated_at, state_code=excluded.state_code""", values)


def _draft_from_row(row):
    try:
        record = DraftRecord(row[0], WorkspaceTarget(row[3], row[5], row[6], row[7], row[8], WorkspaceTabKind(row[4])),
                             row[2], WorkspaceTextKind(row[1]), row[9], row[10])
        _record_values(record)
        return record
    except (WorkspaceStoreError, TypeError, ValueError, IndexError):
        raise WorkspaceStoreError("STORE_RECORD_INVALID") from None


def _write_layout(connection, layout):
    connection.execute("DELETE FROM dc_workspace_layout")
    connection.executemany(
        """INSERT INTO dc_workspace_layout
           (position, tab_id, tab_kind, connection_id, profile_revision, database_name, schema_name)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ((position, tab.tab_id, tab.tab_kind.value, tab.connection_id, tab.profile_revision, tab.database, tab.schema)
         for position, tab in enumerate(layout.tabs)))
    connection.execute(
        """INSERT INTO dc_workspace_layout_meta (singleton, selected_tab_id, saved_at) VALUES (1, ?, ?)
           ON CONFLICT(singleton) DO UPDATE SET selected_tab_id=excluded.selected_tab_id, saved_at=excluded.saved_at""",
        (layout.selected_tab_id, layout.saved_at))


def _read_layout(connection):
    meta = connection.execute("SELECT selected_tab_id, saved_at FROM dc_workspace_layout_meta WHERE singleton = 1").fetchone()
    if meta is None:
        return None
    rows = connection.execute(
        "SELECT tab_id, tab_kind, connection_id, profile_revision, database_name, schema_name FROM dc_workspace_layout ORDER BY position").fetchall()
    try:
        layout = WorkspaceLayout(tuple(WorkspaceTarget(row[0], row[2], row[3], row[4], row[5], WorkspaceTabKind(row[1])) for row in rows), meta[0], meta[1])
        _validate_layout(layout)
        return layout
    except (WorkspaceStoreError, TypeError, ValueError):
        raise WorkspaceStoreError("STORE_RECORD_INVALID") from None


def _layout_draft_ids(layout):
    return tuple(tab.tab_id + ":" + slot for tab in layout.tabs
                 for slot in ({WorkspaceTabKind.SQL: ("sql",), WorkspaceTabKind.REDIS: ("command", "snapshot"),
                               WorkspaceTabKind.MONGO: ("documents", "command", "snapshot")}[tab.tab_kind]))


class WorkspaceStore:
    """Synchronous, explicit-path SQLite store for non-secret workspace DTOs."""

    def __init__(self, path: str | Path):
        if isinstance(path, Path):
            selected_path = path
        elif type(path) is str and path:
            selected_path = Path(path)
        else:
            raise WorkspaceStoreError("INVALID_PATH")
        if str(selected_path) == ":memory:":
            raise WorkspaceStoreError("EXPLICIT_FILE_PATH_REQUIRED")
        self._path = selected_path
        self._lock = threading.RLock()
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        self._run(write=False, operation=lambda _connection: None)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def save_draft(self, record: DraftRecord) -> None:
        values = _record_values(record)

        def operation(connection: sqlite3.Connection) -> None:
            draft_id = values[0]
            existing = connection.execute(
                "SELECT 1 FROM dc_workspace_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            if existing is None:
                count = connection.execute("SELECT COUNT(*) FROM dc_workspace_drafts").fetchone()[0]
                if count >= MAX_DRAFTS:
                    raise WorkspaceStoreError("DRAFT_LIMIT_REACHED")
            _write_draft(connection, values)

        self._run(write=True, operation=operation)

    def load_draft(self, draft_id: str) -> DraftRecord | None:
        draft_id = _validate_id(draft_id, name="draft_id")

        def operation(connection: sqlite3.Connection) -> DraftRecord | None:
            row = connection.execute(
                """SELECT draft_id, text_kind, body, tab_id, tab_kind, connection_id,
                          profile_revision, database_name, schema_name, updated_at, state_code
                   FROM dc_workspace_drafts WHERE draft_id = ?""",
                (draft_id,),
            ).fetchone()
            if row is None:
                return None
            return _draft_from_row(row)

        return self._run(write=False, operation=operation)

    def save_history(self, record: HistoryRecord) -> None:
        if type(record) is not HistoryRecord or record.is_favorite:
            raise WorkspaceStoreError("INVALID_RECORD")
        values = _record_values(record)

        def operation(connection: sqlite3.Connection) -> None:
            existing = connection.execute(
                "SELECT 1 FROM dc_workspace_history WHERE history_id = ?", (values[0],)
            ).fetchone()
            if existing is None:
                count = connection.execute("SELECT COUNT(*) FROM dc_workspace_history").fetchone()[0]
                if count >= MAX_HISTORY:
                    raise WorkspaceStoreError("HISTORY_LIMIT_REACHED")
            connection.execute(
                """INSERT INTO dc_workspace_history (
                    history_id, text_kind, body, tab_id, tab_kind, connection_id,
                    profile_revision, database_name, schema_name, created_at,
                    state_code, is_favorite, row_count, evidence_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(history_id) DO UPDATE SET
                    text_kind=excluded.text_kind, body=excluded.body,
                    tab_id=excluded.tab_id, tab_kind=excluded.tab_kind,
                    connection_id=excluded.connection_id,
                    profile_revision=excluded.profile_revision,
                    database_name=excluded.database_name, schema_name=excluded.schema_name,
                    created_at=excluded.created_at, state_code=excluded.state_code,
                    row_count=excluded.row_count, evidence_summary=excluded.evidence_summary""",
                values,
            )

        self._run(write=True, operation=operation)

    def search_history(
        self,
        query: str = "",
        *,
        connection_id: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[HistoryRecord, ...]:
        query = _validate_nonsecret_string(
            query, name="query", allow_empty=True, max_bytes=MAX_SEARCH_CHARS * 4
        )
        if len(query) > MAX_SEARCH_CHARS:
            raise WorkspaceStoreError("SEARCH_TOO_LONG")
        if connection_id is not None:
            connection_id = _validate_nonsecret_string(
                connection_id, name="connection_id", allow_empty=False
            )
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise WorkspaceStoreError("INVALID_LIMIT")
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_query}%"

        def operation(connection: sqlite3.Connection) -> tuple[HistoryRecord, ...]:
            conditions: list[str] = []
            parameters: list[object] = []
            if query:
                conditions.append("body LIKE ? ESCAPE '\\'")
                parameters.append(pattern)
            if connection_id is not None:
                conditions.append("connection_id = ?")
                parameters.append(connection_id)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            rows = connection.execute(
                """SELECT history_id, text_kind, body, tab_id, tab_kind, connection_id,
                          profile_revision, database_name, schema_name, created_at,
                          state_code, is_favorite, row_count, evidence_summary
                   FROM dc_workspace_history"""
                + where
                + " ORDER BY julianday(created_at) DESC, history_id DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return tuple(_history_from_row(row) for row in rows)

        return self._run(write=False, operation=operation)

    def set_favorite(self, history_id: str, favorite: bool = True) -> None:
        history_id = _validate_id(history_id, name="history_id")
        if type(favorite) is not bool:
            raise WorkspaceStoreError("INVALID_RECORD")

        def operation(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT is_favorite FROM dc_workspace_history WHERE history_id = ?",
                (history_id,),
            ).fetchone()
            if row is None:
                raise WorkspaceStoreError("HISTORY_NOT_FOUND")
            if favorite and not bool(row[0]):
                count = connection.execute(
                    "SELECT COUNT(*) FROM dc_workspace_history WHERE is_favorite = 1"
                ).fetchone()[0]
                if count >= MAX_FAVORITES:
                    raise WorkspaceStoreError("FAVORITE_LIMIT_REACHED")
            connection.execute(
                "UPDATE dc_workspace_history SET is_favorite = ? WHERE history_id = ?",
                (int(favorite), history_id),
            )

        self._run(write=True, operation=operation)

    def list_favorites(self, *, limit: int = DEFAULT_SEARCH_LIMIT) -> tuple[HistoryRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise WorkspaceStoreError("INVALID_LIMIT")

        def operation(connection: sqlite3.Connection) -> tuple[HistoryRecord, ...]:
            rows = connection.execute(
                """SELECT history_id, text_kind, body, tab_id, tab_kind, connection_id,
                          profile_revision, database_name, schema_name, created_at,
                          state_code, is_favorite, row_count, evidence_summary
                   FROM dc_workspace_history WHERE is_favorite = 1
                   ORDER BY julianday(created_at) DESC, history_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return tuple(_history_from_row(row) for row in rows)

        return self._run(write=False, operation=operation)

    def clear_history(self, *, include_favorites: bool = False) -> int:
        if type(include_favorites) is not bool:
            raise WorkspaceStoreError("INVALID_RECORD")

        def operation(connection: sqlite3.Connection) -> int:
            if include_favorites:
                cursor = connection.execute("DELETE FROM dc_workspace_history")
            else:
                cursor = connection.execute(
                    "DELETE FROM dc_workspace_history WHERE is_favorite = 0"
                )
            return int(cursor.rowcount)

        return self._run(write=True, operation=operation)

    def save_layout(self, layout: WorkspaceLayout) -> None:
        _validate_layout(layout)
        self._run(write=True, operation=lambda connection: _write_layout(connection, layout))

    def load_layout(self) -> WorkspaceLayout | None:
        return self._run(write=False, operation=_read_layout)

    def save_workspace(self, snapshot: WorkspaceSnapshot) -> None:
        validate_workspace_snapshot(snapshot)
        values = tuple(_record_values(draft) for draft in snapshot.drafts)

        def operation(connection):
            old_layout = _read_layout(connection)
            if old_layout is not None:
                connection.executemany("DELETE FROM dc_workspace_drafts WHERE draft_id = ?",
                                       ((draft_id,) for draft_id in _layout_draft_ids(old_layout)))
            existing = {row[0] for row in connection.execute("SELECT draft_id FROM dc_workspace_drafts")}
            if len(existing | {value[0] for value in values}) > MAX_DRAFTS:
                raise WorkspaceStoreError("DRAFT_LIMIT_REACHED")
            for value in values:
                _write_draft(connection, value)
            _write_layout(connection, snapshot.layout)

        self._run(write=True, operation=operation)

    def load_workspace(self) -> WorkspaceSnapshot | None:
        def operation(connection):
            connection.execute("BEGIN")
            try:
                layout = _read_layout(connection)
                if layout is None:
                    return None
                records = []
                for draft_id in _layout_draft_ids(layout):
                    row = connection.execute(
                        """SELECT draft_id, text_kind, body, tab_id, tab_kind, connection_id,
                                  profile_revision, database_name, schema_name, updated_at, state_code
                           FROM dc_workspace_drafts WHERE draft_id = ?""", (draft_id,)).fetchone()
                    if row is not None:
                        records.append(_draft_from_row(row))
                snapshot = WorkspaceSnapshot(layout, tuple(records))
                validate_workspace_snapshot(snapshot)
                return snapshot
            finally:
                connection.rollback()

        return self._run(write=False, operation=operation)

    def _run(self, *, write: bool, operation):
        with self._lock:
            if self._closed:
                raise WorkspaceStoreError("STORE_CLOSED")
            connection: sqlite3.Connection | None = None
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(
                    str(self._path), timeout=SQLITE_TIMEOUT_SECONDS, isolation_level=None
                )
                connection.execute(f"PRAGMA busy_timeout={int(SQLITE_TIMEOUT_SECONDS * 1000)}")
                connection.execute("PRAGMA foreign_keys=ON")
                self._ensure_schema(connection)
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                if write:
                    self._check_store_size(connection)
                    connection.commit()
                return result
            except WorkspaceStoreError:
                if connection is not None and connection.in_transaction and not self._rollback(connection):
                    raise WorkspaceStoreError("STORAGE_OUTCOME_UNKNOWN") from None
                raise
            except sqlite3.Error:
                if connection is not None and connection.in_transaction and not self._rollback(connection):
                    raise WorkspaceStoreError("STORAGE_OUTCOME_UNKNOWN") from None
                raise WorkspaceStoreError("STORAGE_FAILED") from None
            finally:
                if connection is not None:
                    connection.close()

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection, *, history_version: int) -> None:
        history_columns = [
            ("history_id", "TEXT", 0, 1),
            ("text_kind", "TEXT", 1, 0),
            ("body", "TEXT", 1, 0),
            ("tab_id", "TEXT", 1, 0),
            ("tab_kind", "TEXT", 1, 0),
            ("connection_id", "TEXT", 1, 0),
            ("profile_revision", "TEXT", 1, 0),
            ("database_name", "TEXT", 1, 0),
            ("schema_name", "TEXT", 1, 0),
            ("created_at", "TEXT", 1, 0),
            ("state_code", "TEXT", 1, 0),
            ("is_favorite", "INTEGER", 1, 0),
        ]
        if history_version == 2:
            history_columns.extend(
                (("row_count", "INTEGER", 0, 0), ("evidence_summary", "TEXT", 1, 0))
            )
        expected: dict[str, list[tuple[str, str, int, int]]] = {
            "dc_workspace_drafts": [
                ("draft_id", "TEXT", 0, 1),
                ("text_kind", "TEXT", 1, 0),
                ("body", "TEXT", 1, 0),
                ("tab_id", "TEXT", 1, 0),
                ("tab_kind", "TEXT", 1, 0),
                ("connection_id", "TEXT", 1, 0),
                ("profile_revision", "TEXT", 1, 0),
                ("database_name", "TEXT", 1, 0),
                ("schema_name", "TEXT", 1, 0),
                ("updated_at", "TEXT", 1, 0),
                ("state_code", "TEXT", 1, 0),
            ],
            "dc_workspace_history": history_columns,
            "dc_workspace_layout": [
                ("position", "INTEGER", 0, 1),
                ("tab_id", "TEXT", 1, 0),
                ("tab_kind", "TEXT", 1, 0),
                ("connection_id", "TEXT", 1, 0),
                ("profile_revision", "TEXT", 1, 0),
                ("database_name", "TEXT", 1, 0),
                ("schema_name", "TEXT", 1, 0),
            ],
            "dc_workspace_layout_meta": [
                ("singleton", "INTEGER", 0, 1),
                ("selected_tab_id", "TEXT", 0, 0),
                ("saved_at", "TEXT", 1, 0),
            ],
        }
        actual_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual_tables != set(expected):
            raise WorkspaceStoreError("SCHEMA_MALFORMED")
        for table, expected_columns in expected.items():
            actual_columns = [
                (row[1], str(row[2]).upper(), int(row[3]), int(row[5]))
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            if actual_columns != expected_columns:
                raise WorkspaceStoreError("SCHEMA_MALFORMED")
        indexes = {
            (row[0], row[1])
            for row in connection.execute(
                "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        required_indexes = {
            ("dc_workspace_history_created", "dc_workspace_history"),
            ("dc_workspace_history_connection", "dc_workspace_history"),
        }
        if not required_indexes.issubset(indexes):
            raise WorkspaceStoreError("SCHEMA_MALFORMED")

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise WorkspaceStoreError("SCHEMA_VERSION_UNSUPPORTED")
        if version == SCHEMA_VERSION:
            app_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            if app_id != APPLICATION_ID:
                raise WorkspaceStoreError("DATABASE_NOT_OWNED")
            self._validate_schema(connection, history_version=2)
            return
        if version not in (0, 1):
            raise WorkspaceStoreError("SCHEMA_MIGRATION_UNSUPPORTED")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            app_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise WorkspaceStoreError("SCHEMA_VERSION_UNSUPPORTED")
            if version not in (0, 1, SCHEMA_VERSION):
                raise WorkspaceStoreError("SCHEMA_MIGRATION_UNSUPPORTED")
            if version == SCHEMA_VERSION:
                if app_id != APPLICATION_ID:
                    raise WorkspaceStoreError("DATABASE_NOT_OWNED")
                self._validate_schema(connection, history_version=2)
                connection.commit()
                return
            if version == 1:
                if app_id != APPLICATION_ID:
                    raise WorkspaceStoreError("DATABASE_NOT_OWNED")
                self._validate_schema(connection, history_version=1)
                connection.execute(
                    f"""ALTER TABLE dc_workspace_history ADD COLUMN row_count INTEGER
                        CHECK (row_count IS NULL OR (typeof(row_count)='integer'
                        AND row_count BETWEEN 0 AND {MAX_HISTORY_ROW_COUNT}))"""
                )
                connection.execute(
                    f"""ALTER TABLE dc_workspace_history ADD COLUMN evidence_summary TEXT
                        NOT NULL DEFAULT '[]' CHECK (typeof(evidence_summary)='text'
                        AND length(CAST(evidence_summary AS BLOB)) <= {MAX_HISTORY_EVIDENCE_BYTES})"""
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._validate_schema(connection, history_version=2)
                self._check_store_size(connection)
                connection.commit()
                return
            if app_id not in (0, APPLICATION_ID):
                raise WorkspaceStoreError("DATABASE_NOT_OWNED")
            existing_tables = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if existing_tables is not None:
                raise WorkspaceStoreError("DATABASE_NOT_OWNED")
            connection.execute(
                """CREATE TABLE dc_workspace_drafts (
                    draft_id TEXT PRIMARY KEY,
                    text_kind TEXT NOT NULL CHECK (text_kind IN ('sql', 'nosql')),
                    body TEXT NOT NULL,
                    tab_id TEXT NOT NULL,
                    tab_kind TEXT NOT NULL CHECK (tab_kind IN ('sql', 'redis', 'mongo')),
                    connection_id TEXT NOT NULL,
                    profile_revision TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_code TEXT NOT NULL
                )"""
            )
            connection.execute(
                f"""CREATE TABLE dc_workspace_history (
                    history_id TEXT PRIMARY KEY,
                    text_kind TEXT NOT NULL CHECK (text_kind IN ('sql', 'nosql')),
                    body TEXT NOT NULL,
                    tab_id TEXT NOT NULL,
                    tab_kind TEXT NOT NULL CHECK (tab_kind IN ('sql', 'redis', 'mongo')),
                    connection_id TEXT NOT NULL,
                    profile_revision TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    state_code TEXT NOT NULL,
                    is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
                    row_count INTEGER CHECK (row_count IS NULL OR (typeof(row_count)='integer'
                        AND row_count BETWEEN 0 AND {MAX_HISTORY_ROW_COUNT})),
                    evidence_summary TEXT NOT NULL DEFAULT '[]' CHECK (typeof(evidence_summary)='text'
                        AND length(CAST(evidence_summary AS BLOB)) <= {MAX_HISTORY_EVIDENCE_BYTES})
                )"""
            )
            connection.execute(
                "CREATE INDEX dc_workspace_history_created ON dc_workspace_history(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX dc_workspace_history_connection ON dc_workspace_history(connection_id, created_at DESC)"
            )
            connection.execute(
                """CREATE TABLE dc_workspace_layout (
                    position INTEGER PRIMARY KEY,
                    tab_id TEXT NOT NULL UNIQUE,
                    tab_kind TEXT NOT NULL CHECK (tab_kind IN ('sql', 'redis', 'mongo')),
                    connection_id TEXT NOT NULL,
                    profile_revision TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    schema_name TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE dc_workspace_layout_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    selected_tab_id TEXT,
                    saved_at TEXT NOT NULL
                )"""
            )
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._validate_schema(connection, history_version=2)
            self._check_store_size(connection)
            connection.commit()
        except Exception:
            if connection.in_transaction and not WorkspaceStore._rollback(connection):
                raise WorkspaceStoreError("SCHEMA_OUTCOME_UNKNOWN") from None
            raise

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> bool:
        try:
            connection.rollback()
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _check_store_size(connection: sqlite3.Connection) -> None:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        if page_count * page_size > MAX_STORE_BYTES:
            raise WorkspaceStoreError("STORE_LIMIT_REACHED")


__all__ = [
    "APPLICATION_ID",
    "DEFAULT_SEARCH_LIMIT",
    "DraftRecord",
    "HistoryEvidenceSummary",
    "HistoryRecord",
    "MAX_DRAFTS",
    "MAX_HISTORY_EVIDENCE_BYTES",
    "MAX_HISTORY_EVIDENCE_ITEMS",
    "MAX_FAVORITES",
    "MAX_HISTORY",
    "MAX_HISTORY_ROW_COUNT",
    "MAX_LAYOUT_TABS",
    "MAX_SEARCH_CHARS",
    "MAX_STORE_BYTES",
    "MAX_TEXT_BYTES",
    "MAX_SEARCH_LIMIT",
    "SCHEMA_VERSION",
    "WorkspaceLayout",
    "WorkspaceStore",
    "WorkspaceStoreError",
    "WorkspaceTabKind",
    "WorkspaceTarget",
    "WorkspaceTextKind",
]
