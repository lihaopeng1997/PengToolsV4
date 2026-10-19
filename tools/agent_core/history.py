"""Pair-aware helpers for bounded model conversation history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def tail_complete_history(
    messages: Sequence[Mapping[str, Any]],
    max_messages: int,
) -> list[dict[str, Any]]:
    """Return a bounded suffix without splitting native tool-call exchanges.

    Callers should normalize/validate persisted messages before calling this
    function. Tool-call assistant messages and their contiguous tool results
    are treated as one unit, so a token/message budget can never retain only
    the assistant declaration or only an orphaned tool result.
    """

    limit = max(0, int(max_messages))
    if limit == 0:
        return []

    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        raw = messages[index]
        if not isinstance(raw, Mapping):
            index += 1
            continue
        message = dict(raw)
        role = str(message.get("role") or "").strip().lower()
        if role == "tool":
            # A result without its assistant declaration is never requestable.
            index += 1
            continue

        calls = message.get("tool_calls") if role == "assistant" else None
        if not calls:
            units.append([message])
            index += 1
            continue

        call_ids = []
        if isinstance(calls, list):
            for call in calls:
                call_id = str(call.get("id") or "").strip() if isinstance(call, Mapping) else ""
                call_ids.append(call_id)
        valid_ids = bool(call_ids) and all(call_ids) and len(call_ids) == len(set(call_ids))

        results: list[dict[str, Any]] = []
        received: set[str] = set()
        cursor = index + 1
        valid_block = valid_ids
        while cursor < len(messages):
            candidate = messages[cursor]
            if not isinstance(candidate, Mapping) or str(candidate.get("role") or "").strip().lower() != "tool":
                break
            result = dict(candidate)
            result_id = str(result.get("tool_call_id") or "").strip()
            if not result_id or result_id not in call_ids or result_id in received:
                valid_block = False
            else:
                received.add(result_id)
                results.append(result)
            cursor += 1
        if valid_block and received == set(call_ids):
            units.append([message, *results])
        # Drop the entire incomplete/ambiguous exchange while consuming its
        # adjacent tool results as a block.
        index = cursor

    selected: list[list[dict[str, Any]]] = []
    used = 0
    for unit in reversed(units):
        if used + len(unit) > limit:
            break
        selected.append(unit)
        used += len(unit)
    return [dict(message) for unit in reversed(selected) for message in unit]


__all__ = ["tail_complete_history"]
