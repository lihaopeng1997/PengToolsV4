"""Host-owned model configuration for the data-center Agent.

The existing :mod:`tools.intranet_llm` module is still the compatibility
client for the rest of the application.  This module only turns one selected
model configuration into an immutable, public-safe snapshot and keeps the
transport details private to the Agent host adapter.

No model configuration is loaded at import time.  The default loader is
resolved lazily when a host explicitly starts an Agent run, which keeps the
package importable in tests and in non-UI processes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping


class AgentModelConfigError(RuntimeError):
    """A safe configuration error at the Agent model boundary."""


class AgentModelCapability(str, Enum):
    """Capabilities that are allowed to affect the Agent request shape."""

    NATIVE_TOOLS = "native_tools"
    STRICT_JSON = "strict_json"
    TEXT_ONLY = "text_only"
    UNKNOWN = "unknown"


_CAPABILITY_ALIASES = {
    "native": AgentModelCapability.NATIVE_TOOLS,
    "tools": AgentModelCapability.NATIVE_TOOLS,
    "function_calling": AgentModelCapability.NATIVE_TOOLS,
    "function-calling": AgentModelCapability.NATIVE_TOOLS,
    "json": AgentModelCapability.STRICT_JSON,
    "strict": AgentModelCapability.STRICT_JSON,
    "text": AgentModelCapability.TEXT_ONLY,
    "draft": AgentModelCapability.TEXT_ONLY,
}


DEFAULT_AGENT_DEADLINE_SECONDS = 60.0
DEFAULT_MAX_REASONING_BYTES = 64 * 1024
DEFAULT_MAX_TEXT_BYTES = 128 * 1024
DEFAULT_MAX_TOOL_ARGUMENT_BYTES = 32 * 1024
DEFAULT_MAX_RAW_BUFFER_BYTES = 2 * 1024 * 1024


def normalize_capability(value: Any) -> AgentModelCapability:
    """Normalize a persisted capability without treating unknown as safe."""

    if isinstance(value, AgentModelCapability):
        return value
    text = str(value or "").strip().lower()
    for item in AgentModelCapability:
        if text == item.value:
            return item
    return _CAPABILITY_ALIASES.get(text, AgentModelCapability.UNKNOWN)


def effective_capability(value: Any) -> AgentModelCapability:
    """Return the request mode that is safe for the current implementation.

    ``strict_json`` is represented in the snapshot so a later protocol
    implementation can be selected explicitly.  Until that parser can turn a
    complete JSON envelope into a validated ``ToolCall``, it is a no-tool
    request and therefore cannot execute database work.
    """

    capability = normalize_capability(value)
    if capability in (AgentModelCapability.NATIVE_TOOLS, AgentModelCapability.STRICT_JSON):
        return capability
    return AgentModelCapability.TEXT_ONLY


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True, slots=True)
class AgentModelSnapshot:
    """Public model metadata captured at the start of one Agent run.

    Deliberately absent: base URL, token, cookies, proxy credentials and any
    other endpoint secret.  The private transport configuration is held only
    by :class:`tools.data_center.host_model_adapter.AgentModelHostAdapter`.
    """

    model_config_id: str
    model: str
    capability: AgentModelCapability = AgentModelCapability.UNKNOWN
    response_format: str = "json_object"
    timeout_seconds: float = DEFAULT_AGENT_DEADLINE_SECONDS
    ssl_verify: bool = True
    app_tag: str = ""
    max_tokens: int = 4096
    include_usage: bool = True
    reasoning_fields: tuple[str, ...] = ()
    deadline_seconds: float | None = None
    max_reasoning_bytes: int = DEFAULT_MAX_REASONING_BYTES
    max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES
    max_tool_argument_bytes: int = DEFAULT_MAX_TOOL_ARGUMENT_BYTES
    max_raw_buffer_bytes: int = DEFAULT_MAX_RAW_BUFFER_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_config_id", str(self.model_config_id or ""))
        object.__setattr__(self, "model", str(self.model or "").strip())
        object.__setattr__(self, "capability", normalize_capability(self.capability))
        response_format = str(self.response_format or "json_object").strip().lower()
        if response_format not in {"json_object", "json_schema"}:
            response_format = "json_object"
        object.__setattr__(self, "response_format", response_format)
        deadline_value = self.deadline_seconds
        if deadline_value is None:
            deadline_value = self.timeout_seconds
        deadline_value = _safe_float(
            deadline_value,
            default=DEFAULT_AGENT_DEADLINE_SECONDS,
            minimum=0.01,
            maximum=60.0,
        )
        object.__setattr__(self, "timeout_seconds", deadline_value)
        object.__setattr__(self, "deadline_seconds", deadline_value)
        object.__setattr__(self, "ssl_verify", _safe_bool(self.ssl_verify, default=True))
        object.__setattr__(self, "app_tag", str(self.app_tag or "").strip())
        object.__setattr__(
            self,
            "max_tokens",
            _safe_int(self.max_tokens, default=4096, minimum=1, maximum=4096),
        )
        object.__setattr__(self, "include_usage", _safe_bool(self.include_usage, default=True))
        raw_reasoning_fields = self.reasoning_fields
        if isinstance(raw_reasoning_fields, str):
            raw_reasoning_fields = (raw_reasoning_fields,)
        fields = []
        for value in raw_reasoning_fields or ():
            field_name = str(value or "").strip()
            if field_name and field_name not in fields:
                fields.append(field_name)
        object.__setattr__(self, "reasoning_fields", tuple(fields))
        object.__setattr__(
            self,
            "max_reasoning_bytes",
            _safe_int(
                self.max_reasoning_bytes,
                default=DEFAULT_MAX_REASONING_BYTES,
                minimum=1,
                maximum=4 * 1024 * 1024,
            ),
        )
        object.__setattr__(
            self,
            "max_text_bytes",
            _safe_int(
                self.max_text_bytes,
                default=DEFAULT_MAX_TEXT_BYTES,
                minimum=1,
                maximum=4 * 1024 * 1024,
            ),
        )
        object.__setattr__(
            self,
            "max_tool_argument_bytes",
            _safe_int(
                self.max_tool_argument_bytes,
                default=DEFAULT_MAX_TOOL_ARGUMENT_BYTES,
                minimum=1,
                maximum=4 * 1024 * 1024,
            ),
        )
        object.__setattr__(
            self,
            "max_raw_buffer_bytes",
            _safe_int(
                self.max_raw_buffer_bytes,
                default=DEFAULT_MAX_RAW_BUFFER_BYTES,
                minimum=1,
                maximum=8 * 1024 * 1024,
            ),
        )

    @property
    def effective_capability(self) -> AgentModelCapability:
        return effective_capability(self.capability)

    @property
    def tools_enabled(self) -> bool:
        return self.effective_capability is AgentModelCapability.NATIVE_TOOLS

    def as_public_dict(self) -> dict[str, Any]:
        """Return the event/UI-safe representation of this snapshot."""

        return {
            "model_config_id": self.model_config_id,
            "model": self.model,
            "capability": self.capability.value,
            "effective_capability": self.effective_capability.value,
            "tools_enabled": self.tools_enabled,
            "response_format": self.response_format,
            "timeout_seconds": self.timeout_seconds,
            "deadline_seconds": self.deadline_seconds,
            "ssl_verify": self.ssl_verify,
            "app_tag": self.app_tag,
            "max_tokens": self.max_tokens,
            "include_usage": self.include_usage,
            "reasoning_fields": list(self.reasoning_fields),
            "max_reasoning_bytes": self.max_reasoning_bytes,
            "max_text_bytes": self.max_text_bytes,
            "max_tool_argument_bytes": self.max_tool_argument_bytes,
            "max_raw_buffer_bytes": self.max_raw_buffer_bytes,
        }


@dataclass(frozen=True, slots=True)
class _AgentModelProfile:
    """Private snapshot plus the endpoint mapping used by the HTTP adapter."""

    snapshot: AgentModelSnapshot
    transport_config: Mapping[str, Any] = field(repr=False, compare=False)


def _default_loader(model_config_id: str) -> Mapping[str, Any]:
    """Load exactly the selected ID through the existing intranet policy."""

    # Deliberately lazy: importing this module must not read data/ or load
    # secure-store backends.  ``_cfg_for_call`` also preserves the old
    # model-id selection semantics instead of silently using the active model.
    from tools import intranet_llm

    return intranet_llm._cfg_for_call(model_config_id=model_config_id)


def _load_raw_config(
    model_config_id: str,
    *,
    loader: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_id = str(model_config_id or "").strip()
    if not selected_id:
        raise AgentModelConfigError("model configuration id is required")
    source = loader or _default_loader
    try:
        raw = source(selected_id)
    except AgentModelConfigError:
        raise
    except Exception as exc:
        # Do not carry loader text into an Agent event; it could contain a URL
        # or a credential from a third-party configuration backend.
        raise AgentModelConfigError("model configuration is unavailable") from exc
    if not isinstance(raw, Mapping):
        raise AgentModelConfigError("model configuration is invalid")
    data = dict(raw)
    configured_id = str(data.get("id") or selected_id).strip()
    if configured_id != selected_id:
        raise AgentModelConfigError("model configuration selection changed")
    return data


def _profile_from_raw(model_config_id: str, raw: Mapping[str, Any], *, require_endpoint: bool) -> _AgentModelProfile:
    selected_id = str(model_config_id or "").strip()
    data = dict(raw)
    model = str(data.get("model") or "").strip()
    if not model:
        raise AgentModelConfigError("selected model is not configured")

    enabled = _safe_bool(data.get("enabled"), default=False)
    base_url = str(data.get("base_url") or "").strip()
    if require_endpoint and (not enabled or not base_url):
        raise AgentModelConfigError("selected intranet model is disabled")

    capability = normalize_capability(
        data.get("agent_capability", data.get("capability", data.get("agent_mode")))
    )
    reasoning_fields = data.get("agent_reasoning_fields", data.get("reasoning_fields", ()))
    if isinstance(reasoning_fields, str):
        reasoning_fields = (reasoning_fields,)
    elif not isinstance(reasoning_fields, (list, tuple)):
        reasoning_fields = ()
    configured_deadline = None
    for deadline_key in ("deadline_seconds", "agent_deadline_seconds", "model_timeout_seconds"):
        if deadline_key in data:
            configured_deadline = data.get(deadline_key)
            break
    snapshot = AgentModelSnapshot(
        model_config_id=selected_id,
        model=model,
        capability=capability,
        response_format=str(data.get("agent_response_format", data.get("response_format", "json_object"))),
        timeout_seconds=data.get(
            "timeout_seconds",
            configured_deadline if configured_deadline is not None else DEFAULT_AGENT_DEADLINE_SECONDS,
        ),
        deadline_seconds=configured_deadline,
        ssl_verify=data.get("ssl_verify", True),
        app_tag=data.get("app_tag", ""),
        max_tokens=data.get("max_tokens", 4096),
        include_usage=data.get("include_usage", True),
        reasoning_fields=tuple(reasoning_fields),
        max_reasoning_bytes=data.get(
            "agent_max_reasoning_bytes",
            data.get(
                "max_reasoning_bytes",
                data.get("reasoning_max_bytes", DEFAULT_MAX_REASONING_BYTES),
            ),
        ),
        max_text_bytes=data.get(
            "agent_max_text_bytes",
            data.get(
                "max_text_bytes",
                data.get("text_max_bytes", DEFAULT_MAX_TEXT_BYTES),
            ),
        ),
        max_tool_argument_bytes=data.get(
            "agent_max_tool_argument_bytes",
            data.get(
                "max_tool_argument_bytes",
                data.get("tool_argument_max_bytes", DEFAULT_MAX_TOOL_ARGUMENT_BYTES),
            ),
        ),
        max_raw_buffer_bytes=data.get(
            "agent_max_raw_buffer_bytes",
            data.get(
                "max_raw_buffer_bytes",
                data.get("raw_buffer_max_bytes", DEFAULT_MAX_RAW_BUFFER_BYTES),
            ),
        ),
    )
    private_config = dict(data)
    private_config["id"] = selected_id
    private_config["model"] = snapshot.model
    private_config["agent_capability"] = snapshot.capability.value
    private_config["timeout_seconds"] = snapshot.timeout_seconds
    private_config["deadline_seconds"] = snapshot.deadline_seconds
    private_config["max_tokens"] = snapshot.max_tokens
    private_config["ssl_verify"] = snapshot.ssl_verify
    private_config["include_usage"] = snapshot.include_usage
    private_config["agent_reasoning_fields"] = list(snapshot.reasoning_fields)
    private_config["agent_max_reasoning_bytes"] = snapshot.max_reasoning_bytes
    private_config["agent_max_text_bytes"] = snapshot.max_text_bytes
    private_config["agent_max_tool_argument_bytes"] = snapshot.max_tool_argument_bytes
    private_config["agent_max_raw_buffer_bytes"] = snapshot.max_raw_buffer_bytes
    return _AgentModelProfile(snapshot=snapshot, transport_config=private_config)


def load_agent_model_snapshot(
    model_config_id: str,
    *,
    loader: Callable[[str], Mapping[str, Any]] | None = None,
) -> AgentModelSnapshot:
    """Load one selected model ID and return only public-safe metadata."""

    raw = _load_raw_config(model_config_id, loader=loader)
    return _profile_from_raw(model_config_id, raw, require_endpoint=False).snapshot


def load_agent_model_profile(
    model_config_id: str,
    *,
    loader: Callable[[str], Mapping[str, Any]] | None = None,
    require_endpoint: bool = True,
) -> tuple[AgentModelSnapshot, Mapping[str, Any]]:
    """Return a safe snapshot and private transport mapping for the host.

    The second tuple member is an implementation detail.  Callers should keep
    it private and must never put it into an event, log, or public DTO.
    """

    raw = _load_raw_config(model_config_id, loader=loader)
    profile = _profile_from_raw(model_config_id, raw, require_endpoint=require_endpoint)
    return profile.snapshot, dict(profile.transport_config)


def build_agent_model_snapshot(model_config_id: str, config: Mapping[str, Any]) -> AgentModelSnapshot:
    """Build a public snapshot from an already selected, injected config."""

    if not isinstance(config, Mapping):
        raise AgentModelConfigError("model configuration is invalid")
    data = dict(config)
    data.setdefault("id", model_config_id)
    raw = _load_raw_config(model_config_id, loader=lambda _selected: data)
    return _profile_from_raw(model_config_id, raw, require_endpoint=False).snapshot


__all__ = [
    "AgentModelCapability",
    "AgentModelConfigError",
    "AgentModelSnapshot",
    "DEFAULT_AGENT_DEADLINE_SECONDS",
    "DEFAULT_MAX_RAW_BUFFER_BYTES",
    "DEFAULT_MAX_REASONING_BYTES",
    "DEFAULT_MAX_TEXT_BYTES",
    "DEFAULT_MAX_TOOL_ARGUMENT_BYTES",
    "build_agent_model_snapshot",
    "effective_capability",
    "load_agent_model_profile",
    "load_agent_model_snapshot",
    "normalize_capability",
]
