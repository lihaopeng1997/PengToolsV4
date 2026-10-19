"""Owner-session adapter for the typed full-query export writer.

This module binds a previously issued session/query identity to the frozen
result-export contract. It does not create connections or read cursor data; the
session owner supplies the page iterator when it executes the request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .manual_session import ManualQueryPage, ManualSessionTarget
from .result_export import (
    DEFAULT_LIMITS,
    ExportError,
    ExportLimits,
    ExportReceipt,
    ExportScope,
    ExportTarget,
    write_result_export,
)


@dataclass(frozen=True, slots=True)
class ManualResultExportRequest:
    destination: Path
    format: str
    target: ExportTarget
    session_target: ManualSessionTarget
    overwrite: bool
    limits: ExportLimits


def make_full_query_request(
    destination: str | os.PathLike[str],
    format: str,
    target: ExportTarget,
    *,
    session_target: ManualSessionTarget,
    overwrite: bool = False,
    limits: ExportLimits = DEFAULT_LIMITS,
) -> ManualResultExportRequest:
    """Validate an immutable request without touching the filesystem or DB."""

    if not isinstance(target, ExportTarget) or not isinstance(session_target, ManualSessionTarget):
        raise ExportError("INVALID_TARGET")
    if not isinstance(format, str):
        raise ExportError("UNSUPPORTED_FORMAT")
    selected_format = format.strip().lower()
    if selected_format not in {"csv", "json", "xlsx"}:
        raise ExportError("UNSUPPORTED_FORMAT")
    if not isinstance(overwrite, bool) or not isinstance(limits, ExportLimits):
        raise ExportError("INVALID_REQUEST")
    try:
        selected_destination = Path(os.fspath(destination))
    except (TypeError, ValueError, OSError) as error:
        raise ExportError("INVALID_DESTINATION") from error
    return ManualResultExportRequest(
        destination=selected_destination,
        format=selected_format,
        target=target,
        session_target=session_target,
        overwrite=overwrite,
        limits=limits,
    )


def write_full_query(
    request: ManualResultExportRequest,
    pages: Iterable[ManualQueryPage],
    *,
    cancel_check: Callable[[], bool],
) -> ExportReceipt:
    """Write a full typed page chain using the existing atomic writer."""

    if not isinstance(request, ManualResultExportRequest):
        raise ExportError("INVALID_REQUEST")
    return write_result_export(
        request.destination,
        request.format,
        ExportScope.FULL_QUERY,
        target=request.target,
        pages=pages,
        cancel_check=cancel_check,
        overwrite=request.overwrite,
        limits=request.limits,
    )


__all__ = [
    "ManualResultExportRequest",
    "make_full_query_request",
    "write_full_query",
]
