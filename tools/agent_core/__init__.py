"""Shared, UI-independent Agent execution primitives."""

from .budget import AgentBudget, Budget, BudgetExceeded, BudgetExhausted, BudgetLimits, BudgetSnapshot
from .contracts import (
    AgentEvent,
    AgentEventEmitter,
    AgentEventType,
    AgentRunResult,
    AgentStatus,
    AgentTurn,
    AgentTurnRequest,
    CancellationToken,
    ModelAdapter as ModelAdapterProtocol,
    ModelDelta,
    RunContext,
    ToolCall,
    ToolRegistry,
    ToolResult,
    stable_json,
)
from .host_model_adapter import AgentHostModelAdapter, AgentModelHostAdapter, HostModelAdapter
from .history import tail_complete_history
from .model_adapter import (
    ModelAdapter as StreamingModelAdapter,
    ModelAdapterError,
    ModelCancelled,
    ModelDeadlineExceeded,
    ModelLimitExceeded,
    ModelProtocolError,
    ModelTransportError,
    StreamingTransport,
    UrlLibStreamingTransport,
)
from .model_config import (
    AgentModelCapability,
    AgentModelConfigError,
    AgentModelSnapshot,
    build_agent_model_snapshot,
    load_agent_model_profile,
    load_agent_model_snapshot,
)
from .runner import AgentRunner, run_agent
from .streaming import (
    IncrementalModelParser,
    IncrementalSSEDecoder,
    ModelStreamParser,
    OpenAIStreamParser,
    SSEEvent,
    SSEParser,
    SSEProtocolError,
    StreamingError,
    ToolArgumentsError,
    iter_sse_events,
)

# ``ModelAdapter`` names the structural protocol used for injected fakes and
# hosts. ``StreamingModelAdapter`` names the concrete OpenAI-compatible SSE
# implementation; selected-config hosts normally use ``AgentModelHostAdapter``.
ModelAdapter = ModelAdapterProtocol

__all__ = [
    "AgentBudget", "AgentEvent", "AgentEventEmitter", "AgentEventType", "AgentHostModelAdapter",
    "AgentModelCapability", "AgentModelConfigError", "AgentModelHostAdapter", "AgentModelSnapshot",
    "AgentRunResult", "AgentRunner", "AgentStatus", "AgentTurn", "AgentTurnRequest", "Budget",
    "BudgetExceeded", "BudgetExhausted", "BudgetLimits", "BudgetSnapshot", "CancellationToken",
    "HostModelAdapter", "IncrementalModelParser", "IncrementalSSEDecoder", "ModelAdapter",
    "ModelAdapterProtocol",
    "ModelAdapterError", "ModelCancelled", "ModelDeadlineExceeded", "ModelDelta", "ModelLimitExceeded",
    "ModelProtocolError", "ModelStreamParser", "ModelTransportError", "OpenAIStreamParser", "RunContext",
    "SSEEvent", "SSEParser", "SSEProtocolError", "StreamingError", "StreamingTransport", "ToolArgumentsError",
    "StreamingModelAdapter", "ToolCall", "ToolRegistry", "ToolResult", "UrlLibStreamingTransport", "build_agent_model_snapshot",
    "iter_sse_events", "load_agent_model_profile", "load_agent_model_snapshot", "run_agent", "stable_json",
    "tail_complete_history",
]
