"""Intranet-mode switch: one opt-in that relaxes product-side guardrails.

Read once per process. Changing the preference takes effect after a restart,
which keeps every call site free of repeated disk I/O and avoids partially
applied states mid-run.

Nothing here removes a security boundary. The SQL read-only whitelist, private
host resolution, primary-key verification, transaction-unknown handling and
context integrity checks stay in force regardless of this switch.
"""
from __future__ import annotations

import os

_ENV_FLAG = 'PENGTOOLS_INTRANET'
_cache: dict[str, bool | None] = {'open': None}


def _from_preferences() -> bool:
    try:
        from config import load_settings
        return bool(load_settings().get('intranet_open'))
    except Exception:
        return False


def is_intranet_open() -> bool:
    """True when the environment flag or the saved preference enables it."""
    cached = _cache['open']
    if cached is None:
        cached = os.environ.get(_ENV_FLAG) == '1' or _from_preferences()
        _cache['open'] = bool(cached)
    return bool(cached)


def reset_cache() -> None:
    """Forget the memoized value; used by settings and by tests."""
    _cache['open'] = None


def intranet_source() -> str:
    """Where the current value came from: env / preference / default."""
    if os.environ.get(_ENV_FLAG) == '1':
        return 'env'
    return 'preference' if _from_preferences() else 'default'
