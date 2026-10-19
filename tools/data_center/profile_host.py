"""Read-only snapshots for data-center Agent connection/model profiles.

The legacy settings readers normalize and may migrate a legacy model file on
read.  The Agent needs a stable per-run revision without changing the user's
configuration, so this host reads the existing JSON stores directly and keeps
their original IDs and unknown fields intact.

Raw profile mappings are private worker inputs.  Public choice/snapshot values
contain only IDs, labels, capability, target scope, and opaque revisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from tools.data_center.host_nosql import NoSQLReadOnlyHost
from tools.data_center.host_relational import RelationalReadOnlyHost, compute_profile_revision
from tools.data_center.model_config import AgentModelConfigError, build_agent_model_snapshot


class ProfileHostError(RuntimeError):
    """A safe profile selection/revision error."""

    def __init__(self, code: str):
        self.code = str(code or "PROFILE_UNAVAILABLE")
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ModelChoice:
    model_config_id: str
    name: str
    model: str
    capability: str
    revision: str

    def __post_init__(self) -> None:
        for key in ("model_config_id", "name", "model", "capability", "revision"):
            object.__setattr__(self, key, str(getattr(self, key) or "").strip())
        if not self.model_config_id or not self.model or not self.revision:
            raise ValueError("model choice requires an ID, model name, and revision")


@dataclass(frozen=True, slots=True)
class ConnectionChoice:
    connection_id: str
    revision: str
    provider: str
    mode: str
    dialect: str
    database: str
    schema_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for key in ("connection_id", "revision", "provider", "mode", "dialect", "database"):
            object.__setattr__(self, key, str(getattr(self, key) or "").strip())
        object.__setattr__(self, "schema_allowlist", tuple(dict.fromkeys(
            str(item or "").strip() for item in self.schema_allowlist if str(item or "").strip()
        )))


def _raw_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        items = raw.get("items")
        if isinstance(items, list):
            return [dict(item) for item in items if isinstance(item, Mapping)]
        # Preserve support for a single legacy model configuration only when
        # it already has a stable ID.  We never generate or persist a new ID.
        if raw.get("id") and any(key in raw for key in ("model", "base_url", "enabled")):
            return [dict(raw)]
        return []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def _default_connections_loader() -> Any:
    from config import HARNESS_CONNECTIONS_FILE
    from tools.data_center.connection_store import ConnectionStore, ConnectionStoreError

    try:
        snapshot = ConnectionStore(HARNESS_CONNECTIONS_FILE).load()
        return {"items": [dict(item) for item in snapshot.items]}
    except ConnectionStoreError:
        raise ProfileHostError("PROFILE_UNAVAILABLE") from None


def _default_model_catalog_loader() -> Any:
    from config import AI_LOCAL_FILE

    try:
        with open(AI_LOCAL_FILE, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, TypeError):
        return {}


def _select_exact(rows: Sequence[Mapping[str, Any]], key: str, selected: str, code: str) -> dict[str, Any]:
    matches = [dict(item) for item in rows if str(item.get(key) or "").strip() == selected]
    if len(matches) != 1:
        raise ProfileHostError(code)
    return matches[0]


def _model_row(raw: Any, selected_id: str) -> dict[str, Any]:
    rows = _raw_rows(raw)
    return _select_exact(rows, "id", selected_id, "MODEL_UNAVAILABLE")


class DataCenterProfileHost:
    """Capture and verify exact profile IDs without writing configuration."""

    def __init__(
        self,
        *,
        connections_loader: Callable[[], Any] | Mapping[str, Any] | Sequence[Any] | None = None,
        model_catalog_loader: Callable[[], Any] | Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> None:
        self._connections_loader = _default_connections_loader if connections_loader is None else connections_loader
        self._model_catalog_loader = _default_model_catalog_loader if model_catalog_loader is None else model_catalog_loader

    @staticmethod
    def _load(source: Any) -> Any:
        return source() if callable(source) else source

    def _connection_rows(self) -> list[dict[str, Any]]:
        raw = self._load(self._connections_loader)
        rows = _raw_rows(raw)
        return [row for row in rows if str(row.get("id", row.get("connection_id", "")) or "").strip()]

    @staticmethod
    def _profile_snapshot(profile: Mapping[str, Any], connection_id: str):
        provider_hint = str(profile.get("provider") or profile.get("engine") or profile.get("dialect") or "").strip()
        provider_norm = provider_hint.casefold().replace("-", "_")
        try:
            if provider_norm in {"redis", "mongodb", "mongo", "mongo_db", "redis_cluster"}:
                host = NoSQLReadOnlyHost(profile=profile)
            else:
                host = RelationalReadOnlyHost(profile=profile)
            return host.profile_snapshot(connection_id)
        except Exception:
            raise ProfileHostError("PROFILE_UNAVAILABLE") from None

    def load_connection_profile(self, connection_id: str, expected_revision: str) -> dict[str, Any]:
        selected = str(connection_id or "").strip()
        if not selected:
            raise ProfileHostError("PROFILE_UNAVAILABLE")
        rows = self._connection_rows()
        matches = [
            row for row in rows
            if str(row.get("id", row.get("connection_id", "")) or "").strip() == selected
        ]
        if len(matches) != 1:
            raise ProfileHostError("PROFILE_UNAVAILABLE")
        profile = dict(matches[0])
        snapshot = self._profile_snapshot(profile, selected)
        revision = str(snapshot.profile_revision)
        if not expected_revision or revision != str(expected_revision):
            raise ProfileHostError("PROFILE_MISMATCH")
        return profile

    def capture_connection(
        self,
        profile: Mapping[str, Any],
        *,
        database: str | None = None,
        schema_allowlist: Sequence[str] = (),
    ) -> ConnectionChoice:
        if not isinstance(profile, Mapping):
            raise ProfileHostError("PROFILE_UNAVAILABLE")
        data = dict(profile)
        connection_id = str(data.get("id", data.get("connection_id", "")) or "").strip()
        if not connection_id:
            raise ProfileHostError("PROFILE_UNAVAILABLE")
        provider_hint = str(data.get("provider") or data.get("engine") or data.get("dialect") or "").strip()
        provider_norm = provider_hint.casefold().replace("-", "_")
        try:
            if provider_norm in {"redis", "mongodb", "mongo", "mongo_db", "redis_cluster"}:
                snapshot = NoSQLReadOnlyHost(profile=data).profile_snapshot(connection_id)
                revision = str(snapshot.profile_revision)
                target_database = str(database if database is not None else snapshot.database_name).strip()
                if snapshot.provider == "redis" and not target_database:
                    target_database = str(data.get("db", data.get("database", 0))).strip() or "0"
                return ConnectionChoice(
                    connection_id, revision, snapshot.provider, snapshot.mode, snapshot.provider,
                    target_database, tuple(schema_allowlist),
                )
            snapshot = RelationalReadOnlyHost(profile=data).profile_snapshot(connection_id)
            revision = str(snapshot.profile_revision)
            target_database = str(database or self._database_from_profile(data)).strip()
            return ConnectionChoice(
                connection_id, revision, snapshot.provider, snapshot.mode, snapshot.dialect,
                target_database, tuple(schema_allowlist),
            )
        except ProfileHostError:
            raise
        except Exception:
            raise ProfileHostError("PROFILE_UNAVAILABLE") from None

    @staticmethod
    def _database_from_profile(profile: Mapping[str, Any]) -> str:
        for key in ("database", "db_name", "database_name", "service_name", "service", "sid", "schema", "owner"):
            value = str(profile.get(key) or "").strip()
            if value:
                return value
        return ""

    def list_model_choices(self) -> tuple[tuple[ModelChoice, ...], str]:
        try:
            raw = self._load(self._model_catalog_loader)
        except Exception:
            raise ProfileHostError("MODEL_CATALOG_UNAVAILABLE") from None
        rows = _raw_rows(raw)
        choices: list[ModelChoice] = []
        for item in rows:
            model_id = str(item.get("id") or "").strip()
            if not model_id or not bool(item.get("enabled")) or not str(item.get("base_url") or "").strip():
                continue
            try:
                snapshot = build_agent_model_snapshot(model_id, item)
                revision = compute_profile_revision(item)
                choices.append(ModelChoice(
                    model_id,
                    str(item.get("name") or model_id),
                    snapshot.model,
                    snapshot.capability.value,
                    revision,
                ))
            except (AgentModelConfigError, ValueError, TypeError):
                continue
        active_id = str(raw.get("active_model_id") or "").strip() if isinstance(raw, Mapping) else ""
        if active_id not in {item.model_config_id for item in choices}:
            active_id = ""
        return tuple(choices), active_id

    def load_model_config(self, model_config_id: str, expected_revision: str) -> dict[str, Any]:
        selected = str(model_config_id or "").strip()
        if not selected:
            raise ProfileHostError("MODEL_UNAVAILABLE")
        try:
            raw = self._load(self._model_catalog_loader)
            profile = _model_row(raw, selected)
        except ProfileHostError:
            raise
        except Exception:
            raise ProfileHostError("MODEL_UNAVAILABLE") from None
        if compute_profile_revision(profile) != str(expected_revision or ""):
            raise ProfileHostError("MODEL_REVISION_MISMATCH")
        if not bool(profile.get("enabled")) or not str(profile.get("base_url") or "").strip() or not str(profile.get("model") or "").strip():
            raise ProfileHostError("MODEL_UNAVAILABLE")
        return profile


__all__ = ["ConnectionChoice", "DataCenterProfileHost", "ModelChoice", "ProfileHostError"]
