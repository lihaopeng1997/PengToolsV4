from __future__ import annotations
import sys
from tools.agent_core import model_config as _shared
for _type in (_shared.AgentModelCapability, _shared.AgentModelConfigError, _shared.AgentModelSnapshot):
    _type.__module__ = __name__
sys.modules[__name__] = _shared