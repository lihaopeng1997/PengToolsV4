"""Bind verified historical evidence to bounded requests, never to tool authority."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from urllib.parse import urlsplit

from .context import ContextPlanner
from .memory import MemoryBinding, build_memory, decode_memory


def model_profile_revision(config: dict) -> str:
    """Hash public routing/behavior metadata, excluding credentials completely.

    URLs containing user-info, query, or fragment get a per-run identity: their
    private route cannot safely be represented by this public cache binding.
    """
    public = {key: config.get(key) for key in (
        'id', 'model', 'provider', 'agent_capability', 'capability',
        'max_tokens', 'ssl_verify', 'response_format', 'enabled',
    )}
    try:
        route = urlsplit(str(config.get('base_url') or ''))
        if route.username or route.password or route.query or route.fragment:
            return 'ephemeral-' + uuid.uuid4().hex
        public['route'] = [route.scheme.lower(), route.hostname, route.port, route.path]
        encoded = json.dumps(public, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        return 'ephemeral-' + uuid.uuid4().hex
    return hashlib.sha256(encoded).hexdigest()


class MemoryContextPlanner:
    """Keep canonical history intact; insert a pinned public memory only on prune."""

    def __init__(self, planner: ContextPlanner, binding: MemoryBinding, previous=None):
        self.planner = planner
        self.limits = planner.limits
        self.binding = binding
        self.previous = previous
        self.memory = None

    def plan(self, *, system_prompt, messages, tools, pending=False):
        base = self.planner.plan(system_prompt=system_prompt, messages=messages,
                                 tools=tools, pending=pending)
        if base.status != 'ready' or pending:
            return base
        try:
            previous = decode_memory(self.previous, messages=messages, binding=self.binding)
            self.memory = build_memory(messages=messages, binding=self.binding, previous=previous)
            self.previous = self.memory.to_dict()
        except (TypeError, ValueError, UnicodeError):
            self.memory = None
        if not base.messages_dropped or self.memory is None:
            return base
        # The first user remains an anchor, and this boundary cannot split a
        # tool exchange. Memory stays an assistant data message, not a policy.
        index = next((i + 1 for i, item in enumerate(messages)
                      if item.get('role') == 'user'), 0)
        augmented = list(messages)
        augmented.insert(index, self.memory.to_message())
        try:
            plan = self.planner.plan(system_prompt=system_prompt, messages=augmented,
                                     tools=tools, pinned_indexes=(index,))
        except TypeError:
            return replace(base, reason='memory_omitted')
        if plan.status != 'ready':
            return replace(base, reason='memory_omitted')
        return replace(plan, reason='memory_compacted',
                       messages_dropped=len(messages) - len(plan.messages) + 1)
