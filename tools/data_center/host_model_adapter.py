"""Host adapter that binds one Agent run to one selected model configuration.

The adapter is the boundary between a UI/controller and the streaming model
protocol.  It resolves a model configuration by its stable ID, keeps the
endpoint mapping private, and exposes only a public-safe snapshot.  The
existing ``tools.intranet_llm.chat_completions`` path is never called here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from .contracts import AgentTurn, AgentTurnRequest, CancellationToken, DeltaSink
from .model_adapter import ModelAdapter as StreamingModelAdapter
from .model_config import (
    AgentModelCapability,
    AgentModelConfigError,
    AgentModelSnapshot,
    load_agent_model_profile,
)


class AgentModelHostAdapter:
    """Bind a streaming adapter to one immutable model-config selection."""

    def __init__(
        self,
        model_config_id: str,
        *,
        loader: Callable[[str], Mapping[str, Any]] | None = None,
        config: Mapping[str, Any] | None = None,
        transport: Any = None,
        retry_count: int = 2,
        require_endpoint: bool | None = None,
        deadline_seconds: float | None = None,
        max_reasoning_bytes: int | None = None,
        max_text_bytes: int | None = None,
        max_tool_argument_bytes: int | None = None,
        max_raw_buffer_bytes: int | None = None,
    ) -> None:
        selected_id = str(model_config_id or "").strip()
        if not selected_id:
            raise AgentModelConfigError("model configuration id is required")
        if config is not None and loader is not None:
            raise AgentModelConfigError("choose a config or a loader, not both")
        if require_endpoint is None:
            # Even an injected transport receives a selected, enabled profile;
            # tests can replace only the I/O boundary, never the config gate.
            require_endpoint = True

        if config is not None:
            supplied = dict(config)
            supplied.setdefault("id", selected_id)
            loader = lambda _selected_id: supplied

        snapshot, private_config = load_agent_model_profile(
            selected_id,
            loader=loader,
            require_endpoint=bool(require_endpoint),
        )
        self._snapshot = snapshot
        # This mapping may contain an encrypted token and an endpoint.  Keep it
        # private; it is deliberately absent from public_metadata and repr.
        self._transport_config = dict(private_config)
        self._adapter = StreamingModelAdapter(
            self._transport_config,
            transport=transport,
            retry_count=retry_count,
            reasoning_fields=self._snapshot.reasoning_fields,
            deadline_seconds=deadline_seconds,
            max_reasoning_bytes=max_reasoning_bytes,
            max_text_bytes=max_text_bytes,
            max_tool_argument_bytes=max_tool_argument_bytes,
            max_raw_buffer_bytes=max_raw_buffer_bytes,
        )

    @property
    def snapshot(self) -> AgentModelSnapshot:
        """Public-safe immutable metadata for the selected model."""

        return self._snapshot

    @property
    def model_config_id(self) -> str:
        return self._snapshot.model_config_id

    @property
    def model(self) -> str:
        return self._snapshot.model

    @property
    def capability(self) -> AgentModelCapability:
        return self._snapshot.capability

    @property
    def effective_capability(self) -> AgentModelCapability:
        return self._snapshot.effective_capability

    @property
    def public_metadata(self) -> dict[str, Any]:
        """Return metadata suitable for a UI event or status card."""

        return self._snapshot.as_public_dict()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model_config_id={self.model_config_id!r}, "
            f"model={self.model!r}, capability={self.capability.value!r})"
        )

    def complete_turn(
        self,
        request: AgentTurnRequest,
        *,
        cancellation: CancellationToken,
        on_delta: DeltaSink,
    ) -> AgentTurn:
        """Complete one turn while enforcing the selected capability boundary."""

        if request is None:
            raise TypeError("request is required")
        context_id = str(request.context.model_config_id or "").strip()
        if context_id != self.model_config_id:
            raise AgentModelConfigError("model configuration context mismatch")

        # Only native tools may put a tool schema on the wire.  This host-side
        # copy is defence in depth in case a future transport accidentally
        # ignores its capability setting; the caller's request is immutable.
        safe_request = request
        if self.effective_capability is not AgentModelCapability.NATIVE_TOOLS and request.tools:
            safe_request = replace(request, tools=())
        return self._adapter.complete_turn(
            safe_request,
            cancellation=cancellation,
            on_delta=on_delta,
        )

    complete_agent_turn = complete_turn


# Friendly names for hosts that used the earlier terminology.
HostModelAdapter = AgentModelHostAdapter
AgentHostModelAdapter = AgentModelHostAdapter


__all__ = [
    "AgentHostModelAdapter",
    "AgentModelHostAdapter",
    "HostModelAdapter",
]
