from __future__ import annotations
import sys
from tools.agent_core import budget as _shared
for _type in (_shared.AgentBudget, _shared.Budget, _shared.BudgetExceeded, _shared.BudgetLimits, _shared.BudgetSnapshot):
    _type.__module__ = __name__
sys.modules[__name__] = _shared