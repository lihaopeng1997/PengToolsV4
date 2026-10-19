"""Standalone data-center UI prototypes.

The package deliberately keeps the Agent event panel independent from the
production data-center workbench.  It may consume the UI-independent event
contract, but it does not own a driver, model client, or application wiring.
"""

from .agent_panel import AgentEventFacade, AgentPanel, DataCenterAgentPanel

__all__ = ["AgentEventFacade", "AgentPanel", "DataCenterAgentPanel"]
