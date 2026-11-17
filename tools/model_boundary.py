"""Classify a model endpoint by its real target host.

The product switch in :mod:`tools.intranet_mode` is deliberately NOT consulted
here. That switch relaxes product guardrails; it must never be able to promote
a public endpoint to "intranet" and skip the outbound confirmation.

Only an IP address that is provably private/loopback, or the literal
``localhost`` name, proves intranet. A DNS suffix such as ``.internal`` or a
single-label name proves nothing - anyone can configure one - so those are
``unknown`` and therefore require confirmation, same as a public host.

No DNS lookup happens: classification is offline and deterministic, and an
unparsable target fails closed.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

_LOOPBACK_NAMES = frozenset({'localhost', 'localhost.localdomain'})


@dataclass(frozen=True, slots=True)
class ModelBoundary:
    """Where a model request would actually be sent."""

    kind: str          # 'intranet' | 'internet' | 'unknown'
    host: str
    reason: str

    @property
    def is_intranet(self) -> bool:
        return self.kind == 'intranet'

    @property
    def requires_confirmation(self) -> bool:
        # Only a proven intranet target skips the outbound confirmation.
        return self.kind != 'intranet'

    def label(self, zh: bool = True) -> str:
        if self.kind == 'intranet':
            return f'内网模型 · {self.host}' if zh else f'Intranet model · {self.host}'
        if self.kind == 'internet':
            return f'外网模型 · {self.host}（发送前需确认）' if zh else f'Internet model · {self.host} (confirm before sending)'
        return '模型地址未知（按外网处理）' if zh else 'Unknown endpoint (treated as internet)'


def endpoint_host(target: str) -> str:
    """Extract a lowercase hostname from a URL or a bare host[:port]."""
    text = str(target or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = '//' + text
    try:
        return (urlsplit(text).hostname or '').strip().lower().rstrip('.')
    except ValueError:
        return ''


def classify_endpoint(target: str) -> ModelBoundary:
    """Classify one endpoint without any network access."""
    host = endpoint_host(target)
    if not host:
        return ModelBoundary('unknown', '', 'no endpoint configured')

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        if address.is_loopback or address.is_private or address.is_link_local:
            return ModelBoundary('intranet', host, 'private, loopback or link-local address')
        return ModelBoundary('internet', host, 'public IP address')

    if host in _LOOPBACK_NAMES:
        return ModelBoundary('intranet', host, 'loopback hostname')
    # A DNS suffix or a single-label name is not proof: it is a configuration,
    # not an address. Treat it as unknown so confirmation is still required.
    return ModelBoundary('unknown', host, 'hostname cannot prove it is internal')


def classify_private_config(private_config) -> ModelBoundary | None:
    """Best-effort classification of a host adapter's private transport config."""
    if not isinstance(private_config, dict):
        return None
    for key in ('base_url', 'endpoint', 'url', 'host'):
        value = str(private_config.get(key) or '').strip()
        if value:
            return classify_endpoint(value)
    return None
