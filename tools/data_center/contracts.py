"""Shared, UI-independent contracts for the data-center Agent.

The data-center workbench is deliberately kept separate from the existing
programming Agent.  These types describe the boundary between an injected
model adapter, an injected data-center tool registry, and the pure Python
runner.  They contain identifiers and bounded payloads only; credentials and
connection objects are intentionally not part of the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import threading
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeAlias


JsonObject: TypeAlias = dict[str, Any]
JsonValue: TypeAlias = Any
EventSink: TypeAlias = Callable[["AgentEvent"], None]
DeltaSink: TypeAlias = Callable[["ModelDelta"], None]


def _copy_mapping(value: Mapping[str, Any] | None) -> JsonObject:
    """Make a shallow, JSON-shaped copy at the contract boundary."""

    if value is None:
        return {}
    return dict(value)


def stable_json(value: Any) -> str:
    """Return deterministic JSON for call-id idempotency and size accounting.

    Tool arguments are expected to be JSON values.  ``default=str`` is used
    only as a defensive representation for test doubles; it never grants the
    model access to a Python object or executable value.
    """

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class RunContext:
    """Immutable scope captured when one Agent run starts."""

    run_id: str
    tab_id: str
    model_config_id: str
    connection_id: str
    database: str
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
class ToolCall:
    """One structured tool request produced by the model."""

    call_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _copy_mapping(self.arguments))

    @property
    def argument_key(self) -> str:
        """Canonical arguments used to detect duplicate/conflicting IDs."""

        return stable_json(self.arguments)

    def as_dict(self) -> JsonObject:
        return {"call_id": self.call_id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """One complete model response, after adapter streaming has finished."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: Mapping[str, Any] = field(default_factory=dict)
    model_id: str = ""

    def __post_init__(self) -> None:
        calls: list[ToolCall] = []
        for item in self.tool_calls:
            if isinstance(item, ToolCall):
                calls.append(item)
            elif isinstance(item, Mapping):
                calls.append(
                    ToolCall(
                        call_id=str(item.get("call_id", item.get("id", ""))),
                        name=str(item.get("name", "")),
                        arguments=item.get("arguments", item.get("args", {})) or {},
                    )
                )
            else:
                raise TypeError(f"unsupported tool call value: {type(item)!r}")
        object.__setattr__(self, "tool_calls", tuple(calls))
        object.__setattr__(self, "usage", _copy_mapping(self.usage))


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Normalized result returned by a data-center tool.

    ``data`` is kept separate from the model projection.  A later policy layer
    may redact or truncate it before it is placed into a model message.
    """

    ok: bool
    code: str = "OK"
    data: JsonValue = None
    evidence_id: str | None = None
    query_id: str | None = None
    scope: JsonValue = None
    truncated: bool = False
    limits: Mapping[str, Any] = field(default_factory=dict)
    elapsed_ms: int | float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", _copy_mapping(self.limits))

    @classmethod
    def from_value(cls, value: Any) -> "ToolResult":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                ok=bool(value.get("ok", False)),
                code=str(value.get("code", "OK" if value.get("ok") else "TOOL_FAILED")),
                data=value.get("data"),
                evidence_id=value.get("evidence_id", value.get("evidenceId")),
                query_id=value.get("query_id", value.get("queryId")),
                scope=value.get("scope"),
                truncated=bool(value.get("truncated", False)),
                limits=value.get("limits", {}) or {},
                elapsed_ms=value.get("elapsed_ms", value.get("elapsedMs")),
            )
        return cls(ok=True, data=value)

    def as_dict(self) -> JsonObject:
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

    def as_model_message(self, call_id: str) -> JsonObject:
        """Build the exact tool-result message used for the next model turn."""

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": stable_json(self.as_dict()),
        }


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Observable event delivered to the UI/event journal."""

    run_id: str
    tab_id: str
    sequence: int
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _copy_mapping(self.payload))

    def as_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "tab_id": self.tab_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "payload": dict(self.payload),
        }


class AgentEventType(str, Enum):
    """Stable event names shared by the runner and a future Qt adapter."""

    RUN_STARTED = "run_started"
    TURN_STARTED = "turn_started"
    MODEL_REASONING_DELTA = "model_reasoning_delta"
    MODEL_TEXT_DELTA = "model_text_delta"
    TOOL_CALL = "tool_call"
    TOOL_STARTED = "tool_started"
    TOOL_RESULT = "tool_result"
    BUDGET = "budget"
    NEEDS_CLARIFICATION = "needs_clarification"
    FINAL = "final"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class AgentEventEmitter:
    """Thread-safe monotonic sequence emitter for one run.

    Sequence numbers are allocated by the producer, rather than by a UI
    consumer.  The emitter is intentionally tiny so it can be used from a
    worker thread without importing Qt.  A sink failure is isolated from
    sequence allocation and does not permit the next event to reuse a number.
    """

    __slots__ = ("run_id", "tab_id", "_next_sequence", "_sink", "_lock")

    def __init__(
        self,
        run_id: str,
        tab_id: str,
        sink: EventSink | None = None,
        *,
        start_sequence: int = 0,
    ) -> None:
        if start_sequence < 0:
            raise ValueError("start_sequence must be non-negative")
        self.run_id = str(run_id)
        self.tab_id = str(tab_id)
        self._next_sequence = int(start_sequence)
        self._sink = sink
        self._lock = threading.Lock()

    @property
    def last_sequence(self) -> int:
        with self._lock:
            return self._next_sequence

    def emit(self, event_type: str | AgentEventType, payload: Mapping[str, Any] | None = None) -> AgentEvent:
        """Allocate, optionally publish, and return the next event."""

        with self._lock:
            self._next_sequence += 1
            event = AgentEvent(
                run_id=self.run_id,
                tab_id=self.tab_id,
                sequence=self._next_sequence,
                event_type=str(event_type.value if isinstance(event_type, AgentEventType) else event_type),
                payload=payload or {},
            )
        if self._sink is not None:
            self._sink(event)
        return event

    def next(self, event_type: str | AgentEventType, payload: Mapping[str, Any] | None = None) -> AgentEvent:
        """Alias for adapters that treat the emitter as a sequence source."""

        return self.emit(event_type, payload)

    def __call__(self, event_type: str | AgentEventType, payload: Mapping[str, Any] | None = None) -> AgentEvent:
        return self.emit(event_type, payload)


@dataclass(frozen=True, slots=True)
class QueryRequest:
    """Bounded request passed from a future engine adapter to a query tool."""

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


@dataclass(frozen=True, slots=True)
class ModelDelta:
    """Incremental adapter output; only explicit display channels are exposed."""

    kind: str
    text: str = ""
    call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", _copy_mapping(self.usage))

    @classmethod
    def from_value(cls, value: Any) -> "ModelDelta":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(kind="text", text=value)
        if isinstance(value, Mapping):
            return cls(
                kind=str(value.get("kind", value.get("type", "text"))),
                text=str(value.get("text", value.get("delta", ""))),
                call_id=value.get("call_id", value.get("id")),
                name=value.get("name"),
                arguments_delta=str(value.get("arguments_delta", value.get("arguments", ""))),
                usage=value.get("usage", {}) or {},
            )
        raise TypeError(f"unsupported model delta value: {type(value)!r}")


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    """Adapter request for one model turn."""

    context: RunContext
    system_prompt: str
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...]
    turn_index: int
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(dict(message) for message in self.messages))
        object.__setattr__(self, "tools", tuple(dict(tool) for tool in self.tools))


class CancellationToken:
    """Thread-safe one-way cancellation state owned by the host.

    The model and tools receive this object but cannot clear it.  A runner
    checks it before every external boundary and suppresses callbacks that
    arrive after the run has become inactive.
    """

    __slots__ = ("_event", "_lock", "_reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if not self._event.is_set():
                self._reason = str(reason) or "cancelled"
                self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


class AgentStatus(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    MODEL_PENDING = "model_pending"
    VALIDATING_TOOL = "validating_tool"
    TOOL_RUNNING = "tool_running"
    NEEDS_CLARIFICATION = "needs_clarification"
    POLICY_BLOCKED = "policy_blocked"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CAPABILITY_BLOCKED = "capability_blocked"
    COMPLETED = "completed"


class ModelAdapter(Protocol):
    """Minimal adapter boundary required by :class:`AgentRunner`."""

    def complete_turn(
        self,
        request: AgentTurnRequest,
        *,
        cancellation: CancellationToken,
        on_delta: DeltaSink,
    ) -> AgentTurn:
        ...


class ToolRegistry(Protocol):
    """Closed-set data-center tools injected by the host."""

    def definitions(self) -> Sequence[Mapping[str, Any]]:
        ...

    def execute(
        self,
        call: ToolCall,
        *,
        context: RunContext,
        cancellation: CancellationToken,
    ) -> ToolResult:
        ...


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Terminal result of a runner invocation."""

    status: AgentStatus
    final_text: str
    events: tuple[AgentEvent, ...]
    messages: tuple[Mapping[str, Any], ...]
    tool_results: tuple[ToolResult, ...] = ()
    error: str | None = None
    budget: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "messages", tuple(dict(message) for message in self.messages))
        object.__setattr__(self, "tool_results", tuple(self.tool_results))
        object.__setattr__(self, "budget", _copy_mapping(self.budget))
