"""Detach Oracle LOBs on the cursor owner, with bounded reads and no SDK import."""

from __future__ import annotations

import sys

from tools.data_center.manual_session import ManualSessionError, _estimate_value_bytes


def _read_value(lob, api, max_bytes, cancelled):
    binary = lob.type is api.DB_TYPE_BLOB
    if not binary and lob.type not in (api.DB_TYPE_CLOB, api.DB_TYPE_NCLOB):
        raise ManualSessionError('LOB_TYPE_UNSUPPORTED')
    if cancelled():
        raise ManualSessionError('CANCELLED')
    size = lob.size()
    if type(size) is not int or size < 0:
        raise ManualSessionError('LOB_VALUE_INVALID')
    if max_bytes < 128 or (binary and size + 128 > max_bytes):
        raise ManualSessionError('RESULT_VALUE_LIMIT')
    parts = []
    offset = 1
    characters = 0
    width = 1
    while offset <= size:
        if cancelled():
            raise ManualSessionError('CANCELLED')
        amount = min(32768, size - offset + 1)
        try:
            part = lob.read(offset=offset, amount=amount)
        except UnicodeDecodeError:
            # A requested UCS-2 boundary can bisect a supplementary character.
            # Retry only this read-only decoding boundary, never server failures.
            if binary or amount <= 1:
                raise
            if cancelled():
                raise ManualSessionError('CANCELLED')
            part = lob.read(offset=offset, amount=amount - 1)
        if cancelled():
            raise ManualSessionError('CANCELLED')
        if type(part) is not (bytes if binary else str):
            raise ManualSessionError('LOB_VALUE_INVALID')
        units = len(part) if binary else len(part.encode('utf-16-le')) // 2
        if units <= 0 or offset + units - 1 > size:
            raise ManualSessionError('LOB_VALUE_INVALID')
        characters += len(part)
        if not binary and not part.isascii():
            width = max(width, 4 if any(ord(char) > 65535 for char in part) else 2)
        if characters * width + 128 > max_bytes:
            raise ManualSessionError('RESULT_VALUE_LIMIT')
        parts.append(part)
        offset += units
    return b''.join(parts) if binary else ''.join(parts)


def detach_oracle_row(row, *, max_bytes, cancelled):
    """Convert only genuine SDK LOBs; arbitrary readable objects remain untouched."""
    api = sys.modules.get('oracledb')
    lob_type = getattr(api, 'LOB', None) if api is not None else None
    values = list(row)
    if lob_type is None:
        return tuple(values)
    changed = False
    for index, value in enumerate(values):
        if not isinstance(value, lob_type):
            continue
        changed = True
        base = _estimate_value_bytes(tuple(values)) - _estimate_value_bytes(value)
        values[index] = _read_value(value, api, max_bytes - base, cancelled)
        if _estimate_value_bytes(tuple(values)) > max_bytes:
            raise ManualSessionError('RESULT_VALUE_LIMIT')
    if changed and cancelled():
        raise ManualSessionError('CANCELLED')
    return tuple(values)
