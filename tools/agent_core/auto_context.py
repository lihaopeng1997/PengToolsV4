"""Automatic, loss-marked request compaction; canonical history is never edited.

The limits here are UTF-8 bytes, not a claim about a model's token window.
System instructions, tool definitions, the latest user request, explicitly
pinned messages and assistant tool-call identities are never abbreviated.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from .context import ContextLimits, ContextPlanner, ConservativeTokenEstimator, _strict_json_bytes

DEFAULT_CONTEXT_INPUT_BYTES = 500 * 1024
_CONSTRAINT = re.compile(r'必须|不要|不得|禁止|不能|只能|仅限|保留|要求|\b(?:must|never|only|preserve|do not)\b', re.I)
_RECEIPT_FIELDS = ('ok', 'code', 'error', 'query_id', 'evidence_id', 'tool', 'path',
                   'truncated', 'next_offset', 'start_offset', 'char_count', 'total_chars')


def _excerpt(text: str, byte_limit: int) -> str:
    raw = text.encode('utf-8')
    if len(raw) <= byte_limit:
        return text
    head = max(0, byte_limit * 2 // 3)
    tail = max(0, byte_limit - head)
    return (raw[:head].decode('utf-8', errors='ignore')
            + '\n[... omitted from this request; full original retained in history ...]\n'
            + (raw[-tail:].decode('utf-8', errors='ignore') if tail else ''))


def _compact_content(content: str, byte_limit: int, *, user: bool = False) -> str:
    if len(_strict_json_bytes(content)) <= byte_limit:
        return content
    raw = content.encode('utf-8')
    receipt = {}
    if not user:
        try:
            payload = json.loads(content)
            if isinstance(payload, dict):
                receipt = {key: payload[key] for key in _RECEIPT_FIELDS if key in payload
                           and type(payload[key]) in (str, bool, int, float, type(None))
                           and len(_strict_json_bytes(payload[key])) <= 512}
        except (ValueError, RecursionError):
            pass
    constraints = ''
    if user:
        # Exact excerpts, never generated instructions or inferred authority.
        lines = [line for line in content.splitlines() if _CONSTRAINT.search(line)]
        constraints = _excerpt('\n'.join(lines), min(4096, byte_limit // 3)) if lines else ''
    payload = {
        'context_compacted': True,
        'original_utf8_bytes': len(raw),
        'original_sha256': hashlib.sha256(raw).hexdigest(),
        'notice': ('Historical excerpts only; omitted text is not available in this request. '
                   'Read source details again when needed. Do not replay completed changes.'),
    }
    if receipt:
        payload['original_receipt'] = receipt
    if constraints:
        payload['constraint_excerpts'] = constraints
    allowance = max(0, byte_limit // 2)
    while True:
        payload['excerpt'] = _excerpt(content, allowance) if allowance else '[content omitted]'
        result = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        # Account for JSON escaping once more when placed inside a message.
        if len(_strict_json_bytes(result)) <= byte_limit:
            return result
        if allowance:
            allowance //= 2
        elif 'constraint_excerpts' in payload:
            payload.pop('constraint_excerpts')
        elif 'original_receipt' in payload:
            payload.pop('original_receipt')
        else:
            return result


class AutoCompactingContextPlanner(ContextPlanner):
    """At 80% usage, aim for 60%, with a separate unchanged wire ceiling.

First remove old complete exchanges with the ordinary planner. If retained
history itself is too large, abbreviate tool observations and older messages
in a request-only copy. Never split exchanges or change calls/arguments.
Memory/evidence wrappers may restore their bounded, source-checked ledgers.
"""

    def plan(self, *, system_prompt, messages, tools, pending=False, pinned_indexes=()):
        base = super().plan(system_prompt=system_prompt, messages=messages, tools=tools,
                            pending=pending, pinned_indexes=pinned_indexes)
        if (pending or type(self.estimator) is not ConservativeTokenEstimator
                or (base.status != 'ready' and base.reason != 'context_anchors_too_large')):
            return base
        ceiling = self._input_budget()
        if base.status == 'ready' and not base.messages_dropped and base.estimate.tokens <= ceiling * 4 // 5:
            return base

        # Leave room for transport-only JSON fields and private continuation.
        reserve = min(32 * 1024, max(0, ceiling // 10))
        hard = ContextPlanner(replace(self.limits, input_token_limit=max(1, ceiling - reserve)))
        soft = ContextPlanner(replace(self.limits, input_token_limit=max(1, ceiling * 3 // 5)))

        def planned(planner, candidate):
            return planner.plan(system_prompt=system_prompt, messages=candidate, tools=tools,
                                pinned_indexes=pinned_indexes)

        def finish(plan, changed=False):
            return replace(plan, max_request_bytes=int(ceiling),
                           reason='auto_compacted' if changed or plan.messages_dropped else plan.reason)

        compact = planned(soft, messages)
        if compact.status == 'ready':
            return finish(compact)

        pinned = set(pinned_indexes)
        latest_user = next((i for i in range(len(messages) - 1, -1, -1)
                            if messages[i].get('role') == 'user'), None)
        best = planned(hard, messages)
        best_changed = False
        for cap, shorten_users in ((32 * 1024, False), (8 * 1024, True), (2 * 1024, True)):
            candidate = [dict(item) for item in messages]
            changed = False
            for i, message in enumerate(candidate):
                role = message.get('role')
                if (i in pinned or i == latest_user or role == 'system'
                        or message.get('tool_calls') or not isinstance(message.get('content'), str)):
                    continue
                if role == 'user' and not shorten_users:
                    continue
                replacement = _compact_content(message['content'], cap, user=role == 'user')
                changed |= replacement != message['content']
                message['content'] = replacement
            compact = planned(soft, candidate)
            if compact.status == 'ready':
                return finish(compact, changed)
            fallback = planned(hard, candidate)
            if (fallback.status == 'ready'
                    and (best.status != 'ready' or fallback.estimate.tokens < best.estimate.tokens)):
                best, best_changed = fallback, changed
        if best.status == 'ready':
            return finish(best, best_changed)
        # A single latest request, system prompt or immutable call can still
        # exceed the ceiling. Do not hide or reinterpret that live instruction.
        return base if base.status == 'ready' else best


def default_context_planner(*, output_reserve_tokens=4096):
    return AutoCompactingContextPlanner(ContextLimits(
        input_token_limit=DEFAULT_CONTEXT_INPUT_BYTES,
        output_reserve_tokens=output_reserve_tokens,
    ))
