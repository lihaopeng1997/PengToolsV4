"""Pure-Python budgets for one data-center Agent run.

The budget is intentionally independent from the model and database
implementations.  It is a small accounting object used by the runner before
each external boundary and after each streamed/result payload.  A policy or
projection layer may impose tighter limits; this object only enforces the
run-level ceilings.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import time
from typing import Any, Callable, Mapping


_UNSET = object()


class BudgetExceeded(RuntimeError):
    """Raised when an operation cannot be reserved within the run budget."""

    def __init__(self, reason: str, *, used: Any = None, limit: Any = None) -> None:
        self.reason = str(reason)
        self.used = used
        self.limit = limit
        super().__init__(self.reason)


# A second name reads naturally at call sites and keeps adapters free to use
# either terminology.
BudgetExhausted = BudgetExceeded


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Default run limits from the DC-01 requirements.

    ``max_bytes`` is a conservative accounting ceiling for the aggregate
    model/tool exchange.  Result projection remains responsible for tighter
    per-result limits and redaction.  ``None`` disables an optional ceiling,
    which is useful for small deterministic tests.
    """

    max_model_turns: int = 12
    max_tool_calls: int = 24
    max_queries: int = 6
    max_duration_s: float | None = 180.0
    max_bytes: int | None = 4 * 1024 * 1024
    max_input_chars: int | None = 24_000
    max_output_tokens: int | None = 4_096

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None:
                continue
            if item.name == "max_duration_s":
                if float(value) < 0:
                    raise ValueError(f"{item.name} must be non-negative")
            elif int(value) < 0:
                raise ValueError(f"{item.name} must be non-negative")

    @property
    def max_total_bytes(self) -> int | None:
        """Alias used by callers that call the aggregate ceiling total bytes."""

        return self.max_bytes

    @property
    def max_time_s(self) -> float | None:
        return self.max_duration_s

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """Immutable accounting snapshot suitable for an AgentEvent payload."""

    model_turns: int
    tool_calls: int
    queries: int
    bytes_used: int
    input_chars: int
    output_tokens: int
    elapsed_ms: float
    exhausted_reason: str | None = None

    @property
    def model_rounds(self) -> int:
        return self.model_turns

    @property
    def data_queries(self) -> int:
        return self.queries

    @property
    def total_bytes(self) -> int:
        return self.bytes_used

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_turns": self.model_turns,
            "model_rounds": self.model_turns,
            "tool_calls": self.tool_calls,
            "queries": self.queries,
            "data_queries": self.queries,
            "bytes_used": self.bytes_used,
            "total_bytes": self.bytes_used,
            "input_chars": self.input_chars,
            "output_tokens": self.output_tokens,
            "elapsed_ms": self.elapsed_ms,
            "exhausted_reason": self.exhausted_reason,
        }


class Budget:
    """Mutable, thread-safe-enough accounting for one synchronous run.

    The runner owns a budget and calls its methods from one worker.  Methods
    intentionally do not perform I/O and are also safe to inspect from a UI
    consumer while the worker is running because the counters are updated in
    a single Python operation.  ``clock`` is injectable for deterministic
    timeout tests.
    """

    def __init__(
        self,
        limits: BudgetLimits | Mapping[str, Any] | None = None,
        *,
        max_model_turns: int | None = None,
        max_tool_calls: int | None = None,
        max_queries: int | None = None,
        max_duration_s: float | None | object = _UNSET,
        max_time_s: float | None | object = _UNSET,
        max_bytes: int | None | object = _UNSET,
        max_total_bytes: int | None | object = _UNSET,
        max_input_chars: int | None = None,
        max_output_tokens: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limits is None:
            base = BudgetLimits()
        elif isinstance(limits, BudgetLimits):
            base = limits
        elif isinstance(limits, Mapping):
            values = BudgetLimits().as_dict()
            values.update({str(key): value for key, value in limits.items() if str(key) in values})
            # Accept common aliases in mapping form as well.
            if "max_total_bytes" in limits and "max_bytes" not in limits:
                values["max_bytes"] = limits["max_total_bytes"]
            if "max_time_s" in limits and "max_duration_s" not in limits:
                values["max_duration_s"] = limits["max_time_s"]
            base = BudgetLimits(**values)
        else:
            raise TypeError("limits must be BudgetLimits, a mapping, or None")

        overrides: dict[str, Any] = {
            "max_model_turns": max_model_turns,
            "max_tool_calls": max_tool_calls,
            "max_queries": max_queries,
            "max_input_chars": max_input_chars,
            "max_output_tokens": max_output_tokens,
        }
        if max_duration_s is not _UNSET:
            overrides["max_duration_s"] = max_duration_s
        elif max_time_s is not _UNSET:
            overrides["max_duration_s"] = max_time_s
        if max_bytes is not _UNSET:
            overrides["max_bytes"] = max_bytes
        elif max_total_bytes is not _UNSET:
            overrides["max_bytes"] = max_total_bytes
        values = base.as_dict()
        for key, value in overrides.items():
            if value is not None or key in {"max_duration_s", "max_bytes"} and key in overrides:
                values[key] = value
        self.limits = BudgetLimits(**values)
        self._clock = clock
        self.started_at = float(clock())
        self.model_turns = 0
        self.tool_calls = 0
        self.queries = 0
        self.bytes_used = 0
        self.input_chars = 0
        self.output_tokens = 0
        self._bytes_overflowed = False
        self._input_overflowed = False
        self._output_overflowed = False

    @property
    def elapsed_s(self) -> float:
        return max(0.0, float(self._clock()) - self.started_at)

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_s * 1000.0

    @property
    def remaining_model_turns(self) -> int | None:
        return max(0, self.limits.max_model_turns - self.model_turns)

    @property
    def remaining_tool_calls(self) -> int | None:
        return max(0, self.limits.max_tool_calls - self.tool_calls)

    @property
    def remaining_queries(self) -> int | None:
        return max(0, self.limits.max_queries - self.queries)

    @property
    def remaining_bytes(self) -> int | None:
        if self.limits.max_bytes is None:
            return None
        return max(0, self.limits.max_bytes - self.bytes_used)

    def exhausted_reason(self) -> str | None:
        """Return the first exhausted ceiling in stable priority order."""

        if self.model_turns >= self.limits.max_model_turns:
            return "model_turns"
        if self.tool_calls >= self.limits.max_tool_calls:
            return "tool_calls"
        if self.queries >= self.limits.max_queries:
            return "queries"
        if self.limits.max_duration_s is not None and self.elapsed_s >= self.limits.max_duration_s:
            return "time"
        if self._bytes_overflowed or (
            self.limits.max_bytes is not None and self.bytes_used >= self.limits.max_bytes
        ):
            return "bytes"
        if self._input_overflowed:
            return "input_chars"
        if self._output_overflowed:
            return "output_tokens"
        return None

    @property
    def is_exhausted(self) -> bool:
        return self.exhausted_reason() is not None

    def _reserve(self, attribute: str, limit: int | None, reason: str) -> None:
        current = int(getattr(self, attribute))
        if limit is not None and current >= limit:
            raise BudgetExceeded(reason, used=current, limit=limit)
        setattr(self, attribute, current + 1)

    def reserve_model_turn(self) -> None:
        self.check_time()
        self._reserve("model_turns", self.limits.max_model_turns, "model_turns")

    consume_model_turn = reserve_model_turn

    def reserve_tool_call(self) -> None:
        self.check_time()
        self._reserve("tool_calls", self.limits.max_tool_calls, "tool_calls")

    consume_tool_call = reserve_tool_call

    def reserve_query(self) -> None:
        self.check_time()
        self._reserve("queries", self.limits.max_queries, "queries")

    consume_query = reserve_query

    def check_time(self) -> None:
        limit = self.limits.max_duration_s
        if limit is not None and self.elapsed_s >= limit:
            raise BudgetExceeded("time", used=self.elapsed_s, limit=limit)

    def add_bytes(self, value: Any, *, encoding: str = "utf-8") -> bool:
        """Account for a payload and return whether the ceiling remains open.

        The counter is capped at the configured limit so a terminal budget
        event never reports an impossible value.  The private overflow flag
        still records that a payload crossed the boundary.
        """

        if isinstance(value, int):
            amount = max(0, value)
        elif isinstance(value, bytes):
            amount = len(value)
        else:
            amount = len(str(value).encode(encoding, errors="replace"))
        if self.limits.max_bytes is None:
            self.bytes_used += amount
            return True
        available = max(0, self.limits.max_bytes - self.bytes_used)
        self.bytes_used += min(available, amount)
        if amount > available:
            self._bytes_overflowed = True
            return False
        return True

    consume_bytes = add_bytes
    record_bytes = add_bytes

    def add_input_chars(self, value: Any) -> bool:
        amount = max(0, value if isinstance(value, int) else len(str(value)))
        limit = self.limits.max_input_chars
        if limit is None:
            self.input_chars += amount
            return True
        available = max(0, limit - self.input_chars)
        self.input_chars += min(available, amount)
        if amount > available:
            self._input_overflowed = True
            return False
        return True

    record_input = add_input_chars

    def add_output_tokens(self, value: Any) -> bool:
        amount = max(0, int(value))
        limit = self.limits.max_output_tokens
        if limit is None:
            self.output_tokens += amount
            return True
        available = max(0, limit - self.output_tokens)
        self.output_tokens += min(available, amount)
        if amount > available:
            self._output_overflowed = True
            return False
        return True

    record_output_tokens = add_output_tokens

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            model_turns=self.model_turns,
            tool_calls=self.tool_calls,
            queries=self.queries,
            bytes_used=self.bytes_used,
            input_chars=self.input_chars,
            output_tokens=self.output_tokens,
            elapsed_ms=self.elapsed_ms,
            exhausted_reason=self.exhausted_reason(),
        )

    def as_dict(self) -> dict[str, Any]:
        snapshot = self.snapshot().as_dict()
        snapshot["limits"] = self.limits.as_dict()
        return snapshot

    def ensure_available(self) -> None:
        """Raise a typed error when a further boundary is not available."""

        reason = self.exhausted_reason()
        if reason is not None:
            raise BudgetExceeded(reason)


# Compatibility spelling for callers that prefer an explicit run prefix.
AgentBudget = Budget


__all__ = [
    "AgentBudget",
    "Budget",
    "BudgetExceeded",
    "BudgetExhausted",
    "BudgetLimits",
    "BudgetSnapshot",
]
