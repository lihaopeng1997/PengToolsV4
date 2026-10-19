from __future__ import annotations
import sys
from tools.agent_core import model_adapter as _shared
_shared.ModelAdapter.__module__ = __name__
sys.modules[__name__] = _shared