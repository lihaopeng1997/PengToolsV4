"""Compatibility exports for the shared, pure-Python Agent runner."""

from __future__ import annotations

from typing import Callable

from tools.agent_core.runner import AgentRunner as _SharedAgentRunner
from tools.agent_core.runner import run_agent as _shared_run_agent
from tools.agent_core.contracts import ToolCall


_QUERY_TOOL_NAMES = frozenset({
    "search_objects", "describe_object", "query_readonly", "query_read_only",
    "sql_query", "read_query", "mongo_read", "redis_read",
})


def _is_query_tool(call: ToolCall) -> bool:
    name = call.name.strip().lower()
    return (
        name in _QUERY_TOOL_NAMES
        or name.startswith("query_")
        or name.endswith("_query")
        or ("query" in name and any(word in name for word in ("read", "select", "execute")))
    )


class AgentRunner(_SharedAgentRunner):
    """Data-center policy adapter over the common execution loop."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("is_query_tool", _is_query_tool)
        super().__init__(*args, **kwargs)


DataCenterAgent = AgentRunner
Agent = AgentRunner


def run_agent(context, user_message, model_adapter, tool_registry, **kwargs):
    """Functional compatibility wrapper retaining data-center query budgets."""
    return AgentRunner(model_adapter, tool_registry, **kwargs).run(context, user_message)


__all__ = ["Agent", "AgentRunner", "DataCenterAgent", "run_agent"]