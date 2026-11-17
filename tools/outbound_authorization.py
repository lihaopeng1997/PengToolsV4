"""Per-run outbound authorization for diagnostics.

A run may only reach a model endpoint when the caller has explicitly approved
*this* model target *and* *this* material set. The grant is checked inside the
run itself, immediately before the adapter is used, so a UI that forgets to ask
cannot send anything.

``tools.intranet_mode`` is not consulted: that switch relaxes product
guardrails and must never stand in for an outbound approval.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

GRANT_TTL_SECONDS = 15 * 60


class OutboundNotAuthorized(RuntimeError):
    """Raised before any request when the outbound scope was not approved."""

    def __init__(self, reason: str, *, run_id: str = '', kind: str = ''):
        super().__init__(reason)
        self.reason = reason
        self.run_id = run_id
        self.kind = kind


@dataclass(frozen=True, slots=True)
class OutboundGrant:
    run_id: str
    model_config_id: str
    host: str
    materials: tuple[str, ...]
    code_root: str
    allowed_tools: tuple[str, ...]
    boundary_kind: str
    issued_at: float

    def covers(self, *, model_config_id: str, host: str, materials,
               code_root: str = '', allowed_tools=()) -> bool:
        return (
            self.model_config_id == model_config_id
            and self.host == host
            and self.materials == normalize_materials(materials)
            and self.code_root == str(code_root or '')
            and self.allowed_tools == normalize_tools(allowed_tools)
            and time.time() - self.issued_at <= GRANT_TTL_SECONDS
        )

    def summary(self) -> dict:
        return {
            'run_id': self.run_id,
            'model_config_id': self.model_config_id,
            'host': self.host,
            'materials': list(self.materials),
            'code_root': self.code_root,
            'allowed_tools': list(self.allowed_tools),
            'boundary': self.boundary_kind,
        }


def normalize_materials(materials) -> tuple[str, ...]:
    if not isinstance(materials, (list, tuple, set, frozenset)):
        return ()
    cleaned = {str(item).strip() for item in materials if str(item).strip()}
    return tuple(sorted(cleaned))


def normalize_tools(tools) -> tuple[str, ...]:
    if not isinstance(tools, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({str(item).strip() for item in tools if str(item).strip()}))


_lock = threading.RLock()
_grants: dict[str, OutboundGrant] = {}


def issue(run_id: str, *, model_config_id: str, host: str, materials,
          code_root: str = '', allowed_tools=(), boundary_kind: str) -> OutboundGrant:
    grant = OutboundGrant(
        run_id=str(run_id),
        model_config_id=str(model_config_id or ''),
        host=str(host or '').lower(),
        materials=normalize_materials(materials),
        code_root=str(code_root or ''),
        allowed_tools=normalize_tools(allowed_tools),
        boundary_kind=str(boundary_kind or 'unknown'),
        issued_at=time.time(),
    )
    with _lock:
        _grants[grant.run_id] = grant
    return grant


def check(run_id: str, *, model_config_id: str, host: str, materials,
          code_root: str = '', allowed_tools=()) -> OutboundGrant:
    """Return the grant or raise; never called after a request has been sent."""
    with _lock:
        grant = _grants.get(str(run_id))
    if grant is None:
        raise OutboundNotAuthorized('本轮尚未获得外发授权', run_id=str(run_id))
    if grant.model_config_id != str(model_config_id or ''):
        raise OutboundNotAuthorized('模型目标已变化，需要重新确认外发', run_id=grant.run_id)
    if grant.host != str(host or '').lower():
        raise OutboundNotAuthorized('模型地址已变化，需要重新确认外发', run_id=grant.run_id)
    if grant.materials != normalize_materials(materials):
        raise OutboundNotAuthorized('材料范围已变化，需要重新确认外发', run_id=grant.run_id)
    if grant.code_root != str(code_root or ''):
        raise OutboundNotAuthorized('代码根目录已变化，需要重新确认外发', run_id=grant.run_id)
    if grant.allowed_tools != normalize_tools(allowed_tools):
        raise OutboundNotAuthorized('允许的工具范围已变化，需要重新确认外发', run_id=grant.run_id)
    if time.time() - grant.issued_at > GRANT_TTL_SECONDS:
        raise OutboundNotAuthorized('外发授权已过期，需要重新确认', run_id=grant.run_id)
    return grant


def revoke(run_id: str) -> None:
    with _lock:
        _grants.pop(str(run_id), None)


def clear() -> None:
    with _lock:
        _grants.clear()
