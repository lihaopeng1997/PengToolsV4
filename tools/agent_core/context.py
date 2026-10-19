"""Pure planning helpers for bounded Agent request context.

The default estimator is deliberately a UTF-8 byte heuristic.  It is useful
for consistent local bounds, but it is not a tokenizer and does not claim to
know a provider's true token count or context window.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .request_budget import validate_request_byte_limit


_SAFE_KIND = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_VALID_STATUSES = {"ready", "blocked", "deferred"}
_MESSAGE_OVERHEAD = 4


def _strict_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_int(name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return value


@dataclass(frozen=True)
class ContextLimits:
    """Local request bounds and optional, explicitly known provider capacity."""

    input_token_limit: int
    context_window_tokens: int | None = None
    output_reserve_tokens: int = 4096
    safety_margin_tokens: int = 1024
    adapter_private_reserve_tokens: int = 0

    def __post_init__(self) -> None:
        _require_int("input_token_limit", self.input_token_limit, minimum=1)
        if self.context_window_tokens is not None:
            _require_int("context_window_tokens", self.context_window_tokens, minimum=1)
        _require_int("output_reserve_tokens", self.output_reserve_tokens, minimum=0)
        _require_int("safety_margin_tokens", self.safety_margin_tokens, minimum=0)
        _require_int("adapter_private_reserve_tokens", self.adapter_private_reserve_tokens, minimum=0)


@dataclass(frozen=True)
class TokenEstimate:
    """A scalar estimate and its provenance label; it is not request content."""

    tokens: int
    kind: str

    def __post_init__(self) -> None:
        _require_int("tokens", self.tokens, minimum=0)
        if not isinstance(self.kind, str) or not _SAFE_KIND.fullmatch(self.kind):
            raise ValueError("kind must be a short safe label")


@dataclass(frozen=True)
class ContextPlan:
    """Result of planning. Callers must send only a ``ready`` plan."""

    status: Literal["ready", "blocked", "deferred"]
    messages: list[dict[str, Any]] = field(repr=False)
    estimate: TokenEstimate
    messages_kept: int
    messages_dropped: int
    reason: str
    window_known: bool
    output_reserve_tokens: int
    max_request_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError("invalid plan status")
        if not isinstance(self.estimate, TokenEstimate):
            raise TypeError("estimate must be TokenEstimate")
        _require_int("messages_kept", self.messages_kept, minimum=0)
        _require_int("messages_dropped", self.messages_dropped, minimum=0)
        _require_int("output_reserve_tokens", self.output_reserve_tokens, minimum=0)
        validate_request_byte_limit(self.max_request_bytes)
        if not isinstance(self.reason, str) or not _SAFE_KIND.fullmatch(self.reason):
            raise ValueError("reason must be a short safe label")
        if not isinstance(self.window_known, bool):
            raise TypeError("window_known must be bool")


@dataclass(frozen=True)
class _Unit:
    indexes: tuple[int, ...]
    kind: Literal["ordinary", "assistant", "tool_exchange"]


class ConservativeTokenEstimator:
    """Count serialized UTF-8 bytes plus a small per-message framing allowance.

    The result kind is ``heuristic_utf8_bytes`` to make clear that these are
    byte-based planning units, not tokens produced by a provider tokenizer.
    """

    kind = "heuristic_utf8_bytes"
    _MESSAGE_OVERHEAD = _MESSAGE_OVERHEAD

    def estimate_request(
        self,
        system_prompt: Any,
        messages: Sequence[Mapping[str, Any]],
        tools: Any,
    ) -> TokenEstimate:
        public_request = {
            "system_prompt": system_prompt,
            "messages": messages,
            "tools": tools,
        }
        encoded = _strict_json_bytes(public_request)
        return TokenEstimate(
            tokens=len(encoded) + self._MESSAGE_OVERHEAD * len(messages),
            kind=self.kind,
        )


class ContextPlanner:
    """Plan a request while preserving user/system anchors and whole exchanges."""

    def __init__(self, limits: ContextLimits, estimator: object | None = None) -> None:
        if not isinstance(limits, ContextLimits):
            raise TypeError("limits must be ContextLimits")
        self.limits = limits
        self.estimator = estimator if estimator is not None else ConservativeTokenEstimator()

    def plan(
        self,
        *,
        system_prompt: Any,
        messages: Sequence[Mapping[str, Any]],
        tools: Any,
        pending: bool = False,
        pinned_indexes: Sequence[int] = (),
    ) -> ContextPlan:
        if not isinstance(pending, bool):
            return self._blocked("invalid_input", 0)
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
            return self._blocked("invalid_input", 0)

        source_count = len(messages)
        if (not isinstance(pinned_indexes, Sequence)
                or isinstance(pinned_indexes, (str, bytes, bytearray))
                or any(type(index) is not int or index < 0 or index >= source_count
                       for index in pinned_indexes)):
            return self._blocked("invalid_input", source_count)
        if _has_private_reasoning_message_field(messages):
            # Never return or retain adapter-private continuation content.
            return self._blocked("private_reasoning_content", source_count)

        try:
            prompt_copy = copy.deepcopy(system_prompt)
            tools_copy = copy.deepcopy(tools)
            message_copy = copy.deepcopy([dict(message) for message in messages])
        except Exception:
            return self._blocked("invalid_input", source_count)
        if any(not isinstance(message, Mapping) for message in message_copy):
            return self._blocked("invalid_input", source_count)
        if any(_role(message) not in {"system", "user", "assistant", "tool"} for message in message_copy):
            return self._blocked("incomplete_history", source_count)

        if pending:
            return ContextPlan(
                status="deferred",
                messages=message_copy,
                estimate=TokenEstimate(0, "not_estimated"),
                messages_kept=len(message_copy),
                messages_dropped=0,
                reason="pending_exchange",
                window_known=self.limits.context_window_tokens is not None,
                output_reserve_tokens=self.limits.output_reserve_tokens,
            )

        units, malformed = _parse_units(message_copy)
        if malformed:
            return self._blocked("incomplete_history", source_count)

        budget = self._input_budget()
        try:
            full_estimate = self._estimate(prompt_copy, message_copy, tools_copy)
        except Exception:
            return self._blocked("estimator_error", source_count)
        if full_estimate is None:
            return self._blocked("invalid_estimate", source_count)
        if full_estimate.tokens <= budget:
            return self._ready(message_copy, full_estimate, 0)

        # All system/user messages are pinned. Also preserve both the newest
        # unit and the newest complete tool exchange, even when newer ordinary
        # messages have been appended after that exchange.
        protected_indexes = set(pinned_indexes)
        if units:
            protected_indexes.update(units[-1].indexes)
        for unit in reversed(units):
            if unit.kind == "tool_exchange":
                protected_indexes.update(unit.indexes)
                break
        removable_units = [
            unit
            for unit in units
            if unit.kind in {"assistant", "tool_exchange"}
            and not any(index in protected_indexes for index in unit.indexes)
        ]
        removable_indexes = {index for unit in removable_units for index in unit.indexes}

        if type(self.estimator) is ConservativeTokenEstimator:
            # The default estimator is an exact deterministic serialization
            # heuristic, so removing a message has an exact local byte cost.
            # This avoids serializing the entire remaining history per unit.
            predicted_tokens = full_estimate.tokens
            removed_indexes: set[int] = set()
            removed_count = 0
            try:
                for unit in removable_units:
                    for index in unit.indexes:
                        message_cost = len(_strict_json_bytes(message_copy[index]))
                        # The newest unit remains pinned, so a comma always
                        # remains in the messages array after each removal.
                        predicted_tokens -= message_cost + 1 + _MESSAGE_OVERHEAD
                        removed_indexes.add(index)
                        removed_count += 1
                    if predicted_tokens <= budget:
                        break
            except Exception:
                return self._blocked("estimator_error", source_count)
            if predicted_tokens > budget:
                return self._blocked("context_anchors_too_large", source_count)

            candidate = [
                message for index, message in enumerate(message_copy) if index not in removed_indexes
            ]
            try:
                verified_estimate = self._estimate(prompt_copy, candidate, tools_copy)
            except Exception:
                return self._blocked("estimator_error", source_count)
            if verified_estimate is None:
                return self._blocked("invalid_estimate", source_count)
            if verified_estimate.tokens != predicted_tokens or verified_estimate.tokens > budget:
                return self._blocked("estimator_inconsistent", source_count)
            return self._ready(candidate, verified_estimate, removed_count)

        anchor_indexes = set(range(len(message_copy))) - removable_indexes
        anchors = [message for index, message in enumerate(message_copy) if index in anchor_indexes]

        try:
            anchor_estimate = self._estimate(prompt_copy, anchors, tools_copy)
        except Exception:
            return self._blocked("estimator_error", source_count)
        if anchor_estimate is None:
            return self._blocked("invalid_estimate", source_count)
        if anchor_estimate.tokens > budget:
            return self._blocked("context_anchors_too_large", source_count)

        kept_indexes = set(range(len(message_copy)))
        removed_count = 0
        candidate_estimate = full_estimate
        for unit in removable_units:
            for index in unit.indexes:
                kept_indexes.discard(index)
                removed_count += 1
            candidate = [message for index, message in enumerate(message_copy) if index in kept_indexes]
            try:
                candidate_estimate = self._estimate(prompt_copy, candidate, tools_copy)
            except Exception:
                return self._blocked("estimator_error", source_count)
            if candidate_estimate is None:
                return self._blocked("invalid_estimate", source_count)
            if candidate_estimate.tokens <= budget:
                return self._ready(candidate, candidate_estimate, removed_count)

        # A valid estimator should agree with the anchor estimate. Keep a
        # defensive block if an injected estimator is inconsistent.
        return self._blocked("context_anchors_too_large", source_count)

    def _input_budget(self) -> int:
        budget = self.limits.input_token_limit
        window = self.limits.context_window_tokens
        if window is not None:
            available = (
                window
                - self.limits.output_reserve_tokens
                - self.limits.adapter_private_reserve_tokens
                - self.limits.safety_margin_tokens
            )
            budget = min(budget, available)
        return budget

    def _estimate(
        self,
        system_prompt: Any,
        messages: Sequence[Mapping[str, Any]],
        tools: Any,
    ) -> TokenEstimate | None:
        method = getattr(self.estimator, "estimate_request", None)
        if callable(method):
            result = method(
                copy.deepcopy(system_prompt),
                copy.deepcopy(list(messages)),
                copy.deepcopy(tools),
            )
        elif callable(self.estimator):
            result = self.estimator(
                copy.deepcopy(system_prompt),
                copy.deepcopy(list(messages)),
                copy.deepcopy(tools),
            )
        else:
            return None
        if not isinstance(result, TokenEstimate):
            return None
        try:
            result.__post_init__()
        except (TypeError, ValueError):
            return None
        return result

    def _ready(self, messages: list[dict[str, Any]], estimate: TokenEstimate, dropped: int) -> ContextPlan:
        known = self.limits.context_window_tokens is not None
        request_byte_limit = None
        if (type(self.estimator) is ConservativeTokenEstimator
                and self.estimator.kind == "heuristic_utf8_bytes"):
            # ContextLimits keeps its legacy int-subclass contract. Normalize
            # only the newly introduced byte cap to the request contract's
            # exact built-in int type.
            candidate = int(self._input_budget())
            if candidate > 0:
                request_byte_limit = candidate
        if dropped:
            reason = "trimmed_to_known_input_budget" if known else "trimmed_to_application_budget_window_unknown"
        else:
            reason = "within_known_input_budget" if known else "within_application_budget_window_unknown"
        return ContextPlan(
            status="ready",
            messages=messages,
            estimate=estimate,
            messages_kept=len(messages),
            messages_dropped=dropped,
            reason=reason,
            window_known=known,
            output_reserve_tokens=self.limits.output_reserve_tokens,
            max_request_bytes=request_byte_limit,
        )

    def _blocked(self, reason: str, source_count: int) -> ContextPlan:
        return ContextPlan(
            status="blocked",
            messages=[],
            estimate=TokenEstimate(0, "not_estimated"),
            messages_kept=0,
            messages_dropped=max(0, source_count),
            reason=reason,
            window_known=self.limits.context_window_tokens is not None,
            output_reserve_tokens=self.limits.output_reserve_tokens,
        )


def _role(message: Mapping[str, Any]) -> str:
    value = message.get("role")
    return value.strip().lower() if isinstance(value, str) else ""


def _parse_units(messages: Sequence[Mapping[str, Any]]) -> tuple[list[_Unit], bool]:
    units: list[_Unit] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        role = _role(message)
        if role not in {"system", "user", "assistant", "tool"}:
            return [], True
        if role == "tool":
            return [], True

        if role != "assistant" or "tool_calls" not in message or message.get("tool_calls") is None:
            units.append(_Unit((index,), "assistant" if role == "assistant" else "ordinary"))
            index += 1
            continue

        calls = message.get("tool_calls")
        if isinstance(calls, (list, tuple)) and not calls:
            units.append(_Unit((index,), "assistant"))
            index += 1
            continue
        if not isinstance(calls, (list, tuple)):
            return [], True

        call_ids: list[str] = []
        for call in calls:
            if not isinstance(call, Mapping):
                return [], True
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                return [], True
            call_ids.append(call_id)
        if not call_ids or len(call_ids) != len(set(call_ids)):
            return [], True

        expected = set(call_ids)
        received: set[str] = set()
        cursor = index + 1
        while cursor < len(messages) and _role(messages[cursor]) == "tool":
            result_id = messages[cursor].get("tool_call_id")
            if not isinstance(result_id, str) or not result_id.strip():
                return [], True
            if result_id not in expected or result_id in received:
                return [], True
            received.add(result_id)
            cursor += 1
        if received != expected:
            return [], True
        units.append(_Unit(tuple(range(index, cursor)), "tool_exchange"))
        index = cursor
    return units, False


def _has_private_reasoning_message_field(messages: Sequence[Any]) -> bool:
    """Reject only the adapter-private field at a message's top level."""
    return any(
        isinstance(message, Mapping) and "reasoning_content" in message
        for message in messages
    )


__all__ = [
    "ConservativeTokenEstimator",
    "ContextLimits",
    "ContextPlan",
    "ContextPlanner",
    "TokenEstimate",
]
