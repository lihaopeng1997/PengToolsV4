"""Format detached scalar values without invoking driver-backed conversions."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID


_SCALAR_TYPES = frozenset({str, bool, int, float, Decimal, date, datetime, time, timedelta, UUID})
_BINARY_TYPES = frozenset({bytes, bytearray, memoryview})
MAX_PREVIEW_CHARACTERS = 1_000_000


def is_loaded_value(value):
    return value is None or type(value) in _SCALAR_TYPES or type(value) in _BINARY_TYPES


def value_preview(value, limit=MAX_PREVIEW_CHARACTERS):
    if value is None:
        return 'NULL', 4
    if type(value) in _BINARY_TYPES:
        raw = memoryview(value)
        if raw.contiguous:
            raw = raw.cast('B')
        visible = max(0, limit - 2)
        items = ((visible + 1) // 2 + raw.itemsize - 1) // raw.itemsize
        return ('0x' + raw[:items].hex()[:visible])[:limit], raw.nbytes * 2 + 2
    if type(value) is str:
        return value[:limit], len(value)
    text = str(value) if is_loaded_value(value) else f'<{type(value).__name__}>'
    return text[:limit], len(text)
