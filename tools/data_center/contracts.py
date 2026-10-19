"""Compatibility contracts for the data-center Agent host.

Shared execution contracts live in :mod:`tools.agent_core.contracts`. This
module adds only database query request/result shapes and the data-center run
scope expected by existing hosts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from tools.agent_core.contracts import (
    AgentEvent,
    AgentEventEmitter,
    AgentEventType,
    AgentRunResult,
    AgentStatus,
    AgentTurn,
    AgentTurnRequest,
    CancellationToken,
    DeltaSink,
    EventSink,
    JsonObject,
    JsonValue,
    ModelAdapter,
    ModelDelta,
    ToolCall,
    ToolRegistry,
    ToolResult,
    stable_json,
    _copy_mapping,
)


@dataclass(frozen=True, slots=True)
class RunContext:
    """Database scope retained for data-center policy and host adapters."""

    run_id: str
    tab_id: str
    model_config_id: str
    connection_id: str = ""
    database: str = ""
    schema_allowlist: tuple[str, ...] = ()
    profile_revision: str = ""
    intent: str = "query"
    policy_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_allowlist", tuple(str(item) for item in self.schema_allowlist))

    def as_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "tab_id": self.tab_id,
            "model_config_id": self.model_config_id,
            "connection_id": self.connection_id,
            "database": self.database,
            "schema_allowlist": list(self.schema_allowlist),
            "profile_revision": self.profile_revision,
            "intent": self.intent,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class QueryRequest:
    """Bounded request passed from an engine adapter to a query tool."""

    session_id: str
    query_id: str
    generation: int
    sql: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    limits: Mapping[str, Any] = field(default_factory=dict)
    deadline: float | None = None
    operation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _copy_mapping(self.parameters))
        object.__setattr__(self, "limits", _copy_mapping(self.limits))


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Engine-facing result retaining native Python value types."""

    ok: bool
    code: str = "OK"
    query_id: str = ""
    columns: tuple[Mapping[str, Any], ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    truncated: bool = False
    elapsed_ms: int | float | None = None
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(dict(item) for item in self.columns))
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))


# Preserve the import/introspection paths used by the existing package API.
for _type in (
    AgentEvent, AgentEventEmitter, AgentEventType, AgentRunResult, AgentStatus,
    AgentTurn, AgentTurnRequest, CancellationToken, ModelDelta, ToolCall, ToolResult,
):
    _type.__module__ = __name__
ModelAdapter.__module__ = __name__
ToolRegistry.__module__ = __name__

__all__ = [
    "AgentEvent", "AgentEventEmitter", "AgentEventType", "AgentRunResult",
    "AgentStatus", "AgentTurn", "AgentTurnRequest", "CancellationToken",
    "DeltaSink", "EventSink", "JsonObject", "JsonValue", "ModelAdapter",
    "ModelDelta", "QueryRequest", "QueryResult", "RunContext", "ToolCall",
    "ToolRegistry", "ToolResult", "stable_json",
]
